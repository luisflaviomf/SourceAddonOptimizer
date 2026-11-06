from __future__ import annotations

import json
from pathlib import Path

import jsonschema


REPOSITORY = Path(__file__).resolve().parents[2]
ADDON = REPOSITORY / "companion" / "sourceaddonoptimizer_companion"
LUA = ADDON / "lua" / "sourceaddonoptimizer"


def read(relative: str) -> str:
    return (LUA / relative).read_text(encoding="utf-8")


def test_profiler_defines_fixed_evidence_tiers_and_hard_caps() -> None:
    constants = read("shared/constants.lua")
    for name, value in (
        ("registered", "0.10"),
        ("instantiated", "0.25"),
        ("visible", "0.50"),
        ("repeated_visible", "0.75"),
        ("focused", "1.00"),
    ):
        assert f'{name} = {{ rank = ' in constants
        assert f"importance = {value}" in constants
    assert "MAX_MODELS = 4096" in constants
    assert "MAX_MATERIALS = 16384" in constants
    assert "MAX_BUNDLE_BYTES = 20000000" in constants
    assert "PROFILER_INITIAL_BUDGET" in constants
    assert "PROFILER_RECONCILE_BUDGET" in constants
    assert "PROFILER_CLIENT_BUDGET" in constants
    assert "MAX_OBSERVATIONS_PER_MESSAGE" in constants


def test_server_profiler_is_lifecycle_driven_and_amortizes_inventory() -> None:
    server = read("server/profiler.lua")
    assert 'hook.Add("OnEntityCreated"' in server
    assert "timer.Simple(0" in server
    assert "initial_queue" in server
    assert "reconcile_queue" in server
    assert "queue_initial_inventory" in server
    assert "queue_reconciliation" in server
    assert "ents.GetAll()" in server
    assert "PROFILER_INITIAL_BUDGET" in server
    assert "PROFILER_RECONCILE_BUDGET" in server
    assert 'hook.Add("Think"' in server
    think_body = server.split('hook.Add("Think"', 1)[1]
    assert "ents.GetAll()" not in think_body
    assert "IsValid(ent)" in server
    assert "ent:GetNoDraw()" in server
    assert "ent:IsDormant()" in server
    assert "ent:GetModel()" in server


def test_model_state_is_deduplicated_and_evidence_saturates() -> None:
    server = read("server/profiler.lua")
    assert "skins_by_value" in server
    assert "bodygroups_by_key" in server
    assert "submaterials_by_key" in server
    assert "materials_by_path" in server
    assert "SAO.Constants.EvidenceByName" in server
    assert "math.max" in server
    assert "observation_count" not in server
    assert "importance" not in server
    for api in (
        "ent:GetSkin()",
        "ent:GetBodyGroups()",
        "ent:GetBodygroup(",
        "ent:GetMaterials()",
        "ent:GetSubMaterial(",
    ):
        assert api in server
    for counter in ('models = 0', 'materials = 0', 'lua_errors = 0', 'bytes = 0'):
        assert counter in server


def test_profile_environment_and_mounted_metadata_are_engine_derived() -> None:
    server = read("server/profiler.lua")
    assert "game.GetMap()" in server
    assert "engine.ActiveGamemode()" in server
    assert "engine.GetAddons()" in server
    assert "addon.mounted" in server
    assert "addon.wsid" in server
    assert "addon.title" in server
    assert "session_seconds" in server
    for forbidden in ("SteamID", "Nick(", "IPAddress", "GetHostName", "chat"):
        assert forbidden not in server


def test_client_visibility_sampling_is_round_robin_bounded_and_separated() -> None:
    client = read("client/profiler.lua")
    assert "round_robin" in client
    assert "PROFILER_CLIENT_BUDGET" in client
    assert "FrameNumber()" in client
    assert "visible_samples >= SAO.Constants.REPEATED_VISIBLE_SAMPLES" in client
    assert "last_visible_frame" in client
    assert "util.PixelVisible" in client
    assert "ent:IsDormant()" in client
    assert "ent:GetNoDraw()" in client
    assert "GetViewModel()" in client
    assert "GetVehicle()" in client
    assert "function SAO.ClientProfiler.FocusEntity(" in client


