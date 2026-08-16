SAO.ServerResultWriter = SAO.ServerResultWriter or {}

function SAO.ServerResultWriter.Write(ply, schema, run_id, created_utc, producer, environment, payloads)
    if not SAO.Permissions.CanUse(ply) then return false, "permission denied" end

    return SAO.Storage.WriteBundle("validation_results", schema, run_id, created_utc, producer, environment, payloads)
end
