SAO.Permissions = SAO.Permissions or {}
local rate_limits = {}
local sessions = {}
SAO.Permissions.SecurityEvents = SAO.Permissions.SecurityEvents or {}

local function session_key(ply)
    if ply == nil or ply == NULL then return "server-console" end

    return ply
end

local function record_rejection(action, detail)
    local safe_action = isstring(action) and string.sub(action, 1, SAO.Constants.MAX_NET_DETAIL_BYTES) or "unknown"
    local safe_detail = isstring(detail) and string.sub(detail, 1, SAO.Constants.MAX_NET_DETAIL_BYTES) or "rejected"
    local events = SAO.Permissions.SecurityEvents
    events[#events + 1] = {
        action = safe_action,
        detail = safe_detail,
        recorded_at = SysTime()
    }

    while #events > 64 do
        table.remove(events, 1)
    end
end

local function new_session_id(ply)
    local seed = tostring(SysTime()) .. ":" .. tostring(math.random()) .. ":" .. tostring(ply)

    return string.sub(util.SHA256(seed), 1, SAO.Constants.MAX_NET_IDENTIFIER_BYTES)
end

function SAO.Permissions.CanUse(ply)
    if ply == nil or ply == NULL then return true end
    if not IsValid(ply) or not ply:IsPlayer() then return false end
    if game.SinglePlayer() and ply:IsListenServerHost() then return true end

    return ply:IsSuperAdmin()
end

function SAO.Permissions.CheckRateLimit(ply, action, interval)
    if not SAO.Permissions.CanUse(ply) or not isstring(action) or not isnumber(interval) or interval <= 0 then return false end
    if ply == nil or ply == NULL then return true end
    local identity = ply:SteamID64()
    local key = identity .. ":" .. action
    local now = SysTime()
    local allowed_at = rate_limits[key] or 0
    if now < allowed_at then return false end
    rate_limits[key] = now + interval

    return true
end

function SAO.Permissions.RecordSecurityEvent(action, detail)
    record_rejection(action, detail)
end

function SAO.Permissions.OpenSession(ply)
    if not SAO.Permissions.CanUse(ply) then
        record_rejection("open_session", "permission denied")

        return nil, "permission denied"
    end

    local identifier = new_session_id(ply)
    sessions[session_key(ply)] = {
        identifier = identifier,
        expires_at = SysTime() + SAO.Constants.SESSION_LIFETIME_SECONDS
    }

    return identifier
end

function SAO.Permissions.ValidateSession(ply, identifier)
    if not SAO.Permissions.CanUse(ply) then
        sessions[session_key(ply)] = nil
        record_rejection("validate_session", "permission denied")

        return false, "permission denied"
    end

    if not isstring(identifier) or #identifier == 0 or #identifier > SAO.Constants.MAX_NET_IDENTIFIER_BYTES then
        record_rejection("validate_session", "invalid identifier")

        return false, "invalid identifier"
    end

    local key = session_key(ply)
    local session = sessions[key]
    if not session or session.identifier ~= identifier then
        record_rejection("validate_session", "unknown identifier")

        return false, "unknown identifier"
    end

    if SysTime() > session.expires_at then
        sessions[key] = nil
        record_rejection("validate_session", "expired")

        return false, "expired"
    end

    return true
end

function SAO.Permissions.CloseSession(ply, identifier)
    local valid, reason = SAO.Permissions.ValidateSession(ply, identifier)
    if not valid then return false, reason end
    sessions[session_key(ply)] = nil

    return true
end

hook.Add("PlayerDisconnected", "SAO.ClearPermissionRateLimits", function(ply)
    if not IsValid(ply) then return end
    sessions[session_key(ply)] = nil
    local prefix = ply:SteamID64() .. ":"

    for key in pairs(rate_limits) do
        if string.StartWith(key, prefix) then
            rate_limits[key] = nil
        end
    end
end)