def test_network_boundary_transfers_only_bounded_observation_records() -> None:
    server = read("server/profiler.lua")
    client = read("client/profiler.lua")
    assert "net.Receive(SAO.Constants.Net.Observation" in server
    assert "SAO.Permissions.CanUse(ply)" in server
    assert "SAO.Permissions.ValidateSession(" in server
    assert "MAX_OBSERVATIONS_PER_MESSAGE" in server
    assert "MAX_NET_IDENTIFIER_BYTES" in server
    assert "MAX_PATH_BYTES" in server
    assert "net.ReadData(" in server
    assert "net.ReadString()" not in server + client
    assert "net.WriteData(" in client
    assert "WriteBundle" not in client
    assert "screenshots" not in client


def test_error_collection_uses_engine_hooks_and_bounded_sanitized_summaries() -> None:
    errors = read("server/error_collector.lua")
    server = read("server/profiler.lua")
    assert 'hook.Add("OnLuaError"' in errors
    assert 'hook.Add("OnClientLuaError"' in errors
    assert "MAX_LUA_ERRORS" in errors + server
    assert "sanitize_text" in errors
    assert "string.gsub" in errors
    assert "util.SHA256" in errors
    assert "math.min" in errors
    for forbidden in ("SteamID", "Nick(", "Chat", "IPAddress", "file.Read", "debug.getinfo"):
        assert forbidden not in errors


def test_profiler_exports_bounded_performance_and_truncation_summary() -> None:
    server = read("server/profiler.lua")
    for field in (
        "average_ms",
        "p95_ms",
        "max_ms",
        "queue_high_water",
        "histogram",
        "truncation",
    ):
        assert field in server
    assert "SysTime()" in server
    assert "MAX_PROFILE_PAYLOAD_BYTES" in server
    assert "MAX_BUNDLE_BYTES" in server
    assert 'SAO.Storage.WriteBundle("profiles"' in server


def test_representative_export_shape_validates_against_frozen_schema() -> None:
    schema = json.loads(
        (REPOSITORY / "companion" / "contracts" / "sao-runtime-profile-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    envelope = {
        "schema": "sao-runtime-profile-v1",
        "contract": {"major": 1, "minor": 0},
        "run_id": "123e4567-e89b-12d3-a456-426614174000",
        "created_utc": "2026-08-16T12:00:00Z",
        "producer": {"name": "Source Addon Optimizer Companion", "version": "1.0.0"},
        "environment": {
            "game_build": "2026.08.16",
            "map": "gm_flatgrass",
            "gamemode": "sandbox",
            "realm": "singleplayer",
            "session_seconds": 12.5,
            "mounted_addons": [{"workshop_id": "3027256228", "title": "LVS Cars Pack"}],
        },
        "chunk": {"sequence": 1, "total": 1},
        "payload": {
            "models": [{
                "id": "a" * 64,
                "path": "models/example/car.mdl",
                "evidence": "focused",
                "skins": [0],
                "bodygroups": [{"name": "wheel", "value": 1, "evidence": "instantiated"}],
                "submaterials": [{"slot": 0, "material": "materials/example/tire.vmt", "evidence": "visible"}],
                "materials": ["materials/example/body.vmt"],
            }],
            "materials": [{"id": "b" * 64, "path": "materials/example/body.vmt", "evidence": "focused"}],
            "lua_errors": [],
            "performance": {"samples": 3, "average_ms": 0.1, "p95_ms": 0.25, "max_ms": 0.2, "queue_high_water": 10},
            "truncation": {"models": 0, "materials": 0, "lua_errors": 0, "bytes": 0},
        },
    }
    jsonschema.Draft202012Validator(schema).validate(envelope)
