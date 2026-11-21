"""Segunda rodada do teste no jogo: o que se perdeu na primeira (sound.PlayFile, duração no cliente) e a correção dos
loops MS-ADPCM.

A primeira rodada mostrou dois comportamentos do motor nos loops MS-ADPCM:
  1. o enchimento do último bloco é tocado a cada volta (buraco do tamanho do enchimento);
  2. no reinício, a posição da marca (em amostras) é dividida pelo tamanho do bloco em BYTES:
     reinício real = (marca // bytes_do_bloco) * amostras_por_bloco + (marca % bytes_do_bloco).
Correções testadas aqui (só na escrita do arquivo, o formato continua MS-ADPCM padrão):
  a. último bloco curto: o arquivo termina na última amostra (no máximo 1 de sobra), sem enchimento;
  b. marca compensada: grava-se a marca m' tal que (m' // B) * spb + (m' % B) = início verdadeiro do loop; quando o
     tamanho de bloco padrão não alcança o ponto, usa-se outro tamanho de bloco (512, 2048, 4096 bytes por canal).

python game_make2.py -> game/addon2/sound/sonslab/*, game/plan2.json (arquivos + lista de testes)
"""
import json
import shutil
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import adpcm
import build
from game_make import OLD, append_chunks, cue_chunk, ff, pcm16, set_fact, sig_l, wav_bytes
from sndlib import LAB, parse_wav, src_root

SRC1 = LAB / "game" / "addon" / "sound" / "sonslab"
OUT = LAB / "game" / "addon2" / "sound" / "sonslab"
TMP = LAB / "game" / "tmp2"


def spb_of(align, ch):
    return 2 + (align - 7 * ch) * 2 // ch


def truncate_last_block(blob, frames):
    """MS-ADPCM WAV with the padded last block cut to the bytes that hold `frames` samples (a short block)."""
    w = parse_wav_bytes(blob)
    ch, align, off, size = w["ch"], w["align"], w["data_offset"], w["data_size"]
    spb = spb_of(align, ch)
    full, rem = divmod(frames, spb)
    if rem == 0:
        keep = full * align
    else:
        nibbles = max(0, rem - 2) * ch
        keep = full * align + 7 * ch + (nibbles + 1) // 2
    keep = min(keep, size)
    data = blob[off:off + keep]
    head = blob[12:off - 8]
    tail = blob[off + size + (size & 1):]
    body = b"WAVE" + head + b"data" + struct.pack("<I", len(data)) + data + (b"\0" if len(data) & 1 else b"") + tail
    return set_fact(b"RIFF" + struct.pack("<I", len(body)) + body, frames)


def parse_wav_bytes(blob):
    pos, out = 12, {}
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        size = struct.unpack_from("<I", blob, pos + 4)[0]
        if cid == b"fmt ":
            _, ch, rate, _, align, _ = struct.unpack_from("<HHIIHH", blob, pos + 8)
            out.update(ch=ch, rate=rate, align=align)
        elif cid == b"data":
            out.update(data_offset=pos + 8, data_size=size)
        pos += 8 + size + (size & 1)
    return out


def compensated(start, align, ch):
    """cue value the engine turns into `start`, or None when this block size cannot reach it."""
    spb = spb_of(align, ch)
    q, r = divmod(start, spb)
    return q * align + r if r < align else None


