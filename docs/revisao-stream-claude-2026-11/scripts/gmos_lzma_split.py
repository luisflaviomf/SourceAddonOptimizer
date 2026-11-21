"""Size cost of cutting a GMOS stream's LZMA into parts (review/claude lab): every stream of lab/<tag> decompressed
and compressed again (the encoder's settings) whole, and cut in parts of at most N MiB of decompressed bytes
(each part its own LZMA, so the runtime could decompress one per frame). python gmos_lzma_split.py <tag> [MiB ...]"""
import lzma
import sys
from multiprocessing import Pool
from pathlib import Path

LAB = Path(r"C:/GModLab/revisao/claude")
GROUPS = ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]
FILTERS = [{"id": lzma.FILTER_LZMA1, "preset": 9 | lzma.PRESET_EXTREME, "dict_size": 1 << 24}]
SIZES = [1.0, 2.0]


def job(path):
    data = Path(path).read_bytes()
    d = bytearray(data[5:])
    d[5:13] = b"\xff" * 8
    blob = lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)
    whole = len(lzma.compress(blob, format=lzma.FORMAT_ALONE, filters=FILTERS))
    out = {"raw": len(blob), "whole": whole, "file": len(data)}
    for mib in SIZES:
        step = int(mib * 2**20)
        out[mib] = sum(len(lzma.compress(blob[i:i + step], format=lzma.FORMAT_ALONE, filters=FILTERS))
                       for i in range(0, len(blob), step))
        out[f"parts{mib}"] = (len(blob) + step - 1) // step
    return out


def main():
    global SIZES
    tag = sys.argv[1]
    if len(sys.argv) > 2:
        SIZES = [float(x) for x in sys.argv[2:]]
    files = []
    for g in GROUPS:
        files += [str(p) for p in (LAB / tag / g / f"{g}_merged_optimized" / "data_static" / "gmo").iterdir()]
    files.sort(key=lambda p: -Path(p).stat().st_size)
    with Pool(8, initializer=_init, initargs=(SIZES,)) as pool:
        rows = pool.map(job, files, chunksize=1)
    whole = sum(r["whole"] for r in rows)
    print(f"{len(rows)} streams, whole {whole / 2**20:.2f} MiB (files {sum(r['file'] for r in rows) / 2**20:.2f})")
    for mib in SIZES:
        cut = sum(r[mib] for r in rows)
        n = sum(1 for r in rows if r[f"parts{mib}"] > 1)
        print(f"  parts of {mib} MiB: {cut / 2**20:.2f} MiB ({(cut - whole) / 2**20:+.2f}, {100 * (cut / whole - 1):+.2f}%), "
              f"{n} streams cut, most parts {max(r[f'parts{mib}'] for r in rows)}")


def _init(sizes):
    global SIZES
    SIZES = sizes


if __name__ == "__main__":
    main()
