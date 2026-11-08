"""Tests for the Models "Tela" engine (model_screen)."""
from __future__ import annotations

import json
import shutil
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from model_screen import simplify as S  # noqa: E402
from model_screen.binary import mesh_arrays, rewrite_model  # noqa: E402

LVS = Path(r"C:/WorkshopDL/steamcmd/steamapps/workshop/content/4000/LVS/lvs_cars_pack")
SAMPLE = LVS / "models/diggercars/caterham_620r"
PROBE = ROOT / "model_stream" / "probe"     # tiny real compiled model shipped with the stream runtime


def test_components_follow_edges():
    edges = np.array([[0, 1], [1, 2], [3, 4], [6, 5]])
    lab = S.components(7, edges)
    assert lab[0] == lab[1] == lab[2]
    assert lab[3] == lab[4] and lab[3] != lab[0]
    assert lab[5] == lab[6] and len({lab[0], lab[3], lab[5]}) == 3


def test_douglas_peucker_keeps_only_corners_of_a_square_path():
    pts = np.array([[0, 0, 0], [0.5, 0, 0], [1, 0, 0], [1, 0.5, 0], [1, 1, 0]], float)
    keep = S.douglas_peucker(pts, 1e-3)
    assert keep.tolist() == [True, False, True, False, True]


def test_profiles_are_ordered():
    b, s = S.PROFILES["balanced"], S.PROFILES["strong"]
    assert s.px > b.px and s.normal_deg > b.normal_deg and s.uv_texels > b.uv_texels
    assert s.drop_tangents and not b.drop_tangents


@pytest.mark.skipif(not SAMPLE.exists(), reason="LVS sample models not on this machine")
@pytest.mark.parametrize("profile", ["balanced", "strong"])
def test_real_model_is_simplified_and_still_loads(tmp_path, profile):
    from texlab.source_model import load_model
    mdl = next(SAMPLE.glob("caterham.mdl"))
    new, st = S.simplify_model(mdl, S.PROFILES[profile])
    assert st["vertices_after"] < st["vertices_before"]
    before = {key: len(t) for key, _s, _n, t in mesh_arrays(mdl)}
    assert all(len(new[k]) > 0 for k, n in before.items() if n > 0)   # no mesh vanished
    rewrite_model(mdl, tmp_path, new, drop_tangents=S.PROFILES[profile].drop_tangents)
    shutil.copy2(mdl.with_suffix(".phy"), tmp_path / (mdl.stem + ".phy"))
    a, b = load_model(mdl), load_model(tmp_path / mdl.name)
    assert len(a.meshes) == len(b.meshes)
    tstart = struct.unpack_from("<i", (tmp_path / (mdl.stem + ".vvd")).read_bytes(), 60)[0]
    assert (tstart == 0) == S.PROFILES[profile].drop_tangents


@pytest.mark.skipif(not SAMPLE.exists(), reason="LVS sample models not on this machine")
def test_cli_end_to_end(tmp_path):
    from model_screen.cli import main
    addon = tmp_path / "addon"
    (addon / "models/diggercars").mkdir(parents=True)
    shutil.copytree(SAMPLE, addon / "models/diggercars/caterham_620r")
    assert main([str(addon), "--suffix", "_o", "--profile", "strong", "--jobs", "2"]) == 0
    out = tmp_path / "addon_o"
    assert (out / "models/diggercars/caterham_620r/caterham.mdl").exists()
    assert not list(out.rglob("*.dx80.vtx"))
    summary = (tmp_path / "addon_o_work" / "logs" / "modscreen_summary.json").read_text()
    assert '"failed": 0' in summary
    # an existing output is never replaced without --overwrite
    assert main([str(addon), "--suffix", "_o", "--profile", "strong"]) == 2


# ----------------------------------------------------------------------------- "exact" (Sem perda)

def _tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _packaging_only(models: Path) -> None:
    """modscreen's Step 3/3 on its own: everything the exact profile is allowed to change."""
    import vtx_tristrip_optimizer
    import vvd_engine_exact
    from model_screen.cli import _dll
    for pat in ("*.dx80.vtx", "*.sw.vtx"):
        for p in models.rglob(pat):
            p.unlink()
    vvd_engine_exact.optimize_tree(models)
    vtx_tristrip_optimizer.optimize_tree(models, _dll("vtx", "meshopt_stripify.dll"))


def _run_exact(tmp_path: Path, addon: Path, capsys) -> dict:
    from model_screen.cli import main
    expected = tmp_path / "expected"
    shutil.copytree(addon, expected)
    _packaging_only(expected / "models")
    assert main([str(addon), "--suffix", "_o", "--profile", "exact", "--jobs", "2"]) == 0
    out = addon.parent / (addon.name + "_o")
    # same bytes as a plain copy followed by the packaging steps: no geometry was simplified or rewritten
    assert _tree(out) == _tree(expected)
    for mdl in (addon / "models").rglob("*.mdl"):         # packaging never touches the .mdl files
        assert (out / mdl.relative_to(addon)).read_bytes() == mdl.read_bytes()
    assert not list(out.rglob("*.dx80.vtx")) and not list(out.rglob("*.sw.vtx"))
    text = capsys.readouterr().out
    n = len(list((addon / "models").rglob("*.mdl")))
    assert f">>> DONE ({n}/{n})" in text and "Step 3/3: Packaging" in text and "[WARN]" not in text
    summary = json.loads((addon.parent / (addon.name + "_o_work") / "logs" / "modscreen_summary.json").read_text())
    assert summary["profile"] == "exact" and summary["models"] == n and summary["failed"] == 0
    assert summary["vertices_before"] == summary["vertices_after"]
    assert summary["models_bytes_after"] < summary["models_bytes_before"]
    assert all(r["bytes_before"] == r["bytes_after"] for r in summary["rows"])
    return summary


def test_exact_profile_is_a_copy_plus_packaging(tmp_path, capsys):
    assert S.PROFILES["exact"].engine == "copy"
    addon = tmp_path / "addon"
    m = addon / "models" / "probe"
    m.mkdir(parents=True)
    for ext in (".mdl", ".vvd", ".dx90.vtx"):
        shutil.copy2(PROBE / ("probe_v1" + ext), m / ("probe_v1" + ext))
    for ext in (".dx80.vtx", ".sw.vtx"):
        shutil.copy2(PROBE / "probe_v1.dx90.vtx", m / ("probe_v1" + ext))
    (m / "probe_v1.phy").write_bytes(b"physics, kept as it is")
    # a model no engine can read: the exact profile never parses geometry, so it is kept, not "failed"
    for ext in (".mdl", ".vvd", ".dx90.vtx"):
        (m / ("broken" + ext)).write_bytes(b"not a model" + ext.encode())
    (addon / "materials" / "probe").mkdir(parents=True)
    shutil.copy2(PROBE / "probe_v1.vmt", addon / "materials" / "probe" / "probe_v1.vmt")
    summary = _run_exact(tmp_path, addon, capsys)
    assert summary["models"] == 2 and summary["streamed"] == 0


@pytest.mark.skipif(not SAMPLE.exists(), reason="LVS sample models not on this machine")
def test_exact_profile_on_a_real_car(tmp_path, capsys):
    addon = tmp_path / "addon"
    shutil.copytree(SAMPLE, addon / "models/diggercars/caterham_620r")
    summary = _run_exact(tmp_path, addon, capsys)
    assert summary["vertices_before"] > 0
