SAO.AssetValidator = SAO.AssetValidator or {}

local state = {active = false}
local progress_fields = {schema = true, run_id = true, plan_sha256 = true, cursor = true, completed_models = true, resources = true, screenshots = true, save_serial = true}

local function bounded_integer(value, minimum, maximum)
    return isnumber(value) and value == math.floor(value) and value >= minimum and value <= maximum
end

local function can_use_local_admin()
    local ply = LocalPlayer()

    return IsValid(ply) and (game.SinglePlayer() or ply:IsSuperAdmin())
end

local function notify(status, detail)
    hook.Run("SAO.VisualValidationStatus", status, detail or "", state.active and state.completed_models or 0, state.active and #state.tests or 0)
end

local function progress_root(run_id)
    return SAO.Constants.DATA_ROOT .. "/" .. SAO.Constants.DataFolders.validation_progress .. "/" .. run_id
end

local function latest_progress(run_id, plan_sha256)
    local root = progress_root(run_id)
    local files = file.Find(root .. "/visual-progress-*.json", "DATA")
    table.sort(files or {}, function(left, right) return left > right end)

    for _, filename in ipairs(files or {}) do
        if string.match(filename, "^visual%-progress%-%d%d%d%d%.json$") then
            local raw = file.Read(root .. "/" .. filename, "DATA")
            local saved = raw and SAO.Contract.DecodeJSON(raw, SAO.Constants.MAX_BUNDLE_BYTES) or nil
            local closed = saved and SAO.Contract.ValidateClosedKeys(saved, progress_fields) or false
            if closed and saved.schema == "sao-visual-progress-v1" and saved.run_id == run_id and saved.plan_sha256 == plan_sha256 and bounded_integer(saved.cursor, 1, SAO.Constants.MAX_PLAN_MODELS + 1) and bounded_integer(saved.completed_models, 0, SAO.Constants.MAX_PLAN_MODELS) and bounded_integer(saved.save_serial, 0, SAO.Constants.MAX_VALIDATION_PROGRESS_FILES) and istable(saved.resources) and #saved.resources <= SAO.Constants.MAX_PLAN_MODELS and table.Count(saved.resources) == #saved.resources and istable(saved.screenshots) and #saved.screenshots <= SAO.Constants.MAX_SCREENSHOTS and table.Count(saved.screenshots) == #saved.screenshots then return saved end
        end
    end

    return nil
end

local function save_progress(reason)
    if not state.active then return false end
    state.save_serial = state.save_serial + 1
    if state.save_serial > SAO.Constants.MAX_VALIDATION_PROGRESS_FILES then
        state.truncation.models = math.min(1000000, state.truncation.models + 1)

        return false
    end
    local root = progress_root(state.run_id)
    file.CreateDir(root)
    local raw = SAO.Contract.EncodeJSON({
        schema = "sao-visual-progress-v1",
        run_id = state.run_id,
        plan_sha256 = state.plan_sha256,
        cursor = state.cursor,
        completed_models = state.completed_models,
        resources = state.resources,
        screenshots = state.screenshots,
        save_serial = state.save_serial
    })
    if not raw or #raw > SAO.Constants.MAX_BUNDLE_BYTES then return false end
    local path = root .. "/" .. string.format("visual-progress-%04d.json", state.save_serial)
    local partial = path .. ".partial.json"
    local handle = file.Open(partial, "wb", "DATA")
    if not handle then return false end
    handle:Write(raw)
    handle:Flush()
    handle:Close()
    if file.Size(partial, "DATA") ~= #raw or not file.Rename(partial, path) then return false end
    state.last_progress_reason = string.sub(tostring(reason or "progress"), 1, 128)

    return true
end

local function resource_for(test, status, reason, warnings)
    return {
        id = test.id,
        test_id = test.id,
        path = test.path,
        status = status,
        reason = string.sub(tostring(reason), 1, 1024),
        warnings = warnings
    }
end

local function finalize(interrupted)
    local completed = state.completed_models
    local total = #state.tests
    local ok, result = SAO.ClientResultWriter.WriteVisual(state, interrupted)
    state.active = false
    state.capture_pending = false
    hook.Run("SAO.VisualValidationStatus", ok and (interrupted and "INCOMPLETE" or "PASS") or "FAILED", tostring(result), completed, total)

    return ok, result
end

local begin_next_model

local function finish_current_model()
    local test = state.current_test
    if not test then return end
    local status = #state.current_warnings > 0 and "WARNING" or "PASS"
    local reason = status == "PASS" and "all declared canonical evidence was captured" or "capture completed with bounded warnings"
    state.resources[#state.resources + 1] = resource_for(test, status, reason, table.Copy(state.current_warnings))
    state.completed_models = state.completed_models + 1
    state.cursor = state.cursor + 1
    state.current_test = nil
    state.current_views = nil
    state.capture_pending = false
    save_progress("model completed")
    notify(status, reason)
    timer.Simple(0, begin_next_model)
end

local function capture_next_view()
    if not state.active or not state.current_test or state.capture_pending then return end
    local view = state.current_views[state.view_cursor]
    if not view then
        finish_current_model()

        return
    end
    if #state.screenshots >= SAO.Constants.MAX_SCREENSHOTS then
        state.truncation.screenshots = state.truncation.screenshots + 1
        state.current_warnings[#state.current_warnings + 1] = "screenshot cap reached"
        state.view_cursor = state.view_cursor + 1
        capture_next_view()

        return
    end
    local metadata, configure_reason = SAO.ModelGallery.Configure(state.gallery, state.current_test, view)
    if not metadata then
        state.current_warnings[#state.current_warnings + 1] = string.sub(tostring(configure_reason), 1, 512)
        state.view_cursor = state.view_cursor + 1
        capture_next_view()

        return
    end
    state.capture_pending = true
    local accepted, capture_reason = SAO.ScreenshotCapture.Capture(state.gallery, state.run_id, state.current_test, view, metadata, function(success, screenshot, reason)
        if not state.active or not state.current_test then return end
        state.capture_pending = false
        if istable(screenshot) then state.screenshots[#state.screenshots + 1] = screenshot end
        if not success then state.current_warnings[#state.current_warnings + 1] = string.sub(tostring(reason), 1, 512) end
        state.view_cursor = state.view_cursor + 1
        timer.Simple(0, capture_next_view)
    end)
    if not accepted then
        state.capture_pending = false
        state.current_warnings[#state.current_warnings + 1] = string.sub(tostring(capture_reason), 1, 512)
        state.view_cursor = state.view_cursor + 1
        timer.Simple(0, capture_next_view)
    end
end

    begin_next_model = function()
    if not state.active or state.current_test then return end
    local test = state.tests[state.cursor]
    if not test then
        save_progress("completed")
        finalize(false)

        return
    end
    state.current_test = test
    state.current_views = table.Copy(test.views)
    if state.closeup_requests[test.id] and not state.closeups[test.id] and #state.current_views < SAO.Constants.MAX_ORDINARY_VIEWS + 1 and #state.screenshots < SAO.Constants.MAX_SCREENSHOTS then
        state.current_views[#state.current_views + 1] = "closeup"
        state.closeups[test.id] = true
    end
    state.current_warnings = {}
    state.view_cursor = 1
    state.capture_pending = false
    notify("RUNNING", test.path)
    timer.Simple(0.05, capture_next_view)
end

function SAO.AssetValidator.IsActive()
    return state.active
end

function SAO.AssetValidator.Start(run_id, gallery)
    if not can_use_local_admin() then return false, "local administrator permission is required" end
    if state.active then return false, "visual validation is already active" end
    if not IsValid(gallery) then return false, "the controlled gallery is not open" end
    if not isstring(run_id) or run_id == "" then
        local plans = SAO.Validation.FindPlanRunIds()
        run_id = plans[1]
    end
    if not run_id then return false, "no validation plan is available" end
    local plan, reason = SAO.Validation.ReadPlan(run_id)
    if not plan then return false, reason end
    local tests = {}
    local models = {}
    local screenshots = 0
    for _, test in ipairs(plan.tests) do
        if test.kind == "model_render" and not models[test.path] then
            models[test.path] = true
            tests[#tests + 1] = test
            screenshots = screenshots + #test.views
        end
    end
    if #tests == 0 then return false, "validation plan has no visual tests" end
    if #tests > SAO.Constants.MAX_PLAN_MODELS or screenshots > SAO.Constants.MAX_SCREENSHOTS then return false, "visual queue exceeds its hard caps" end
    local saved = latest_progress(plan.run_id, plan.plan_sha256)
    if saved and saved.cursor > #tests + 1 then saved = nil end
    state = {
        active = true,
        run_id = plan.run_id,
        plan_sha256 = plan.plan_sha256,
        tests = tests,
        cursor = saved and saved.cursor or 1,
        completed_models = saved and saved.completed_models or 0,
        resources = saved and saved.resources or {},
        screenshots = saved and saved.screenshots or {},
        save_serial = saved and saved.save_serial or 0,
        gallery = gallery,
        started_at = SysTime(),
        truncation = {models = 0, screenshots = 0},
        closeup_requests = {},
        closeups = {},
        capture_pending = false
    }
    notify("RUNNING", "visual queue validated")
    timer.Simple(0, begin_next_model)

    return true, plan.run_id
end

function SAO.AssetValidator.RequestCloseup()
    if not state.active or not state.current_test then return false, "no current model" end
    local test_id = state.current_test.id
    if state.closeups[test_id] or state.closeup_requests[test_id] then return false, "close-up already requested" end
    if #state.screenshots >= SAO.Constants.MAX_SCREENSHOTS then return false, "screenshot cap reached" end
    state.closeup_requests[test_id] = true
    if state.current_views and not table.HasValue(state.current_views, "closeup") then state.current_views[#state.current_views + 1] = "closeup" end

    return true
end

function SAO.AssetValidator.Cancel(reason)
    if not state.active then return false, "no visual validation is active" end
    local interrupted_test = state.current_test or state.tests[state.cursor]
    state.active = false
    SAO.ScreenshotCapture.Cancel()
    state.active = true
    save_progress(reason or "cancelled")
    if interrupted_test and #state.resources < SAO.Constants.MAX_PLAN_MODELS then
        state.resources[#state.resources + 1] = resource_for(interrupted_test, "INCOMPLETE", reason or "visual validation interrupted", {})
    end

    return finalize(true)
end

hook.Add("ShutDown", "SAO.VisualValidationShutdown", function()
    if state.active then SAO.AssetValidator.Cancel("shutdown") end
end)

hook.Add("InitPostEntity", "SAO.VisualValidationMapTransition", function()
    if state.active then SAO.AssetValidator.Cancel("map change") end
end)

hook.Add("Disconnected", "SAO.VisualValidationDisconnect", function()
    if state.active then SAO.AssetValidator.Cancel("disconnected") end
end)
