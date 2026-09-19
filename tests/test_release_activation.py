import sqlite3
from contextlib import contextmanager, nullcontext

import pytest

from scripts import activate_release as activation
from scripts.activate_release import activate_release
from scripts.server_backup import create_backup

UNIT_NAMES = (
    "knowledge-manager.service",
    "knowledge-manager-wechat.service",
    "syncthing@knowledge-manager.service",
    "cloudflared-knowledge-manager.service",
    "knowledge-manager-backup.service",
    "knowledge-manager-backup.timer",
)


class FakeSystemd:
    def __init__(self, fail_start=False, missing_services=False):
        self.fail_start = fail_start
        self.missing_services = missing_services
        self.calls = []

    def __call__(self, *command):
        self.calls.append(command)
        if command[:2] == ("systemctl", "show"):
            return "not-found" if self.missing_services else "loaded"
        if self.fail_start and command == ("systemctl", "start", "knowledge-manager.service"):
            raise RuntimeError("synthetic start failure")
        return ""


@contextmanager
def recorded_lock(events):
    events.append(("lock", "acquire"))
    try:
        yield
    finally:
        events.append(("lock", "release"))


def fixture(tmp_path):
    root = tmp_path / "root"
    release = tmp_path / "candidate"
    old = tmp_path / "old"
    (root / "opt/knowledge-manager").mkdir(parents=True)
    old.mkdir()
    (root / "etc/systemd/system").mkdir(parents=True)
    (root / "etc/knowledge-manager").mkdir(parents=True)
    (root / "etc/knowledge-manager/server.env").write_text(
        "SECRET=preserved\nKNOWLEDGE_PORT=9123\n"
    )
    (root / "etc/knowledge-manager/wechat.enabled").touch()
    (root / "srv/knowledge-manager/vault").mkdir(parents=True)
    (root / "srv/knowledge-manager/data").mkdir()
    (root / "srv/knowledge-manager/vault/note.md").write_text("preserve me")
    with sqlite3.connect(root / "srv/knowledge-manager/data/knowledge.sqlite") as db:
        db.execute("CREATE TABLE items(value TEXT)")
        db.execute("INSERT INTO items VALUES ('preserve me')")
    (root / "var/backups/knowledge-manager").mkdir(parents=True)
    (release / "deploy").mkdir(parents=True)
    for name in UNIT_NAMES:
        (root / "etc/systemd/system" / name).write_text("old " + name)
        (release / "deploy" / name).write_text("new " + name)
    return root, release, old


def test_activation_quiesces_backs_up_then_starts_main_before_callback(tmp_path):
    root, release, old = fixture(tmp_path)
    systemd = FakeSystemd()
    current = {"target": old}

    def backup(*args):
        systemd.calls.append(("backup",))
        return create_backup(*args)

    def healthy(port):
        systemd.calls.append(("health", port))

    archive = activate_release(
        release,
        root=root,
        run=systemd,
        read_current=lambda: current["target"],
        write_current=lambda target: current.update(target=target),
        backup=backup,
        lock=lambda path: recorded_lock(systemd.calls),
        health_check=healthy,
    )

    calls = systemd.calls
    assert calls.index(("systemctl", "stop", "knowledge-manager-wechat.service")) < calls.index(
        ("systemctl", "stop", "syncthing@knowledge-manager.service")
    ) < calls.index(("systemctl", "stop", "knowledge-manager.service")
    ) < calls.index(("backup",))
    assert calls.index(("systemctl", "stop", "knowledge-manager-backup.timer")) < calls.index(
        ("systemctl", "stop", "knowledge-manager-backup.service")
    ) < calls.index(("lock", "acquire")) < calls.index(
        ("systemctl", "stop", "knowledge-manager-wechat.service")
    )
    assert calls.index(("lock", "release")) > calls.index(
        ("systemctl", "start", "knowledge-manager-wechat.service")
    )
    assert calls.index(("systemctl", "start", "knowledge-manager.service")) < calls.index(
        ("health", 9123)
    ) < calls.index(
        ("systemctl", "start", "knowledge-manager-wechat.service")
    )
    assert archive.is_file()
    assert current["target"] == release.resolve()
    assert (root / "srv/knowledge-manager/vault/note.md").read_text() == "preserve me"


def test_failed_activation_restores_program_and_units_but_keeps_writers_stopped(tmp_path):
    root, release, old = fixture(tmp_path)
    systemd = FakeSystemd(fail_start=True)
    current = {"target": old}

    with pytest.raises(RuntimeError, match="synthetic start failure"):
        activate_release(
            release,
            root=root,
            run=systemd,
            read_current=lambda: current["target"],
            write_current=lambda target: current.update(target=target),
            lock=lambda path: nullcontext(),
        )

    assert current["target"] == old
    for name in UNIT_NAMES:
        assert (root / "etc/systemd/system" / name).read_text() == "old " + name
    assert ("systemctl", "disable", "knowledge-manager.service") in systemd.calls
    assert ("systemctl", "disable", "knowledge-manager-wechat.service") in systemd.calls
    assert ("systemctl", "disable", "knowledge-manager-backup.timer") in systemd.calls
    assert not any(call[:2] == ("systemctl", "start") for call in systemd.calls[systemd.calls.index(("systemctl", "start", "knowledge-manager.service")) + 1:])
    with sqlite3.connect(root / "srv/knowledge-manager/data/knowledge.sqlite") as db:
        assert db.execute("SELECT value FROM items").fetchone() == ("preserve me",)


