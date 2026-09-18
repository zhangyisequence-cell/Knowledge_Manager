import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

if os.name != "nt":
    pytest.skip("Windows PowerShell integration tests", allow_module_level=True)


ROOT = Path(__file__).resolve().parents[1]
COPIER = ROOT / "scripts" / "copy_backup_to_host.ps1"
POWERSHELL = shutil.which("powershell.exe")
if not POWERSHELL:
    pytest.skip("Windows PowerShell 5.1 is unavailable", allow_module_level=True)


def write_fixture(tmp_path, payload=b"binary\x00backup\xffpayload"):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (destination / ".knowledge-manager-backup-root").write_text("managed\n")
    name = "knowledge-20260919T010203-1234abcd.tar.gz"
    (source / name).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    fake = tmp_path / "fake_ssh.py"
    fake.write_text(
        """import json, os, pathlib, sys
root = pathlib.Path(os.environ['FAKE_SSH_ROOT'])
name = 'knowledge-20260919T010203-1234abcd.tar.gz'
data = (root / name).read_bytes()
mode = os.environ.get('FAKE_MODE', 'ok')
command = sys.argv[-1]
if command == 'latest':
    sha = __import__('hashlib').sha256(data).hexdigest()
    if mode == 'badhash': sha = '0' * 64
    print(json.dumps({'name': name, 'size': len(data), 'sha256': sha}))
elif command == name and sys.argv[-2] == 'get':
    if mode == 'timeout':
        __import__('time').sleep(6)
    if mode == 'truncated': data = data[:-1]
    if mode == 'overrun': data = data + b'x'
    sys.stdout.buffer.write(data)
else:
    sys.exit(2)
""",
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "destination": str(destination),
                "vmName": "Ubuntu",
                "guestMac": "00155D031703",
                "guestUser": "backup-reader",
                "keyPath": str(tmp_path / "key"),
                "knownHostsPath": str(tmp_path / "known_hosts"),
                "hostKeyAlias": "ubuntu-backup",
                "timeoutSeconds": 5,
                "maxArchiveBytes": 1024 * 1024,
                "minimumFreeBytes": 5 * 1024 * 1024 * 1024,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "key").write_text("fixture")
    (tmp_path / "known_hosts").write_text("fixture")
    return source, destination, config, fake, name, digest, payload


def run_copy(tmp_path, mode="ok"):
    source, destination, config, fake, name, digest, payload = write_fixture(tmp_path)
    env = {**os.environ, "FAKE_SSH_ROOT": str(source), "FAKE_MODE": mode}
    result = subprocess.run(
        [
            str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(COPIER),
            "-ConfigPath", str(config), "-GuestAddress", "127.0.0.1", "-SshPath", sys.executable,
            "-SshPrefixArgument", str(fake),
        ],
        capture_output=True, text=True, env=env, check=False,
    )
    return result, destination, name, digest, payload


def test_binary_copy_and_idempotent_reuse(tmp_path):
    result, destination, name, digest, payload = run_copy(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (destination / name).read_bytes() == payload
    assert json.loads((destination / "status.json").read_text())["sha256"] == digest

    # A second run reuses the verified final instead of rewriting it.
    env = {**os.environ, "FAKE_SSH_ROOT": str(tmp_path / "source"), "FAKE_MODE": "ok"}
    second = subprocess.run(result.args, capture_output=True, text=True, env=env, check=False)
    assert second.returncode == 0, second.stderr
    assert json.loads((destination / "status.json").read_text())["result"] == "reused"


def test_existing_mismatching_final_is_never_overwritten(tmp_path):
    result, destination, name, _, _ = run_copy(tmp_path)
    assert result.returncode == 0, result.stderr
    mismatching = b"do not overwrite"
    (destination / name).write_bytes(mismatching)
    env = {**os.environ, "FAKE_SSH_ROOT": str(tmp_path / "source"), "FAKE_MODE": "ok"}

    second = subprocess.run(result.args, capture_output=True, text=True, env=env, check=False)

    assert second.returncode != 0
    assert (destination / name).read_bytes() == mismatching
    assert json.loads((destination / "status.json").read_text())["result"] == "failed"


@pytest.mark.parametrize("mode", ["truncated", "badhash", "overrun", "timeout"])
def test_invalid_transfer_is_not_published(tmp_path, mode):
    result, destination, name, _, _ = run_copy(tmp_path, mode)
    assert result.returncode != 0
    assert not (destination / name).exists()
    assert not list(destination.glob("*.partial"))
    assert json.loads((destination / "status.json").read_text())["result"] == "failed"
