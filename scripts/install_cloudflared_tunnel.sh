#!/bin/sh
set -eu

check_only=false
hostname=
token=
token_stdin=false
credentials=
config_dir=/etc/knowledge-manager/cloudflared
while [ "$#" -gt 0 ]; do
    case "$1" in
        --check-only) check_only=true ;;
        --hostname) hostname=$2; shift ;;
        --token) token=$2; shift ;;
        --token-stdin) token_stdin=true ;;
        --credentials-file) credentials=$2; shift ;;
        --config-dir) config_dir=$2; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done
case "$hostname" in
    ""|*'*'*|*'/'*|*' '* ) echo 'A concrete WECHAT_PUBLIC_HOSTNAME is required.' >&2; exit 1 ;;
esac
if [ "$(id -u)" -ne 0 ] && [ "$check_only" != true ]; then
    echo 'Run with sudo for installation.' >&2
    exit 1
fi
if [ "$check_only" = true ]; then
    [ -x "$(command -v cloudflared 2>/dev/null || true)" ] || {
        echo 'check-only: cloudflared is missing (no changes made).' >&2; exit 1;
    }
    echo "check-only: hostname=$hostname config=$config_dir"
    exit 0
fi
if [ "$token_stdin" = true ] && { [ -n "$token" ] || [ -n "$credentials" ]; }; then
    echo '--token-stdin cannot be combined with --token or --credentials-file.' >&2
    exit 1
fi
if [ "$token_stdin" = true ]; then
    if [ -t 0 ] && [ -t 2 ]; then
        printf 'Cloudflare tunnel token (input hidden): ' >&2
        terminal_state=$(stty -g)
        stty -echo
        IFS= read -r token || true
        stty "$terminal_state"
        printf '\n' >&2
    else
        IFS= read -r token || true
    fi
fi
if [ -z "$token" ] && [ -z "$credentials" ]; then
    echo 'Provide --token-stdin or a local credentials file path.' >&2
    exit 1
fi
command -v cloudflared >/dev/null 2>&1 || {
    echo 'Install cloudflared from the verified vendor package before running this installer.' >&2
    exit 1
}
install -d -m 0700 "$config_dir"
if [ -n "$token" ]; then
    printf '%s' "$token" > "$config_dir/token"
    chmod 0600 "$config_dir/token"
    auth_config=
elif [ ! -f "$credentials" ]; then
    echo "Credentials file does not exist: $credentials" >&2
    exit 1
else
    install -m 0600 "$credentials" "$config_dir/credentials.json"
tunnel_id=$(python3 - "$config_dir/credentials.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    data = json.load(stream)
value = data.get("TunnelID") or data.get("tunnel_id")
if not isinstance(value, str) or not value.strip():
    raise SystemExit("credentials file has no tunnel ID")
print(value.strip())
PY
)
    auth_config="tunnel: $tunnel_id
credentials-file: $config_dir/credentials.json"
fi
cat > "$config_dir/config.yml" <<EOF
$auth_config
ingress:
  - hostname: $hostname
    path: /wechat/callback
    service: http://127.0.0.1:8766
  - service: http_status:404
EOF
chmod 0600 "$config_dir/config.yml"
install -m 0644 "$(dirname "$0")/../deploy/cloudflared-knowledge-manager.service" \
    /etc/systemd/system/cloudflared-knowledge-manager.service
install -d -m 0755 /usr/local/libexec
install -m 0755 "$(dirname "$0")/run_cloudflared_tunnel.sh" \
    /usr/local/libexec/knowledge-manager-cloudflared
systemctl daemon-reload
echo "Cloudflare Tunnel configured for $hostname; only /wechat/callback is routed and the service remains disabled until verified."
