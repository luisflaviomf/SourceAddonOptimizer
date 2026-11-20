"""Three runs side by side (centre crops, exposure matched to the first), FLIP of each against the first.
python clab_crops3.py <run1> <run2> <run3> <views> <out dir>"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from clab_compare import LAB, flip, load, match  # noqa: E402


def crop(img):
    h, w = img.shape[:2]
    cw, ch = 800, 450
    y, x = (h - ch) // 2, (w - cw) // 2
    return img[y:y + ch, x:x + cw]


def main():
    runs, views, out = sys.argv[1:4], sys.argv[4], Path(sys.argv[5])
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    tags = sorted({p.stem.rsplit("_", 1)[0] for p in (LAB / "clab" / runs[0]).glob("*.png")})
    for tag in tags:
        panels = []
        for v in views:
            imgs = [load(r, f"{tag}_{v}.png") for r in runs]
            if any(i is None for i in imgs):
                continue
            a = crop(imgs[0])
            cs = [a] + [match(a, crop(i)) for i in imgs[1:]]
            fl = [flip(a, c)[0] for c in cs[1:]]
            rows.append({"tag": tag, "view": v, "flip": fl})
            panels.append((v, cs, fl))
        if not panels:
            continue
        w, h = panels[0][1][0].shape[1], panels[0][1][0].shape[0]
        sheet = Image.new("RGB", (3 * w + 16, len(panels) * (h + 20)), (30, 30, 30))
        d = ImageDraw.Draw(sheet)
        for k, (v, cs, fl) in enumerate(panels):
            y = k * (h + 20)
            d.text((4, y + 4), f"{tag} vista {v.upper()}: {runs[0]} | {runs[1]} (FLIP {fl[0]:.3f}) | {runs[2]} (FLIP {fl[1]:.3f})",
                   fill=(255, 255, 255))
            for j, c in enumerate(cs):
                sheet.paste(Image.fromarray((c * 255).astype(np.uint8)), (j * (w + 8), y + 20))
        sheet.save(out / f"{tag}.jpg", quality=90)
    (out / "crops3.json").write_text(json.dumps(rows, indent=1))
    for j, r in enumerate(runs[1:]):
        print(f"{r}: FLIP vs {runs[0]} mean {np.mean([x['flip'][j] for x in rows]):.4f}")


if __name__ == "__main__":
    main()
