#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
printf '%s\n' 'Ready. Run: .venv/bin/python scripts/run_local.py'
