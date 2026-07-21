import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


THUMBNAIL = 360
LABEL_HEIGHT = 54


def checker_composite(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    checker = Image.new("RGBA", rgba.size)
    pixels = checker.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            value = 80 if ((x // 16) + (y // 16)) % 2 else 176
            pixels[x, y] = (value, value, value, 255)
    return Image.alpha_composite(checker, rgba).convert("RGB")


def fitted(image: Image.Image) -> Image.Image:
    copy = checker_composite(image)
    copy.thumbnail((THUMBNAIL, THUMBNAIL), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (THUMBNAIL, THUMBNAIL), (32, 32, 32))
    canvas.paste(copy, ((THUMBNAIL - copy.width) // 2, (THUMBNAIL - copy.height) // 2))
    return canvas


def alpha_thumbnail(image: Image.Image) -> Image.Image:
    alpha = image.convert("RGBA").getchannel("A").convert("RGB")
    alpha.thumbnail((THUMBNAIL, THUMBNAIL), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", (THUMBNAIL, THUMBNAIL), (32, 32, 32))
    canvas.paste(alpha, ((THUMBNAIL - alpha.width) // 2, (THUMBNAIL - alpha.height) // 2))
    return canvas


def winner_image(texture: dict, work: Path) -> Path:
    digest = hashlib.sha256(texture["RelativePath"].encode("utf-8")).hexdigest()[:16]
    texture_work = work / digest
    encoder = texture["WinnerEncoder"]
    if not encoder:
        return texture_work / "reference.png"
    if encoder in {"current-exact", "current-top-original-mips"}:
        return texture_work / "current-aligned.png"
    if encoder.startswith("original-mip-"):
        return texture_work / encoder / "aligned.png"
    return texture_work / f"x{texture['WinnerScaleDivisor']}" / encoder / "aligned.png"


def selected(texture: dict) -> bool:
    tags = set(texture["Tags"])
    important = {
        "semantic:normal",
        "semantic:glass",
        "semantic:emissive",
        "semantic:decal",
        "structure:cubemap",
        "version:7.5",
    }
    return (
        texture["WinnerEncoder"] != "current-exact"
        or bool(tags & important)
        or texture["RelativePath"].endswith("gauges_e.vtf")
    )


def render_panel(texture: dict, work: Path, output: Path) -> Path:
    digest = hashlib.sha256(texture["RelativePath"].encode("utf-8")).hexdigest()[:16]
    texture_work = work / digest
    paths = [
        texture_work / "reference.png",
        texture_work / "current-aligned.png",
        winner_image(texture, work),
    ]
    images = [Image.open(path).convert("RGBA") for path in paths]
    has_alpha = "alpha:opaque" not in set(texture["Tags"])
    rows = 2 if has_alpha else 1
    panel = Image.new("RGB", (THUMBNAIL * 3, LABEL_HEIGHT + THUMBNAIL * rows), (20, 20, 20))
    draw = ImageDraw.Draw(panel)
    font = ImageFont.load_default(size=16)
    title = Path(texture["RelativePath"]).name
    metrics = texture.get("MaximumMetrics") or texture["CurrentMetrics"]
    subtitle = (
        f"{texture['WinnerEncoder'] or 'preserved'} x{texture['WinnerScaleDivisor']} | "
        f"{texture['OriginalBytes']} -> {texture['CurrentBytes']} -> {texture['MaximumBytes']} bytes | "
        f"SSIM {metrics['RgbSsim']:.4f} FLIP {metrics['FlipMean']:.4f}/{metrics['FlipP95']:.4f}"
    )
    draw.text((8, 4), title, fill="white", font=font)
    draw.text((8, 27), subtitle, fill=(205, 205, 205), font=ImageFont.load_default(size=12))
    for index, (label, image) in enumerate(zip(("Original", "Current 2x", "Maximum"), images)):
        tile = fitted(image)
        panel.paste(tile, (index * THUMBNAIL, LABEL_HEIGHT))
        ImageDraw.Draw(panel).rectangle((index * THUMBNAIL, LABEL_HEIGHT, index * THUMBNAIL + 104, LABEL_HEIGHT + 24), fill=(0, 0, 0))
        ImageDraw.Draw(panel).text((index * THUMBNAIL + 6, LABEL_HEIGHT + 4), label, fill="white", font=font)
        if has_alpha:
            panel.paste(alpha_thumbnail(image), (index * THUMBNAIL, LABEL_HEIGHT + THUMBNAIL))
    safe = texture["RelativePath"].replace("/", "__").replace(" ", "_")
    panel_path = output / f"{safe}.png"
    panel.save(panel_path)
    for image in images:
        image.close()
    return panel_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    report = json.loads((root / "maximum-results.json").read_text(encoding="utf-8"))
    output = root / "previews"
    output.mkdir(parents=True, exist_ok=True)
    work = root / "candidate-work"
    panels = [render_panel(texture, work, output) for texture in report["Textures"] if selected(texture)]

    contact_width = THUMBNAIL * 3
    contact_thumb_height = 220
    contact = Image.new("RGB", (contact_width, contact_thumb_height * len(panels)), (16, 16, 16))
    for index, path in enumerate(panels):
        with Image.open(path) as image:
            thumbnail = image.convert("RGB")
            thumbnail.thumbnail((contact_width, contact_thumb_height), Image.Resampling.LANCZOS)
            contact.paste(thumbnail, (0, index * contact_thumb_height))
    contact_path = output / "contact-sheet.png"
    contact.save(contact_path)
    print(json.dumps({"panels": len(panels), "contact_sheet": str(contact_path)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
