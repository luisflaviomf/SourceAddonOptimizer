"""Centre crops (the camera always aims at the model's centre) of two capture runs, exposure matched, FLIP inside
the crop, and contact sheets. python clab_crops.py <ref> <test> <noise ref> <views> <out dir>
Crop: 640x360 around the centre for near views, 240x135 (shown x3) for the far view D."""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from clab_compare import LAB, flip, load, match  # noqa: E402


def crop(img, view):
    h, w = img.shape[:2]
    cw, ch = (240, 135) if view == "d" else (640, 360)
    y, x = (h - ch) // 2, (w - cw) // 2
    return img[y:y + ch, x:x + cw]


def main():
    ref, test, noise, views, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], Path(sys.argv[5])
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    tags = sorted({p.stem.rsplit("_", 1)[0] for p in (LAB / "clab" / ref).glob("*.png")})
    for tag in tags:
        panels = []
        for v in views:
            n = f"{tag}_{v}.png"
            a, b, c = load(ref, n), load(test, n), load(noise, n)
            if a is None or b is None:
                continue
            a, b = crop(a, v), crop(match(a, b), v) if False else crop(b, v)
            b = match(a, b)
            r = {"tag": tag, "view": v, "flip": flip(a, b)[0]}
            if c is not None:
                c = match(a, crop(c, v))
                r["noise"] = flip(a, c)[0]
            rows.append(r)
            scale = 3 if v == "d" else 1
            pa = Image.fromarray((a * 255).astype(np.uint8)).resize((a.shape[1] * scale, a.shape[0] * scale), Image.NEAREST)
            pb = Image.fromarray((b * 255).astype(np.uint8)).resize((b.shape[1] * scale, b.shape[0] * scale), Image.NEAREST)
            panels.append((v, pa, pb, r))
        if not panels:
            continue
        W = max(p[1].width for p in panels) * 2 + 8
        H = sum(p[1].height + 20 for p in panels)
        sheet = Image.new("RGB", (W, H), (30, 30, 30))
        d = ImageDraw.Draw(sheet)
        y = 0
        for v, pa, pb, r in panels:
            d.text((4, y + 4), f"{tag} vista {v.upper()}  {ref} | {test}   FLIP {r['flip']:.3f} (ruido {r.get('noise', float('nan')):.3f})",
                   fill=(255, 255, 255))
            sheet.paste(pa, (0, y + 20))
            sheet.paste(pb, (pa.width + 8, y + 20))
            y += pa.height + 20
        sheet.save(out / f"{tag}.png")
    (out / "crops.json").write_text(json.dumps(rows, indent=1))
    for v in views:
        rs = [r for r in rows if r["view"] == v]
        if rs:
            print(f"view {v}: FLIP mean {np.mean([r['flip'] for r in rs]):.4f} (noise {np.mean([r.get('noise', np.nan) for r in rs]):.4f}); "
                  f"worst {max(rs, key=lambda r: r['flip'] - r.get('noise', 0))}")


if __name__ == "__main__":
    main()
