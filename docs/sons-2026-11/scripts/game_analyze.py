"""Lê a rodada do teste no jogo (game/run/<rodada>): a gravação de áudio do motor, o registro do Lua e o console.

python game_analyze.py <rodada> -> game/run/<rodada>/result.json + tabela
Para cada teste: tocou? (energia), frequência do tom (1000 Hz esperado; 2000 no teste de pitch 200), duração,
atraso do início; nos loops: se continua depois do fim do arquivo, com que tom, e se há buraco ou estalo na emenda.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import FFMPEG, LAB

FS = 44100


def load_capture(run):
    wav = run / "sonslab_cap.wav"
    webm = run / "sonslab_cap.webm"
    src = wav if wav.exists() else webm
    if not src.exists():
        return None, None
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(src), "-ac", "2", "-ar", str(FS),
                        "-f", "f32le", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, dtype=np.float32)
    if len(x) < FS and src == wav:
        # header never fixed (game killed before endmovie): raw 16-bit stereo after the 44-byte header
        raw = np.fromfile(wav, dtype="<i2", offset=44)
        x = (raw[:len(raw) // 2 * 2] / 32768.0).astype(np.float32)
    return x.reshape(-1, 2), src.name


def rms_env(x, hop):
    n = len(x) // hop
    return np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-20)


def tone(x, lo=200.0, hi=6000.0):
    """dominant frequency (parabolic interpolation) and its share of the energy"""
    if len(x) < 512:
        return 0.0, 0.0
    w = np.hanning(len(x))
    sp = np.abs(np.fft.rfft(x * w, n=1 << int(np.ceil(np.log2(len(x) * 4)))))
    f = np.fft.rfftfreq((len(sp) - 1) * 2, 1.0 / FS)
    band = (f >= lo) & (f <= hi)
    if not band.any() or sp[band].max() <= 0:
        return 0.0, 0.0
    i = int(np.argmax(np.where(band, sp, 0)))
    if 0 < i < len(sp) - 1:
        a, b, c = np.log(sp[i - 1] + 1e-20), np.log(sp[i] + 1e-20), np.log(sp[i + 1] + 1e-20)
        d = 0.5 * (a - c) / (a - 2 * b + c + 1e-20)
    else:
        d = 0.0
    k = max(1, int(0.03 * len(sp) * 2 * (f[1] - f[0]) and 3))
    share = float((sp[max(0, i - 3):i + 4] ** 2).sum() / ((sp ** 2).sum() + 1e-20))
    return float((i + d) * (f[1] - f[0])), share


def find_sync(mono):
    """start of the three 3 kHz bursts (80 ms on, 70 ms off)"""
    hop = 441
    n = len(mono) // hop
    t = np.arange(hop) / FS
    c, s = np.cos(2 * np.pi * 3000 * t), np.sin(2 * np.pi * 3000 * t)
    fr = mono[:n * hop].reshape(n, hop)
    e = np.sqrt((fr @ c) ** 2 + (fr @ s) ** 2) / hop
    tpl = np.array(([1.0] * 8 + [-1.0] * 7) * 2 + [1.0] * 8)
    score = np.correlate(e, tpl, mode="valid")
    i = int(np.argmax(score))
    return i * hop / FS, float(score[i])


def main():
    run = LAB / "game" / "run" / sys.argv[1]
    tests = {t["file"]: t for t in json.loads((LAB / "game" / "tests.json").read_text(encoding="utf-8"))}
    log = json.loads((run / "log.json").read_text(encoding="utf-8"))
    x, capname = load_capture(run)
    out = {"capture": capname, "info": log.get("info"), "slots": []}
    if x is None:
        print("sem gravacao")
        t_sync = None
    else:
        mono = x.mean(axis=1)
        t_sync, strength = find_sync(mono)
        noise = float(np.percentile(rms_env(mono, 441), 5))
        out["sync"] = {"t": t_sync, "strength": strength, "seconds": len(x) / FS, "noise_floor_db": 20 * np.log10(noise + 1e-12)}
        print("gravacao", capname, "%.1f s" % (len(x) / FS), "sync em %.3f s" % t_sync, "forca", round(strength, 4),
              "piso de ruido %.1f dBFS" % (20 * np.log10(noise + 1e-12)))
    for ev in log["events"]:
        t = tests.get(ev["file"], {})
        row = {"id": ev["id"], "file": ev["file"], "api": ev["api"], "kind": t.get("kind"),
               "lua": {k: v for k, v in ev.items() if k not in ("id", "file", "api", "t", "t_stop")}}
        if ev["api"] == "bass" or x is None:
            out["slots"].append(row)
            continue
        a = int((t_sync + ev["t"] - 0.05) * FS)
        b = int((t_sync + ev["t_stop"] + 0.25) * FS)
        seg = x[max(0, a):b]
        hop = 221
        env = rms_env(seg.mean(axis=1), hop)
        thr = max(noise * 4, 10 ** (-52 / 20))
        active = env > thr
        row["peak_db"] = float(20 * np.log10(env.max() + 1e-12)) if len(env) else -120.0
        if not active.any():
            row["played"] = False
            out["slots"].append(row)
            continue
        on = int(np.argmax(active))
        off = len(active) - 1 - int(np.argmax(active[::-1]))
        row["played"] = True
        row["onset_ms"] = round(1000.0 * (on * hop / FS - 0.05), 1)
        row["active_s"] = round((off - on + 1) * hop / FS, 3)
        s0 = on * hop
        win = lambda t0, t1: seg[s0 + int(t0 * FS):s0 + int(t1 * FS)]
        pitch = 2.0 if ev["api"] == "pitch" else 1.0
        w = win(0.05 / pitch, 0.25 / pitch)
        fl, sl = tone(w[:, 0])
        frr, sr = tone(w[:, 1])
        row["hz_l"], row["hz_r"] = round(fl, 1), round(frr, 1)
        row["purity"] = round(sl, 3)
        l = float(np.sqrt(np.mean(w[:, 0] ** 2) + 1e-20))
        r = float(np.sqrt(np.mean(w[:, 1] ** 2) + 1e-20))
        row["level_db"] = round(20 * np.log10(max(l, r)), 1)
        row["lr_db"] = round(20 * np.log10(l / r), 1)
        if t.get("kind") == "real":
            # the engine's output against the same file decoded by ffmpeg: best lag, correlation, level ratio
            ref = np.fromfile(LAB / "game" / "ref" / (ev["file"].split("/", 1)[1] + ".f32"), dtype=np.float32).reshape(-1, 2)
            n = min(len(ref), int(3.0 * FS))
            a_ = seg[s0:s0 + n + 4000].mean(axis=1)
            b_ = ref[:n].mean(axis=1)
            best = (-2.0, 0)
            for lag in range(0, 4000, 4):
                c_ = a_[lag:lag + n]
                if len(c_) < n:
                    break
                v = float(np.dot(c_, b_) / (np.linalg.norm(c_) * np.linalg.norm(b_) + 1e-20))
                if v > best[0]:
                    best = (v, lag)
            lag = best[1]
            for fine in range(max(0, lag - 4), lag + 5):
                c_ = a_[fine:fine + n]
                if len(c_) == n:
                    v = float(np.dot(c_, b_) / (np.linalg.norm(c_) * np.linalg.norm(b_) + 1e-20))
                    if v > best[0]:
                        best = (v, fine)
            c_ = a_[best[1]:best[1] + n]
            row["real_corr"] = round(best[0], 4)
            row["real_gain_db"] = round(20 * np.log10(np.linalg.norm(c_) / (np.linalg.norm(b_) + 1e-20) + 1e-20), 2)
            row["real_seconds"] = round(n / FS, 2)
        if t.get("kind") == "loop":
            marks = {}
            for name, (t0, t1) in {"0.1": (0.1, 0.3), "0.6": (0.6, 0.9), "1.3": (1.3, 1.6), "2.0": (2.0, 2.3),
                                   "2.9": (2.9, 3.2)}.items():
                ww = win(t0, t1)
                if len(ww) < 2000:
                    continue
                f1, sh = tone(ww[:, 0])
                marks[name] = [round(f1, 1), round(20 * np.log10(np.sqrt(np.mean(ww[:, 0] ** 2)) + 1e-12), 1)]
            row["loop_marks"] = marks
            # seam check between 0.5 s and 3.2 s after the start: holes and clicks
            body = seg[s0 + int(0.5 * FS):s0 + int(3.2 * FS)].mean(axis=1)
            if len(body) > FS:
                e5 = rms_env(body, 110)
                med = float(np.median(e5))
                row["dip_db"] = round(20 * np.log10(e5.min() / (med + 1e-12) + 1e-12), 1)
                dips = np.nonzero(e5 < med * 0.5)[0]
                row["dips"] = int(len(dips))
                row["dip_times"] = [round(0.5 + d * 110 / FS, 3) for d in dips[:12]]
                # clicks: energy above 5 kHz in 2.5 ms hops
                sp = np.fft.rfft(body)
                f = np.fft.rfftfreq(len(body), 1.0 / FS)
                sp[f < 5000] = 0
                hf = np.fft.irfft(sp, n=len(body))
                eh = rms_env(hf, 110)
                mh = float(np.median(eh))
                spikes = np.nonzero(eh > max(mh * 8, 10 ** (-60 / 20)))[0]
                row["click_db"] = round(20 * np.log10(eh.max() / (med + 1e-12) + 1e-12), 1)
                row["clicks"] = int(len(spikes))
                row["click_times"] = [round(0.5 + d * 110 / FS, 3) for d in spikes[:12]]
        out["slots"].append(row)
    # console
    tail = run / "console_tail.log"
    if tail.exists():
        text = tail.read_text(encoding="utf-8", errors="replace")
        lines = [l for l in text.splitlines() if re.search(r"sonslab|sample rate|[Uu]nsupported|block size|Failed to load sound|"
                                                           r"S_StartSound|decoder|MP3|mp3|ogg|wave|Wave|sound", l)]
        out["console"] = lines[:400]
        out["console_lines"] = len(text.splitlines())
    for name in ("sv_dur.json",):
        p = run / name
        if p.exists():
            out["sv_dur"] = json.loads(p.read_text(encoding="utf-8"))
    out["cl_dur"] = log.get("dur_cl")
    (run / "result.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")

    def show(row):
        if row["api"] == "bass":
            l = row["lua"]
            return "bass  cb=%s err=%s len=%s rate=%s pico=%s Hz estado=%s" % (
                l.get("cb"), l.get("err"), l.get("len") and round(l["len"], 3), l.get("rate"),
                l.get("peak_hz") and round(l["peak_hz"]), l.get("state"))
        if not row.get("played"):
            return "%-7s SILENCIO (pico %.0f dB) %s" % (row["api"], row.get("peak_db", -120), row["lua"] or "")
        s = "%-7s %6.0f/%6.0f Hz dur %.2f s atraso %5.1f ms nivel %5.1f dB L-R %+5.1f" % (
            row["api"], row["hz_l"], row["hz_r"], row["active_s"], row["onset_ms"], row["level_db"], row["lr_db"])
        if "real_corr" in row:
            s += " | correlacao com o arquivo decodificado %.4f (ganho %+.1f dB, %.1f s)" % (
                row["real_corr"], row["real_gain_db"], row["real_seconds"])
        if "loop_marks" in row:
            s += " | " + " ".join("%s:%d" % (k, v[0]) if v[1] > -50 else "%s:--" % k for k, v in row["loop_marks"].items())
            s += " | buraco %s dB (%d) estalo %s dB (%d)" % (row.get("dip_db"), row.get("dips", 0), row.get("click_db"),
                                                             row.get("clicks", 0))
        return s

    last = None
    for row in out["slots"]:
        if row["file"] != last:
            print(row["file"])
            last = row["file"]
        print("   ", show(row))
    print("\nconsole (linhas ligadas a som):")
    for l in out.get("console", [])[:200]:
        print("   ", l)


if __name__ == "__main__":
    main()
