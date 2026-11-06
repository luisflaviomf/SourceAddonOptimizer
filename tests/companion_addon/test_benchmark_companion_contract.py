from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY / "scripts" / "benchmark_companion.py"


def load_script():
    spec = importlib.util.spec_from_file_location("benchmark_companion", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evidence_root_must_not_overlap_protected_roots(tmp_path: Path) -> None:
    module = load_script()
    repo = tmp_path / "repo"
    addon = tmp_path / "source-addon"
    framework = tmp_path / "framework"
    local_work = tmp_path / "localappdata" / "GmodAddonOptimizer" / "work"
    for path in (repo, addon, framework, local_work):
        path.mkdir(parents=True)

    for unsafe in (repo / "evidence", addon / "evidence", framework / "evidence", local_work / "evidence", tmp_path):
        with pytest.raises(ValueError, match="overlaps protected root"):
            module.validate_evidence_root(unsafe, (repo, addon, framework, local_work))

    safe = tmp_path.parent / f"{tmp_path.name}-external-evidence"
    assert module.validate_evidence_root(safe, (repo, addon, framework, local_work)) == safe.resolve()


def test_acceptance_run_requires_five_families_three_repetitions_and_warmup() -> None:
    module = load_script()
    with pytest.raises(ValueError, match="5 to 10"):
        module.validate_run_shape(("one",), repetitions=3, warmups=1, pilot=False)
    with pytest.raises(ValueError, match="at least 3"):
        module.validate_run_shape(tuple(str(index) for index in range(5)), repetitions=2, warmups=1, pilot=False)
    with pytest.raises(ValueError, match="warm-up"):
        module.validate_run_shape(tuple(str(index) for index in range(5)), repetitions=3, warmups=0, pilot=False)

    module.validate_run_shape(("one",), repetitions=1, warmups=0, pilot=True)


def test_schedule_is_paired_and_interleaved() -> None:
    module = load_script()
    schedule = module.build_schedule(("family-a", "family-b"), repetitions=3, warmups=1)

    assert [(item.phase, item.index, item.lane) for item in schedule[:2]] == [
        ("warmup", 1, "maximum-no-profile"),
        ("warmup", 1, "maximum-profile"),
    ]
    measured_a = [item.lane for item in schedule if item.family_id == "family-a" and item.phase == "measured"]
    assert measured_a == [
        "maximum-no-profile", "maximum-profile",
        "maximum-profile", "maximum-no-profile",
        "maximum-no-profile", "maximum-profile",
    ]


def test_profile_argument_is_present_only_in_profile_lane(tmp_path: Path) -> None:
    module = load_script()
    common = dict(
        repo_root=REPOSITORY,
        addon_path=tmp_path / "source",
        work_path=tmp_path / "work",
        blender=tmp_path / "blender.exe",
        studiomdl=tmp_path / "studiomdl.exe",
        framework_root=tmp_path / "framework",
    )
    profile_root = tmp_path / "profile"
    lane_a = module.build_companion_command("maximum-no-profile", profile_root=profile_root, **common)
    lane_b = module.build_companion_command("maximum-profile", profile_root=profile_root, **common)

    assert "--maximum-runtime-profile" not in lane_a
    assert "--maximum-framework-resolver" in lane_a
    position = lane_b.index("--maximum-runtime-profile")
    assert lane_b[position + 1] == str(profile_root.resolve())
    assert [value for value in lane_b if value == "--maximum-runtime-profile"] == ["--maximum-runtime-profile"]


def test_lua_measurement_driver_uses_public_profiler_api_and_bounded_timers() -> None:
    source = (REPOSITORY / "scripts" / "measure_companion_profiler.lua").read_text(encoding="utf-8")
    assert "SAO.ServerProfiler.Start" in source
    assert "SAO.ServerProfiler.StopAndExport" in source
    assert "SAO.ClientProfiler.FocusEntity" in source
    assert "timer.Simple" in source
    assert "file.Delete" not in source
    assert "RunString" not in source


def test_benchmark_analysis_percent_change_is_directional() -> None:
    script = REPOSITORY / "scripts" / "analyze_companion_benchmark.py"
    spec = importlib.util.spec_from_file_location("analyze_companion_benchmark", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.percent_change(100.0, 110.0) == pytest.approx(10.0)
    assert module.percent_change(100.0, 90.0) == pytest.approx(-10.0)
    assert module.median_range((3.0, 1.0, 2.0)) == {"median": 2.0, "minimum": 1.0, "maximum": 3.0}


def test_benchmark_analysis_fingerprints_geometry_decisions_not_report_noise() -> None:
    script = REPOSITORY / "scripts" / "analyze_companion_benchmark.py"
    spec = importlib.util.spec_from_file_location("analyze_companion_benchmark", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    first = {
        "report_path": "one/work/report.json",
        "stages": [{"wall_seconds": 1.0}],
        "region_details": [{
            "key": "r-b",
            "source": "body.smd",
            "material": "paint",
            "representation": "normal",
            "original_triangles": 100,
            "normal_triangles": 40,
            "selected_triangles": 40,
            "selected_vertices": 24,
            "failed_gates": [],
            "targeted_render": True,
        }],
    }
    reordered_with_noise = {
        "region_details": [dict(reversed(list(first["region_details"][0].items())))],
        "stages": [{"wall_seconds": 99.0}],
        "report_path": "two/work/report.json",
    }
    changed = json.loads(json.dumps(first))
    changed["region_details"][0]["selected_triangles"] = 41

    assert module.region_decision_sha256(first) == module.region_decision_sha256(reordered_with_noise)
    assert module.region_decision_sha256(first) != module.region_decision_sha256(changed)

    artifact = {"selected_smd_files": [
        {"path": "b.smd", "bytes": 2, "sha256": "b" * 64},
        {"path": "a.smd", "bytes": 1, "sha256": "a" * 64},
    ]}
    artifact_reordered = {"selected_smd_files": list(reversed(artifact["selected_smd_files"]))}
    artifact_changed = json.loads(json.dumps(artifact))
    artifact_changed["selected_smd_files"][0]["bytes"] = 3
    assert module.selected_smd_manifest_sha256(artifact) == module.selected_smd_manifest_sha256(artifact_reordered)
    assert module.selected_smd_manifest_sha256(artifact) != module.selected_smd_manifest_sha256(artifact_changed)


def test_benchmark_analysis_summarizes_region_outcomes_and_profile_uplift() -> None:
    script = REPOSITORY / "scripts" / "analyze_companion_benchmark.py"
    spec = importlib.util.spec_from_file_location("analyze_companion_benchmark", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    summary = module.region_run_summary({
        "region_details": [
            {"representation": "aggressive", "failed_gates": [], "targeted_render": False},
            {"representation": "original", "failed_gates": ["normal", "uv"], "targeted_render": True},
        ],
        "runtime_profile": {
            "regions": [
                {"geometric_risk": 0.2, "effective_risk": 0.5},
                {"geometric_risk": 0.4, "effective_risk": 0.5},
            ],
        },
    })

    assert summary["representations"] == {"aggressive": 1, "original": 1}
    assert summary["failed_gates"] == {"normal": 1, "uv": 1}
    assert summary["targeted_regions"] == 1
    assert summary["runtime_risk_uplift_average"] == pytest.approx(0.2)
    assert summary["runtime_risk_uplift_maximum"] == pytest.approx(0.3)
