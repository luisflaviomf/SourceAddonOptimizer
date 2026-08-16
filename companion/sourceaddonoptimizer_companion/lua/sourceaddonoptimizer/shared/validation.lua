SAO.Validation = SAO.Validation or {}

SAO.Validation.TestKinds = {
    ["model_structure"] = true,
    ["model_state"] = true,
    ["material_resolve"] = true,
    ["model_render"] = true
}

SAO.Validation.Statuses = {
    ["PASS"] = true,
    ["WARNING"] = true,
    ["FAILED"] = true,
    ["INCOMPLETE"] = true
}

local allowed_views = {
    front = true,
    side = true,
    rear = true,
    isometric = true,
    closeup = true
}

local test_fields = {
    id = true,
    kind = true,
    path = true,
    required_files = true,
    expected_phy = true,
    skins = true,
    bodygroups = true,
    materials = true,
    views = true,
    priority = true,
    expected_bytes = true
}

local bodygroup_fields = {name = true, value = true}
local payload_fields = {tests = true, limits = true, dx80_excluded = true, truncation = true}
local limit_fields = {max_models = true, max_screenshots = true, max_ordinary_views = true, screenshot_retries = true}
local truncation_fields = {tests = true, models = true, screenshots = true}
local chunk_declaration_fields = {sequence = true, file = true, bytes = true, sha256 = true}

local function is_integer(value, minimum, maximum)
    return isnumber(value) and value == math.floor(value) and value >= minimum and value <= maximum
end

local function is_identifier(value)
    return isstring(value) and #value >= 1 and #value <= 64 and string.match(value, "^[a-z0-9][a-z0-9._-]*$") ~= nil
end

local function bounded_array(value, maximum)
    return istable(value) and #value <= maximum and table.Count(value) == #value
end

