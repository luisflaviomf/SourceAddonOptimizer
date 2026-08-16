SAO.Storage = SAO.Storage or {}
local INDEX_FILE = "run-index.json"
local COMPLETE_FILE = "complete.ready.json"
local allowed_schemas_by_kind = {
    profiles = { ["sao-runtime-profile-v1"] = true },
    validation_inbox = { ["sao-validation-plan-v1"] = true },
    validation_results = {
        ["sao-structural-result-v1"] = true,
        ["sao-visual-result-v1"] = true
    }
}

local function ensure_base_folders()
    file.CreateDir(SAO.Constants.DATA_ROOT)

    for _, folder in pairs(SAO.Constants.DataFolders) do
        file.CreateDir(SAO.Constants.DATA_ROOT .. "/" .. folder)
    end
end

local function run_root(kind, run_id)
    local folder = SAO.Constants.DataFolders[kind]
    if not folder or not SAO.Contract.IsRunId(run_id) then return nil end

    return SAO.Constants.DATA_ROOT .. "/" .. folder .. "/" .. run_id
end

local function write_atomic(path, raw)
    local partial = path .. ".partial.json"
    local handle = file.Open(partial, "wb", "DATA")
    if not handle then return false end
    handle:Write(raw)
    handle:Flush()
    handle:Close()
    if file.Size(partial, "DATA") ~= #raw then return false end
    if not file.Rename(partial, path) then return false end

    return file.Exists(path, "DATA")
end

function SAO.Storage.WriteBundle(kind, schema, run_id, created_utc, producer, environment, payloads)
    local root = run_root(kind, run_id)
    local allowed_schemas = allowed_schemas_by_kind[kind]
    if not root or not allowed_schemas or not allowed_schemas[schema] or not SAO.Constants.Schemas[schema] or not istable(payloads) or #payloads == 0 then return false, "invalid bundle request" end
    if file.IsDir(root, "DATA") or file.Exists(root .. "/" .. INDEX_FILE, "DATA") or file.Exists(root .. "/" .. COMPLETE_FILE, "DATA") then return false, "run id already exists" end
    ensure_base_folders()
    file.CreateDir(root)
    local chunks = {}
    local total_bytes = 0

    for sequence, payload in ipairs(payloads) do
        local envelope = {
            schema = schema,
            contract = {
                major = SAO.Constants.CONTRACT_MAJOR,
                minor = SAO.Constants.CONTRACT_MINOR
            },
            run_id = run_id,
            created_utc = created_utc,
            producer = producer,
            environment = environment,
            chunk = {
                sequence = sequence,
                total = #payloads
            },
            payload = payload
        }

        local valid, reason = SAO.Contract.ValidateEnvelope(envelope, schema, run_id, sequence, #payloads)
        if not valid then return false, reason end
        local raw, encode_reason = SAO.Contract.EncodeJSON(envelope)
        if not raw then return false, encode_reason end
        if #raw > SAO.Constants.MAX_CHUNK_BYTES then return false, "chunk exceeds byte limit" end
        total_bytes = total_bytes + #raw
        if total_bytes > SAO.Constants.MAX_BUNDLE_BYTES then return false, "bundle exceeds byte limit" end
        local filename = string.format("chunk-%04d.json", sequence)
        if not write_atomic(root .. "/" .. filename, raw) then return false, "chunk write failed" end

        chunks[#chunks + 1] = {
            sequence = sequence,
            file = filename,
            bytes = #raw,
            sha256 = SAO.Contract.Sha256(raw)
        }
    end

    local index = {
        schema = "sao-run-index-v1",
        contract = {
            major = SAO.Constants.CONTRACT_MAJOR,
            minor = SAO.Constants.CONTRACT_MINOR
        },
        run_id = run_id,
        payload_schema = schema,
        created_utc = created_utc,
        producer = producer,
        environment = environment,
        chunk_count = #chunks,
        total_bytes = total_bytes,
        chunks = chunks
    }

    local index_valid, index_reason = SAO.Contract.ValidateIndex(index)
    if not index_valid then return false, index_reason end
    local index_raw, index_encode_reason = SAO.Contract.EncodeJSON(index)
    if not index_raw or #index_raw > SAO.Constants.MAX_INDEX_BYTES then return false, index_encode_reason or "run index exceeds byte limit" end
    if not write_atomic(root .. "/" .. "run-index.json", index_raw) then return false, "run index write failed" end

    local completion = {
        schema = "sao-completion-v1",
        run_id = run_id,
        index_sha256 = SAO.Contract.Sha256(index_raw)
    }

    local completion_valid, completion_reason = SAO.Contract.ValidateCompletion(completion, run_id)
    if not completion_valid then return false, completion_reason end
    local completion_raw, completion_encode_reason = SAO.Contract.EncodeJSON(completion)
    if not completion_raw or #completion_raw > SAO.Constants.MAX_COMPLETION_BYTES then return false, completion_encode_reason or "completion marker exceeds byte limit" end
    if not write_atomic(root .. "/" .. COMPLETE_FILE, completion_raw) then return false, "completion marker write failed" end

    return true, root
end

ensure_base_folders()
