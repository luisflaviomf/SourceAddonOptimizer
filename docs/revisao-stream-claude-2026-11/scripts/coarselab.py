"""In-game check of the coarse stand-in mesh (review/claude lab): the same streamed models, captured in the lab
client (single player, gm_construct) from optimized addons with a 2% and a 1% coarse mesh.

python coarselab.py <run> <addons root>   (root holds <group>/<group>_merged_optimized folders, e.g. lab/cb)
Captures -> C:/GModLab/revisao/claude/clab/<run>/<tag>_<view>.png + report.json. Views of each model, spawned as
a frozen prop_physics on the ground (it casts the engine's RTT shadow, which the coarse mesh draws):
  A near, B shadow (from above), C near with gmo_stream 0 (the coarse mesh alone: first instant / no Lua),
  D beyond gmo_stream_far (4300 units, the coarse mesh is what the engine draws there), E the same with a 8 deg
  field of view (a scope).
The lab client is shared: the focus goes back to the owner's window as soon as the game is in the map
(foco-de-volta.ps1 of the TTT session), the game's cursor stays free, and only our zz_clab_* folders are added.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

CLIENT = Path(r"C:/GModLab/runtime/client-install")
LAB = Path(r"C:/GModLab/revisao/claude")
FOCO = r"C:\dev\gmod-server-capacity\research\ttt-volta-v1\tools\foco-de-volta.ps1"
PREFIX = "zz_clab_"
VIEWS = [  # name, pitch, yaw, distance (x radius, or absolute when > 100), fov, gmo_stream
    ("A", 12, 35, 1.7, 75, 1), ("B", 70, 200, 3.2, 75, 1), ("C", 12, 35, 1.7, 75, 0),
    ("D", 4, 35, 4300, 75, 1), ("E", 4, 35, 4300, 8, 1)]
if os.environ.get("CLAB_VIEWS") == "alpha":
    # close views for the alpha comparison: glass, grilles, lights (stream drawn)
    VIEWS = [("A", 12, 35, 1.7, 75, 1), ("F", 8, 90, 0.85, 75, 1), ("G", 6, 180, 0.85, 75, 1), ("H", 6, 0, 0.85, 75, 1)]

LUA = r"""
local MODELS = __MODELS__
local function find_spot()
    -- an open spot under the sky: ground below, sky above, nothing solid within 700 units around
    local best, bestscore = Vector(-1500, -1650, -84), -1
    local st = { inworld = 0, ground = 0, sky = 0 }
    for x = -15000, 15000, 400 do
        for y = -15000, 15000, 400 do
            for _, z in ipairs({ 3000, 1500, 500, 0, -500, -1500 }) do
                local p = Vector(x, y, z)
                if util.IsInWorld(p) then
                    st.inworld = st.inworld + 1
                    local g = util.TraceLine({ start = p, endpos = p - Vector(0, 0, 4000), mask = MASK_SOLID_BRUSHONLY })
                    if g.Hit and g.HitNormal.z > 0.97 and not g.HitSky then
                        st.ground = st.ground + 1
                        local up = util.TraceLine({ start = g.HitPos + Vector(0, 0, 8), endpos = g.HitPos + Vector(0, 0, 8000), mask = MASK_SOLID_BRUSHONLY })
                        if up.HitSky or not up.Hit then
                            st.sky = st.sky + 1
                            local clear = 99999
                            for a = 0, 315, 45 do
                                local d = Angle(0, a, 0):Forward()
                                local h = util.TraceLine({ start = g.HitPos + Vector(0, 0, 60), endpos = g.HitPos + Vector(0, 0, 60) + d * 1500, mask = MASK_SOLID_BRUSHONLY })
                                clear = math.min(clear, h.Fraction * 1500)
                            end
                            if clear > bestscore then best, bestscore = g.HitPos, clear end
                        end
                    end
                    break
                end
            end
        end
    end
    file.CreateDir("clab")
    file.Write("clab/spot.json", util.TableToJSON({ best = tostring(best), score = bestscore, stats = st }))
    return best, bestscore
