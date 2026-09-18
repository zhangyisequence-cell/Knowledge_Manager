# Second physical disk backup implementation plan

> **For agentic workers:** Continue the existing approved server-persistence design in this task. Root coordinates bounded implementation and independent review; deploy only after verification.

**Goal:** Keep verified application backups on a physical disk separate from the Ubuntu VHD and prove recovery from that copy.

**Architecture:** Ubuntu continues creating its existing consistent archives. The Windows host pulls completed archives over its existing guest SSH port using a dedicated key restricted to a read-only backup exporter, then verifies size and SHA-256 before publishing the copy on `D:\KnowledgeManagerBackups`. The copier discovers the guest through the known Hyper-V adapter MAC and verifies its pinned SSH host key. It runs independently of the management computer.

**Tech stack:** Python standard library on Ubuntu, Windows PowerShell 5.1/OpenSSH/.NET streams and Task Scheduler on the host.

**Spec:** `docs/superpowers/specs/2026-09-18-ubuntu-knowledge-server-design.md`, section 部署与保存; `2026-09-18-server-persistence.md`, second-medium requirement.

## Facts and scope decisions

- Read-only inventory: the active Ubuntu AVHDX and its parent VHDX are both on E:, physical Disk 1 (`eVtran V26SX`). D: is on Disk 0 (`INTEL SSDSC2BF240A4H`), with about 32.7 GiB free. Both disks are healthy/online.
- This implements the already requested second-medium backup within the old laptop. It protects against failure of the Ubuntu storage disk; an external/offsite backup remains a separate option.
- Create a new dedicated destination only after checking it does not contain unrelated files. Do not format disks, move existing files, mount disks, or copy live VHDX/AVHDX files.
- Do not add inbound ports, firewall rules, management forwarding, cloud storage, or cloud credentials. The host reaches the guest's existing SSH endpoint on the internal switch.
- Keep all completed backup versions. Do not delete old backups automatically. Require at least 5 GiB free after a proposed copy; report insufficient capacity rather than filling D:.
- Run hourly on the Windows host, copying only when a new completed archive exists. Leave evidence of the most recent success or failure. No notifications to third parties.
- Private keys, real configuration, and backup contents never enter Git. Windows key/config/backups are accessible only to SYSTEM and Administrators. The guest key cannot obtain an interactive shell or perform port forwarding.

## Task 1: Read-only archive exporter

**Files:** `scripts/export_backup.py`, `tests/test_export_backup.py`.

**Interface:** `latest` returns UTF-8 JSON `{name, size, sha256}` for the latest completed, valid archive in `/var/backups/knowledge-manager`; `get <name>` writes only the archive bytes to stdout. Diagnostics go to stderr and failures exit nonzero.

- [ ] Test archive validation using real `server_backup.create_backup` fixtures. Partial files, missing checksum, wrong checksum, symlinks and unsafe names must not be exported. Reject every request except exact `latest` and `get knowledge-YYYYMMDDTHHMMSS-xxxxxxxx.tar.gz` forms.
- [ ] Use only a fixed root in the CLI. Keep a testable function accepting a temporary root. Verify the archive/sidecar and refuse path escape or non-regular files before reading.
- [ ] Do not allow the SSH caller to supply flags, root paths, shell fragments, environment changes, or executable names. Stream bytes in bounded chunks; stdout contains no progress text.
- [ ] Run focused tests and Ruff, then independent review.

## Task 2: Host copier and schedule

**Files:** `scripts/copy_backup_to_host.ps1`, `scripts/install_host_backup.ps1`.

**Inputs:** a private local JSON config with destination, VM name/MAC, guest backup username, key path, pinned known-hosts path and host-key alias. Config is created at deployment, never stored in Git.

- [ ] Discover guest IPv4 through Hyper-V/Get-NetNeighbor and the configured MAC; do not trust an IP alone. SSH uses BatchMode, StrictHostKeyChecking and the dedicated known-hosts file.
- [ ] Parse the exporter manifest and validate filename, integer size and hexadecimal SHA. Check destination containment, reject reparse points, enforce 5 GiB free-space reserve, and refuse concurrent copies with a local lock.
- [ ] Use `System.Diagnostics.Process` with `StandardOutput.BaseStream` for archive bytes; do not pass binary data through PowerShell's text pipeline. Bound command duration, collect stderr separately, and terminate only this copy's own timed-out child.
- [ ] Write to a unique partial file inside the destination. Verify size/SHA before moving it into place. Existing matching files are reused; existing mismatching final files cause failure instead of overwrite. Clean only this operation's own partial file.
- [ ] Write a small status JSON with archive name, hash, timestamp and result. No secrets or archive contents in logs.
- [ ] Installer requires an explicit install switch, creates a dedicated task running as SYSTEM hourly, and applies private ACLs only to the new project paths. Detect conflicting existing task/path rather than overwriting unrelated state. Avoid changing global PowerShell or SSH settings.
- [ ] Parse/check scripts locally; use an isolated fake SSH program to exercise binary integrity, failed/truncated transfers and duplicate-copy behavior before live use.

## Task 3: Deploy and prove recovery

- [ ] Review both components together against this protocol; verify the user cannot select arbitrary guest files or commands.
- [ ] Generate a dedicated key on the host and protect it with SYSTEM/Administrators ACLs. Create a locked guest backup account, a root-owned forced-command wrapper and an exact sudo rule for the exporter. The authorized key disables PTY, forwarding, agent/X11 forwarding and user rc. Do not change existing management keys or sshd settings.
- [ ] Verify the pinned guest public host key through the existing authenticated management connection before installing the host known-hosts entry.
- [ ] Run one manual host copy and independently hash the D: file. Verify the scheduled task's action, principal, trigger and result.
- [ ] Transfer the D: copy through authenticated management into a fresh guest scratch path; run existing `server_backup.py verify` and `restore` to a new directory. Check SQLite integrity/counts and attachment hashes.
- [ ] Commit all source, tests, and actual deployment evidence to the user's GitHub branch. Keep missing/failed runtime gates explicit.
