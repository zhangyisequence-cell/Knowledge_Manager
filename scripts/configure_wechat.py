"""Securely configure the local WeChat客服 credentials without enabling ingress."""
from __future__ import annotations

import argparse
import getpass
import os
import re
import tempfile
from pathlib import Path

SECRET_KEYS = (
    "WECHAT_SECRET",
    "WECHAT_CALLBACK_TOKEN",
    "WECHAT_ENCODING_AES_KEY",
)
REQUIRED_KEYS = ("WECHAT_CORP_ID", *SECRET_KEYS, "WECHAT_ALLOWED_ACCOUNTS", "WECHAT_ALLOWED_SENDERS")


def _required(value: str, key: str) -> str:
    value = value.strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError(f"{key} must be non-empty and contain no whitespace")
    return value


def _csv(value: str, key: str) -> str:
    parts = [part.strip() for part in value.split(",")]
    if not value.strip() or any(not part or any(character.isspace() for character in part) for part in parts):
        raise ValueError(f"{key} must contain one or more comma-separated values")
    return ",".join(parts)


def validate_values(values: dict[str, str]) -> dict[str, str]:
    missing = [key for key in REQUIRED_KEYS if not str(values.get(key, "")).strip()]
    if missing:
        raise ValueError("Missing required WeChat settings: " + ", ".join(missing))
    normalized = {
        "WECHAT_CORP_ID": _required(values["WECHAT_CORP_ID"], "WECHAT_CORP_ID"),
        "WECHAT_SECRET": _required(values["WECHAT_SECRET"], "WECHAT_SECRET"),
        "WECHAT_CALLBACK_TOKEN": _required(values["WECHAT_CALLBACK_TOKEN"], "WECHAT_CALLBACK_TOKEN"),
        "WECHAT_ENCODING_AES_KEY": _required(
            values["WECHAT_ENCODING_AES_KEY"], "WECHAT_ENCODING_AES_KEY"
        ),
        "WECHAT_ALLOWED_ACCOUNTS": _csv(values["WECHAT_ALLOWED_ACCOUNTS"], "WECHAT_ALLOWED_ACCOUNTS"),
        "WECHAT_ALLOWED_SENDERS": _csv(values["WECHAT_ALLOWED_SENDERS"], "WECHAT_ALLOWED_SENDERS"),
    }
    if not re.fullmatch(r"[A-Za-z0-9]{43}", normalized["WECHAT_ENCODING_AES_KEY"]):
        raise ValueError("WECHAT_ENCODING_AES_KEY must be the 43-character WeChat key")
    return normalized


def _write_environment(path: Path, updates: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    written: set[str] = set()
    lines: list[str] = []
    for line in existing:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in updates:
            lines.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            lines.append(line)
    if lines and lines[-1] != "":
        lines.append("")
    lines.extend(f"{key}={value}" for key, value in updates.items() if key not in written)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write("\n".join(lines).rstrip("\n") + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def configure_files(environment: Path, marker: Path, values: dict[str, str]) -> None:
    normalized = validate_values(values)
    updates = {"WECHAT_ENABLED": "false", **normalized}
    _write_environment(environment, updates)
    marker.unlink(missing_ok=True)


def _prompt_values() -> dict[str, str]:
    return {
        "WECHAT_CORP_ID": input("CorpID: "),
        "WECHAT_SECRET": getpass.getpass("客服应用 Secret: "),
        "WECHAT_CALLBACK_TOKEN": getpass.getpass("回调 Token: "),
        "WECHAT_ENCODING_AES_KEY": getpass.getpass("EncodingAESKey（43 个字符）: "),
        "WECHAT_ALLOWED_ACCOUNTS": input("允许的 open_kfid（逗号分隔）: "),
        "WECHAT_ALLOWED_SENDERS": input("允许的发送者标识（逗号分隔）: "),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, default=Path("/etc/knowledge-manager/server.env"))
    parser.add_argument("--marker", type=Path, default=Path("/etc/knowledge-manager/wechat.enabled"))
    args = parser.parse_args()
    try:
        configure_files(args.environment, args.marker, _prompt_values())
    except (OSError, ValueError, EOFError, KeyboardInterrupt) as error:
        print(f"微信配置未写入：{error}", file=os.sys.stderr)
        return 1
    print(f"微信配置已写入 {args.environment}；WECHAT_ENABLED=false，启用标记已移除。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

