$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

python --version
if ($LASTEXITCODE -ne 0) { throw "Python was not found." }

python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit $LASTEXITCODE)." }

python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "requirements install failed (exit $LASTEXITCODE)." }

python -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller install failed (exit $LASTEXITCODE)." }

pyinstaller --noconfirm --clean pyinstaller/worker.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller worker build failed (exit $LASTEXITCODE)." }
pyinstaller --noconfirm --clean pyinstaller/gui.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller GUI build failed (exit $LASTEXITCODE)." }

# Bundle worker inside GUI dist folder.
$guiDist = "dist\\GModAddonOptimizer"
$workerDist = "dist\\GModAddonOptimizerWorker"

if (Test-Path "$guiDist\\worker") {
    Remove-Item "$guiDist\\worker" -Recurse -Force
}
Copy-Item $workerDist "$guiDist\\worker" -Recurse -Force

if (-not (Test-Path "$guiDist\\GModAddonOptimizer.exe")) {
    throw "GUI executable not found in $guiDist."
}
if (-not (Test-Path "$guiDist\\worker\\GModAddonOptimizerWorker.exe")) {
    throw "Worker executable not found in $guiDist\\worker."
}

Write-Host "OK: dist\\GModAddonOptimizer ready."
