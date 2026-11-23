param(
    [ValidateSet('agency', 'copilot')][string]$Backend = 'copilot',
    [string]$Workspace = (Join-Path ([IO.Path]::GetTempPath()) ('verify-and-repair-' + [guid]::NewGuid().ToString('N')))
)
$ErrorActionPreference = 'Stop'
$Project = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path $Project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run setup.ps1 in the implementation directory first.' }
if (Test-Path -LiteralPath $Workspace) { throw "Refusing to overwrite an existing workspace: $Workspace" }
$Workspace = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Workspace)
Copy-Item -LiteralPath $PSScriptRoot -Destination $Workspace -Recurse
& git -C $Workspace init --quiet
if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the disposable repository.' }
$Output = "$Workspace-result.json"
Write-Output "Disposable workspace: $Workspace"
Write-Output 'Before repair:'
Get-Content -LiteralPath (Join-Path $Workspace 'src\logic.py')
& $Python -m verify_and_repair.cli solve `
    --config (Join-Path $Workspace 'verify-and-repair.json') `
    --backend $Backend --task 'Fix inc: for every integer x, return x + 1.' --output $Output
if ($LASTEXITCODE -ne 0) { throw "Lean repair failed; inspect $Output" }
Write-Output 'After repair:'
Get-Content -LiteralPath (Join-Path $Workspace 'src\logic.py')
Push-Location $Workspace
try {
    & $Python -B run_public.py
    if ($LASTEXITCODE -ne 0) { throw 'Independent public re-check failed.' }
} finally { Pop-Location }
Write-Output "Full evidence: $Output"
