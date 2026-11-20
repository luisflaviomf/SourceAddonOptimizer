"""Side-by-side crops (input | today in game | variant in game) of the worst textures of a texexp run.

python sidebyside.py <texexp json> <variant> <out dir> [n] : for the n textures where the variant's final PSNR
falls most below today's (and the n with the lowest final PSNR today), a 3-panel PNG of the 256x256 crop with
the largest error, nearest-neighbour x2.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))
LAB = Path(r"C:/GModLab/revisao/claude")


def worst_crop(ref, test, side=128):
    err = ((ref[..., :3] - test[..., :3]) ** 2).sum(-1)
    h, w = err.shape
    side = min(side, h, w)
    best, at = -1, (0, 0)
    for y in range(0, h - side + 1, max(1, side // 2)):
        for x in range(0, w - side + 1, max(1, side // 2)):
            e = err[y:y + side, x:x + side].mean()
            if e > best:
                best, at = e, (y, x)
    return at, side


def panel(imgs, labels, scale=2):
    h, w = imgs[0].shape[:2]
    out = Image.new("RGB", (len(imgs) * (w * scale + 8), h * scale + 22), (32, 32, 32))
    d = ImageDraw.Draw(out)
    for i, (im, lab) in enumerate(zip(imgs, labels)):
        pil = Image.fromarray(np.clip(im[..., :3], 0, 255).astype(np.uint8)).resize((w * scale, h * scale), Image.NEAREST)
        out.paste(pil, (i * (w * scale + 8), 22))
        d.text((i * (w * scale + 8) + 4, 4), lab, fill=(255, 255, 255))
    return out


def render(png, blob_today, data_variant, variant, codec_variant, out, title):
    import texexp
    from texture_stream import codec as c0
    ref = np.asarray(Image.open(png).convert("RGBA")).astype(np.float64)
    today = texexp.final_rgba(c0, Path(blob_today).read_bytes())
    var = texexp.final_rgba(codec_variant, data_variant)
    (y, x), s = worst_crop(ref, today)
    crops = [a[y:y + s, x:x + s] for a in (ref, today, var)]
    panel(crops, ["entrada", "hoje no jogo", variant]).save(out)
