"""Lossless repack of a Source BSP in the engine's own compressed format.

What `bspzip -repack -compress` writes, and what maps of the test collection already ship: every lump as an
LZMA lump (Valve's 17-byte "LZMA" header; the lump's fourCC field holds its size uncompressed) and every file
of the pakfile as a zip LZMA entry (method 14). GMod reads both: a map compressed this way loads with the
same lumps and the same files (lab client, 2026-11-13).

Two details the game checks, found in that test:
- the zip LZMA entries carry the LZMA SDK version bytes 9.38 (09 26) and general purpose flags 0, as Valve's
  tools write them. With Python zipfile's own LZMA entries (version 9.4, flag "end marker") the filesystem
  printed "LZMA compression header is invalid" for every file and the map lost its cubemaps;
- the game lump directory holds absolute file offsets, so they move with the lump.

Lumps that Lua addons read from the .bsp file themselves stay uncompressed (the entities and the texture name
lumps; this program's own texture runtime reads them), and so do the game lump and the pakfile lump (a zip
whose files are compressed one by one). A lump or a file that would not get smaller is left as it is.

The whole file is rewritten (lumps in their original order, 4-byte aligned), so the pakfile does not have to
be the last lump. verify() proves the result: every lump decompresses to the original bytes and the pakfile
holds exactly the expected files.
"""
from __future__ import annotations

import io
import lzma
import struct
import zipfile
import zlib
from pathlib import Path

HEADER_LUMPS = 64
LUMP_PAK, LUMP_GAME = 40, 35
KEEP_LUMPS = {0, 35, 40, 43, 44}        # entities, game lump, pakfile, texture name lumps
LZMA_ID = b"LZMA"
ZIP_LZMA_VERSION = b"\x09\x26"          # LZMA SDK 9.38, as Valve's tools write it
# Valve's zip comment ("XZP1" preload marker, padded); kept from the source when it has one
VALVE_COMMENT = b"XZP1 0" + bytes(26)
MIN_GAIN = 64                           # bytes a compressed copy must save to be used


class BspError(Exception):
    pass


def lumps(data: bytes) -> list[list[int]]:
    """[fileofs, filelen, version, fourCC] of the 64 lumps."""
    if data[:4] != b"VBSP":
        raise BspError("not a VBSP file")
    return [list(struct.unpack_from("<iiii", data, 8 + i * 16)) for i in range(HEADER_LUMPS)]


def _dict_size(n: int) -> int:
    size = 1 << 16
    while size < n and size < (1 << 24):
        size <<= 1
    return size


def lzma_raw(data: bytes) -> tuple[bytes, bytes]:
    """(5 property bytes, LZMA stream) of data. The stream ends with an end marker (Python's encoder always
    writes one); the game's decoder stops at the known size and accepts it."""
    alone = lzma.compress(data, format=lzma.FORMAT_ALONE,
                          filters=[{"id": lzma.FILTER_LZMA1, "preset": 9, "dict_size": _dict_size(len(data))}])
    return alone[:5], alone[13:]


def valve_lzma(data: bytes) -> bytes:
    props, raw = lzma_raw(data)
    return LZMA_ID + struct.pack("<II", len(data), len(raw)) + props + raw


def valve_unlzma(blob: bytes) -> bytes:
    if blob[:4] != LZMA_ID:
        raise BspError("not an LZMA lump")
    actual, size = struct.unpack_from("<II", blob, 4)
    # size "unknown" in the .lzma header: streams of this module end with an end marker (liblzma refuses
    # a marker after a known size), Valve's do not (the decoder stops at max_length)
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    out = dec.decompress(blob[12:17] + bytes([255]) * 8 + blob[17:17 + size], max_length=actual)
    if len(out) != actual:
        raise BspError("LZMA lump shorter than its header says")
    return out


def lump_data(data: bytes, L: list[list[int]], i: int) -> bytes:
    """Lump i uncompressed."""
    ofs, length, _version, fourcc = L[i]
    if length <= 0:
        return b""
    raw = data[ofs:ofs + length]
    return valve_unlzma(raw) if fourcc else raw


def pak_files(data: bytes, L: list[list[int]] | None = None) -> tuple[list[tuple[str, bytes]], bytes]:
    """([(name, bytes)] in the pakfile's order, zip comment). Directory entries are left out."""
    L = L or lumps(data)
    ofs, length = L[LUMP_PAK][0], L[LUMP_PAK][1]
    if length <= 0:
        return [], b""
    files = []
    with zipfile.ZipFile(io.BytesIO(data[ofs:ofs + length])) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            with z.open(info) as f:
                # a wrong CRC in the map's own zip (ttt_plaza_b7): the game does not check it, the bytes are
                # kept as they are and the rebuilt pakfile carries their real CRC
                f._expected_crc = None
                files.append((info.filename, f.read()))
        return files, z.comment


