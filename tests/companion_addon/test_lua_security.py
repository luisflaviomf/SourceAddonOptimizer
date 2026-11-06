from __future__ import annotations

from pathlib import Path
import re


ROOT = (
    Path(__file__).resolve().parents[2]
    / "companion"
    / "sourceaddonoptimizer_companion"
)

FORBIDDEN = (
    re.compile(r"\bRunString\b"),
    re.compile(r"\bCompileString\b"),
    re.compile(r"\bos\.execute\b"),
    re.compile(r"\bgame\.ConsoleCommand\b"),
    re.compile(r"\bConCommand\s*\("),
    re.compile(r"\brequire\s*\("),
    re.compile(r"\bhttp\."),
    re.compile(r"https?://"),
)


def lua_sources() -> dict[str, str]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "lua").rglob("*.lua")
    }


def test_addon_contains_no_code_execution_download_or_console_boundary() -> None:
    sources = lua_sources()
    assert sources
    for relative, source in sources.items():
        for pattern in FORBIDDEN:
            assert pattern.search(source) is None, (relative, pattern.pattern)
        assert "chat.AddText" not in source, relative
        assert "PrintMessage" not in source, relative


def test_storage_uses_only_fixed_data_roots_and_atomic_completion() -> None:
    source = lua_sources()["lua/sourceaddonoptimizer/server/storage.lua"]
    assert 'DATA_ROOT = "sourceaddonoptimizer"' in lua_sources()[
        "lua/sourceaddonoptimizer/shared/constants.lua"
    ]
    for folder in ("profiles", "validation_inbox", "validation_results", "screenshots"):
        assert f'"{folder}"' in lua_sources()[
            "lua/sourceaddonoptimizer/shared/constants.lua"
        ]
    assert "function SAO.Storage.WriteBundle(" in source
    assert '".partial.json"' in source
    assert '"complete.ready.json"' in source
    assert source.index('"run-index.json"') < source.index('"complete.ready.json"')
    assert 'write_atomic(root .. "/" .. COMPLETE_FILE, completion_raw)' in source
    assert 'file.Open(partial, "wb", "DATA")' in source
    assert "handle:Flush()" in source
    assert "file.Write(partial" not in source
    assert "file.Rename" in source
    assert "file.Delete" not in source


def test_contract_decoder_keeps_default_gmod_json_limits_and_closed_operations() -> None:
    sources = lua_sources()
    contract = sources["lua/sourceaddonoptimizer/shared/contract.lua"]
    validation = sources["lua/sourceaddonoptimizer/shared/validation.lua"]
    assert "util.JSONToTable(raw)" in contract
    assert "util.JSONToTable(raw," not in contract
    assert "ValidateClosedKeys" in contract
    assert "NormalizeVirtualPath" in contract
    for kind in ("model_structure", "model_state", "material_resolve", "model_render"):
        assert f'"{kind}"' in validation


def test_every_net_receiver_rechecks_permission_through_one_helper() -> None:
    for relative, source in lua_sources().items():
        if "net.Receive" not in source:
            continue
        receiver_count = source.count("net.Receive")
        assert source.count("SAO.Constants.Net.") >= receiver_count, relative
        if "/server/" in relative:
            assert source.count("SAO.Permissions.CanUse(") >= receiver_count, relative
        else:
            assert "function can_use_local_admin()" in source, relative
            assert source.count("can_use_local_admin()") > receiver_count, relative
            assert "LocalPlayer()" in source and ":IsSuperAdmin()" in source, relative
        assert "net.ReadString()" not in source, relative


def test_privileged_sessions_are_bounded_expiring_and_revalidated() -> None:
    sources = lua_sources()
    constants = sources["lua/sourceaddonoptimizer/shared/constants.lua"]
    permissions = sources["lua/sourceaddonoptimizer/server/permissions.lua"]
    assert "SESSION_LIFETIME_SECONDS = 900" in constants
    assert "MAX_NET_IDENTIFIER_BYTES = 36" in constants
    assert "MAX_NET_DETAIL_BYTES = 256" in constants
    assert "function SAO.Permissions.OpenSession(" in permissions
    assert "function SAO.Permissions.ValidateSession(" in permissions
    assert "function SAO.Permissions.CloseSession(" in permissions
    assert "SAO.Permissions.CanUse(ply)" in permissions
    assert "expires_at" in permissions
    assert "SecurityEvents" in permissions
    assert "ply == nil or ply == NULL" in permissions


def test_storage_restricts_each_folder_to_its_declared_schema() -> None:
    source = lua_sources()["lua/sourceaddonoptimizer/server/storage.lua"]
    assert "allowed_schemas_by_kind" in source
    assert 'profiles = { ["sao-runtime-profile-v1"] = true }' in source
    assert 'validation_inbox = { ["sao-validation-plan-v1"] = true }' in source
    assert '["sao-structural-result-v1"] = true' in source
    assert '["sao-visual-result-v1"] = true' in source
