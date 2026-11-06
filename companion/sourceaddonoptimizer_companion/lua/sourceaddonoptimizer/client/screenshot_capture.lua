SAO.ScreenshotCapture = SAO.ScreenshotCapture or {}

local pending = nil

local function screenshot_id(test_id, view)
    return "shot-" .. string.sub(string.lower(util.SHA256(test_id .. "\0" .. view)), 1, 59)
end

local function complete(success, record, reason)
    local callback = pending and pending.callback or nil
    pending = nil
    if callback then callback(success, record, reason) end
end

local function failure_record(job, status)
    return {
        id = job.id,
        test_id = job.test.id,
        path = job.relative_path,
        sha256 = SAO.Contract.Sha256(""),
        bytes = 0,
        view = job.view,
        retry_count = job.attempt,
        status = status,
        camera = table.Copy(job.metadata.camera),
        state = table.Copy(job.metadata.state)
    }
end

function SAO.ScreenshotCapture.Capture(panel, run_id, test, view, metadata, callback)
    if pending then return false, "a screenshot capture is already pending" end
    if not IsValid(panel) or not SAO.Contract.IsRunId(run_id) or not istable(test) or not isstring(test.id) or not isstring(view) or not istable(metadata) or not isfunction(callback) then return false, "invalid screenshot request" end
    local declared = false
    for _, value in ipairs(test.views) do
        if value == view then declared = true break end
    end
    if not declared and view ~= "closeup" then return false, "view was not declared by the validation plan" end
    local identifier = screenshot_id(test.id, view)
    pending = {
        panel = panel,
        run_id = run_id,
        test = test,
        view = view,
        metadata = table.Copy(metadata),
        callback = callback,
        attempt = 0,
        wait_frames = 0,
        id = identifier,
        filename = identifier .. ".png",
        relative_path = "screenshots/" .. run_id .. "/" .. identifier .. ".png"
    }

    return true
end

function SAO.ScreenshotCapture.Cancel()
    if not pending then return end
    local job = pending
    complete(false, failure_record(job, "INCOMPLETE"), "capture cancelled")
end

hook.Add("PostRenderVGUI", "SAO.BoundedScreenshotCapture", function()
    local job = pending
    if not job then return end
    local panel = job.panel
    if not IsValid(panel) or not panel:IsVisible() then
        complete(false, failure_record(job, "WARNING"), "gallery panel became unavailable")

        return
    end
    if panel.SAOPaintedFrame ~= FrameNumber() then
        job.wait_frames = job.wait_frames + 1
        if job.wait_frames <= 120 then return end
        if job.attempt < SAO.Constants.SCREENSHOT_RETRIES then
            job.attempt = job.attempt + 1
            job.wait_frames = 0

            return
        end
        complete(false, failure_record(job, "WARNING"), "gallery was not painted before the bounded timeout")

        return
    end
    local x, y = panel:LocalToScreen(0, 0)
    local width = math.Clamp(panel:GetWide(), 1, ScrW())
    local height = math.Clamp(panel:GetTall(), 1, ScrH())
    if x < 0 or y < 0 or x + width > ScrW() or y + height > ScrH() then
        complete(false, failure_record(job, "WARNING"), "gallery crop is outside the active screen")

        return
    end
    local ok, raw = pcall(function()
        return render.Capture({
            format = "png",
            x = x,
            y = y,
            w = width,
            h = height,
            alpha = false
        })
    end)
    if not ok or not isstring(raw) or #raw == 0 then
        if job.attempt < SAO.Constants.SCREENSHOT_RETRIES then
            job.attempt = job.attempt + 1
            job.wait_frames = 0
            panel.SAOPaintedFrame = -1

            return
        end
        complete(false, failure_record(job, "WARNING"), "PNG capture failed after the single retry")

        return
    end
    local root = SAO.Constants.DATA_ROOT .. "/" .. SAO.Constants.DataFolders.screenshots .. "/" .. job.run_id
    file.CreateDir(root)
    local path = root .. "/" .. job.filename
    local handle = file.Open(path, "wb", "DATA")
    if not handle then
        complete(false, failure_record(job, "WARNING"), "PNG output could not be opened")

        return
    end
    handle:Write(raw)
    handle:Flush()
    handle:Close()
    if file.Size(path, "DATA") ~= #raw then
        complete(false, failure_record(job, "WARNING"), "PNG output size verification failed")

        return
    end
    local record = {
        id = job.id,
        test_id = job.test.id,
        path = job.relative_path,
        sha256 = SAO.Contract.Sha256(raw),
        bytes = #raw,
        view = job.view,
        retry_count = job.attempt,
        status = "PASS",
        camera = table.Copy(job.metadata.camera),
        state = table.Copy(job.metadata.state)
    }
    complete(true, record, "captured")
end)
