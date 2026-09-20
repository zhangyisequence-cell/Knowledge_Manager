from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_cloudflared_installer_supports_token_from_stdin_without_cli_secret():
    script = (ROOT / "scripts" / "install_cloudflared_tunnel.sh").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "wechat-interface.md").read_text(encoding="utf-8")

    assert "--token-stdin" in script
    assert "read -r token" in script
    assert "--token '<" not in docs
