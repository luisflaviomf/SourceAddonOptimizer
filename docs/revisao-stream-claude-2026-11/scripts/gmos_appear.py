"""How soon a streamed model shows in full, in the game (review/claude lab): the n largest stream models of
lab/<tag> spawned one at a time in front of the camera in the lab client (gm_construct, single player, the
texture stream left out), and per model: seconds and frames until GMOS.Ready, frame times meanwhile (worst,
mean, frames over 1/60 s), the stream's decode time per frame (every coroutine.resume of the client timed: the
streams resume coroutines; the lab's own coroutine is not counted) and the frame time before the spawn.

python gmos_appear.py <run> <tag> <fps> [runtime.lua to load over the addons' runtime] [n]
Report -> C:/GModLab/revisao/claude/clab/<run>/report.json. The lab client is shared: only zz_clab_* folders are
added and the focus goes back to the owner's window as soon as the game is in the map (see coarselab.py)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coarselab as CL  # noqa: E402

LAB = Path(r"C:/GModLab/revisao/claude")
GROUPS = ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]

LUA = r"""
local MODELS = __MODELS__
local OUT = "clab/__RUN__"
if SERVER then
    local SPOT = Vector(-3400, 3000, -117)
    concommand.Add("clab_spawn", function(ply, cmd, args)
        for _, e in ipairs(ents.FindByClass("prop_physics")) do if e.clab then e:Remove() end end
        local path = MODELS[tonumber(args[1] or 0)]
        if not path then return end
        local e = ents.Create("prop_physics")
        e:SetModel(path); e:SetPos(SPOT + Vector(0, 0, 300)); e:SetAngles(Angle(0, 0, 0)); e:Spawn(); e.clab = true
        local po = e:GetPhysicsObject(); if IsValid(po) then po:EnableMotion(false) end
        local tr = util.TraceLine({ start = SPOT + Vector(0, 0, 300), endpos = SPOT - Vector(0, 0, 3000), mask = MASK_SOLID_BRUSHONLY })
        e:SetPos(tr.HitPos - Vector(0, 0, e:OBBMins().z) + Vector(0, 0, 1))
    end)
    hook.Add("PlayerInitialSpawn", "clab", function(ply)
        timer.Simple(1, function()
            ply:SetMoveType(MOVETYPE_NOCLIP); ply:SetPos(SPOT + Vector(0, 0, 2500)); ply:StripWeapons(); ply:SetNoDraw(true)
        end)
    end)
    return
end
local resume = coroutine.resume
local FR = {}                    -- per frame: { decode seconds, resumes }
coroutine.resume = function(co, ...)
    local t0 = SysTime()
    local r = { resume(co, ...) }
    local f = FrameNumber()
    local x = FR[f]
    if not x then x = { 0, 0 }; FR[f] = x end
    x[1] = x[1] + (SysTime() - t0); x[2] = x[2] + 1
    return unpack(r)
end
local cam_
hook.Add("HUDShouldDraw", "clab", function() return false end)
hook.Add("CalcView", "clab", function(ply, origin, angles, fov)
    if cam_ then return { origin = cam_.pos, angles = cam_.ang, fov = 75, drawviewer = false } end
end)
local function find(path)
    for _, e in ipairs(ents.FindByClass("prop_physics")) do
        if string.lower(e:GetModel() or "") == string.lower(path) then return e end
    end
end
local function place(e)
    local mins, maxs = e:OBBMins(), e:OBBMaxs()
    local c = e:LocalToWorld((mins + maxs) * 0.5)
    local r = (maxs - mins):Length() * 0.5
    local dir = Angle(12, 35, 0):Forward()
    cam_ = { pos = c - dir * r * 1.7, ang = dir:Angle() }
end
local function frames_for(seconds)
    local t, n, sum = RealTime() + seconds, 0, 0
    while RealTime() < t do coroutine.yield(); n = n + 1; sum = sum + RealFrameTime() end
    return sum / math.max(n, 1)