local function normalize_paths(values, maximum, required_root, required_extension)
    if not bounded_array(values, maximum) then return nil, "invalid bounded path array" end
    local result = {}
    local seen = {}

    for _, raw in ipairs(values) do
        local path, reason = SAO.Contract.NormalizeVirtualPath(raw)
        if not path then return nil, reason end
        if required_root and not string.StartWith(path, required_root .. "/") then return nil, "invalid path root" end
        if required_extension and not string.EndsWith(path, required_extension) then return nil, "invalid path extension" end
        if seen[path] then return nil, "duplicate path" end
        if string.StartWith(path, "lua/") or string.StartWith(path, "sound/") or string.StartWith(path, "maps/") then return nil, "forbidden validation path" end
        if string.EndsWith(path, ".dx80.vtx") then return nil, "DX80 is forbidden in validation plans" end
        seen[path] = true
        result[#result + 1] = path
    end

    return result
end

local function model_stem(path)
    return string.sub(path, 1, #path - 4)
end

local function contains(values, expected)
    for _, value in ipairs(values) do
        if value == expected then return true end
    end

    return false
end

function SAO.Validation.IsKnownTestKind(kind)
    return isstring(kind) and SAO.Validation.TestKinds[kind] == true
end

function SAO.Validation.ValidateTest(value)
    local ok, reason = SAO.Contract.ValidateClosedKeys(value, test_fields)
    if not ok then return false, reason end
    if not is_identifier(value.id) then return false, "invalid validation test id" end
    if not SAO.Validation.IsKnownTestKind(value.kind) then return false, "unknown validation test kind" end
    local path, path_reason = SAO.Contract.NormalizeVirtualPath(value.path)
    if not path then return false, path_reason end
    local is_material = value.kind == "material_resolve"
    if is_material then
        if not string.StartWith(path, "materials/") or not string.EndsWith(path, ".vmt") then return false, "material test requires a VMT path" end
    elseif not string.StartWith(path, "models/") or not string.EndsWith(path, ".mdl") then
        return false, "model test requires an MDL path"
    end

    local required, required_reason = normalize_paths(value.required_files, 64)
    if not required then return false, required_reason end
    local materials, material_reason = normalize_paths(value.materials, 4096, "materials", ".vmt")
    if not materials then return false, material_reason end
    if not isbool(value.expected_phy) then return false, "expected_phy must be boolean" end
    if not is_integer(value.priority, 0, 1000000) or not is_integer(value.expected_bytes, 0, 9007199254740991) then return false, "invalid priority or expected bytes" end

    if is_material and not contains(required, path) then return false, "material test target is missing from required files" end
    if not is_material then
        local stem = model_stem(path)
        if not contains(required, path) and value.kind ~= "model_state" then return false, "model file is missing from required files" end
        if value.kind ~= "model_state" and (not contains(required, stem .. ".vvd") or not contains(required, stem .. ".dx90.vtx")) then return false, "required DX90 model sidecars are missing" end
        if value.expected_phy ~= contains(required, stem .. ".phy") and value.kind ~= "model_state" then return false, "expected PHY contract mismatch" end
    end

    if not bounded_array(value.skins, 256) then return false, "invalid skins" end
    local skins = {}
    for _, skin in ipairs(value.skins) do
        if not is_integer(skin, 0, 255) or skins[skin] then return false, "invalid or duplicate skin" end
        skins[skin] = true
    end

    if not bounded_array(value.bodygroups, 1024) then return false, "invalid bodygroups" end
    local bodygroups = {}
    for _, state in ipairs(value.bodygroups) do
        local state_ok, state_reason = SAO.Contract.ValidateClosedKeys(state, bodygroup_fields)
        if not state_ok then return false, state_reason end
        if not isstring(state.name) or #state.name < 1 or #state.name > 128 or string.find(state.name, "[^%w_. %-]") then return false, "invalid bodygroup name" end
        if not is_integer(state.value, 0, 255) then return false, "invalid bodygroup value" end
        local key = string.lower(state.name) .. "\0" .. tostring(state.value)
        if bodygroups[key] then return false, "duplicate bodygroup state" end
        bodygroups[key] = true
    end

    if not bounded_array(value.views, SAO.Constants.MAX_ORDINARY_VIEWS) then return false, "invalid validation views" end
    local views = {}
    for _, view in ipairs(value.views) do
        if not allowed_views[view] or views[view] then return false, "invalid or duplicate validation view" end
        views[view] = true
    end
    if value.kind == "model_render" and #value.views == 0 then return false, "render test requires a view" end
    if value.kind ~= "model_render" and #value.views ~= 0 then return false, "only render tests may request views" end

    value.path = path
    value.required_files = required
    value.materials = materials

    return true
end

local function validate_plan_payload(payload, state)
    local ok, reason = SAO.Contract.ValidateClosedKeys(payload, payload_fields)
    if not ok then return false, reason end
    if payload.dx80_excluded ~= true or not bounded_array(payload.tests, SAO.Constants.MAX_VALIDATION_TESTS) then return false, "invalid validation payload" end
    local limits_ok, limits_reason = SAO.Contract.ValidateClosedKeys(payload.limits, limit_fields)
    if not limits_ok then return false, limits_reason end
    if not is_integer(payload.limits.max_models, 1, SAO.Constants.MAX_PLAN_MODELS) or not is_integer(payload.limits.max_screenshots, 1, SAO.Constants.MAX_SCREENSHOTS) or not is_integer(payload.limits.max_ordinary_views, 1, SAO.Constants.MAX_ORDINARY_VIEWS) or payload.limits.screenshot_retries ~= 1 then return false, "invalid validation limits" end
    local truncation_ok, truncation_reason = SAO.Contract.ValidateClosedKeys(payload.truncation, truncation_fields)
    if not truncation_ok then return false, truncation_reason end
    for _, count in pairs(payload.truncation) do
        if not is_integer(count, 0, 1000000) then return false, "invalid truncation count" end
    end

    for _, test in ipairs(payload.tests) do
        local test_ok, test_reason = SAO.Validation.ValidateTest(test)
        if not test_ok then return false, test_reason end
        if state.ids[test.id] then return false, "duplicate validation test id" end
        state.ids[test.id] = true
        state.tests[#state.tests + 1] = table.Copy(test)
        if test.kind ~= "material_resolve" then state.models[test.path] = true end
        state.screenshots = state.screenshots + #test.views
        if table.Count(state.models) > SAO.Constants.MAX_PLAN_MODELS or state.screenshots > SAO.Constants.MAX_SCREENSHOTS then return false, "validation aggregate cap exceeded" end
    end

    return true
end

local function read_bounded(path, maximum)
    local size = file.Size(path, "DATA")
    if not isnumber(size) or size <= 0 or size > maximum then return nil, "missing or oversized validation file" end
    local raw = file.Read(path, "DATA")
    if not isstring(raw) or #raw ~= size then return nil, "validation file changed while reading" end

    return raw
end

function SAO.Validation.ReadPlan(run_id)
    if not SAO.Contract.IsRunId(run_id) then return nil, "invalid validation run id" end
    local root = SAO.Constants.DATA_ROOT .. "/" .. SAO.Constants.DataFolders.validation_inbox .. "/" .. run_id
    local index_raw, index_read_reason = read_bounded(root .. "/run-index.json", SAO.Constants.MAX_INDEX_BYTES)
    if not index_raw then return nil, index_read_reason end
    local completion_raw, completion_read_reason = read_bounded(root .. "/complete.ready.json", SAO.Constants.MAX_COMPLETION_BYTES)
    if not completion_raw then return nil, completion_read_reason end
    local index, index_decode_reason = SAO.Contract.DecodeJSON(index_raw, SAO.Constants.MAX_INDEX_BYTES)
    if not index then return nil, index_decode_reason end
    local completion, completion_decode_reason = SAO.Contract.DecodeJSON(completion_raw, SAO.Constants.MAX_COMPLETION_BYTES)
    if not completion then return nil, completion_decode_reason end
    local index_ok, index_reason = SAO.Contract.ValidateIndex(index)
    if not index_ok then return nil, index_reason end
    if index.payload_schema ~= "sao-validation-plan-v1" or index.run_id ~= run_id then return nil, "validation plan index identity mismatch" end
    local completion_ok, completion_reason = SAO.Contract.ValidateCompletion(completion, run_id)
    if not completion_ok then return nil, completion_reason end
    local index_sha256 = SAO.Contract.Sha256(index_raw)
    if completion.index_sha256 ~= index_sha256 then return nil, "validation plan completion hash mismatch" end
    if not bounded_array(index.chunks, SAO.Constants.MAX_VALIDATION_TESTS) or #index.chunks == 0 then return nil, "invalid validation chunk declarations" end

    local state = {ids = {}, models = {}, screenshots = 0, tests = {}}
    local total_bytes = 0
    for sequence, declaration in ipairs(index.chunks) do
        local declaration_ok, declaration_reason = SAO.Contract.ValidateClosedKeys(declaration, chunk_declaration_fields)
        if not declaration_ok then return nil, declaration_reason end
        local filename = string.format("chunk-%04d.json", sequence)
        if declaration.sequence ~= sequence or declaration.file ~= filename or not is_integer(declaration.bytes, 1, SAO.Constants.MAX_CHUNK_BYTES) or not isstring(declaration.sha256) or #declaration.sha256 ~= 64 or string.match(declaration.sha256, "^[a-f0-9]+$") == nil then return nil, "unsafe validation chunk declaration" end
        local raw, read_reason = read_bounded(root .. "/" .. filename, SAO.Constants.MAX_CHUNK_BYTES)
        if not raw then return nil, read_reason end
        if #raw ~= declaration.bytes or SAO.Contract.Sha256(raw) ~= declaration.sha256 then return nil, "validation chunk hash mismatch" end
        total_bytes = total_bytes + #raw
        if total_bytes > SAO.Constants.MAX_BUNDLE_BYTES then return nil, "validation plan exceeds bundle cap" end
        local envelope, decode_reason = SAO.Contract.DecodeJSON(raw, SAO.Constants.MAX_CHUNK_BYTES)
        if not envelope then return nil, decode_reason end
        local envelope_ok, envelope_reason = SAO.Contract.ValidateEnvelope(envelope, "sao-validation-plan-v1", run_id, sequence, #index.chunks)
        if not envelope_ok then return nil, envelope_reason end
        local payload_ok, payload_reason = validate_plan_payload(envelope.payload, state)
        if not payload_ok then return nil, payload_reason end
    end
    if total_bytes ~= index.total_bytes then return nil, "validation plan total byte mismatch" end

    table.sort(state.tests, function(left, right)
        if left.priority ~= right.priority then return left.priority > right.priority end

        return left.id < right.id
    end)

    return {
        run_id = run_id,
        plan_sha256 = index_sha256,
        created_utc = index.created_utc,
        tests = state.tests,
        model_count = table.Count(state.models),
        screenshot_count = state.screenshots
    }
end

function SAO.Validation.FindPlanRunIds()
    local pattern = SAO.Constants.DATA_ROOT .. "/" .. SAO.Constants.DataFolders.validation_inbox .. "/*"
    local _, folders = file.Find(pattern, "DATA")
    local result = {}

    for _, folder in ipairs(folders or {}) do
        if SAO.Contract.IsRunId(folder) then result[#result + 1] = folder end
    end

    table.sort(result)

    return result
end
