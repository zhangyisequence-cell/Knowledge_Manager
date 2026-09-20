from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.backup_retention import select_retained


def _archive(root, stamp, nonce="deadbeef", *, checksum=True, upgrade=False):
    suffix = "-upgrade" if upgrade else ""
    path = root / f"knowledge-{stamp}-{nonce}{suffix}.tar.gz"
    path.write_bytes(b"verified archive")
    if checksum:
        path.with_suffix(path.suffix + ".sha256").write_text("checksum\n", encoding="ascii")
    return path


def test_retention_keeps_hour_day_week_upgrade_and_newest_verified(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    paths = []
    for index in range(80 * 24):
        stamp = (start + timedelta(hours=index)).strftime("%Y%m%dT%H%M%S")
        paths.append(_archive(tmp_path, stamp, f"{index:08x}"[-8:]))
    marked = _archive(tmp_path, "20200101T000000", "cafebabe", upgrade=True)
    partial = tmp_path / "knowledge-20260101T000000-feedface.tar.gz.partial"
    partial.write_bytes(b"partial")
    missing_checksum = _archive(tmp_path, "20260101T010000", "facefeed", checksum=False)
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")

    retained = select_retained(paths + [marked, partial, missing_checksum, unrelated], start)

    assert marked.resolve() in retained
    assert paths[-1].resolve() in retained
    assert partial.resolve() not in retained
    assert missing_checksum.resolve() not in retained
    assert unrelated.resolve() not in retained
    assert len(retained) <= 24 + 30 + 12 + 1


def test_retention_does_not_require_wall_clock_or_delete_files(tmp_path):
    path = _archive(tmp_path, "20260919T120000", "12345678")
    retained = select_retained([path], datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert retained == {path.resolve()}
    assert path.exists()
