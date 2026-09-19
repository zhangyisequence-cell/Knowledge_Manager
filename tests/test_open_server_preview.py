import os
import shutil
import subprocess
from pathlib import Path

import pytest

if os.name != "nt":
    pytest.skip("Windows PowerShell integration tests", allow_module_level=True)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "open_server_preview.ps1"
POWERSHELL = shutil.which("powershell.exe")
if not POWERSHELL:
    pytest.skip("Windows PowerShell 5.1 is unavailable", allow_module_level=True)


def test_preview_requires_explicit_open_and_does_not_connect_by_default():
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "-Open" in result.stdout
    assert "127.0.0.1:18787" in result.stdout


def test_preview_uses_strict_two_hop_foreground_tunnel_contract():
    source = SCRIPT.read_text(encoding="utf-8")

    for required in (
        "BatchMode=yes",
        "StrictHostKeyChecking=yes",
        "ExitOnForwardFailure=yes",
        "IdentitiesOnly=yes",
        "UserKnownHostsFile",
        "[string]$HostKeyAlias = '100.64.186.105'",
        "[string]$GuestHostKeyAlias = '172.27.70.12'",
        "-W",
        "-N",
        "127.0.0.1:{0}:127.0.0.1:8787",
        "00155D031703",
        "Get-NetNeighbor",
        "$addresses.Count -ne 1",
    ):
        assert required in source
    for forbidden in ("portproxy", "New-NetFirewallRule", "Start-Process"):
        assert forbidden not in source


def test_preview_script_parses_in_windows_powershell_51():
    command = (
        f"$tokens=$null; $errors=$null; "
        f"[void][Management.Automation.Language.Parser]::ParseFile('{SCRIPT}',"
        "[ref]$tokens,[ref]$errors); if($errors.Count){$errors | % Message; exit 1}"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_guest_discovery_requires_one_ipv4():
    success = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-Command",
            f". '{SCRIPT}'; ConvertFrom-GuestDiscovery '[\"172.27.65.219\"]'",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    ambiguous = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-Command",
            (
                f". '{SCRIPT}'; ConvertFrom-GuestDiscovery "
                "'[\"172.27.65.219\",\"172.27.65.220\"]'"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert success.returncode == 0, success.stderr
    assert "172.27.65.219" in success.stdout
    assert ambiguous.returncode != 0
    assert "exactly one guest IPv4" in ambiguous.stderr


def test_proxy_command_rejects_windows_shell_metacharacters():
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-Command",
            f". '{SCRIPT}'; Assert-SafeProxyToken 'C:\\Tools&Apps\\ssh.exe' 'SSH path'",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "unsafe for the nested SSH proxy command" in result.stderr


def test_ssh_resolution_returns_one_deterministic_application():
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-Command",
            (
                f". '{SCRIPT}'; $resolved = Resolve-SshApplication 'ssh.exe'; "
                "if ($resolved -is [array]) { throw 'array result' }; "
                "if (!(Test-Path -LiteralPath $resolved -PathType Leaf)) { throw 'missing' }; "
                "$resolved"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().lower().endswith("ssh.exe")


def test_known_hosts_path_with_spaces_survives_openssh_option_parsing(tmp_path):
    known_hosts = tmp_path / "trusted host keys" / "known_hosts"
    known_hosts.parent.mkdir()
    known_hosts.write_text("fixture\n", encoding="utf-8")
    command = (
        f". '{SCRIPT}'; "
        f"$option = Format-SshPathOption 'UserKnownHostsFile' '{known_hosts}'; "
        "$ssh = Resolve-SshApplication 'ssh.exe'; "
        "& $ssh -G -o $option example.invalid"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    parsed = [
        line.split(maxsplit=1)[1]
        for line in result.stdout.splitlines()
        if line.lower().startswith("userknownhostsfile ")
    ]
    assert parsed == [str(known_hosts).replace("\\", "/")]


def test_preview_uses_one_deterministic_user_local_public_key_cache():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "Join-Path $env:LOCALAPPDATA 'KnowledgeManagerPreview'" in source
    assert "Join-Path $cacheDirectory 'known_hosts'" in source
    assert "[IO.File]::Copy($knownHostsSource, $knownHosts, $true)" in source
    assert "knowledge-manager-known-hosts-" not in source
