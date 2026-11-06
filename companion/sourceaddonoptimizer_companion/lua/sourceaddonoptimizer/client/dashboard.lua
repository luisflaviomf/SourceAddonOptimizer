SAO.Dashboard = SAO.Dashboard or {}

local frame = nil
local status_label = nil
local progress_bar = nil
local validation_gallery = nil
local property_sheet = nil
local validation_tab = nil

local status_text = {
    PASS = "✓ PASS",
    WARNING = "⚠ WARNING",
    FAILED = "✕ FAILED",
    INCOMPLETE = "◷ INCOMPLETE",
    RUNNING = "● RUNNING"
}

local status_colors = {
    PASS = Color(75, 190, 125),
    WARNING = Color(235, 180, 65),
    FAILED = Color(225, 90, 90),
    INCOMPLETE = Color(150, 160, 175),
    RUNNING = Color(80, 155, 235)
}

function SAO.Dashboard.CanUseLocalAdmin()
    local ply = LocalPlayer()

    return IsValid(ply) and (game.SinglePlayer() or ply:IsSuperAdmin())
end

local function heading(parent, title, detail)
    local title_label = vgui.Create("DLabel", parent)
    title_label:Dock(TOP)
    title_label:DockMargin(18, 16, 18, 0)
    title_label:SetTall(math.max(28, ScreenScale(18)))
    title_label:SetFont("Trebuchet24")
    title_label:SetTextColor(Color(235, 238, 244))
    title_label:SetText(title)
    local detail_label = vgui.Create("DLabel", parent)
    detail_label:Dock(TOP)
    detail_label:DockMargin(18, 4, 18, 12)
    detail_label:SetTall(math.max(38, ScreenScale(24)))
    detail_label:SetWrap(true)
    detail_label:SetTextColor(Color(165, 174, 190))
    detail_label:SetText(detail)
end

local function base_panel(sheet, title, detail)
    local panel = vgui.Create("DPanel", sheet)
    panel:Dock(FILL)
    panel.Paint = function(_, width, height)
        surface.SetDrawColor(22, 25, 31, 255)
        surface.DrawRect(0, 0, width, height)
    end
    heading(panel, title, detail)

    return panel
end

local function make_overview(sheet)
    local panel = base_panel(sheet, "Optimization Companion", "Optional in-game evidence for Maximum v2. The desktop optimizer remains fully usable without this addon.")
    local copy = vgui.Create("DLabel", panel)
    copy:Dock(TOP)
    copy:DockMargin(18, 10, 18, 0)
    copy:SetTall(170)
    copy:SetWrap(true)
    copy:SetTextColor(Color(205, 210, 220))
    copy:SetText("1. Profile real gameplay only when you want runtime evidence.\n\n2. Generate a bounded validation plan in the desktop app.\n\n3. Run structural validation from the server console and visual validation here.\n\n4. Import the local result bundles in the desktop app. No screenshot bytes are sent over game networking.")

    return panel
end


local function make_profile(sheet)
    local panel = base_panel(sheet, "Profile", "Record bounded model/material evidence during normal gameplay. Starting and stopping are always explicit.")
    local row = vgui.Create("DPanel", panel)
    row:Dock(TOP)
    row:DockMargin(18, 6, 18, 0)
    row:SetTall(38)
    row.Paint = nil
    local start = vgui.Create("DButton", row)
    start:Dock(LEFT)
    start:SetWide(170)
    start:SetText("Start Profiling")
    start.DoClick = function() RunConsoleCommand("sao_profile_start") end
    local stop = vgui.Create("DButton", row)
    stop:Dock(LEFT)
    stop:DockMargin(8, 0, 0, 0)
    stop:SetWide(170)
    stop:SetText("Stop and Export")
    stop.DoClick = function() RunConsoleCommand("sao_profile_stop") end

    return panel
end

local function make_validate(sheet)
    local panel = base_panel(sheet, "Validate", "Capture canonical evidence only from a complete, hashed local plan. Keep this window visible until the queue completes.")
    local actions = vgui.Create("DPanel", panel)
    actions:Dock(TOP)
    actions:DockMargin(18, 4, 18, 8)
    actions:SetTall(38)
    actions.Paint = nil
    local start = vgui.Create("DButton", actions)
    start:Dock(LEFT)
    start:SetWide(190)
    start:SetText("Start Visual Validation")
    local closeup = vgui.Create("DButton", actions)
    closeup:Dock(LEFT)
    closeup:DockMargin(8, 0, 0, 0)
    closeup:SetWide(170)
    closeup:SetText("Capture One Close-up")
    local cancel = vgui.Create("DButton", actions)
    cancel:Dock(LEFT)
    cancel:DockMargin(8, 0, 0, 0)
    cancel:SetWide(150)
    cancel:SetText("Cancel Validation")
    status_label = vgui.Create("DLabel", panel)
    status_label:Dock(TOP)
    status_label:DockMargin(18, 2, 18, 4)
    status_label:SetTall(24)
    status_label:SetTextColor(status_colors.INCOMPLETE)
    status_label:SetText("◷ INCOMPLETE — no visual run started")
    progress_bar = vgui.Create("DProgress", panel)
    progress_bar:Dock(TOP)
    progress_bar:DockMargin(18, 0, 18, 10)
    progress_bar:SetTall(10)
    local host = vgui.Create("DPanel", panel)
    host:Dock(FILL)
    host:DockMargin(18, 0, 18, 16)
    host.Paint = function(_, width, height)
        surface.SetDrawColor(28, 31, 38, 255)
        surface.DrawRect(0, 0, width, height)
    end
    local gallery = SAO.ModelGallery.Create(host)
    validation_gallery = gallery
    gallery:Center()
    host.PerformLayout = function(self)
        local size = math.Clamp(math.floor(math.min(self:GetWide(), self:GetTall()) * 0.94), 192, 512)
        gallery:SetSize(size, size)
        gallery:Center()
    end
    start.DoClick = function()
        local ok, reason = SAO.AssetValidator.Start(nil, gallery)
        if not ok then notification.AddLegacy("Validation did not start: " .. tostring(reason), NOTIFY_ERROR, 6) end
    end
    closeup.DoClick = function()
        local ok, reason = SAO.AssetValidator.RequestCloseup()
        notification.AddLegacy(ok and "One close-up was queued for the current model." or tostring(reason), ok and NOTIFY_HINT or NOTIFY_ERROR, 4)
    end
    cancel.DoClick = function()
        local ok, reason = SAO.AssetValidator.Cancel("user cancelled")
        if not ok then notification.AddLegacy(tostring(reason), NOTIFY_ERROR, 4) end
    end

    return panel
