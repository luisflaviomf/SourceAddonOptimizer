"""GMOS v6 (lab/<old>) against v7 (lab/<new>), every stream of the bench: same geometry (positions, UVs,
triangles by model_stream.geometry, the product decoder), same unchanged streams (references, normals, strips),
sizes, and the time of geometry.decode_geometry on both. python gmos_v7_check.py cb_a15 cb_gp"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
sys.path.insert(0, str(Path(__file__).parent))
import gmoslib  # noqa: E402
from model_stream import geometry  # noqa: E402

LAB = Path(r"C:/GModLab/revisao/claude")
GROUPS = ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]


def main():
    old, new = sys.argv[1], sys.argv[2]
    tot = {"old": 0, "new": 0, "n": 0, "t_old": 0.0, "t_new": 0.0, "bad": []}
    per = {}
    for g in GROUPS:
        ro = LAB / old / g / f"{g}_merged_optimized" / "data_static" / "gmo"
        rn = LAB / new / g / f"{g}_merged_optimized" / "data_static" / "gmo"
        fo = {p.name: p for p in ro.iterdir()}
        fn = {p.name: p for p in rn.iterdir()}
        assert set(fo) == set(fn), (g, len(set(fo) ^ set(fn)))
        so = sn = 0
        for name in sorted(fo):
            a, b = fo[name].read_bytes(), fn[name].read_bytes()
            assert a[4] == 6 and b[4] == 7, name
            so += len(a); sn += len(b)
            t0 = time.perf_counter(); ga = geometry.decode_geometry(a); tot["t_old"] += time.perf_counter() - t0
            t0 = time.perf_counter(); gb = geometry.decode_geometry(b); tot["t_new"] += time.perf_counter() - t0
            same = len(ga) == len(gb) and all(np.array_equal(x[k], y[k]) for x, y in zip(ga, gb) for k in ("pos", "uv", "tris"))
            sa, sb = gmoslib.parse(a)["S"], gmoslib.parse(b)["S"]
            same &= all(np.array_equal(sa[k], sb[k]) for k in ("pref", "nref", "ncx", "ncy", "uvref", "idx"))
            if not same:
                tot["bad"].append(f"{g}/{name}")
            tot["n"] += 1
        per[g] = (so, sn)
        tot["old"] += so; tot["new"] += sn
        print(f"{g:9} {len(fo)} streams {so / 2**20:7.2f} -> {sn / 2**20:7.2f} MiB ({100 * (sn / so - 1):+.1f}%)", flush=True)
    print(f"total {tot['n']} streams {tot['old'] / 2**20:.2f} -> {tot['new'] / 2**20:.2f} MiB "
          f"({(tot['new'] - tot['old']) / 2**20:+.2f}); geometry different in {len(tot['bad'])}: {tot['bad'][:5]}; "
          f"decode_geometry {tot['t_old']:.1f} s -> {tot['t_new']:.1f} s")
    (LAB / "out" / f"gmos_v7_check_{old}_{new}.json").write_text(json.dumps({"total": tot, "per": per}, indent=1))


if __name__ == "__main__":
    main()
