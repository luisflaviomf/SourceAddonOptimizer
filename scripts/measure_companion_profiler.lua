-- Test-only real-game driver. Copy into lua/autorun/server for a bounded smoke run,
-- then remove that copied file. This script deliberately uses only Companion APIs.

local entity_classes = {
    "lvs_wheeldrive_vw_beetle",
    "lvs_wheeldrive_caterham620",
    "lvs_wheeldrive_ferrari365",
    "lvs_wheeldrive_dc_fairlane500",
    "lvs_wheeldrive_vw_touareg_1",
    "lvs_wheeldrive_charger",
    "lvs_wheeldrive_supra",
    "lvs_wheeldrive_pontiac_transam3"
}

if CLIENT then
    hook.Add("InitPostEntity", "SAO.CompanionBenchmarkFocus", function()
        timer.Simple(8, function()
            if not SAO or not SAO.ClientProfiler then return end
            for _, class_name in ipairs(entity_classes) do
                for _, ent in ipairs(ents.FindByClass(class_name)) do
                    SAO.ClientProfiler.FocusEntity(ent)
                end
            end
        end)
    end)

    return
end

if not SERVER then return end

local spawned = {}
local started = false

local function owner()
    return player.GetHumans()[1]
end

local function cleanup()
    for _, ent in ipairs(spawned) do
        if IsValid(ent) then ent:Remove() end
    end
    spawned = {}
end

local function spawn_corpus()
    for index, class_name in ipairs(entity_classes) do
        local ent = ents.Create(class_name)
        if IsValid(ent) then
            ent:SetPos(Vector((index - 1) * 360, 0, 32))
            ent:SetAngles(Angle(0, 90, 0))
            ent:Spawn()
            ent:Activate()
            spawned[#spawned + 1] = ent
        else
            MsgN("[SAO BENCH] Could not spawn " .. class_name)
        end
    end
end

local function begin_profile()
    if started or not SAO or not SAO.ServerProfiler then return end
    local ply = owner()
    if not IsValid(ply) then
        timer.Simple(1, begin_profile)
        return
    end
    local ok, run_id = SAO.ServerProfiler.Start(ply)
    if not ok then
        MsgN("[SAO BENCH] Profile start failed: " .. tostring(run_id))
        return
    end
    started = true
    MsgN("[SAO BENCH] Profile started: " .. tostring(run_id))
    spawn_corpus()

    timer.Simple(4, function()
        if not IsValid(ply) then return end
        for _, ent in ipairs(spawned) do
            if IsValid(ent) then
                ply:SetEyeAngles((ent:WorldSpaceCenter() - ply:EyePos()):Angle())
            end
        end
    end)

    timer.Simple(15, function()
        local success, result = SAO.ServerProfiler.StopAndExport(ply)
        MsgN("[SAO BENCH] Profile export: " .. tostring(success) .. " " .. tostring(result))
        cleanup()
        timer.Simple(1, function() game.ConsoleCommand("quit\n") end)
    end)
end

hook.Add("InitPostEntity", "SAO.CompanionBenchmarkProfile", function()
    timer.Simple(3, begin_profile)
end)

hook.Add("ShutDown", "SAO.CompanionBenchmarkCleanup", cleanup)
