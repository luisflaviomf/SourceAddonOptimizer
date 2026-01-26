$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

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

Write-Host "OK: dist\\GModAddonOptimizer ready."
