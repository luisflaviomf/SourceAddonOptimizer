"""Gera o addon de teste de formatos de som para o cliente do laboratório (lab/game/addon) e a lista de testes.

Sinais sintéticos, para a gravação do jogo dizer objetivamente o que tocou:
  S (0,8 s): tom de 1000 Hz (0-0,3 s) + varredura 400->3000 Hz; no estéreo o canal direito leva 1500 Hz
  L (1,0 s): entrada de 0,4 s em 700 Hz + trecho de loop de 0,6 s em 1100 Hz (ciclos inteiros, emenda sem estalo),
             com a marca de loop (cue/smpl) em 0,4 s
Cada arquivo e uma combinacao de container, codec, taxa e canais; os nomes dizem o que sao.

python game_make.py -> game/addon/sound/sonslab/*, game/tests.json
"""
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import adpcm
from sndlib import FFMPEG, LAB

OLD = os.path.expandvars(r"%LOCALAPPDATA%\GmodAddonOptimizer\tools\ffmpeg\2022-09-22\ffmpeg.exe")
OUT = LAB / "game" / "addon" / "sound" / "sonslab"
TMP = LAB / "game" / "tmp"


def fade(x, rate, ms=5):
    n = int(rate * ms / 1000)
    w = np.ones(len(x))
    w[:n] = np.linspace(0, 1, n)
    w[-n:] = np.linspace(1, 0, n)
    return x * w


def sig_s(rate, tone=1000.0, up=True):
    t1 = np.arange(int(0.3 * rate)) / rate
    a = fade(0.5 * np.sin(2 * np.pi * tone * t1), rate)
    t2 = np.arange(int(0.5 * rate)) / rate
    f0, f1 = (400.0, 3000.0) if up else (3000.0, 400.0)
    ph = 2 * np.pi * (f0 * t2 + (f1 - f0) * t2 * t2 / (2 * 0.5))
    b = fade(0.5 * np.sin(ph), rate)
    return np.concatenate([a, b])


def sig_l(rate, intro_n=None, loop_n=None, f_loop=1100.0):
    intro_n = int(0.4 * rate) if intro_n is None else intro_n
    loop_n = int(0.6 * rate) if loop_n is None else loop_n
    ti = np.arange(intro_n) / rate
    intro = 0.5 * np.sin(2 * np.pi * 700.0 * ti)
    intro[:int(rate * 0.005)] *= np.linspace(0, 1, int(rate * 0.005))
    tl = np.arange(loop_n) / rate
    loop = 0.5 * np.sin(2 * np.pi * f_loop * tl)
    return np.concatenate([intro, loop]), intro_n


def pcm16(x):
    return np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2")


def wav_bytes(samples, rate, channels, bits=16, tag=1, extra=b""):
    """samples: little-endian sample bytes. extra: chunks appended after data."""
    align = channels * bits // 8
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * align, align, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(samples)) + samples
    if len(samples) & 1:
        body += b"\0"
    body += extra
    return b"RIFF" + struct.pack("<I", len(body)) + body


def cue_chunk(positions, labels=None):
    out = b"cue " + struct.pack("<II", 4 + 24 * len(positions), len(positions))
    for i, p in enumerate(positions):
        out += struct.pack("<II4sIII", i + 1, p, b"data", 0, 0, p)
    if labels:
        adtl = b"adtl"
        for i, l in enumerate(labels):
            txt = l.encode() + b"\0"
            if len(txt) & 1:
                txt += b"\0"
            adtl += b"labl" + struct.pack("<II", 4 + len(txt), i + 1) + txt
        out += b"LIST" + struct.pack("<I", len(adtl)) + adtl
    return out


def smpl_chunk(rate, start, end):
    body = struct.pack("<IIIIIIIII", 0, 0, int(round(1e9 / rate)), 60, 0, 0, 0, 1, 0)
    body += struct.pack("<IIIIII", 0, 0, start, end, 0, 0)
    return b"smpl" + struct.pack("<I", len(body)) + body


