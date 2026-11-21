"""Finer phases of the GMOS build (review/claude lab), for the runtime on feature/gmos-velocidade, plus the JIT
traces that compiled and aborted per source line. python gmos_profile3.py <runtime.lua> [n models]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gmos_profile as P  # noqa: E402

MARKS = [
    ("    local blob = util.Decompress(sub(raw, 6))\n    if not blob then error(\"decompress failed \" .. st.path) end\n", "read", True),
    ("    local blob = util.Decompress(sub(raw, 6))\n    if not blob then error(\"decompress failed \" .. st.path) end\n", "lzma", False),
    ("    local info, d = decode(blob, yield)\n    blob = nil\n    local mats = {}", "decode", False),
    ("        -- strip indices of all chunks first (high-water mark code): format 7 predicts from them\n", "mats/rest", True),
    ("        local X, Y, Z, NX, NY, NZ, U, V = {}, {}, {}, {}, {}, {}, {}, {}\n", "strips", True),
    ("        FP = nil\n", "vertices", False),
    ("            for i = 1, nvg do\n                local n1, n2, n3", "triangles", True),
    ("        -- strip IMeshes: one per chunk", "per-vertex", True),
    ("        g.chunks = chunks\n", "meshes", True),
]
TRACE = b"""
rawset(_G, "TR", {})
local fi = require("jit.util").funcinfo
jit.attach(function(what, tr, func, pc, otr, oex)
    if what == "stop" or what == "abort" then
        local info = fi(func, pc)
        local k = what .. " " .. tostring(info.currentline) .. (what == "abort" and (" e" .. tostring(otr)) or "")
        TR[k] = (TR[k] or 0) + 1
    end
end, "trace")
"""


def instrument(src: str) -> str:
    for text, name, before in MARKS:
        i = src.find(text)
        assert i >= 0, name
        mark = f'PMARK("{name}")\n'
        src = src[:i] + mark + src[i:] if before else src[:i + len(text)] + mark + src[i + len(text):]
    return src.replace('CreateClientConVar("gmo_stream_budget_ms", "4"', 'CreateClientConVar("gmo_stream_budget_ms", "100000"')


def main():
    rt_path = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    src = instrument(rt_path.read_text(encoding="utf-8"))
    files = []
    for g in ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]:
        files += list((P.LAB / "cb_gp" / g / f"{g}_merged_optimized" / "data_static" / "gmo").iterdir())
    files.sort(key=lambda p: -p.stat().st_size)
    tot, prof, tr = 0.0, {}, {}
    for p in files[:n]:
        data = p.read_bytes()
        best = None
        for rep in range(2):
            t, pr = P.run(data, src)
            if best is None or t < best[0]:
                best = (t, pr)
        tot += best[0]
        for k, v in best[1].items():
            prof[k] = prof.get(k, 0) + v
    # traces on one run of the largest
    import lzma
    from lupa import luajit21
    rt = luajit21.LuaRuntime(encoding=None, unpack_returned_tuples=True)
    g = rt.globals()

    def dec(s):
        d = bytearray(s)
        d[5:13] = b"\xff" * 8
        return lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)
    data = files[0].read_bytes()
    g[b"PY_READ"] = lambda p, w: data
    g[b"PY_DECOMPRESS"] = dec
    g[b"MODEL"] = b"models/test/car.mdl"
    g[b"MESH_LIMIT"] = 65535
    rt.execute(P.STUBS.encode())
    rt.execute(b"function PMARK() end function PRESET() end")
    rt.execute(src.encode("utf-8"))
    rt.execute(TRACE)
    rt.execute(b"""GMOS.AddManifest({ [MODEL] = "data_static/gmo/test.dat" }); local ent = NewEntity()
        for _ = 1, 2000000 do if GMOS.Ready(MODEL) then break end; ent:DrawModel(0) end""")
    for k, v in g[b"TR"].items():
        tr[k.decode()] = v
    print(f"{n} models: total {tot:.2f} s")
    for k, v in sorted(prof.items(), key=lambda kv: -kv[1]):
        print(f"  {k:10} {v:6.3f} s  {100 * v / tot:5.1f}%")
    print("traces (largest stream):", sorted(tr.items(), key=lambda kv: kv[0]))


if __name__ == "__main__":
    main()
