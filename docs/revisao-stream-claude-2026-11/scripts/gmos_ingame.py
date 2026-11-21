"""GMOS build time inside the game (review/claude lab): the n largest streams built by several runtime variants in
the lab client (single player, gm_construct), with the engine's real mesh calls and util.Decompress, each phase
timed with SysTime. Also the cost of the engine's mesh calls alone (numbers vs Vector, JIT on vs off).

python gmos_ingame.py <run> [n streams] [reps] variant=path.lua ...
Report -> C:/GModLab/revisao/claude/clab/<run>/report.json. The lab client is shared (see coarselab.py): only
zz_clab_* folders are added, the focus goes back to the owner's window as soon as the game is in the map."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coarselab as CL  # noqa: E402
import gmos_profile as P1  # noqa: E402
import gmos_profile3 as P3  # noqa: E402

LAB = Path(r"C:/GModLab/revisao/claude")
ADDON = "zz_clab_bench"

LUA = r"""
if SERVER then return end
local OUT = "clab/__RUN__"
local SETS = __SETS__
local VARIANTS = __VARIANTS__
local REPS = __REPS__
PROF = {}
local last = SysTime()
function PMARK(n) local t = SysTime(); PROF[n] = (PROF[n] or 0) + (t - last); last = t end
function PRESET() last = SysTime() end

local function wait(s) local t = RealTime() + s; while RealTime() < t do coroutine.yield() end end

-- the engine's mesh calls alone: one strip mesh of 65534 vertices, five or seven calls per vertex
local MB = [[
local mode = ...
local p, nr, uv, ud, adv = mesh.Position, mesh.Normal, mesh.TexCoord, mesh.UserData, mesh.AdvanceVertex
local vp, vn = Vector(), Vector()
return function(n)
    if mode == "num" then
        for i = 1, n do p(i * 0.01, 0, 1); nr(0, 0, 1); uv(0, 0.5, 0.25); ud(1, 0, 0, 1); adv() end
    else
        for i = 1, n do vp:SetUnpacked(i * 0.01, 0, 1); vn:SetUnpacked(0, 0, 1); p(vp); nr(vn); uv(0, 0.5, 0.25); ud(1, 0, 0, 1); adv() end
    end
end
]]
local function microbench(rep)
    local res = {}
    for _, mode in ipairs({ "num", "vec" }) do
        for _, jiton in ipairs({ true, false }) do
            local f = CompileString(MB, "clab_mb_" .. mode .. (jiton and "_on" or "_off"))(mode)
            if not jiton then jit.off(f) end
            local best = 1e9
            for r = 1, rep do
                local obj = Mesh()
                local t0 = SysTime()
                for k = 1, 10 do
                    mesh.Begin(obj, MATERIAL_TRIANGLE_STRIP, 65532)
                    f(65534)
                    mesh.End()
                end
                local t = SysTime() - t0
                obj:Destroy()
                if t < best then best = t end
                coroutine.yield()
            end
            res[mode .. (jiton and "_jit" or "_nojit")] = best / (10 * 65534) * 1e9   -- ns per vertex
        end
    end
    return res
end

local function load_variant(name)
    local src = file.Read("data_static/clabbench/" .. name .. ".txt", "GAME")
    assert(src, "variant " .. name)
    local keep = GMOS
    GMOS = nil
    local f = CompileString(src, "clab_rt_" .. name)
    f()
    local build = GMOS.BENCH_BUILD
    GMOS = keep
    return build
end

local function free(st)
    if st.info then
        for _, g in ipairs(st.info.groups) do
            for _, m in ipairs(g.chunks or {}) do m:Destroy() end
        end
    end
end

