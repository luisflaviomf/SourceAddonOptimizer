"""Inventário dos sons: originais (bancada) e Custo-benefício (lab/cb/<grupo>, WAV pelo código do produto; MP3 e
OGG ficam como no original, o assistente não mexe neles).

python inventory.py  -> out/inventory.json (uma linha por arquivo) + tabelas impressas
"""
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sndlib import GROUPS, LAB, MiB, parse_wav, probe, sounds, src_root


def describe(path):
    ext = path.suffix.lower()
    row = {"bytes": path.stat().st_size, "ext": ext}
    if ext == ".wav":
        w = parse_wav(path)
        if w is None:
            # not RIFF: maybe another codec under a .wav name
            pr = probe(path)
            row.update(fmt="?" if pr is None else "nao-RIFF:" + pr["codec"], rate=pr and pr["rate"] or 0,
                       channels=pr and pr["channels"] or 0, duration=pr and pr["duration"] or 0, loop=False)
        else:
            row.update(fmt=w["fmt"], rate=w["rate"], channels=w["channels"], duration=w["duration"], loop=w["loop"],
                       cue=w["cue"], smpl=w["smpl"], labels=w["labels"], align=w["align"], bits=w["bits"],
                       frames=w["frames"], data_bytes=w["data_bytes"],
                       extra=[c for c in w["chunks"] if c not in ("fmt ", "data", "fact", "cue ", "smpl", "LIST")])
    else:
        pr = probe(path)
        if pr is None:
            row.update(fmt="?", rate=0, channels=0, duration=0, loop=False)
        else:
            row.update(fmt=pr["codec"], rate=pr["rate"], channels=pr["channels"], duration=pr["duration"], loop=False,
                       bit_rate=pr["bit_rate"])
    return row


def main():
    rows = {}
    jobs = []
    for g in GROUPS:
        root = src_root(g)
        for p in sounds(root):
            rel = p.relative_to(root).as_posix()
            jobs.append((g, rel, "orig", p))
            if p.suffix.lower() == ".wav":
                cb = LAB / "cb" / g / rel
                if cb.exists():
                    jobs.append((g, rel, "cb", cb))
    with ThreadPoolExecutor(16) as ex:
        for (g, rel, kind, _), row in zip(jobs, ex.map(lambda j: describe(j[3]), jobs)):
            rows.setdefault(f"{g}/{rel}", {"group": g, "rel": rel})[kind] = row
    for r in rows.values():
        if "cb" not in r:
            r["cb"] = r["orig"]            # MP3/OGG: untouched by the assistant
    (LAB / "out" / "inventory.json").write_text(json.dumps(rows, indent=0), encoding="utf-8")

    def table(kind, keyf, title):
        acc = defaultdict(lambda: [0, 0, 0.0])
        for r in rows.values():
            k = keyf(r, r[kind])
            a = acc[k]
            a[0] += 1
            a[1] += r[kind]["bytes"]
            a[2] += r[kind].get("duration") or 0
        print(f"\n== {title}")
        for k in sorted(acc, key=lambda k: -acc[k][1]):
            n, b, d = acc[k]
            print(f"{str(k):58s} {n:5d} arq {b / MiB:8.2f} MiB {d / 60:8.1f} min")
        print(f"{'TOTAL':58s} {sum(a[0] for a in acc.values()):5d} arq {sum(a[1] for a in acc.values()) / MiB:8.2f} MiB "
              f"{sum(a[2] for a in acc.values()) / 60:8.1f} min")

    for kind, name in (("orig", "ORIGINAL"), ("cb", "CUSTO-BENEFICIO")):
        table(kind, lambda r, s: r["group"], f"{name} por grupo")
        table(kind, lambda r, s: (r["group"], s["ext"]), f"{name} por grupo e extensao")
        table(kind, lambda r, s: (s["ext"], s["fmt"], s["rate"], s["channels"]), f"{name} por formato/taxa/canais")
        table(kind, lambda r, s: (s["ext"], "loop" if s.get("loop") else "sem loop"), f"{name} por loop")


if __name__ == "__main__":
    main()
