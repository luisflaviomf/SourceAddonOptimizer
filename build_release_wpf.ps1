[CmdletBinding()]
param(
    [string] $CandidateRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSCommandPath

$packageScript = Join-Path $repoRoot "pyinstaller\package_wpf_tools.ps1"
$csprojPath = Join-Path $repoRoot "GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj"
$runtimeIdentifier = "win-x64"
$configuration = "Release"

$projectDir = Split-Path -Parent $csprojPath
$runtimeBuildDir = Join-Path $projectDir "bin\$configuration\net6.0-windows\$runtimeIdentifier"
$runtimeObjDir = Join-Path $projectDir "obj\$configuration\net6.0-windows\$runtimeIdentifier"
$candidateMode = ![string]::IsNullOrWhiteSpace($CandidateRoot)
if ($candidateMode) {
    $publishDir = [System.IO.Path]::GetFullPath($CandidateRoot)
    if (Test-Path -LiteralPath $publishDir) {
        throw "Candidate publish root already exists; refusing to overwrite it: $publishDir"
    }
    $candidateBuildRoot = Join-Path (Split-Path -Parent $publishDir) (".build-" + (Split-Path -Leaf $publishDir))
    if (Test-Path -LiteralPath $candidateBuildRoot) {
        throw "Candidate build root already exists; refusing to reuse it: $candidateBuildRoot"
    }
    $candidateBinRoot = Join-Path $candidateBuildRoot "bin\"
    $candidateObjRoot = Join-Path $candidateBuildRoot "obj\"
    $candidateDefaultExcludes = "$projectDir\bin\**%3B$projectDir\obj\**"
}
else {
    $publishDir = Join-Path $runtimeBuildDir "publish"
}
$finalExePath = Join-Path $publishDir "GmodAddonOptimizer.exe"
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

Invoke-Step -Name "Clean runtime-specific build output" -Action {
    if ($candidateMode) {
        New-Item -ItemType Directory -Force -Path $candidateBuildRoot | Out-Null
        Invoke-Dotnet @(
            "clean",
            $csprojPath,
            "-c", $configuration,
            "-p:BaseOutputPath=$candidateBinRoot",
            "-p:BaseIntermediateOutputPath=$candidateObjRoot",
            "-p:DefaultItemExcludes=$candidateDefaultExcludes"
        )
    }
    else {
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

Invoke-Step -Name "Publish WPF x64 application" -Action {
    $publishArguments = @(
        "publish",
        $csprojPath,
        "-c", $configuration,
        "-r", $runtimeIdentifier,
        "-p:DebugType=None",
        "-p:DebugSymbols=false"
    )
    if ($candidateMode) {
        $publishArguments += @(
            "-p:BaseOutputPath=$candidateBinRoot",
            "-p:BaseIntermediateOutputPath=$candidateObjRoot",
            "-p:DefaultItemExcludes=$candidateDefaultExcludes",
            "--output", $publishDir
        )
    }
    Invoke-Dotnet $publishArguments

    if (!(Test-Path $finalExePath)) {
        throw "Final executable was not generated: $finalExePath"
    }
    $debugSymbols = @(Get-ChildItem -LiteralPath $publishDir -Filter "*.pdb" -File -Recurse)
    if ($debugSymbols.Count -ne 0) {
        throw "Publish unexpectedly contains debug symbols: $($debugSymbols.FullName -join ', ')"
    }
}

if ($candidateMode) {
    Invoke-Step -Name "Create candidate audit artifacts" -Action {
        $auditZip = Join-Path $publishDir "SourceAddonOptimizer.win-x64.zip"
        Copy-Item -LiteralPath $packagedZipPath -Destination $auditZip

        $candidateZip = "$publishDir.zip"
        $temporaryZip = "$candidateZip.$PID.tmp"
        if (Test-Path -LiteralPath $candidateZip) {
            throw "Candidate archive already exists; refusing to overwrite it: $candidateZip"
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        try {
            [System.IO.Compression.ZipFile]::CreateFromDirectory(
                $publishDir,
                $temporaryZip,
                [System.IO.Compression.CompressionLevel]::Optimal,
                $false
            )
            [System.IO.File]::Move($temporaryZip, $candidateZip)
        }
        finally {
            Remove-Item -LiteralPath $temporaryZip -Force -ErrorAction SilentlyContinue
        }

        $artifacts = @($finalExePath, $auditZip, $packagedZipPath, $candidateZip) | ForEach-Object {
            $item = Get-Item -LiteralPath $_
            [PSCustomObject][ordered]@{
                path = $item.FullName
                size = [Int64]$item.Length
                sha256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
        $hashManifest = "$publishDir.hashes.json"
        [System.IO.File]::WriteAllText(
            $hashManifest,
            ($artifacts | ConvertTo-Json -Depth 4),
            [System.Text.UTF8Encoding]::new($false)
        )
        Write-Host "Candidate publish: $publishDir"
        Write-Host "Candidate archive: $candidateZip"
        Write-Host "Candidate hashes: $hashManifest"
    }
}

$exeInfo = Get-Item $finalExePath

Write-Host ""
Write-Host "Release complete."
Write-Host "Executable: $($exeInfo.FullName)"
Write-Host ("Size: {0:N1} MB" -f ($exeInfo.Length / 1MB))
