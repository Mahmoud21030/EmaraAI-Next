# Start the hub (foreground). Logs: data\logs\hub.jsonl
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
.\.venv\Scripts\python -m emaraai_hub serve --config config\hub.yaml