def zip_entry(data: bytes, compress: bool) -> tuple[int, bytes]:
    """(method, stored bytes) of one pakfile file: method 14 with Valve's LZMA entry header when it saves
    at least MIN_GAIN bytes, else stored."""
    if compress and len(data) > MIN_GAIN:
        props, raw = lzma_raw(data)
        body = ZIP_LZMA_VERSION + struct.pack("<H", len(props)) + props + raw
        if len(body) + MIN_GAIN <= len(data):
            return 14, body
    return 0, data


def write_zip(entries: list[tuple[str, bytes, int, bytes]], comment: bytes) -> bytes:
    """A zip as Valve's tools write map pakfiles: no extra fields, DOS date 0, flags 0, made by version 2.0,
    version needed 6.3 for LZMA and 1.0 for stored. entries: (name, original bytes, method, stored bytes)."""
    out = bytearray()
    central = bytearray()
    for name, data, method, body in entries:
        n = name.encode("utf-8")
        crc = zlib.crc32(data) & 0xFFFFFFFF
        need = 63 if method == 14 else 10
        offset = len(out)
        out += struct.pack("<IHHHHHIIIHH", 0x04034B50, need, 0, method, 0, 0, crc, len(body), len(data), len(n), 0)
        out += n + body
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, need, 0, method, 0, 0, crc, len(body), len(data),
                               len(n), 0, 0, 0, 0, 0, offset)
        central += n
    cd_offset = len(out)
    out += central
    out += struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(central), cd_offset, len(comment))
    out += comment
    return bytes(out)


def build_pak(files: list[tuple[str, bytes]], comment: bytes = VALVE_COMMENT, compress: bool = True,
              map_fn=map) -> bytes:
    """The pakfile zip of `files` [(name, bytes)]. map_fn: map or a process pool's map (files are compressed
    one by one, so a pool spreads them)."""
    bodies = list(map_fn(_zip_entry_task, [(data, compress) for _name, data in files]))
    return write_zip([(name, data, m, b) for (name, data), (m, b) in zip(files, bodies)], comment or VALVE_COMMENT)


def _zip_entry_task(task):
    return zip_entry(*task)


def _lump_task(raw: bytes) -> bytes | None:
    packed = valve_lzma(raw)
    return packed if len(packed) + MIN_GAIN <= len(raw) else None


def repack(src: bytes, pak: list[tuple[str, bytes]] | None = None, compress: bool = True, map_fn=map) -> bytes:
    """The BSP src rewritten: lumps LZMA-compressed (except KEEP_LUMPS and lumps already compressed), the
    pakfile rebuilt from `pak` (default: the files it already holds) with LZMA entries. compress=False writes
    the same layout without compressing anything (only the pakfile contents change)."""
    L = lumps(src)
    comment = VALVE_COMMENT
    if pak is None or L[LUMP_PAK][1] > 0:
        own, own_comment = pak_files(src, L)
        comment = own_comment or VALVE_COMMENT
        if pak is None:
            pak = own
    order = sorted((i for i in range(HEADER_LUMPS) if L[i][1] > 0 or i == LUMP_PAK), key=lambda i: (L[i][0], i))
    plain = [i for i in order if compress and i not in KEEP_LUMPS and not L[i][3]]
    packed = dict(zip(plain, map_fn(_lump_task, [src[L[i][0]:L[i][0] + L[i][1]] for i in plain])))
    pak_blob = build_pak(pak, comment, compress, map_fn) if pak else b""
    out = bytearray(src[:8 + HEADER_LUMPS * 16 + 4])     # ident, version, lump directory, map revision
    new = [[0, 0, L[i][2], 0] for i in range(HEADER_LUMPS)]
    for i in order:
        while len(out) % 4:
            out.append(0)
        ofs, length, version, fourcc = L[i]
        start = len(out)
        if i == LUMP_PAK:
            out += pak_blob
            new[i] = [start if pak_blob else 0, len(pak_blob), version, 0]
        elif i == LUMP_GAME:
            g = bytearray(src[ofs:ofs + length])
            (count,) = struct.unpack_from("<i", g, 0)
            for k in range(count):
                p = 4 + 16 * k
                gofs = struct.unpack_from("<i", g, p + 8)[0]
                if gofs:
                    if not ofs <= gofs < ofs + length:
                        # the data would not move with the lump: the map stays as it is
                        raise BspError(f"game lump {k} has its data outside lump 35")
                    struct.pack_into("<i", g, p + 8, gofs - ofs + start)
            out += g
            new[i] = [start, length, version, fourcc]
        elif packed.get(i) is not None:
            out += packed[i]
            new[i] = [start, len(packed[i]), version, length]
        else:
            out += src[ofs:ofs + length]
            new[i] = [start, length, version, fourcc]
    for i in range(HEADER_LUMPS):
        struct.pack_into("<iiii", out, 8 + i * 16, *new[i])
    return bytes(out)


