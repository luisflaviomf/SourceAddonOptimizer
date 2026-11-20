"""Compare two clab capture runs (review/claude lab): per model and view, FLIP (LDR, 67 ppd) over the whole frame
and over the pixels that differ between the reference runs' noise, plus side-by-side crops of the most
different region. python clab_compare.py <ref run> <test run> [<noise run>] [out dir for images]"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

LAB = Path(r"C:/GModLab/revisao/claude")


def load(run, name):
    p = LAB / "clab" / run / name
    return np.asarray(Image.open(p).convert("RGB")).astype(np.float32) / 255 if p.exists() else None


def match(ref, test):
    """test with its exposure matched to ref: one gain per channel in linear light, fitted on the whole frame
    (the scene changes brightness between game sessions: HDR adaptation / map light)"""
    a, b = ref ** 2.2, test ** 2.2
    k = (a * b).sum((0, 1)) / np.maximum((b * b).sum((0, 1)), 1e-9)
    return np.clip(b * k, 0, 1) ** (1 / 2.2)


def flip(a, b):
    import flip_evaluator as fe
    m, mean, _ = fe.evaluate(a, b, "LDR", applyMagma=False)
    return float(mean), m


def crop_box(diff, side=360):
    h, w = diff.shape
    best, at = -1, (0, 0)
    for y in range(0, h - side + 1, side // 3):
        for x in range(0, w - side + 1, side // 3):
            s = diff[y:y + side, x:x + side].sum()
            if s > best:
                best, at = s, (y, x)
    return at, side


def main():
    ref, test = sys.argv[1], sys.argv[2]
    noise = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "-" else None
    outdir = Path(sys.argv[4]) if len(sys.argv) > 4 else None
    names = sorted(p.name for p in (LAB / "clab" / ref).glob("*.png"))
    rows = []
    for n in names:
        a, b = load(ref, n), load(test, n)
        if a is None or b is None:
            continue
        b = match(a, b)
        f, fmap = flip(a, b)
        r = {"name": n, "flip": f, "max": float(np.abs(a - b).max()), "changed": float((np.abs(a - b).max(2) > 0.02).mean())}
        if noise:
            c = load(noise, n)
            if c is not None:
                r["flip_noise"] = flip(a, match(a, c))[0]
        rows.append(r)
        if outdir is not None:
            d = np.abs(a - b).max(2)
            (y, x), s = crop_box(d)
            panel = Image.new("RGB", (2 * s + 8, s + 20), (30, 30, 30))
            dr = ImageDraw.Draw(panel)
            for k, (img, lab) in enumerate(((a, ref), (b, test))):
                panel.paste(Image.fromarray((img[y:y + s, x:x + s] * 255).astype(np.uint8)), (k * (s + 8), 20))
                dr.text((k * (s + 8) + 4, 4), lab, fill=(255, 255, 255))
            outdir.mkdir(parents=True, exist_ok=True)
            panel.save(outdir / f"{Path(n).stem}.png")
    (LAB / "out" / f"clab_{ref}_vs_{test}.json").write_text(json.dumps(rows, indent=1))
    by_view = {}
    for r in rows:
        by_view.setdefault(r["name"].rsplit("_", 1)[1][0], []).append(r)
    for v, rs in sorted(by_view.items()):
        msg = f"view {v}: n={len(rs)} FLIP mean {np.mean([r['flip'] for r in rs]):.4f} max {max(r['flip'] for r in rs):.4f}"
        if any("flip_noise" in r for r in rs):
            msg += f" | noise (ref vs ref) mean {np.mean([r.get('flip_noise', 0) for r in rs]):.4f}"
        print(msg)
    for r in sorted(rows, key=lambda r: -r["flip"])[:8]:
        print(f"  {r['name']:40} FLIP {r['flip']:.4f} noise {r.get('flip_noise', float('nan')):.4f} changed {r['changed']:.4f}")


if __name__ == "__main__":
    main()
