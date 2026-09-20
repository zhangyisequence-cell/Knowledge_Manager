"""Static checks for the single public Cloudflare callback route."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


def validate_config(path: Path, hostname: str | None = None) -> dict:
    try:
        config = yaml.safe_load(path.read_text("utf-8")) or {}
    except yaml.YAMLError as error:
        raise ValueError("Cloudflare 配置不是有效 YAML") from error
    ingress = config.get("ingress")
    if not isinstance(ingress, list) or len(ingress) != 2:
        raise ValueError("Cloudflare ingress must contain exactly one route and a final 404")
    route, fallback = ingress
    if fallback != {"service": "http_status:404"}:
        raise ValueError("Cloudflare ingress must end with http_status:404")
    if route.get("path") not in {"/wechat/callback", "^/wechat/callback$"}:
        raise ValueError("Only /wechat/callback may be public")
    if route.get("service") != "http://127.0.0.1:8766":
        raise ValueError("Callback must point to the loopback callback service")
    value = route.get("hostname")
    if not isinstance(value, str) or not value.strip() or "*" in value:
        raise ValueError("WECHAT_PUBLIC_HOSTNAME must be a concrete hostname")
    if hostname and value != hostname:
        raise ValueError("Cloudflare hostname does not match configured hostname")
    for key in ("management", "access", "tunnel-token"):
        if key in config:
            raise ValueError("Management or Access settings must not be exposed")
    return {"hostname": value, "path": "/wechat/callback", "service": route["service"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--hostname")
    args = parser.parse_args()
    try:
        print(json.dumps(validate_config(args.config, args.hostname), ensure_ascii=False))
        return 0
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"Public entry check failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
