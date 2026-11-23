$ErrorActionPreference = "Stop"
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "Run .\setup.ps1 first." }
& $Python -m verify_and_repair.cli @args
exit $LASTEXITCODE
