"""Same meshes from two GMOS runtimes (review/claude lab): every vertex the runtime hands to the engine
(mesh.Position, mesh.Normal, mesh.TexCoord, mesh.UserData, in order, per mesh) recorded in LuaJIT and compared
exactly, plus the build time of each (best of N). The engine's mesh functions record into flat tables.
python gmos_equal.py <ref runtime.lua> <new runtime.lua> <tag dir> [n streams|all] [reps]"""
import lzma
import sys
import time
from pathlib import Path

REPO = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
sys.path.insert(0, str(REPO / "tests"))
LAB = Path(r"C:/GModLab/revisao/claude")
from lupa import luajit21  # noqa: E402
import test_model_stream as T  # noqa: E402

REC = """
MESHES = {}
local cur
function Mesh() return { n = 0, d = {} } end
mesh = {}
function mesh.Begin(obj, kind, prims) cur = obj; MESHES[#MESHES + 1] = obj end
local function f32(x) return x end
function mesh.Position(x, y, z) if type(x) == "table" then x, y, z = x[1], x[2], x[3] end
    local d = cur.d; local n = #d; d[n + 1], d[n + 2], d[n + 3] = x, y, z end
function mesh.Normal(x, y, z) if type(x) == "table" then x, y, z = x[1], x[2], x[3] end
    local d = cur.d; local n = #d; d[n + 1], d[n + 2], d[n + 3] = x, y, z end
function mesh.TexCoord(_, u, v) local d = cur.d; local n = #d; d[n + 1], d[n + 2] = u, v end
function mesh.UserData(a, b, c, e) local d = cur.d; local n = #d; d[n + 1], d[n + 2], d[n + 3], d[n + 4] = a, b, c, e end
function mesh.AdvanceVertex() end
function mesh.End() cur = nil end
"""
NOOP = """
function Mesh() return {} end
mesh = {}
function mesh.Begin() end
function mesh.Position() end
function mesh.Normal() end
function mesh.TexCoord() end
function mesh.UserData() end
function mesh.AdvanceVertex() end
function mesh.End() end
"""


def runtime(src: bytes, data: bytes, record: bool):
    rt = luajit21.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    g = rt.globals()

    def dec(s):
        d = bytearray(s)
        d[5:13] = b"\xff" * 8
        return lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)
    g[b"PY_READ"] = lambda p, w: data
    g[b"PY_DECOMPRESS"] = dec
    g[b"MODEL"] = b"models/test/car.mdl"
    g[b"MESH_LIMIT"] = 65535
    rt.execute(T.RUNTIME_STUBS.encode())
    rt.execute((REC if record else NOOP).encode())
    rt.execute(src.replace(b'CreateClientConVar("gmo_stream_budget_ms", "4"', b'CreateClientConVar("gmo_stream_budget_ms", "100000"'))
    return rt, g


def build(src, data, record):
    rt, g = runtime(src, data, record)
    t0 = time.perf_counter()
    rt.execute(b"""GMOS.AddManifest({ [MODEL] = "data_static/gmo/test.dat" }); local ent = NewEntity()
        for _ = 1, 2000000 do if GMOS.Ready(MODEL) then break end; ent:DrawModel(0) end
        assert(GMOS.Ready(MODEL))""")
    t = time.perf_counter() - t0
    return t, g


def meshes(g):
    return [list(m[b"d"].values()) for m in g[b"MESHES"].values()]


def main():
    ref, new, tag = Path(sys.argv[1]).read_bytes(), Path(sys.argv[2]).read_bytes(), sys.argv[3]
    n = sys.argv[4] if len(sys.argv) > 4 else "12"
    reps = int(sys.argv[5]) if len(sys.argv) > 5 else 2
    tag_ref, _, tag_new = tag.partition(":")      # "old:new": the reference runtime on lab/old, the new on lab/new
    tag_new = tag_new or tag_ref
    files = []
    for gr in ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]:
        files += list((LAB / tag_ref / gr / f"{gr}_merged_optimized" / "data_static" / "gmo").iterdir())
    files.sort(key=lambda p: -p.stat().st_size)
    if n != "all":
        files = files[:int(n)]
    tr = tn = 0.0
    bad = []
    for p in files:
        data = p.read_bytes()
        data_new = Path(str(p).replace("\\" + tag_ref + "\\", "\\" + tag_new + "\\").replace("/" + tag_ref + "/", "/" + tag_new + "/")).read_bytes()
        _t, ga = build(ref, data, True)
        _t, gb = build(new, data_new, True)
        if meshes(ga) != meshes(gb):
            bad.append(p.name)
        if reps:
            tr += min(build(ref, data, False)[0] for _ in range(reps))
            tn += min(build(new, data_new, False)[0] for _ in range(reps))
    print(f"{len(files)} streams: meshes different in {len(bad)} {bad[:5]}")
    if reps:
        print(f"build time: ref {tr:.3f} s, new {tn:.3f} s ({100 * (tn / tr - 1):+.1f}%)")


if __name__ == "__main__":
    main()
