$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-WorkerSourceFiles {
    $sourcePaths = @(
        (Join-Path $PWD "worker\\worker_main.py"),
        (Join-Path $PWD "build_optimized_addon.py"),
        (Join-Path $PWD "batch_compile_opt_qc.py"),
        (Join-Path $PWD "batch_build_map_bsp.py"),
        (Join-Path $PWD "batch_decompile_organize.py"),
        (Join-Path $PWD "batch_merge_addons.py"),
        (Join-Path $PWD "batch_scan_map_bsp.py"),
        (Join-Path $PWD "batch_optimize_parallel.py"),
        (Join-Path $PWD "batch_optimize_qc.py"),
        (Join-Path $PWD "batch_optimize_selective_policy.py"),
        (Join-Path $PWD "batch_optimize_round_parts_policy.py"),
        (Join-Path $PWD "batch_unpack_addons.py"),
        (Join-Path $PWD "render_previews.py"),
        (Join-Path $PWD "selective_policy_models.py"),
        (Join-Path $PWD "vehicle_steer_turn_basis_fix.py"),
        (Join-Path $PWD "pyinstaller\\worker.spec")
    )

    foreach ($path in $sourcePaths) {
        if (Test-Path $path) {
            Get-Item $path
        }
    }
}

function Test-WorkerRebuildNeeded {
    param(
        [Parameter(Mandatory = $true)]
        [string] $WorkerExe
    )

    if (!(Test-Path $WorkerExe)) {
        return $true
    }

    $workerTimestamp = (Get-Item $WorkerExe).LastWriteTimeUtc
    foreach ($source in Get-WorkerSourceFiles) {
        if ($source.LastWriteTimeUtc -gt $workerTimestamp) {
            return $true
        }
    }

    return $false
}

function Test-ZipHasEntry {
    param(
        [Parameter(Mandatory = $true)]
        [string] $ZipFile,

        [Parameter(Mandatory = $true)]
        [string] $EntryPath
    )

    $normalizedEntryPath = $EntryPath.Replace('\', '/')
    $archive = [System.IO.Compression.ZipFile]::OpenRead($ZipFile)
    try {
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName.Replace('\', '/') -eq $normalizedEntryPath) {
                return $true
            }
        }

        return $false
    }
    finally {
        $archive.Dispose()
    }
}

function Assert-PackageZip {
    param(
        [Parameter(Mandatory = $true)]
        [string] $ZipFile
    )

    if (!(Test-Path $ZipFile)) {
        throw "Package zip was not created: $ZipFile"
    }

    $zipInfo = Get-Item $ZipFile
    if ($zipInfo.Length -le 0) {
        throw "Package zip is empty: $ZipFile"
    }

    $requiredEntries = @(
        "SourceAddonOptimizerWorker.exe",
        "CrowbarCommandLineDecomp.exe",
        "_internal/base_library.zip",
        "_internal/python311.dll"
    )

    foreach ($entry in $requiredEntries) {
        if (!(Test-ZipHasEntry -ZipFile $ZipFile -EntryPath $entry)) {
            throw "Package zip is missing required entry '$entry': $ZipFile"
        }
    }
}

function New-PackageZip {
    param(
        [Parameter(Mandatory = $true)]
        [string] $SourceDirectory,

        [Parameter(Mandatory = $true)]
        [string] $DestinationZip
    )

    $destinationDir = Split-Path -Parent $DestinationZip
    $tempZip = Join-Path $destinationDir ([System.IO.Path]::GetFileNameWithoutExtension($DestinationZip) + ".tmp.zip")

    if (Test-Path $tempZip) {
        Remove-Item $tempZip -Force
    }

    if (Test-Path $DestinationZip) {
        Remove-Item $DestinationZip -Force
    }

    $maxAttempts = 5
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            [System.IO.Compression.ZipFile]::CreateFromDirectory(
                $SourceDirectory,
                $tempZip,
                [System.IO.Compression.CompressionLevel]::Optimal,
                $false
            )

            Assert-PackageZip -ZipFile $tempZip
            Move-Item $tempZip $DestinationZip -Force
            Assert-PackageZip -ZipFile $DestinationZip
            return
        }
        catch {
            if (Test-Path $tempZip) {
                Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
            }

            if ($attempt -eq $maxAttempts) {
                throw "Failed to build package zip after $maxAttempts attempts. $($_.Exception.Message)"
            }

            Start-Sleep -Milliseconds (400 * $attempt)
        }
    }
}

$workerDist = Join-Path $PWD "dist\\GModAddonOptimizerWorker"
$workerExe = Join-Path $workerDist "GModAddonOptimizerWorker.exe"
$internalDir = Join-Path $workerDist "_internal"
$crowbarExe = Join-Path $PWD "CrowbarCommandLineDecomp.exe"

$resourcesDir = Join-Path $PWD "GmodAddonCompressor-master\\GmodAddonCompressor\\Resources"
$zipPath = Join-Path $resourcesDir "SourceAddonOptimizer.win-x64.zip"

if (Test-WorkerRebuildNeeded -WorkerExe $workerExe) {
    Write-Host "Worker build is missing or stale, running PyInstaller..."
    pyinstaller --noconfirm --clean pyinstaller/worker.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller worker build failed (exit $LASTEXITCODE)."
    }
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

New-PackageZip -SourceDirectory $stagingRoot -DestinationZip $zipPath

Write-Host "OK: $zipPath"
