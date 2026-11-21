"""Leitura de sons para o estudo de sons (docs/sons-2026-11.md): cabeçalho RIFF/WAVE (formato, duração lógica,
marcas de loop) e ffprobe para MP3/OGG. Só leitura."""
import json
import struct
import subprocess
from pathlib import Path

LAB = Path(r"C:/GModLab/revisao/sons")
BENCH = Path(r"C:/GModLab/bench/src")
GROUPS = ["arc9", "GLIDE", "LVS", "m9k", "simfphys"]
FFMPEG = r"C:/ffmpeg/bin/ffmpeg.exe"
FFPROBE = r"C:/ffmpeg/bin/ffprobe.exe"
MiB = 2 ** 20

FMT_NAMES = {1: "PCM", 2: "MS-ADPCM", 3: "FLOAT", 0x11: "IMA-ADPCM", 0x55: "MP3-in-WAV", 0xFFFE: "EXTENSIBLE"}


def src_root(group):
    return BENCH / group / f"{group}_merged"


def parse_wav(path):
    """dict with the fmt fields, logical frames, duration, loop markers; None when it is not RIFF/WAVE."""
    data = Path(path).read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    out = {"size": len(data), "chunks": [], "cue": 0, "cue_pos": [], "labels": [], "smpl": False, "smpl_loops": [],
           "fact": None, "data_bytes": 0, "tag": None}
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        end = min(len(data), body + size)
        name = cid.decode("latin1")
        out["chunks"].append(name)
        if cid == b"fmt " and size >= 16:
            tag, ch, rate, avg, align, bits = struct.unpack_from("<HHIIHH", data, body)
            out.update(tag=tag, channels=ch, rate=rate, avg_bytes=avg, align=align, bits=bits)
            if tag == 0xFFFE and size >= 26:
                out["subtag"] = struct.unpack_from("<H", data, body + 24)[0]
            if tag in (2, 0x11) and size >= 20:
                out["spb"] = struct.unpack_from("<H", data, body + 18)[0]
        elif cid == b"data":
            out["data_bytes"] = end - body
            out["data_offset"] = body
        elif cid == b"fact" and size >= 4:
            out["fact"] = struct.unpack_from("<I", data, body)[0]
        elif cid == b"cue " and size >= 4:
            n = struct.unpack_from("<I", data, body)[0]
            out["cue"] = n
            for i in range(n):
                o = body + 4 + 24 * i
                if o + 24 <= end:
                    out["cue_pos"].append(struct.unpack_from("<I", data, o + 20)[0])
        elif cid == b"smpl" and size >= 36:
            out["smpl"] = True
            n = struct.unpack_from("<I", data, body + 28)[0]
            for i in range(n):
                o = body + 36 + 24 * i
                if o + 16 <= end:
                    out["smpl_loops"].append(struct.unpack_from("<II", data, o + 8))
        elif cid == b"LIST" and size >= 4 and data[body:body + 4] == b"adtl":
            p = body + 4
            while p + 8 <= end:
                sid = data[p:p + 4]
                ssz = struct.unpack_from("<I", data, p + 4)[0]
                if sid == b"labl" and ssz >= 4:
                    out["labels"].append(data[p + 12:p + 8 + ssz].split(b"\0")[0].decode("latin1").strip())
                p += 8 + ssz + (ssz & 1)
        pos = body + size + (size & 1)
    if out["tag"] is None:
        return None
    tag = out["tag"]
    if tag == 0xFFFE:
        tag = out.get("subtag", tag)
    ch = max(1, out["channels"])
    if tag in (1, 3) and out["bits"]:
        frames = out["data_bytes"] // max(1, ch * out["bits"] // 8)
    elif out["fact"] is not None:
        frames = out["fact"]
    elif tag in (2, 0x11) and out.get("spb"):
        frames = out["data_bytes"] // max(1, out["align"]) * out["spb"]
    else:
        frames = 0
    out["frames"] = frames
    out["duration"] = frames / out["rate"] if out["rate"] else 0.0
    out["loop"] = bool(out["cue"] or out["smpl"])
    out["fmt"] = FMT_NAMES.get(tag, hex(tag)) + (str(out["bits"]) if tag == 1 else "")
    return out


def probe(path):
    """codec, rate, channels, duration, bit_rate of an MP3/OGG (ffprobe)."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=codec_name,sample_rate,channels,duration,bit_rate:format=duration,bit_rate",
                        "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        j = json.loads(r.stdout)
        s = j["streams"][0]
        dur = float(s.get("duration") or j["format"].get("duration") or 0)
        br = int(s.get("bit_rate") or j["format"].get("bit_rate") or 0)
        return {"codec": s["codec_name"], "rate": int(s["sample_rate"]), "channels": int(s["channels"]),
                "duration": dur, "bit_rate": br}
    except Exception:
        return None


def sounds(root):
    root = Path(root)
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".wav", ".mp3", ".ogg"):
            yield p
