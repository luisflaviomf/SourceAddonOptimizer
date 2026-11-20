"""Lua (LuaJIT via lupa) check and timing of the context-coded bands against the current GMOT decode.

For each sampled blob: every chunk is coded by rust/gmotcm (cm_chunk_bytes), decoded by cm_decoder.lua and
compared with the stored coefficients (and noise codes); times: the whole current GMOT.Decode (decoder.lua,
full size, util.Decompress = Python's liblzma), the LZMA decompression of all its chunks alone, and the Lua CM
decode of all chunks. python cm_lua_bench.py <tag> [n] -> out/cmlua_<tag>.json
"""
import ctypes
import json
import lzma
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import cm_real  # noqa: E402
import gmotlib  # noqa: E402

LAB = Path(r"C:/GModLab/revisao/claude")
REPO = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
CFG = np.array([12, 4, 1, 1, 0], np.int32)


def lua_runtime():
    from lupa import luajit21
    rt = luajit21.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    rt.globals().SysTime = time.perf_counter
    rt.execute(b"util = {}")
    rt.globals().util.Decompress = lambda s: gmotlib.unlzma(bytes(s))
    rt.execute((REPO / "texture_stream" / "decoder.lua").read_bytes())
    rt.execute((Path(__file__).parent / "cm_decoder.lua").read_bytes())
    rt.execute((Path(__file__).parent / "cm_decoder2.lua").read_bytes())
    rt.execute((Path(__file__).parent / "cm_decoder3.lua").read_bytes())
    rt.execute(b"ABORTS = 0; jit.attach(function(what) if what == 'abort' then ABORTS = ABORTS + 1 end end, 'trace')")
    return rt


def coded_chunks(g):
    lib = cm_real.lib()
    lib.cm_chunk_bytes.restype = ctypes.c_ssize_t
    V = ctypes.c_void_p
    lib.cm_chunk_bytes.argtypes = [V, ctypes.c_int, V, ctypes.c_size_t, V, ctypes.c_size_t, V, V, ctypes.c_size_t]
    chunks = {}
    for b in g["bands"]:
        chunks.setdefault(b["chunk"], []).append(b)
    out = []
    for c in sorted(chunks):
        bl = [b for b in chunks[c] if not (b["kind"] == 0 and b["codes"] is None)]
        for b in bl:
            b["class"] = (min(b["plane"], 1) * 4 + b["k"]) if c == 0 else 0
        if not bl:
            continue
        meta = np.array([[b["q"].shape[0], b["q"].shape[1], b["class"], 1 if b["k"] == 0 else 0,
                          1 if b["codes"] is not None else 0, -1] for b in bl], np.int32)
        q = np.ascontiguousarray(np.concatenate([b["q"].ravel() for b in bl]).astype(np.int32))
        cl = [b["codes"].ravel() for b in bl if b["codes"] is not None]
        codes = np.ascontiguousarray(np.concatenate(cl).astype(np.uint8)) if cl else np.zeros(1, np.uint8)
        buf = np.zeros(q.size * 4 + 4096, np.uint8)
        n = lib.cm_chunk_bytes(meta.ctypes.data, len(bl), q.ctypes.data, q.size, codes.ctypes.data,
                               int(sum(x.size for x in cl)), CFG.ctypes.data, buf.ctypes.data, buf.size)
        assert n > 0
        out.append((buf[:n].tobytes(), bl, max(b["class"] for b in bl) + 1))
    return out


