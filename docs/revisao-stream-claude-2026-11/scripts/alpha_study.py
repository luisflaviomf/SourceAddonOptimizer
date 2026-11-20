"""How the kept alpha planes are used, and what a cut-out mask would cost (review/claude lab).

For every streamed texture whose alpha is kept: the materials naming it (VMTs of the addon), whether every one
of them reads the alpha only as an alpha-test cut-out ($alphatest without $translucent, alpha-to-coverage,
$distancealpha, envmap/phong masks in the base alpha, blend-tint, self-illum, detail blend), the GMOT bytes of
its alpha plane, how binary the input alpha is, and the cut-out errors of what the game shows today
(pixels whose side of $alphatestreference differs from the input after the Lua DXT5 alpha).
Mask option: the input alpha cut at the reference, coded losslessly (packed bits + LZMA 9; the runtime would
rebuild 0/255 alpha, DXT5 alpha blocks exact): its size.
python alpha_study.py [groups] -> out/alpha_study.json
"""
import json
import lzma
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
OTHER_ALPHA = ("$translucent", "$allowalphatocoverage", "$distancealpha", "$basemapalphaphongmask",
               "$basealphaenvmapmask", "$blendtintbybasealpha", "$selfillum", "$vertexalpha")


def flag(p, k):
    v = str(p.get(k, "0")).strip().strip('"')
    try:
        return float(v) != 0
    except ValueError:
        return v.lower() in ("true", "yes")


def uses(root):
    """texture ref -> list of (vmt, how): 'cutout' | 'other' | 'none' for how each material reads base alpha"""
    from texlab.materials import MaterialResolver
    from texture_stream.choose import read_vmts
    res = MaterialResolver([root])
    out = {}
    for vref, sh, p in read_vmts(res):
        m = res.material(vref)
        if m is None:
            continue
        for param, tref in m.all_textures():
            how = "other"
            if param == "$basetexture":
                if flag(p, "$alphatest") and not any(flag(p, k) for k in OTHER_ALPHA) and not (
                        "$detail" in p and str(p.get("$detailblendmode", "0")).strip() not in ("0", "1", "4")):
                    ref = p.get("$alphatestreference")
                    how = ("cutout", float(str(ref).strip('"')) if ref is not None else 0.5)
                elif not any(flag(p, k) for k in OTHER_ALPHA) and sh in ("vertexlitgeneric", "lightmappedgeneric", "unlitgeneric") \
                        and not flag(p, "$alphatest"):
                    how = "none"
            out.setdefault(tref, []).append(how)
    return out


def one(task):
    png, blob, kind, refs = task
    import gmotlib
    from texture_stream.codec import lua_vtf
    from texlab.vtf import decode_level, read_vtf
    a = np.asarray(Image.open(png).convert("RGBA"))[..., 3].astype(np.float64)
    data = Path(blob).read_bytes()
    g = gmotlib.parse(data)
    alpha_plane = g["n"] - 1
    b0 = [b for b in g["bands"] if b["chunk"] == 0]
    alone = [gmotlib.lzma_chunk(gmotlib.band_bytes(b)) - 13 for b in b0]
    scale = (g["sizes"][0] - 40) / max(1, sum(alone))
    abytes = sum(s * scale for b, s in zip(b0, alone) if b["plane"] == alpha_plane)
    abytes += sum(g["sizes"][b["chunk"]] for b in g["bands"] if b["chunk"] and b["plane"] == alpha_plane)
    fin = decode_level(read_vtf(lua_vtf(data)), 0)[..., 3].astype(np.float64)
    near = float(((a <= 8) | (a >= 247)).mean())
    res = {"png": png, "w": a.shape[1], "h": a.shape[0], "kind": kind, "file": len(data), "alpha_bytes": abytes,
           "binary_frac": near}
    if kind == "cutout":
        ref = 255.0 * refs
        m_in = a >= ref
        m_out = fin >= ref
        res["cut_err"] = float((m_in != m_out).mean())
        res["mask_bytes"] = len(lzma.compress(np.packbits(m_in).tobytes(), format=lzma.FORMAT_ALONE, preset=9)) - 8
    return res


def main():
    import texeval
    groups = sys.argv[1].split(",") if len(sys.argv) > 1 else texeval.GROUPS
    tasks = []
    for g in groups:
        root = LAB / "dump" / g / f"{g}_merged_optimized"
        u = uses(root)
        for png, blob, rel, d, r in texeval.rows_of(g):
            if not r["alpha"]:
                continue
            ref = rel[len("materials/"):-4] if rel.startswith("materials/") else rel[:-4]
            hows = u.get(ref, [])
            if hows and all(isinstance(h, tuple) for h in hows):
                tasks.append((png, blob, "cutout", min(h[1] for h in hows)))
            elif not hows:
                tasks.append((png, blob, "unnamed", None))
            else:
                tasks.append((png, blob, "other", None))
    with ProcessPoolExecutor(int(os.environ.get("NPROC", "12"))) as ex:
        rows = list(ex.map(one, tasks, chunksize=4))
    (LAB / "out" / "alpha_study.json").write_text(json.dumps(rows, indent=1))
    MiB = 2 ** 20
    for kind in ("cutout", "other", "unnamed"):
        rs = [r for r in rows if r["kind"] == kind]
        if not rs:
            continue
        msg = (f"{kind:8} n={len(rs):5} alpha bytes {sum(r['alpha_bytes'] for r in rs) / MiB:6.2f} MiB of "
               f"{sum(r['file'] for r in rs) / MiB:6.2f}; median binary fraction {np.median([r['binary_frac'] for r in rs]):.3f}")
        if kind == "cutout":
            msg += (f"; today cut-out pixel errors median {np.median([r['cut_err'] for r in rs]):.4f} p90 "
                    f"{np.percentile([r['cut_err'] for r in rs], 90):.4f}; exact mask {sum(r['mask_bytes'] for r in rs) / MiB:.2f} MiB")
        print(msg)


if __name__ == "__main__":
    main()
