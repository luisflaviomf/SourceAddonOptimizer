"""Build time of the product runtime (now reading v6 and v7) on the same models in both formats."""
import sys, time, lzma, os
sys.path.insert(0, r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
sys.path.insert(0, r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116/tests")
from pathlib import Path
from lupa import luajit21
import test_model_stream as T
ROOT = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
LAB = Path(r"C:/GModLab/revisao/claude")
stubs = T.RUNTIME_STUBS.replace("""function mesh.Position(v)
    local n = cur.n
    cur[n + 1], cur[n + 2], cur[n + 3] = v[1], v[2], v[3]
    cur.n = n + 3
end""", "function mesh.Position(v) end").replace("assert(cur.n == (cur.prims + 2) * 3)", "")
def build_time(data):
    rt = luajit21.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    g = rt.globals()
    def dec(s):
        d = bytearray(s); d[5:13] = b"\xff" * 8; return lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)
    g[b"PY_READ"] = lambda p, w: data; g[b"PY_DECOMPRESS"] = dec
    g[b"MODEL"] = b"models/test/car.mdl"; g[b"MESH_LIMIT"] = 65535
    rt.execute(stubs.encode()); rt.execute((ROOT / "model_stream" / "runtime.lua").read_bytes())
    t0 = time.perf_counter()
    rt.execute(b"""GMOS.AddManifest({ [MODEL] = "data_static/gmo/test.dat" }); local ent = NewEntity()
        for _ = 1, 2000000 do if GMOS.Ready(MODEL) then break end; ent:DrawModel(0) end
        assert(GMOS.Ready(MODEL))""")
    return time.perf_counter() - t0
pairs = []
for g in ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]:
    for p in (LAB / "cb_a15" / g / f"{g}_merged_optimized" / "data_static" / "gmo").iterdir():
        pairs.append((p.stat().st_size, p, LAB / "cb_gp" / g / f"{g}_merged_optimized" / "data_static" / "gmo" / p.name))
pairs.sort(reverse=True)
t6 = t7 = 0
for _, a, b in pairs[:12]:
    da, db = a.read_bytes(), b.read_bytes()
    x = min(build_time(da), build_time(da)); y = min(build_time(db), build_time(db))
    t6 += x; t7 += y
print(f"12 biggest models: runtime build {t6:.2f} s (v6) -> {t7:.2f} s (v7) ({100 * (t7 / t6 - 1):+.1f}%)")
