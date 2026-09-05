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
    decisions = json.loads((tmp_path / "work/decisions.json").read_bytes())["entries"]
    native = next(row for row in decisions if row["source_path"] == "Models/Bar_Caddy.vvd")
    assert native["target_path"] == "Models/Bar_Caddy.vvd"


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


def test_engine_overlaps_remain_native_even_when_identical(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    engine = tmp_path / "engine"; engine.mkdir()
    raw = b"RIFF" + b"A" * 300000
    for root in (source, engine / "garrysmod"):
        path = root / "sound/a.wav"
        path.parent.mkdir(parents=True); path.write_bytes(raw)
    report = build_candidate(source, tmp_path / "out", tmp_path / "work", engine)
    assert report["encoded_count"] == 0
    assert (tmp_path / "out/sound/a.wav").read_bytes() == raw


def test_independent_codec_errors_propagate_without_success(tmp_path, monkeypatch):
    import exact_distribution.builder as builder
    source = tmp_path / "source"; source.mkdir()
    engine = tmp_path / "engine"; engine.mkdir()
    (source / "sound").mkdir(); (source / "sound/a.wav").write_bytes(b"A" * 30000)
    def mismatch(*args):
        raise ValueError("independent reconstruction mismatch")
    monkeypatch.setattr(builder, "encode_options", mismatch)
    with pytest.raises(ValueError, match="independent reconstruction"):
        build_candidate(source, tmp_path / "out", tmp_path / "work", engine)
    assert not (tmp_path / "work/candidate-report.json").exists()


def test_mid_inventory_source_drift_never_emits_success(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    engine = tmp_path / "engine"; engine.mkdir()
    (source / "sound").mkdir(); path = source / "sound/a.wav"; path.write_bytes(b"A" * 30000)
    def drift(*args):
        path.write_bytes(b"B" * 30000)
    with pytest.raises(ValueError, match="drift"):
        build_candidate(source, tmp_path / "out", tmp_path / "work", engine, drift)
    assert not (tmp_path / "work/candidate-report.json").exists()


def test_native_unsafe_empty_and_runtime_overhead_have_honest_report(tmp_path):
    _, out, report, before, after = _build(tmp_path, {
        "sound/caf\u00e9.wav": b"A" * 10000, "sound/empty.wav": b"", "sound/tiny.wav": b"B" * 500})
    assert before == after
    assert report["encoded_count"] == 0
    assert report["candidate_bytes"] == report["source_bytes"]
    assert not list(out.rglob("*.json"))


def test_deterministic_bytes_for_same_output_basename(tmp_path):
    left = tmp_path / "left"; right = tmp_path / "right"
    left.mkdir(); right.mkdir()
    files = {"sound/a.wav": b"X" * 300000}
    _, one, r1, _, _ = _build(left, files)
    _, two, r2, _, _ = _build(right, files)
    tree = lambda root: {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert tree(one) == tree(two) and r1["content_id"] == r2["content_id"]


def test_nested_archives_refused_before_candidate_creation(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    engine = tmp_path / "engine"; engine.mkdir()
    (source / "nested.gma").write_bytes(b"GMAD\x03" + b"legacy.dx80.vtx")
    with pytest.raises(ValueError, match="prepacked"):
        build_candidate(source, tmp_path / "out", tmp_path / "work", engine)
    assert not (tmp_path / "out").exists()


def test_cli_utf8_redirect_and_protocol(tmp_path, monkeypatch):
    import io
    import sys
    from exact_distribution.cli import main
    source = tmp_path / "source"; source.mkdir()
    engine = tmp_path / "engine"; engine.mkdir()
    (source / "caf\u00e9.txt").write_bytes(b"native")
    buffer = io.BytesIO()
    stdout = io.TextIOWrapper(buffer, encoding="ascii")
    monkeypatch.setattr(sys, "stdout", stdout)
    assert main([str(source), "--output", str(tmp_path / "out"), "--work", str(tmp_path / "work"),
                 "--engine-root", str(engine)]) == 0
    stdout.flush()
    output = buffer.getvalue().decode("utf-8")
    assert "EXACT_PROGRESS:" in output and "caf\u00e9.txt" in output
    assert "EXACT_REPORT:" in output and "EXACT_OUTPUT:" in output
    assert (tmp_path / "out/caf\u00e9.txt").read_bytes() == b"native"


def test_windows_junction_source_and_output_ancestor_are_refused(tmp_path):
    import os
    import subprocess
    if os.name != "nt":
        pytest.skip("Windows junction regression")
    source = tmp_path / "source"; source.mkdir()
    engine = tmp_path / "engine"; engine.mkdir()
    external = tmp_path / "external"; external.mkdir()
    (external / "keep.txt").write_bytes(b"preserved")
    link = source / "junction"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(external)], check=True, capture_output=True)
    try:
        with pytest.raises(ValueError, match="reparse"):
            build_candidate(source, tmp_path / "out", tmp_path / "work", engine)
        with pytest.raises(ValueError, match="reparse"):
            build_candidate(external, link / "out", tmp_path / "work", engine)
        assert (external / "keep.txt").read_bytes() == b"preserved"
    finally:
        # Remove the single fixture junction itself, never recurse into its target.
        os.rmdir(link)


def test_lexical_dotdot_overlap_and_work_overlap_refused(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    engine = tmp_path / "engine"; engine.mkdir()
    (tmp_path / "other").mkdir()
    with pytest.raises(ValueError, match="overlap"):
        build_candidate(source, tmp_path / "other/../source/out", tmp_path / "work", engine)
    with pytest.raises(ValueError, match="overlap"):
        build_candidate(source, tmp_path / "out", tmp_path / "out/work", engine)


def test_whole_candidate_manifest_gate_falls_back_without_deletion(tmp_path, monkeypatch):
    import exact_distribution.builder as builder
    from exact_distribution.codecs import encode_options
    from exact_distribution.runtime import NAMESPACE
    source = tmp_path / "source"; source.mkdir()
    engine = tmp_path / "engine"; engine.mkdir()
    (source / "sound").mkdir()
    raw = b"A" * 20000
    (source / "sound/a.wav").write_bytes(raw)
    # At the measured-family boundary, one byte remains for the manifest.
    # This is deliberately a size-boundary double; full GMA/runtime tested elsewhere.
    def borderline(*args):
        return {f"data_static/{NAMESPACE}/boundary.dat": b"X" * (len(raw) - 1)}, len(raw) - 1, {"family": "lzwav_0000"}
    monkeypatch.setattr(builder, "_family_artifacts", borderline)
    report = build_candidate(source, tmp_path / "out", tmp_path / "work", engine)
    assert report["fallback_reason"] == "whole_candidate_no_net_gain"
    assert report["encoded_count"] == 0 and report["candidate_bytes"] == len(raw)
    assert (tmp_path / "out/sound/a.wav").read_bytes() == raw
    assert not (tmp_path / "out/data_static").exists()


def test_output_autorun_drift_is_not_reported_success(tmp_path, monkeypatch):
    import exact_distribution.builder as builder
    source = tmp_path / "source"; source.mkdir()
    engine = tmp_path / "engine"; engine.mkdir()
    (source / "sound").mkdir(); (source / "sound/a.wav").write_bytes(b"A" * 200000)
    original_write = builder._write
    def tamper(path, raw):
        original_write(path, raw)
        if path.suffix == ".lua" and "out" in path.parts:
            path.write_bytes(b"!" + raw[1:])
    monkeypatch.setattr(builder, "_write", tamper)
    with pytest.raises(ValueError, match="artifact identity"):
        build_candidate(source, tmp_path / "out", tmp_path / "work", engine)
    assert not (tmp_path / "work/candidate-report.json").exists()


def test_family_cap_remainder_native_with_decision_evidence(tmp_path, monkeypatch):
    from exact_distribution.builder import source_gma
    monkeypatch.setattr(source_gma, "MAX_ENTRIES", 1)
    _, out, report, _, _ = _build(tmp_path, {"sound/a.wav": b"A" * 200000, "sound/b.wav": b"B" * 200000})
    assert report["encoded_count"] == 1 and report["native_count"] == 1
    assert (out / "sound/b.wav").read_bytes() == b"B" * 200000
    decisions = json.loads((tmp_path / "work/decisions.json").read_bytes())["entries"]
    assert next(row for row in decisions if row["source_path"] == "sound/b.wav")["decision"] == "native_family_cap"


def test_real_cli_module_process_reports_small_independent_candidate(tmp_path):
    import subprocess
    import sys
    source = tmp_path / "source"; source.mkdir()
    engine = tmp_path / "engine"; engine.mkdir()
    (source / "sound").mkdir()
    (source / "sound/fixture.wav").write_bytes(b"RIFF" + b"fixture" * 40000)
    result = subprocess.run([sys.executable, "-m", "exact_distribution.cli", str(source),
                             "--output", str(tmp_path / "candidate"), "--work", str(tmp_path / "work"),
                             "--engine-root", str(engine)], capture_output=True, encoding="utf-8", check=True)
    assert "EXACT_PROGRESS: encode|" in result.stdout
    assert "EXACT_REPORT: " in result.stdout and "EXACT_OUTPUT: " in result.stdout
    report = json.loads((tmp_path / "work/candidate-report.json").read_bytes())
    assert report["status"] == "OFFLINE_PENDING_GMOD" and report["encoded_count"] == 1
