import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_centos_installer_is_posix_shell_and_dnf_only():
    script = ROOT / "scripts" / "install_centos.sh"
    result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    text = script.read_text(encoding="utf-8")
    assert "centos|rhel|rocky|almalinux" in text
    assert "python3.12" in text
    assert "ffmpeg-free" in text
    assert "antiword" in text
    assert "tesseract-langpack-chi_sim" in text
    assert "apt-get" not in text
    assert "/opt/knowledge-manager/releases/" in text
    assert "/srv/knowledge-manager" in text


def test_centos_syncthing_installer_is_posix_shell_and_dnf_only():
    script = ROOT / "scripts" / "install_syncthing_centos.sh"
    result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    text = script.read_text(encoding="utf-8")
    assert "tailscale ip -4" in text
    assert "syncthing" in text
    assert "epel-release" in text
    assert "apt-get" not in text
    assert "--windows-device-id" in text
    assert "--android-device-id" in text


def test_centos_runbook_is_the_primary_linux_deployment_path():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "centos-deployment.md").read_text(encoding="utf-8")
    assert "CentOS 服务器" in readme
    assert "docs/centos-deployment.md" in readme
    assert "install_centos.sh" in runbook
    assert "install_syncthing_centos.sh" in runbook
