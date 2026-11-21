"""Terceira rodada (game/run/g3, plano game/plan3.json): loops gravados pelo código do produto, conferidos no jogo.

python game_analyze3.py <rodada> [plano] -> game/run/<rodada>/result3.json + duas tabelas
1. Volta por volta: acha o começo pela correlação com o arquivo decodificado (o limiar de volume do game_analyze2 erra
   quando o som começa baixo), mede onde o jogo reiniciou depois da primeira passada (procura o trecho seguinte da
   gravação dentro do arquivo inteiro) e segue as voltas (correlação e deslocamento de cada uma).
2. Modelo exato: a gravação inteira contra o sinal que o motor deveria tocar, amostra por amostra: o arquivo
   decodificado uma vez e depois [reinício, fim) repetido, com o reinício calculado da marca gravada pela conta do motor
   ((marca // bloco) * amostrasPorBloco + marca % bloco no MS-ADPCM; a própria marca no PCM). Resíduo geral, resíduo e
   queda de nível em cada emenda.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import game_analyze as ga
from game_analyze2 import OLD
from sndlib import LAB, parse_wav

FS = 44100


def decode_mono(path):
    r = subprocess.run([OLD, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-ar", str(FS),
                        "-f", "f32le", "-acodec", "pcm_f32le", "-"], capture_output=True)
    w = parse_wav(path)
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, w["channels"]).mean(axis=1).astype(np.float64)


def ncorr(a, b):
    n = min(len(a), len(b))
    if n < 32:
        return 0.0
    a, b = a[:n], b[:n]
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-20))


def find(hay, needle):
    """lag of needle inside hay with the highest normalised correlation (FFT for the products, then normalise)"""
    n, m = len(hay), len(needle)
    if m > n:
        return 0, 0.0
    size = 1 << int(np.ceil(np.log2(n + m)))
    prod = np.fft.irfft(np.fft.rfft(hay, size) * np.conj(np.fft.rfft(needle, size)), size)[:n - m + 1]
    c2 = np.concatenate([[0.0], np.cumsum(hay * hay)])
    energy = np.sqrt(np.maximum(c2[m:] - c2[:-m], 1e-20))[:n - m + 1]
    score = prod / (energy * (np.linalg.norm(needle) + 1e-20))
    lag = int(np.argmax(score))
    return lag, float(score[lag])


def native(path, w):
    r = subprocess.run([OLD, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-f", "f32le",
                        "-acodec", "pcm_f32le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, w["channels"]).mean(axis=1).astype(np.float64)


def engine_restart(w):
    """the mark as written in the file and the sample the engine restarts at"""
    mark = w["smpl_loops"][0][0] if w["smpl_loops"] else w["cue_pos"][0]
    if w["fmt"] != "MS-ADPCM":
        return mark, mark
    b, ch = w["align"], w["channels"]
    spb = (b - 7 * ch) * 2 // ch + 2
    return mark, (mark // b) * spb + mark % b


def model_check(mono, t_sync, ev, slot, path):
    w = parse_wav(path)
    dec = native(path, w)
    mark, s = engine_restart(w)
    need = int((slot["dur"] - 0.14) * w["rate"]) + 8
    parts, total, seams = [dec], len(dec), []
    while total < need:
        seams.append(total)
        parts.append(dec[s:])
        total += len(dec) - s
    model = np.concatenate(parts)[:need]
    k = FS // w["rate"]
    if k != 1:                                   # 22050 Hz file in the 44100 Hz mix
        model = np.interp(np.arange(len(model) * k) / k, np.arange(len(model)), model)
        seams = [q * k for q in seams]
    a = max(0, int((t_sync + ev["t"] - 0.20) * FS))
    seg = mono[a:a + int((slot["dur"] + 0.7) * FS)]
    first = min(len(model), len(dec) * k)
    lag0, _ = find(seg[:first + int(0.6 * FS)], model[:first])
    n = min(len(model), len(seg) - lag0)
    cap, mod = seg[lag0:lag0 + n], model[:n]
    g = np.dot(cap, mod) / np.dot(mod, mod)
    res = cap - g * mod
    db = lambda v: 10 * np.log10(np.mean(v ** 2) + 1e-20)
    seam_res, around, dips = [], [], []
    for q in seams:
        if q + 1323 >= n:
            continue
        seam_res.append(db(res[q - 441:q + 441]))
        around.append(max(db(res[q - 1323:q - 441]), db(res[q + 441:q + 1323])))
        low = lambda v: 20 * np.log10(np.sqrt(np.mean(v[q - 880:q + 880].reshape(-1, 88) ** 2, axis=1) + 1e-20).min())
        dips.append(low(cap) - low(g * mod))
    gap = mono[a + int((slot["dur"] + 0.35) * FS):a + int((slot["dur"] + 0.55) * FS)]
    return {"frames": w["frames"], "decoded": len(dec), "mark": mark, "restart": s, "restart_s": round(s / w["rate"], 5),
            "model_corr": round(ncorr(cap, mod), 4), "signal_dbfs": round(db(g * mod), 1), "residual_dbfs": round(db(res), 1),
            "silence_after_dbfs": round(db(gap), 1), "seams": len(seam_res),
            "seam_residual_dbfs": [round(v, 1) for v in seam_res], "seam_neighbour_dbfs": [round(v, 1) for v in around],
            "seam_dip_db": round(min(dips), 1) if dips else None}


def main():
    run = LAB / "game" / "run" / sys.argv[1]
    plan = json.loads((LAB / "game" / (sys.argv[2] if len(sys.argv) > 2 else "plan3.json")).read_text(encoding="utf-8"))
    snd = Path(plan["addon"]) / "sound"
    meta = {t["file"]: t for t in plan["tests"]}
    slots = {s["id"]: s for s in plan["slots"]}
    x, capname = ga.load_capture(run)
    mono = x.mean(axis=1).astype(np.float64)
    t_sync, strength = ga.find_sync(mono)
    log = json.loads((run / "log.json").read_text(encoding="utf-8"))
    rows = []
    print("gravacao %s %.1f s, sync %.3f s" % (capname, len(x) / FS, t_sync))
    print("%-14s %-8s %5s | 1a passada | reinicio medido (esperado) corr | voltas: n, corr min, desloc. por volta (amostras)" % (
        "arquivo", "formato", "bloco"))
    for ev in log["events"]:
        m = meta[ev["file"]]
        slot = slots[ev["id"]]
        w = parse_wav(snd / ev["file"])
        dec = decode_mono(snd / ev["file"])
        n = int(round(m["length"] * FS))
        s = int(round(m["loop_start"] * FS))
        dec = dec[:n]
        a = max(0, int((t_sync + ev["t"] - 0.20) * FS))
        seg = mono[a:a + int((slot["dur"] + 0.7) * FS)]
        end_play = None
        lag0, c0 = find(seg[:n + int(0.6 * FS)], dec)
        row = {"file": ev["file"], "source": m["source"], "version": m["version"], "case": m["case"], "fmt": w["fmt"],
               "align": w["align"], "rate": w["rate"], "channels": w["channels"], "first_pass_corr": round(c0, 4),
               "onset_s": round((a + lag0) / FS - t_sync - ev["t"], 4), "true_start_s": m["loop_start"]}
        # where did the engine restart? the 80 ms after the first pass, searched inside the whole file
        win = seg[lag0 + n:lag0 + n + min(n - s, int(0.08 * FS))]
        r_lag, r_c = find(dec, win) if len(win) >= 64 else (0, 0.0)
        row["restart_measured"] = r_lag
        row["restart_expected"] = s
        row["restart_corr"] = round(r_c, 4)
        # passes one by one
        part = dec[s:]
        L = len(part)
        p = lag0 + n
        stop = lag0 + int((slot["dur"] - 0.12) * FS)
        shifts, corrs = [], []
        while p + L <= min(stop, len(seg) - 80):
            best = (-2.0, 0)
            for d in range(-48, 49):
                if p + d < 0:
                    continue
                c = ncorr(seg[p + d:p + d + L], part)
                if c > best[0]:
                    best = (c, d)
            corrs.append(best[0])
            shifts.append(best[1])
            p += best[1] + L
        row["passes"] = len(corrs)
        row["pass_corr_min"] = round(min(corrs), 4) if corrs else None
        row["pass_corr_med"] = round(float(np.median(corrs)), 4) if corrs else None
        row["pass_shifts"] = shifts
        row["model"] = model_check(mono, t_sync, ev, slot, snd / ev["file"])
        rows.append(row)
        print("%-14s %-8s %5d | %.4f     | %6d (%6d) %.4f | %2d, %s, %s" % (
            ev["file"].split("/")[1], w["fmt"], w["align"], c0, r_lag, s, r_c, len(corrs),
            "%.4f" % min(corrs) if corrs else "-", shifts[:14]))
    print()
    print("== a gravacao contra o sinal exato que o motor deveria tocar")
    print("%-14s %-8s %5s | amostras arquivo/decod. | marca -> reinicio (plano)        | corr   | sinal, residuo, silencio "
          "(dBFS) | emendas: n, pior residuo (vizinhanca), maior queda de nivel dB" % ("arquivo", "formato", "bloco"))
    for r in rows:
        m = r["model"]
        worst = max(range(m["seams"]), key=lambda i: m["seam_residual_dbfs"][i]) if m["seams"] else None
        print("%-14s %-8s %5d | %6d / %6d         | %6d -> %6d = %.4f s (%.4f) | %.4f | %5.1f %5.1f %5.1f | %2d, %s, %s" % (
            r["file"].split("/")[1], r["fmt"], r["align"], m["frames"], m["decoded"], m["mark"], m["restart"], m["restart_s"],
            r["true_start_s"], m["model_corr"], m["signal_dbfs"], m["residual_dbfs"], m["silence_after_dbfs"], m["seams"],
            "-" if worst is None else "%.1f (%.1f)" % (m["seam_residual_dbfs"][worst], m["seam_neighbour_dbfs"][worst]),
            "-" if m["seam_dip_db"] is None else "%.1f" % m["seam_dip_db"]))
    (run / "result3.json").write_text(json.dumps({"capture": capname, "sync": t_sync, "info": log.get("info"), "rows": rows},
                                                 indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
