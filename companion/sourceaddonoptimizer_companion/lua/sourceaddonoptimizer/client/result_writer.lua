SAO.ClientResultWriter = SAO.ClientResultWriter or {}

function SAO.ClientResultWriter.BuildRequest(schema, run_id, created_utc, producer, environment, payloads)
    if not SAO.Constants.Schemas[schema] or not SAO.Contract.IsRunId(run_id) or not istable(payloads) then return nil, "invalid result request" end

    return {
        schema = schema,
        run_id = run_id,
        created_utc = created_utc,
        producer = producer,
        environment = environment,
        payloads = payloads
    }
end
