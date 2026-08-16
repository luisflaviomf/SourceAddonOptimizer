from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
import shutil
import subprocess


ROOT = (
    Path(__file__).resolve().parents[2]
    / "companion"
    / "sourceaddonoptimizer_companion"
)
REPOSITORY = Path(__file__).resolve().parents[2]

EXPECTED_FILES = {
    "addon.json",
    "lua/autorun/sourceaddonoptimizer_loader.lua",
    "lua/sourceaddonoptimizer/shared/constants.lua",
    "lua/sourceaddonoptimizer/shared/contract.lua",
    "lua/sourceaddonoptimizer/shared/validation.lua",
    "lua/sourceaddonoptimizer/server/permissions.lua",
    "lua/sourceaddonoptimizer/server/profiler.lua",
    "lua/sourceaddonoptimizer/server/error_collector.lua",
    "lua/sourceaddonoptimizer/server/storage.lua",
    "lua/sourceaddonoptimizer/server/result_writer.lua",
    "lua/sourceaddonoptimizer/server/structural_validator.lua",
    "lua/sourceaddonoptimizer/client/profiler.lua",
    "lua/sourceaddonoptimizer/client/result_writer.lua",
}


def relative_files() -> set[str]:
    return {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
    }


def test_minimal_addon_has_one_linux_safe_declared_package() -> None:
    assert relative_files() == EXPECTED_FILES
    for relative in relative_files():
        assert relative == relative.lower()
        assert relative.isascii()
        assert re.fullmatch(r"[a-z0-9_./-]+", relative)
    assert len(tuple((ROOT / "lua" / "autorun").glob("*.lua"))) == 1

    metadata = json.loads((ROOT / "addon.json").read_text(encoding="utf-8"))
    assert metadata == {
        "title": "Source Addon Optimizer Companion",
        "type": "tool",
        "tags": ["build"],
        "ignore": ["*.psd", "*.xcf"],
    }


def test_lua_sources_are_utf8_without_a_byte_order_mark() -> None:
    for path in (ROOT / "lua").rglob("*.lua"):
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), path


def test_loader_sends_client_files_before_including_shared_and_realm_modules() -> None:
    loader = (ROOT / "lua" / "autorun" / "sourceaddonoptimizer_loader.lua").read_text(
        encoding="utf-8"
    )
    assert loader.count("SAO = SAO or {}") == 1
    assert loader.count("AddCSLuaFile") == 3
    assert loader.index("AddCSLuaFile(path)") < loader.index("include(path)")
    assert loader.index("include(path)") < loader.index("include_server")
    assert loader.index("include_server") < loader.index("include_client")
    for relative in (
        "sourceaddonoptimizer/shared/constants.lua",
        "sourceaddonoptimizer/shared/contract.lua",
        "sourceaddonoptimizer/shared/validation.lua",
        "sourceaddonoptimizer/server/permissions.lua",
        "sourceaddonoptimizer/server/profiler.lua",
        "sourceaddonoptimizer/server/error_collector.lua",
        "sourceaddonoptimizer/server/storage.lua",
        "sourceaddonoptimizer/server/result_writer.lua",
        "sourceaddonoptimizer/server/structural_validator.lua",
        "sourceaddonoptimizer/client/profiler.lua",
        "sourceaddonoptimizer/client/result_writer.lua",
    ):
        assert relative in loader


def test_lua_modules_export_only_through_the_sao_namespace() -> None:
    assignment = re.compile(r"^(?!\s*(?:local\b|SAO\b))[A-Za-z_][A-Za-z0-9_.]*\s*=", re.MULTILINE)
    for path in (ROOT / "lua").rglob("*.lua"):
        source = path.read_text(encoding="utf-8")
        assert assignment.search(source) is None, path


def test_lua_lint_script_runs_the_bundled_linter_and_cleans_its_temp_directory() -> None:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY / "scripts" / "test_companion_lua.ps1"),
        ],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Companion Lua lint passed" in result.stdout
    assert "temporary directory retained:" not in result.stdout.lower()


def test_lua_lint_script_never_rewrites_the_addon(tmp_path: Path) -> None:
    copy = tmp_path / "sourceaddonoptimizer_companion"
    shutil.copytree(ROOT, copy)
    target = copy / "lua" / "sourceaddonoptimizer" / "shared" / "constants.lua"
    target.write_text("SAO.Constants = SAO.Constants or {}\n", encoding="utf-8")
    before = hashlib.sha256(target.read_bytes()).hexdigest()

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY / "scripts" / "test_companion_lua.ps1"),
            "-AddonRoot",
            str(copy),
        ],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before
