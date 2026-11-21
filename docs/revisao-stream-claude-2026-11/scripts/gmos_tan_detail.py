"""Which tangents moved with the strip order (review/claude lab): per vertex key (pos, normal, uv) the set of
tangents the meshes carry, old vs new; keys that carry several tangents (duplicated vertices) are counted apart.
python gmos_tan_detail.py <runtime.lua> <old dir> <new dir> [n]"""
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gmos_equal as E  # noqa: E402


def tangents(meshes):
    t = defaultdict(set)
    for d in meshes:
        for k in range(len(d) // 12):
            v = tuple(d[12 * k:12 * k + 12])
            t[v[:8]].add(v[8:])
    return t


def main():
    rt = Path(sys.argv[1]).read_bytes()
    old, new = Path(sys.argv[2]), Path(sys.argv[3])
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    files = sorted(old.iterdir(), key=lambda p: -p.stat().st_size)[:n]
    keys = dup = moved = flips = 0
    angles = []
    for p in files:
        _t, ga = E.build(rt, p.read_bytes(), True)
        _t, gb = E.build(rt, (new / p.name).read_bytes(), True)
        ta, tb = tangents(E.meshes(ga)), tangents(E.meshes(gb))
        for k, sa in ta.items():
            keys += 1
            sb = tb[k]
            if len(sa) > 1 or len(sb) > 1:
                dup += 1
                continue
            if sa != sb:
                moved += 1
                (a,), (b,) = sa, sb
                if a[3] != b[3]:
                    flips += 1
                else:
                    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a[:3], b[:3]))))
                    angles.append(math.degrees(math.acos(dot)))
    angles.sort()
    print(f"{len(files)} streams: {keys} vertex keys, {dup} carry several tangents (duplicated vertices, not "
          f"compared), {moved} moved ({100 * moved / keys:.4f}%): {flips} handedness flips, angles of the others "
          f"median {angles[len(angles) // 2] if angles else 0:.2f} max {angles[-1] if angles else 0:.2f} deg")


if __name__ == "__main__":
    main()