def test_partial_unit_publication_is_rolled_back(tmp_path, monkeypatch):
    root, release, old = fixture(tmp_path)
    systemd = FakeSystemd()
    current = {"target": old}
    real_replace = activation._replace_file
    replacements = 0

    def fail_during_publication(source, destination):
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("synthetic publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(activation, "_replace_file", fail_during_publication)
    with pytest.raises(RuntimeError, match="synthetic publication failure"):
        activate_release(
            release,
            root=root,
            run=systemd,
            read_current=lambda: current["target"],
            write_current=lambda target: current.update(target=target),
            lock=lambda path: nullcontext(),
        )

    assert current["target"] == old
    for name in UNIT_NAMES:
        assert (root / "etc/systemd/system" / name).read_text() == "old " + name


def test_first_install_tolerates_services_that_do_not_exist_yet(tmp_path):
    root, release, _ = fixture(tmp_path)
    (root / "etc/knowledge-manager/wechat.enabled").unlink()
    current = {"target": None}
    systemd = FakeSystemd(missing_services=True)

    archive = activate_release(
        release,
        root=root,
        run=systemd,
        read_current=lambda: current["target"],
        write_current=lambda target: current.update(target=target),
        lock=lambda path: nullcontext(),
        health_check=lambda port: None,
    )

    assert archive.is_file()
    assert current["target"] == release.resolve()
    assert ("systemctl", "disable", "knowledge-manager-wechat.service") in systemd.calls
    assert ("systemctl", "start", "knowledge-manager-wechat.service") not in systemd.calls


def test_unhealthy_candidate_rolls_back_before_callback_and_disables_auto_start(tmp_path):
    root, release, old = fixture(tmp_path)
    systemd = FakeSystemd()
    current = {"target": old}

    with pytest.raises(RuntimeError, match="health check failed"):
        activate_release(
            release,
            root=root,
            run=systemd,
            read_current=lambda: current["target"],
            write_current=lambda target: current.update(target=target),
            lock=lambda path: nullcontext(),
            health_check=lambda port: (_ for _ in ()).throw(RuntimeError("health check failed")),
        )

    assert current["target"] == old
    assert ("systemctl", "start", "knowledge-manager-wechat.service") not in systemd.calls
    assert {
        ("systemctl", "disable", "knowledge-manager.service"),
        ("systemctl", "disable", "knowledge-manager-wechat.service"),
        ("systemctl", "disable", "knowledge-manager-backup.timer"),
    }.issubset(systemd.calls)


def test_invalid_deepseek_configuration_aborts_before_stopping_services(tmp_path):
    root, release, _old = fixture(tmp_path)
    (root / "etc/knowledge-manager/server.env").write_text(
        "KNOWLEDGE_PORT=9123\nAI_ENABLED=true\nOPENAI_MODEL=deepseek-chat\n"
        "KNOWLEDGE_OPENAI_BASE_URL=https://api.deepseek.com\n"
    )
    systemd = FakeSystemd()
    with pytest.raises(ValueError, match="API key"):
        activate_release(
            release,
            root=root,
            run=systemd,
            read_current=lambda: None,
            write_current=lambda target: None,
            lock=lambda path: nullcontext(),
        )
    assert not any(call[:2] == ("systemctl", "stop") for call in systemd.calls)


def test_invalid_cloudflare_marker_aborts_before_stopping_services(tmp_path):
    root, release, _old = fixture(tmp_path)
    marker = root / "etc/knowledge-manager/cloudflared.enabled"
    marker.touch()
    (root / "etc/knowledge-manager/cloudflared").mkdir()
    (root / "etc/knowledge-manager/cloudflared/token").write_text("token")
    (root / "etc/knowledge-manager/cloudflared/config.yml").write_text(
        'ingress:\n  - hostname: "*.bad"\n    path: /api\n    service: http://127.0.0.1:8787\n'
        "  - service: http_status:404\n"
    )
    systemd = FakeSystemd()
    with pytest.raises(ValueError, match="Only|concrete"):
        activate_release(
            release,
            root=root,
            run=systemd,
            read_current=lambda: None,
            write_current=lambda target: None,
            lock=lambda path: nullcontext(),
        )
    assert not any(call[:2] == ("systemctl", "stop") for call in systemd.calls)
