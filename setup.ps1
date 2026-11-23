param([string]$Python = "python", [switch]$Dev)
$ErrorActionPreference = "Stop"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Install Git first." }
& $Python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ is required'"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11+ is required." }
$EnvironmentPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $EnvironmentPython)) {
    & $Python -m venv (Join-Path $PSScriptRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }
}
$Package = if ($Dev) { "${PSScriptRoot}[dev]" } else { $PSScriptRoot }
& $EnvironmentPython -m pip install --quiet -e $Package
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $EnvironmentPython -m verify_and_repair.cli --help
if ($LASTEXITCODE -ne 0) { throw "CLI startup failed." }
Write-Output "Ready. Use .\verify-and-repair.ps1 configure --backend agency or --backend copilot, then .\verify-and-repair.ps1 solve. Install and authenticate the chosen CLI separately."
