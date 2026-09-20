"""Build a public-key-only Ubuntu autoinstall ISO for a verified empty VM disk.

Operator prerequisite: inspect disk partition state first. Autoinstall formats the
selected disk. This script only creates media; it does not boot a VM or install.
Requires PyYAML and pycdlib (provisioning-only dependency).
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

import pycdlib
import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--mac-address", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    key = args.public_key.read_text("ascii").strip()
    if not key.startswith("ssh-ed25519 "):
        parser.error("Expected an Ed25519 public key")
    if args.output.exists():
        parser.error("Output already exists; choose a new path")
    config = {
        "autoinstall": {
            "version": 1,
            "refresh-installer": {"update": False},
            "locale": "en_US.UTF-8",
            "keyboard": {"layout": "us"},
            "identity": {
                "hostname": "knowledge-server", "username": "knowledgeadmin", "password": "!"
            },
            "ssh": {"install-server": True, "allow-pw": False, "authorized-keys": [key]},
            "network": {
                "version": 2,
                "ethernets": {
                    "management": {
                        "match": {"macaddress": args.mac_address},
                        "dhcp4": True, "dhcp6": False,
                    }
                },
            },
            "storage": {"layout": {"name": "direct"}},
            "updates": "security",
            "late-commands": [
                ("curtin in-target --target=/target -- sh -c "
                "\"printf 'knowledgeadmin ALL=(ALL) NOPASSWD:ALL\\n' "
                "> /etc/sudoers.d/90-knowledge-manager; "
                "chmod 440 /etc/sudoers.d/90-knowledge-manager; "
                "visudo -cf /etc/sudoers.d/90-knowledge-manager\"")
            ],
            "shutdown": "poweroff",
        }
    }
    user_data = ("#cloud-config\n" + yaml.safe_dump(config, sort_keys=False)).encode()
    metadata = b"instance-id: knowledge-manager-ubuntu-20260918\nlocal-hostname: knowledge-server\n"
    image = pycdlib.PyCdlib()
    image.new(vol_ident="cidata", joliet=3, rock_ridge="1.09")
    try:
        for name, iso_name, content in (
            ("user-data", "USER_DAT", user_data), ("meta-data", "META_DAT", metadata)
        ):
            image.add_fp(io.BytesIO(content), len(content), iso_path=f"/{iso_name}.;1",
                         rr_name=name, joliet_path=f"/{name}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        image.write(str(args.output))
    finally:
        image.close()
    print(f"Created public-key-only installation media: {args.output}")


if __name__ == "__main__":
    main()
