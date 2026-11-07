"""VTF reading, exact mip-chain truncation and DXT decoding (texlab).

Truncating a VTF to start at mip level k re-uses the encoded blocks already
stored in the file, so the new top level is bit-identical to what the GPU
samples from the original at that level. No re-encoding happens.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .bcnp import bc3_to_bc1, decode_bc

FMT_NAMES = {0: "RGBA8888", 1: "ABGR8888", 2: "RGB888", 3: "BGR888", 4: "RGB565", 5: "I8", 6: "IA88",
             8: "A8", 12: "BGRA8888", 13: "DXT1", 14: "DXT3", 15: "DXT5", 16: "BGRX8888",
             20: "DXT1_ONEBITALPHA", 24: "RGBA16161616F", 25: "RGBA16161616"}
BLOCK_BYTES = {13: 8, 20: 8, 14: 16, 15: 16}
PIXEL_BYTES = {0: 4, 1: 4, 2: 3, 3: 3, 4: 2, 5: 1, 6: 2, 8: 1, 12: 4, 16: 4, 24: 8, 25: 8}
HIGHRES_TAG = b"\x30\x00\x00"
LOWRES_TAG = b"\x01\x00\x00"
NO_DATA_FLAG = 0x02

TEXTUREFLAGS_NOMIP = 0x100
TEXTUREFLAGS_ENVMAP = 0x4000
TEXTUREFLAGS_NORMAL = 0x80
TEXTUREFLAGS_ONEBITALPHA = 0x1000
TEXTUREFLAGS_EIGHTBITALPHA = 0x2000


def image_bytes(fmt: int, w: int, h: int) -> int:
    if fmt in BLOCK_BYTES:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK_BYTES[fmt]
    if fmt in PIXEL_BYTES:
        return w * h * PIXEL_BYTES[fmt]
    raise ValueError(f"unsupported VTF format {fmt}")


class VtfError(ValueError):
    pass


@dataclass
class Vtf:
    path: Path | None
    raw: bytes
    version: tuple[int, int]
    header_size: int
    width: int
    height: int
    flags: int
    frames: int
    first_frame: int
    fmt: int
    mip_count: int
    lowres_fmt: int
    lowres_w: int
    lowres_h: int
    depth: int
    resources: list[tuple[bytes, int, int]] = field(default_factory=list)  # (tag3, flags, data)
    highres_offset: int = 0
    lowres_offset: int = 0
    faces: int = 1

    @property
    def fmt_name(self) -> str:
        return FMT_NAMES.get(self.fmt, str(self.fmt))

    def mip_size(self, level: int) -> tuple[int, int]:
        return max(1, self.width >> level), max(1, self.height >> level)

    def level_bytes(self, level: int) -> int:
        w, h = self.mip_size(level)
        d = max(1, self.depth >> level)
        return image_bytes(self.fmt, w, h) * self.frames * self.faces * d

    def level_offset(self, level: int) -> int:
        """Byte offset of the first frame/face/slice of `level` (data is stored smallest mip first)."""
        offset = self.highres_offset
        for lvl in range(self.mip_count - 1, level, -1):
            offset += self.level_bytes(lvl)
        return offset

    def level_data(self, level: int, frame: int = 0, face: int = 0) -> bytes:
        w, h = self.mip_size(level)
        one = image_bytes(self.fmt, w, h)
        d = max(1, self.depth >> level)
        start = self.level_offset(level) + ((frame * self.faces + face) * d) * one
        return self.raw[start:start + one]

    @property
    def highres_bytes(self) -> int:
        return sum(self.level_bytes(l) for l in range(self.mip_count))


def read_vtf_size(path) -> tuple[int, int, int]:
    """(width, height, mip_count) from the header only, same values read_vtf reports."""
    with open(path, "rb") as fh:
        raw = fh.read(64)
    if raw[:4] != b"VTF\0":
        raise VtfError("not a VTF")
    width, height = struct.unpack_from("<HH", raw, 16)
    mip_count = struct.unpack_from("<B", raw, 56)[0]
    return width, height, mip_count


def read_vtf(path_or_bytes) -> Vtf:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw, path = bytes(path_or_bytes), None
    else:
        path = Path(path_or_bytes)
        raw = path.read_bytes()
    if raw[:4] != b"VTF\0":
        raise VtfError("not a VTF")
    major, minor, header_size = struct.unpack_from("<3I", raw, 4)
    width, height, flags, frames, first_frame = struct.unpack_from("<HHIHH", raw, 16)
    fmt, mip_count, lowres_fmt, lowres_w, lowres_h = struct.unpack_from("<iBiBB", raw, 52)
    depth = struct.unpack_from("<H", raw, 63)[0] if minor >= 2 else 1
    v = Vtf(path, raw, (major, minor), header_size, width, height, flags, frames, first_frame,
            fmt, mip_count, lowres_fmt, lowres_w, lowres_h, max(1, depth))
    v.faces = 6 if flags & TEXTUREFLAGS_ENVMAP else 1
    if flags & TEXTUREFLAGS_ENVMAP and minor < 5 and first_frame != 0xFFFF:
        v.faces = 7 if minor < 5 else 6  # legacy spheremap face (pre 7.5)
    lowres_bytes = image_bytes(lowres_fmt, lowres_w, lowres_h) if lowres_fmt >= 0 and lowres_w else 0
    if minor >= 3:
        num_resources = struct.unpack_from("<I", raw, 68)[0]
        for i in range(num_resources):
            tag = raw[80 + i * 8: 83 + i * 8]
            rflags = raw[83 + i * 8]
            data = struct.unpack_from("<I", raw, 84 + i * 8)[0]
            v.resources.append((tag, rflags, data))
            if tag == HIGHRES_TAG:
                v.highres_offset = data
            elif tag == LOWRES_TAG:
                v.lowres_offset = data
        if not v.highres_offset:
            raise VtfError("VTF has no high-res resource")
    else:
        v.lowres_offset = header_size
        v.highres_offset = header_size + lowres_bytes
    if v.highres_offset + v.highres_bytes > len(raw):
        raise VtfError("VTF image data truncated")
    return v


def truncate_mips(v: Vtf, drop: int) -> bytes:
    """Return a new VTF whose top level is the original mip `drop` (exact block copy)."""
    if drop <= 0:
        return v.raw
    if drop >= v.mip_count:
        raise VtfError("cannot drop every mip level")
    if v.depth > 1:
        raise VtfError("volume textures are not supported")
    new_w, new_h = v.mip_size(drop)
    new_mips = v.mip_count - drop
    image = v.raw[v.level_offset(v.mip_count - 1): v.level_offset(drop) + v.level_bytes(drop)]
    header = bytearray(v.raw[:v.header_size])
    struct.pack_into("<HH", header, 16, new_w, new_h)
    struct.pack_into("<B", header, 56, new_mips)
    if v.version[1] >= 3:
        # Resource directory: keep every resource, move the high-res block to the end.
        old_end = v.highres_offset + v.highres_bytes
        tail = v.raw[old_end:]  # resources stored after the image (rare)
        body = bytearray()
        offset = v.header_size
        new_resources = []
        # Data blocks that live outside the header, in original order, except the image.
        blocks = sorted((data, tag, rflags) for tag, rflags, data in v.resources
                        if not rflags & NO_DATA_FLAG and tag != HIGHRES_TAG)
        moved = {}
        for idx, (data, tag, rflags) in enumerate(blocks):
            if tag == LOWRES_TAG:
                size = image_bytes(v.lowres_fmt, v.lowres_w, v.lowres_h)
            else:
                size = struct.unpack_from("<I", v.raw, data)[0] + 4
            moved[tag] = offset
            body += v.raw[data:data + size]
            offset += size
        image_offset = offset
        body += image
        for tag, rflags, data in v.resources:
            if rflags & NO_DATA_FLAG:
                new_resources.append((tag, rflags, data))
            elif tag == HIGHRES_TAG:
                new_resources.append((tag, rflags, image_offset))
            else:
                new_resources.append((tag, rflags, moved[tag]))
        for i, (tag, rflags, data) in enumerate(new_resources):
            header[80 + i * 8: 83 + i * 8] = tag
            header[83 + i * 8] = rflags
            struct.pack_into("<I", header, 84 + i * 8, data)
        return bytes(header) + bytes(body) + (b"" if not tail else b"")
    lowres = v.raw[v.lowres_offset:v.highres_offset]
    return bytes(header) + lowres + image


# ----------------------------------------------------------------------------- decoding

def decode_level(v: Vtf, level: int = 0, frame: int = 0, face: int = 0) -> np.ndarray:
    """Decode one mip level to an (h,w,4) uint8 RGBA array."""
    w, h = v.mip_size(level)
    if v.fmt in (13, 14, 15, 20):
        return decode_bc(v.level_data(level, frame, face), w, h, v.fmt)
    data = np.frombuffer(v.level_data(level, frame, face), dtype=np.uint8)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    if v.fmt in (0, 12, 16, 1):
        px = data.reshape(h, w, 4)
        order = {0: [0, 1, 2, 3], 12: [2, 1, 0, 3], 16: [2, 1, 0, 3], 1: [3, 2, 1, 0]}[v.fmt]
        out[:] = px[..., order]
        if v.fmt == 16:
            out[..., 3] = 255
        return out
    if v.fmt in (2, 3):
        px = data.reshape(h, w, 3)
        out[..., :3] = px if v.fmt == 2 else px[..., ::-1]
        out[..., 3] = 255
        return out
    if v.fmt == 5:
        out[..., :3] = data.reshape(h, w, 1)
        out[..., 3] = 255
        return out
    if v.fmt == 6:
        px = data.reshape(h, w, 2)
        out[..., :3] = px[..., :1]
        out[..., 3] = px[..., 1]
        return out
    if v.fmt == 8:
        out[..., 3] = data.reshape(h, w)
        return out
    raise VtfError(f"decode not implemented for {v.fmt_name}")


# ----------------------------------------------------------------------------- writing

def bc3_blocks_to_bc1(data: bytes) -> bytes:
    """Exact DXT5 -> DXT1 colour transcode (alpha dropped)."""
    return bc3_to_bc1(data)


def write_vtf(template: Vtf, fmt: int, width: int, height: int, levels: list[bytes], flags: int | None = None) -> bytes:
    """Rebuild a VTF from `template`, replacing format/size and the high-res chain.

    levels[0] is the new top level (largest); each entry holds every frame/face of that level.
    Header fields, the low-res thumbnail and other resources are kept from the template.
    """
    header = bytearray(template.raw[:template.header_size])
    struct.pack_into("<HH", header, 16, width, height)
    if flags is not None:
        struct.pack_into("<I", header, 20, flags)
    struct.pack_into("<i", header, 52, fmt)
    struct.pack_into("<B", header, 56, len(levels))
    image = b"".join(reversed(levels))   # stored smallest first
    if template.version[1] < 3:
        lowres = template.raw[template.lowres_offset:template.highres_offset]
        return bytes(header) + lowres + image
    body = bytearray()
    offset = template.header_size
    moved = {}
    blocks = sorted((data, tag, rflags) for tag, rflags, data in template.resources
                    if not rflags & NO_DATA_FLAG and tag != HIGHRES_TAG)
    for data, tag, rflags in blocks:
        if tag == LOWRES_TAG:
            size = image_bytes(template.lowres_fmt, template.lowres_w, template.lowres_h)
        else:
            size = struct.unpack_from("<I", template.raw, data)[0] + 4
        moved[tag] = offset
        body += template.raw[data:data + size]
        offset += size
    image_offset = offset
    for i, (tag, rflags, data) in enumerate(template.resources):
        if rflags & NO_DATA_FLAG:
            new = data
        elif tag == HIGHRES_TAG:
            new = image_offset
        else:
            new = moved[tag]
        struct.pack_into("<I", header, 84 + i * 8, new)
    return bytes(header) + bytes(body) + image
