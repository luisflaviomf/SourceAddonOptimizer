$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

$workerDist = Join-Path $PWD "dist\\GModAddonOptimizerWorker"
$workerExe = Join-Path $workerDist "GModAddonOptimizerWorker.exe"
$internalDir = Join-Path $workerDist "_internal"
$crowbarExe = Join-Path $PWD "CrowbarCommandLineDecomp.exe"

$resourcesDir = Join-Path $PWD "GmodAddonCompressor-master\\GmodAddonCompressor\\Resources"
$zipPath = Join-Path $resourcesDir "SourceAddonOptimizer.win-x64.zip"

if (!(Test-Path $workerExe)) {
    Write-Host "Worker not built yet, running PyInstaller..."
    pyinstaller --noconfirm --clean pyinstaller/worker.spec
}

if (!(Test-Path $workerExe)) { throw "Worker exe not found: $workerExe" }
if (!(Test-Path $internalDir)) { throw "Worker _internal folder not found: $internalDir" }
if (!(Test-Path $crowbarExe)) { throw "Crowbar exe not found: $crowbarExe" }

New-Item -ItemType Directory -Force -Path $resourcesDir | Out-Null

$stagingRoot = Join-Path $PWD "build\\wpf-tools\\SourceAddonOptimizer"
if (Test-Path $stagingRoot) { Remove-Item $stagingRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

Copy-Item $workerExe (Join-Path $stagingRoot "SourceAddonOptimizerWorker.exe") -Force
Copy-Item $crowbarExe (Join-Path $stagingRoot "CrowbarCommandLineDecomp.exe") -Force
Copy-Item $internalDir (Join-Path $stagingRoot "_internal") -Recurse -Force

if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

$maxAttempts = 5
for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    try {
        Compress-Archive -Path (Join-Path $stagingRoot "*") -DestinationPath $zipPath -Force
        break
    }
    catch {
        if ($attempt -eq $maxAttempts) { throw }
        Start-Sleep -Milliseconds (400 * $attempt)
    }
}

Write-Host "OK: $zipPath"