def engine_restart(cue, align, ch):
    return (cue // align) * spb_of(align, ch) + cue % align


def fixed_loop(pcm, rate, start, labels=None, encoder="lab"):
    """MS-ADPCM loop written so that the engine restarts at `start` and plays no padding. -> blob, info"""
    ch = pcm.shape[1]
    for per_ch in (1024, 2048, 512, 4096, 256):
        align = per_ch * ch
        cue = compensated(start, align, ch)
        if cue is not None:
            break
    else:
        return None, None
    blob = adpcm.encode_wav(pcm, rate, align=align, beam=16)
    blob = truncate_last_block(blob, len(pcm))
    blob = append_chunks(blob, cue_chunk([cue], labels or ["loop"]))
    return blob, {"align": align, "cue_written": cue, "loop_start": start,
                  "engine_restart": engine_restart(cue, align, ch)}


def main():
    shutil.rmtree(LAB / "game" / "addon2", ignore_errors=True)
    shutil.rmtree(TMP, ignore_errors=True)
    OUT.mkdir(parents=True)
    TMP.mkdir(parents=True)
    for f in SRC1.iterdir():
        shutil.copy2(f, OUT / f.name)
    tests1 = json.loads((LAB / "game" / "tests.json").read_text(encoding="utf-8"))
    inv = json.loads((LAB / "out" / "inventory.json").read_text(encoding="utf-8"))
    files, slots = [], []

    def add_file(name, blob=None, **meta):
        if blob is not None:
            (OUT / name).write_bytes(blob)
        files.append({"file": "sonslab/" + name, **meta})
        return "sonslab/" + name

    def slot(file, api, dur, gap, **kw):
        slots.append({"id": len(slots) + 1, "file": file, "api": api, "dur": dur, "gap": gap, "shim": 0,
                      "prefix": kw.get("prefix", ""), "group": kw.get("group", "")})

    # ---- 2. synthetic loop fixes
    def synth(name, rate, ch, mode, cue_at=None, per_ch=None):
        s, start = sig_l(rate)
        if cue_at is not None:
            start = int(cue_at * rate)
        x = pcm16(s).reshape(-1, 1)
        if ch == 2:
            x = np.concatenate([x, x], axis=1)
        if per_ch is None and mode == "ambos":
            # the first block size that reaches the loop start
            per_ch = next(b for b in (1024, 2048, 512, 4096, 256) if compensated(start, b * ch, ch) is not None)
        align = (per_ch or 1024) * ch
        spb = spb_of(align, ch)
        meta = {"kind": "loopfix", "rate": rate, "ch": ch, "mode": mode, "loop_start": start / rate, "length": len(x) / rate,
                "align": align}
        if mode == "hoje":            # ffmpeg, padded, true cue, fact fixed: what the product writes today
            p = TMP / "src.wav"
            p.write_bytes(wav_bytes(x.tobytes(), rate, ch))
            ff(OLD, p, TMP / "out.wav", "-c:a", "adpcm_ms", "-block_size", str(align))
            blob = set_fact((TMP / "out.wav").read_bytes(), len(x))
            cue = start
        elif mode == "curto":         # only the short last block
            p = TMP / "src.wav"
            p.write_bytes(wav_bytes(x.tobytes(), rate, ch))
            ff(OLD, p, TMP / "out.wav", "-c:a", "adpcm_ms", "-block_size", str(align))
            blob = truncate_last_block((TMP / "out.wav").read_bytes(), len(x))
            cue = start
        elif mode == "marca":         # only the compensated cue (lab encoder pads with the last sample)
            blob = adpcm.encode_wav(x, rate, align=align, beam=16)
            cue = compensated(start, align, ch)
        elif mode == "ambos":
            blob = truncate_last_block(adpcm.encode_wav(x, rate, align=align, beam=16), len(x))
            cue = compensated(start, align, ch)
        assert cue is not None, name
        blob = append_chunks(blob, cue_chunk([cue], ["loop"]))
        meta.update(cue_written=cue, engine_restart=engine_restart(cue, align, ch) / rate,
                    pad_ms=round(1000.0 * ((-len(x)) % spb) / rate, 2) if mode in ("hoje", "marca") else 0.0)
        f = add_file(name, blob, **meta)
        slot(f, "patch", 3.0, 0.4, group="loopfix")

    synth("fix_hoje_22050_m.wav", 22050, 1, "hoje")
    synth("fix_curto_22050_m.wav", 22050, 1, "curto")
    synth("fix_marca_22050_m.wav", 22050, 1, "marca")
    synth("fix_ambos_22050_m.wav", 22050, 1, "ambos")
    synth("fix_ambos_44100_m_b2048.wav", 44100, 1, "ambos", per_ch=2048)
    synth("fix_ambos_22050_s.wav", 22050, 2, "ambos")
    synth("fix_hoje_cue0_22050_m.wav", 22050, 1, "hoje", cue_at=0.0)
    synth("fix_curto_cue0_22050_m.wav", 22050, 1, "curto", cue_at=0.0)
    synth("fix_hoje_alem_22050_m.wav", 22050, 1, "hoje", cue_at=0.7)      # restart beyond the end of the file
    synth("fix_ambos_alem_22050_m.wav", 22050, 1, "ambos", cue_at=0.7)

    # ---- 3. real loops of the bench: original, today's file, fixed file
    ref_dir = LAB / "game" / "ref2"
    shutil.rmtree(ref_dir, ignore_errors=True)
    ref_dir.mkdir(parents=True)
    REAL = ["simfphys/sound/gta3d/mci102_low.wav", "LVS/sound/lvs/vehicles/bmw_r75/eng_loop.wav",
            "GLIDE/sound/glide/horns/car_horn_med_3.wav", "simfphys/sound/gta4/vehicles/horns/vigero_horn.wav",
            "simfphys/sound/simulated_vehicles/gta5_dukes/dukes_idle.wav", "GLIDE/sound/glide/horns/police_horn_2.wav"]
    for i, key in enumerate(REAL):
        row = inv[key]
        cbf = LAB / "cb" / row["group"] / row["rel"]
        w = parse_wav(cbf)
        start = w["cue_pos"][0] if w["cue"] else (w["smpl_loops"][0][0] if w["smpl_loops"] else 0)
        base = {"kind": "realloop", "source": key, "rate": w["rate"], "ch": w["channels"], "loop_start": start / w["rate"],
                "length": w["frames"] / w["rate"]}
        f0 = add_file(f"rl{i}_orig.wav", (src_root(row["group"]) / row["rel"]).read_bytes(), version="orig", **base)
        f1 = add_file(f"rl{i}_hoje.wav", cbf.read_bytes(), version="hoje",
                      engine_restart=engine_restart(start, w["align"], w["channels"]) / w["rate"],
                      pad_ms=round(1000.0 * (w["data_bytes"] // w["align"] * spb_of(w["align"], w["channels"]) - w["frames"]) / w["rate"], 1),
                      **base)
        # same audio as today's file (decode of its own PCM source at the same layout), lab encoder, fixed writing
        pcm_path = build.build_candidate(row, f"pcm_{w['rate']}_{w['channels']}", TMP / "pcm.wav")
        pw = parse_wav(pcm_path)
        pcm = np.frombuffer(pcm_path.read_bytes(), dtype="<i2", count=pw["frames"] * pw["channels"],
                            offset=pw["data_offset"]).reshape(-1, pw["channels"])
        blob, info = fixed_loop(pcm, w["rate"], start)
        f2 = add_file(f"rl{i}_novo.wav", blob, version="novo", **base, **{k: v for k, v in info.items() if k != "loop_start"})
        for f in (f0, f1, f2):
            slot(f, "patch", 2.4, 0.4, group="realloop")

    # ---- 4. stereo at a point of the map, with the name prefixes that change spatialization
    for name in ("pcm16_44100_s.wav", "ogg_44100_s_q4.ogg", "mp3_44100_s_v4.mp3"):
        for prefix in ("", ")", "^"):
            slot("sonslab/" + name, "worldemit", 1.1, 0.3, prefix=prefix, group="estereo")
    # ---- 5. the stereo real sound of the first round again, WAV and OGG alternated
    for _ in range(2):
        slot("sonslab/real0_cb.wav", "emit", 2.6, 0.4, group="real0")
        slot("sonslab/real0_ogg_q4.ogg", "emit", 2.6, 0.4, group="real0")
    # ---- sound.PlayFile last (it is not in the engine's recording): every file of the first round (lost with its
    # log) and the new ones
    for t in tests1:
        if t["kind"] != "shim" and (SRC1 / t["file"].split("/", 1)[1]).exists():
            slot(t["file"], "bass", 0.2, 0.1, group="bass")
    for f in files:
        slot(f["file"], "bass", 0.2, 0.1, group="bass")

    all_files = sorted({s["file"] for s in slots} | {"sonslab/sync.wav"})
    plan = {"addon": str(LAB / "game" / "addon2"), "files": all_files, "slots": slots, "tests": files, "endmovie": False}
    (LAB / "game" / "plan2.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    shutil.rmtree(TMP, ignore_errors=True)
    print(len(files), "arquivos novos,", len(slots), "testes, tempo de jogo %.0f s" % sum(s["dur"] + s["gap"] for s in slots))
    for f in files:
        print("  ", f["file"], {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()
                                if k in ("mode", "version", "align", "cue_written", "loop_start", "engine_restart", "pad_ms", "length")})


if __name__ == "__main__":
    main()
