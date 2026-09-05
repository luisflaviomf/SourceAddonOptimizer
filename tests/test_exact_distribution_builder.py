import hashlib
import json
from pathlib import Path

import pytest

from exact_distribution.builder import build_candidate


def _build(tmp_path: Path, files: dict[str, bytes]):
    source = tmp_path / "source"
    engine = tmp_path / "engine"
    source.mkdir(); engine.mkdir()
    for name, data in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    before = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    report = build_candidate(source, tmp_path / "out", tmp_path / "work", engine)
    after = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    return source, tmp_path / "out", report, before, after


def test_native_candidate_is_deterministic_preserves_spelling_and_excludes_legacy_vtx(tmp_path):
    files = {"Models/Bar_Caddy.vvd": b"not-a-vvd", "lua/X.lua": b"print(1)",
             "models/x.dx80.vtx": b"old", "models/x.sw.vtx": b"old", "models/x.dx90.vtx": b"random"}
    source, out, report, before, after = _build(tmp_path, files)
    assert before == after
    assert (out / "Models/Bar_Caddy.vvd").read_bytes() == b"not-a-vvd"
    assert not (out / "models/x.dx80.vtx").exists() and not (out / "models/x.sw.vtx").exists()
    assert report["excluded"] == {"bytes": 6, "count": 2, "paths": ["Models/x.dx80.vtx", "Models/x.sw.vtx"]}
    assert report["status"] == "OFFLINE_PENDING_GMOD" and report["installed"] is False


def test_refuses_collisions_overlap_existing_output_and_symlink(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    (source / "A.txt").write_bytes(b"a")
    engine = tmp_path / "engine"; engine.mkdir()
    with pytest.raises(ValueError, match="overlap"):
        build_candidate(source, source / "out", tmp_path / "work2", engine)
    existing = tmp_path / "existing"; existing.mkdir()
    with pytest.raises(FileExistsError):
        build_candidate(source, existing, tmp_path / "work3", engine)


def test_duplicate_compressible_blobs_are_deduplicated_or_whole_candidate_falls_back(tmp_path):
    payload = b"RIFF" + b"same-pattern" * 30000
    _, out, report, _, _ = _build(tmp_path, {"sound/a.wav": payload, "sound/b.wav": payload})
    if report["encoded_count"]:
        assert report["encoded_count"] == 2 and report["unique_blob_count"] == 1
        assert not (out / "sound/a.wav").exists()
    else:
        assert (out / "sound/a.wav").read_bytes() == payload
        assert report["fallback_reason"] == "whole_candidate_no_net_gain"
    assert report["candidate_bytes"] < report["source_bytes"]


def test_prepacked_runtime_and_input_drift_are_refused(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.mkdir(); engine = tmp_path / "engine"; engine.mkdir()
    marker = source / "data_static/codex_exact_distribution/manifest.json"
    marker.parent.mkdir(parents=True); marker.write_text("{}")
    with pytest.raises(ValueError, match="prepacked"):
        build_candidate(source, tmp_path / "out", tmp_path / "work", engine)
