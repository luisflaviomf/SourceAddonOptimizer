SAO.ModelGallery = SAO.ModelGallery or {}

local view_directions = {
    front = Vector(1, 0, 0.08),
    side = Vector(0, 1, 0.08),
    rear = Vector(-1, 0, 0.08),
    isometric = Vector(1, 1, 0.72),
    closeup = Vector(1, 1, 0.35)
}

local function gallery_size()
    local minimum = math.max(192, ScreenScale(128))

    return math.Clamp(math.floor(math.min(ScrW(), ScrH()) * 0.55), minimum, 512)
end

local function apply_declared_state(entity, test)
    local skin = test.skins[1] or 0
    entity:SetSkin(skin)
    local bodygroups = {}
    local applied_names = {}

    for _, state in ipairs(test.bodygroups) do
        local name = string.lower(state.name)
        if not applied_names[name] then
            local identifier = entity:FindBodygroupByName(state.name)
            if isnumber(identifier) and identifier >= 0 then
                entity:SetBodygroup(identifier, state.value)
                bodygroups[#bodygroups + 1] = {name = state.name, value = state.value}
            end
            applied_names[name] = true
        end
    end

    return {skin = skin, bodygroups = bodygroups}
end

function SAO.ModelGallery.Create(parent)
    local panel = vgui.Create("DModelPanel", parent)
    local size = gallery_size()
    panel:SetSize(size, size)
    panel:SetFOV(35)
    panel:SetAmbientLight(Color(82, 86, 94))
    panel:SetDirectionalLight(BOX_FRONT, Color(245, 245, 245))
    panel:SetDirectionalLight(BOX_RIGHT, Color(125, 140, 165))
    panel:SetDirectionalLight(BOX_TOP, Color(180, 185, 195))
    local model_paint = panel.Paint
    panel.Paint = function(self, width, height)
        surface.SetDrawColor(28, 31, 38, 255)
        surface.DrawRect(0, 0, width, height)
        if IsValid(self.Entity) then return model_paint(self, width, height) end

        return true
    end
    panel.LayoutEntity = function() end

    function panel:PostDrawModel()
        self.SAOPaintedFrame = FrameNumber()
    end

    return panel
end

function SAO.ModelGallery.Configure(panel, test, view)
    if not IsValid(panel) or not istable(test) or not view_directions[view] then return nil, "invalid gallery request" end
    panel:SetModel(test.path)
    local entity = panel.Entity
    if not IsValid(entity) then return nil, "DModelPanel did not create the model" end
    local applied = apply_declared_state(entity, test)
    local mins, maxs = entity:GetRenderBounds()
    if not isvector(mins) or not isvector(maxs) then return nil, "model render bounds are unavailable" end
    local center = (mins + maxs) * 0.5
    local radius = math.max((maxs - mins):Length() * 0.5, 1)
    local fov = 35
    local distance = radius / math.tan(math.rad(fov * 0.5)) * 1.18
    if view == "closeup" then distance = distance * 0.65 end
    local direction = view_directions[view]:GetNormalized()
    panel:SetFOV(fov)
    panel:SetLookAt(center)
    panel:SetCamPos(center + direction * distance)
    panel.SAOPaintedFrame = -1

    return {
        camera = {
            fov = fov,
            distance = distance,
            crop_width = panel:GetWide(),
            crop_height = panel:GetTall()
        },
        state = applied
    }
end
