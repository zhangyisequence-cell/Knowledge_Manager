#!/bin/sh
# Execute from an immutable source release under /opt/knowledge-manager/releases/.
# Program upgrades preserve /srv/knowledge-manager and existing local configuration.
set -eu
if [ "$(id -u)" -ne 0 ]; then echo 'Run with sudo.' >&2; exit 1; fi
. /etc/os-release
if [ "$ID" != ubuntu ] || [ "$VERSION_ID" != 24.04 ]; then
    echo 'This installer is validated for Ubuntu 24.04 only.' >&2; exit 1
fi
release=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
case "$release" in
    /opt/knowledge-manager/releases/*) ;;
    *) echo 'Unpack the source into /opt/knowledge-manager/releases/<version> first.' >&2; exit 1 ;;
esac
if [ -e /opt/knowledge-manager/current ] && [ ! -L /opt/knowledge-manager/current ]; then
    echo 'Refusing to replace an existing non-symlink program directory.' >&2; exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3.12-venv ffmpeg antiword tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng curl ca-certificates
if ! id knowledge-manager >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/knowledge-manager --shell /usr/sbin/nologin knowledge-manager
fi
install -d -m 0750 -o knowledge-manager -g knowledge-manager /srv/knowledge-manager
for directory in data vault model-cache; do
    install -d -m 0700 -o knowledge-manager -g knowledge-manager "/srv/knowledge-manager/$directory"
done
install -d -m 0750 -o root -g knowledge-manager /etc/knowledge-manager
install -d -m 0700 /var/backups/knowledge-manager
if [ ! -e /etc/knowledge-manager/server.env ]; then
    install -m 0600 "$release/deploy/server.env.example" /etc/knowledge-manager/server.env
fi
cd "$release"
python3.12 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.lock
.venv/bin/python -m pip check
install -m 0644 deploy/knowledge-manager.service /etc/systemd/system/knowledge-manager.service
install -m 0644 deploy/knowledge-manager-backup.service /etc/systemd/system/knowledge-manager-backup.service
install -m 0644 deploy/knowledge-manager-backup.timer /etc/systemd/system/knowledge-manager-backup.timer
temporary_link="/opt/knowledge-manager/.current-$$"
trap 'test ! -L "$temporary_link" || rm -- "$temporary_link"' EXIT HUP INT TERM
ln -s "$release" "$temporary_link"
mv -Tf "$temporary_link" /opt/knowledge-manager/current
systemd-analyze verify /etc/systemd/system/knowledge-manager.service /etc/systemd/system/knowledge-manager-backup.service /etc/systemd/system/knowledge-manager-backup.timer
systemctl daemon-reload
systemctl enable knowledge-manager.service knowledge-manager-backup.timer
systemctl restart knowledge-manager.service
systemctl start knowledge-manager-backup.timer
systemctl --no-pager status knowledge-manager.service
echo 'Service started. Verify /api/health, actual ingestion, and backup restore before declaring ready.'
