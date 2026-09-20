"""Run each suite in the working directory required by its repository."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor" / "knowledge-inbox"


def main():
    environment = os.environ.copy()
    environment["HF_HUB_OFFLINE"] = "1"
    environment["HF_HOME"] = str(ROOT / "runtime" / "test-model-cache")
    environment["PYTHONIOENCODING"] = "utf-8"
    commands = [
        (UPSTREAM, ["-m", "pytest", "tests", "-q"]),
        (ROOT, ["-m", "pytest", "tests", "-q"]),
        (ROOT, ["-m", "ruff", "check", "tests", "scripts"]),
        (UPSTREAM, ["-m", "ruff", "check", "backend", "scripts", "tests"]),
    ]
    for directory, args in commands:
        result = subprocess.run(
            [sys.executable, *args], cwd=directory, env=environment, check=False, timeout=180
        )
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
