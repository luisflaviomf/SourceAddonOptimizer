"""Work size of the n largest GMOS streams: vertices, strip places, triangles, decompressed bytes (header only).
python gmos_count.py [n] [tag]"""
import lzma
import struct
import sys
from pathlib import Path

LAB = Path(r"C:/GModLab/revisao/claude")


def header(blob):
    pos = 20
    (ntex,) = struct.unpack_from("<I", blob, pos); pos += 4
    for _ in range(ntex):
        pos = blob.index(b"\0", pos) + 1
    nfam, nref = struct.unpack_from("<II", blob, pos); pos += 8 + 2 * nfam * nref
    (ng,) = struct.unpack_from("<I", blob, pos); pos += 4
    groups = []
    for _ in range(ng):
        b, m, slot, bone, flags, nv, nch = struct.unpack_from("<4HBIH", blob, pos); pos += struct.calcsize("<4HBIH")
        lens = struct.unpack_from(f"<{nch}I", blob, pos); pos += 4 * nch + 48
        groups.append((flags, nv, lens))
    return groups


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    tag = sys.argv[2] if len(sys.argv) > 2 else "cb_gp"
    files = []
    for gr in ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]:
        files += list((LAB / tag / gr / f"{gr}_merged_optimized" / "data_static" / "gmo").iterdir())
    files.sort(key=lambda p: -p.stat().st_size)
    T = dict(files=0, comp=0, raw=0, nv=0, places=0, tris=0, tan_nv=0, groups=0, meshes=0)
    for p in files[:n]:
        data = p.read_bytes()
        d = bytearray(data[5:])
        d[5:13] = b"\xff" * 8
        blob = lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)
        T["files"] += 1
        T["comp"] += len(data)
        T["raw"] += len(blob)
        for flags, nv, lens in header(blob):
            T["groups"] += 1
            T["nv"] += nv
            T["places"] += sum(lens)
            T["tris"] += sum(max(0, x - 2) for x in lens)
            T["meshes"] += len(lens)
            if flags % 2 == 1:
                T["tan_nv"] += nv
    print({k: (round(v / 2**20, 2) if k in ("comp", "raw") else v) for k, v in T.items()})


if __name__ == "__main__":
    main()