def main():
    tag = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    blobs = sorted(p for p in (LAB / tag).rglob("data_static/gmot/*/*") if p.is_file())
    # biggest first: the decode cost that matters is the one of large textures
    blobs.sort(key=lambda p: -p.stat().st_size)
    pick = blobs[:n // 2] + blobs[n // 2::max(1, len(blobs) // n)][:n - n // 2]
    rt = lua_runtime()
    G = rt.globals()
    rows = []
    for p in pick:
        data = p.read_bytes()
        g = gmotlib.parse(data)
        # current full decode in Lua
        reps = int(__import__("os").environ.get("REPS", "1"))
        tf = []
        for _ in range(reps):
            t0 = time.perf_counter()
            G.GMOT.Decode(data, False)
            tf.append(time.perf_counter() - t0)
        t_full, t_full_cold = min(tf), tf[0]
        # LZMA of all chunks alone
        nch = data[5]
        sizes = np.frombuffer(data, "<u4", nch, 6)
        offs = np.cumsum([6 + 4 * nch] + list(sizes))
        tl = []
        for _ in range(reps):
            t0 = time.perf_counter()
            for c in range(nch):
                gmotlib.unlzma(data[offs[c]:offs[c] + sizes[c]])
            tl.append(time.perf_counter() - t0)
        t_lzma = min(tl)
        # CM decode in Lua, checked
        t_cm, t_cm_cold, ncoef, ok = 0.0, 0.0, 0, True
        for coded, bl, ncl in coded_chunks(g):
            shapes = rt.table_from([rt.table_from({b"h": b["q"].shape[0], b"w": b["q"].shape[1], b"class": b["class"],
                                                   b"ll": b["k"] == 0, b"codes": b["codes"] is not None, b"copy": __import__("os").environ.get("CMCOPY", "1") == "1"})
                                    for b in bl])
            dec = {"1": G.CM, "2": G.CM2, "3": G.CM3}[__import__("os").environ.get("CMV", "3")]
            tc = []
            for _ in range(reps):
                t0 = time.perf_counter()
                res = dec.DecodeChunk(coded, shapes, ncl)
                tc.append(time.perf_counter() - t0)
            t_cm += min(tc)
            t_cm_cold += tc[0]
            if not __import__("os").environ.get("CMCOPY", "1") == "1":
                ncoef += sum(b["q"].size for b in bl)
                continue
            for k, b in enumerate(bl, start=1):
                r = res[k]; r = {"vals": r[b"vals"], "codes": r[b"codes"]}
                vals = np.array(list(r["vals"].values()), np.int32) if b["q"].size else np.zeros(0, np.int32)
                if not np.array_equal(vals, b["q"].ravel()):
                    ok = False
                if b["codes"] is not None and list(r["codes"].values()) != b["codes"].ravel().tolist():
                    ok = False
                ncoef += b["q"].size
        rows.append({"path": str(p.relative_to(LAB)), "file": len(data), "side": max(g["w"], g["h"]), "n": g["n"],
                     "t_full": t_full, "t_full_cold": t_full_cold, "t_lzma": t_lzma, "t_cm": t_cm, "t_cm_cold": t_cm_cold, "coef": ncoef, "ok": ok})
        print(f"{p.name:10} {max(g['w'], g['h']):5} n={g['n']} full {t_full * 1000:7.1f} ms  lzma {t_lzma * 1000:6.1f}  "
              f"cm {t_cm * 1000:7.1f} ms  ({ncoef / max(t_cm, 1e-9) / 1e6:.1f} Mcoef/s) ok={ok}", flush=True)
    (LAB / "out" / f"cmlua_{tag.replace('/', '_')}.json").write_text(json.dumps(rows, indent=1))
    T = {k: sum(r[k] for r in rows) for k in ("t_full", "t_full_cold", "t_lzma", "t_cm", "t_cm_cold", "coef")}
    print(f"cold: full {T['t_full_cold']:.2f} s, cm {T['t_cm_cold']:.2f} s -> +{100 * (T['t_cm_cold'] - T['t_lzma']) / T['t_full_cold']:.0f}%")
    print(f"total: full {T['t_full']:.2f} s, lzma {T['t_lzma']:.2f} s, cm {T['t_cm']:.2f} s "
          f"-> decode +{100 * (T['t_cm'] - T['t_lzma']) / T['t_full']:.0f}% (cm replaces lzma); all ok: {all(r['ok'] for r in rows)}; trace aborts {G.ABORTS}")


if __name__ == "__main__":
    main()
