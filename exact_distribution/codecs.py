"""Small adapter over the byte-pinned, frozen exact codec implementations."""

from __future__ import annotations

import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath
import struct

from _runtime_validation.high_impact_research.vvd_scalar_tiled_fixup_delta_exact_p1 import decoder as p1_decoder
from _runtime_validation.high_impact_research.vvd_scalar_tiled_fixup_delta_exact_p1 import encoder as p1_encoder
from _runtime_validation.high_impact_research.vvd_scalar_tiled_fixup_delta_exact_p1.format import CODEC_DOMAIN as P1_DOMAIN
from _runtime_validation.high_impact_research.tacrp_compression_portability_v1.vvd_portable import decoder as portable_decoder
from _runtime_validation.high_impact_research.tacrp_compression_portability_v1.vvd_portable import encoder as portable_encoder
from _runtime_validation.high_impact_research.tacrp_compression_portability_v1.vvd_portable.format import CODEC_DOMAIN as PORTABLE_DOMAIN
from _runtime_validation.high_impact_research.tacrp_compression_portability_v1.vtf_p2_encoder import encode_vtf
from _runtime_validation.high_impact_research.tacrp_compression_portability_v1.vtf_p2_independent_decoder import decode_vtf
from _runtime_validation.high_impact_research.tacrp_compression_portability_v1.vtf_p2_format import TILE_SIZES


VTF_DOMAIN = "vtf-bc-tiled-component-exact-p2/v1"
LZMA_DOMAIN = "source-asset-lzma-known-size-exact/v1"
GENERAL_LIMIT = 64 * 1024 * 1024
VTF_LIMIT = 8 * 1024 * 1024
_ROOT = Path(__file__).resolve().parent.parent
_PINS = Path(__file__).with_name("vendor-pins.json")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _option(family: str, domain: str, source: bytes, blob: bytes, **metadata: object) -> dict:
    return {
        "family": family, "codec_domain": domain, "blob": blob,
        "source_size": len(source), "source_sha256": _sha(source),
        "blob_size": len(blob), "blob_sha256": _sha(blob), **metadata,
    }


def _safe_path(target_path: str) -> str:
    if not isinstance(target_path, str) or "\\" in target_path:
        raise ValueError("unsafe asset path")
    path = PurePosixPath(target_path)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("unsafe asset path")
    return target_path.lower()


def _vvd_metadata(blob: bytes, domain: str) -> dict:
    parser = p1_decoder._parse_header if domain == P1_DOMAIN else portable_decoder._parse_header
    parsed = parser(blob, maximum_output=GENERAL_LIMIT)
    codes = ({"overlay": 1, "suffix_overlay_tail": 2, "disjoint": 3} if domain == P1_DOMAIN
             else {"contiguous_no_fixup": 1, "disjoint_padded": 2})
    meta = {key: value for key, value in parsed.items() if type(value) is int}
    meta["layout_code"] = codes[parsed["layout"]]
    return {"layout": parsed["layout"], "codec_meta": meta}


def _encode_lzma(source: bytes) -> bytes:
    compressed = lzma.compress(source, format=lzma.FORMAT_ALONE, preset=6)
    return compressed[:5] + struct.pack("<Q", len(source)) + compressed[13:]


def _vtf_metadata(layout: object, encoded: object) -> dict:
    fields = (
        ("major", layout.major), ("minor", layout.minor), ("header", layout.header),
        ("width", layout.width), ("height", layout.height), ("flags", layout.flags),
        ("frames", layout.frames), ("first", layout.first_frame), ("depth", layout.depth),
        ("high-format", layout.high_format), ("low-format", layout.low_format),
        ("low-width", layout.low_width), ("low-height", layout.low_height),
        ("mips", layout.mips), ("faces", layout.faces), ("low-offset", layout.low_offset),
        ("low-length", layout.low_length), ("high-offset", layout.high_offset),
        ("high-length", layout.high_length), ("trailing", layout.trailing),
        ("mip-bytes", ":".join(map(str, layout.mip_bytes))),
        ("resources", ";".join(f"{r.tag}:{r.flags}:{r.data}:{int(r.is_inline)}" for r in layout.resources) or "-"),
    )
    strategy, tile = encoded.strategy, encoded.tile
    rank = strategy if strategy in (1, 2) else 3 + TILE_SIZES.index(tile)
    return {"layout": ",".join(f"{key}={value}" for key, value in fields),
            "codec_parameters": f"strategy={strategy},tile={tile},rank={rank}"}


