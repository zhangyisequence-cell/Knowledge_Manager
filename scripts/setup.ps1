param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    & $Python -c "import sys; assert sys.version_info >= (3, 12), 'Python 3.12+ required'"
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required. Pass -Python with its executable path.' }
    if (-not (Test-Path '.venv/Scripts/python.exe')) {
        & $Python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
    }
    & .venv/Scripts/python.exe -m pip install -r requirements.lock
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    Write-Output 'Ready. Run: .venv/Scripts/python.exe scripts/run_local.py'
} finally { Pop-Location }