def append_chunks(blob, extra):
    body = blob[8:] + extra
    return b"RIFF" + struct.pack("<I", len(body)) + body


def set_fact(blob, frames):
    i = blob.find(b"fact")
    if i < 0:
        return blob
    return blob[:i + 8] + struct.pack("<I", frames) + blob[i + 12:]


def ff(exe, src, dst, *args):
    r = subprocess.run([exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-map_metadata", "-1",
                        *args, str(dst)], capture_output=True)
    if r.returncode != 0 or not Path(dst).exists():
        raise RuntimeError(f"{dst}: {r.stderr.decode('utf-8', 'replace')[-300:]}")


def main():
    shutil.rmtree(LAB / "game" / "addon", ignore_errors=True)
    shutil.rmtree(TMP, ignore_errors=True)
    OUT.mkdir(parents=True)
    TMP.mkdir(parents=True)
    tests = []          # file entries: name, kind (shot/loop), expect, meta

    def add(name, blob=None, kind="shot", **meta):
        if blob is not None:
            (OUT / name).write_bytes(blob)
        tests.append({"file": "sonslab/" + name, "kind": kind, **meta})

    def src_wav(sig, rate, stereo_sig=None):
        x = pcm16(sig) if stereo_sig is None else np.stack([pcm16(sig), pcm16(stereo_sig)], axis=1)
        ch = 1 if stereo_sig is None else 2
        p = TMP / f"src_{rate}_{ch}_{len(x)}.wav"
        p.write_bytes(wav_bytes(x.tobytes(), rate, ch))
        return p, x.reshape(-1, ch)

    # sync marker: three 3 kHz bursts
    r = 44100
    burst = fade(0.6 * np.sin(2 * np.pi * 3000.0 * np.arange(int(0.08 * r)) / r), r, 2)
    gap = np.zeros(int(0.07 * r))
    sync = np.concatenate([burst, gap, burst, gap, burst])
    (OUT / "sync.wav").write_bytes(wav_bytes(pcm16(sync).tobytes(), r, 1))

    # ---------------- one-shots, PCM in a WAV container
    for rate in (44100, 22050, 11025, 8000, 16000, 32000, 48000):
        p, x = src_wav(sig_s(rate), rate)
        add(f"pcm16_{rate}_m.wav", p.read_bytes(), rate=rate, ch=1)
    for rate in (44100, 22050):
        p, x = src_wav(sig_s(rate), rate, sig_s(rate, 1500.0, up=False))
        add(f"pcm16_{rate}_s.wav", p.read_bytes(), rate=rate, ch=2)
    for rate in (44100, 22050, 11025):
        s = sig_s(rate)
        u8 = np.clip(np.round(s * 127.0 + 128.0), 0, 255).astype(np.uint8)
        add(f"pcm8_{rate}_m.wav", wav_bytes(u8.tobytes(), rate, 1, bits=8), rate=rate, ch=1)
    s = sig_s(44100)
    i24 = np.clip(np.round(s * 8388607.0), -8388608, 8388607).astype("<i4")
    b24 = b"".join(int(v).to_bytes(3, "little", signed=True) for v in i24)
    add("pcm24_44100_m.wav", wav_bytes(b24, 44100, 1, bits=24), rate=44100, ch=1)
    add("float32_44100_m.wav", wav_bytes(s.astype("<f4").tobytes(), 44100, 1, bits=32, tag=3), rate=44100, ch=1)

    # ---------------- one-shots, MS-ADPCM
    for rate, ch, block in ((44100, 1, 1024), (22050, 1, 1024), (22050, 2, 2048), (44100, 2, 2048), (11025, 1, 1024),
                            (22050, 1, 512), (11025, 1, 256)):
        p, x = src_wav(sig_s(rate), rate, sig_s(rate, 1500.0, up=False) if ch == 2 else None)
        name = f"adpcm_ff_{rate}_{'m' if ch == 1 else 's'}_b{block}.wav"
        ff(OLD, p, OUT / name, "-c:a", "adpcm_ms", "-block_size", str(block))
        (OUT / name).write_bytes(set_fact((OUT / name).read_bytes(), len(x)))      # as the product does
        add(name, rate=rate, ch=ch)
    for rate, ch in ((44100, 1), (22050, 1), (22050, 2)):
        p, x = src_wav(sig_s(rate), rate, sig_s(rate, 1500.0, up=False) if ch == 2 else None)
        blob = adpcm.encode_wav(x, rate, beam=16)
        preds = sorted(set(blob[blob.find(b"data") + 8::(1024 if ch == 1 else 2048)][:64]))
        add(f"adpcm_lab_{rate}_{'m' if ch == 1 else 's'}.wav", blob, rate=rate, ch=ch, predictors=preds)

    # ---------------- one-shots, other codecs inside a WAV container
    p, x = src_wav(sig_s(22050), 22050)
    ff(FFMPEG, p, OUT / "ima_adpcm_22050_m.wav", "-c:a", "adpcm_ima_wav")
    add("ima_adpcm_22050_m.wav", rate=22050, ch=1)
    p44, x44 = src_wav(sig_s(44100), 44100)
    ff(FFMPEG, p44, OUT / "mp3inwav_44100_m.wav", "-c:a", "libmp3lame", "-b:a", "128k", "-f", "wav")
    add("mp3inwav_44100_m.wav", rate=44100, ch=1)

    # ---------------- one-shots, MP3 and OGG
    for rate, ch, args, tag in ((44100, 1, ["-b:a", "128k"], "cbr128"), (44100, 2, ["-q:a", "4"], "v4"),
                                (22050, 1, ["-q:a", "4"], "v4"), (11025, 1, ["-q:a", "4"], "v4"),
                                (48000, 1, ["-q:a", "4"], "v4"), (32000, 1, ["-q:a", "4"], "v4")):
        p, x = src_wav(sig_s(rate), rate, sig_s(rate, 1500.0, up=False) if ch == 2 else None)
        name = f"mp3_{rate}_{'m' if ch == 1 else 's'}_{tag}.mp3"
        ff(OLD, p, OUT / name, "-c:a", "libmp3lame", *args)
        add(name, rate=rate, ch=ch)
    for rate, ch, q in ((44100, 1, 4), (44100, 2, 4), (22050, 1, 4), (22050, 2, 2), (11025, 1, 4), (48000, 1, 4),
                        (32000, 1, 4), (16000, 1, 4), (8000, 1, 4)):
        p, x = src_wav(sig_s(rate), rate, sig_s(rate, 1500.0, up=False) if ch == 2 else None)
        name = f"ogg_{rate}_{'m' if ch == 1 else 's'}_q{q}.ogg"
        ff(OLD, p, OUT / name, "-c:a", "libvorbis", "-q:a", str(q))
        add(name, rate=rate, ch=ch)

    # ---------------- the data of one codec under the extension of another
    ogg = (OUT / "ogg_44100_m_q4.ogg").read_bytes()
    mp3 = (OUT / "mp3_44100_m_cbr128.mp3").read_bytes()
    wav = (OUT / "pcm16_44100_m.wav").read_bytes()
    add("oggdata_as.wav", ogg, rate=44100, ch=1, renamed="ogg->wav")
    add("mp3data_as.wav", mp3, rate=44100, ch=1, renamed="mp3->wav")
    add("oggdata_as.mp3", ogg, rate=44100, ch=1, renamed="ogg->mp3")
    add("mp3data_as.ogg", mp3, rate=44100, ch=1, renamed="mp3->ogg")
    add("wavdata_as.ogg", wav, rate=44100, ch=1, renamed="wav->ogg")
    add("wavdata_as.mp3", wav, rate=44100, ch=1, renamed="wav->mp3")
    # a name asked with the extension the file does not have
    (OUT / "only_ogg.ogg").write_bytes(ogg)
    (OUT / "only_wav.wav").write_bytes(wav)
    add("only_ogg.wav", None, rate=44100, ch=1, missing="so existe only_ogg.ogg")
    add("only_wav.ogg", None, rate=44100, ch=1, missing="so existe only_wav.wav")
    # asked as .wav, only the .ogg exists, a Lua remap (prototype) fixes the name at the call
    (OUT / "shim_a.ogg").write_bytes(ogg)
    add("shim_a.wav", None, kind="shim", rate=44100, ch=1, missing="so existe shim_a.ogg; remapeado em Lua")

    # ---------------- loops
    def loop_pcm(name, rate, ch=1, bits=16, extra=None, tail_tone=None, **meta):
        s, start = sig_l(rate)
        if tail_tone:
            # the last 0.2 s in another tone: it is only heard if the engine plays past the second cue
            n = int(0.2 * rate)
            s[-n:] = 0.5 * np.sin(2 * np.pi * tail_tone * np.arange(n) / rate)
        if bits == 8:
            data = np.clip(np.round(s * 127.0 + 128.0), 0, 255).astype(np.uint8)
        else:
            data = pcm16(s)
        if ch == 2:
            data = np.stack([data, data], axis=1)
        blob = wav_bytes(data.tobytes(), rate, ch, bits=bits, extra=extra(rate, start, len(s)))
        add(name, blob, kind="loop", rate=rate, ch=ch, loop_start=start / rate, length=len(s) / rate, **meta)

    cue_mid = lambda rate, start, n: cue_chunk([start], ["loop"])
    loop_pcm("loop_pcm16_22050_m_cue.wav", 22050, extra=cue_mid)
    loop_pcm("loop_pcm16_44100_m_cue.wav", 44100, extra=cue_mid)
    loop_pcm("loop_pcm16_11025_m_cue.wav", 11025, extra=cue_mid)
    loop_pcm("loop_pcm8_22050_m_cue.wav", 22050, bits=8, extra=cue_mid)
    loop_pcm("loop_pcm16_22050_s_cue.wav", 22050, ch=2, extra=cue_mid)
    loop_pcm("loop_pcm16_22050_m_smpl.wav", 22050, extra=lambda rate, start, n: smpl_chunk(rate, start, n - 1))
    loop_pcm("loop_pcm16_22050_m_cue0.wav", 22050, extra=lambda rate, start, n: cue_chunk([0]), cue0=True)
    # two cues (start_cue / end_cue), the second one 0.2 s before the end of the file
    loop_pcm("loop_pcm16_22050_m_cue2.wav", 22050,
             extra=lambda rate, start, n: cue_chunk([start, n - int(0.2 * rate)], ["start_cue", "end_cue"]),
             tail_tone=1600.0, end_cue=0.8)
    loop_pcm("noloop_pcm16_22050_m.wav", 22050, extra=lambda rate, start, n: b"", control="sem marca")

    def loop_adpcm(name, rate, ch, encoder, fix_fact=True, aligned=False, **meta):
        block = 1024 if ch == 1 else 2048
        spb = 2 + (block - 7 * ch) * 2 // ch
        if aligned:
            intro_n, loop_n = 4 * spb, 6 * spb
            cycles = round(1100.0 * loop_n / rate)
            s, start = sig_l(rate, intro_n, loop_n, cycles * rate / loop_n)
        else:
            s, start = sig_l(rate)
        x = pcm16(s).reshape(-1, 1)
        if ch == 2:
            x = np.concatenate([x, x], axis=1)
        if encoder == "ff":
            p = TMP / f"loop_{rate}_{ch}_{len(x)}.wav"
            p.write_bytes(wav_bytes(x.tobytes(), rate, ch))
            ff(OLD, p, TMP / "loop_out.wav", "-c:a", "adpcm_ms", "-block_size", str(block))
            blob = (TMP / "loop_out.wav").read_bytes()
            if fix_fact:
                blob = set_fact(blob, len(x))
        else:
            blob = adpcm.encode_wav(x, rate, beam=16)
        blob = append_chunks(blob, cue_chunk([start], ["loop"]))
        pad = (-len(x)) % spb
        add(name, blob, kind="loop", rate=rate, ch=ch, loop_start=start / rate, length=len(x) / rate,
            pad_ms=round(1000.0 * pad / rate, 2), **meta)

    loop_adpcm("loop_adpcm_ff_22050_m.wav", 22050, 1, "ff")
    loop_adpcm("loop_adpcm_ff_44100_m.wav", 44100, 1, "ff")
    loop_adpcm("loop_adpcm_ff_22050_s.wav", 22050, 2, "ff")
    loop_adpcm("loop_adpcm_ff_22050_m_nofact.wav", 22050, 1, "ff", fix_fact=False)
    loop_adpcm("loop_adpcm_lab_22050_m.wav", 22050, 1, "lab")
    loop_adpcm("loop_adpcm_lab_44100_m.wav", 44100, 1, "lab")
    loop_adpcm("loop_adpcm_lab_22050_m_aligned.wav", 22050, 1, "lab", aligned=True)

    s, start = sig_l(44100)
    p = TMP / "loop_src_44100.wav"
    p.write_bytes(wav_bytes(pcm16(s).tobytes(), 44100, 1))
    ff(OLD, p, OUT / "loop_ogg_44100_m.ogg", "-c:a", "libvorbis", "-q:a", "5")
    add("loop_ogg_44100_m.ogg", kind="loop", rate=44100, ch=1, loop_start=0.4, length=1.0, control="ogg nao tem marca")
    ff(OLD, p, OUT / "loop_mp3_44100_m.mp3", "-c:a", "libmp3lame", "-q:a", "2")
    add("loop_mp3_44100_m.mp3", kind="loop", rate=44100, ch=1, loop_start=0.4, length=1.0, control="mp3 nao tem marca")

    # ---------------- real bench sounds in the candidate formats (and today's file of each)
    import build
    inv = json.loads((LAB / "out" / "inventory.json").read_text(encoding="utf-8"))
    ref_dir = LAB / "game" / "ref"
    shutil.rmtree(ref_dir, ignore_errors=True)
    ref_dir.mkdir(parents=True)
    REAL = [("arc9/sound/weapons/arc9_fas/glock20/glock20_fire1.wav", ["cb", "ogg_q4"]),
            ("m9k/sound/weapons/fg42/ak47-1.wav", ["cb", "ogg_q4"]),
            ("simfphys/sound/vehicles/tdmcars/supra/supra_idle.wav", ["cb", "adx_44100_1", "adx_22050_1", "adn85_22050_1"]),
            ("LVS/sound/lvs/vehicles/ferrari_360/eng_loop.wav", ["cb", "adx_44100_1", "adn85_22050_1"]),
            ("GLIDE/sound/glide/streams/speedo/idle.wav", ["cb", "adx_44100_1"])]
    for i, (key, names) in enumerate(REAL):
        row = inv[key]
        for name in names:
            cand = name
            if name == "ogg_q4":
                cand = f"ogg_{row['orig']['rate']}_{row['orig']['channels']}_q4"
            ext = ".ogg" if cand.startswith("ogg") else ".wav"
            fname = f"real{i}_{name}{ext}"
            build.build_candidate(row, cand, OUT / fname)
            build.ff(["-i", str(OUT / fname), "-vn", "-ac", "2", "-ar", "44100", "-f", "f32le", "-acodec", "pcm_f32le",
                      str(ref_dir / (fname + ".f32"))])
            add(fname, kind="real", rate=row["orig"]["rate"], ch=row["orig"]["channels"], source=key, cand=cand,
                loop=bool(row["cb"].get("loop")), length=row["orig"]["duration"])

    for t in tests:
        f = OUT / t["file"].split("/", 1)[1]
        t["bytes"] = f.stat().st_size if f.exists() else 0
    (LAB / "game" / "tests.json").write_text(json.dumps(tests, indent=1), encoding="utf-8")
    shutil.rmtree(TMP, ignore_errors=True)
    print(len(tests), "arquivos de teste;", sum(1 for t in tests if t["kind"] == "loop"), "loops")
    for t in tests:
        print(" ", t["file"], t["bytes"], {k: v for k, v in t.items() if k not in ("file", "bytes", "kind", "rate", "ch")})


if __name__ == "__main__":
    main()
