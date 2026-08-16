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

function SAO.Validation.IsKnownTestKind(kind)
    return isstring(kind) and SAO.Validation.TestKinds[kind] == true
end

function SAO.Validation.ValidateTest(value)
    local ok, reason = SAO.Contract.ValidateClosedKeys(value, test_fields)
    if not ok then return false, reason end
    if not SAO.Validation.IsKnownTestKind(value.kind) then return false, "unknown validation test kind" end
    local normalized, path_reason = SAO.Contract.NormalizeVirtualPath(value.path)
    if not normalized then return false, path_reason end
    if not istable(value.required_files) or not istable(value.skins) or not istable(value.bodygroups) or not istable(value.materials) or not istable(value.views) then return false, "invalid validation test arrays" end

    for _, path in ipairs(value.required_files) do
        if not SAO.Contract.NormalizeVirtualPath(path) then return false, "invalid required file path" end
    end

    for _, path in ipairs(value.materials) do
        if not SAO.Contract.NormalizeVirtualPath(path) then return false, "invalid material path" end
    end

    return true
end
