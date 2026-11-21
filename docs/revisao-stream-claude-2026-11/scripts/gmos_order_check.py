"""Strip order check (review/claude lab): the meshes the runtime hands to the game from the streams of lab/<old>
and lab/<new> (same models, triangles in another order), recorded in LuaJIT with one runtime. Per model: the
multiset of triangles (winding kept) by position, normal and UV must be the same; tangents are sums over the
triangles of each vertex, so a different order can move the 2-byte code of a few: counted, with the angle.
python gmos_order_check.py <runtime.lua> <old tag> <new tag> [n|all]"""
import json
import math
import sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
LAB = Path(r"C:/GModLab/revisao/claude")
GROUPS = ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]
RT = None


def triangles(meshes):
    """(multiset of triangles by pos/normal/uv, {vertex key: tangent})"""
    tris = Counter()
    tan = {}
    for d in meshes:
        n = len(d) // 12
        verts = [tuple(d[12 * k:12 * k + 12]) for k in range(n)]
        for k in range(n - 2):
            a, b, c = verts[k], verts[k + 1], verts[k + 2]
            ka, kb, kc = a[:8], b[:8], c[:8]
            if ka == kb or kb == kc or ka == kc:
                continue
            t = (ka, kb, kc) if k % 2 == 0 else (kb, ka, kc)
            i = t.index(min(t))
            tris[t[i:] + t[:i]] += 1
        for v in verts:
            tan[v[:8]] = v[8:]
    return tris, tan


def job(args):
    old, new = args
    import gmos_equal as E
    src = Path(RT).read_bytes()
    _t, ga = E.build(src, Path(old).read_bytes(), True)
    _t, gb = E.build(src, Path(new).read_bytes(), True)
    ta, xa = triangles(E.meshes(ga))
    tb, xb = triangles(E.meshes(gb))
    same = ta == tb
    moved, worst = 0, 0.0
    for k, v in xa.items():
        w = xb.get(k)
        if w is not None and w != v:
            moved += 1
            dot = max(-1.0, min(1.0, sum(p * q for p, q in zip(v[:3], w[:3]))))
            worst = max(worst, math.degrees(math.acos(dot)) if v[3] == w[3] else 180.0)
    return dict(name=Path(old).name, same=same, tris=sum(ta.values()), verts=len(xa), moved=moved, worst=worst,
                keys_same=set(xa) == set(xb))


def init(rt):
    global RT
    RT = rt


def main():
    rt, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
    n = sys.argv[4] if len(sys.argv) > 4 else "all"
    tasks = []
    if Path(old).is_absolute():                     # two flat folders of streams with the same names
        tasks = [(str(p), str(Path(new) / p.name)) for p in Path(old).iterdir()]
        old, new = Path(old).name, Path(new).name
    for g in ([] if tasks else GROUPS):
        ro = LAB / old / g / f"{g}_merged_optimized" / "data_static" / "gmo"
        rn = LAB / new / g / f"{g}_merged_optimized" / "data_static" / "gmo"
        for p in ro.iterdir():
            tasks.append((str(p), str(rn / p.name)))
    tasks.sort(key=lambda t: -Path(t[0]).stat().st_size)
    if n != "all":
        tasks = tasks[:int(n)]
    with Pool(6, initializer=init, initargs=(rt,)) as pool:
        out = list(pool.imap_unordered(job, tasks, chunksize=4))
    bad = [r["name"] for r in out if not (r["same"] and r["keys_same"])]
    verts = sum(r["verts"] for r in out)
    moved = sum(r["moved"] for r in out)
    print(f"{len(out)} streams, {sum(r['tris'] for r in out)} triangles: different triangle sets in {len(bad)} {bad[:5]}")
    print(f"tangent codes moved: {moved} of {verts} vertices ({100 * moved / max(verts, 1):.4f}%), "
          f"worst {max((r['worst'] for r in out), default=0):.2f} deg")
    (LAB / "out" / f"gmos_order_check_{old}_{new}.json").write_text(json.dumps(out, indent=0))


if __name__ == "__main__":
    main()
