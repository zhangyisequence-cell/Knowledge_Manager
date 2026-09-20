#!/bin/sh
set -eu
config=/etc/knowledge-manager/cloudflared/config.yml
token=/etc/knowledge-manager/cloudflared/token
if [ -s "$token" ]; then
    exec /usr/bin/cloudflared tunnel --no-autoupdate --config "$config" run --token-file "$token"
fi
exec /usr/bin/cloudflared tunnel --no-autoupdate --config "$config" run
