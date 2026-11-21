"""Protótipo em lote: cada som da bancada codificado em cada opção, com tamanho e qualidade contra o ORIGINAL.

python proto.py [grupo,...]  -> work/res/<n>.json por som (retomável) e out/proto.json no fim

Para cada WAV original (referência = original decodificado na taxa/canais de origem, como o produto mede):
  cb          arquivo do Custo-benefício de hoje (lab/cb)
  base        referência manual do Adaptativo (22050 Hz mono PCM16; ou canais originais nos loops de uma marca "loop")
  pcm_R_C     PCM16 nos layouts do plano do produto (mesmo comando ffmpeg/soxr dos candidatos) e 11025 Hz mono
  adx_R_C     MS-ADPCM com o codificador de laboratório (adpcm.py) nos mesmos layouts
  ogg_R_C_qN  Vorbis (libvorbis do ffmpeg empacotado) no layout original, no de hoje e em 22050 mono
  mp3_R_C_VN  MP3 (lame VBR) no layout original
Para OGG/MP3 originais: recodificação no mesmo layout (ogg_qN, mp3_VN).
Métricas: SDR / SNR segmentar / LSD (as do produto) e NMR perceptual (metrics.py).
"""
import json
import os
import shutil
import subprocess
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import metrics
from sndlib import GROUPS, LAB, parse_wav, src_root

OLD = os.path.expandvars(r"%LOCALAPPDATA%\GmodAddonOptimizer\tools\ffmpeg\2022-09-22\ffmpeg.exe")
RES = LAB / "work" / "res"
TMP = LAB / "work" / "p"
OGG_Q = [1, 2, 3, 4, 5, 6]
MP3_V = [2, 4, 6]


def soxr_filter(src_rate, rate):
    f = f"aresample=osr={rate}:resampler=soxr:precision=28:cheby=1:osf=s16:dither_method=none"
    if src_rate != rate:
        f += ":internal_sample_fmt=dblp"
    return f


def run(args):
    r = subprocess.run(args, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + r.stderr.decode("utf-8", "replace")[-400:])


