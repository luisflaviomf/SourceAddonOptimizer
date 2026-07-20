[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$packageScript = Join-Path $repoRoot 'pyinstaller\package_wpf_tools.ps1'
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("maximum-package-test-{0}" -f [guid]::NewGuid().ToString('N'))

function Assert-True {
    param([bool] $Condition, [string] $Message)
    if (!$Condition) { throw $Message }
}

try {
    $env:SOURCE_ADDON_OPTIMIZER_PACKAGE_TEST_ONLY = '1'
    . $packageScript
    Remove-Item Env:SOURCE_ADDON_OPTIMIZER_PACKAGE_TEST_ONLY -ErrorAction SilentlyContinue

    $parallelSources = @(
        'maximum_optimizer\parallelism.py',
        'maximum_optimizer\family_scheduler.py',
        'maximum_optimizer\progress_journal.py',
        'maximum_optimizer\reference_bundle.py'
    )
    $workerSources = @(Get-WorkerSourceFiles | ForEach-Object { $_.FullName })
    foreach ($relative in $parallelSources) {
        $expected = (Join-Path $repoRoot $relative)
        Assert-True ($workerSources -contains $expected) `
            "Worker freshness must include $relative"
    }

    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    $fakeWorker = Join-Path $tempRoot 'SourceAddonOptimizerWorker.exe'
    Set-Content -LiteralPath $fakeWorker -Value 'old worker'
    (Get-Item -LiteralPath $fakeWorker).LastWriteTimeUtc = [datetime]'2000-01-01T00:00:00Z'
    Assert-True (Test-WorkerRebuildNeeded -WorkerExe $fakeWorker) `
        'Maximum optimizer sources must make an old worker stale.'

    (Get-Item -LiteralPath $fakeWorker).LastWriteTimeUtc = [datetime]'2099-01-01T00:00:00Z'
    Assert-True (Test-WorkerRebuildNeeded -WorkerExe $fakeWorker) `
        'A new-looking worker with a missing or corrupt runtime must still be stale.'

    $staging = Join-Path $tempRoot 'staging'
    $requiredFiles = @(
        'SourceAddonOptimizerWorker.exe',
        'CrowbarCommandLineDecomp.exe',
        '_internal\base_library.zip',
        '_internal\python311.dll',
        '_internal\maximum_optimizer\native\bin\win-x64\meshopt_bridge.dll',
        '_internal\maximum_optimizer\profiles\maximum-experimental-v1.json',
        '_internal\third_party\meshoptimizer\LICENSE.md'
    )
    foreach ($relative in $requiredFiles) {
        $path = Join-Path $staging $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
        Set-Content -LiteralPath $path -Value $relative
    }

    $completeZip = Join-Path $tempRoot 'complete.zip'
    [System.IO.Compression.ZipFile]::CreateFromDirectory($staging, $completeZip)
    Assert-PackageZip -ZipFile $completeZip

    Remove-Item -LiteralPath (Join-Path $staging '_internal\third_party\meshoptimizer\LICENSE.md')
    $missingLicenseZip = Join-Path $tempRoot 'missing-license.zip'
    [System.IO.Compression.ZipFile]::CreateFromDirectory($staging, $missingLicenseZip)
    $rejected = $false
    try {
        Assert-PackageZip -ZipFile $missingLicenseZip
    }
    catch {
        $rejected = $_.Exception.Message -match 'LICENSE\.md'
    }
    Assert-True $rejected 'Package validation must reject a ZIP without the meshoptimizer license.'

    Write-Host 'PASS: Maximum worker freshness and required ZIP entries are enforced.'
}
finally {
    Remove-Item Env:SOURCE_ADDON_OPTIMIZER_PACKAGE_TEST_ONLY -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
