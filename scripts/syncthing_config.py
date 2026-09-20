"""Pure Syncthing configuration and ignore-rule helpers."""
from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

FOLDER_ID = "knowledge-vault"
VERSIONING_MAX_AGE = "2592000"
IGNORE_RULES = """(?d)/.obsidian/workspace.json
(?d)/.obsidian/workspace-mobile.json
(?d)/.obsidian/cache
(?d)/.trash
(?d)/.stfolder
"""


def render_config(
    vault: Path,
    device_ids: Iterable[str],
    device_addresses: dict[str, str] | None = None,
) -> str:
    vault = vault.resolve()
    devices = list(dict.fromkeys(
        value.strip() for value in device_ids if value and value.strip()
    ))
    if not devices:
        raise ValueError("至少需要一个已提供的 Syncthing 设备 ID")
    addresses = device_addresses or {}
    if any(device_id not in devices for device_id in addresses):
        raise ValueError("设备地址只能配置在已提供的设备 ID 上")
    root = ET.Element("configuration", {"version": "37"})
    # Syncthing requires every remote device to be declared at the root and
    # referenced again by each shared folder. The folder-only form is accepted
    # by XML parsers but never establishes a device connection.
    for device_id in devices:
        device = ET.SubElement(root, "device", {
            "id": device_id,
            "introducedBy": "",
        })
        if address := addresses.get(device_id):
            ET.SubElement(device, "address").text = address
    ET.SubElement(root, "folder", {
        "id": FOLDER_ID,
        "label": "Knowledge Vault",
        "path": str(vault),
        "type": "sendreceive",
        "rescanIntervalS": "60",
        "fsWatcherEnabled": "true",
        "versioning": "staggered",
    })
    folder = root.find("folder")
    versioning = ET.SubElement(folder, "versioning", {"type": "staggered"})
    ET.SubElement(versioning, "param", {"key": "maxAge", "value": VERSIONING_MAX_AGE})
    for device_id in devices:
        ET.SubElement(folder, "device", {"id": device_id, "introducedBy": ""})
    ET.SubElement(root, "options", {
        "globalAnnounceEnabled": "false",
        "relaysEnabled": "false",
        "natEnabled": "false",
    })
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"


def write_atomic(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(output.fileno(), mode)
            else:  # Windows test runner
                os.chmod(temporary, mode)
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_stignore(vault: Path) -> Path:
    target = vault.resolve() / ".stignore"
    write_atomic(target, IGNORE_RULES, 0o640)
    return target


def validate_config(path: Path, vault: Path, device_ids: Iterable[str] = ()) -> dict:
    root = ET.parse(path).getroot()
    folders = root.findall("folder")
    if len(folders) != 1:
        raise ValueError("Syncthing 配置必须只共享一个 Vault 文件夹")
    folder = folders[0]
    root_devices = {
        node.attrib.get("id")
        for node in root.findall("device")
        if node.attrib.get("id")
    }
    if folder.attrib.get("id") != FOLDER_ID:
        raise ValueError("Syncthing 共享文件夹 ID 不正确")
    if Path(folder.attrib.get("path", "")).resolve() != vault.resolve():
        raise ValueError("Syncthing 共享路径不是指定 Vault")
    if folder.attrib.get("type") != "sendreceive":
        raise ValueError("Syncthing Vault 必须是 Send & Receive")
    versioning = folder.find("versioning")
    if versioning is None or versioning.attrib.get("type") != "staggered":
        raise ValueError("Syncthing 必须启用 staggered 版本保留")
    param = versioning.find("param[@key='maxAge']")
    if param is None or param.attrib.get("value") != VERSIONING_MAX_AGE:
        raise ValueError("Syncthing 版本保留必须为 30 天")
    options = root.find("options")
    if options is None or any(options.attrib.get(key) != "false" for key in (
        "globalAnnounceEnabled", "relaysEnabled", "natEnabled"
    )):
        raise ValueError("Syncthing 公共发现、Relay 和 NAT 必须关闭")
    configured = {node.attrib.get("id") for node in folder.findall("device")}
    expected = {value.strip() for value in device_ids if value and value.strip()}
    if expected and (not expected.issubset(configured) or not expected.issubset(root_devices)):
        raise ValueError("Syncthing 配置缺少指定客户端设备")
    return {"folder": FOLDER_ID, "path": str(vault.resolve()), "devices": len(configured)}
