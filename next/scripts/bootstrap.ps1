# Everything in one go: installs what is missing (Python, Git, Tailscale) with winget, installs EmaraAI Next,
# asks the setup questions (Enter = default), starts it and opens it in the browser. Safe to run again.
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Need($cmd, $id, $label) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "$label is missing and winget is not available. Install $label, then run Setup again." }
    Write-Host "Installing $label ..." -ForegroundColor Cyan
    winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
}

Write-Host "`n== EmaraAI Next ==`n" -ForegroundColor Green
Need "git" "Git.Git" "Git"
if (-not ((Get-Command py -ErrorAction SilentlyContinue) -or (Get-Command python -ErrorAction SilentlyContinue))) { Need "py" "Python.Python.3.13" "Python 3.13" }
$ts = (Get-Command tailscale -ErrorAction SilentlyContinue) -or (Test-Path "$env:ProgramFiles\Tailscale\tailscale.exe")
if (-not $ts) {
    $a = Read-Host "Install Tailscale so you can reach this PC from your phone/laptop? (y/n) [y]"
    if ($a -eq "" -or $a -like "y*") {
        Need "tailscale" "Tailscale.Tailscale" "Tailscale"
        Write-Host "Sign in to Tailscale in the window that opened, then press Enter here." -ForegroundColor Yellow
        Start-Process "$env:ProgramFiles\Tailscale\tailscale-ipn.exe" -ErrorAction SilentlyContinue
        Read-Host | Out-Null
    }
}

& "$here\install.ps1"
$exe = Join-Path $env:USERPROFILE ".emaraai-next\venv\Scripts\emaraai-next.exe"
& $exe setup

# start now (hidden window) unless it already runs, then open the page
try { Invoke-RestMethod http://127.0.0.1:8810/v1/health -TimeoutSec 2 | Out-Null } catch {
    Start-Process $exe -ArgumentList "serve" -WindowStyle Hidden -WorkingDirectory (Join-Path $env:USERPROFILE ".emaraai-next")
    Start-Sleep 3
}
Start-Process "http://127.0.0.1:8810"
Write-Host "`nEmaraAI Next is running." -ForegroundColor Green
