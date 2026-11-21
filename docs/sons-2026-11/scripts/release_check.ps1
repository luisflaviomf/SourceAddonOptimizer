# Roda o assistente Otimizar do exe publicado num addon de teste (so sons) e guarda o registro.
# Baseado em C:\GModLab\bench\wizard_run.ps1. O addon precisa estar na lista de recentes do assistente.
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$AddonName,
    [string]$Objective = "Só texturas e sons",
    [string]$Profile = "Custo-benefício",
    [string]$LogOut = "C:\GModLab\revisao\sons\out\release_check_ui_log.txt"
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]
$TS = [System.Windows.Automation.TreeScope]
$CT = [System.Windows.Automation.ControlType]

function Say($m) { Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $m) }

$proc = Start-Process -FilePath $Exe -PassThru
Say "started pid $($proc.Id)"
$win = $null
for ($i = 0; $i -lt 180 -and -not $win; $i++) {
    Start-Sleep -Seconds 1
    $c = New-Object System.Windows.Automation.PropertyCondition($AE::ProcessIdProperty, $proc.Id)
    $win = $AE::RootElement.FindFirst($TS::Children, $c)
}
if (-not $win) { throw "main window not found" }
Say "window: $($win.Current.Name)"

function All($name) {
    $c = New-Object System.Windows.Automation.PropertyCondition($AE::NameProperty, $name)
    return $win.FindAll($TS::Descendants, $c)
}
function Owner($el) {
    $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
    $e = $el
    while ($e -ne $null) {
        $t = $e.Current.ControlType
        if ($t -eq $CT::Button -or $t -eq $CT::RadioButton) { return $e }
        $e = $walker.GetParent($e)
    }
    return $null
}
function Visible($name, $type) {
    foreach ($el in (All $name)) {
        if ($el.Current.IsOffscreen) { continue }
        $o = Owner $el
        if ($o -ne $null -and $o.Current.ControlType -eq $type -and -not $o.Current.IsOffscreen) { return $o }
    }
    return $null
}
function WaitFor($name, $type, $seconds, [switch]$Enabled) {
    for ($i = 0; $i -lt $seconds; $i++) {
        $o = Visible $name $type
        if ($o -ne $null -and (-not $Enabled -or $o.Current.IsEnabled)) { return $o }
        Start-Sleep -Seconds 1
    }
    throw "timeout waiting for '$name'"
}
function Press($o) { $o.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() }
function Pick($o) { $o.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern).Select() }

$state = "error"
try {
    Press (WaitFor $AddonName $CT::Button 300 -Enabled)
    Say "picked recent addon"
    Press (WaitFor "Continuar" $CT::Button 600 -Enabled)
    Say "step 2"
    Pick (WaitFor $Objective $CT::RadioButton 60 -Enabled)
    Pick (WaitFor $Profile $CT::RadioButton 60 -Enabled)
    Start-Sleep -Seconds 1
    Press (WaitFor "Continuar" $CT::Button 60 -Enabled)
    Say "step 3"
    $idc = New-Object System.Windows.Automation.PropertyCondition($AE::AutomationIdProperty, "Button_WizardStart")
    $start = $win.FindFirst($TS::Descendants, $idc)
    for ($i = 0; $i -lt 60 -and ($start -eq $null -or $start.Current.IsOffscreen -or -not $start.Current.IsEnabled); $i++) {
        Start-Sleep -Seconds 1
        $start = $win.FindFirst($TS::Descendants, $idc)
    }
    Press $start
    Say "running"
    $t0 = Get-Date
    $state = "timeout"
    while (((Get-Date) - $t0).TotalMinutes -lt 60) {
        Start-Sleep -Seconds 5
        if ((Visible "Abrir pasta" $CT::Button) -ne $null) { $state = "done"; break }
        if ((Visible "Tentar de novo" $CT::Button) -ne $null) { $state = "failed"; break }
    }
    Say "state: $state after $([int]((Get-Date) - $t0).TotalSeconds) s"
    try {
        $toggle = Visible "Mostrar detalhes" $CT::Button
        if ($toggle) { Press $toggle; Start-Sleep -Seconds 1 }
    } catch {}
    $box = $win.FindFirst($TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::NameProperty, "Detalhes da otimização")))
    if ($box) {
        $text = $box.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value
        Set-Content -LiteralPath $LogOut -Value $text -Encoding UTF8
        Say "log saved ($($text.Length) chars)"
    }
    $texts = $win.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition) |
        Where-Object { $_.Current.ControlType -eq $CT::Text -and -not $_.Current.IsOffscreen } |
        ForEach-Object { $_.Current.Name } | Where-Object { $_ -match "MiB|menor|%" }
    Say ("screen: " + ($texts -join " | "))
}
catch {
    Say ("ERRO: " + $_.Exception.Message)
}
finally {
    try { $win.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close() } catch {}
    Start-Sleep -Seconds 5
    if (-not $proc.HasExited) { Say "app still open; stopping it"; Stop-Process -Id $proc.Id -Force } else { Say "app closed" }
}
Say "final: $state"
