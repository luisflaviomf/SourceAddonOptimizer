SAO.ClientProfiler = SAO.ClientProfiler or {}

local active_identifier = nil
local round_robin = {}
local candidate_by_entity = {}
local round_robin_cursor = 1
local pending_by_path = {}
local pending_paths = {}
local next_send_at = 0

local function can_use_local_admin()
    local ply = LocalPlayer()

    return IsValid(ply) and (game.SinglePlayer() or ply:IsSuperAdmin())
end

local function normalized_model_path(ent)
    if not IsValid(ent) or ent:GetNoDraw() or ent:IsDormant() then return nil end
    local path = SAO.Contract.NormalizeVirtualPath(ent:GetModel())
    if not path or not string.StartWith(path, "models/") or not string.EndsWith(path, ".mdl") then return nil end

    return path
end

local function reset_candidates()
    round_robin = {}
    candidate_by_entity = {}
    round_robin_cursor = 1
    pending_by_path = {}
    pending_paths = {}
end

local function add_candidate(ent)
    if not IsValid(ent) or candidate_by_entity[ent] or #round_robin >= SAO.Constants.MAX_ENTITY_QUEUE then return end
    candidate_by_entity[ent] = {
        ent = ent,
        pixel_handle = util.GetPixelVisibleHandle(),
        visible_samples = 0,
        last_visible_frame = -1
    }
    round_robin[#round_robin + 1] = candidate_by_entity[ent]
end

local function queue_observation(path, evidence)
    local definition = SAO.Constants.EvidenceByName[evidence]
    if not active_identifier or not path or not definition then return end
    local current = pending_by_path[path]

    if current then
        if definition.rank > SAO.Constants.EvidenceByName[current].rank then pending_by_path[path] = evidence end

        return
    end

    if #pending_paths >= SAO.Constants.MAX_MODELS then return end
    pending_by_path[path] = evidence
    pending_paths[#pending_paths + 1] = path
end

local function remove_candidate(index)
    local candidate = round_robin[index]
    if candidate then candidate_by_entity[candidate.ent] = nil end
    local last = #round_robin
    round_robin[index] = round_robin[last]
    round_robin[last] = nil
    if round_robin_cursor > #round_robin then round_robin_cursor = 1 end
end

local function sample_candidate()
    if #round_robin == 0 then return end
    if round_robin_cursor > #round_robin then round_robin_cursor = 1 end
    local index = round_robin_cursor
    local candidate = round_robin[index]
    local ent = candidate and candidate.ent or nil
    local path = normalized_model_path(ent)

    if not path then
        remove_candidate(index)

        return
    end

    round_robin_cursor = round_robin_cursor % #round_robin + 1
    local frame = FrameNumber()
    if frame == candidate.last_visible_frame then return end
    local visible = util.PixelVisible(ent:WorldSpaceCenter(), math.Clamp(ent:BoundingRadius(), 1, 256), candidate.pixel_handle)
    if visible <= 0 then return end
    candidate.last_visible_frame = frame
    candidate.visible_samples = math.min(SAO.Constants.REPEATED_VISIBLE_SAMPLES, candidate.visible_samples + 1)
    local evidence = candidate.visible_samples >= SAO.Constants.REPEATED_VISIBLE_SAMPLES and "repeated_visible" or "visible"
    queue_observation(path, evidence)
end

local function sample_focus()
    local ply = LocalPlayer()
    if not IsValid(ply) then return end
    local view_model = ply:GetViewModel()
    local vehicle = ply:GetVehicle()
    local view_path = normalized_model_path(view_model)
    local vehicle_path = normalized_model_path(vehicle)
    if view_path then queue_observation(view_path, "focused") end
    if vehicle_path then queue_observation(vehicle_path, "focused") end
end

local function flush_observations()
    if not active_identifier or #pending_paths == 0 or RealTime() < next_send_at then return end
    local observations = {}

    while #pending_paths > 0 and #observations < SAO.Constants.MAX_OBSERVATIONS_PER_MESSAGE do
        local path = pending_paths[#pending_paths]
        pending_paths[#pending_paths] = nil
        local evidence = pending_by_path[path]
        pending_by_path[path] = nil

        if evidence then
            observations[#observations + 1] = {
                path = path,
                evidence = evidence
            }
        end
    end

    if #observations == 0 then return end
    net.Start(SAO.Constants.Net.Observation)
    net.WriteUInt(#active_identifier, 6)
    net.WriteData(active_identifier, #active_identifier)
    net.WriteUInt(#observations, 5)

    for _, observation in ipairs(observations) do
        net.WriteUInt(#observation.path, 10)
        net.WriteData(observation.path, #observation.path)
        net.WriteUInt(SAO.Constants.EvidenceByName[observation.evidence].wire, 3)
    end

    net.SendToServer()
    next_send_at = RealTime() + SAO.Constants.OBSERVATION_SEND_INTERVAL
end

function SAO.ClientProfiler.FocusEntity(ent)
    local path = normalized_model_path(ent)
    if not path then return false end
    queue_observation(path, "focused")

    return true
end

net.Receive(SAO.Constants.Net.BeginSession, function()
    if not can_use_local_admin() then return end
    local bytes = net.ReadUInt(6)
    if bytes == 0 or bytes > SAO.Constants.MAX_NET_IDENTIFIER_BYTES then return end
    active_identifier = net.ReadData(bytes)
    reset_candidates()

    for _, ent in ipairs(ents.GetAll()) do
        add_candidate(ent)
        if #round_robin >= SAO.Constants.MAX_ENTITY_QUEUE then break end
    end
end)

net.Receive(SAO.Constants.Net.EndSession, function()
    if not can_use_local_admin() then return end
    local bytes = net.ReadUInt(6)
    if bytes == 0 or bytes > SAO.Constants.MAX_NET_IDENTIFIER_BYTES then return end
    local identifier = net.ReadData(bytes)
    if identifier ~= active_identifier then return end
    active_identifier = nil
    reset_candidates()
end)

hook.Add("OnEntityCreated", "SAO.ClientProfileEntityCreated", function(ent)
    if not active_identifier then return end
    timer.Simple(0, function()
        if active_identifier then add_candidate(ent) end
    end)
end)

hook.Add("Think", "SAO.ClientProfileBudgetedVisibility", function()
    if not active_identifier then return end

    for _ = 1, SAO.Constants.PROFILER_CLIENT_BUDGET do
        sample_candidate()
    end

    sample_focus()
    flush_observations()
end)
