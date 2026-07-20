$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSCommandPath

$packageScript = Join-Path $repoRoot "pyinstaller\package_wpf_tools.ps1"
$csprojPath = Join-Path $repoRoot "GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj"
$testProjectPath = Join-Path $repoRoot "GmodAddonCompressor-master\GmodAddonCompressor.Tests\GmodAddonCompressor.Tests.csproj"
$bridgeBuildScript = Join-Path $repoRoot "maximum_optimizer\native\build.ps1"
$runtimeIdentifier = "win-x64"
$configuration = "Release"
$publishProfile = "win-x64-singlefile"

$projectDir = Split-Path -Parent $csprojPath
$runtimeBuildDir = Join-Path $projectDir "bin\$configuration\net6.0-windows\$runtimeIdentifier"
$runtimeObjDir = Join-Path $projectDir "obj\$configuration\net6.0-windows\$runtimeIdentifier"
$finalExePath = Join-Path $runtimeBuildDir "publish\GmodAddonOptimizer.exe"
$packagedZipPath = Join-Path $projectDir "Resources\SourceAddonOptimizer.win-x64.zip"

function Invoke-Step {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [scriptblock] $Action
    )

    Write-Host ""
    Write-Host "==> $Name"
    & $Action
}

function Invoke-Dotnet {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments
    )

    & dotnet @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "dotnet failed with exit code $LASTEXITCODE. Arguments: $($Arguments -join ' ')"
    }
}

if (!(Test-Path $packageScript)) {
    throw "Package script not found: $packageScript"
}

if (!(Test-Path $csprojPath)) {
    throw "WPF project not found: $csprojPath"
}

Invoke-Step -Name "Build meshoptimizer bridge" -Action {
    & $bridgeBuildScript
    if ($LASTEXITCODE -ne 0) {
        throw "meshoptimizer bridge build failed with exit code $LASTEXITCODE."
    }
}

Invoke-Step -Name "Run Maximum optimizer Python tests" -Action {
    & python -m unittest discover -s tests/maximum_optimizer -t . -p "test_*.py" -q
    if ($LASTEXITCODE -ne 0) {
        throw "Maximum optimizer Python tests failed with exit code $LASTEXITCODE."
    }
}

Invoke-Step -Name "Run WPF tests" -Action {
    Invoke-Dotnet @(
        "test",
        $testProjectPath,
        "-c", $configuration
    )
}

Invoke-Step -Name "Clean runtime-specific build output" -Action {
    Invoke-Dotnet @(
        "clean",
        $csprojPath,
        "-c", $configuration
    )

    foreach ($path in @($runtimeBuildDir, $runtimeObjDir)) {
        if (Test-Path $path) {
            Remove-Item $path -Recurse -Force
        }
    }
}

Invoke-Step -Name "Package embedded WPF tools ZIP" -Action {
    & $packageScript
    if ($LASTEXITCODE -ne 0) {
        throw "package_wpf_tools.ps1 failed with exit code $LASTEXITCODE."
    }

    if (!(Test-Path $packagedZipPath)) {
        throw "Expected packaged tools ZIP was not generated: $packagedZipPath"
    }
}

Invoke-Step -Name "Publish WPF single-file executable" -Action {
    Invoke-Dotnet @(
        "publish",
        $csprojPath,
        "-c", $configuration,
        "-r", $runtimeIdentifier,
        "-p:PublishProfile=$publishProfile"
    )

    if (!(Test-Path $finalExePath)) {
        throw "Final executable was not generated: $finalExePath"
    }
}

$exeInfo = Get-Item $finalExePath

Write-Host ""
Write-Host "Release complete."
Write-Host "Executable: $($exeInfo.FullName)"
Write-Host ("Size: {0:N1} MB" -f ($exeInfo.Length / 1MB))
