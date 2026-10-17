# Builds dist\PurpleSector\PurpleSector.exe and dist\PurpleSector-windows.zip from source.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
$ErrorActionPreference = "Stop"
$py = if (Test-Path .venv\Scripts\python.exe) { ".venv\Scripts\python.exe" } else { "python" }
& $py -m pip install -q -r requirements.txt -r packaging\requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
& $py -m PyInstaller packaging\purplesector.spec --noconfirm --clean --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Copy-Item README.md dist\PurpleSector\README.md
if (Test-Path dist\PurpleSector-windows.zip) { Remove-Item dist\PurpleSector-windows.zip }
Compress-Archive -Path dist\PurpleSector -DestinationPath dist\PurpleSector-windows.zip
Write-Host "Built dist\PurpleSector\PurpleSector.exe and dist\PurpleSector-windows.zip"
