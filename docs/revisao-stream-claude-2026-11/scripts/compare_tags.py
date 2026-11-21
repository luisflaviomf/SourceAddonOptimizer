"""Two bench outputs (lab/<old>, lab/<new>) compared per group: files by content (stream blobs matched by
texture, their folder holds a content hash), GMOT bytes, and with --quality the alpha and colour quality in game
of every streamed texture with alpha against the dumped encoder input (Custo-benefício runs only).
python compare_tags.py <old> <new> [--quality] -> out/compare_<old>_<new>.json"""
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from compare_anf import GROUPS, LAB, files, h, quality, stream_rows  # noqa: E402


def main():
    old, new = sys.argv[1], sys.argv[2]
    do_q = "--quality" in sys.argv
    import texeval
    report, tasks = {}, []
    for g in GROUPS:
        orow, oslug = stream_rows(old, g)
        nrow, nslug = stream_rows(new, g)
        fo, fn = files(old, g, oslug), files(new, g, nslug)
        diff = sorted(k for k in set(fo) & set(fn) if fo[k].stat().st_size != fn[k].stat().st_size or h(fo[k]) != h(fn[k]))
        kinds = {}
        for k in diff:
            kind = ("gmot blob" if k.startswith("data_static/gmot/") else "gmot runtime" if k.startswith("lua/autorun/!gmot_")
                    else k.rsplit(".", 1)[-1].lower())
            kinds[kind] = kinds.get(kind, 0) + 1
        ch_a = sum(1 for p, (b, r) in orow.items() if r["alpha"] and (b.stat().st_size != nrow[p][0].stat().st_size or h(b) != h(nrow[p][0])))
        ch_n = sum(1 for p, (b, r) in orow.items() if not r["alpha"] and (b.stat().st_size != nrow[p][0].stat().st_size or h(b) != h(nrow[p][0])))
        report[g] = {"only_one_side": len(set(fo) ^ set(fn)), "differ": kinds, "changed_with_alpha": ch_a,
                     "changed_without_alpha": ch_n, "alpha_blobs": sum(1 for _p, (_b, r) in orow.items() if r["alpha"]),
                     "gmot_old": sum(b.stat().st_size for b, _r in orow.values()),
                     "gmot_new": sum(b.stat().st_size for b, _r in nrow.values())}
        print(g, report[g], flush=True)
        if do_q:
            for png, _blob, rel, d, r in texeval.rows_of(g):
                if r["alpha"] and rel in orow:
                    tasks.append((png, str(orow[rel][0]), str(nrow[rel][0]), bool(r["normal"])))
    if tasks:
        with ProcessPoolExecutor(int(os.environ.get("NPROC", "16"))) as ex:
            q = list(ex.map(quality, tasks, chunksize=4))
        a0 = np.array([x["old_a"] for x in q]); a1 = np.array([x["new_a"] for x in q])
        c0 = np.array([x["old_rgb"] for x in q]); c1 = np.array([x["new_rgb"] for x in q])
        report["quality"] = {"n": len(q), "alpha_median": [float(np.median(a0)), float(np.median(a1))],
                             "alpha_p10": [float(np.percentile(a0, 10)), float(np.percentile(a1, 10))],
                             "alpha_p1": [float(np.percentile(a0, 1)), float(np.percentile(a1, 1))],
                             "alpha_min": [float(a0.min()), float(a1.min())],
                             "worse_1dB": int((a1 < a0 - 1).sum()), "worse_3dB": int((a1 < a0 - 3).sum()),
                             "worst_change": float((a1 - a0).min()), "rgb_max_abs_change": float(np.abs(c1 - c0).max())}
        print("quality", report["quality"], flush=True)
        (LAB / "out" / f"compare_rows_{old}_{new}.json").write_text(json.dumps([dict(x, png=t[0]) for x, t in zip(q, tasks)], indent=1))
    (LAB / "out" / f"compare_{old}_{new}.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
