-- GMod Addon Optimizer: bullet marks on optimized models, server side.
-- The models listed above are drawn by the clients from compressed streams (GMOS), and the game's own bullet
-- holes do not show on them. This file tells the clients where each bullet stopped on one of those models
-- (one small unreliable message to the players who can see the spot); the clients draw the mark themselves.
-- Console: gmo_stream_marks 0 turns it off.
if not SERVER then return end

local REVISION = 1
GMOS_SV = GMOS_SV or { models = {} }
for _, mdl in ipairs(GMOS_SV_PENDING or {}) do GMOS_SV.models[string.lower(mdl)] = true end
GMOS_SV_PENDING = nil
if (GMOS_SV.REVISION or 0) >= REVISION then return end      -- another optimized addon already loaded this
GMOS_SV.REVISION = REVISION

util.AddNetworkString("gmo_stream_decal")
local enabled = CreateConVar("gmo_stream_marks", "1", FCVAR_ARCHIVE,
    "Tell the clients where bullets hit optimized models, for the bullet marks")
local models = GMOS_SV.models
local PER_ENTITY, PER_SECOND = 12, 60       -- messages a second: per model hit, and in all
local second, total, per = -1, 0, {}

hook.Add("PostEntityFireBullets", "gmo_stream_marks", function(_, data)
    local tr = data and data.Trace
    local ent = tr and tr.Entity
    if not ent or not ent.IsValid or not ent:IsValid() or not enabled:GetBool() then return end
    local mdl = ent:GetModel()
    if not mdl or not models[string.lower((string.gsub(mdl, "\\", "/")))] then return end
    local now = math.floor(CurTime())
    if now ~= second then second, total, per = now, 0, {} end
    local n = per[ent] or 0
    if total >= PER_SECOND or n >= PER_ENTITY then return end
    total, per[ent] = total + 1, n + 1
    local dir = tr.HitPos - tr.StartPos
    if dir:LengthSqr() == 0 then return end
    dir:Normalize()
    net.Start("gmo_stream_decal", true)
    net.WriteEntity(ent)
    net.WriteVector(tr.HitPos)
    net.WriteNormal(dir)
    net.WriteUInt(tr.MatType or 0, 8)
    net.SendPVS(tr.HitPos)
end)
