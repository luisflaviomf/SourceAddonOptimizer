SAO.Constants = SAO.Constants or {}
SAO.Constants.CONTRACT_MAJOR = 1
SAO.Constants.CONTRACT_MINOR = 0
SAO.Constants.DATA_ROOT = "sourceaddonoptimizer"
SAO.Constants.MAX_CHUNK_BYTES = 2000000
SAO.Constants.MAX_INDEX_BYTES = 262144
SAO.Constants.MAX_COMPLETION_BYTES = 4096
SAO.Constants.MAX_BUNDLE_BYTES = 20000000
SAO.Constants.MAX_JSON_DEPTH = 12
SAO.Constants.MAX_TOTAL_KEYS = 100000
SAO.Constants.MAX_PATH_BYTES = 512
SAO.Constants.MAX_PATH_COMPONENT_BYTES = 128
SAO.Constants.MAX_MODELS = 4096
SAO.Constants.MAX_MATERIALS = 16384
SAO.Constants.MAX_PLAN_MODELS = 250
SAO.Constants.MAX_SCREENSHOTS = 1000
SAO.Constants.MAX_ORDINARY_VIEWS = 4
SAO.Constants.MAX_LUA_ERRORS = 1024
SAO.Constants.MAX_ENTITY_QUEUE = 8192
SAO.Constants.MAX_MATERIALS_PER_MODEL = 128
SAO.Constants.MAX_BODYGROUP_STATES_PER_MODEL = 256
SAO.Constants.MAX_SUBMATERIALS_PER_MODEL = 32
SAO.Constants.MAX_PROFILE_PAYLOAD_BYTES = 1800000
SAO.Constants.PROFILER_INITIAL_BUDGET = 12
SAO.Constants.PROFILER_RECONCILE_BUDGET = 4
SAO.Constants.PROFILER_CLIENT_BUDGET = 6
SAO.Constants.PROFILER_RECONCILE_SECONDS = 30
SAO.Constants.REPEATED_VISIBLE_SAMPLES = 3
SAO.Constants.MAX_OBSERVATIONS_PER_MESSAGE = 16
SAO.Constants.OBSERVATION_SEND_INTERVAL = 0.25
SAO.Constants.SESSION_LIFETIME_SECONDS = 900
SAO.Constants.NET_RATE_LIMIT_SECONDS = 0.25
SAO.Constants.MAX_NET_IDENTIFIER_BYTES = 36
SAO.Constants.MAX_NET_DETAIL_BYTES = 256

SAO.Constants.DataFolders = {
    profiles = "profiles",
    validation_inbox = "validation_inbox",
    validation_results = "validation_results",
    screenshots = "screenshots"
}

SAO.Constants.Schemas = {
    ["sao-runtime-profile-v1"] = true,
    ["sao-validation-plan-v1"] = true,
    ["sao-structural-result-v1"] = true,
    ["sao-visual-result-v1"] = true
}

SAO.Constants.Net = {
    BeginSession = "sao_begin_session_v1",
    EndSession = "sao_end_session_v1",
    Progress = "sao_progress_v1",
    Observation = "sao_observation_v1"
}

SAO.Constants.EvidenceByName = {
    registered = { rank = 1, importance = 0.10, wire = 1 },
    instantiated = { rank = 2, importance = 0.25, wire = 2 },
    visible = { rank = 3, importance = 0.50, wire = 3 },
    repeated_visible = { rank = 4, importance = 0.75, wire = 4 },
    focused = { rank = 5, importance = 1.00, wire = 5 }
}

SAO.Constants.EvidenceByWire = {
    [1] = "registered",
    [2] = "instantiated",
    [3] = "visible",
    [4] = "repeated_visible",
    [5] = "focused"
}

SAO.Constants.PROFILER_HISTOGRAM_MS = {0.10, 0.25, 0.50, 1.00, 2.00, 5.00}
