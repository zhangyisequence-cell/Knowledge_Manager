import io
import json
import signal
import sqlite3
import tarfile
from types import SimpleNamespace

import pytest

from scripts.server_backup import create_backup, restore_backup, verify_backup


def sample_data(root):
    (root / 'vault' / '_attachments').mkdir(parents=True)
    (root / 'vault' / '知识.md').write_text('来源和摘要\n', encoding='utf-8')
    (root / 'vault' / '_attachments' / '原件.bin').write_bytes(bytes(range(256)))
    (root / 'data').mkdir()
    with sqlite3.connect(root / 'data' / 'knowledge.sqlite') as db:
        db.execute('CREATE TABLE items (body TEXT)')
        db.execute('INSERT INTO items VALUES (?)', ('保存知识',))
    (root / 'model-cache').mkdir()
    (root / 'model-cache' / 'model.bin').write_bytes(b'not part of a data backup')


def test_backup_restore_preserves_vault_originals_and_database(tmp_path):
    source = tmp_path / 'source'
    sample_data(source)
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'server.env').write_text('DUMMY_SETTING=synthetic\n')
    archive = create_backup(source, tmp_path / 'backups', config)
    manifest = verify_backup(archive)
    assert 'vault/知识.md' in manifest['files']
    assert all(not name.startswith('model-cache/') for name in manifest['files'])
    restored = restore_backup(archive, tmp_path / 'restore')
    for folder in ('vault', 'data'):
        for path in (source / folder).rglob('*'):
            if path.is_file():
                assert (restored / path.relative_to(source)).read_bytes() == path.read_bytes()
    assert (restored / 'config' / 'server.env').read_bytes() == (config / 'server.env').read_bytes()
    with sqlite3.connect(restored / 'data' / 'knowledge.sqlite') as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT body FROM items').fetchone()[0] == '保存知识'


def test_restore_refuses_existing_destination(tmp_path):
    source = tmp_path / 'source'
    sample_data(source)
    archive = create_backup(source, tmp_path / 'backups')
    destination = tmp_path / 'existing'
    destination.mkdir()
    (destination / 'keep.txt').write_text('keep')
    with pytest.raises(ValueError, match='存在'):
        restore_backup(archive, destination)
    assert (destination / 'keep.txt').read_text() == 'keep'


def test_tampered_archive_is_rejected(tmp_path):
    source = tmp_path / 'source'
    sample_data(source)
    archive = create_backup(source, tmp_path / 'backups')
    tampered = tmp_path / 'tampered.tar.gz'
    with tarfile.open(archive) as original, tarfile.open(tampered, 'w:gz') as output:
        for member in original:
            data = original.extractfile(member).read()
            if member.name == 'vault/知识.md':
                data = b'altered'
                member.size = len(data)
            output.addfile(member, io.BytesIO(data))
    with pytest.raises(ValueError, match='校验'):
        restore_backup(tampered, tmp_path / 'restore')
    assert not (tmp_path / 'restore').exists()


@pytest.mark.parametrize('name,kind', [('vault/../../escape', 'file'), ('vault/link', 'symlink')])
def test_unsafe_archive_members_are_rejected(tmp_path, name, kind):
    archive = tmp_path / 'unsafe.tar.gz'
    with tarfile.open(archive, 'w:gz') as output:
        payload = json.dumps({'version': 1, 'files': {}}).encode()
        manifest = tarfile.TarInfo('manifest.json')
        manifest.size = len(payload)
        output.addfile(manifest, io.BytesIO(payload))
        member = tarfile.TarInfo(name)
        if kind == 'symlink':
            member.type = tarfile.SYMTYPE
            member.linkname = '/etc/passwd'
        output.addfile(member, io.BytesIO(b''))
    with pytest.raises(ValueError, match='不安全路径或非普通文件'):
        restore_backup(archive, tmp_path / 'restore')
    assert not (tmp_path / 'escape').exists()
    assert not (tmp_path / 'restore').exists()


def test_backup_does_not_succeed_without_expected_data_dirs(tmp_path):
    with pytest.raises(ValueError):
        create_backup(tmp_path / 'missing', tmp_path / 'backups')


