from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from maximum_optimizer.pipeline import run_maximum_adaptive


FORBIDDEN_RUNTIME_MARKERS = (b"sourceaddonoptimizer", b"sourceaddonoptimizer_companion")


def dependency_findings(root: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix().casefold()):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().lower()
        if "sourceaddonoptimizer" in relative:
            findings.append(relative)
            continue
        raw = path.read_bytes().lower()
        if any(marker in raw for marker in FORBIDDEN_RUNTIME_MARKERS):
            findings.append(relative)
    return findings


def test_no_profile_optimized_addon_has_no_companion_runtime_dependency(tmp_path: Path) -> None:
    from tests.maximum_optimizer.test_pipeline_reporting import AdaptivePipelineTests

    case = AdaptivePipelineTests()
    options = replace(case._fixture_options(tmp_path), validate_candidate=None, runtime_evidence=None)
    run_maximum_adaptive(options)
    optimized = tmp_path / "optimized-addon"
    (optimized / "models").mkdir(parents=True)
    (optimized / "materials").mkdir(parents=True)
    for path in (tmp_path / "compiled-models").rglob("*"):
        if path.is_file():
            target = optimized / "models" / path.relative_to(tmp_path / "compiled-models")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    for path in options.addon_root.joinpath("materials").rglob("*"):
        if path.is_file():
            target = optimized / "materials" / path.relative_to(options.addon_root / "materials")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())

    assert dependency_findings(optimized) == []


def test_artifact_dependency_scanner_detects_path_and_content_references(tmp_path: Path) -> None:
    addon = tmp_path / "optimized-addon"
    path_reference = addon / "lua" / "sourceaddonoptimizer" / "runtime.lua"
    content_reference = addon / "materials" / "cars" / "body.vmt"
    path_reference.parent.mkdir(parents=True)
    content_reference.parent.mkdir(parents=True)
    path_reference.write_text("return true", encoding="utf-8")
    content_reference.write_text('"$include" "sourceaddonoptimizer/materials/audit"', encoding="utf-8")

    assert dependency_findings(addon) == [
        "lua/sourceaddonoptimizer/runtime.lua",
        "materials/cars/body.vmt",
    ]
