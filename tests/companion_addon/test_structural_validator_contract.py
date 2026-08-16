from __future__ import annotations

from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
LUA = (
    REPOSITORY
    / "companion"
    / "sourceaddonoptimizer_companion"
    / "lua"
    / "sourceaddonoptimizer"
)


def read(relative: str) -> str:
    return (LUA / relative).read_text(encoding="utf-8")


def test_plan_reader_requires_complete_hashed_closed_validation_bundle() -> None:
    validation = read("shared/validation.lua")
    for token in (
        "complete.ready.json",
        "run-index.json",
        '"sao-validation-plan-v1"',
        "SAO.Contract.ValidateCompletion",
        "SAO.Contract.ValidateIndex",
        "SAO.Contract.ValidateEnvelope",
        "SAO.Contract.Sha256",
        "SAO.Validation.ValidateTest",
        "MAX_PLAN_MODELS",
        "MAX_SCREENSHOTS",
        "MAX_ORDINARY_VIEWS",
    ):
        assert token in validation
    assert "loadstring" not in validation.lower()
    assert "RunString" not in validation


def test_test_dispatch_is_closed_and_server_handles_only_structural_kinds() -> None:
    source = read("server/structural_validator.lua")
    assert "local dispatch = {" in source
    for kind in ("model_structure", "model_state", "material_resolve", "model_render"):
        assert f'["{kind}"]' in source
    assert "local handler = dispatch[test.kind]" in source
    assert "handler(test)" in source
    for forbidden in ("RunString", "CompileString", "include(test", "ConCommand", "game.ConsoleCommand"):
        assert forbidden not in source


def test_structure_checks_dx90_phy_model_entity_bones_and_cleans_up() -> None:
    source = read("server/structural_validator.lua")
    validation = read("shared/validation.lua")
    assert '".dx90.vtx"' in validation
    assert 'string.EndsWith(path, ".dx80.vtx")' in validation
    assert "expected_phy" in validation
    for token in (
        'file.Exists(path, "GAME")',
        "util.IsValidModel(test.path)",
        'ents.Create("prop_dynamic")',
        "entity:SetModel(test.path)",
        "entity:Spawn()",
        "entity:GetBoneCount()",
        "entity:GetModelBounds()",
        "entity:Remove()",
    ):
        assert token in source
    assert "cleanup_entity(entity)" in source
    assert "xpcall" in source


def test_state_and_material_checks_are_bounded_and_declarative() -> None:
    source = read("server/structural_validator.lua")
    for token in (
        "entity:SkinCount()",
        "entity:SetSkin(skin)",
        "entity:GetBodyGroups()",
        "entity:SetBodygroup(group.id, bodygroup.value)",
        "entity:GetMaterials()",
        'file.Size(path, "GAME")',
        "MAX_BODYGROUP_STATES_PER_MODEL",
        "MAX_MATERIALS_PER_MODEL",
        "mounted provider",
    ):
        assert token in source
    assert "test.required_files" in source
    assert "test.materials" in source


def test_queue_is_tick_bounded_resumable_and_cleans_up_on_every_exit() -> None:
    source = read("server/structural_validator.lua")
    constants = read("shared/constants.lua")
    assert "VALIDATION_TESTS_PER_TICK" in constants
    assert 'hook.Add("Think"' in source
    assert "SAO.Constants.VALIDATION_TESTS_PER_TICK" in source
    assert "save_progress(" in source
    assert "structural-progress-" in source
    assert 'hook.Add("PreCleanupMap"' in source
    assert 'hook.Add("ShutDown"' in source
    assert 'hook.Add("PlayerDisconnected"' in source
    assert "SAO.StructuralValidator.Cancel(" in source
    assert "cleanup_all_entities()" in source


def test_status_precedence_headless_warning_and_atomic_result_output() -> None:
    source = read("server/structural_validator.lua")
    writer = read("server/result_writer.lua")
    assert 'status == "FAILED"' in writer
    assert 'status == "WARNING"' in writer
    assert 'status == "INCOMPLETE"' in writer
    assert "visual client missing" in source.lower()
    assert 'SAO.Storage.WriteBundle("validation_results", "sao-structural-result-v1"' in writer
    assert "plan_sha256" in writer
    assert "overall_status" in writer
    assert "wall_seconds" in writer
    assert "duplicate" in writer.lower() or "attempt" in read("server/storage.lua").lower()


def test_lua_error_summaries_join_the_active_structural_session() -> None:
    errors = read("server/error_collector.lua")
    source = read("server/structural_validator.lua")
    assert "SAO.StructuralValidator.RecordLuaError" in errors
    assert "function SAO.StructuralValidator.RecordLuaError(" in source
    assert "MAX_LUA_ERRORS" in source
    assert "lua-error-" in source


def test_headless_console_can_complete_without_a_player() -> None:
    source = read("server/structural_validator.lua")
    permissions = read("server/permissions.lua")
    assert "ply == nil or ply == NULL" in permissions
    assert 'concommand.Add("sao_validate_start"' in source
    assert 'concommand.Add("sao_validate_cancel"' in source
    assert "SAO.Permissions.CanUse(ply)" in source
