$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python environment not found. Create .venv and install requirements.txt first.'
}
Write-Host 'Process Lab: http://127.0.0.1:8000 (Ctrl+C to stop)'
& $python -m processlab.main