end

local function make_results(sheet)
    local panel = base_panel(sheet, "Results", "Result JSON stays under data/sourceaddonoptimizer/validation_results. PNG evidence stays under data/sourceaddonoptimizer/screenshots/<run-id>.")
    local label = vgui.Create("DLabel", panel)
    label:Dock(TOP)
    label:DockMargin(18, 8, 18, 0)
    label:SetTall(120)
    label:SetWrap(true)
    label:SetTextColor(Color(205, 210, 220))
    label:SetText("PASS means every planned capture completed. WARNING means a capture or environment check was limited. FAILED means a required validation operation failed. INCOMPLETE means the queue was interrupted. Visual appearance remains reviewable evidence; the tool does not silently claim subjective fidelity.")

    return panel
end

local function make_help(sheet)
    local panel = base_panel(sheet, "Help", "Local: copy the plan to validation_inbox. Remote: transfer it manually to the server/client that will validate it.")
    local label = vgui.Create("DLabel", panel)
    label:Dock(TOP)
    label:DockMargin(18, 8, 18, 0)
    label:SetTall(150)
    label:SetWrap(true)
    label:SetTextColor(Color(205, 210, 220))
    label:SetText("Server console: sao_validate_start [run-id]\nCancel structural work: sao_validate_cancel\n\nClient gallery: sao_companion\n\nKeep original and optimized addons from mounting together during validation. A size mismatch is reported as a possible mounted-provider conflict.")

    return panel
end

function SAO.Dashboard.Open()
    if not SAO.Dashboard.CanUseLocalAdmin() then
        notification.AddLegacy("Source Addon Optimizer Companion requires local admin authority.", NOTIFY_ERROR, 6)

        return
    end
    if IsValid(frame) then frame:MakePopup() return end
    frame = vgui.Create("DFrame")
    frame:SetTitle("Source Addon Optimizer Companion")
    frame:SetSize(math.Clamp(math.floor(ScrW() * 0.82), 760, 1180), math.Clamp(math.floor(ScrH() * 0.82), 560, 860))
    frame:Center()
    frame:SetSizable(true)
    frame:SetMinWidth(720)
    frame:SetMinHeight(520)
    frame:MakePopup()
    local sheet = vgui.Create("DPropertySheet", frame)
    property_sheet = sheet
    sheet:Dock(FILL)
    sheet:AddSheet("Overview", make_overview(sheet), "icon16/house.png")
    sheet:AddSheet("Profile", make_profile(sheet), "icon16/chart_curve.png")
    validation_tab = sheet:AddSheet("Validate", make_validate(sheet), "icon16/camera.png").Tab
    sheet:AddSheet("Results", make_results(sheet), "icon16/report.png")
    sheet:AddSheet("Help", make_help(sheet), "icon16/help.png")
end

function SAO.Dashboard.StartVisual(run_id)
    local timer_name = "SAO.StartVisualValidation"
    timer.Remove(timer_name)
    timer.Create(timer_name, 0.5, 20, function()
        if not SAO.Dashboard.CanUseLocalAdmin() then return end
        SAO.Dashboard.Open()
        if not IsValid(validation_gallery) then return end
        if IsValid(property_sheet) and IsValid(validation_tab) then property_sheet:SetActiveTab(validation_tab) end
        local ok, reason = SAO.AssetValidator.Start(run_id, validation_gallery)
        timer.Remove(timer_name)
        if ok then
            MsgN("[SAO] Visual validation started: " .. tostring(reason))
        else
            MsgN("[SAO] Visual validation did not start: " .. tostring(reason))
            notification.AddLegacy("Validation did not start: " .. tostring(reason), NOTIFY_ERROR, 6)
        end
    end)
end

hook.Add("SAO.VisualValidationStatus", "SAO.DashboardVisualStatus", function(status, detail, completed, total)
    if IsValid(status_label) then
        status_label:SetText((status_text[status] or status) .. " — " .. tostring(detail))
        status_label:SetTextColor(status_colors[status] or Color(205, 210, 220))
    end
    if IsValid(progress_bar) then progress_bar:SetFraction(total > 0 and math.Clamp(completed / total, 0, 1) or 0) end
end)

concommand.Add("sao_companion", function()
    SAO.Dashboard.Open()
end)

concommand.Add("sao_validate_visual_start", function(_, _, arguments)
    SAO.Dashboard.StartVisual(arguments and arguments[1] or nil)
end)

hook.Add("PopulateToolMenu", "SAO.CompanionUtilitiesMenu", function()
    spawnmenu.AddToolMenuOption("Utilities", "Source Addon Optimizer", "SAO.Companion", "Companion", "", "", function(panel)
        panel:Button("Open Optimization Companion", "sao_companion")
        panel:Help("Optional runtime profiling and bounded post-optimization validation.")
    end)
end)
