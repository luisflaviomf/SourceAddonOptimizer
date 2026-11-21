"""Teste de formatos de som dentro do jogo (cliente do laboratório, um jogador, gm_construct).

python game_run.py <rodada>
- copia o addon de teste (game/addon + o Lua gerado aqui) para addons/zz_sonslab do cliente;
- abre o jogo fora da tela, com a gravação de áudio do próprio motor (startmovie <nome> wav, na linha de comando:
  o Lua não pode chamar startmovie); se a gravação não existir, o Lua grava com video.Record (webm, áudio Vorbis);
- o Lua toca cada arquivo por cada API em horários marcados e grava o registro em data/sonslab/log.json;
- no fim: guarda registro + gravação + trecho novo do console.log em game/run/<rodada>, restaura config.cfg e
  console.log e remove tudo o que foi posto em addons, data e na pasta do jogo.
O cliente é compartilhado: o foco volta para a janela do dono assim que o jogo entra no mapa (foco-de-volta.ps1
da sessão do TTT), e o cursor do jogo fica livre.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import LAB

CLIENT = Path(r"C:/GModLab/runtime/client-install")
GAME = CLIENT / "garrysmod"
FOCO = r"C:\dev\gmod-server-capacity\research\ttt-volta-v1\tools\foco-de-volta.ps1"
ADDON = "zz_sonslab"
CAP = "sonslab_cap"

CORE = ["pcm16_44100_m.wav", "pcm16_22050_m.wav", "pcm16_11025_m.wav", "pcm16_44100_s.wav", "pcm8_22050_m.wav",
        "adpcm_ff_44100_m_b1024.wav", "adpcm_ff_22050_m_b1024.wav", "adpcm_ff_22050_s_b2048.wav",
        "adpcm_lab_44100_m.wav", "adpcm_lab_22050_m.wav", "mp3_44100_m_cbr128.mp3", "mp3_22050_m_v4.mp3",
        "ogg_44100_m_q4.ogg", "ogg_44100_s_q4.ogg", "ogg_22050_m_q4.ogg", "oggdata_as.wav", "oggdata_as.mp3"]

LUA = r"""
local FILES = __FILES__
local SLOTS = __SLOTS__
-- prototype of a name remap at the Lua calls (a .wav asked by the addon's code, an .ogg on disk)
local SHIM_MAP = { ["sonslab/shim_a.wav"] = "sonslab/shim_a.ogg" }
local shim_on = false
local function remap(s)
    if not shim_on or not isstring(s) then return s end
    local pre, body = string.match(s, "^([%*#@><%^%)}%$!%?&~]*)(.*)$")
    local to = SHIM_MAP[string.lower((string.gsub(body, "\\", "/")))]
    return to and (pre .. to) or s
end
do
    local ENT = FindMetaTable("Entity")
    local o_emit, o_stop, o_create, o_add = ENT.EmitSound, ENT.StopSound, CreateSound, sound.Add
    ENT.EmitSound = function(self, name, ...) return o_emit(self, remap(name), ...) end
    ENT.StopSound = function(self, name, ...) return o_stop(self, remap(name), ...) end
    CreateSound = function(ent, name, ...) return o_create(ent, remap(name), ...) end
    sound.Add = function(t)
        if istable(t) then
            if isstring(t.sound) then t.sound = remap(t.sound)
            elseif istable(t.sound) then for i, v in ipairs(t.sound) do t.sound[i] = remap(v) end end
        end
        return o_add(t)
    end
    if CLIENT then
        local o_sp = surface.PlaySound
        surface.PlaySound = function(name) return o_sp(remap(name)) end
    end
end
if SERVER then
    concommand.Add("sonslab_shim", function(ply, cmd, args) shim_on = args[1] == "1" end)
    concommand.Add("sonslab_sv_emit", function(ply, cmd, args)
        if IsValid(ply) then ply:EmitSound(args[1], 75, 100, 0.5, CHAN_STATIC) end
    end)
    concommand.Add("sonslab_sv_stop", function(ply, cmd, args)
        if IsValid(ply) then ply:StopSound(args[1]) end
    end)
    concommand.Add("sonslab_sv_dur", function(ply)
        local t = {}
        for _, f in ipairs(FILES) do t[f] = SoundDuration(f) end
        file.CreateDir("sonslab")
        file.Write("sonslab/sv_dur.json", util.TableToJSON(t))
    end)
    hook.Add("PlayerInitialSpawn", "sonslab", function(ply)
        timer.Simple(1, function()
            if not IsValid(ply) then return end
            -- open air, far from walls: no room reverb on the recording
            ply:SetMoveType(MOVETYPE_NOCLIP); ply:SetPos(Vector(-3400, 3000, 1500)); ply:StripWeapons()
            ply:GodEnable(); ply:Freeze(true)
        end)
    end)
    timer.Create("sonslab_weather", 1, 0, function()
        if StormFox2 and StormFox2.Weather and StormFox2.Weather.Set then pcall(StormFox2.Weather.Set, "Clear", 1, 0) end
    end)
    return
end

local OUT = "sonslab"
local log = { events = {}, dur_cl = {}, info = {} }
local co
local function waitt(s) local t = CurTime() + s; while CurTime() < t do coroutine.yield() end end
local function waitr(s) local t = RealTime() + s; while RealTime() < t do coroutine.yield() end end
hook.Add("HUDShouldDraw", "sonslab", function() return false end)

local function run()
    file.CreateDir(OUT)
    file.Write(OUT .. "/inmap.txt", "1")
    gui.EnableScreenClicker(true)
    local ply = LocalPlayer()
    log.info.movie_wav = file.Exists("__CAP__.wav", "MOD")
    local vw
    if not log.info.movie_wav then
        local ok, w, err = pcall(video.Record, { container = "webm", video = "vp8", audio = "vorbis", quality = 1,
            bitrate = 800, fps = 30, lockfps = true, name = "__CAP__", width = 320, height = 180 })
        if ok and w then
            w:SetRecordSound(true)
            vw = w
            hook.Add("DrawOverlay", "sonslab_cap", function() if vw then vw:AddFrame(FrameTime(), true) end end)
            log.info.video = "ok"
        else
            log.info.video = tostring(w) .. " " .. tostring(err)
        end
    end
    for _, f in ipairs(FILES) do
        log.dur_cl[f] = SoundDuration(f)
        util.PrecacheSound(f)
    end
    RunConsoleCommand("sonslab_sv_dur")
    file.Write(OUT .. "/log.json", util.TableToJSON(log))
    waitt(3)
    log.info.frametime = FrameTime()
    log.info.realframe = RealFrameTime()
    local t0 = CurTime()
    log.info.t0 = t0
    ply:EmitSound("sonslab/sync.wav", 75, 100, 1, CHAN_STATIC)
    waitt(2)
    for _, s in ipairs(SLOTS) do
        local ev = { id = s.id, file = s.file, api = s.api, t = CurTime() - t0 }
        local stop
        if s.shim == 1 then shim_on = true; RunConsoleCommand("sonslab_shim", "1"); waitt(0.1) end
        local ok, err = pcall(function()
            if s.api == "worldemit" then
                -- the same point, by the global EmitSound, with the name prefix under test (")" or "^")
                local pos = EyePos() + EyeAngles():Right() * 300
                EmitSound((s.prefix or "") .. s.file, pos, 0, CHAN_STATIC, 0.5, 75, 0, 100)
            elseif s.api == "world" then
                -- 300 units to the listener's right: a spatialized sound comes out louder on the right
                local pos = EyePos() + EyeAngles():Right() * 300
                ev.right = tostring(EyeAngles():Right())
                sound.Play(s.file, pos, 75, 100, 0.5)
            elseif s.api == "emit" then
                ply:EmitSound(s.file, 75, 100, 0.5, CHAN_STATIC)
                stop = function() ply:StopSound(s.file) end
            elseif s.api == "patch" or s.api == "pitch" then
                local p = CreateSound(ply, s.file)
                p:SetSoundLevel(75)
                p:PlayEx(0.5, s.api == "pitch" and 200 or 100)
                ev.patch = true
                stop = function() ev.playing_end = p:IsPlaying(); p:Stop() end
                s.probe = function() ev.playing = p:IsPlaying() end
            elseif s.api == "surface" then
                surface.PlaySound(s.file)
            elseif s.api == "script" then
                local name = "sonslab.t" .. s.id
                sound.Add({ name = name, channel = CHAN_STATIC, volume = 0.5, level = 75, pitch = 100, sound = s.file })
                ply:EmitSound(name)
                stop = function() ply:StopSound(name) end
            elseif s.api == "server" then
                RunConsoleCommand("sonslab_sv_emit", s.file)
                stop = function() RunConsoleCommand("sonslab_sv_stop", s.file) end
            elseif s.api == "bass" then
                local chan
                sound.PlayFile("sound/" .. s.file, "noblock", function(ch, errid, errname)
                    ev.cb = true
                    if IsValid(ch) then
                        ch:SetVolume(0.02)
                        ev.len = ch:GetLength(); ev.rate = ch:GetSamplingRate(); ev.state0 = ch:GetState()
                        chan = ch
                    else
                        ev.err = tostring(errid) .. " " .. tostring(errname)
                    end
                end)
                local limit = RealTime() + 1.5
                while not ev.cb and RealTime() < limit do coroutine.yield() end
                waitr(0.12)
                if IsValid(chan) then
                    local t = {}
                    local n = chan:FFT(t, FFT_4096)
                    local best, bi = 0, 0
                    for i = 2, n do if t[i] > best then best, bi = t[i], i end end
                    ev.peak_hz = (bi - 1) * (ev.rate or 44100) / 4096
                    ev.peak = best
                    ev.state = chan:GetState()
                    ev.pos = chan:GetTime()
                end
                stop = function() if IsValid(chan) then chan:Stop() end end
            end
        end)
        if not ok then ev.lua_error = tostring(err) end
        if s.probe then waitt(0.25); pcall(s.probe); waitt(s.dur - 0.25) else waitt(s.dur) end
        if stop then pcall(stop) end
        ev.t_stop = CurTime() - t0
        if s.shim == 1 then shim_on = false; RunConsoleCommand("sonslab_shim", "0") end
        waitt(s.gap)
        log.events[#log.events + 1] = ev
        -- the log is saved as the run goes: the first round lost it when the game closed at the end
        if #log.events % 8 == 0 then file.Write(OUT .. "/log.json", util.TableToJSON(log)) end
    end
    waitt(1)
    log.info.t_end = CurTime() - t0
    file.Write(OUT .. "/log.json", util.TableToJSON(log, true))
    file.Write(OUT .. "/done.txt", "1")
    print("sonslab_done")
    if vw then local v = vw; vw = nil; hook.Remove("DrawOverlay", "sonslab_cap"); v:Finish() end
    if __ENDMOVIE__ then RunConsoleCommand("endmovie") end
end

hook.Add("InitPostEntity", "sonslab", function()
    timer.Simple(6, function()
        co = coroutine.create(run)
        hook.Add("Think", "sonslab_step", function()
            if co and coroutine.status(co) ~= "dead" then
                local ok, err = coroutine.resume(co)
                if not ok then
                    print("sonslab_error " .. tostring(err))
                    file.CreateDir(OUT)
                    file.Write(OUT .. "/log.json", util.TableToJSON(log, true))
                    file.Write(OUT .. "/done.txt", "error " .. tostring(err))
                    co = nil
                end
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


def lua_table(rows):
    def val(v):
        if isinstance(v, str):
            return '"%s"' % v
        return repr(v)
    return "{" + ",".join("{" + ",".join(f"{k}={val(v)}" for k, v in r.items()) + "}" for r in rows) + "}"


def build_slots(tests):
    slots = []

    def add(t, api, dur, gap, shim=0):
        slots.append({"id": len(slots) + 1, "file": t["file"], "api": api, "dur": dur, "gap": gap, "shim": shim})

    shots = [t for t in tests if t["kind"] == "shot"]
    loops = [t for t in tests if t["kind"] == "loop"]
    for t in shots:
        add(t, "emit", 1.1, 0.3)
        add(t, "patch", 1.1, 0.3)
    for t in loops:
        add(t, "patch", 3.4, 0.5)
        add(t, "emit", 3.4, 0.5)
    for t in shots:
        if t["file"].split("/", 1)[1] in CORE:
            for api in ("pitch", "surface", "script", "server"):
                add(t, api, 1.1, 0.3)
    world = ["pcm16_44100_m.wav", "pcm16_44100_s.wav", "adpcm_ff_22050_m_b1024.wav", "adpcm_ff_22050_s_b2048.wav",
             "ogg_44100_m_q4.ogg", "ogg_44100_s_q4.ogg", "mp3_44100_m_cbr128.mp3", "mp3_44100_s_v4.mp3"]
    for t in shots:
        if t["file"].split("/", 1)[1] in world:
            add(t, "world", 1.1, 0.3)
    for t in tests:
        if t["kind"] == "shim":
            for api in ("emit", "patch", "surface", "script", "server"):
                add(t, api, 1.1, 0.3)                 # without the remap: silence expected
                add(t, api, 1.1, 0.3, shim=1)
    for t in tests:
        if t["kind"] == "real":
            add(t, "patch" if t.get("loop") else "emit", 3.4, 0.5)
    for t in tests:
        if t["kind"] not in ("shim", "real"):
            add(t, "bass", 0.5, 0.2)
    return slots


def remove_frames():
    """startmovie also writes one image per frame (sonslab_cap0001.tga ...): they are removed as they appear."""
    import os
    import re
    pat = re.compile(r"^%s\d+\.(tga|jpg)$" % CAP)
    n = 0
    try:
        for e in os.scandir(GAME):
            if e.is_file() and pat.match(e.name):
                try:
                    os.unlink(e.path)
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n


def main():
    run = sys.argv[1]
    plan = None
    if len(sys.argv) > 2 and sys.argv[2].endswith(".json"):
        plan = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if plan:
        slots, files, addon_src = plan["slots"], plan["files"], Path(plan["addon"])
    else:
        tests = json.loads((LAB / "game" / "tests.json").read_text(encoding="utf-8"))
        slots = build_slots(tests)
        files = sorted({t["file"] for t in tests} | {"sonslab/sync.wav"})
        addon_src = LAB / "game" / "addon"
    lua = (LUA.replace("__FILES__", "{" + ",".join('"%s"' % f for f in files) + "}")
              .replace("__SLOTS__", lua_table(slots)).replace("__CAP__", CAP)
              .replace("__ENDMOVIE__", "true" if (plan or {}).get("endmovie", True) else "false"))
    out = LAB / "game" / "run" / run
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "slots.json").write_text(json.dumps(slots), encoding="utf-8")
    (out / "sonslab.lua").write_text(lua, encoding="utf-8")
    print("slots", len(slots), "tempo de jogo previsto %.0f s" % sum(s["dur"] + s["gap"] for s in slots), flush=True)
    if sys.argv[-1] == "dry":
        return

    addon = GAME / "addons" / ADDON
    data_dir = GAME / "data" / "sonslab"
    cap_files = [GAME / f"{CAP}.wav", GAME / "videos" / f"{CAP}.webm", CLIENT / f"{CAP}.wav"]
    console = GAME / "console.log"
    config = GAME / "cfg" / "config.cfg"
    backup = LAB / "game" / "backup"
    backup.mkdir(parents=True, exist_ok=True)
    had_console = console.exists()
    if had_console:
        shutil.copy2(console, backup / "console.log")
    shutil.copy2(config, backup / "config.cfg")
    console_size = console.stat().st_size if had_console else 0
    for p in [addon, data_dir]:
        if p.exists():
            shutil.rmtree(p)
    for p in cap_files:
        if p.exists():
            p.unlink()
    crashes = CLIENT / "crashes"
    crashes_before = set(p.name for p in crashes.iterdir()) if crashes.exists() else set()
    focus = None
    proc = None
    t0 = time.time()
    try:
        shutil.copytree(addon_src, addon)
        (addon / "lua" / "autorun").mkdir(parents=True)
        (addon / "lua" / "autorun" / "sonslab.lua").write_text(lua, encoding="utf-8")
        args = [str(CLIENT / "bin" / "win64" / "gmod.exe"), "-multirun", "-allowquit", "-windowed", "-noborder",
                "-x", "32000", "-y", "32000", "-w", "320", "-h", "240", "-noworkshop", "-nomouse", "-novid",
                "-condebug", "+developer", "1", "+fps_max", "60", "+fps_max_nofocus", "60",
                "+engine_no_focus_sleep", "0", "+gamemode", "sandbox",
                "+map", "gm_construct", "+startmovie", CAP, "wav"]
        proc = subprocess.Popen(args, cwd=str(CLIENT))
        loop = LAB / "work" / "sonslab_focus.ps1"
        loop.parent.mkdir(parents=True, exist_ok=True)
        loop.write_text(FOCUS_LOOP.replace("__FOCO__", FOCO), encoding="utf-8")
        focus = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(loop),
                                  "-Flag", str(data_dir / "inmap.txt"), "-GamePid", str(proc.pid)])
        done = data_dir / "done.txt"
        removed = 0
        while not done.exists() and proc.poll() is None and time.time() - t0 < 3000:
            time.sleep(2)
            removed += remove_frames()
        time.sleep(3)
        print("quadros de imagem removidos durante a rodada:", removed, flush=True)
        print("done", done.exists(), "jogo ainda aberto", proc.poll() is None, round(time.time() - t0), "s", flush=True)
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(30)
        if focus is not None:
            try:
                focus.wait(15)
            except Exception:
                focus.kill()
        time.sleep(1)
        if data_dir.exists():
            for f in data_dir.iterdir():
                shutil.copy2(f, out / f.name)
        for p in cap_files:
            if p.exists():
                shutil.move(str(p), str(out / p.name))
        if console.exists():
            with open(console, "rb") as fh:
                fh.seek(console_size if console.stat().st_size >= console_size else 0)
                (out / "console_tail.log").write_bytes(fh.read())
        # put the shared client back as it was
        if had_console:
            shutil.copy2(backup / "console.log", console)
        elif console.exists():
            console.unlink()
        shutil.copy2(backup / "config.cfg", config)
        for p in [addon, data_dir]:
            shutil.rmtree(p, ignore_errors=True)
        time.sleep(1)
        print("quadros de imagem removidos no fim:", remove_frames(), flush=True)
        if crashes.exists():
            for p in crashes.iterdir():
                if p.name not in crashes_before:
                    shutil.move(str(p), str(out / ("crash_" + p.name)))
        left = [str(p) for p in [addon, data_dir, *cap_files] if p.exists()]
        print("limpeza: sobrou", left or "nada", flush=True)
        print("saida", out, sorted(f.name for f in out.iterdir()), flush=True)


if __name__ == "__main__":
    main()
