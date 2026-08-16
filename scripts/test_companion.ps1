[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot

function Format-CommandArgument {
    param([Parameter(Mandatory = $true)][string] $Value)

    if ($Value -match '[\s"]') {
        return '"' + $Value.Replace('"', '\"') + '"'
    }
    return $Value
}

function Invoke-CompanionGate {
    param(
        [Parameter(Mandatory = $true)][string] $Name,
        [Parameter(Mandatory = $true)][string] $Executable,
        [Parameter(Mandatory = $true)][string[]] $Arguments
    )

    $rendered = @($Executable) + @($Arguments | ForEach-Object { Format-CommandArgument $_ })
    Write-Host (">>> [{0}] {1}" -f $Name, ($rendered -join " "))
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Companion gate '$Name' failed with exit code $LASTEXITCODE."
    }
}

Push-Location $repositoryRoot
try {
    Invoke-CompanionGate "Python contract" "python" @("-m", "pytest", "tests\companion_contract", "-q", "-ra")
    Invoke-CompanionGate "Addon contract" "python" @("-m", "pytest", "tests\companion_addon", "-q")
    Invoke-CompanionGate "Maximum focused" "python" @("-m", "pytest", "tests\maximum_optimizer", "-q")
    Invoke-CompanionGate "Legacy backend regression" "python" @("-m", "pytest", "tests\test_batch_compile_paths.py", "tests\test_batch_optimize_parallel.py", "-q")
    Invoke-CompanionGate "Companion Lua lint" "powershell" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "scripts\test_companion_lua.ps1")
    Invoke-CompanionGate "WPF optimizer contract" "dotnet" @("run", "--project", "tests\wpf_optimizer_contract\GmodAddonOptimizer.ContractTests.csproj", "-c", "Release")
    Invoke-CompanionGate "WPF Companion contract" "dotnet" @("run", "--project", "tests\wpf_companion_contract\GmodAddonOptimizer.CompanionContractTests.csproj", "-c", "Release")
    Invoke-CompanionGate "WPF Release build" "dotnet" @("build", "GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj", "-c", "Release", "--no-restore", "-v:minimal")
    Write-Host ">>> All Companion gates passed."
}
finally {
    Pop-Location
}
