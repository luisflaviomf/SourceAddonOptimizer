"""Terceira passada (só WAV): o LSD exatamente como o produto calcula (FFT em precisão simples, metrics.lsd32) para
os candidatos da regra do produto: arquivo de hoje, referência manual e MS-ADPCM de laboratório nos layouts do plano.

python proto3.py -> work/res3/<n>.json, out/proto3.json  ({candidato: lsd32})
"""
import json
import os
import shutil
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import metrics
from proto import OLD, read_f32, run, soxr_filter
from sndlib import LAB, parse_wav, src_root

RES = LAB / "work" / "res3"
TMP = LAB / "work" / "p3"


def process(job):
    idx, key, row = job
    out_path = RES / f"{idx:05d}.json"
    if out_path.exists():
        return None
    tmp = TMP / f"{idx:05d}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    result = {"key": key, "lsd32": {}}
    try:
        import adpcm
        g, rel = row["group"], row["rel"]
        src = src_root(g) / rel
        o = row["orig"]
        rate, ch = o["rate"], o["channels"]
        single = o.get("cue") == 1 and not o.get("smpl") and [l.lower() for l in o.get("labels", [])] == ["loop"]
        base_ch = ch if single else 1
        files = {"cb": LAB / "cb" / g / rel, "base": tmp / "base.wav"}
        cmd = [OLD, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
               "-vn", "-ac", str(ch), "-ar", str(rate), "-f", "f32le", "-acodec", "pcm_f32le", str(tmp / "ref.f32"),
               "-c:a", "pcm_s16le", "-ar", "22050", "-ac", str(base_ch), str(tmp / "base.wav")]
        rates = [22050] if rate <= 22050 else [22050, 44100]
        layouts = [(r, c) for r in rates for c in sorted({1, ch})]
        for r, c in layouts:
            cmd += ["-map_metadata", "-1", "-vn", "-af", soxr_filter(rate, r), "-ac", str(c), "-ar", str(r),
                    "-c:a", "pcm_s16le", str(tmp / f"pcm_{r}_{c}.wav")]
        run(cmd)
        for r, c in layouts:
            p = tmp / f"pcm_{r}_{c}.wav"
            w = parse_wav(p)
            blob = p.read_bytes()
            pcm = np.frombuffer(blob, dtype="<i2", count=w["frames"] * c, offset=w["data_offset"]).reshape(-1, c)
            if len(pcm) < 4:
                continue
            q = tmp / f"adx_{r}_{c}.wav"
            q.write_bytes(adpcm.encode_wav(pcm, r, beam=16))
            files[f"adx_{r}_{c}"] = q
        names = [n for n in files if files[n].exists() and files[n].stat().st_size > 0]
        dcmd = [OLD, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
        for n in names:
            dcmd += ["-i", str(files[n])]
        for i, n in enumerate(names):
            dcmd += ["-map", f"{i}:a:0", "-vn", "-ac", str(ch), "-ar", str(rate), "-f", "f32le", "-acodec", "pcm_f32le",
                     str(tmp / f"d_{n}.f32")]
        run(dcmd)
        ref = read_f32(tmp / "ref.f32", ch)
        for n in names:
            result["lsd32"][n] = round(metrics.lsd32(ref, read_f32(tmp / f"d_{n}.f32", ch)), 6)
    except Exception as ex:
        result["error"] = f"{type(ex).__name__}: {ex}"[:600] + " | " + traceback.format_exc()[-300:]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out_path.write_text(json.dumps(result), encoding="utf-8")
    return key if "error" in result else None


def main():
    inv = json.loads((LAB / "out" / "inventory.json").read_text(encoding="utf-8"))
    RES.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    keys = sorted(inv)
    jobs = [(i, k, inv[k]) for i, k in enumerate(keys) if inv[k]["orig"]["ext"] == ".wav"]
    jobs.sort(key=lambda j: -(j[2]["orig"].get("duration") or 0))
    with ProcessPoolExecutor(int(os.environ.get("JOBS", "16"))) as ex:
        for n, bad in enumerate(ex.map(process, jobs, chunksize=4)):
            if bad:
                print("ERRO", bad, flush=True)
            if n % 400 == 0:
                print(n, "/", len(jobs), flush=True)
    merged = {}
    for i, k in enumerate(keys):
        p = RES / f"{i:05d}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            if "error" not in d:
                merged[k] = d["lsd32"]
    (LAB / "out" / "proto3.json").write_text(json.dumps(merged), encoding="utf-8")
    print("feito", len(merged), flush=True)


if __name__ == "__main__":
    main()
