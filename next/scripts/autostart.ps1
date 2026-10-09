# Start EmaraAI Next when you log in to Windows (a Scheduled Task, no admin rights needed).
#   powershell -ExecutionPolicy Bypass -File next\scripts\autostart.ps1          # enable
#   powershell -ExecutionPolicy Bypass -File next\scripts\autostart.ps1 -Remove  # disable
param([switch]$Remove)
$name = "EmaraAI Next"
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; Write-Host "Removed."; exit }
$exe = Join-Path $env:USERPROFILE ".emaraai-next\venv\Scripts\emaraai-next.exe"
if (-not (Test-Path $exe)) { throw "Run install.ps1 first." }
$action = New-ScheduledTaskAction -Execute $exe -Argument "serve" -WorkingDirectory (Join-Path $env:USERPROFILE ".emaraai-next")
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "EmaraAI Next will start at every logon (and restart if it stops)."
