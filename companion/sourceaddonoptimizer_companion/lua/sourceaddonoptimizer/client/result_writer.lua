SAO.ClientResultWriter = SAO.ClientResultWriter or {}

local INDEX_FILE = "run-index.json"
local COMPLETE_FILE = "complete.ready.json"

local resource_fields = {id = true, test_id = true, path = true, status = true, reason = true, warnings = true}
local screenshot_fields = {id = true, test_id = true, path = true, sha256 = true, bytes = true, view = true, retry_count = true, status = true, camera = true, state = true}
local camera_fields = {fov = true, distance = true, crop_width = true, crop_height = true}
local state_fields = {skin = true, bodygroups = true}
local bodygroup_fields = {name = true, value = true}
local allowed_views = {front = true, side = true, rear = true, isometric = true, closeup = true}

local function finite(value)
    return isnumber(value) and value == value and value ~= math.huge and value ~= -math.huge
end

local function integer(value, minimum, maximum)
    return finite(value) and value == math.floor(value) and value >= minimum and value <= maximum
end

local function safe_text(value, maximum)
    if not isstring(value) then return "" end

    return string.sub(string.Trim(string.gsub(value, "[%c]", " ")), 1, maximum)
end

local function mounted_addons()
    local result = {}
    local ok, addons = pcall(function() return engine.GetAddons() end)
    if not ok or not istable(addons) then return result end

    for _, addon in ipairs(addons) do
        local workshop_id = tostring(addon.wsid or "")
        if addon.mounted and string.match(workshop_id, "^[0-9]+$") and #workshop_id <= 20 and #result < SAO.Constants.MAX_MODELS then
            result[#result + 1] = {workshop_id = workshop_id, title = safe_text(tostring(addon.title or ""), 256)}
        end
    end

    table.sort(result, function(left, right) return left.workshop_id < right.workshop_id end)

    return result
end


local function environment(started_at)
    return {
        game_build = safe_text(tostring(VERSIONSTR or VERSION or BRANCH or "unknown"), 64),
        map = safe_text(game.GetMap() or "", 128),
        gamemode = safe_text(engine.ActiveGamemode() or "", 128),
        realm = "client",
        session_seconds = math.Clamp(SysTime() - started_at, 0, 604800),
        mounted_addons = mounted_addons()
    }
end

local function overall_status(resources, interrupted)
    local warning = false
    local incomplete = interrupted == true

    for _, value in ipairs(resources) do
        if value.status == "FAILED" then return "FAILED" end
        if value.status == "WARNING" then warning = true end
        if value.status == "INCOMPLETE" then incomplete = true end
    end

    if warning then return "WARNING" end
    if incomplete then return "INCOMPLETE" end

    return "PASS"
end

local function write_atomic(path, raw)
    local partial = path .. ".partial.json"
    local handle = file.Open(partial, "wb", "DATA")
    if not handle then return false end
    handle:Write(raw)
    handle:Flush()
    handle:Close()
    if file.Size(partial, "DATA") ~= #raw or not file.Rename(partial, path) then return false end

    return file.Exists(path, "DATA")
end

local function result_root(run_id)
    local parent = SAO.Constants.DATA_ROOT .. "/" .. SAO.Constants.DataFolders.validation_results .. "/" .. run_id
    file.CreateDir(parent)

    for attempt = 1, 9999 do
        local root = parent .. "/" .. string.format("visual-%04d", attempt)
        if not file.IsDir(root, "DATA") and not file.Exists(root .. "/" .. INDEX_FILE, "DATA") then return root end
    end

    return nil
end

local function payloads_for(state, status)
    local screenshots_by_test = {}
    for _, screenshot in ipairs(state.screenshots) do
        screenshots_by_test[screenshot.test_id] = screenshots_by_test[screenshot.test_id] or {}
        screenshots_by_test[screenshot.test_id][#screenshots_by_test[screenshot.test_id] + 1] = screenshot
    end
    local payloads = {}
    local cursor = 1

    while cursor <= #state.resources do
        local resources = {}
        local screenshots = {}
        while cursor <= #state.resources and #resources < 20 do
            local current = state.resources[cursor]
            resources[#resources + 1] = current
            for _, screenshot in ipairs(screenshots_by_test[current.test_id] or {}) do
                screenshots[#screenshots + 1] = screenshot
            end
            cursor = cursor + 1
        end
        payloads[#payloads + 1] = {
            plan_sha256 = state.plan_sha256,
            overall_status = status,
            resources = resources,
            screenshots = screenshots,
            performance = {
                models = state.completed_models,
                screenshots = #state.screenshots,
                wall_seconds = math.Clamp(SysTime() - state.started_at, 0, 604800)
            },
            truncation = table.Copy(state.truncation)
        }
    end

    if #payloads == 0 then
        payloads[1] = {
            plan_sha256 = state.plan_sha256,
            overall_status = status,
            resources = {},
            screenshots = {},
            performance = {models = 0, screenshots = 0, wall_seconds = math.Clamp(SysTime() - state.started_at, 0, 604800)},
            truncation = table.Copy(state.truncation)
        }
    end

    return payloads
end

local function validate_resource(value)
    local ok = SAO.Contract.ValidateClosedKeys(value, resource_fields)
    if not ok or not isstring(value.id) or #value.id < 1 or #value.id > 64 or not isstring(value.test_id) or #value.test_id < 1 or #value.test_id > 64 or not SAO.Validation.Statuses[value.status] or not isstring(value.reason) or #value.reason > 1024 or not istable(value.warnings) or #value.warnings > SAO.Constants.MAX_VALIDATION_WARNINGS then return false end
    if not SAO.Contract.NormalizeVirtualPath(value.path) then return false end
    for _, warning in ipairs(value.warnings) do
        if not isstring(warning) or #warning > 512 then return false end
    end

    return true
end

local function validate_screenshot(value, run_id)
    local ok = SAO.Contract.ValidateClosedKeys(value, screenshot_fields)
    local expected_prefix = "screenshots/" .. run_id .. "/"
    local filename = isstring(value.path) and string.sub(value.path, #expected_prefix + 1) or ""
    if not ok or not isstring(value.id) or #value.id < 1 or #value.id > 64 or not isstring(value.test_id) or #value.test_id < 1 or #value.test_id > 64 or not isstring(value.path) or not string.StartWith(value.path, expected_prefix) or string.match(filename, "^[a-z0-9._-]+%.png$") == nil or not isstring(value.sha256) or #value.sha256 ~= 64 or string.match(value.sha256, "^[a-f0-9]+$") == nil or not integer(value.bytes, 0, SAO.Constants.MAX_BUNDLE_BYTES) or not allowed_views[value.view] or not integer(value.retry_count, 0, SAO.Constants.SCREENSHOT_RETRIES) or not SAO.Validation.Statuses[value.status] then return false end
    if not istable(value.camera) or not SAO.Contract.ValidateClosedKeys(value.camera, camera_fields) or not finite(value.camera.fov) or value.camera.fov < 1 or value.camera.fov > 120 or not finite(value.camera.distance) or value.camera.distance < 0 or value.camera.distance > 1000000 or not integer(value.camera.crop_width, 1, 4096) or not integer(value.camera.crop_height, 1, 4096) then return false end
    if not istable(value.state) or not SAO.Contract.ValidateClosedKeys(value.state, state_fields) or not integer(value.state.skin, 0, 255) or not istable(value.state.bodygroups) or #value.state.bodygroups > SAO.Constants.MAX_BODYGROUP_STATES_PER_MODEL then return false end
    for _, bodygroup in ipairs(value.state.bodygroups) do
        if not SAO.Contract.ValidateClosedKeys(bodygroup, bodygroup_fields) or not isstring(bodygroup.name) or #bodygroup.name < 1 or #bodygroup.name > 128 or not integer(bodygroup.value, 0, 255) then return false end
    end

    return true
end

function SAO.ClientResultWriter.WriteVisual(state, interrupted)
    if not istable(state) or not SAO.Contract.IsRunId(state.run_id) or not isstring(state.plan_sha256) or #state.plan_sha256 ~= 64 or string.match(state.plan_sha256, "^[a-f0-9]+$") == nil then return false, "invalid visual result state" end
    if not istable(state.resources) or #state.resources > SAO.Constants.MAX_PLAN_MODELS or not istable(state.screenshots) or #state.screenshots > SAO.Constants.MAX_SCREENSHOTS then return false, "visual result exceeds its caps" end
    for _, value in ipairs(state.resources) do
        if not validate_resource(value) then return false, "invalid visual resource result" end
    end
    for _, value in ipairs(state.screenshots) do
        if not validate_screenshot(value, state.run_id) then return false, "invalid screenshot camera/state metadata" end
    end
    local status = overall_status(state.resources, interrupted)
    local payloads = payloads_for(state, status)
    local root = result_root(state.run_id)
    if not root then return false, "visual result attempt limit reached" end
    file.CreateDir(root)
    local producer = {name = "Source Addon Optimizer Companion", version = "1.0.0"}
    local created_utc = os.date("!%Y-%m-%dT%H:%M:%SZ")
    local result_environment = environment(state.started_at)
    local declarations = {}
    local total_bytes = 0

    for sequence, payload in ipairs(payloads) do
        local envelope = {
            schema = "sao-visual-result-v1",
            contract = {major = SAO.Constants.CONTRACT_MAJOR, minor = SAO.Constants.CONTRACT_MINOR},
            run_id = state.run_id,
            created_utc = created_utc,
            producer = producer,
            environment = result_environment,
            chunk = {sequence = sequence, total = #payloads},
            payload = payload
        }
        local valid, reason = SAO.Contract.ValidateEnvelope(envelope, "sao-visual-result-v1", state.run_id, sequence, #payloads)
        if not valid then return false, reason end
        local raw, encode_reason = SAO.Contract.EncodeJSON(envelope)
        if not raw or #raw > SAO.Constants.MAX_CHUNK_BYTES then return false, encode_reason or "visual result chunk exceeds its cap" end
        total_bytes = total_bytes + #raw
        if total_bytes > SAO.Constants.MAX_BUNDLE_BYTES then return false, "visual result bundle exceeds its cap" end
        local filename = string.format("chunk-%04d.json", sequence)
        if not write_atomic(root .. "/" .. filename, raw) then return false, "visual result chunk write failed" end
        declarations[#declarations + 1] = {sequence = sequence, file = filename, bytes = #raw, sha256 = SAO.Contract.Sha256(raw)}
    end

    local index = {
        schema = "sao-run-index-v1",
        contract = {major = SAO.Constants.CONTRACT_MAJOR, minor = SAO.Constants.CONTRACT_MINOR},
        run_id = state.run_id,
        payload_schema = "sao-visual-result-v1",
        created_utc = created_utc,
        producer = producer,
        environment = result_environment,
        chunk_count = #declarations,
        total_bytes = total_bytes,
        chunks = declarations
    }
    local index_raw, index_reason = SAO.Contract.EncodeJSON(index)
    if not index_raw or #index_raw > SAO.Constants.MAX_INDEX_BYTES then return false, index_reason or "visual result index exceeds its cap" end
    if not write_atomic(root .. "/" .. INDEX_FILE, index_raw) then return false, "visual result index write failed" end
    local completion_raw, completion_reason = SAO.Contract.EncodeJSON({schema = "sao-completion-v1", run_id = state.run_id, index_sha256 = SAO.Contract.Sha256(index_raw)})
    if not completion_raw or #completion_raw > SAO.Constants.MAX_COMPLETION_BYTES then return false, completion_reason or "visual completion marker exceeds its cap" end
    if not write_atomic(root .. "/" .. COMPLETE_FILE, completion_raw) then return false, "visual completion marker write failed" end

    return true, root
end

function SAO.ClientResultWriter.BuildRequest(schema, run_id, created_utc, producer, result_environment, payloads)
    if not SAO.Constants.Schemas[schema] or not SAO.Contract.IsRunId(run_id) or not istable(payloads) then return nil, "invalid result request" end

    return {schema = schema, run_id = run_id, created_utc = created_utc, producer = producer, environment = result_environment, payloads = payloads}
end
