# EmaraAI Next - install on Windows (PowerShell 5.1 or 7). Run from the repository root:
#   powershell -ExecutionPolicy Bypass -File next\scripts\install.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git for Windows is required: https://git-scm.com/download/win" }
& $py -3 -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ is required'"
$venv = Join-Path $env:USERPROFILE ".emaraai-next\venv"
if (-not (Test-Path $venv)) { & $py -3 -m venv $venv }
& "$venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& "$venv\Scripts\python.exe" -m pip install "$root"
git config --global core.longpaths true
$cfg = Join-Path $env:USERPROFILE ".emaraai-next\emaraai.toml"
if (-not (Test-Path $cfg)) { Copy-Item (Join-Path $root "emaraai.example.toml") $cfg; Write-Host "Edit your settings: $cfg" }
Write-Host "Installed. Start it with:  $venv\Scripts\emaraai-next.exe serve"
Write-Host "Start with Windows (optional): next\scripts\autostart.ps1"