end
local function run()
    file.CreateDir(OUT)
    file.Write(OUT .. "/inmap.txt", "1")
    gui.EnableScreenClicker(true)
    local t = RealTime() + 8
    while RealTime() < t do coroutine.yield() end
    local report = { runtime = GMOS and GMOS.REVISION, fps_max = GetConVar("fps_max"):GetFloat(), models = {} }
    for i, path in ipairs(MODELS) do
        cam_ = nil
        local idle = frames_for(1.5)
        RunConsoleCommand("clab_spawn", tostring(i))
        local e
        for _ = 1, 600 do coroutine.yield(); e = find(path); if IsValid(e) then break end end
        local rep = { path = path, idle_ft = idle, found = IsValid(e) }
        report.models[i] = rep
        if IsValid(e) then
            place(e)
            local t0, f0 = RealTime(), FrameNumber()
            local fts = {}
            while not (GMOS and GMOS.Ready(path)) and RealTime() - t0 < 90 do
                coroutine.yield()
                fts[#fts + 1] = RealFrameTime()
            end
            local f1 = FrameNumber()
            rep.ready = GMOS and GMOS.Ready(path) or false
            rep.seconds = RealTime() - t0
            rep.frames = f1 - f0
            local mx, sum, over = 0, 0, 0
            for _, x in ipairs(fts) do
                if x > mx then mx = x end
                sum = sum + x
                if x > 1 / 60 + 0.0005 then over = over + 1 end
            end
            rep.ft_max, rep.ft_mean, rep.over60 = mx, sum / math.max(#fts, 1), over
            local dsum, dmax, calls, nf = 0, 0, 0, 0
            for f = f0, f1 do
                local x = FR[f]
                if x then
                    dsum = dsum + x[1]; calls = calls + x[2]; nf = nf + 1
                    if x[1] > dmax then dmax = x[1] end
                end
            end
            rep.decode_s, rep.decode_max, rep.decode_frames, rep.resumes = dsum, dmax, nf, calls
        end
        file.Write(OUT .. "/progress.txt", tostring(i))
        coroutine.yield()
    end
    RunConsoleCommand("clab_spawn", "0")
    file.Write(OUT .. "/report.json", util.TableToJSON(report, true))
    print("clab_done")
end
hook.Add("InitPostEntity", "clab", function()
    timer.Simple(5, function()
        local co = coroutine.create(run)
        hook.Add("Think", "clab_step", function()
            if co and coroutine.status(co) ~= "dead" then
                local ok, err = resume(co)
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


def link_addon(src: Path, dst: Path):
    """models, materials, data_static and the model stream runtime (the texture stream is left out)"""
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        top = rel.parts[0].lower()
        if top in ("models", "materials", "data_static") or p.name.lower().startswith("gmo_stream_"):
            t = dst / rel
            t.parent.mkdir(parents=True, exist_ok=True)
            if not t.exists():
                os.link(p, t)


def main():
    run, tag, fps = sys.argv[1], sys.argv[2], int(sys.argv[3])
    rt = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] != "-" else None
    n = int(sys.argv[5]) if len(sys.argv) > 5 else 12
    by_hash = {}
    for g in GROUPS:
        root = LAB / "src" / g / f"{g}_merged"
        for p in (root / "models").rglob("*.mdl"):
            rel = p.relative_to(root).as_posix()
            by_hash[hashlib.sha1(rel.lower().encode()).hexdigest()[:16]] = (g, rel)
    streams = []
    for g in GROUPS:
        streams += list((LAB / tag / g / f"{g}_merged_optimized" / "data_static" / "gmo").iterdir())
    streams.sort(key=lambda p: -p.stat().st_size)
    models = [by_hash[p.stem] for p in streams[:n]]
    addons = CL.CLIENT / "garrysmod" / "addons"
    for old in addons.glob(CL.PREFIX + "*"):
        shutil.rmtree(old)
    data_dir = CL.CLIENT / "garrysmod" / "data" / "clab" / run
    if data_dir.exists():
        shutil.rmtree(data_dir)
    focus = None
    try:
        for g in sorted({m[0] for m in models}):
            link_addon(LAB / tag / g / f"{g}_merged_optimized", addons / f"{CL.PREFIX}{g}")
        if rt:
            t = addons / (CL.PREFIX + "rt") / "lua" / "autorun" / "client" / "gmo_stream_zzz_lab.lua"
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rt, t)
        lua = LUA.replace("__RUN__", run).replace("__MODELS__", "{" + ",".join(f'"{m[1]}"' for m in models) + "}")
        t = addons / (CL.PREFIX + "zdrive") / "lua" / "autorun" / "clab_appear.lua"
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(lua, encoding="utf-8")
        args = [str(CL.CLIENT / "bin" / "win64" / "gmod.exe"), "-multirun", "-allowquit", "-windowed", "-noborder",
                "-x", "32000", "-y", "32000", "-w", "1920", "-h", "1080", "-noworkshop", "-nomouse", "-novid",
                "+fps_max", str(fps), "+fps_max_nofocus", str(fps), "+mat_vsync", "0", "+gamemode", "sandbox",
                "+map", "gm_construct"]
        proc = subprocess.Popen(args, cwd=str(CL.CLIENT))
        loop = LAB / "work" / "clab_focus.ps1"
        loop.parent.mkdir(parents=True, exist_ok=True)
        loop.write_text(CL.FOCUS_LOOP.replace("__FOCO__", CL.FOCO), encoding="utf-8")
        focus = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(loop),
                                  "-Flag", str(data_dir / "inmap.txt"), "-GamePid", str(proc.pid)])
        rep = data_dir / "report.json"
        t0 = time.time()
        try:
            while not rep.exists() and proc.poll() is None and time.time() - t0 < 1500:
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
        parent = data_dir.parent
        for f in ("spot.json",):
            if (parent / f).exists():
                (parent / f).unlink()
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()


if __name__ == "__main__":
    main()