def read_f32(path, channels):
    a = np.fromfile(path, dtype=np.float32)
    return a[:len(a) // channels * channels].reshape(-1, channels)


def loop_chunk_bytes(path):
    blob = Path(path).read_bytes()
    pos, total = 12, 0
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        size = int.from_bytes(blob[pos + 4:pos + 8], "little")
        if cid in (b"cue ", b"smpl") or (cid == b"LIST" and blob[pos + 8:pos + 12] == b"adtl"):
            total += 8 + size + (size & 1)
        pos += 8 + size + (size & 1)
    return total


def to44(x, rate):
    if rate == 44100:
        return x
    import soxr
    return soxr.resample(x.astype(np.float64), rate, 44100, quality="VHQ").astype(np.float32)


def tails(ref):
    peak = np.abs(ref).max(axis=1)

    def run_len(mask):
        if mask.all():
            return len(mask), len(mask)
        nz = np.nonzero(~mask)[0]
        return int(nz[0]), int(len(mask) - 1 - nz[-1])

    h0, t0 = run_len(peak <= 1.6e-5)          # at most half a 16-bit step
    h80, t80 = run_len(peak <= 1e-4)          # -80 dBFS
    return {"head0": h0, "tail0": t0, "head80": h80, "tail80": t80}


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
        g, rel = row["group"], row["rel"]
        src = src_root(g) / rel
        o, cb = row["orig"], row["cb"]
        rate, ch = o["rate"], o["channels"]
        ext = o["ext"]
        files = {}                       # candidate name -> path
        cmd = [OLD, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
               "-vn", "-ac", str(ch), "-ar", str(rate), "-f", "f32le", "-acodec", "pcm_f32le", str(tmp / "ref.f32")]
        pcm_layouts = []
        if ext == ".wav":
            single_cue_loop = (o.get("cue") == 1 and not o.get("smpl")
                               and [l.lower() for l in o.get("labels", [])] == ["loop"])
            base_ch = ch if single_cue_loop else 1
            result["base_channels"] = base_ch
            result["loop_extra"] = loop_chunk_bytes(LAB / "cb" / g / rel)
            cmd += ["-c:a", "pcm_s16le", "-ar", "22050", "-ac", str(base_ch), str(tmp / "base.wav")]
            files["base"] = tmp / "base.wav"
            rates = [22050] if rate <= 22050 else [22050, 44100]
            pcm_layouts = [(r, c) for r in rates for c in sorted({1, ch})] + [(11025, 1)]
            for r, c in pcm_layouts:
                p = tmp / f"pcm_{r}_{c}.wav"
                cmd += ["-map_metadata", "-1", "-vn", "-af", soxr_filter(rate, r), "-ac", str(c), "-ar", str(r),
                        "-c:a", "pcm_s16le", str(p)]
                files[f"pcm_{r}_{c}"] = p
            ogg_layouts = []
            for lay in ((rate, ch), (cb["rate"], cb["channels"]), (22050, 1)):
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
            for v in MP3_V:
                p = tmp / f"mp3_{rate}_{ch}_V{v}.mp3"
                cmd += ["-map_metadata", "-1", "-vn", "-ac", str(ch), "-ar", str(rate), "-c:a", "libmp3lame",
                        "-q:a", str(v), str(p)]
                files[f"mp3_{rate}_{ch}_V{v}"] = p
            files["cb"] = LAB / "cb" / g / rel
        else:
            for q in OGG_Q:
                p = tmp / f"ogg_q{q}.ogg"
                cmd += ["-map_metadata", "-1", "-vn", "-ac", str(ch), "-ar", str(rate), "-c:a", "libvorbis",
                        "-q:a", str(q), str(p)]
                files[f"ogg_q{q}"] = p
            if ext == ".mp3":
                for v in (2, 4, 5, 6):
                    p = tmp / f"mp3_V{v}.mp3"
                    cmd += ["-map_metadata", "-1", "-vn", "-ac", str(ch), "-ar", str(rate), "-c:a", "libmp3lame",
                            "-q:a", str(v), str(p)]
                    files[f"mp3_V{v}"] = p
        run(cmd)
        # lab MS-ADPCM from the PCM16 layouts
        for r, c in pcm_layouts:
            p = tmp / f"pcm_{r}_{c}.wav"
            w = parse_wav(p)
            blob = p.read_bytes()
            pcm = np.frombuffer(blob, dtype="<i2", count=w["frames"] * c, offset=w["data_offset"]).reshape(-1, c)
            if len(pcm) < 4:
                continue
            q = tmp / f"adx_{r}_{c}.wav"
            q.write_bytes(adpcm.encode_wav(pcm, r, beam=16))
            files[f"adx_{r}_{c}"] = q
        # decode every candidate to the source layout, in one process
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
        if len(ref) == 0:
            raise RuntimeError("empty reference")
        hf55, hf11 = metrics.band_energy(ref, rate)
        result["ref"] = {"frames": len(ref), "rate": rate, "channels": ch, "hf55": hf55, "hf11": hf11,
                         "peak": float(np.abs(ref).max()), "rms": float(np.sqrt(np.mean(ref.astype(np.float64) ** 2))),
                         **tails(ref)}
        nref = metrics.NmrReference(to44(ref, rate))
        for n in names:
            dec = read_f32(tmp / f"d_{n}.f32", ch)
            if n.startswith(("adx_", "cb", "base")) or n.startswith("pcm_"):
                pass
            s = metrics.product_scores(ref, dec)
            nm = nref.score(to44(metrics._align(dec, len(ref)), rate))
            size = files[n].stat().st_size
            if n.startswith(("adx_", "pcm_")):
                size += result.get("loop_extra", 0)
            result["cands"][n] = {"bytes": size, "sdr": round(s[0], 3), "seg": round(s[1], 3), "lsd": round(s[2], 4),
                                  "nmr": round(nm[0], 2), "nmr95": round(nm[1], 2), "over": round(nm[2], 4)}
    except Exception as ex:
        result["error"] = f"{type(ex).__name__}: {ex}"[:600] + " | " + traceback.format_exc()[-300:]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out_path.write_text(json.dumps(result), encoding="utf-8")
    return key if "error" in result else None


def main():
    groups = sys.argv[1].split(",") if len(sys.argv) > 1 else GROUPS
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    inv = json.loads((LAB / "out" / "inventory.json").read_text(encoding="utf-8"))
    RES.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    keys = sorted(inv)
    jobs = [(i, k, inv[k]) for i, k in enumerate(keys) if inv[k]["group"] in groups]
    # longest first, so the pool does not end on one long sound
    jobs.sort(key=lambda j: -(j[2]["orig"].get("duration") or 0))
    if limit:
        jobs = jobs[::max(1, len(jobs) // limit)][:limit]
    errors = 0
    with ProcessPoolExecutor(int(os.environ.get("JOBS", "16"))) as ex:
        for n, bad in enumerate(ex.map(process, jobs, chunksize=4)):
            if bad:
                errors += 1
                print("ERRO", bad, flush=True)
            if n % 200 == 0:
                print(n, "/", len(jobs), flush=True)
    merged = {}
    for i, k in enumerate(keys):
        p = RES / f"{i:05d}.json"
        if p.exists():
            merged[k] = json.loads(p.read_text(encoding="utf-8"))
    (LAB / "out" / "proto.json").write_text(json.dumps(merged), encoding="utf-8")
    print("feito", len(merged), "erros", sum(1 for v in merged.values() if "error" in v), flush=True)


if __name__ == "__main__":
    main()
