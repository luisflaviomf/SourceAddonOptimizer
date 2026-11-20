"""Bench with alpha without noise filling (lab/<tag>_anf) against the baseline (lab/<tag>), per group:
- every file compared by content; stream blobs matched by texture (their folder holds the content hash)
- every streamed texture with alpha: alpha and colour quality in game (codec.lua_vtf decoded back) against the
  encoder input dumped earlier (lab/dumpimg; the inputs do not depend on this change), old blob vs new blob
python compare_anf.py <tag> -> out/compare_anf_<tag>.json
"""
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))
LAB = Path(r"C:/GModLab/revisao/claude")
GROUPS = ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]


def h(p):
    return hashlib.sha1(p.read_bytes()).hexdigest()


def stream_rows(tag, g):
    rep = json.loads((LAB / "out" / f"texscreen_{tag}_{g}.json").read_text(encoding="utf-8"))["stream"]
    root = LAB / tag / g / f"{g}_merged_optimized"
    return {r["path"].lower(): (root / "data_static" / "gmot" / rep["slug"] / f"{r['i']}{rep['blob_ext']}", r)
            for r in rep["rows"]}, rep["slug"]


def files(tag, g, slug):
    root = LAB / tag / g / f"{g}_merged_optimized"
    out = {}
    for p in root.rglob("*"):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            out[rel.replace(slug, "<slug>")] = p
    return out


def quality(task):
    png, old, new, normal = task
    import texeval
    from texture_stream import codec
    from texlab.vtf import decode_level, read_vtf
    ref = np.asarray(Image.open(png).convert("RGBA")).astype(np.float64)
    out = {}
    for name, blob in (("old", old), ("new", new)):
        data = Path(blob).read_bytes()
        fin = decode_level(read_vtf(codec.lua_vtf(data)), 0).astype(np.float64)
        m = texeval.metrics(ref, fin, normal, alpha=True)
        out[name + "_a"] = m["a_psnr"]
        out[name + "_rgb"] = m["psnr"]
        out[name + "_size"] = len(data)
    return out


def main():
    tag = sys.argv[1]
    import texeval
    report = {}
    tasks = []
    for g in GROUPS:
        old_rows, old_slug = stream_rows(tag, g)
        new_rows, new_slug = stream_rows(tag + "_anf", g)
        fo, fn = files(tag, g, old_slug), files(tag + "_anf", g, new_slug)
        only = sorted(set(fo) ^ set(fn))
        diff = sorted(k for k in set(fo) & set(fn) if fo[k].stat().st_size != fn[k].stat().st_size or h(fo[k]) != h(fn[k]))
        kinds = {}
        for k in diff:
            kind = ("gmot blob" if k.startswith("data_static/gmot/") else "gmot runtime" if k.startswith("lua/autorun/!gmot_")
                    else "png" if k.lower().endswith(".png") else "other")
            kinds[kind] = kinds.get(kind, 0) + 1
        # blobs that changed: alpha kept?
        changed_alpha = changed_noalpha = 0
        for path, (ob, r) in old_rows.items():
            nb, rn = new_rows[path]
            same = ob.stat().st_size == nb.stat().st_size and h(ob) == h(nb)
            if not same:
                if r["alpha"]:
                    changed_alpha += 1
                else:
                    changed_noalpha += 1
        report[g] = {"files": len(fo), "only_one_side": only[:10], "n_only": len(only), "differ": kinds,
                     "blobs_changed_with_alpha": changed_alpha, "blobs_changed_without_alpha": changed_noalpha,
                     "alpha_blobs": sum(1 for _p, (_b, r) in old_rows.items() if r["alpha"])}
        if tag == "cb":
            # the dumped inputs belong to the Custo-benefício run
            for png, blob, rel, d, r in texeval.rows_of(g):
                if r["alpha"] and rel in old_rows:
                    tasks.append((png, str(old_rows[rel][0]), str(new_rows[rel][0]), bool(r["normal"])))
        print(g, report[g], flush=True)
    if tasks:
        with ProcessPoolExecutor(int(os.environ.get("NPROC", "16"))) as ex:
            q = list(ex.map(quality, tasks, chunksize=4))
        a0 = np.array([x["old_a"] for x in q]); a1 = np.array([x["new_a"] for x in q])
        c0 = np.array([x["old_rgb"] for x in q]); c1 = np.array([x["new_rgb"] for x in q])
        s0 = sum(x["old_size"] for x in q); s1 = sum(x["new_size"] for x in q)
        report["quality"] = {"n": len(q), "alpha_median": [float(np.median(a0)), float(np.median(a1))],
                             "alpha_p10": [float(np.percentile(a0, 10)), float(np.percentile(a1, 10))],
                             "alpha_min": [float(a0.min()), float(a1.min())],
                             "alpha_worse_0.5dB": int((a1 < a0 - 0.5).sum()), "alpha_better_0.5dB": int((a1 > a0 + 0.5).sum()),
                             "alpha_worst_change": float((a1 - a0).min()),
                             "rgb_max_abs_change": float(np.abs(c1 - c0).max()), "size": [s0, s1]}
        print("quality", report["quality"], flush=True)
        (LAB / "out" / f"compare_anf_rows_{tag}.json").write_text(json.dumps([dict(x, png=t[0]) for x, t in zip(q, tasks)], indent=1))
    (LAB / "out" / f"compare_anf_{tag}.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
