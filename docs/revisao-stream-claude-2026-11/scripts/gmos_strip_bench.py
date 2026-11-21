"""Strip order on the whole bench (review/claude lab): every streamed model of cb_gp encoded as now and with the
triangles reordered by meshoptimizer's optimizeVertexCacheStrip before stripify (Python binding of the lab).
Bytes, strip places, and both streams written to lab/stripexp/full/<stock|vc>/<group>/<name>.
python gmos_strip_bench.py [processes]"""
import hashlib
import json
import shutil
import sys
from multiprocessing import Pool
from pathlib import Path

LAB = Path(r"C:/GModLab/revisao/claude")
GROUPS = ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]


def job(args):
    g, stream_path, mdl_path = args
    import os
    sys.path.insert(0, str(Path(__file__).parent))
    import gmos_strip_exp as X
    from model_stream import encoder
    from model_screen.binary import flatten_lods
    src = Path(mdl_path)
    tmp = LAB / "work" / f"stripbench_{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    for q in src.parent.glob(src.stem + ".*"):
        shutil.copyfile(q, tmp / q.name)
    mdl = tmp / src.name
    flatten_lods(mdl)
    encoder.stripify = X.STOCK
    a = encoder.encode(mdl, None)
    encoder.stripify = X.vcache_stripify
    b = encoder.encode(mdl, None)
    encoder.stripify = X.STOCK
    name = Path(stream_path).name
    for tag, data in (("stock", a), ("vc", b)):
        q = LAB / "stripexp" / "full" / tag / g / name
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(data)
    return dict(g=g, name=name, shipped=Path(stream_path).stat().st_size, stock=len(a), vc=len(b),
                p_stock=X.places(a), p_vc=X.places(b))


def main():
    nproc = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    tasks = []
    for g in GROUPS:
        root = LAB / "src" / g / f"{g}_merged"
        by_hash = {}
        for p in (root / "models").rglob("*.mdl"):
            rel = p.relative_to(root).as_posix()
            by_hash[hashlib.sha1(rel.lower().encode()).hexdigest()[:16]] = p
        for f in (LAB / "cb_gp" / g / f"{g}_merged_optimized" / "data_static" / "gmo").iterdir():
            tasks.append((g, str(f), str(by_hash[f.stem])))
    tasks.sort(key=lambda t: -Path(t[1]).stat().st_size)
    out = []
    with Pool(nproc) as pool:
        for k, r in enumerate(pool.imap_unordered(job, tasks, chunksize=1)):
            out.append(r)
            if k % 200 == 0:
                print(k, len(tasks), flush=True)
    (LAB / "out" / "strip_bench.json").write_text(json.dumps(out, indent=1))
    T = {k: sum(r[k] for r in out) for k in ("shipped", "stock", "vc", "p_stock", "p_vc")}
    print({k: (round(v / 2**20, 3) if k in ("shipped", "stock", "vc") else v) for k, v in T.items()})
    print(f"bytes {100 * (T['vc'] / T['stock'] - 1):+.2f}% ({(T['vc'] - T['stock']) / 2**20:+.2f} MiB)  "
          f"places {100 * (T['p_vc'] / T['p_stock'] - 1):+.2f}%")
    worse = sorted(out, key=lambda r: r["stock"] - r["vc"])[:5]
    print("largest losses:", [(r["name"], r["stock"], r["vc"]) for r in worse])


if __name__ == "__main__":
    main()
