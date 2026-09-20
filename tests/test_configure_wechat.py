import os
import stat
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "configure_wechat.py"


def _values(**overrides):
    values = {
        "WECHAT_CORP_ID": "corp-123",
        "WECHAT_SECRET": "secret-value",
        "WECHAT_CALLBACK_TOKEN": "callback-token",
        "WECHAT_ENCODING_AES_KEY": "a" * 43,
        "WECHAT_ALLOWED_ACCOUNTS": "kf-one,kf-two",
        "WECHAT_ALLOWED_SENDERS": "sender-one,sender-two",
    }
    values.update(overrides)
    return values


def test_configure_wechat_writes_private_values_and_keeps_feature_disabled(tmp_path):
    assert SCRIPT.is_file()
    from scripts.configure_wechat import configure_files

    env = tmp_path / "server.env"
    env.write_text("AI_ENABLED=true\nOPENAI_MODEL=deepseek-chat\n", encoding="utf-8")
    marker = tmp_path / "wechat.enabled"
    marker.write_text("enabled", encoding="utf-8")

    configure_files(env, marker, _values())

    text = env.read_text(encoding="utf-8")
    assert "AI_ENABLED=true" in text
    assert "WECHAT_ENABLED=false" in text
    assert "WECHAT_ALLOWED_ACCOUNTS=kf-one,kf-two" in text
    assert "WECHAT_SECRET=secret-value" in text
    assert not marker.exists()
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(env).st_mode) == 0o600


@pytest.mark.parametrize(
    "field,value",
    [
        ("WECHAT_ALLOWED_ACCOUNTS", ""),
        ("WECHAT_ALLOWED_SENDERS", "sender-one,"),
        ("WECHAT_ENCODING_AES_KEY", "too-short"),
    ],
)
def test_configure_wechat_rejects_incomplete_security_values(tmp_path, field, value):
    assert SCRIPT.is_file()
    from scripts.configure_wechat import configure_files

    values = _values(**{field: value})
    with pytest.raises(ValueError):
        configure_files(tmp_path / "server.env", tmp_path / "wechat.enabled", values)

