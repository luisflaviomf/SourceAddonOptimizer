SAO.ErrorCollector = SAO.ErrorCollector or {}

local function sanitize_text(value, maximum_bytes)
    if not isstring(value) then return "" end
    value = string.gsub(value, "[%c]", " ")
    value = string.gsub(value, "%a:[/\\][^%s]+", "<absolute-path>")
    value = string.gsub(value, "\\\\[^%s]+", "<absolute-path>")
    if string.StartWith(value, "/") then value = "<absolute-path>" end
    value = string.gsub(value, "%s/[^%s]+", " <absolute-path>")
    value = string.gsub(value, "%d+%.%d+%.%d+%.%d+", "<address>")
    value = string.gsub(value, "%d%d%d%d%d%d%d%d%d%d%d%d%d%d%d%d%d", "<identifier>")
    value = string.Trim(value)

    return string.sub(value, 1, maximum_bytes)
end

local function sanitized_source(stack)
    if not istable(stack) then return "" end

    for _, frame in ipairs(stack) do
        if istable(frame) then
            local source = frame.File or frame.file or frame.Source or frame.source
            source = sanitize_text(source, 512)

            if source ~= "" then return source end
        end
    end

    return ""
end

local function record_error(message, realm, stack)
    realm = realm == "client" and "client" or realm == "menu" and "menu" or "server"
    message = sanitize_text(message, 1024)
    local source = sanitized_source(stack)
    local identifier = string.lower(util.SHA256(realm .. "\0" .. message .. "\0" .. source))
    SAO.ServerProfiler.RecordLuaError({
        id = identifier,
        realm = realm,
        message = message,
        source = source,
        count = math.min(1000000, 1)
    })
end

hook.Add("OnLuaError", "SAO.CollectServerLuaError", function(message, realm, stack)
    record_error(message, realm, stack)
end)

hook.Add("OnClientLuaError", "SAO.CollectClientLuaError", function(message, _, stack)
    record_error(message, "client", stack)
end)
