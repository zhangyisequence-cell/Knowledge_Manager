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
apt-get install -y --no-install-recommends python3.12-venv ffmpeg antiword tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng curl ca-certificates
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
# Install the template only; Syncthing remains disabled until Tailscale and
# explicit Windows/Android device IDs have been verified by its installer.
install -m 0644 "$release/deploy/syncthing@knowledge-manager.service" \
    /etc/systemd/system/syncthing@.service
install -m 0644 "$release/deploy/cloudflared-knowledge-manager.service" \
    /etc/systemd/system/cloudflared-knowledge-manager.service
install -d -m 0755 /usr/local/libexec
install -m 0755 "$release/scripts/run_cloudflared_tunnel.sh" \
    /usr/local/libexec/knowledge-manager-cloudflared
.venv/bin/python scripts/activate_release.py "$release"
systemctl --no-pager status knowledge-manager.service
echo 'Service started. Verify /api/health, actual ingestion, and backup restore before declaring ready.'
