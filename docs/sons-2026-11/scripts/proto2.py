"""Segunda passada do protótipo (só WAV): MS-ADPCM com ruído moldado (adpcm_ns.py) e a métrica perceptual de quadro
curto (pré-eco/ataques) para os candidatos que importam.

python proto2.py -> work/res2/<n>.json, out/proto2.json
Candidatos medidos (contra o original, como em proto.py):
  cb, adx_R_C (ADPCM de laboratório, sem moldar)                 -> acrescenta nmr_s (quadro curto)
  adn{G}_R_C  ADPCM com ruído moldado, gamma G em (60, 85)        -> bytes, as 3 do produto, nmr, nmr_s
  ogg_R_C_qN  no layout original e no de hoje, q 2..6             -> acrescenta nmr_s
Layouts R_C: os do plano do produto mais 22050 Hz mono.
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
from proto import OLD, loop_chunk_bytes, read_f32, run, soxr_filter, to44
from sndlib import GROUPS, LAB, parse_wav, src_root

RES = LAB / "work" / "res2"
TMP = LAB / "work" / "p2"
GAMMAS = (0.6, 0.85)
OGG_Q = (2, 3, 4, 5, 6)


def process(job):
    idx, key, row = job
    out_path = RES / f"{idx:05d}.json"
    if out_path.exists():
        return None
    tmp = TMP / f"{idx:05d}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    result = {"key": key, "cands": {}}
    try:
        import adpcm
        import adpcm_ns
        g, rel = row["group"], row["rel"]
        src = src_root(g) / rel
        o, cb = row["orig"], row["cb"]
        rate, ch = o["rate"], o["channels"]
        loop_extra = loop_chunk_bytes(LAB / "cb" / g / rel)
        files = {"cb": LAB / "cb" / g / rel}
        cmd = [OLD, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
               "-vn", "-ac", str(ch), "-ar", str(rate), "-f", "f32le", "-acodec", "pcm_f32le", str(tmp / "ref.f32")]
        rates = [22050] if rate <= 22050 else [22050, 44100]
        layouts = [(r, c) for r in rates for c in sorted({1, ch})]
        for r, c in layouts:
            cmd += ["-map_metadata", "-1", "-vn", "-af", soxr_filter(rate, r), "-ac", str(c), "-ar", str(r),
                    "-c:a", "pcm_s16le", str(tmp / f"pcm_{r}_{c}.wav")]
        ogg_layouts = []
        for lay in ((rate, ch), (cb["rate"], cb["channels"])):
            if lay not in ogg_layouts:
                ogg_layouts.append(lay)
        for r, c in ogg_layouts:
            for q in OGG_Q:
                p = tmp / f"ogg_{r}_{c}_q{q}.ogg"
                cmd += ["-map_metadata", "-1", "-vn"]
                if r != rate:
                    cmd += ["-af", f"aresample=osr={r}:resampler=soxr:precision=28:cheby=1:internal_sample_fmt=dblp"]
                cmd += ["-ac", str(c), "-ar", str(r), "-c:a", "libvorbis", "-q:a", str(q), str(p)]
                files[f"ogg_{r}_{c}_q{q}"] = p
        run(cmd)
        new = set()
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
            for gm in GAMMAS:
                name = f"adn{int(gm * 100)}_{r}_{c}"
                q = tmp / f"{name}.wav"
                q.write_bytes(adpcm_ns.encode_wav(pcm, r, gamma=gm, beam=8))
                files[name] = q
                new.add(name)
        names = [n for n in files if files[n].exists() and files[n].stat().st_size > 0]
        for start in range(0, len(names), 24):
            part = names[start:start + 24]
            dcmd = [OLD, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
            for n in part:
                dcmd += ["-i", str(files[n])]
            for i, n in enumerate(part):
                dcmd += ["-map", f"{i}:a:0", "-vn", "-ac", str(ch), "-ar", str(rate), "-f", "f32le",
                         "-acodec", "pcm_f32le", str(tmp / f"d_{n}.f32")]
            run(dcmd)
        ref = read_f32(tmp / "ref.f32", ch)
        ref44 = to44(ref, rate)
        long_ref = metrics.NmrReference(ref44)
        short_ref = metrics.NmrReference(ref44, short=True)
        for n in names:
            dec = metrics._align(read_f32(tmp / f"d_{n}.f32", ch), len(ref))
            d44 = to44(dec, rate)
            ns = short_ref.score(d44)
            entry = {"nmr_s": round(ns[0], 2), "nmr_s95": round(ns[1], 2), "over_s": round(ns[2], 4)}
            if n in new:
                s = metrics.product_scores(ref, dec)
                nm = long_ref.score(d44)
                entry.update(bytes=files[n].stat().st_size + loop_extra, sdr=round(s[0], 3), seg=round(s[1], 3),
                             lsd=round(s[2], 4), nmr=round(nm[0], 2), nmr95=round(nm[1], 2), over=round(nm[2], 4))
            result["cands"][n] = entry
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
    if len(sys.argv) > 1:
        n = int(sys.argv[1])
        jobs = jobs[::max(1, len(jobs) // n)][:n]
    with ProcessPoolExecutor(int(os.environ.get("JOBS", "16"))) as ex:
        for n, bad in enumerate(ex.map(process, jobs, chunksize=4)):
            if bad:
                print("ERRO", bad, flush=True)
            if n % 200 == 0:
                print(n, "/", len(jobs), flush=True)
    merged = {}
    for i, k in enumerate(keys):
        p = RES / f"{i:05d}.json"
        if p.exists():
            merged[k] = json.loads(p.read_text(encoding="utf-8"))
    (LAB / "out" / "proto2.json").write_text(json.dumps(merged), encoding="utf-8")
    print("feito", len(merged), "erros", sum(1 for v in merged.values() if "error" in v), flush=True)


if __name__ == "__main__":
    main()