def replace_lumps(src: bytes, replace: dict[int, bytes]) -> bytes:
    """src with the lumps in `replace` swapped for new uncompressed contents (the map's FPS edits: entities,
    the static prop game lump). Every other lump is copied as it is, in the original order, 4-aligned; the
    game lump's absolute offsets move with it. A replaced game lump is the original one patched in place
    (same size, same offsets, as read from src)."""
    L = lumps(src)
    if LUMP_PAK in replace:
        raise BspError("the pakfile is rebuilt by repack, not replaced here")
    order = sorted((i for i in range(HEADER_LUMPS) if L[i][1] > 0 or i in replace), key=lambda i: (L[i][0], i))
    out = bytearray(src[:8 + HEADER_LUMPS * 16 + 4])
    new = [list(L[i]) for i in range(HEADER_LUMPS)]
    for i in range(HEADER_LUMPS):
        if L[i][1] <= 0 and i not in replace:
            new[i] = [0, 0, L[i][2], 0]
    for i in order:
        while len(out) % 4:
            out.append(0)
        ofs, length, version, fourcc = L[i]
        start = len(out)
        if i == LUMP_GAME:
            g = bytearray(replace[i]) if i in replace else bytearray(src[ofs:ofs + length])
            if len(g) != length:
                raise BspError("a replaced game lump must keep its size (patched in place)")
            base = ofs
            (count,) = struct.unpack_from("<i", g, 0) if len(g) >= 4 else (0,)
            for k in range(count):
                p = 4 + 16 * k
                gofs = struct.unpack_from("<i", g, p + 8)[0]
                if gofs:
                    if not base <= gofs < base + len(g):
                        raise BspError(f"game lump {k} has its data outside lump 35")
                    struct.pack_into("<i", g, p + 8, gofs - base + start)
            out += g
            new[i] = [start, len(g), version, fourcc]
        elif i in replace:
            out += replace[i]
            new[i] = [start, len(replace[i]), version, 0]
        else:
            out += src[ofs:ofs + length]
            new[i] = [start, length, version, fourcc]
    for i in range(HEADER_LUMPS):
        struct.pack_into("<iiii", out, 8 + i * 16, *new[i])
    return bytes(out)


def verify(src: bytes, out: bytes, pak: list[tuple[str, bytes]] | None = None) -> list[str]:
    """[] when `out` holds the lumps of `src` (decompressed, game lump with its offsets moved back) and the
    pakfile holds exactly `pak` (default: the files of src's pakfile), in the same order."""
    problems = []
    try:
        A, B = lumps(src), lumps(out)
    except BspError as e:
        return [str(e)]
    if src[4:8] != out[4:8] or src[8 + 64 * 16:8 + 64 * 16 + 4] != out[8 + 64 * 16:8 + 64 * 16 + 4]:
        problems.append("version or map revision")
    for i in range(HEADER_LUMPS):
        if i in (LUMP_PAK, LUMP_GAME):
            continue
        try:
            if lump_data(src, A, i) != lump_data(out, B, i):
                problems.append(f"lump {i}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"lump {i}: {e}")
        if A[i][2] != B[i][2]:
            problems.append(f"lump {i} version")
    ga, gb = lump_data(src, A, LUMP_GAME), bytearray(lump_data(out, B, LUMP_GAME))
    if len(ga) != len(gb):
        problems.append("game lump size")
    elif ga:
        # the data of every game lump lies inside lump 35 (repack refuses a map where it does not): with the
        # offsets moved back, the whole lump is the original byte for byte. The directory's length of a game
        # lump Valve's tools compressed is its uncompressed size, so it is not used to cut the data.
        lo = A[LUMP_GAME][0]
        (count,) = struct.unpack_from("<i", gb, 0)
        for k in range(count):
            p = 4 + 16 * k
            gofs = struct.unpack_from("<i", gb, p + 8)[0]
            if gofs:
                back = gofs - B[LUMP_GAME][0] + lo
                if not lo <= back < lo + A[LUMP_GAME][1]:
                    problems.append(f"game lump {k} outside lump 35")
                struct.pack_into("<i", gb, p + 8, back)
        if bytes(gb) != ga:
            problems.append("game lump directory or data")
    want = pak if pak is not None else pak_files(src, A)[0]
    try:
        got = pak_files(out, B)[0]
    except Exception as e:  # noqa: BLE001
        return problems + [f"pakfile unreadable: {e}"]
    if [n for n, _d in want] != [n for n, _d in got]:
        problems.append("pakfile file list")
    else:
        for (n, a), (_n, b) in zip(want, got):
            if a != b:
                problems.append("pakfile " + n)
                break
    return problems


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Lossless repack of a BSP (LZMA lumps and pakfile entries).")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--pak-dir", default=None, help="folder whose files become the pakfile (default: the BSP's own)")
    ap.add_argument("--no-compress", action="store_true")
    args = ap.parse_args(argv)
    src = Path(args.src).read_bytes()
    pak = None
    if args.pak_dir:
        root = Path(args.pak_dir)
        pak = [(p.relative_to(root).as_posix(), p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()]
    out = repack(src, pak, compress=not args.no_compress)
    problems = verify(src, out, pak)
    if problems:
        print("[FAIL]", problems)
        return 1
    Path(args.dst).write_bytes(out)
    print(f"{len(src) / 2**20:.1f} -> {len(out) / 2**20:.1f} MiB, verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
