[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptRoot '..\..')).Path
$versionFile = Join-Path $repoRoot 'third_party\meshoptimizer\VERSION'
$expectedVersion = 'v1.2 9d9890c73011d75920af614485296d1e03e95448'
if ((Get-Content -Raw $versionFile).Trim() -cne $expectedVersion) {
    throw "Unexpected meshoptimizer VERSION; expected '$expectedVersion'."
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (!(Test-Path -LiteralPath $vswhere)) {
    throw "vswhere.exe not found at '$vswhere'; install Visual Studio 2022 C++ x64 build tools."
}

$installations = @(& $vswhere -all -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath)
if ($LASTEXITCODE -ne 0 -or $installations.Count -eq 0) {
    throw 'Visual Studio 2022 C++ x64 build tools were not found.'
}

$cmake = $null
$cmakeCommand = Get-Command cmake.exe -ErrorAction SilentlyContinue
if ($cmakeCommand) {
    $cmake = $cmakeCommand.Source
}
if (!$cmake) {
    foreach ($installation in $installations) {
        $candidate = Join-Path $installation 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
        if (Test-Path -LiteralPath $candidate) {
            $cmake = $candidate
            break
        }
    }
}
if (!$cmake) {
    throw 'cmake.exe was not found; install the Visual Studio CMake component or put CMake on PATH.'
}

$buildRoot = Join-Path $repoRoot ("build\maximum-meshopt-{0}" -f $PID)
$targetDir = Join-Path $scriptRoot 'bin\win-x64'
$temporaryDll = Join-Path $targetDir ("meshopt_bridge.dll.tmp-{0}" -f $PID)
$targetDll = Join-Path $targetDir 'meshopt_bridge.dll'
$backupDll = Join-Path $targetDir ("meshopt_bridge.dll.bak-{0}" -f $PID)

try {
    New-Item -ItemType Directory -Force -Path $buildRoot, $targetDir | Out-Null
    & $cmake -S $scriptRoot -B $buildRoot -G 'Visual Studio 17 2022' -A x64
    if ($LASTEXITCODE -ne 0) { throw "CMake configure failed with exit code $LASTEXITCODE." }
    & $cmake --build $buildRoot --config Release --target meshopt_bridge --parallel
    if ($LASTEXITCODE -ne 0) { throw "CMake build failed with exit code $LASTEXITCODE." }

    $builtDll = Join-Path $buildRoot 'Release\meshopt_bridge.dll'
    if (!(Test-Path -LiteralPath $builtDll)) { throw "Build did not produce '$builtDll'." }
    Copy-Item -LiteralPath $builtDll -Destination $temporaryDll -Force

    $bytes = [System.IO.File]::ReadAllBytes($temporaryDll)
    if ($bytes.Length -lt 64 -or $bytes[0] -ne 0x4d -or $bytes[1] -ne 0x5a) { throw 'Built DLL is not a valid PE file.' }
    $peOffset = [BitConverter]::ToInt32($bytes, 0x3c)
    if ($peOffset -lt 0 -or $peOffset + 6 -gt $bytes.Length) { throw 'Built DLL has an invalid PE header.' }
    $machine = [BitConverter]::ToUInt16($bytes, $peOffset + 4)
    if ($machine -ne 0x8664) { throw ("Built DLL is not x64 (PE machine 0x{0:x4})." -f $machine) }

    if (Test-Path -LiteralPath $targetDll) {
        [System.IO.File]::Replace($temporaryDll, $targetDll, $backupDll, $true)
        Remove-Item -LiteralPath $backupDll -Force -ErrorAction SilentlyContinue
    } else {
        [System.IO.File]::Move($temporaryDll, $targetDll)
    }
    Write-Host "Built x64 meshopt bridge: $targetDll"
} finally {
    Remove-Item -LiteralPath $temporaryDll -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backupDll -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $buildRoot -Recurse -Force -ErrorAction SilentlyContinue
}
