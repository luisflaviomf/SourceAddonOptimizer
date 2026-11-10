"""Lossless PNG recompression (worker command `pnglossless`, png_lossless/)."""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import png_lossless  # noqa: E402
from png_lossless import describe, find_oxipng, optimize_tree, same_pixels  # noqa: E402
from png_lossless.cli import main as cli_main  # noqa: E402

pytestmark = pytest.mark.skipif(find_oxipng() is None, reason="tools/oxipng/oxipng.exe not on this machine")


def _rgba(path: Path, arr: np.ndarray, **save) -> np.ndarray:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(path, compress_level=0, **save)
    return arr


def _pixels(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGBA")).copy()


def _chunks(path: Path) -> list[bytes]:
    data = path.read_bytes()
    out, pos = [], 8
    while pos + 8 <= len(data):
        length, = struct.unpack(">I", data[pos:pos + 4])
        out.append(data[pos + 4:pos + 8])
        pos += 12 + length
    return out


def _addon(tmp_path: Path) -> tuple[Path, dict[str, np.ndarray]]:
    """An addon with one PNG per kind of content the recompression treats differently."""
    rng = np.random.default_rng(3)
    n = 96
    yy, xx = np.mgrid[0:n, 0:n]
    addon = tmp_path / "addon ação"
    mat = addon / "materials"
    ref = {}
    noise = rng.integers(0, 256, (n, n, 4), dtype=np.uint8)
    ref["materials/entities/noise.png"] = _rgba(mat / "entities" / "noise.png", noise)
    opaque = noise.copy()
    opaque[..., 3] = 255
    ref["materials/entities/opaque.png"] = _rgba(mat / "entities" / "opaque.png", opaque)
    palette = rng.integers(0, 256, (12, 4), dtype=np.uint8)
    ref["materials/hud/few_colours.png"] = _rgba(mat / "hud" / "few_colours.png", palette[(xx // 8 + yy // 8) % 12])
    gray = (xx * 2).astype(np.uint8)
    ref["materials/hud/gray.png"] = _rgba(mat / "hud" / "gray.png", np.dstack([gray, gray, gray, np.full_like(gray, 255)]))
    bw = np.where((xx // 12 + yy // 12) % 2 == 0, 255, 0).astype(np.uint8)
    ref["materials/hud/UPPER.PNG"] = _rgba(mat / "hud" / "UPPER.PNG", np.dstack([bw, bw, bw, bw]))
    (addon / "lua").mkdir()
    (addon / "lua" / "x.lua").write_text("print('x')", encoding="utf-8")
    return addon, ref


def test_describe_reads_the_storage_format(tmp_path):
    arr = np.zeros((4, 4, 4), np.uint8)
    Image.fromarray(arr).save(tmp_path / "rgba.png")
    Image.fromarray(arr[..., :3]).save(tmp_path / "rgb.png")
    Image.fromarray(arr[..., 0]).save(tmp_path / "gray.png")
    Image.fromarray(arr[..., 0]).convert("P").save(tmp_path / "pal.png", transparency=0)
    Image.fromarray(arr[..., 0].astype(np.uint16)).save(tmp_path / "deep.png")
    assert describe(tmp_path / "rgba.png").storage == (6, 8, False)
    assert describe(tmp_path / "rgb.png").storage == (2, 8, False)
    assert describe(tmp_path / "gray.png").storage == (0, 8, False)
    assert describe(tmp_path / "pal.png").storage == (3, 8, True)
    assert describe(tmp_path / "deep.png")[1] == 16
    (tmp_path / "fake.png").write_bytes(b"\xff\xd8\xff\xe0 not a png")
    assert describe(tmp_path / "fake.png") is None
    assert describe(tmp_path / "missing.png") is None


def test_every_png_gets_smaller_with_the_same_pixels(tmp_path):
    addon, ref = _addon(tmp_path)
    before = {rel: (addon / rel).stat().st_size for rel in ref}
    lua_before = (addon / "lua" / "x.lua").read_bytes()
    work = tmp_path / "work"
    report = optimize_tree(addon, work=work, threads=2, log=lambda *a, **k: None)
    assert report["files"] == 5 and report["changed"] == 5 and not report["failed"] and not report["skipped"]
    for rel, arr in ref.items():
        assert (addon / rel).stat().st_size < before[rel], rel
        assert np.array_equal(_pixels(addon / rel), arr), rel
    assert report["before"] == sum(before.values())
    assert report["after"] == sum((addon / rel).stat().st_size for rel in ref)
    # smaller storage formats were used where the pixels allow it
    assert describe(addon / "materials/entities/noise.png").storage == (6, 8, False)
    assert describe(addon / "materials/entities/opaque.png").storage == (2, 8, False)
    assert describe(addon / "materials/hud/few_colours.png")[0] == 3
    assert describe(addon / "materials/hud/gray.png")[0] == 0
    assert report["format_changed"] == 4
    # nothing else was touched and nothing was left behind
    assert (addon / "lua" / "x.lua").read_bytes() == lua_before
    assert sorted(p.relative_to(addon).as_posix().lower() for p in addon.rglob("*") if p.is_file()) == \
        sorted([rel.lower() for rel in ref] + ["lua/x.lua"])
    assert not list(work.iterdir())
    # a second run finds nothing left to gain and changes no byte
    snapshot = {rel: (addon / rel).read_bytes() for rel in ref}
    again = optimize_tree(addon, work=work, threads=2, log=lambda *a, **k: None)
    assert again["changed"] == 0 and again["skipped"] == {"not smaller": 5}
    assert snapshot == {rel: (addon / rel).read_bytes() for rel in ref}


def test_the_result_does_not_depend_on_the_thread_count(tmp_path):
    a, ref = _addon(tmp_path / "one")
    b, _ = _addon(tmp_path / "many")
    optimize_tree(a, threads=1, log=lambda *args, **k: None)
    optimize_tree(b, threads=6, log=lambda *args, **k: None)
    for rel in ref:
        assert (a / rel).read_bytes() == (b / rel).read_bytes(), rel


def test_colour_chunks_stay_and_editor_text_goes(tmp_path):
    meta = PngInfo()
    meta.add_text("Software", "an image editor " * 40)
    meta.add(b"gAMA", struct.pack(">I", 50000))
    arr = np.random.default_rng(1).integers(0, 256, (64, 64, 4), dtype=np.uint8)
    path = tmp_path / "a" / "materials" / "x.png"
    _rgba(path, arr, pnginfo=meta, dpi=(96, 96))
    assert {b"tEXt", b"gAMA", b"pHYs"} <= set(_chunks(path))
    report = optimize_tree(tmp_path / "a", threads=1, log=lambda *a, **k: None)
    assert report["changed"] == 1
    kinds = set(_chunks(path))
    assert b"gAMA" in kinds and b"pHYs" in kinds and b"tEXt" not in kinds
    assert np.array_equal(_pixels(path), arr)


def test_files_the_step_does_not_understand_stay_byte_for_byte(tmp_path):
    addon = tmp_path / "addon"
    mat = addon / "materials"
    mat.mkdir(parents=True)
    Image.fromarray(np.arange(64 * 64, dtype=np.uint16).reshape(64, 64)).save(mat / "deep16.png", compress_level=0)
    frames = [Image.fromarray(np.full((16, 16, 4), v, np.uint8)) for v in (10, 200)]
    frames[0].save(mat / "animated.png", save_all=True, append_images=frames[1:], compress_level=0)
    (mat / "jpeg_inside.png").write_bytes(b"\xff\xd8\xff\xe0" + bytes(200))
    (mat / "empty.png").write_bytes(b"")
    truncated = mat / "truncated.png"
    Image.fromarray(np.zeros((64, 64, 4), np.uint8)).save(truncated, compress_level=0)
    truncated.write_bytes(truncated.read_bytes()[:400])
    before = {p.name: p.read_bytes() for p in mat.iterdir()}
    report = optimize_tree(addon, threads=2, log=lambda *a, **k: None)
    assert {p.name: p.read_bytes() for p in mat.iterdir()} == before
    assert report["changed"] == 0 and report["after"] == report["before"]
    assert report["skipped"].get("16-bit") == 1 and report["skipped"].get("animated") == 1
    assert report["skipped"].get("not a PNG inside") == 2


def test_a_hard_linked_original_is_never_modified(tmp_path):
    original = tmp_path / "original.png"
    yy, xx = np.mgrid[0:64, 0:64]
    arr = _rgba(original, np.dstack([xx * 4, yy * 4, (xx + yy) * 2, 255 - xx * 3]).astype(np.uint8))
    original_bytes = original.read_bytes()
    addon = tmp_path / "copy" / "materials"
    addon.mkdir(parents=True)
    os.link(original, addon / "icon.png")
    report = optimize_tree(tmp_path / "copy", threads=1, log=lambda *a, **k: None)
    assert report["changed"] == 1
    assert original.read_bytes() == original_bytes          # the link was replaced, not written through
    assert (addon / "icon.png").stat().st_size < len(original_bytes)
    assert np.array_equal(_pixels(addon / "icon.png"), arr)


def test_a_format_the_game_was_not_seen_reading_is_not_written(tmp_path, monkeypatch):
    addon, ref = _addon(tmp_path)
    before = {rel: (addon / rel).stat().st_size for rel in ref}
    monkeypatch.setattr(png_lossless, "GAME_FORMATS", frozenset({(6, 8, False), (2, 8, False)}))
    report = optimize_tree(addon, threads=2, log=lambda *a, **k: None)
    assert report["kept_format"] == 3 and report["changed"] == 5
    for rel, arr in ref.items():
        assert describe(addon / rel).storage in {(6, 8, False), (2, 8, False)}, rel
        assert (addon / rel).stat().st_size < before[rel]
        assert np.array_equal(_pixels(addon / rel), arr)


def test_keep_format_only_recompresses(tmp_path):
    addon, ref = _addon(tmp_path)
    report = optimize_tree(addon, threads=2, keep_format=True, log=lambda *a, **k: None)
    assert report["changed"] == 5 and report["format_changed"] == 0
    for rel, arr in ref.items():
        assert describe(addon / rel).storage == (6, 8, False)
        assert np.array_equal(_pixels(addon / rel), arr)


def test_only_the_listed_files_are_processed(tmp_path):
    addon, ref = _addon(tmp_path)
    before = {rel: (addon / rel).read_bytes() for rel in ref}
    listed = ["materials/entities/noise.png", str(addon / "materials" / "hud" / "gray.png"), "materials/missing.png",
              "lua/x.lua"]
    report = optimize_tree(addon, threads=2, files=listed, log=lambda *a, **k: None)
    assert report["files"] == 2 and report["changed"] == 2
    for rel in ref:
        changed = rel in ("materials/entities/noise.png", "materials/hud/gray.png")
        assert ((addon / rel).read_bytes() != before[rel]) == changed, rel


def test_a_tool_that_changes_pixels_is_rejected(tmp_path):
    """The result is decoded again and compared; a smaller file with other pixels never replaces the original."""
    fake_py = tmp_path / "fake_tool.py"
    fake_py.write_text(
        "import sys\n"
        "from PIL import Image\n"
        "for f in [a for a in sys.argv[1:] if a.lower().endswith('.png')]:\n"
        "    im = Image.open(f).convert('RGBA')\n"
        "    Image.new('RGBA', im.size, (1, 2, 3, 255)).save(f, optimize=True)\n", encoding="utf-8")
    fake = tmp_path / "fake_oxipng.cmd"
    fake.write_text(f'@"{sys.executable}" "{fake_py}" %*\r\n', encoding="utf-8")
    addon, ref = _addon(tmp_path)
    before = {rel: (addon / rel).read_bytes() for rel in ref}
    lines = []
    report = optimize_tree(addon, oxipng=fake, threads=1, log=lambda *a, **k: lines.append(a[0]))
    assert report["changed"] == 0 and sorted(report["failed"]) == sorted(ref)
    assert {rel: (addon / rel).read_bytes() for rel in ref} == before
    assert report["after"] == report["before"]
    assert sum("pixels would change" in line for line in lines) == 5


def test_same_pixels_sees_one_changed_value(tmp_path):
    arr = np.random.default_rng(9).integers(0, 256, (32, 32, 4), dtype=np.uint8)
    Image.fromarray(arr).save(tmp_path / "a.png")
    Image.fromarray(arr).save(tmp_path / "same.png", compress_level=9)
    other = arr.copy()
    other[31, 31, 3] ^= 1
    Image.fromarray(other).save(tmp_path / "other.png")
    Image.fromarray(arr[:16]).save(tmp_path / "smaller.png")
    assert same_pixels(tmp_path / "a.png", tmp_path / "same.png")
    assert not same_pixels(tmp_path / "a.png", tmp_path / "other.png")
    assert not same_pixels(tmp_path / "a.png", tmp_path / "smaller.png")


def test_cli_reports_a_missing_tool_and_changes_nothing(tmp_path, capsys):
    addon, ref = _addon(tmp_path)
    before = {rel: (addon / rel).read_bytes() for rel in ref}
    assert cli_main([str(addon), "--oxipng", str(tmp_path / "no_such_tool.exe")]) == 2
    assert "[ERROR]" in capsys.readouterr().out
    assert cli_main([str(tmp_path / "no_such_addon")]) == 2
    assert {rel: (addon / rel).read_bytes() for rel in ref} == before


def test_worker_routes_pnglossless_with_unicode_paths(tmp_path):
    addon, ref = _addon(tmp_path)
    before = sum((addon / rel).stat().st_size for rel in ref)
    work = tmp_path / "trabalho ç"
    report_path = work / "relatório.json"
    listing = tmp_path / "lista.txt"
    listing.write_text("﻿" + "\n".join(ref) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "worker" / "worker_main.py"), "pnglossless", str(addon), "--work", str(work),
         "--report", str(report_path), "--files-from", str(listing), "--threads", "2"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PNGLOSSLESS_PROGRESS: 5|5|" in result.stdout
    assert "[PNGLOSSLESS] files=5 changed=5 " in result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["before"] == before and report["after"] < before and report["changed"] == 5
    for rel, arr in ref.items():
        assert np.array_equal(_pixels(addon / rel), arr)


def test_minimal_png_writer_matches_what_the_step_reads(tmp_path):
    """describe() parses chunks itself: check it against a hand-built file with tRNS after PLTE."""
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + bytes([0b01010101] * 2) for _ in range(16))
    data = (png_lossless.SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 1, 3, 0, 0, 0))
            + chunk(b"PLTE", bytes([255, 0, 0, 0, 0, 255])) + chunk(b"tRNS", bytes([0, 255]))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    (tmp_path / "p1.png").write_bytes(data)
    info = describe(tmp_path / "p1.png")
    assert info.storage == (3, 1, True) and info.storage in png_lossless.GAME_FORMATS
    assert _pixels(tmp_path / "p1.png")[0, 0].tolist() == [255, 0, 0, 0]
    assert _pixels(tmp_path / "p1.png")[0, 1].tolist() == [0, 0, 255, 255]
