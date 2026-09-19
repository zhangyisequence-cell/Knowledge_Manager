"""Deterministic retention selection for verified Knowledge Manager archives."""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE_RE = re.compile(
    r"^knowledge-(?P<stamp>\d{8}T\d{6})-(?P<nonce>[0-9a-f]{8})(?P<upgrade>-upgrade)?\.tar\.gz$"
)


def _archive_time(path: Path) -> datetime | None:
    match = ARCHIVE_RE.fullmatch(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _verified(path: Path) -> bool:
    checksum = path.with_suffix(path.suffix + ".sha256")
    return path.is_file() and checksum.is_file() and not path.name.endswith(".partial")


def _upgrade_marked(path: Path) -> bool:
    return "-upgrade" in path.stem or path.with_suffix(path.suffix + ".upgrade").is_file()


def select_retained(paths: Iterable[Path], now: datetime | None = None) -> set[Path]:
    """Return verified archives retained by hourly/daily/weekly generations.

    Selection is pure: it never removes files and only accepts the strict archive
    naming format plus a checksum sidecar. ``now`` is accepted for deterministic
    callers and future age policies; current retention is generation based.
    """
    del now  # generation windows are relative to the newest archive, not wall time
    candidates = {
        Path(path).resolve()
        for path in paths
        if _archive_time(Path(path)) is not None and _verified(Path(path))
    }
    if not candidates:
        return set()
    ordered = sorted(candidates, key=lambda path: (_archive_time(path), path.name), reverse=True)
    retained: set[Path] = {path for path in ordered if _upgrade_marked(path)}
    retained.add(ordered[0])

    def keep_generations(limit: int, key):
        seen = set()
        for path in ordered:
            generation = key(_archive_time(path))
            if generation in seen:
                continue
            seen.add(generation)
            retained.add(path)
            if len(seen) >= limit:
                break

    keep_generations(24, lambda value: (value.year, value.timetuple().tm_yday, value.hour))
    keep_generations(30, lambda value: (value.year, value.timetuple().tm_yday))
    keep_generations(12, lambda value: (value.isocalendar().year, value.isocalendar().week))
    return retained


def prune(backup_root: Path, now: datetime | None = None) -> list[Path]:
    """Delete only verified, unretained archives inside ``backup_root``."""
    root = backup_root.resolve()
    archives = list(root.glob("knowledge-*.tar.gz"))
    retained = select_retained(archives, now)
    removed: list[Path] = []
    for archive in archives:
        resolved = archive.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("备份路径超出托管目录")
        if not _verified(archive):
            continue
        if resolved not in retained:
            archive.unlink()
            archive.with_suffix(archive.suffix + ".sha256").unlink(missing_ok=True)
            removed.append(archive)
    return removed
