"""Byte inventory of an optimized bench (lab/<tag>/<group>/<group>_merged_optimized).

python inventory.py <tag> -> out/inventory_<tag>.json + printed tables
Categories follow Systems/Reporting/AddonInventory.cs (Modelos = models/ + data_static/gmo/, Texturas = VTF/VMT/
images + data_static/gmot/, Sons = wav/mp3/ogg, Outros), split further:
- Modelos: GMOS blobs (by field), streamed models' coarse mdl/vvd/vtx, phy, ani, the other models by extension
- Texturas: GMOT blobs (container, chunk 0 = header + preview pyramid, finer levels by level and plane),
  placeholders, VTFs kept, VMT, PNG/JPG, runtime Lua
"""
import json
import lzma
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

LAB = Path(r"C:/GModLab/revisao/claude")
TAG = sys.argv[1] if len(sys.argv) > 1 else "cb"
MiB = 2 ** 20


def unlzma(c):
    d = bytearray(c)
    d[5:13] = b"\xff" * 8
    return lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)


def gmot_breakdown(data, acc, meta):
    nch = data[5]
    sizes = struct.unpack_from("<%dI" % nch, data, 6)
    acc["container"] += 6 + 4 * nch
    offs = 6 + 4 * nch
    body = unlzma(data[offs:offs + sizes[0]])
    w, h, L, n, mode, cdrop, P = struct.unpack_from("<HHBBBBB", body, 0)
    pos = 9 + 4 * n
    hl = struct.unpack_from("<I", body, pos)[0]
    acc["chunk0"] += sizes[0]
    acc["chunk0_raw_vtfheader"] += hl
    acc["chunk0_raw"] += len(body)
    planes = (["nx", "ny"] if mode == 1 else ["y", "co", "cg"])
    if n > len(planes):
        planes.append("a")
    kind = "normal" if mode == 1 else "colour"
    alpha = n > (2 if mode == 1 else 3)
    meta["kinds"][kind + ("+a" if alpha else "")] += 1
    meta["sizes"][max(w, h)] += 1
    keys = [(lvl, pi, k) for lvl in range(P, 0, -1) for pi in range(n) for k in range(3)]
    o = offs + sizes[0]
    for c, (lvl, pi, k) in enumerate(keys, start=1):
        sz = sizes[c]
        raw = unlzma(data[o:o + sz])
        o += sz
        acc[f"lvl{lvl}"] += sz
        acc[f"plane_{planes[pi]}"] += sz
        acc[f"{kind}_lvl{lvl}_{planes[pi]}"] += sz
        if len(raw) <= 1:
            acc["trivial_chunks_bytes"] += sz
            meta["trivial_chunks"] += 1
        if raw[0] & 16:
            th, tw = max(1, (h >> lvl) // 8), max(1, (w >> lvl) // 8)
            acc["raw_noise_map"] += th * tw
        acc["raw_fine"] += len(raw)
        meta["chunks"] += 1
    meta["chunks"] += 1
    return w, h, mode, n, P


GMOS_FIELDS = ("pref", "px", "py", "pz", "nref", "ncx", "ncy", "uvref", "u", "v", "idx")


def gmos_breakdown(data, acc, meta):
    d = bytearray(data[5:])
    d[5:13] = b"\xff" * 8
    blob = lzma.decompress(bytes(d), format=lzma.FORMAT_ALONE)
    pos = 20
    ntex = struct.unpack_from("<I", blob, pos)[0]; pos += 4
    for _ in range(ntex):
        pos = blob.index(b"\0", pos) + 1
    nfam, nref = struct.unpack_from("<II", blob, pos); pos += 8 + 2 * nfam * nref
    ng = struct.unpack_from("<I", blob, pos)[0]; pos += 4
    nv = ni = 0
    for _ in range(ng):
        b, m, slot, bone, flags, gnv, nch = struct.unpack_from("<4HBIH", blob, pos)
        pos += struct.calcsize("<4HBIH")
        lens = struct.unpack_from(f"<{nch}I", blob, pos); pos += 4 * nch + 48
        nv += gnv
        ni += sum(lens)
    head = pos
    np_, nn_, nu_ = struct.unpack_from("<3I", blob, pos); pos += 12
    counts = dict(pref=nv, px=np_, py=np_, pz=np_, nref=nv, ncx=nn_, ncy=nn_, uvref=nv, u=nu_, v=nu_, idx=ni)
    parts = {"header": blob[:head + 12]}
    for f in GMOS_FIELDS:
        nb = blob[pos]
        parts[f] = blob[pos:pos + 1 + nb * counts[f]]
        pos += 1 + nb * counts[f]
    assert pos == len(blob), (pos, len(blob))
    acc["gmos_file"] += len(data)
    acc["gmos_raw"] += len(blob)
    for f, b in parts.items():
        acc["raw_" + f] += len(b)
    meta["verts"] += nv
    meta["newpos"] += np_
    meta["newnrm"] += nn_
    meta["newuv"] += nu_
    meta["strip_idx"] += ni
    meta["groups"] += ng
    return parts


def lz9e(b):
    return len(lzma.compress(bytes(b), format=lzma.FORMAT_ALONE,
                             filters=[{"id": lzma.FILTER_LZMA1, "preset": 9 | lzma.PRESET_EXTREME, "dict_size": 1 << 24}]))


def manifest_of(lua_text, start):
    i = lua_text.index(start) + len(start)
    j = lua_text.index("]]", i) if start.endswith("[[") else None
    return json.loads(lua_text[i:j])


def main():
    out = {}
    tot = Counter()
    gmot_acc, gmot_meta = Counter(), {"kinds": Counter(), "sizes": Counter(), "chunks": 0, "trivial_chunks": 0}
    gmos_acc, gmos_meta = Counter(), Counter()
    gmos_sep = Counter()
    for gdir in sorted((LAB / TAG).iterdir()):
        root = gdir / f"{gdir.name}_merged_optimized"
        cat = Counter()
        # streamed models: GMOS manifest in lua/autorun/client/gmo_stream_*.lua
        streamed = set()
        for lua in (root / "lua" / "autorun" / "client").glob("gmo_stream_*.lua"):
            t = lua.read_text(encoding="utf-8")
            i = t.index("util.JSONToTable([[") + len("util.JSONToTable([[")
            man = json.loads(t[i:t.index("]])", i)])
            streamed |= {k.lower() for k in man}
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            low = rel.lower()
            size = p.stat().st_size
            ext = p.suffix.lower()
            if low.startswith("data_static/gmo/"):
                k = "M:gmos"
                gmos_breakdown(p.read_bytes(), gmos_acc, gmos_meta)
            elif low.startswith("models/"):
                if low.startswith("models/gmo_stream/"):
                    k = "M:probe"
                else:
                    stem = low[:-len(".dx90.vtx")] if low.endswith(".dx90.vtx") else low.rsplit(".", 1)[0]
                    is_s = stem + ".mdl" in streamed
                    e = ".vtx" if low.endswith(".vtx") else ext
                    if e in (".mdl", ".vvd", ".vtx"):
                        k = "M:coarse_" + e[1:] if is_s else "M:model_" + e[1:]
                    elif e == ".phy":
                        k = "M:phy_streamed" if is_s else "M:phy_other"
                    elif e == ".ani":
                        k = "M:ani"
                    else:
                        k = "M:other_" + e.lstrip(".")
            elif low.startswith("data_static/gmot/"):
                k = "T:gmot"
                gmot_breakdown(p.read_bytes(), gmot_acc, gmot_meta)
            elif ext == ".vtf":
                k = "T:vtf"
            elif ext == ".vmt":
                k = "T:vmt"
            elif ext in (".png", ".jpg", ".jpeg", ".tga"):
                k = "T:image"
            elif ext in (".wav", ".mp3", ".ogg"):
                k = "S:" + ext[1:]
            elif low.startswith("lua/autorun/!gmot_"):
                k = "T:gmot_runtime"
            elif low.startswith("lua/autorun/client/gmo_stream_") or low.startswith("lua/autorun/server/gmo_stream_sv_"):
                k = "M:gmos_runtime"
            elif ext == ".lua":
                k = "O:lua"
            else:
                k = "O:" + (ext.lstrip(".") or "noext")
            cat[k] += size
        # split VTF into placeholders (streamed) and kept: placeholders are tiny (<= 16 px) VTFs
        rep = LAB / "out" / f"texscreen_{TAG}_{gdir.name}.json"
        if rep.exists():
            r = json.loads(rep.read_text(encoding="utf-8"))
            st = r.get("stream", {})
            cat["T:vtf_placeholder"] = st.get("placeholders", 0)
            cat["T:vtf_kept"] = cat["T:vtf"] - cat["T:vtf_placeholder"]
        out[gdir.name] = dict(cat)
        tot.update(cat)
    # per field compressed sizes of GMOS, each field alone (an attribution: the real file is one LZMA stream)
    out["_total"] = dict(tot)
    out["_gmot"] = {"acc": dict(gmot_acc), "meta": {k: (dict(v) if isinstance(v, Counter) else v) for k, v in gmot_meta.items()}}
    out["_gmos"] = {"acc": dict(gmos_acc), "meta": dict(gmos_meta)}
    (LAB / "out" / f"inventory_{TAG}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    def group_sum(c, prefix):
        return sum(v for k, v in c.items() if k.startswith(prefix) and k not in ("T:vtf_placeholder", "T:vtf_kept"))
    print(f"{'grupo':10} {'total':>8} {'Modelos':>8} {'Texturas':>8} {'Sons':>8} {'Outros':>8}  (MiB)")
    for g, c in list(out.items()):
        if g.startswith("_") and g != "_total":
            continue
        print(f"{g:10} {sum(v for k, v in c.items() if k not in ('T:vtf_placeholder', 'T:vtf_kept')) / MiB:8.2f} "
              f"{group_sum(c, 'M:') / MiB:8.2f} {group_sum(c, 'T:') / MiB:8.2f} {group_sum(c, 'S:') / MiB:8.2f} "
              f"{group_sum(c, 'O:') / MiB:8.2f}")
    print()
    for k, v in sorted(tot.items(), key=lambda kv: -kv[1]):
        print(f"  {k:24} {v / MiB:9.2f} MiB")
    print("\nGMOT:", {k: round(v / MiB, 2) for k, v in sorted(gmot_acc.items())})
    print("GMOT meta:", gmot_meta["kinds"], "chunks", gmot_meta["chunks"], "trivial", gmot_meta["trivial_chunks"])
    print("GMOS:", {k: round(v / MiB, 2) for k, v in sorted(gmos_acc.items())})
    print("GMOS meta:", dict(gmos_meta))


if __name__ == "__main__":
    main()