local report = { jit = jit.version, status = { jit.status() }, branch = BRANCH, results = {} }
local function run()
    file.CreateDir(OUT)
    file.Write(OUT .. "/inmap.txt", "1")
    wait(8)
    report.micro = microbench(5)
    local builds = {}
    for _, v in ipairs(VARIANTS) do builds[v[1]] = builds[v[1]] or load_variant(v[3]) end
    for rep = 1, REPS do
        for k = 0, #VARIANTS - 1 do
            local vv = VARIANTS[(rep + k - 1) % #VARIANTS + 1]
            local v, build = vv[1] .. "@" .. vv[2], builds[vv[1]]
            report.results[v] = report.results[v] or {}
            for si, path in ipairs(SETS[vv[2]]) do
                collectgarbage()
                coroutine.yield()
                PROF = {}
                local st = { path = path }
                PRESET()
                local t0 = SysTime()
                build(st, function() end)
                local t = SysTime() - t0
                local r = report.results[v][si]
                if not r or t < r.total then report.results[v][si] = { total = t, prof = PROF, path = path } end
                free(st)
                st = nil
            end
        end
        file.Write(OUT .. "/progress.txt", tostring(rep))
    end
    file.Write(OUT .. "/report.json", util.TableToJSON(report, true))
    print("clab_done")
end
hook.Add("InitPostEntity", "clab_bench", function()
    timer.Simple(5, function()
        local co = coroutine.create(run)
        hook.Add("Think", "clab_bench_step", function()
            if co and coroutine.status(co) ~= "dead" then
                local ok, err = coroutine.resume(co)
                if not ok then
                    print("clab_error " .. tostring(err))
                    file.Write(OUT .. "/report.json", util.TableToJSON({ error = tostring(err) }))
                    co = nil
                end
            end
        end)
    end)
end)
"""


def variant_source(path: Path) -> str:
    src = path.read_text(encoding="utf-8")
    marks = P3.MARKS if "local C, FP = strip_places" in src else P1.MARKS
    out = src
    for text, name, before in marks:
        i = out.find(text)
        assert i >= 0, (path, name)
        mark = f'PMARK("{name}")\n'
        out = out[:i] + mark + out[i:] if before else out[:i + len(text)] + mark + out[i + len(text):]
    i = out.find("    st.info = info\n")
    out = out[:i] + 'PMARK("tail")\n' + out[i:]
    return out + "\nGMOS.BENCH_BUILD = build\n"


def main():
    run = sys.argv[1]
    n = int(sys.argv[2])
    reps = int(sys.argv[3])
    variants = []
    for a in sys.argv[4:]:
        name, rest = a.split("=", 1)
        path, _, sset = rest.partition("@")
        variants.append((name, path, sset or "stock"))
    files = []
    for gr in ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]:
        files += list((LAB / "cb_gp" / gr / f"{gr}_merged_optimized" / "data_static" / "gmo").iterdir())
    files.sort(key=lambda p: -p.stat().st_size)
    files = files[:n]
    addons = CL.CLIENT / "garrysmod" / "addons"
    for old in addons.glob(CL.PREFIX + "*"):
        shutil.rmtree(old)
    data_dir = CL.CLIENT / "garrysmod" / "data" / "clab" / run
    if data_dir.exists():
        shutil.rmtree(data_dir)
    root = addons / ADDON
    focus = None
    try:
        sets = {}
        for sset in sorted({v[2] for v in variants}):
            sets[sset] = []
            for k, p in enumerate(files):
                src = p if sset == "stock" else LAB / "stripexp" / sset / p.name
                rel = f"data_static/gmo_{sset}/b{k:02d}_{p.name}"
                t = root / rel
                t.parent.mkdir(parents=True, exist_ok=True)
                os.link(src, t)
                sets[sset].append(rel)
        for name, path, _s in variants:
            t = root / "data_static" / "clabbench" / f"{name}.txt"
            if not t.exists():
                t.parent.mkdir(parents=True, exist_ok=True)
                t.write_text(variant_source(Path(path)), encoding="utf-8")
        # the models' own materials (the coarse model has them loaded in a real session)
        for g in sorted({p.parent.parent.parent.parent.name for p in files}):
            mat = LAB / "cb_gp" / g / f"{g}_merged_optimized" / "materials"
            for q in mat.rglob("*"):
                if q.is_file():
                    t = addons / f"{CL.PREFIX}mat_{g}" / q.relative_to(mat.parent)
                    t.parent.mkdir(parents=True, exist_ok=True)
                    os.link(q, t)
        lua = (LUA.replace("__RUN__", run).replace("__REPS__", str(reps))
               .replace("__SETS__", "{" + ",".join(k + "={" + ",".join(f'"{x}"' for x in v) + "}" for k, v in sets.items()) + "}")
               .replace("__VARIANTS__", "{" + ",".join('{"%s","%s","%s"}' % (v[0], v[2], v[0]) for v in variants) + "}"))
        t = root / "lua" / "autorun" / "client" / "clab_bench.lua"
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(lua, encoding="utf-8")
        args = [str(CL.CLIENT / "bin" / "win64" / "gmod.exe"), "-multirun", "-allowquit", "-windowed", "-noborder",
                "-x", "32000", "-y", "32000", "-w", "1280", "-h", "720", "-noworkshop", "-nomouse", "-novid",
                "+fps_max_nofocus", "1000", "+gamemode", "sandbox", "+map", "gm_construct"]
        proc = subprocess.Popen(args, cwd=str(CL.CLIENT))
        loop = LAB / "work" / "clab_focus.ps1"
        loop.parent.mkdir(parents=True, exist_ok=True)
        loop.write_text(CL.FOCUS_LOOP.replace("__FOCO__", CL.FOCO), encoding="utf-8")
        focus = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(loop),
                                  "-Flag", str(data_dir / "inmap.txt"), "-GamePid", str(proc.pid)])
        rep = data_dir / "report.json"
        t0 = time.time()
        try:
            while not rep.exists() and proc.poll() is None and time.time() - t0 < 1800:
                time.sleep(2)
            time.sleep(2)
        finally:
            proc.kill()
            proc.wait(30)
        out = LAB / "clab" / run
        if out.exists():
            shutil.rmtree(out)
        if data_dir.exists():
            shutil.copytree(data_dir, out)
        print(run, "report", rep.exists(), round(time.time() - t0), "s", flush=True)
    finally:
        if focus is not None:
            focus.wait(15)
        for old in addons.glob(CL.PREFIX + "*"):
            shutil.rmtree(old, ignore_errors=True)
        if data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
