"""Time of the product's GMOS runtime (decode + tangents + strip meshes, mesh.* stubbed as no-ops) in LuaJIT."""
import sys, time, lzma, glob, os
sys.path.insert(0, r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
sys.path.insert(0, r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116/tests")
from pathlib import Path
from lupa import luajit21
import test_model_stream as T
ROOT = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
stubs = T.RUNTIME_STUBS.replace("""function mesh.Position(v)
    local n = cur.n
    cur[n + 1], cur[n + 2], cur[n + 3] = v[1], v[2], v[3]
    cur.n = n + 3
end""", "function mesh.Position(v) end").replace("assert(cur.n == (cur.prims + 2) * 3)", "")
fs = sorted(glob.glob(r"C:/GModLab/revisao/claude/cb/**/data_static/gmo/*", recursive=True), key=lambda p: -os.path.getsize(p))[:12]
tl = tt = tb = 0
for f in fs:
    data = open(f, "rb").read()
    rt = luajit21.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    g = rt.globals()
    lz = [0.0]
    def dec(s):
        t0 = time.perf_counter(); d = bytearray(s); d[5:13] = b"\xff" * 8; out = lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE); lz[0] += time.perf_counter() - t0; return out
    g[b"PY_READ"] = lambda p, w: data
    g[b"PY_DECOMPRESS"] = dec
    g[b"MODEL"] = b"models/test/car.mdl"
    g[b"MESH_LIMIT"] = 65535
    rt.execute(stubs.encode())
    rt.execute((ROOT / "model_stream" / "runtime.lua").read_bytes())
    t0 = time.perf_counter()
    rt.execute(b"""GMOS.AddManifest({ [MODEL] = "data_static/gmo/test.dat" }); local ent = NewEntity()
        for _ = 1, 2000000 do if GMOS.Ready(MODEL) then break end; ent:DrawModel(0) end
        assert(GMOS.Ready(MODEL))""")
    t = time.perf_counter() - t0
    raw = len(dec(data[5:])); lz[0] /= 2
    tt += t; tl += lz[0]; tb += len(data)
    print(f"{os.path.basename(f)} {len(data)/1024:7.0f} KiB (raw {raw/2**20:5.1f} MiB): total {t*1000:6.0f} ms, LZMA {lz[0]*1000:5.0f} ms")
print(f"total {tb/2**20:.1f} MiB stream: {tt:.2f} s, of which LZMA {tl:.2f} s -> {tb/2**20/tt:.2f} MiB of stream per second")
