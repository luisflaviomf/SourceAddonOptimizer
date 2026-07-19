from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.lvs_models.prepare_region_candidate import prepare_workspace
from maximum_optimizer.qc_graph import parse_qc_graph


def test_prepare_workspace_isolates_exact_source_for_one_ratio(tmp_path: Path):
    source_root = tmp_path / "source"
    source = source_root / "car" / "animated part.smd"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"version 1\nsource payload\n")
    out = tmp_path / "candidate"

    summary = prepare_workspace(
        source_root=source_root,
        source_identity="car/animated part.smd",
        ratio=0.45,
        candidate_id="supra-body12-r045",
        strategy="blender-importance-map-v1",
        out=out,
    )

    copied = out / "car" / "animated part.smd"
    assert copied.read_bytes() == source.read_bytes()
    assert summary["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert summary["source_identity"] == "car/animated part.smd"
    assert summary["ratio"] == 0.45
    candidate = json.loads((out / "candidate.json").read_text(encoding="utf-8"))
    assert candidate == {
        "candidate_id": "supra-body12-r045",
        "engine": "blender",
        "ratio": 0.45,
        "region_overrides": [],
        "strategy": "blender-importance-map-v1",
        "target_error": 0,
        "transfer": "blender-native-v1",
        "update_vertices": True,
    }
    graph = parse_qc_graph(out / "calibration.qc", out)
    assert [(item.role, item.logical_path) for item in graph.references] == [
        ("visual", "car/animated part.smd"),
    ]


def test_prepare_workspace_rejects_existing_output_and_unsafe_identity(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    out = tmp_path / "candidate"
    out.mkdir()

    for identity in ("../escape.smd", "C:/escape.smd", "/escape.smd"):
        try:
            prepare_workspace(
                source_root=source_root,
                source_identity=identity,
                ratio=0.5,
                candidate_id="bad",
                strategy="blender-adaptive-v1",
                out=tmp_path / identity.replace("/", "_"),
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe identity accepted: {identity}")

    source = source_root / "safe.smd"
    source.write_bytes(b"safe")
    try:
        prepare_workspace(
            source_root=source_root,
            source_identity="safe.smd",
            ratio=0.5,
            candidate_id="existing",
            strategy="blender-adaptive-v1",
            out=out,
        )
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("existing output was accepted")


def test_prepare_workspace_supports_direct_position_with_typed_prefilter(tmp_path: Path):
    source_root = tmp_path / "source"
    source = source_root / "car" / "body.smd"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"version 1\nsource payload\n")
    out = tmp_path / "candidate"

    prepare_workspace(
        source_root=source_root,
        source_identity="car/body.smd",
        ratio=0.25,
        candidate_id="monaco-body20-direct-r025",
        strategy="meshopt-direct-position-v1",
        out=out,
        target_error=0.03,
    )

    candidate = json.loads((out / "candidate.json").read_text(encoding="utf-8"))
    assert candidate == {
        "candidate_id": "monaco-body20-direct-r025",
        "direct_degenerate_prefilter": "direct-degenerate-prefilter-v1",
        "engine": "meshoptimizer",
        "ratio": 0.25,
        "region_overrides": [],
        "strategy": "meshopt-direct-position-v1",
        "target_error": 0.03,
        "transfer": "direct-v1",
        "update_vertices": False,
    }
