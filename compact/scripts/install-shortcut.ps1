# Create the EmaraAI 3 icon: a shortcut that opens the EmaraAI window (the program runs while that window is open).
#   scripts\install-shortcut.ps1            -> "EmaraAI 3.lnk" in the project folder and on the Desktop
#   scripts\install-shortcut.ps1 -NoDesktop -> only in the project folder
param([switch]$NoDesktop)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "Not found: $pythonw  (run scripts\install.ps1 first)" }
$icon = Join-Path $root 'assets\emaraai.ico'
if (-not (Test-Path $icon)) { & (Join-Path $root '.venv\Scripts\python.exe') (Join-Path $root 'scripts\make_icon.py') | Out-Null }

$targets = @(Join-Path $root 'EmaraAI 3.lnk')
if (-not $NoDesktop) { $targets += Join-Path ([Environment]::GetFolderPath('Desktop')) 'EmaraAI 3.lnk' }
$shell = New-Object -ComObject WScript.Shell
foreach ($path in $targets) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = $pythonw
    $s.Arguments = '-m emaraai_hub.launcher'
    $s.WorkingDirectory = $root
    $s.IconLocation = "$icon,0"
    $s.Description = 'EmaraAI 3 (compact) - runs while its window is open'
    $s.Save()
    Write-Output "Created $path"
}