@pytest.mark.parametrize('initial_state', ['active', 'inactive'])
def test_cancelled_backup_restores_previous_service_state(monkeypatch, tmp_path, initial_state):
    from scripts import server_backup

    calls = []
    monkeypatch.setattr(server_backup.subprocess, 'check_output', lambda *a, **k: 'loaded\n')

    def systemctl(command, **kwargs):
        calls.append(command[1])
        return SimpleNamespace(stdout=initial_state + '\n')

    def cancelled(*args):
        signal.raise_signal(signal.SIGTERM)
        pytest.fail('SIGTERM should cancel the backup')

    monkeypatch.setattr(server_backup.subprocess, 'run', systemctl)
    monkeypatch.setattr(server_backup, 'create_backup', cancelled)
    args = SimpleNamespace(service='knowledge-manager.service', data_root=tmp_path,
                           backup_root=tmp_path, config_root=tmp_path)
    old_handler = signal.getsignal(signal.SIGTERM)
    with pytest.raises(SystemExit) as stopped:
        server_backup.backup_service(args)
    assert stopped.value.code == 128 + signal.SIGTERM
    assert calls == ['is-active', 'stop'] + (['start'] if initial_state == 'active' else [])
    assert signal.getsignal(signal.SIGTERM) == old_handler


@pytest.mark.parametrize('callback_state', ['active', 'inactive'])
def test_backup_quiesces_all_writers_and_restores_only_running_services(monkeypatch, tmp_path, callback_state):
    from scripts import server_backup

    app, callback = 'knowledge-manager.service', 'knowledge-manager-wechat.service'
    states = {app: 'active', callback: callback_state}
    calls = []
    monkeypatch.setattr(server_backup.subprocess, 'check_output', lambda *a, **k: 'loaded\n')

    def systemctl(command, **kwargs):
        action, unit = command[1:3]
        calls.append((action, unit))
        if action == 'is-active':
            return SimpleNamespace(stdout=states[unit] + '\n')
        states[unit] = 'inactive' if action == 'stop' else 'active'
        return SimpleNamespace(stdout='')

    def snapshot(*args):
        assert states == {app: 'inactive', callback: 'inactive'}
        raise RuntimeError('synthetic archive failure')

    monkeypatch.setattr(server_backup.subprocess, 'run', systemctl)
    monkeypatch.setattr(server_backup, 'create_backup', snapshot)
    args = SimpleNamespace(service=app, companion_service=[callback], data_root=tmp_path,
                           backup_root=tmp_path, config_root=tmp_path)
    with pytest.raises(RuntimeError, match='synthetic archive failure'):
        server_backup.backup_service(args)
    assert states == {app: 'active', callback: callback_state}
    assert [call for call in calls if call[0] == 'stop'] == [('stop', callback), ('stop', app)]
    assert [call for call in calls if call[0] == 'start'] == [('start', app)] + (
        [('start', callback)] if callback_state == 'active' else [])


def test_unknown_companion_aborts_before_stopping_any_writer(monkeypatch, tmp_path):
    from scripts import server_backup

    calls = []
    monkeypatch.setattr(server_backup.subprocess, 'check_output',
                        lambda command, **k: 'not-found\n' if command[2] == 'missing.service' else 'loaded\n')
    monkeypatch.setattr(server_backup.subprocess, 'run',
                        lambda command, **k: calls.append(command) or SimpleNamespace(stdout='active\n'))
    monkeypatch.setattr(server_backup, 'create_backup', lambda *a: pytest.fail('must not archive'))
    args = SimpleNamespace(service='knowledge-manager.service', companion_service=['missing.service'],
                           data_root=tmp_path, backup_root=tmp_path, config_root=tmp_path)
    with pytest.raises(RuntimeError, match='service'):
        server_backup.backup_service(args)
    assert all(command[1] not in {'stop', 'start'} for command in calls)


@pytest.mark.parametrize("sync_state", ["active", "inactive"])
def test_backup_orders_callback_syncthing_application_and_restores_state(
    monkeypatch, tmp_path, sync_state
):
    from scripts import server_backup

    app = "knowledge-manager.service"
    callback = "knowledge-manager-wechat.service"
    sync = "syncthing@knowledge-manager.service"
    states = {app: "active", callback: "active", sync: sync_state}
    calls = []
    monkeypatch.setattr(server_backup.subprocess, "check_output", lambda *a, **k: "loaded\n")

    def systemctl(command, **kwargs):
        action, unit = command[1:3]
        calls.append((action, unit))
        if action == "is-active":
            return SimpleNamespace(stdout=states[unit] + "\n")
        states[unit] = "inactive" if action == "stop" else "active"
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(server_backup.subprocess, "run", systemctl)
    monkeypatch.setattr(server_backup, "create_backup", lambda *a: None)
    args = SimpleNamespace(
        service=app,
        companion_service=[callback, sync],
        data_root=tmp_path,
        backup_root=tmp_path,
        config_root=tmp_path,
    )
    server_backup.backup_service(args)
    assert [unit for action, unit in calls if action == "stop"] == [sync, callback, app]
    expected = [app, callback] + ([sync] if sync_state == "active" else [])
    assert [unit for action, unit in calls if action == "start"] == expected
