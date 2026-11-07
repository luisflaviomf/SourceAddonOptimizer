"""Tests for the Models "Tela" profile strong_mirror (model_screen.remesh + model_screen.mirror)."""
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

from model_screen import mirror as MR  # noqa: E402
from model_screen import remesh as RM  # noqa: E402
from model_screen.binary import parse_vtx, rewrite_model_bytes  # noqa: E402
from model_screen.gridsearch import radius_pairs  # noqa: E402
from model_screen.simplify import PROFILES  # noqa: E402

LVS = Path(r"C:/WorkshopDL/steamcmd/steamapps/workshop/content/4000/LVS")
PACK = LVS / "lvs_cars_pack"
E46 = PACK / "models/diggercars/bmw_e46/e46.mdl"
MONACO = PACK / "models/diggercars/dodge_monaco/monaco_police.mdl"
need_lvs = pytest.mark.skipif(not E46.exists(), reason="LVS sample models not on this machine")


def _resolver():
    from texlab.materials import MaterialResolver
    return MaterialResolver([PACK, LVS / "lvs_framework"])


def test_radius_pairs_matches_brute_force():
    rng = np.random.default_rng(3)
    P = np.round(rng.uniform(-5, 5, (600, 3)), 2)
    P = np.concatenate([P, P[:50]])                      # exact duplicates
    Q = P[rng.choice(len(P), 200)] + rng.normal(0, 0.05, (200, 3))
    qi, pj, dist, rank = radius_pairs(P, Q, 0.12, k=4)
    D = np.sqrt(((Q[:, None, :] - P[None, :, :]) ** 2).sum(2))
    for i in range(len(Q)):
        cand = np.flatnonzero(D[i] <= 0.12)
        cand = cand[np.lexsort((cand, D[i, cand]))][:4]
        assert pj[qi == i].tolist() == cand.tolist()
        assert rank[qi == i].tolist() == list(range(len(cand)))


def test_strong_mirror_has_forte_limits():
    s, m = PROFILES["strong"], PROFILES["strong_mirror"]
    assert (m.px, m.normal_deg, m.uv_texels, m.rel_size, m.drop_tangents) == \
        (s.px, s.normal_deg, s.uv_texels, s.rel_size, s.drop_tangents)
    assert m.engine == "remesh" and s.engine == "seams"


@need_lvs
def test_append_bones_keeps_tables_consistent():
    raw = E46.read_bytes()
    before = MR.read_bones(raw)
    M = MR.reflection(1)
    new = [dict(name="Car~Car", parent=0, ptb=(np.vstack([before[0]["ptb"], [0, 0, 0, 1]]) @ M)[:3], template=0)]
    out = MR.append_bones(raw, new)
    after = MR.read_bones(out)
    assert [b["name"] for b in after[:len(before)]] == [b["name"] for b in before]
    assert after[-1]["name"] == "Car~Car" and after[-1]["parent"] == 0
    assert np.allclose(after[-1]["ptb"][:, :3], before[0]["ptb"][:, :3] @ M[:3, :3])
    assert struct.unpack_from("<i", out, 76)[0] == len(out)
    nb = len(after)
    tbl = struct.unpack_from("<i", out, 364)[0]
    assert sorted(out[tbl:tbl + nb]) == list(range(nb))
    h2 = struct.unpack_from("<i", out, 400)[0]
    lb = h2 + struct.unpack_from("<i", out, h2 + 16)[0]
    assert struct.unpack_from("<i", out, lb)[0] == nb
    nseq, si = struct.unpack_from("<ii", out, 188)
    for s in range(nseq):
        so = si + s * MR.SEQ
        w = struct.unpack_from(f"<{nb}f", out, so + struct.unpack_from("<i", out, so + 156)[0])
        assert w[-1] == w[0]


def _build(mdl, resolver):
    from model_screen.uvtol import mesh_uv_tolerance
    p = PROFILES["strong_mirror"]
    plan = RM.build(mdl, p, mesh_uv_tolerance(mdl, resolver), RM.mesh_uv_free(mdl, resolver), resolver)
    m, v, x, _ = rewrite_model_bytes(mdl, plan["main"], drop_tangents=True)
    m, x, nbones = MR.apply(m, x, plan)
    return plan, m, v, x, nbones


