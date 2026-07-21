param(
    [Parameter(Mandatory = $true)]
    [string]$ExperimentRoot,

    [Parameter(Mandatory = $true)]
    [string]$GmodRoot
)

$ErrorActionPreference = 'Stop'

$experiment = (Resolve-Path -LiteralPath $ExperimentRoot).Path
$game = (Resolve-Path -LiteralPath $GmodRoot).Path
$garrysmod = Join-Path $game 'garrysmod'
if (-not (Test-Path -LiteralPath (Join-Path $game 'gmod.exe') -PathType Leaf) -or
    -not (Test-Path -LiteralPath $garrysmod -PathType Container)) {
    throw "Garry's Mod root is invalid: $game"
}

$manifestPath = Join-Path $experiment 'sample-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Sample manifest is missing: $manifestPath"
}

$addonRoot = Join-Path $garrysmod 'addons\gmod_optimizer_maximum_validation'
if (Test-Path -LiteralPath $addonRoot) {
    throw "Validation addon already exists; remove it explicitly before recreating: $addonRoot"
}

$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$trees = @('original', 'current-2x-8x8', 'maximum')
$treeKeys = @('original', 'current', 'maximum')
$entries = [System.Collections.Generic.List[object]]::new()

for ($treeIndex = 0; $treeIndex -lt $trees.Count; $treeIndex++) {
    $treeName = $trees[$treeIndex]
    $treeKey = $treeKeys[$treeIndex]
    $treeRoot = Join-Path $experiment $treeName
    for ($index = 0; $index -lt $manifest.Items.Count; $index++) {
        $item = $manifest.Items[$index]
        $relative = [string]$item.RelativePath
        $relativeBelowMaterials = $relative.Substring('materials/'.Length)
        $source = Join-Path $treeRoot ($relative.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Validation source is missing: $source"
        }

        $destination = Join-Path $addonRoot ("materials\gmod_optimizer_validation\$treeKey\" + $relativeBelowMaterials.Replace('/', '\'))
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination

        $id = 't{0:D3}' -f ($index + 1)
        $isCubemap = @($item.Tags) -contains 'structure:cubemap'
        $slot = if ($isCubemap) { '$envmap' } else { '$basetexture' }
        $textureKey = "gmod_optimizer_validation/$treeKey/" + $relativeBelowMaterials.Substring(0, $relativeBelowMaterials.Length - 4)
        $wrapperKey = "gmod_optimizer_validation/wrappers/$treeKey/$id"
        $wrapperPath = Join-Path $addonRoot ("materials\$wrapperKey.vmt".Replace('/', '\'))
        New-Item -ItemType Directory -Path (Split-Path -Parent $wrapperPath) -Force | Out-Null
        $shader = if ($isCubemap) { 'VertexLitGeneric' } else { 'UnlitGeneric' }
        $wrapper = @"
"$shader"
{
    "$slot" "$textureKey"
}
"@
        Set-Content -LiteralPath $wrapperPath -Value $wrapper -Encoding ASCII

        $versionTag = @($item.Tags | Where-Object { $_ -like 'version:*' } | Select-Object -First 1)
        $entries.Add([pscustomobject]@{
            id = $id
            tree = $treeKey
            relativePath = $relative
            wrapper = $wrapperKey
            slot = $slot
            version = if ($versionTag.Count -gt 0) { $versionTag[0].Substring('version:'.Length) } else { 'unknown' }
        })
    }
}

$criticalPatterns = @(
    'chrome_rusty_nm.vtf',
    'lights_nm.vtf',
    'tire_nm.vtf',
    'nodamage_lod0.vtf',
    'ford_fairlane/chrome.vtf',
    'shared/rust_skin.vtf'
)
$criticalIds = [System.Collections.Generic.List[string]]::new()
for ($index = 0; $index -lt $manifest.Items.Count; $index++) {
    $relative = ([string]$manifest.Items[$index].RelativePath).Replace('\', '/')
    if ($criticalPatterns | Where-Object { $relative.EndsWith($_, [System.StringComparison]::OrdinalIgnoreCase) }) {
        $criticalIds.Add(('t{0:D3}' -f ($index + 1)))
    }
}

$luaEntries = $entries | ForEach-Object {
    $path = $_.relativePath.Replace('\', '/').Replace('"', '\"')
    "    { id = `"$($_.id)`", tree = `"$($_.tree)`", path = `"$path`", wrapper = `"$($_.wrapper)`", slot = `"$($_.slot)`", version = `"$($_.version)`" },"
}
$luaCritical = ($criticalIds | ForEach-Object { '"' + $_ + '"' }) -join ', '
$lua = @"
local entries = {
$($luaEntries -join "`n")
}
local criticalIds = { $luaCritical }

local function validateTextures()
    local results = {}
    local failed = 0
    local materialByTreeAndId = {}
    for _, entry in ipairs(entries) do
        local material = Material(entry.wrapper, "smooth")
        local texture = material:GetTexture(entry.slot)
        local ok = not material:IsError() and texture ~= nil and not texture:IsError()
        local width = texture and texture:Width() or 0
        local height = texture and texture:Height() or 0
        if not ok then failed = failed + 1 end
        table.insert(results, {
            id = entry.id,
            tree = entry.tree,
            path = entry.path,
            version = entry.version,
            ok = ok,
            width = width,
            height = height
        })
        materialByTreeAndId[entry.tree .. ":" .. entry.id] = material
        print(string.format("[MAXIMUM_VTF_VALIDATE] %s tree=%s version=%s size=%dx%d path=%s",
            ok and "OK" or "FAIL", entry.tree, entry.version, width, height, entry.path))
    end

    file.Write("gmod_optimizer_vtf_validation.json", util.TableToJSON({
        total = #results,
        failed = failed,
        passed = #results - failed,
        results = results
    }, true))

    local frame = vgui.Create("DFrame")
    frame:SetSize(math.min(ScrW() - 20, 1260), math.min(ScrH() - 20, 700))
    frame:Center()
    frame:SetTitle("GmodAddonOptimizer - VTF Maximum validation")
    frame:SetDraggable(false)
    frame:ShowCloseButton(false)
    frame:MakePopup()

    local columnWidth = math.floor((frame:GetWide() - 32) / 3)
    local rowHeight = math.floor((frame:GetTall() - 70) / math.max(1, #criticalIds))
    local trees = { "original", "current", "maximum" }
    for column, tree in ipairs(trees) do
        local label = vgui.Create("DLabel", frame)
        label:SetText(string.upper(tree))
        label:SetPos(12 + (column - 1) * columnWidth, 30)
        label:SetSize(columnWidth - 8, 24)
        label:SetContentAlignment(5)
        for row, id in ipairs(criticalIds) do
            local image = vgui.Create("DImage", frame)
            image:SetPos(12 + (column - 1) * columnWidth, 54 + (row - 1) * rowHeight)
            image:SetSize(columnWidth - 8, rowHeight - 4)
            image:SetKeepAspect(true)
            image:SetMaterial(materialByTreeAndId[tree .. ":" .. id])
        end
    end

    timer.Simple(3, function()
        RunConsoleCommand("jpeg", "gmod_optimizer_vtf_validation")
    end)
    timer.Simple(6, function()
        RunConsoleCommand("quit")
    end)
end

hook.Add("InitPostEntity", "GmodOptimizerMaximumVtfValidation", function()
    timer.Simple(4, validateTextures)
end)
"@

$luaPath = Join-Path $addonRoot 'lua\autorun\client\gmod_optimizer_vtf_validation.lua'
New-Item -ItemType Directory -Path (Split-Path -Parent $luaPath) -Force | Out-Null
Set-Content -LiteralPath $luaPath -Value $lua -Encoding UTF8

Write-Output "Validation addon: $addonRoot"
Write-Output "Textures copied: $($entries.Count)"
Write-Output "Critical preview rows: $($criticalIds.Count)"
