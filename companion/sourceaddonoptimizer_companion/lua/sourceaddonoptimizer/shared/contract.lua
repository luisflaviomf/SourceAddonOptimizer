SAO.Contract = SAO.Contract or {}

local allowed_extensions = {
    models = {
        ani = true,
        mdl = true,
        phy = true,
        vtx = true,
        vvd = true
    },
    materials = {
        vmt = true,
        vtf = true
    },
    sound = {
        mp3 = true,
        ogg = true,
        wav = true
    },
    lua = {
        lua = true
    },
    maps = {
        bsp = true
    }
}

local envelope_fields = {
    schema = true,
    contract = true,
    run_id = true,
    created_utc = true,
    producer = true,
    environment = true,
    chunk = true,
    payload = true
}

local index_fields = {
    schema = true,
    contract = true,
    run_id = true,
    payload_schema = true,
    created_utc = true,
    producer = true,
    environment = true,
    chunk_count = true,
    total_bytes = true,
    chunks = true
}

function SAO.Contract.ValidateClosedKeys(value, allowed)
    if not istable(value) then return false, "expected table" end

    for key in pairs(value) do
        if not allowed[key] then return false, "unknown field: " .. tostring(key) end
    end

    for key in pairs(allowed) do
        if value[key] == nil then return false, "missing field: " .. tostring(key) end
    end

    return true
end

local function validate_shape(value, depth, state)
    if depth > SAO.Constants.MAX_JSON_DEPTH then return false, "maximum JSON depth exceeded" end

    if not istable(value) then
        if isnumber(value) and (value ~= value or value == math.huge or value == -math.huge) then return false, "JSON number is not finite" end

        return true
    end

    for key, item in pairs(value) do
        if not isstring(key) and not isnumber(key) then return false, "invalid JSON key" end
        state.keys = state.keys + 1
        if state.keys > SAO.Constants.MAX_TOTAL_KEYS then return false, "maximum JSON key count exceeded" end
        local ok, reason = validate_shape(item, depth + 1, state)
        if not ok then return false, reason end
    end

    return true
end

function SAO.Contract.DecodeJSON(raw, maximum_bytes)
    if not isstring(raw) or #raw > maximum_bytes then return nil, "JSON input exceeds its byte limit" end
    local value = util.JSONToTable(raw)
    if not istable(value) then return nil, "invalid JSON object" end

    local ok, reason = validate_shape(value, 1, {
        keys = 0
    })

    if not ok then return nil, reason end

    return value
end

function SAO.Contract.EncodeJSON(value)
    local ok, reason = validate_shape(value, 1, {
        keys = 0
    })

    if not ok then return nil, reason end
    local raw = util.TableToJSON(value, false)
    if not isstring(raw) then return nil, "failed to encode JSON" end

    return raw
end

function SAO.Contract.IsRunId(value)
    if not isstring(value) or #value ~= 36 or value ~= string.lower(value) then return false end
    if string.sub(value, 9, 9) ~= "-" or string.sub(value, 14, 14) ~= "-" or string.sub(value, 19, 19) ~= "-" or string.sub(value, 24, 24) ~= "-" then return false end

    return string.match(string.Replace(value, "-", ""), "^[0-9a-f]+$") ~= nil
end

function SAO.Contract.NormalizeVirtualPath(value)
    if not isstring(value) or #value == 0 or #value > SAO.Constants.MAX_PATH_BYTES then return nil, "invalid virtual path" end
    if value ~= string.Trim(value) or string.find(value, "\0", 1, true) then return nil, "invalid virtual path" end

    for index = 1, #value do
        local byte = string.byte(value, index)
        if byte < 32 or byte > 126 then return nil, "virtual path must be ASCII" end
    end

    local normalized = string.lower(string.Replace(value, "\\", "/"))
    if string.StartWith(normalized, "/") or string.find(normalized, "//", 1, true) or string.find(normalized, ":", 1, true) then return nil, "invalid virtual path" end
    local parts = string.Explode("/", normalized, false)
    if #parts < 2 or not allowed_extensions[parts[1]] then return nil, "virtual path root is not allowed" end

    for _, part in ipairs(parts) do
        if part == "" or part == "." or part == ".." or #part > SAO.Constants.MAX_PATH_COMPONENT_BYTES then return nil, "invalid virtual path component" end
    end

    local extension = string.GetExtensionFromFilename(normalized)
    if not extension or not allowed_extensions[parts[1]][string.lower(extension)] then return nil, "virtual path extension is not allowed" end

    return normalized
end

function SAO.Contract.ValidateEnvelope(value, schema, run_id, sequence, total)
    local ok, reason = SAO.Contract.ValidateClosedKeys(value, envelope_fields)
    if not ok then return false, reason end
    if value.schema ~= schema or not SAO.Constants.Schemas[schema] or value.run_id ~= run_id or not SAO.Contract.IsRunId(run_id) then return false, "envelope identity mismatch" end
    if not istable(value.contract) or value.contract.major ~= SAO.Constants.CONTRACT_MAJOR or value.contract.minor ~= SAO.Constants.CONTRACT_MINOR or table.Count(value.contract) ~= 2 then return false, "unsupported contract" end
    if not isstring(value.created_utc) or not istable(value.producer) or not istable(value.environment) or not istable(value.payload) then return false, "invalid envelope metadata" end
    if not istable(value.chunk) or value.chunk.sequence ~= sequence or value.chunk.total ~= total or table.Count(value.chunk) ~= 2 then return false, "invalid chunk metadata" end

    return validate_shape(value, 1, {
        keys = 0
    })
end

function SAO.Contract.ValidateIndex(value)
    local ok, reason = SAO.Contract.ValidateClosedKeys(value, index_fields)
    if not ok then return false, reason end
    if value.schema ~= "sao-run-index-v1" or not SAO.Contract.IsRunId(value.run_id) or not SAO.Constants.Schemas[value.payload_schema] then return false, "invalid run index identity" end
    if not istable(value.chunks) or value.chunk_count ~= #value.chunks or not isnumber(value.total_bytes) or value.total_bytes < 0 or value.total_bytes > SAO.Constants.MAX_BUNDLE_BYTES then return false, "invalid run index limits" end

    return true
end

function SAO.Contract.ValidateCompletion(value, run_id)
    local ok, reason = SAO.Contract.ValidateClosedKeys(value, {
        schema = true,
        run_id = true,
        index_sha256 = true
    })

    if not ok then return false, reason end

    return value.schema == "sao-completion-v1" and value.run_id == run_id and isstring(value.index_sha256) and #value.index_sha256 == 64
end

function SAO.Contract.Sha256(raw)
    if not isstring(raw) then return nil end

    return string.lower(util.SHA256(raw))
end
