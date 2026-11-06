SAO.ServerResultWriter = SAO.ServerResultWriter or {}

local function safe_text(value, maximum)
    if not isstring(value) then return "" end
    value = string.gsub(value, "[%c]", " ")

    return string.sub(string.Trim(value), 1, maximum)
end

local function mounted_addons()
    local result = {}
    local ok, addons = pcall(function() return engine.GetAddons() end)
    if not ok or not istable(addons) then return result end

    for _, addon in ipairs(addons) do
        local workshop_id = tostring(addon.wsid or "")
        if addon.mounted and string.match(workshop_id, "^[0-9]+$") and #workshop_id <= 20 and #result < SAO.Constants.MAX_MODELS then
            result[#result + 1] = {
                workshop_id = workshop_id,
                title = safe_text(tostring(addon.title or ""), 256)
            }
        end
    end

    table.sort(result, function(left, right) return left.workshop_id < right.workshop_id end)

    return result
end

function SAO.ServerResultWriter.Environment(started_at)
    return {
        game_build = safe_text(tostring(VERSIONSTR or VERSION or BRANCH or "unknown"), 64),
        map = safe_text(game.GetMap() or "", 128),
        gamemode = safe_text(engine.ActiveGamemode() or "", 128),
        realm = game.SinglePlayer() and "singleplayer" or "server",
        session_seconds = math.Clamp(SysTime() - started_at, 0, 604800),
        mounted_addons = mounted_addons()
    }
end

function SAO.ServerResultWriter.OverallStatus(resources, interrupted)
    local has_warning = false
    local has_incomplete = interrupted == true

    for _, resource in ipairs(resources) do
        local status = resource.status
        if status == "FAILED" then return "FAILED" end
        if status == "WARNING" then has_warning = true end
        if status == "INCOMPLETE" then has_incomplete = true end
    end

    if has_warning then return "WARNING" end
    if has_incomplete then return "INCOMPLETE" end

    return "PASS"
end

local function structural_payloads(state, overall_status)
    local payloads = {}
    local cursor = 1

    while cursor <= #state.resources do
        local resources = {}
        while cursor <= #state.resources and #resources < 20 do
            resources[#resources + 1] = state.resources[cursor]
            cursor = cursor + 1
        end
        payloads[#payloads + 1] = {
            plan_sha256 = state.plan_sha256,
            overall_status = overall_status,
            resources = resources,
            performance = {
                tests = state.completed_tests,
                wall_seconds = math.Clamp(SysTime() - state.started_at, 0, 604800)
            },
            truncation = table.Copy(state.truncation)
        }
    end

    if #payloads == 0 then
        payloads[1] = {
            plan_sha256 = state.plan_sha256,
            overall_status = overall_status,
            resources = {},
            performance = {tests = state.completed_tests, wall_seconds = math.Clamp(SysTime() - state.started_at, 0, 604800)},
            truncation = table.Copy(state.truncation)
        }
    end

    return payloads
end

function SAO.ServerResultWriter.WriteStructural(ply, state, interrupted)
    if not SAO.Permissions.CanUse(ply) then return false, "permission denied" end
    if not istable(state) or not SAO.Contract.IsRunId(state.run_id) or not isstring(state.plan_sha256) or #state.plan_sha256 ~= 64 then return false, "invalid structural result state" end
    if not istable(state.resources) or #state.resources > SAO.Constants.MAX_VALIDATION_TESTS then return false, "structural resource limit exceeded" end
    local overall_status = SAO.ServerResultWriter.OverallStatus(state.resources, interrupted)
    local payloads = structural_payloads(state, overall_status)

    return SAO.Storage.WriteBundle("validation_results", "sao-structural-result-v1", state.run_id, os.date("!%Y-%m-%dT%H:%M:%SZ"), {
        name = "Source Addon Optimizer Companion",
        version = "1.0.0"
    }, SAO.ServerResultWriter.Environment(state.started_at), payloads)
end

function SAO.ServerResultWriter.Write(ply, schema, run_id, created_utc, producer, environment, payloads)
    if not SAO.Permissions.CanUse(ply) then return false, "permission denied" end

    return SAO.Storage.WriteBundle("validation_results", schema, run_id, created_utc, producer, environment, payloads)
end
