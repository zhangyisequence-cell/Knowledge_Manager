from __future__ import annotations

import pytest
import yaml

from scripts.check_public_entry import validate_config


def test_public_entry_has_only_callback_route():
    result = validate_config(__import__("pathlib").Path("tests/fixtures/public-entry.yaml"))
    assert result == {
        "hostname": "inbox.example.test",
        "path": "/wechat/callback",
        "service": "http://127.0.0.1:8766",
    }


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda data: data["ingress"].append({"hostname": "x", "service": "http://127.0.0.1:8787"}), "exactly one"),
        (lambda data: data["ingress"].__setitem__(1, {"service": "http://127.0.0.1:8787"}), "404"),
        (lambda data: data["ingress"][0].__setitem__("path", "/api/health"), "Only"),
        (lambda data: data["ingress"][0].__setitem__("hostname", "*.example.test"), "concrete"),
    ],
)
def test_public_entry_rejects_extra_or_management_route(tmp_path, change, match):
    with open("tests/fixtures/public-entry.yaml", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    change(data)
    config = tmp_path / "entry.yaml"
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        validate_config(config)
