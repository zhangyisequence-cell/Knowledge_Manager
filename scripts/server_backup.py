"""Consistent server backup and non-overwriting restore with per-file verification."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import tarfile
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4


def _digest(stream) -> str:
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(chunk)
    return result.hexdigest()


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not path.is_absolute() and "\\" not in name and ":" not in name
        and len(path.parts) >= 2 and path.parts[0] in {"vault", "data", "config"}
        and all(part not in {".", "..", ""} for part in name.split("/"))
    )


def verify_backup(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or names.count("manifest.json") != 1:
            raise ValueError("备份清单缺失或路径重复")
        for member in members:
            if not member.isfile() or (member.name != "manifest.json" and not _safe_name(member.name)):
                raise ValueError("备份含不安全路径或非普通文件")
        if bundle.getmember("manifest.json").size > 16 * 1024 * 1024:
            raise ValueError("备份清单过大")
        manifest = json.load(bundle.extractfile("manifest.json"))
        if manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict):
            raise ValueError("备份清单格式无效")
        if set(names) != set(manifest["files"]) | {"manifest.json"}:
            raise ValueError("备份文件与清单不一致")
        for name, expected in manifest["files"].items():
            member = bundle.getmember(name)
            with bundle.extractfile(member) as source:
                if member.size != expected["size"] or _digest(source) != expected["sha256"]:
                    raise ValueError(f"备份校验失败：{name}")
        return manifest


def create_backup(data_root: Path, backup_root: Path, config_root: Path | None = None) -> Path:
    """Caller MUST quiesce every writer first; CLI stops the application service."""
    data_root, backup_root = data_root.resolve(), backup_root.resolve()
    if backup_root.is_relative_to(data_root):
        raise ValueError("备份目录不能位于数据目录内")
    roots = {"vault": data_root / "vault", "data": data_root / "data"}
    if config_root is not None:
        roots["config"] = config_root.resolve()
    files = []
    for prefix, root in roots.items():
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"数据目录不存在或为链接：{prefix}")
        for source in sorted(root.rglob("*")):
            if source.is_symlink() or (not source.is_file() and not source.is_dir()):
                raise ValueError("备份源含链接或非普通文件")
            if source.is_file():
                name = f"{prefix}/{source.relative_to(root).as_posix()}"
                if not _safe_name(name):
                    raise ValueError("备份源含无法安全恢复的文件名")
                files.append((name, source))
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    archive = backup_root / f"knowledge-{stamp}-{uuid4().hex[:8]}.tar.gz"
    temporary = archive.with_suffix(".partial")
    manifest = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(), "files": {}}
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            with tarfile.open(fileobj=output, mode="w:gz") as bundle:
                for name, source in files:
                    info = bundle.gettarinfo(str(source), arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o600
                    with source.open("rb") as content:
                        digest = _digest(content)
                        content.seek(0)
                        bundle.addfile(info, content)
                    manifest["files"][name] = {"sha256": digest, "size": info.size}
                payload = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
                info = tarfile.TarInfo("manifest.json")
                info.size, info.mode = len(payload), 0o600
                bundle.addfile(info, io.BytesIO(payload))
            output.flush()
            os.fsync(output.fileno())
        verify_backup(temporary)
        os.replace(temporary, archive)
        with archive.open("rb") as source:
            checksum = _digest(source)
        archive.with_suffix(archive.suffix + ".sha256").write_text(
            f"{checksum}  {archive.name}\n", encoding="ascii"
        )
        return archive
    finally:
        temporary.unlink(missing_ok=True)


def restore_backup(archive: Path, destination: Path) -> Path:
    if destination.exists() or destination.is_symlink():
        raise ValueError("恢复目标已存在；请使用新的空路径，不能覆盖知识库")
    manifest = verify_backup(archive)
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    required = sum(row["size"] for row in manifest["files"].values())
    if shutil.disk_usage(destination.parent).free < required + 16 * 1024 * 1024:
        raise ValueError("恢复目标磁盘空间不足")
    parent = destination.parent.resolve()
    staging = Path(tempfile.mkdtemp(prefix=".km-restore-", dir=parent))
    try:
        for name in ("vault", "data"):
            (staging / name).mkdir(mode=0o700)
        with tarfile.open(archive, "r:gz") as bundle:
            for name, expected in manifest["files"].items():
                member = bundle.getmember(name)
                if not member.isfile() or not _safe_name(name):
                    raise ValueError("恢复时遇到不安全路径")
                target = staging.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with bundle.extractfile(member) as source, os.fdopen(fd, "wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                with target.open("rb") as restored:
                    if target.stat().st_size != expected["size"] or _digest(restored) != expected["sha256"]:
                        raise ValueError(f"恢复校验失败：{name}")
        for database in (staging / "data").glob("*.sqlite"):
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
                if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("恢复后数据库完整性检查失败")
        # Renaming a nonempty staging directory cannot replace a nonempty destination.
        # Recheck first so even an existing empty directory is not deliberately overwritten.
        if destination.exists() or destination.is_symlink():
            raise ValueError("恢复目标已存在")
        staging.rename(destination)
        return destination
    finally:
        # Only clean this operation's fresh staging directory, never the destination.
        if staging.exists() and staging.resolve().parent == parent and staging.name.startswith(".km-restore-"):
            shutil.rmtree(staging)


def backup_service(args):
    """Quiesce writers and restore their prior state even on a normal cancellation."""
    services = list(dict.fromkeys([args.service, *getattr(args, 'companion_service', [])]))
    running = {}
    # Validate the entire writer set before interrupting any service.
    for service in services:
        state = subprocess.check_output(
            ["systemctl", "show", service, "--property=LoadState", "--value"], text=True
        ).strip()
        if state != "loaded":
            raise RuntimeError("Cannot identify a writer service; backup aborted")
        state = subprocess.run(
            ["systemctl", "is-active", service], text=True, capture_output=True, check=False
        )
        running[service] = state.stdout.strip() not in {"inactive", "failed"}

    def cancel(signum, frame):
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, cancel)
    attempted = set()
    try:
        # Stop ingress first so no new callbacks race the application shutdown.
        for service in reversed(services):
            attempted.add(service)
            subprocess.run(["systemctl", "stop", service], check=True)
        print(create_backup(args.data_root, args.backup_root, args.config_root))
    finally:
        # A repeated cancellation must not interrupt the service recovery itself.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            errors = []
            for service in services:
                if service in attempted and running[service]:
                    try:
                        subprocess.run(["systemctl", "start", service], check=True)
                    except subprocess.CalledProcessError as error:
                        errors.append(error)
            if errors:
                raise RuntimeError('Failed to restore one or more writer services') from errors[0]
        finally:
            signal.signal(signal.SIGTERM, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--data-root", type=Path, default=Path("/srv/knowledge-manager"))
    create.add_argument("--config-root", type=Path, default=Path("/etc/knowledge-manager"))
    create.add_argument("--backup-root", type=Path, default=Path("/var/backups/knowledge-manager"))
    create.add_argument("--service", default="knowledge-manager.service")
    create.add_argument("--companion-service", action="append", default=[],
                        help="Additional database writers; repeat for each service")
    verify = sub.add_parser("verify")
    verify.add_argument("archive", type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.command == "verify":
        print(f"Verified {len(verify_backup(args.archive)['files'])} files")
    elif args.command == "restore":
        print(restore_backup(args.archive, args.destination))
    else:
        import fcntl

        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.@-]*", unit)
               for unit in [args.service, *args.companion_service]):
            parser.error("Invalid service name")
        args.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (args.backup_root / ".backup.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            backup_service(args)


if __name__ == "__main__":
    main()
