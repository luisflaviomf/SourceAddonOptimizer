[CmdletBinding()]
param(
    [string]$SourceRoot = '',
    [string]$OutputPath = '',
    [string]$PackageVersion = '1.0.0'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = Join-Path $PSScriptRoot '..\companion\sourceaddonoptimizer_companion'
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $PSScriptRoot '..\GmodAddonCompressor-master\GmodAddonCompressor\Resources\SourceAddonOptimizerCompanion.zip'
}

function Get-Sha256Hex {
    param([byte[]]$Bytes)

    $hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        return (([BitConverter]::ToString($hash.ComputeHash($Bytes))) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $hash.Dispose()
    }
}

function Assert-AsciiLowerPath {
    param([string]$RelativePath)

    if ([string]::IsNullOrWhiteSpace($RelativePath) -or
        $RelativePath -cne $RelativePath.ToLowerInvariant() -or
        $RelativePath.Contains('\') -or
        $RelativePath.Contains(':') -or
        $RelativePath.StartsWith('/') -or
        $RelativePath.Contains('//')) {
        throw "Companion package paths must be safe lowercase ASCII: $RelativePath"
    }

    foreach ($character in $RelativePath.ToCharArray()) {
        if ([int]$character -gt 127) {
            throw "Companion package paths must be safe lowercase ASCII: $RelativePath"
        }
    }

    foreach ($part in $RelativePath.Split('/')) {
        if ([string]::IsNullOrEmpty($part) -or $part -eq '.' -or $part -eq '..' -or $part.Length -gt 128) {
            throw "Companion package path has an unsafe component: $RelativePath"
        }
    }

    if ($RelativePath.Length -gt 512) {
        throw "Companion package path exceeds 512 bytes: $RelativePath"
    }
}

function Write-ZipEntry {
    param(
        [System.IO.Compression.ZipArchive]$Archive,
        [string]$Name,
        [byte[]]$Bytes,
        [DateTimeOffset]$Timestamp
    )

    $entry = $Archive.CreateEntry($Name, [System.IO.Compression.CompressionLevel]::Optimal)
    $entry.LastWriteTime = $Timestamp
    $entry.ExternalAttributes = 0
    $stream = $entry.Open()
    try {
        $stream.Write($Bytes, 0, $Bytes.Length)
    }
    finally {
        $stream.Dispose()
    }
}

if ($PackageVersion -notmatch '^[A-Za-z0-9.-]{1,32}$') {
    throw "Invalid Companion package version: $PackageVersion"
}

$source = (Resolve-Path -LiteralPath $SourceRoot).Path.TrimEnd('\')
if ((Get-Item -LiteralPath $source -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Companion source root cannot be a reparse point: $source"
}

$payloads = New-Object 'System.Collections.Generic.Dictionary[string,byte[]]' ([StringComparer]::Ordinal)
$totalBytes = [int64]0
foreach ($directory in Get-ChildItem -LiteralPath $source -Recurse -Directory -Force) {
    if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Companion source cannot contain reparse point directories: $($directory.FullName)"
    }
}
foreach ($file in Get-ChildItem -LiteralPath $source -Recurse -File -Force) {
    if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Companion source cannot contain reparse points: $($file.FullName)"
    }

    $relative = $file.FullName.Substring($source.Length + 1).Replace('\', '/')
    Assert-AsciiLowerPath $relative
    $extension = [IO.Path]::GetExtension($relative)
    if ($extension -cne '.json' -and $extension -cne '.lua') {
        throw "Companion source contains a forbidden extension: $relative"
    }
    if (-not ($relative -eq 'addon.json' -or $relative.StartsWith('lua/'))) {
        throw "Companion source contains a file outside the declared addon tree: $relative"
    }

    $bytes = [IO.File]::ReadAllBytes($file.FullName)
    if ($extension -ceq '.lua' -and $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        throw "Companion Lua must be UTF-8 without BOM: $relative"
    }
    if ($payloads.ContainsKey($relative)) {
        throw "Duplicate Companion package path: $relative"
    }
    $payloads.Add($relative, $bytes)
    $totalBytes += $bytes.LongLength
}

if (-not $payloads.ContainsKey('addon.json')) {
    throw 'Companion package is missing addon.json.'
}
if ($payloads.Count -gt 512 -or $totalBytes -gt 20000000) {
    throw 'Companion package exceeds its file-count or byte limit.'
}

$autorun = @($payloads.Keys | Where-Object { $_.StartsWith('lua/autorun/') -and $_.EndsWith('.lua') })
if ($autorun.Count -ne 1) {
    throw "Companion package must contain exactly one autorun Lua file; found $($autorun.Count)."
}

$lintScript = Join-Path $PSScriptRoot 'test_companion_lua.ps1'
& powershell -NoProfile -ExecutionPolicy Bypass -File $lintScript -AddonRoot $source
if ($LASTEXITCODE -ne 0) {
    throw "Companion Lua lint failed with exit code $LASTEXITCODE."
}

$paths = [string[]]$payloads.Keys
[Array]::Sort($paths, [StringComparer]::Ordinal)
$declared = New-Object System.Collections.ArrayList
foreach ($path in $paths) {
    $bytes = $payloads[$path]
    [void]$declared.Add([ordered]@{
        path = $path
        bytes = [int64]$bytes.LongLength
        sha256 = Get-Sha256Hex $bytes
    })
}

$manifest = [ordered]@{
    schemaVersion = 1
    packageVersion = $PackageVersion
    addonDirectory = 'sourceaddonoptimizer_companion'
    files = @($declared)
}
$manifestJson = $manifest | ConvertTo-Json -Depth 6 -Compress
$utf8 = New-Object System.Text.UTF8Encoding($false)
$manifestBytes = $utf8.GetBytes($manifestJson)

$output = [IO.Path]::GetFullPath($OutputPath)
$outputParent = [IO.Path]::GetDirectoryName($output)
if ([string]::IsNullOrWhiteSpace($outputParent)) {
    throw "Invalid Companion package output: $OutputPath"
}
[IO.Directory]::CreateDirectory($outputParent) | Out-Null
$temporary = Join-Path $outputParent ('.' + [IO.Path]::GetFileName($output) + '.' + [Guid]::NewGuid().ToString('N') + '.partial')
$backup = $temporary + '.backup'
$fixedTimestamp = [DateTimeOffset]::new(2000, 1, 1, 0, 0, 0, [TimeSpan]::Zero)

try {
    $fileStream = New-Object System.IO.FileStream($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    try {
        $archive = [System.IO.Compression.ZipArchive]::new($fileStream, [System.IO.Compression.ZipArchiveMode]::Create, $true)
        try {
            Write-ZipEntry $archive 'companion-package-manifest.json' $manifestBytes $fixedTimestamp
            foreach ($path in $paths) {
                Write-ZipEntry $archive ('sourceaddonoptimizer_companion/' + $path) $payloads[$path] $fixedTimestamp
            }
        }
        finally {
            $archive.Dispose()
        }
        $fileStream.Flush($true)
    }
    finally {
        $fileStream.Dispose()
    }

    $expectedNames = @('companion-package-manifest.json') + @($paths | ForEach-Object { 'sourceaddonoptimizer_companion/' + $_ })
    $verifyStream = [IO.File]::OpenRead($temporary)
    try {
        $verifyArchive = [System.IO.Compression.ZipArchive]::new($verifyStream, [System.IO.Compression.ZipArchiveMode]::Read, $false)
        try {
            $actualNames = @($verifyArchive.Entries | ForEach-Object { $_.FullName })
            if ($actualNames.Count -ne $expectedNames.Count) {
                throw 'Companion ZIP entry count differs after creation.'
            }
            for ($index = 0; $index -lt $expectedNames.Count; $index++) {
                if ($actualNames[$index] -cne $expectedNames[$index]) {
                    throw "Companion ZIP ordering differs at entry $index."
                }
                if ($verifyArchive.Entries[$index].LastWriteTime.DateTime -ne $fixedTimestamp.DateTime) {
                    throw "Companion ZIP timestamp is not deterministic: $($actualNames[$index])"
                }
            }

            foreach ($record in $declared) {
                $entryName = 'sourceaddonoptimizer_companion/' + $record.path
                $entry = $verifyArchive.GetEntry($entryName)
                if ($null -eq $entry -or $entry.Length -ne $record.bytes) {
                    throw "Companion ZIP size mismatch: $entryName"
                }
                $entryStream = $entry.Open()
                try {
                    $memory = New-Object System.IO.MemoryStream
                    try {
                        $entryStream.CopyTo($memory)
                        $entryHash = Get-Sha256Hex $memory.ToArray()
                    }
                    finally {
                        $memory.Dispose()
                    }
                }
                finally {
                    $entryStream.Dispose()
                }
                if ($entryHash -cne $record.sha256) {
                    throw "Companion ZIP hash mismatch: $entryName"
                }
            }
        }
        finally {
            $verifyArchive.Dispose()
        }
    }
    finally {
        $verifyStream.Dispose()
    }

    if ([IO.File]::Exists($output)) {
        [IO.File]::Replace($temporary, $output, $backup, $true)
        if ([IO.File]::Exists($backup)) {
            [IO.File]::Delete($backup)
        }
    }
    else {
        [IO.File]::Move($temporary, $output)
    }

    $packageBytes = [IO.File]::ReadAllBytes($output)
    Write-Host "Companion package validated: $output ($($packageBytes.LongLength) bytes, sha256=$(Get-Sha256Hex $packageBytes))"
}
finally {
    if ([IO.File]::Exists($temporary)) {
        [IO.File]::Delete($temporary)
    }
    if ([IO.File]::Exists($backup)) {
        [IO.File]::Delete($backup)
    }
}
