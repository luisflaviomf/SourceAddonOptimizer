"""Worker boundary: real CLI routing, Unicode arguments, progress and failure."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_worker_exactpack_exports_actual_input_from_unrelated_cwd(tmp_path):
    source = tmp_path / "entrada ação"
    source.mkdir()
    (source / "models").mkdir()
    (source / "models/a.dx80.vtx").write_bytes(b"legacy")
    (source / "models/a.dx90.vtx").write_bytes(b"native")
    engine = tmp_path / "engine"; engine.mkdir()
    output = tmp_path / "candidato_novo"
    work = tmp_path / "relatório"
    result = subprocess.run([sys.executable, str(ROOT / "worker/worker_main.py"), "exactpack",
        str(source), "--output", str(output), "--work", str(work), "--engine-root", str(engine)],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "EXACT_PROGRESS:" in result.stdout
    assert f"EXACT_REPORT: {work / 'candidate-report.json'}" in result.stdout
    assert f"EXACT_OUTPUT: {output}" in result.stdout
    report = json.loads((work / "candidate-report.json").read_bytes())
    assert report["status"] == "OFFLINE_PENDING_GMOD"
    assert report["excluded"]["bytes"] == 6
    assert (output / "models/a.dx90.vtx").read_bytes() == b"native"
    assert (source / "models/a.dx80.vtx").exists()


def test_worker_exactpack_existing_output_never_emits_success(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    (source / "file.txt").write_bytes(b"untouched")
    engine = tmp_path / "engine"; engine.mkdir()
    output = tmp_path / "out"; output.mkdir()
    result = subprocess.run([sys.executable, str(ROOT / "worker/worker_main.py"), "exactpack",
        str(source), "--output", str(output), "--work", str(tmp_path / "work"),
        "--engine-root", str(engine)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode != 0
    assert "EXACT_OUTPUT:" not in result.stdout
    assert list(output.iterdir()) == []
