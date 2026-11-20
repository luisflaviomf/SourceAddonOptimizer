"""Same blobs, better runtime DXT1: final quality of today's GMOT bytes with texexp.better_dxt1 instead of the Lua
encoder (decoder-only change)."""
import sys, json, os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
LAB = Path(r"C:/GModLab/revisao/claude")

def one(t):
    png, blob, normal, alpha = t
    import texexp, texeval
    from texture_stream import codec
    ref = np.asarray(Image.open(png).convert("RGBA")).astype(np.float64)
    data = Path(blob).read_bytes()
    global ORIG
    if "ORIG" not in globals():
        ORIG = codec.lua_dxt1
    codec.lua_dxt1 = ORIG
    a = texeval.metrics(ref, texexp.final_rgba(codec, data), normal, alpha)
    codec.lua_dxt1 = texexp.better_dxt1
    b = texeval.metrics(ref, texexp.final_rgba(codec, data), normal, alpha)
    codec.lua_dxt1 = ORIG
    return {"png": png, "normal": normal, "lua": a["psnr"], "better": b["psnr"], "lua_ssim": a.get("ssim"), "better_ssim": b.get("ssim")}

if __name__ == "__main__":
    import texeval
    rows = json.load(open(LAB / "out" / "texeval_all_1200.json"))
    allrows = {}
    for g in texeval.GROUPS:
        for png, blob, rel, d, r in texeval.rows_of(g):
            allrows[rel] = (png, blob, bool(r["normal"]), bool(r["alpha"]))
    rows.sort(key=lambda x: x["fin_psnr"])
    worst = [allrows[r["rel"]] for r in rows[:40]]
    rnd = [allrows[r["rel"]] for r in rows[::6]]
    with ProcessPoolExecutor(int(os.environ.get("NPROC", "4"))) as ex:
        W = list(ex.map(one, worst)); R = list(ex.map(one, rnd))
    json.dump({"worst": W, "random": R}, open(LAB / "out" / "dxt_gain.json", "w"), indent=1)
    for name, rs in (("40 worst", W), ("random 200", R)):
        for kind in (False, True):
            s = [r for r in rs if r["normal"] == kind]
            if not s: continue
            d = np.array([r["better"] - r["lua"] for r in s])
            print(f"{name:10} {'normal' if kind else 'colour'} n={len(s)}: final PSNR median {np.median([r['lua'] for r in s]):.2f} -> {np.median([r['better'] for r in s]):.2f}; gain median {np.median(d):+.2f} dB, p90 {np.percentile(d, 90):+.2f}, min {d.min():+.2f}")
