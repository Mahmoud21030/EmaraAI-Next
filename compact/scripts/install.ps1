# One-time install (Windows). Run from the EmaraAI-Hub folder.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .venv)) {
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
.\.venv\Scripts\python -m pip install --upgrade 'pip>=26.2' 'setuptools>=83'
if ($LASTEXITCODE -ne 0) { throw 'Could not update the package installer.' }
.\.venv\Scripts\python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw 'Could not install project dependencies.' }
if (-not (Test-Path config\hub.yaml)) { Copy-Item config\hub.example.yaml config\hub.yaml }
$auditTemp = Join-Path '.tmp' ('install-tests-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python -m pytest -q --basetemp=$auditTemp
if ($LASTEXITCODE -ne 0) { throw 'Project tests failed. Installation is incomplete.' }
.\.venv\Scripts\python -m emaraai_hub doctor
if ($LASTEXITCODE -ne 0) { throw 'The environment check found problems. Follow its repair instructions.' }
