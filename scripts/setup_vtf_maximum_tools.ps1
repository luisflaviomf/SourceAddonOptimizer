$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$toolsRoot = Join-Path $repoRoot "experiments\_tools"
New-Item -ItemType Directory -Path $toolsRoot -Force | Out-Null

function Get-VerifiedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Sha256
    )

    if (Test-Path -LiteralPath $Destination) {
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash.ToLowerInvariant()
        if ($actual -eq $Sha256) {
            return
        }
        throw "Existing tool hash mismatch: $Destination"
    }

    $temporary = "$Destination.download"
    Invoke-WebRequest -Uri $Uri -OutFile $temporary
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $temporary).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) {
        Remove-Item -LiteralPath $temporary -Force
        throw "Downloaded tool hash mismatch for $Uri (expected $Sha256, got $actual)"
    }
    Move-Item -LiteralPath $temporary -Destination $Destination
}

$texconvPath = Join-Path $toolsRoot "texconv-may2026.exe"
Get-VerifiedDownload `
    -Uri "https://github.com/microsoft/DirectXTex/releases/download/may2026/texconv.exe" `
    -Destination $texconvPath `
    -Sha256 "dcfdec10244e02cf5037fba089c55fb7e1326b1c8181742d77d15fa5cb5eef06"

$compressonatorArchive = Join-Path $toolsRoot "compressonatorcli-4.5.52-win64.zip"
Get-VerifiedDownload `
    -Uri "https://github.com/GPUOpen-Tools/compressonator/releases/download/V4.5.52/compressonatorcli-4.5.52-win64.zip" `
    -Destination $compressonatorArchive `
    -Sha256 "49ca7c85e299fc104878aef82470d731a7c853cfb5facd879e9bc633fb48a444"

$compressonatorRoot = Join-Path $toolsRoot "compressonator-4.5.52"
$compressonatorPath = Join-Path $compressonatorRoot "compressonatorcli-4.5.52-win64\compressonatorcli.exe"
if (-not (Test-Path -LiteralPath $compressonatorPath)) {
    if (Test-Path -LiteralPath $compressonatorRoot) {
        throw "Compressonator extraction is incomplete: $compressonatorRoot"
    }
    Expand-Archive -LiteralPath $compressonatorArchive -DestinationPath $compressonatorRoot
}

if (-not (Test-Path -LiteralPath $compressonatorPath)) {
    throw "Compressonator CLI was not found after extraction."
}

Write-Host "texconv: $texconvPath"
& $texconvPath --version
Write-Host "CompressonatorCLI: $compressonatorPath"
& $compressonatorPath -version

python -c "import flip_evaluator; assert flip_evaluator.__version__ == '1.7' if hasattr(flip_evaluator, '__version__') else True" 2>$null
if ($LASTEXITCODE -ne 0) {
    python -m pip install --disable-pip-version-check flip-evaluator==1.7
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install NVIDIA FLIP evaluator 1.7."
    }
}
Write-Host "FLIP evaluator: 1.7"
