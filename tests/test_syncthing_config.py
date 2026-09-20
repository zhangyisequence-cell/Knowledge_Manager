from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from scripts.syncthing_config import (
    FOLDER_ID,
    IGNORE_RULES,
    render_config,
    validate_config,
    write_stignore,
)


def test_generated_config_shares_only_vault_with_staggered_versioning(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    path = tmp_path / "config.xml"
    path.write_text(render_config(vault, ["WIN-DEVICE", "ANDROID-DEVICE"]), encoding="utf-8")
    result = validate_config(path, vault, ["WIN-DEVICE", "ANDROID-DEVICE"])
    assert result["folder"] == FOLDER_ID
    root = ET.parse(path).getroot()
    folders = root.findall("folder")
    assert len(folders) == 1
    assert folders[0].attrib["path"] == str(vault.resolve())
    assert folders[0].find("versioning").attrib["type"] == "staggered"
    assert folders[0].find("versioning/param").attrib["val"] == "2592000"
    root_devices = {node.attrib["id"] for node in root.findall("device")}
    assert root_devices == {"WIN-DEVICE", "ANDROID-DEVICE"}
    options = root.find("options")
    assert options is not None
    assert options.findtext("globalAnnounceEnabled") == "false"
    assert options.findtext("localAnnounceEnabled") == "false"
    assert options.findtext("relaysEnabled") == "false"
    assert options.findtext("natEnabled") == "false"


def test_generated_config_can_pin_devices_to_tailscale_addresses(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    path = tmp_path / "config.xml"
    path.write_text(
        render_config(
            vault,
            ["WIN-DEVICE"],
            {"WIN-DEVICE": "tcp://100.64.186.105:22000"},
        ),
        encoding="utf-8",
    )
    root = ET.parse(path).getroot()
    device = root.find("device")
    assert device is not None
    assert device.findtext("address") == "tcp://100.64.186.105:22000"


def test_generated_config_rejects_address_for_unknown_device(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ValueError, match="设备地址"):
        render_config(vault, ["WIN-DEVICE"], {"OTHER": "tcp://100.64.186.105:22000"})


def test_ignore_rules_keep_notes_attachments_and_settings(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ignore = write_stignore(vault)
    content = ignore.read_text(encoding="utf-8")
    assert ".obsidian/workspace" in content
    assert ".trash" in content
    assert ".stfolder" in content
    assert "*.md" not in content
    assert "attachments" not in content
    assert IGNORE_RULES == content


def test_config_rejects_database_or_backup_as_shared_path(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "config.xml"
    config.write_text(render_config(tmp_path / "data", ["WIN-DEVICE"]), encoding="utf-8")
    with pytest.raises(ValueError, match="Vault"):
        validate_config(config, vault, ["WIN-DEVICE"])


def test_config_rejects_public_discovery_and_missing_device(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "config.xml"
    config.write_text(render_config(vault, ["WIN-DEVICE"]), encoding="utf-8")
    root = ET.parse(config)
    root.getroot().find("options").find("relaysEnabled").text = "true"
    root.write(config, encoding="unicode")
    with pytest.raises(ValueError, match="公共"):
        validate_config(config, vault, ["ANDROID-DEVICE"])
