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
