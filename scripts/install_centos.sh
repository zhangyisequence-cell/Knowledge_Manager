#!/bin/sh
# Install a Knowledge Manager release on CentOS Stream/RHEL-compatible hosts.
# Execute from an immutable source release under /opt/knowledge-manager/releases/.
# Program upgrades preserve /srv/knowledge-manager and existing local config.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo 'Run with sudo.' >&2
    exit 1
fi

. /etc/os-release
case "${ID:-}" in
    centos|rhel|rocky|almalinux) ;;
    *)
        echo "This installer requires CentOS Stream or a RHEL-compatible host (got ${ID:-unknown})." >&2
        exit 1
        ;;
esac

major=${VERSION_ID%%.*}
case "$major" in
    9|10) ;;
    *)
        echo "This installer is validated for CentOS/RHEL major versions 9 or 10 (got ${VERSION_ID:-unknown})." >&2
        exit 1
        ;;
esac

release=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
case "$release" in
    /opt/knowledge-manager/releases/*) ;;
    *)
        echo 'Unpack the source into /opt/knowledge-manager/releases/<version> first.' >&2
        exit 1
        ;;
esac
if [ -e /opt/knowledge-manager/current ] && [ ! -L /opt/knowledge-manager/current ]; then
    echo 'Refusing to replace an existing non-symlink program directory.' >&2
    exit 1
fi

if command -v dnf5 >/dev/null 2>&1; then
    dnf_cmd=dnf5
else
    dnf_cmd=dnf
fi

# FFmpeg and antiword are normally supplied by EPEL/CRB on CentOS. Enabling
# these repositories is explicit and limited to package metadata; no service
# is started by this installer.
"$dnf_cmd" install -y dnf-plugins-core epel-release || true
if command -v crb >/dev/null 2>&1; then
    crb enable || true
fi
"$dnf_cmd" install -y \
    python3.12 python3.12-devel gcc gcc-c++ make git curl ca-certificates \
    ffmpeg-free antiword tesseract tesseract-langpack-chi_sim \
    tesseract-langpack-eng policycoreutils-python-utils

if ! command -v python3.12 >/dev/null 2>&1; then
    echo 'python3.12 is required. Use CentOS Stream 10 or enable a repository that provides Python 3.12.' >&2
    exit 1
fi
for executable in ffmpeg antiword tesseract curl; do
    if ! command -v "$executable" >/dev/null 2>&1; then
        echo "Required executable is missing after package installation: $executable" >&2
        exit 1
    fi
done

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

# Keep SELinux labels usable when enforcing mode is enabled. If semanage is
# unavailable, the package install above should have provided it; fail rather
# than silently deploying a service that cannot write its own data.
if command -v semanage >/dev/null 2>&1; then
    semanage fcontext -a -t var_lib_t '/srv/knowledge-manager(/.*)?' 2>/dev/null || \
        semanage fcontext -m -t var_lib_t '/srv/knowledge-manager(/.*)?'
    restorecon -RF /srv/knowledge-manager /etc/knowledge-manager
fi

cd "$release"
python3.12 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.lock
.venv/bin/python -m pip check

install -m 0644 "$release/deploy/syncthing@knowledge-manager.service" /etc/systemd/system/syncthing@.service
install -m 0644 "$release/deploy/cloudflared-knowledge-manager.service" /etc/systemd/system/cloudflared-knowledge-manager.service
install -d -m 0755 /usr/local/libexec
install -m 0755 "$release/scripts/run_cloudflared_tunnel.sh" /usr/local/libexec/knowledge-manager-cloudflared

.venv/bin/python scripts/activate_release.py "$release"
systemctl --no-pager status knowledge-manager.service
echo 'CentOS installation complete. Verify /api/health, ingestion, backup restore, and real interface credentials before enabling production inputs.'
