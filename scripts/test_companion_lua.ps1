[CmdletBinding()]
param(
    [string] $AddonRoot
)

$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($AddonRoot)) {
    $AddonRoot = Join-Path $repositoryRoot "companion\sourceaddonoptimizer_companion"
}

$resolvedAddon = [System.IO.Path]::GetFullPath($AddonRoot)
if (!(Test-Path -LiteralPath $resolvedAddon -PathType Container)) {
    throw "Companion addon root not found: $resolvedAddon"
}

$linterZip = Join-Path $repositoryRoot "GmodAddonCompressor-master\GmodAddonCompressor\Resources\glualint.zip"
if (!(Test-Path -LiteralPath $linterZip -PathType Leaf)) {
    throw "Bundled glualint ZIP not found: $linterZip"
}

$temporaryBase = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar
$temporaryRoot = Join-Path $temporaryBase ("sao-companion-lint-" + [Guid]::NewGuid().ToString("N"))

try {
    New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
    Expand-Archive -LiteralPath $linterZip -DestinationPath $temporaryRoot
    $linter = Join-Path $temporaryRoot "glualint.exe"
    if (!(Test-Path -LiteralPath $linter -PathType Leaf)) {
        throw "glualint.exe is missing from the bundled ZIP."
    }

    $luaFiles = @(
        Get-ChildItem -LiteralPath $resolvedAddon -Recurse -File -Filter "*.lua" |
            Sort-Object FullName
    )
    if ($luaFiles.Count -eq 0) {
        throw "No Companion Lua files were found beneath: $resolvedAddon"
    }

    foreach ($luaFile in $luaFiles) {
        & $linter lint $luaFile.FullName
        if ($LASTEXITCODE -ne 0) {
            throw "glualint failed for '$($luaFile.FullName)' with exit code $LASTEXITCODE."
        }
    }

    Write-Output "Companion Lua lint passed ($($luaFiles.Count) files)."
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedTemporary = [System.IO.Path]::GetFullPath($temporaryRoot)
        $leaf = [System.IO.Path]::GetFileName($resolvedTemporary)
        if (!$resolvedTemporary.StartsWith($temporaryBase, [System.StringComparison]::OrdinalIgnoreCase) -or
            !$leaf.StartsWith("sao-companion-lint-", [System.StringComparison]::Ordinal)) {
            throw "Refusing to clean an unexpected temporary directory: $resolvedTemporary"
        }
        [System.IO.Directory]::Delete($resolvedTemporary, $true)
    }
}