end
if SERVER then
    local SPOT
    local function spot()
        if not SPOT then
            local score
            SPOT, score = find_spot()
            print("clab_spot", SPOT, score)
        end
        return SPOT
    end
    concommand.Add("clab_spawn", function(ply, cmd, args)
        for _, e in ipairs(ents.FindByClass("prop_physics")) do if e.clab then e:Remove() end end
        local it = MODELS[tonumber(args[1] or 0)]
        if not it then return end
        local e = ents.Create("prop_physics")
        e:SetModel(it[3]); e:SetPos(spot() + Vector(0, 0, 300)); e:SetAngles(Angle(0, 0, 0)); e:Spawn(); e.clab = true
        local po = e:GetPhysicsObject(); if IsValid(po) then po:EnableMotion(false) end
        local SPOT = spot()
        local tr = util.TraceLine({ start = SPOT + Vector(0, 0, 300), endpos = SPOT - Vector(0, 0, 3000), mask = MASK_SOLID_BRUSHONLY })
        print("clab_spawn", it[3], tr.HitPos)
        e:SetPos(tr.HitPos - Vector(0, 0, e:OBBMins().z) + Vector(0, 0, 1))
    end)
    hook.Add("PlayerInitialSpawn", "clab", function(ply)
        timer.Simple(1, function()
            ply:SetMoveType(MOVETYPE_NOCLIP); ply:SetPos(spot() + Vector(0, 0, 2500)); ply:StripWeapons(); ply:SetNoDraw(true)
        end)
    end)
    -- fixed time of day when StormFox 2 is installed (the brightness of the captures depends on it)
    -- runtime state only (Time.Pause would change StormFox's saved day/night length settings)
    timer.Create("clab_time", 1, 0, function()
        if StormFox2 and StormFox2.Time and StormFox2.Time.Set then pcall(StormFox2.Time.Set, 720) end
        if StormFox2 and StormFox2.Weather and StormFox2.Weather.Set then pcall(StormFox2.Weather.Set, "Clear", 1, 0) end
    end)
    return
end
local VIEWS = __VIEWS__
local OUT = "clab/__RUN__"
local cam_, pending, co = nil, nil, nil
hook.Add("HUDShouldDraw", "clab", function() return false end)
hook.Add("PreDrawViewModel", "clab", function() return true end)
hook.Add("CalcView", "clab", function(ply, origin, angles, fov)
    if cam_ then return { origin = cam_.pos, angles = cam_.ang, fov = cam_.fov, drawviewer = false } end
end)
hook.Add("PostRender", "clab_capture", function()
    if pending then
        local data = render.Capture({ format = "png", x = 0, y = 0, w = ScrW(), h = ScrH(), alpha = false })
        if data then file.Write(pending, data) end
        pending = nil
    end
end)
local function wait(n) for _ = 1, n do coroutine.yield() end end
local function waits(s) local t = RealTime() + s; while RealTime() < t do coroutine.yield() end end
local function find(path)
    for _, e in ipairs(ents.FindByClass("prop_physics")) do
        if string.lower(e:GetModel() or "") == string.lower(path) then return e end
    end
end
local function place(e, v)
    local mins, maxs = e:OBBMins(), e:OBBMaxs()
    local c = e:LocalToWorld((mins + maxs) * 0.5)
    local r = (maxs - mins):Length() * 0.5
    local dir = Angle(v[2], v[3], 0):Forward()
    local d = v[4] > 100 and v[4] or v[4] * r
    local pos = c - dir * d
    -- keep the camera out of walls: shorten until the line to the model is clear
    local tr = util.TraceLine({ start = c, endpos = pos, mask = MASK_SOLID_BRUSHONLY })
    if tr.Hit then pos = tr.HitPos + dir * 16; d = (pos - c):Length() end
    cam_ = { pos = pos, ang = dir:Angle(), fov = v[5] }
    return d
end
local function run()
    file.CreateDir(OUT)
    file.Write(OUT .. "/inmap.txt", "1")
    gui.EnableScreenClicker(true)
    local report = { models = {} }
    for i, it in ipairs(MODELS) do
        local tag, path = it[1], it[3]
        RunConsoleCommand("clab_spawn", tostring(i))
        local e
        for _ = 1, 300 do coroutine.yield(); e = find(path); if IsValid(e) then break end end
        local rep = { exists = IsValid(e) }
        report.models[tag] = rep
        if IsValid(e) then
            place(e, VIEWS[1])
            local t0 = RealTime()
            while GMOS and GMOS.Ready and not GMOS.Ready(path) and RealTime() - t0 < 20 do coroutine.yield() end
            rep.ready = GMOS and GMOS.Ready and GMOS.Ready(path) or false
            rep.ready_s = RealTime() - t0
            waits(__WAIT__)                            -- the texture stream finishes the full sizes
            rep.views = {}
            for _, v in ipairs(VIEWS) do
                RunConsoleCommand("gmo_stream", tostring(v[6]))
                RunConsoleCommand("gmo_stream_far", (v[1] == "D" or v[1] == "E") and "300" or "4000")
                local d = place(e, v)
                waits(1.5)
                pending = string.format("%s/%s_%s.png", OUT, tag, v[1])
                wait(4)
                rep.views[v[1]] = d
            end
            RunConsoleCommand("gmo_stream", "1")
            RunConsoleCommand("gmo_stream_far", "4000")
        end
    end
    RunConsoleCommand("clab_spawn", "0")
    cam_ = nil
    file.Write(OUT .. "/report.json", util.TableToJSON(report, true))
    print("clab_done")
end
hook.Add("InitPostEntity", "clab", function()
    timer.Simple(5, function()
        co = coroutine.create(run)
        hook.Add("Think", "clab_step", function()
            if co and coroutine.status(co) ~= "dead" then
                local ok, err = coroutine.resume(co)
                if not ok then print("clab_error " .. tostring(err)); file.Write(OUT .. "/report.json", "{\"error\":\"" .. tostring(err) .. "\"}"); co = nil end
            end
        end)
    end)
end)
"""

FOCUS_LOOP = r"""
param([string]$Flag, [int]$GamePid)
. '__FOCO__'
Update-Foco -SoLembrar
while (Get-Process -Id $GamePid -ErrorAction SilentlyContinue) {
    if (Test-Path -LiteralPath $Flag) { Update-Foco } else { Update-Foco -SoLembrar }
    Start-Sleep -Milliseconds 500
}
"""


def link_addon(src: Path, dst: Path):
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        top = rel.parts[0].lower()
        name = p.name.lower()
        if top in ("models", "materials", "data_static") or name.startswith("gmo_stream_") or name.startswith("!gmot_"):
            t = dst / rel
            t.parent.mkdir(parents=True, exist_ok=True)
            if not t.exists():
                os.link(p, t)


def main():
    run, root = sys.argv[1], Path(sys.argv[2])
    models = json.loads((LAB / "out" / "clab_models.json").read_text())
    addons = CLIENT / "garrysmod" / "addons"
    for old in addons.glob(PREFIX + "*"):
        shutil.rmtree(old)
    data_dir = CLIENT / "garrysmod" / "data" / "clab" / run
    if data_dir.exists():
        shutil.rmtree(data_dir)
    focus = None
    try:
        for g in sorted({m[0] for m in models}):
            link_addon(root / g / f"{g}_merged_optimized", addons / f"{PREFIX}{g}")
        lua = LUA.replace("__RUN__", run)
        lua = lua.replace("__WAIT__", "20" if os.environ.get("CLAB_VIEWS") == "alpha" else "8")
        lua = lua.replace("__MODELS__", "{" + ",".join('{"%s_%d","%s","%s"}' % (m[0], i, m[0], m[1])
                                                         for i, m in enumerate(models)) + "}")
        lua = lua.replace("__VIEWS__", "{" + ",".join('{"%s",%g,%g,%g,%g,%d}' % v for v in VIEWS) + "}")
        t = addons / (PREFIX + "zdrive") / "lua" / "autorun" / "clab.lua"
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(lua, encoding="utf-8")
        args = [str(CLIENT / "bin" / "win64" / "gmod.exe"), "-multirun", "-allowquit", "-windowed", "-noborder",
                "-x", "32000", "-y", "32000", "-w", "1920", "-h", "1080", "-noworkshop", "-nomouse", "-novid",
                "+fps_max_nofocus", "1000", "+mat_picmip", "0", "+gamemode", "sandbox",
                "+map", "gm_construct"]
        proc = subprocess.Popen(args, cwd=str(CLIENT))
        loop = LAB / "work" / "clab_focus.ps1"
        loop.parent.mkdir(parents=True, exist_ok=True)
        loop.write_text(FOCUS_LOOP.replace("__FOCO__", FOCO), encoding="utf-8")
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
        for old in addons.glob(PREFIX + "*"):
            shutil.rmtree(old, ignore_errors=True)
        if data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
