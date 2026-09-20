#!/bin/sh
set -eu

check_only=false
vault=/srv/knowledge-manager/vault
config_dir=/var/lib/knowledge-manager/.config/syncthing
windows_id=
android_id=
windows_address=
android_address=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --check-only) check_only=true ;;
        --vault) vault=$2; shift ;;
        --config-dir) config_dir=$2; shift ;;
        --windows-device-id) windows_id=$2; shift ;;
        --android-device-id) android_id=$2; shift ;;
        --windows-address) windows_address=$2; shift ;;
        --android-address) android_address=$2; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ "$(id -u)" -ne 0 ] && [ "$check_only" != true ]; then
    echo 'Run with sudo for installation.' >&2
    exit 1
fi
if [ ! -d "$vault" ]; then
    echo "Vault directory is missing: $vault" >&2
    exit 1
fi
if ! id knowledge-manager >/dev/null 2>&1; then
    echo 'knowledge-manager service account is missing.' >&2
    exit 1
fi
if ! command -v tailscale >/dev/null 2>&1; then
    echo 'tailscale is not installed or not on PATH.' >&2
    exit 1
fi
tailscale_ip=$(tailscale ip -4 2>/dev/null || true)
if [ -z "$tailscale_ip" ]; then
    echo 'tailscale ip -4 returned no address; refusing public or LAN fallback.' >&2
    exit 1
fi
if [ "$check_only" = true ]; then
    if ! command -v syncthing >/dev/null 2>&1; then
        echo 'check-only: syncthing is missing (no changes made).' >&2
        exit 1
    fi
    echo "check-only: Vault=$vault Tailscale=$tailscale_ip config=$config_dir"
    exit 0
fi

if ! command -v syncthing >/dev/null 2>&1; then
    apt-get update
    apt-get install -y --no-install-recommends syncthing
fi
install -d -o knowledge-manager -g knowledge-manager -m 0700 "$config_dir"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHONPATH="$script_dir" python3 - "$vault" "$config_dir" "$windows_id" "$android_id" "$windows_address" "$android_address" <<'PY'
import sys
from pathlib import Path
from syncthing_config import render_config, write_atomic, write_stignore

vault = Path(sys.argv[1])
config_dir = Path(sys.argv[2])
ids = [value for value in sys.argv[3:5] if value]
addresses = {
    device_id: address
    for device_id, address in zip(ids, sys.argv[5:7], strict=False)
    if address
}
write_atomic(config_dir / "config.xml", render_config(vault, ids, addresses), 0o600)
write_stignore(vault)
PY
chown -R knowledge-manager:knowledge-manager "$config_dir" "$vault/.stignore"
chmod 0600 "$config_dir/config.xml"
install -m 0644 "$(dirname "$0")/../deploy/syncthing@knowledge-manager.service" /etc/systemd/system/syncthing@.service
systemctl daemon-reload
echo "Syncthing configuration installed for Tailscale address $tailscale_ip; pair device IDs before enabling the service."
