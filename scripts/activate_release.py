"""Activate a prepared Ubuntu release without overlapping database writers."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

try:
    from scripts.server_backup import create_backup
except ModuleNotFoundError:  # Executed directly from the release's scripts directory.
    from server_backup import create_backup


UNITS = (
    "knowledge-manager.service",
    "knowledge-manager-wechat.service",
    "knowledge-manager-backup.service",
    "knowledge-manager-backup.timer",
)
APP = "knowledge-manager.service"
CALLBACK = "knowledge-manager-wechat.service"


def _default_run(*command: str) -> str:
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    return result.stdout.strip()


@contextmanager
def _file_lock(path: Path):
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _stop_if_loaded(run, unit: str) -> None:
    state = run("systemctl", "show", unit, "--property=LoadState", "--value")
    if state == "loaded":
        run("systemctl", "stop", unit)


def _verify_candidate(release: Path, candidates: list[Path], run) -> None:
    with tempfile.TemporaryDirectory(prefix="km-unit-verify-") as temporary:
        staged = Path(temporary)
        paths = []
        for source in candidates:
            destination = staged / source.name
            destination.write_text(
                source.read_text(encoding="utf-8").replace(
                    "/opt/knowledge-manager/current", str(release)
                ),
                encoding="utf-8",
            )
            paths.append(str(destination))
        run("systemd-analyze", "verify", *paths)


def _configured_port(environment: Path) -> int:
    value = None
    for raw_line in environment.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("KNOWLEDGE_PORT="):
            value = line.split("=", 1)[1].strip().strip("'\"")
    try:
        port = int(value) if value is not None else 0
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise ValueError("server.env must define a valid KNOWLEDGE_PORT")
    return port


def _wait_for_health(port: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.load(response)
            if payload.get("status") == "ok" and payload.get("storage_configured") is True:
                return
            last_error = "unhealthy response"
        except (OSError, ValueError, urllib.error.URLError) as error:
            last_error = type(error).__name__
        time.sleep(0.5)
    raise RuntimeError(f"local application health check failed: {last_error}")


def _replace_file(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.new")
    shutil.copyfile(source, temporary)
    os.chmod(temporary, 0o644)
    os.replace(temporary, destination)


def _replace_link(link: Path, target: Path) -> None:
    temporary = link.with_name(f".{link.name}.{os.getpid()}.new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target, target_is_directory=True)
    os.replace(temporary, link)


def _read_current(link: Path) -> Path | None:
    return link.resolve() if link.is_symlink() else None


def _write_current(link: Path, target: Path | None) -> None:
    if target is None:
        link.unlink(missing_ok=True)
    else:
        _replace_link(link, target)


def activate_release(
    release: Path,
    *,
    root: Path = Path("/"),
    run=_default_run,
    read_current=None,
    write_current=None,
    backup=create_backup,
    lock=_file_lock,
    health_check=_wait_for_health,
) -> Path:
    release = release.resolve()
    root = root.resolve()
    unit_dir = root / "etc/systemd/system"
    current = root / "opt/knowledge-manager/current"
    marker = root / "etc/knowledge-manager/wechat.enabled"
    data_root = root / "srv/knowledge-manager"
    config_root = root / "etc/knowledge-manager"
    backup_root = root / "var/backups/knowledge-manager"
    candidates = [release / "deploy" / name for name in UNITS]

    # Resolve absolute executable paths against the candidate before interrupting services.
    _verify_candidate(release, candidates, run)

    read_current = read_current or (lambda: _read_current(current))
    write_current = write_current or (lambda target: _write_current(current, target))
    old_target = read_current()
    old_units = {
        name: (unit_dir / name).read_bytes() if (unit_dir / name).is_file() else None
        for name in UNITS
    }
    archive = None
    publication_started = False
    _stop_if_loaded(run, "knowledge-manager-backup.timer")
    _stop_if_loaded(run, "knowledge-manager-backup.service")
    lock_context = lock(backup_root / ".backup.lock")
    lock_context.__enter__()
    try:
        _stop_if_loaded(run, CALLBACK)
        _stop_if_loaded(run, APP)
        archive = backup(data_root, backup_root, config_root)
        publication_started = True
        for name, source in zip(UNITS, candidates):
            _replace_file(source, unit_dir / name)
        write_current(release)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", APP, "knowledge-manager-backup.timer")
        run("systemctl", "start", APP)
        health_check(_configured_port(config_root / "server.env"))
        if marker.exists():
            run("systemctl", "enable", CALLBACK)
            run("systemctl", "start", CALLBACK)
        else:
            run("systemctl", "disable", CALLBACK)
        run("systemctl", "start", "knowledge-manager-backup.timer")
        return archive
    except BaseException as error:
        # Never restart old code automatically: new code may already have migrated data.
        try:
            run("systemctl", "stop", CALLBACK)
        finally:
            try:
                run("systemctl", "stop", APP)
            finally:
                if publication_started:
                    write_current(old_target)
                    for name, content in old_units.items():
                        destination = unit_dir / name
                        if content is None:
                            destination.unlink(missing_ok=True)
                        else:
                            temporary = destination.with_name(f".{name}.{os.getpid()}.old")
                            temporary.write_bytes(content)
                            os.chmod(temporary, 0o644)
                            os.replace(temporary, destination)
                run("systemctl", "daemon-reload")
                run("systemctl", "disable", APP)
                run("systemctl", "disable", CALLBACK)
                run("systemctl", "disable", "knowledge-manager-backup.timer")
        detail = str(archive) if archive is not None else "backup did not complete"
        raise RuntimeError(
            f"Release activation failed ({error}); both writers are stopped and the old program/units "
            f"were restored. Verify the pre-upgrade backup before manual recovery: {detail}"
        ) from error
    finally:
        lock_context.__exit__(*sys.exc_info())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    args = parser.parse_args()
    archive = activate_release(args.release)
    print(f"Activated release after verified pre-upgrade backup: {archive}")


if __name__ == "__main__":
    main()
