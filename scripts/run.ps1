<#
  Launch PurpleSector. Works from any directory; does not require an activated venv.
    powershell -ExecutionPolicy Bypass -File scripts\run.ps1            # run (creates .venv on first use)
    powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Port 8502
#>
param([int]$Port = 8501, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Creating virtual environment in .venv ..."
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv .venv }
    else { Write-Error "Python 3.11+ was not found. Install it from https://www.python.org/downloads/ and re-run." }
    if ($LASTEXITCODE -ne 0) { Write-Error "Could not create the virtual environment." }
}

& $py -c "import streamlit, plotly, pandas, numpy, comtypes" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies from requirements.txt ..."
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Write-Error "Dependency installation failed (see the pip output above)." }
}

$browser = if ($NoBrowser) { "true" } else { "false" }
Write-Host "Starting PurpleSector on http://localhost:$Port  (Ctrl+C to stop)"
& $py -m streamlit run app.py --server.port $Port --server.headless $browser
exit $LASTEXITCODE
