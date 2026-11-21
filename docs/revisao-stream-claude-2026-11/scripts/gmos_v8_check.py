"""GMOS format 7 (lab/<old>) against format 8 (lab/<new>), every stream: the decompressed blob must be the same
(then every decoder gives the same geometry), streams that fit one part byte-identical, sizes and part counts.
python gmos_v8_check.py cb_vc cb_v8"""
import sys
from pathlib import Path

sys.path.insert(0, r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
from model_stream import geometry  # noqa: E402

LAB = Path(r"C:/GModLab/revisao/claude")
GROUPS = ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]


def main():
    old, new = sys.argv[1], sys.argv[2]
    so = sn = n = cut = same_bytes = 0
    parts = []
    bad = []
    for g in GROUPS:
        ro = LAB / old / g / f"{g}_merged_optimized" / "data_static" / "gmo"
        rn = LAB / new / g / f"{g}_merged_optimized" / "data_static" / "gmo"
        fo = {p.name: p for p in ro.iterdir()}
        fn = {p.name: p for p in rn.iterdir()}
        assert set(fo) == set(fn), (g, len(set(fo) ^ set(fn)))
        for name in sorted(fo):
            a, b = fo[name].read_bytes(), fn[name].read_bytes()
            n += 1
            so += len(a)
            sn += len(b)
            if a == b:
                same_bytes += 1
            if b[4] == 8:
                cut += 1
                parts.append(b[5])
            if geometry.stream_blob(a)[1] != geometry.stream_blob(b)[1]:
                bad.append(f"{g}/{name}")
    print(f"{n} streams: {same_bytes} byte-identical, {cut} in format 8 (parts {min(parts, default=0)}-"
          f"{max(parts, default=0)}), blob different in {len(bad)} {bad[:5]}; "
          f"{so / 2**20:.2f} -> {sn / 2**20:.2f} MiB ({(sn - so) / 2**20:+.2f})")


if __name__ == "__main__":
    main()
