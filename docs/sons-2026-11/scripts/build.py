"""Refaz um candidato do protótipo como arquivo de verdade (mesmos comandos de proto.py / proto2.py), com as marcas de
loop do arquivo de hoje copiadas para os WAV (posições reescaladas quando a taxa muda)."""
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import LAB, parse_wav, src_root

OLD = os.path.expandvars(r"%LOCALAPPDATA%\GmodAddonOptimizer\tools\ffmpeg\2022-09-22\ffmpeg.exe")


def soxr_filter(src_rate, rate, s16=True):
    f = f"aresample=osr={rate}:resampler=soxr:precision=28:cheby=1"
    if s16:
        f += ":osf=s16:dither_method=none"
    if src_rate != rate:
        f += ":internal_sample_fmt=dblp"
    return f


def ff(args):
    r = subprocess.run([OLD, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-400:])


def loop_chunks(path, to_rate):
    """cue / smpl / LIST adtl chunks of a WAV, with sample positions scaled to to_rate."""
    blob = Path(path).read_bytes()
    w = parse_wav(path)
    scale = to_rate / w["rate"]
    pos, out = 12, b""
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        size = struct.unpack_from("<I", blob, pos + 4)[0]
        chunk = bytearray(blob[pos:pos + 8 + size + (size & 1)])
        if cid == b"cue ":
            n = struct.unpack_from("<I", chunk, 8)[0]
            for i in range(n):
                o = 12 + 24 * i
                if o + 24 <= len(chunk):
                    for off in (4, 20):
                        v = struct.unpack_from("<I", chunk, o + off)[0]
                        struct.pack_into("<I", chunk, o + off, int(round(v * scale)))
            out += bytes(chunk)
        elif cid == b"smpl":
            struct.pack_into("<I", chunk, 8 + 8, int(round(1e9 / to_rate)))
            n = struct.unpack_from("<I", chunk, 8 + 28)[0]
            for i in range(n):
                o = 8 + 36 + 24 * i
                if o + 16 <= len(chunk):
                    for off in (8, 12):
                        v = struct.unpack_from("<I", chunk, o + off)[0]
                        struct.pack_into("<I", chunk, o + off, int(round(v * scale)))
            out += bytes(chunk)
        elif cid == b"LIST" and blob[pos + 8:pos + 12] == b"adtl":
            out += bytes(chunk)
        pos += 8 + size + (size & 1)
    return out


def with_chunks(blob, extra):
    body = blob[8:] + extra
    return b"RIFF" + struct.pack("<I", len(body)) + body


def build_candidate(row, name, dst):
    """row: inventory row. name: candidate name of proto/proto2 ('cb', 'orig', 'ogg_44100_1_q4', 'adx_22050_1', ...)."""
    import adpcm
    import adpcm_ns
    g, rel = row["group"], row["rel"]
    src = src_root(g) / rel
    cb_file = LAB / "cb" / g / rel
    rate, ch = row["orig"]["rate"], row["orig"]["channels"]
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if name == "orig":
        shutil.copyfile(src, dst)
        return dst
    if name == "cb":
        shutil.copyfile(cb_file if cb_file.exists() else src, dst)
        return dst
    m = re.fullmatch(r"ogg_(?:(\d+)_(\d)_)?q(\d+)", name)
    if m:
        r = int(m.group(1) or rate)
        c = int(m.group(2) or ch)
        args = ["-i", str(src), "-map_metadata", "-1", "-vn"]
        if r != rate:
            args += ["-af", soxr_filter(rate, r, s16=False)]
        ff(args + ["-ac", str(c), "-ar", str(r), "-c:a", "libvorbis", "-q:a", m.group(3), "-f", "ogg", str(dst)])
        return dst
    m = re.fullmatch(r"mp3_(?:(\d+)_(\d)_)?V(\d+)", name)
    if m:
        r = int(m.group(1) or rate)
        c = int(m.group(2) or ch)
        ff(["-i", str(src), "-map_metadata", "-1", "-vn", "-ac", str(c), "-ar", str(r), "-c:a", "libmp3lame",
            "-q:a", m.group(3), "-f", "mp3", str(dst)])
        return dst
    m = re.fullmatch(r"(pcm|adx|adn(\d+))_(\d+)_(\d)", name)
    if m:
        r, c = int(m.group(3)), int(m.group(4))
        with tempfile.TemporaryDirectory(dir=str(LAB / "work")) as td:
            pcm_path = Path(td) / "pcm.wav"
            ff(["-i", str(src), "-map_metadata", "-1", "-vn", "-af", soxr_filter(rate, r), "-ac", str(c), "-ar", str(r),
                "-c:a", "pcm_s16le", str(pcm_path)])
            blob = pcm_path.read_bytes()
        if m.group(1) != "pcm":
            w = parse_wav_bytes(blob)
            pcm = np.frombuffer(blob, dtype="<i2", count=w[1] // c * c, offset=w[0]).reshape(-1, c)
            if m.group(1) == "adx":
                blob = adpcm.encode_wav(pcm, r, beam=16)
            else:
                blob = adpcm_ns.encode_wav(pcm, r, gamma=int(m.group(2)) / 100.0, beam=8)
        extra = loop_chunks(cb_file, r) if cb_file.exists() else b""
        dst.write_bytes(with_chunks(blob, extra) if extra else blob)
        return dst
    raise ValueError(name)


def parse_wav_bytes(blob):
    pos = 12
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        size = struct.unpack_from("<I", blob, pos + 4)[0]
        if cid == b"data":
            return pos + 8, size // 2
        pos += 8 + size + (size & 1)
    raise ValueError("no data chunk")


def decode_pcm16(src, dst, rate=None, channels=None):
    args = ["-i", str(src), "-map_metadata", "-1", "-vn"]
    if rate:
        args += ["-ar", str(rate)]
    if channels:
        args += ["-ac", str(channels)]
    ff(args + ["-c:a", "pcm_s16le", "-f", "wav", str(dst)])
