[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $PackageRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path -LiteralPath $PackageRoot).Path.TrimEnd('\', '/')
$manifestRelative = "_internal/maximum_optimizer/native/tool-package-manifest.json"
$dllRelative = "_internal/maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll"
$manifestPath = Join-Path $root $manifestRelative.Replace('/', '\')
$dllPath = Join-Path $root $dllRelative.Replace('/', '\')

if (!(Test-Path -LiteralPath $dllPath -PathType Leaf)) {
    throw "Promoted native DLL is missing from package staging: $dllPath"
}

$dllBytes = [System.IO.File]::ReadAllBytes($dllPath)
if ($dllBytes.Length -lt 64 -or $dllBytes[0] -ne 0x4d -or $dllBytes[1] -ne 0x5a) {
    throw "Promoted native DLL is not a PE file: $dllPath"
}
$peOffset = [BitConverter]::ToInt32($dllBytes, 0x3c)
if ($peOffset -lt 0 -or $peOffset + 6 -gt $dllBytes.Length) {
    throw "Promoted native DLL has a truncated PE header: $dllPath"
}
$machine = [BitConverter]::ToUInt16($dllBytes, $peOffset + 4)
if ($machine -ne 0x8664) {
    throw ("Promoted native DLL is not AMD64 (0x{0:x4}): {1}" -f $machine, $dllPath)
}

$rootPrefix = $root + [System.IO.Path]::DirectorySeparatorChar
$files = @(
    Get-ChildItem -LiteralPath $root -Recurse -File |
        Where-Object { $_.FullName -ne $manifestPath } |
        ForEach-Object {
            if (!$_.FullName.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Package file escapes staging root: $($_.FullName)"
            }
            $relative = $_.FullName.Substring($rootPrefix.Length).Replace('\', '/')
            [PSCustomObject][ordered]@{
                path = $relative
                size = [Int64]$_.Length
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        } |
        Sort-Object -Property @{ Expression = {
            [BitConverter]::ToString([System.Text.Encoding]::UTF8.GetBytes($_.path))
        } } -CaseSensitive
)

if ($files.Count -eq 0) {
    throw "Package staging contains no files: $root"
}
$dllEntry = @($files | Where-Object { $_.path -ceq $dllRelative })
if ($dllEntry.Count -ne 1) {
    throw "Package file manifest does not contain exactly one promoted native DLL."
}

$manifest = [PSCustomObject][ordered]@{
    schemaVersion = 1
    toolName = "SourceAddonOptimizer"
    toolVersion = "0.1.18"
    silhouette = [PSCustomObject][ordered]@{
        apiVersion = "1.0.0"
        buildId = "maximum-silhouette-raw-v1-20260722"
        architecture = "x64"
        dllPath = $dllRelative
        sha256 = $dllEntry[0].sha256
        size = $dllEntry[0].size
        minimumWorkerContract = "0.1.18"
        minimumWpfContract = "0.1.18"
    }
    files = $files
}

$manifestDirectory = Split-Path -Parent $manifestPath
New-Item -ItemType Directory -Force -Path $manifestDirectory | Out-Null
$temporaryPath = "$manifestPath.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
try {
    $json = $manifest | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($temporaryPath, $json, [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporaryPath -Destination $manifestPath -Force
}
finally {
    Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
}

Write-Host "Generated package manifest: $manifestPath"
