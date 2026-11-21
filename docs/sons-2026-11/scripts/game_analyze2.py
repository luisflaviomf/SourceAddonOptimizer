"""Lê a segunda rodada do teste no jogo (game/run/<rodada>, plano game/plan2.json).

python game_analyze2.py <rodada> -> game/run/<rodada>/result2.json + tabelas
- loops (sintéticos e reais): a gravação do jogo contra o que o loop DEVERIA tocar (arquivo decodificado, repetido a
  partir do início verdadeiro do loop) e contra o que tocaria com o defeito do motor; buracos na emenda;
- estéreo num ponto do mapa: nível de cada tom em cada saída;
- glock estéreo WAV x Vorbis: ganho contra o arquivo decodificado;
- sound.PlayFile: o que o registro do Lua guardou.
"""
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import game_analyze as ga
from sndlib import LAB

FS = 44100
OLD = os.path.expandvars(r"%LOCALAPPDATA%\GmodAddonOptimizer\tools\ffmpeg\2022-09-22\ffmpeg.exe")
SND = LAB / "game" / "addon2" / "sound"


def decode(path):
    r = subprocess.run([OLD, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "2", "-ar", str(FS),
                        "-f", "f32le", "-acodec", "pcm_f32le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2)


def looped(dec, length_s, start_s, seconds):
    """what a loop should play: the file once, then [start, end) again and again"""
    n = int(round(length_s * FS))
    s = int(round(start_s * FS))
    body = dec[:n]
    if s >= n:
        return np.concatenate([body, np.zeros((int(seconds * FS), 2), dtype=np.float32)])[:int(seconds * FS)]
    part = body[s:]
    reps = int(np.ceil((seconds * FS - n) / max(1, len(part)))) + 1
    return np.concatenate([body] + [part] * reps)[:int(seconds * FS)]


def best_corr(cap, exp, max_lag=0.06):
    """correlation of the mono mixes at the best lag (the onset anchor is good to a few ms)"""
    a = cap.mean(axis=1).astype(np.float64)
    b = exp.mean(axis=1).astype(np.float64)
    n = min(len(b), len(a) - int(max_lag * FS))
    b = b[:n]
    best = (-2.0, 0)
    for lag in range(0, int(max_lag * FS), 2):
        c = a[lag:lag + n]
        v = float(np.dot(c, b) / (np.linalg.norm(c) * np.linalg.norm(b) + 1e-20))
        if v > best[0]:
            best = (v, lag)
    return best[0], best[1], n


def holes(mono, t0=0.3, t1=2.2):
    hop = 44
    env = np.sqrt(np.mean(mono[:len(mono) // hop * hop].reshape(-1, hop) ** 2, axis=1) + 1e-20)
    a, b = int(t0 * 1000), min(len(env), int(t1 * 1000))
    med = float(np.median(env[a:b]))
    low = env < med * 0.25
    runs, i = [], a
    while i < b:
        if low[i]:
            j = i
            while j < b and low[j]:
                j += 1
            if j - i >= 3:
                runs.append((i, j - i))
            i = j
        else:
            i += 1
    return runs


def tone_db(w, f):
    t = np.arange(len(w)) / FS
    win = np.hanning(len(w))
    c = np.dot(w * win, np.cos(2 * np.pi * f * t))
    s = np.dot(w * win, np.sin(2 * np.pi * f * t))
    return 20 * np.log10(2 * np.hypot(c, s) / win.sum() + 1e-9)


def main():
    global SND
    run = LAB / "game" / "run" / sys.argv[1]
    plan = json.loads((LAB / "game" / (sys.argv[2] if len(sys.argv) > 2 else "plan2.json")).read_text(encoding="utf-8"))
    SND = Path(plan["addon"]) / "sound"
    meta = {t["file"]: t for t in plan["tests"]}
    slots = {s["id"]: s for s in plan["slots"]}
    x, capname = ga.load_capture(run)
    mono = x.mean(axis=1)
    t_sync, strength = ga.find_sync(mono)
    print("gravacao", capname, "%.1f s" % (len(x) / FS), "sync %.3f s" % t_sync, "forca %.3f" % strength)
    log_path = run / "log.json"
    if log_path.exists():
        log = json.loads(log_path.read_text(encoding="utf-8"))
        events = log["events"]
        print("registro do Lua:", len(events), "testes; info", log.get("info"))
    else:
        log = {"events": []}
        events = ga.reconstruct(x, t_sync, plan["slots"], 0)
        print("registro perdido: horarios refeitos da gravacao")
    out = {"capture": capname, "sync": t_sync, "info": log.get("info"), "loops": [], "stereo": [], "real0": [], "bass": []}
    cap_of = {}
    for ev in events:
        s = slots.get(ev["id"], {})
        ev["group"] = s.get("group", "")
        ev["prefix"] = s.get("prefix", "")
        if ev["api"] == "bass":
            continue
        # onset: first strong sample near the logged call time
        a = int((t_sync + ev["t"] - 0.10) * FS)
        seg = x[max(0, a):a + int((s.get("dur", 3.0) + 0.5) * FS)]
        env = ga.rms_env(seg.mean(axis=1), 147)
        thr = 10 ** (-38 / 20) if ev["group"] in ("realloop", "real0") else 10 ** (-30 / 20)
        act = np.nonzero(env > thr)[0]
        ev["onset"] = None if len(act) == 0 else int(act[0]) * 147
        cap_of[ev["id"]] = (seg, ev["onset"])

    print("\n== loops: correlacao da gravacao com o loop certo / com o defeito do motor; buracos (inicio ms, duracao ms)")
    for ev in events:
        if ev["group"] not in ("loopfix", "realloop"):
            continue
        m = meta[ev["file"]]
        seg, on = cap_of[ev["id"]]
        row = {"file": ev["file"], "mode": m.get("mode") or m.get("version"), "played": on is not None}
        if on is None:
            print("  %-34s SILENCIO" % ev["file"].split("/")[1], ev.get("playing"))
            out["loops"].append(row)
            continue
        seconds = slots[ev["id"]]["dur"] - 0.15
        cap = seg[max(0, on - 220):]
        dec = decode(SND / ev["file"])
        length, start = m["length"], m["loop_start"]
        right = looped(dec, length, start, seconds)
        c_right, lag, n = best_corr(cap, right)
        row.update(corr_right=round(c_right, 4), lag_ms=round(1000 * (lag - 220) / FS, 1))
        # the same file as the engine plays it with the two defects: padded length, restart from the byte formula
        if m.get("engine_restart") is not None and (m.get("pad_ms") or abs(m.get("engine_restart", 0) - start) > 1e-4):
            er = m["engine_restart"]
            if er > 1000:                      # stored in samples for the fixed real files
                er = er / m["rate"]
            bug = looped(dec, length + (m.get("pad_ms") or 0) / 1000.0, er, seconds)
            row["corr_bug"] = round(best_corr(cap, bug)[0], 4)
        hl = holes(cap[lag:].mean(axis=1), 0.05 if m["length"] < 0.4 else 0.3, seconds - 0.05)
        row["holes"] = hl[:10]
        row["n_holes"] = len(hl)
        if m.get("kind") == "realloop":
            row["source"] = m["source"]
        out["loops"].append(row)
        print("  %-34s %-6s certo %.4f%s | buracos %d %s" % (
            ev["file"].split("/")[1], row["mode"], c_right,
            (" defeito %.4f" % row["corr_bug"]) if "corr_bug" in row else "", len(hl), hl[:5]))
    # real loops: each version against the ORIGINAL file looped correctly
    print("\n== loops reais: gravacao de cada versao contra o original repetido certo")
    by_src = {}
    for ev in events:
        if ev["group"] == "realloop":
            by_src.setdefault(meta[ev["file"]]["source"], []).append(ev)
    for src, evs in by_src.items():
        orig_file = next(e["file"] for e in evs if meta[e["file"]]["version"] == "orig")
        mo = meta[orig_file]
        ref = looped(decode(SND / orig_file), mo["length"], mo["loop_start"], 2.2)
        line = []
        for e in evs:
            seg, on = cap_of[e["id"]]
            if on is None:
                line.append("%s: silencio" % meta[e["file"]]["version"])
                continue
            c, lag, n = best_corr(seg[max(0, on - 220):], ref)
            line.append("%s %.4f" % (meta[e["file"]]["version"], c))
            for r in out["loops"]:
                if r["file"] == e["file"]:
                    r["corr_vs_orig_loop"] = round(c, 4)
        print("  %-62s %s" % (src[-62:], " | ".join(line)))

    print("\n== estereo num ponto do mapa (EmitSound global, 300 unidades a direita): L@1000 L@1500 | R@1000 R@1500")
    for ev in events:
        if ev["group"] != "estereo":
            continue
        seg, on = cap_of[ev["id"]]
        row = {"file": ev["file"], "prefix": ev["prefix"], "played": on is not None}
        if on is not None:
            w = seg[on + int(0.06 * FS):on + int(0.24 * FS)]
            row["levels"] = [round(float(tone_db(w[:, c], f)), 1) for c in (0, 1) for f in (1000, 1500)]
        out["stereo"].append(row)
        print("  %-24s prefixo '%s' %s" % (ev["file"].split("/")[1], ev["prefix"],
                                           "silencio" if on is None else "%6.1f %6.1f | %6.1f %6.1f" % tuple(row["levels"])))

    print("\n== glock estereo: ganho da gravacao contra o arquivo decodificado (primeiros 0,3 s / 2,0 s)")
    for ev in events:
        if ev["group"] != "real0":
            continue
        seg, on = cap_of[ev["id"]]
        if on is None:
            print("  ", ev["file"], "silencio")
            continue
        dec = decode(SND / ev["file"])
        c, lag, n = best_corr(seg[max(0, on - 220):], dec[:int(2.0 * FS)])
        cap = seg[max(0, on - 220) + lag:]
        g = lambda t: 20 * np.log10(np.linalg.norm(cap[:int(t * FS)]) / (np.linalg.norm(dec[:int(t * FS)]) + 1e-20) + 1e-20)
        out["real0"].append({"file": ev["file"], "corr": round(c, 4), "gain_03": round(float(g(0.3)), 2), "gain_20": round(float(g(2.0)), 2)})
        print("  %-22s correlacao %.4f ganho %+.2f / %+.2f dB" % (ev["file"].split("/")[1], c, g(0.3), g(2.0)))

    print("\n== sound.PlayFile")
    for ev in events:
        if ev["api"] != "bass":
            continue
        row = {k: ev.get(k) for k in ("file", "cb", "err", "len", "rate", "peak_hz", "peak", "state", "pos", "lua_error")}
        out["bass"].append(row)
        print("  %-36s %s" % (ev["file"].split("/")[1],
                              ("ERRO " + str(ev.get("err"))) if ev.get("err") or not ev.get("cb") else
                              "ok len %.3f s, %s Hz, pico %s Hz, estado %s" % (ev.get("len") or 0, ev.get("rate"),
                                                                              ev.get("peak_hz") and round(ev["peak_hz"]), ev.get("state"))))
    if log.get("dur_cl"):
        out["dur_cl"] = log["dur_cl"]
    tail = run / "console_tail.log"
    if tail.exists():
        text = tail.read_text(encoding="utf-8", errors="replace")
        pat = re.compile(r"sonslab|sample rate|[Uu]nsupported .*wave|block size|Failed to load sound|[Cc]an't create mixer|"
                         r"needs Decoder|recording movie|LockResource")
        cnt = Counter(l.strip() for l in text.splitlines() if pat.search(l))
        out["console"] = ["%dx %s" % (n, l) for l, n in cnt.items()]
        print("\n== console")
        for l in out["console"][:80]:
            print("  ", l[:200])
    (run / "result2.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
