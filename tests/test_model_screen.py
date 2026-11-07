"""Tests for the Models "Tela" engine (model_screen)."""
from __future__ import annotations

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
