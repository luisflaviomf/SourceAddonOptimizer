$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

pyinstaller --noconfirm --clean pyinstaller/worker.spec
pyinstaller --noconfirm --clean pyinstaller/gui.spec

# Bundle worker inside GUI dist folder.
$guiDist = "dist\\GModAddonOptimizer"
$workerDist = "dist\\GModAddonOptimizerWorker"

if (Test-Path "$guiDist\\worker") {
    Remove-Item "$guiDist\\worker" -Recurse -Force
}
Copy-Item $workerDist "$guiDist\\worker" -Recurse -Force

Write-Host "OK: dist\\GModAddonOptimizer ready."
