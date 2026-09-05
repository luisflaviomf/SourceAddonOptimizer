import hashlib
import struct

import pytest

from exact_distribution import decode_option, encode_options, verify_vendor


VVD_HEADER = struct.Struct("<4sIII8I4I")
FIXUP = struct.Struct("<III")


def make_vvd(*, padding=0, active_lods=(3, 2, 1)):
    count = active_lods[0]
    rows = ((2, 0, 1), (1, 1, 1), (0, 2, 1)) if active_lods[-1] else ((1, 0, 2), (0, 2, 1))
    fixup_start = 64
    fixup_end = fixup_start + len(rows) * 12
    vertex_start = fixup_end + padding
    tangent_start = vertex_start + count * 48
    slots = active_lods + (0,) * (8 - len(active_lods))
    source = bytearray(VVD_HEADER.pack(
        b"IDSV", 4, 0x13579BDF, len(active_lods), *slots,
        len(rows), fixup_start, vertex_start, tangent_start,
    ))
    for row in rows:
        source.extend(FIXUP.pack(*row))
    source.extend(b"\0" * padding)
    for cell in range(count):
        for lane in range(12):
            source.extend(struct.pack("<I", 0x21000000 + lane * 0x1000 + cell))
    for cell in range(count):
        for lane in range(4):
            source.extend(struct.pack("<I", 0x61000000 + lane * 0x1000 + cell))
    return bytes(source)


def make_no_fixup_vvd():
    count = 64
    tangent_start = 64 + count * 48
    source = bytearray(VVD_HEADER.pack(
        b"IDSV", 4, 7, 2, count, 0, 0, 0, 0, 0, 0, 0,
        0, 64, 64, tangent_start,
    ))
    source.extend((b"ABCD" * 12) * count)
    source.extend((b"EFGH" * 4) * count)
    return bytes(source)


def make_vtf():
    width = height = 64
    payload = b"A" * 2048
    header = bytearray(80)
    header[:4] = b"VTF\0"
    struct.pack_into("<IIIHHIHH", header, 4, 7, 2, 80, width, height, 0, 1, 0)
    struct.pack_into("<I", header, 52, 13)
    header[56] = 1
    struct.pack_into("<i", header, 57, -1)
    struct.pack_into("<H", header, 63, 1)
    return bytes(header) + payload


@pytest.mark.parametrize("source", [make_vvd(), make_vvd(padding=8, active_lods=(3, 2, 0)), make_no_fixup_vvd()])
def test_vvd_layouts_roundtrip_from_arbitrarily_renamed_paths(source):
    options = encode_options("models/renamed/anything.vvd", source)
    assert options
    assert all(decode_option(option) == source for option in options)
    assert all(option["source_sha256"] == hashlib.sha256(source).hexdigest() for option in options)


def test_vtf_p2_and_lzma_roundtrip_with_complete_metadata():
    for path, source in (
        ("materials/renamed/test.vtf", make_vtf()),
        ("sound/example/test.wav", b"RIFF" + b"abc123" * 20000),
    ):
        options = encode_options(path, source)
        assert options and all(decode_option(option) == source for option in options)
        for option in options:
            assert option["family"] and option["codec_domain"]
            assert option["blob_size"] == len(option["blob"])
            assert option["blob_sha256"] == hashlib.sha256(option["blob"]).hexdigest()


def test_native_unsupported_and_size_caps():
    assert encode_options("lua/example.lua", b"print(1)") == []
    assert encode_options("materials/x.vtf", b"x" * (8 * 1024 * 1024 + 1)) == []
    with pytest.raises(ValueError, match="size"):
        encode_options("sound/x.wav", b"x" * (64 * 1024 * 1024 + 1))


def test_corrupt_and_trailing_blobs_are_rejected():
    option = encode_options("sound/example/test.wav", b"RIFF" + b"abc123" * 20000)[0]
    corrupt = dict(option, blob=option["blob"][:-1] + bytes([option["blob"][-1] ^ 1]))
    trailing = dict(option, blob=option["blob"] + b"tail")
    with pytest.raises(ValueError):
        decode_option(corrupt)
    with pytest.raises(ValueError):
        decode_option(trailing)


def test_frozen_vendor_files_match_provenance_pins():
    pins = verify_vendor()
    assert pins
    assert all(len(digest) == 64 for digest in pins.values())
