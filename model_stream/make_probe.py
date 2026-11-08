"""Development tool: builds model_stream/probe/probe_v1.{mdl,vvd,dx90.vtx}, the one-triangle light probe
the stream runtime draws (invisibly) at an entity's lighting origin. Made from a small compiled model:
one triangle kept, lighting-modifier flags cleared, every texture renamed to gmo_stream/probe_v1 (a VertexLitGeneric material
shipped next to it: the engine only sets up model lighting when a material is lit)."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_screen.binary import mesh_arrays, rewrite_model_bytes  # noqa: E402

NAME = "probe_v1"
INTERNAL = "gmo_stream/probe_v1.mdl"
# STUDIOHDR_FLAGS_STATIC_PROP, _CONSTANT_DIRECTIONAL_LIGHT_DOT, _AMBIENT_BOOST change how a model is lit
CLEAR_FLAGS = 0x10 | 0x2000 | 0x10000


def make(src: Path, out_dir: Path) -> int:
    tris, first = {}, True
    for key, _st, _cnt, t in mesh_arrays(src):
        if first and len(t):
            tris[key] = np.asarray(t[:1], np.int64)
            first = False
        else:
            tris[key] = np.zeros((0, 3), np.int64)
    m, v, x, _ = rewrite_model_bytes(src, tris, drop_tangents=True)
    m = bytearray(m)
    struct.pack_into("<i", m, 152, struct.unpack_from("<i", m, 152)[0] & ~CLEAR_FLAGS)
    nm = INTERNAL.encode()
    m[12:76] = nm + b"\0" * (64 - len(nm))
    # strings appended at the end: texture names and cdmaterials
    while len(m) % 4:
        m.append(0)
    tex_off = len(m)
    m += b"probe_v1\0"
    cd_off = len(m)
    m += b"gmo_stream/\0"
    while len(m) % 4:
        m.append(0)
    ntex, texidx = struct.unpack_from("<ii", m, 204)
    for i in range(ntex):
        base = texidx + i * 64
        struct.pack_into("<i", m, base, tex_off - base)
    ncd, cdidx = struct.unpack_from("<ii", m, 212)
    for i in range(ncd):
        struct.pack_into("<i", m, cdidx + i * 4, cd_off)
    struct.pack_into("<i", m, 76, len(m))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / (NAME + ".mdl")).write_bytes(bytes(m))
    (out_dir / (NAME + ".vvd")).write_bytes(v)
    (out_dir / (NAME + ".dx90.vtx")).write_bytes(x)
    return len(m) + len(v) + len(x)


if __name__ == "__main__":
    print(make(Path(sys.argv[1]), Path(__file__).with_name("probe")))