@need_lvs
def test_e46_is_mirrored_deterministic_and_loads(tmp_path):
    from texlab.source_model import load_model
    res = _resolver()
    plan, m, v, x, nbones = _build(E46, res)
    st = plan["stats"]
    assert st["vertices_after"] < 0.7 * st["vertices_before"]
    assert st["mirrored_out"] > 1000 and nbones >= 2 and plan["axis"] == 1
    plan2, m2, v2, x2, _ = _build(E46, res)
    assert (m, v, x) == (m2, v2, x2)                                # same bytes every run
    for ext, data in ((".mdl", m), (".vvd", v), (".dx90.vtx", x)):
        (tmp_path / ("e46" + ext)).write_bytes(data)
    shutil.copy2(E46.with_suffix(".phy"), tmp_path / "e46.phy")
    model = load_model(tmp_path / "e46.mdl")
    assert len(model.meshes) == len(load_model(E46).meshes)
    vtx = parse_vtx(x)
    two = [g for bp in vtx.bodyparts for mo in bp for lod in mo for me in lod.meshes for g in me.groups if len(g.strips) == 2]
    assert len(two) == len(plan["mirror"])
    bones = MR.read_bones(m)
    for g in two:                                                    # mirrored strips only use mirror bones
        ch = g.strips[1].bone_changes
        assert all("~" in bones[struct.unpack_from("<ii", ch, j * 8)[1]]["name"] for j in range(len(ch) // 8))


@need_lvs
def test_mirror_images_land_on_the_original_right_side():
    from model_screen.binary import mesh_arrays
    from model_screen.simplify import vvd_data
    plan, *_ = _build(E46, _resolver())
    f, _ = vvd_data(E46)
    P = f[:, :3].astype(np.float64)
    starts = {k: st for k, st, _n, _t in mesh_arrays(E46)}
    pts = np.concatenate([P[t + starts[k]].reshape(-1, 3) for k, t in plan["mirror"].items()])
    pts[:, plan["axis"]] = 2 * plan["c"] - pts[:, plan["axis"]]
    qi, *_ = radius_pairs(P, pts, 0.06, k=1)
    assert len(np.unique(qi)) == len(pts)                           # every image corner is a real right-side point


@pytest.mark.skipif(not MONACO.exists(), reason="LVS sample models not on this machine")
def test_refract_tint_texture_counts_as_uv_texture():
    # the Monaco lightbar dome (Refract, red/blue $refracttinttexture) must keep its UV and never be mirrored blindly
    from texlab.source_model import _read_mdl
    res = _resolver()
    free = RM.mesh_uv_free(MONACO, res)
    _v, _n, _c, textures, _cd, _sk, bodyparts = _read_mdl(MONACO)
    slots = [mat for _b, models in bodyparts for _m, _nv, _vi, meshes in models for mat, _x, _y in meshes]
    refract = [i for i, s in enumerate(slots) if textures[s].lower() == "twin_sonic_chp_refract"]
    assert refract and not any(free[i] for i in refract)


@need_lvs
def test_cli_end_to_end_strong_mirror(tmp_path):
    from model_screen.cli import main
    addon = tmp_path / "lvs" / "addon"
    (addon / "models/diggercars").mkdir(parents=True)
    shutil.copytree(E46.parent, addon / "models/diggercars/bmw_e46")
    # the materials the models use (the framework beside it is found as a sibling addon)
    shutil.copytree(PACK / "materials/models/diggercars/bmw_e46", addon / "materials/models/diggercars/bmw_e46")
    shutil.copytree(LVS / "lvs_framework/materials/models/diggercars/shared",
                    tmp_path / "lvs" / "framework/materials/models/diggercars/shared")
    assert main([str(addon), "--suffix", "_o", "--profile", "strong_mirror", "--jobs", "2"]) == 0
    summary = json.loads((tmp_path / "lvs" / "addon_o_work" / "logs" / "modscreen_summary.json").read_text())
    assert summary["failed"] == 0 and summary["profile"] == "strong_mirror"
    row = next(r for r in summary["rows"] if r["model"].endswith("e46.mdl"))
    assert row["mirror_bones"] >= 2 and row["mirrored_triangles"] > 1000
    assert not list((tmp_path / "lvs" / "addon_o").rglob("*.dx80.vtx"))
