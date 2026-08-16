SAO = SAO or {}

local include_shared = {"sourceaddonoptimizer/shared/constants.lua", "sourceaddonoptimizer/shared/contract.lua", "sourceaddonoptimizer/shared/validation.lua"}

if SERVER then
    AddCSLuaFile()

    for _, path in ipairs(include_shared) do
        AddCSLuaFile(path)
    end
end

for _, path in ipairs(include_shared) do
    include(path)
end

local include_server = {"sourceaddonoptimizer/server/permissions.lua", "sourceaddonoptimizer/server/storage.lua", "sourceaddonoptimizer/server/profiler.lua", "sourceaddonoptimizer/server/error_collector.lua", "sourceaddonoptimizer/server/result_writer.lua", "sourceaddonoptimizer/server/structural_validator.lua"}

local include_client = {"sourceaddonoptimizer/client/profiler.lua", "sourceaddonoptimizer/client/result_writer.lua", "sourceaddonoptimizer/client/screenshot_capture.lua", "sourceaddonoptimizer/client/model_gallery.lua", "sourceaddonoptimizer/client/asset_validator.lua", "sourceaddonoptimizer/client/dashboard.lua"}

if SERVER then
    for _, path in ipairs(include_client) do
        AddCSLuaFile(path)
    end

    for _, path in ipairs(include_server) do
        include(path)
    end
else
    for _, path in ipairs(include_client) do
        include(path)
    end
end

SAO.Initialized = true
