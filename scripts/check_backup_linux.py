"""Exercise real systemd backup cancellation using disposable fixture services only."""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4


def main():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise SystemExit('Run as root on a Linux systemd host.')
    root = Path(tempfile.mkdtemp(prefix='km-backup-check-'))
    suffix = uuid4().hex[:10]
    app = f'km-backup-fixture-{suffix}.service'
    job = f'km-backup-check-{suffix}.service'
    app_unit = Path('/run/systemd/system') / app
    backup = Path(__file__).with_name('server_backup.py').resolve()

    def run(*args, check=True):
        return subprocess.run(args, capture_output=True, text=True, check=check)

    def active(unit):
        return run('systemctl', 'is-active', unit, check=False).stdout.strip()

    def wait_for(predicate):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        raise RuntimeError('Timed out waiting for fixture state')

    try:
        (root / 'source' / 'vault').mkdir(parents=True)
        (root / 'source' / 'data').mkdir()
        (root / 'config').mkdir()
        with (root / 'source' / 'vault' / 'fixture.bin').open('wb') as data:
            for _ in range(256):
                data.write(os.urandom(1024 * 1024))
        # A transient unit is unloaded after stopping and cannot be restarted.
        with app_unit.open('x') as unit:
            unit.write('[Service]\nType=simple\nExecStart=/usr/bin/sleep infinity\n')
        run('systemctl', 'daemon-reload')
        run('systemctl', 'start', app)
        wait_for(lambda: active(app) == 'active')
        run('systemd-run', '--no-block', '--unit=' + job, '--property=Type=oneshot',
            '--property=KillMode=mixed', '--property=TimeoutStopSec=240',
            sys.executable, str(backup), 'create', '--data-root', str(root / 'source'),
            '--backup-root', str(root / 'backups'), '--config-root', str(root / 'config'),
            '--service', app)
        wait_for(lambda: active(app) == 'inactive' and bool(list((root / 'backups').glob('*.partial'))))
        run('systemctl', 'stop', job)
        wait_for(lambda: active(app) == 'active')
        status = run('systemctl', 'show', job, '--property=ExecMainStatus', '--value').stdout.strip()
        if status != str(128 + signal.SIGTERM):
            raise RuntimeError(f'Expected cancelled backup, got exit status {status}')
        if list((root / 'backups').glob('*.partial')) or list((root / 'backups').glob('*.tar.gz')):
            raise RuntimeError('Cancelled backup left a partial or published archive')
        print('PASS: systemd cancellation restored the running service and removed incomplete backup')
    finally:
        for unit in (job, app):
            run('systemctl', 'stop', unit, check=False)
            run('systemctl', 'reset-failed', unit, check=False)
        app_unit.unlink(missing_ok=True)
        run('systemctl', 'daemon-reload', check=False)
        # root is a freshly generated directory, never a configured data path.
        shutil.rmtree(root)


if __name__ == '__main__':
    main()