def encode_options(target_path: str, source: bytes) -> list[dict]:
    path = _safe_path(target_path)
    if type(source) is not bytes:
        raise TypeError("source must be immutable bytes")
    is_vvd = path.startswith("models/") and path.endswith(".vvd")
    lzma_family = next((family for family, prefix, suffix in (
        ("lzmdl", "models/", ".mdl"), ("lzvtx", "models/", ".dx90.vtx"),
        ("lzvtf", "materials/", ".vtf"), ("lzwav", "sound/", ".wav"),
    ) if path.startswith(prefix) and path.endswith(suffix)), None)
    if not is_vvd and lzma_family is None:
        return []
    if not source or len(source) > GENERAL_LIMIT:
        raise ValueError("asset size out of bounds")
    options: list[dict] = []
    if is_vvd:
        for domain, encoder in ((P1_DOMAIN, p1_encoder), (PORTABLE_DOMAIN, portable_encoder)):
            try:
                encoded = encoder.encode_vvd(source)
            except ValueError:
                continue
            option = _option("vvd", domain, source, encoded.blob, **_vvd_metadata(encoded.blob, domain))
            if decode_option(option) != source:
                raise ValueError("independent VVD reconstruction mismatch")
            options.append(option)
        return options
    if path.endswith(".vtf"):
        if len(source) > VTF_LIMIT:
            return []
        encoded = encode_vtf(source)
        if encoded.blob is not None:
            layout = encoded.layout
            option = _option("vtf", VTF_DOMAIN, source, encoded.blob, **_vtf_metadata(layout, encoded))
            if decode_option(option) != source:
                raise ValueError("independent VTF reconstruction mismatch")
            options.append(option)
    blob = _encode_lzma(source)
    option = _option(lzma_family, LZMA_DOMAIN, source, blob,
                     codec_meta={"format": "lzma-alone-known-size", "preset": 6})
    if decode_option(option) != source:
        raise ValueError("independent LZMA reconstruction mismatch")
    options.append(option)
    return options


def _decode_lzma(blob: bytes, size: int) -> bytes:
    if len(blob) <= 13 or blob[:5] != bytes.fromhex("5d00008000") or blob[5:13] != struct.pack("<Q", size):
        raise ValueError("asset compressed header")
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[
            {"id": lzma.FILTER_LZMA1, "dict_size": 8388608, "lc": 3, "lp": 0, "pb": 2}])
        source = decoder.decompress(blob[13:], max_length=size + 1)
    except lzma.LZMAError as exc:
        raise ValueError("asset LZMA invalid") from exc
    if not decoder.eof or decoder.unused_data or len(source) != size:
        raise ValueError("asset reconstruction identity or trailing bytes")
    return source


def decode_option(option: dict) -> bytes:
    blob = option.get("blob")
    size = option.get("source_size")
    if type(blob) is not bytes or type(size) is not int or not 0 < size <= GENERAL_LIMIT:
        raise ValueError("invalid exact option")
    if len(blob) != option.get("blob_size") or _sha(blob) != option.get("blob_sha256"):
        raise ValueError("blob identity mismatch")
    domain = option.get("codec_domain")
    if domain == P1_DOMAIN:
        source = p1_decoder.decode_vvd(blob, maximum_output=GENERAL_LIMIT)
    elif domain == PORTABLE_DOMAIN:
        source = portable_decoder.decode_vvd(blob, maximum_output=GENERAL_LIMIT)
    elif domain == VTF_DOMAIN:
        if size > VTF_LIMIT:
            raise ValueError("VTF output size out of bounds")
        source = decode_vtf(blob, maximum_output=VTF_LIMIT)
    elif domain == LZMA_DOMAIN:
        source = _decode_lzma(blob, size)
    else:
        raise ValueError("unknown codec domain")
    if len(source) != size or _sha(source) != option.get("source_sha256"):
        raise ValueError("source identity mismatch")
    return source


def verify_vendor() -> dict[str, str]:
    manifest = json.loads(_PINS.read_text(encoding="utf-8"))
    verified = {}
    for relative, record in manifest["files"].items():
        digest = _sha((_ROOT / relative).read_bytes())
        if digest != record["sha256"]:
            raise ValueError(f"vendored codec mismatch: {relative}")
        verified[relative] = digest
    return verified
