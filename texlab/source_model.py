"""Minimal readers for compiled Source models (MDL v44-v49, VVD v4, VTX v7).

Only what texlab needs: per-mesh geometry of LOD 0 (positions, normals, UVs,
triangles), the material slot of each mesh, skin families and the material
search directories ($cdmaterials).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

VVD_VERTEX_SIZE = 48
VTX_STRIP_TRISTRIP = 0x02
VTX_STRIPGROUP_SIZE = 25   # MDL v44-v48 layout (no topology fields)
VTX_STRIP_SIZE = 27
VTX_VERTEX_SIZE = 9


class ModelFormatError(ValueError):
    pass


def _cstr(buf: bytes, offset: int) -> str:
    end = buf.index(b"\0", offset)
    return buf[offset:end].decode("latin-1")


@dataclass
class MeshGeometry:
    bodypart: str
    model: str
    material_slot: int          # index into the skin reference table
    positions: np.ndarray       # (V,3) float32, Source units (1 unit = 1 inch)
    normals: np.ndarray         # (V,3) float32
    uvs: np.ndarray             # (V,2) float32
    triangles: np.ndarray       # (T,3) int64 indices into this mesh's arrays
    tangents: np.ndarray | None = None   # (V,4) float32 (xyz + handedness), when the VVD has them


@dataclass
class SourceModel:
    path: Path
    name: str
    version: int
    textures: list[str]
    cdmaterials: list[str]
    skin_families: list[list[int]]   # [family][slot] -> texture index
    meshes: list[MeshGeometry] = field(default_factory=list)

    def texture_names_for_slot(self, slot: int) -> list[str]:
        """Every texture name a material slot can show across skin families."""
        names: list[str] = []
        for family in self.skin_families:
            if 0 <= slot < len(family):
                name = self.textures[family[slot]]
                if name not in names:
                    names.append(name)
        if not names and 0 <= slot < len(self.textures):
            names.append(self.textures[slot])
        return names

    @property
    def triangle_count(self) -> int:
        return int(sum(len(m.triangles) for m in self.meshes))


def _read_mdl(path: Path):
    buf = path.read_bytes()
    if buf[:4] != b"IDST":
        raise ModelFormatError(f"{path}: not an MDL")
    version, checksum = struct.unpack_from("<2i", buf, 4)
    if not 44 <= version <= 49:
        raise ModelFormatError(f"{path}: unsupported MDL version {version}")
    name = buf[12:76].split(b"\0", 1)[0].decode("latin-1")
    (numtextures, textureindex, numcd, cdindex, numskinref, numskinfamilies,
     skinindex, numbodyparts, bodypartindex) = struct.unpack_from("<9i", buf, 204)

    textures = []
    for i in range(numtextures):
        base = textureindex + i * 64
        rel = struct.unpack_from("<i", buf, base)[0]
        textures.append(_cstr(buf, base + rel).replace("\\", "/"))

    cdmaterials = []
    for i in range(numcd):
        off = struct.unpack_from("<i", buf, cdindex + i * 4)[0]
        cdmaterials.append(_cstr(buf, off).replace("\\", "/"))

    skins = [list(struct.unpack_from(f"<{numskinref}h", buf, skinindex + f * numskinref * 2))
             for f in range(numskinfamilies)]

    bodyparts = []
    for b in range(numbodyparts):
        bbase = bodypartindex + b * 16
        name_rel, nummodels, _base, modelindex = struct.unpack_from("<4i", buf, bbase)
        bname = _cstr(buf, bbase + name_rel)
        models = []
        for m in range(nummodels):
            mbase = bbase + modelindex + m * 148
            mname = buf[mbase:mbase + 64].split(b"\0", 1)[0].decode("latin-1")
            nummeshes, meshindex, numvertices, vertexindex = struct.unpack_from("<4i", buf, mbase + 72)
            meshes = []
            for k in range(nummeshes):
                kbase = mbase + meshindex + k * 116
                material, _mi, mnumverts, vertexoffset = struct.unpack_from("<4i", buf, kbase)
                meshes.append((material, mnumverts, vertexoffset))
            models.append((mname, numvertices, vertexindex, meshes))
        bodyparts.append((bname, models))
    return version, name, checksum, textures, cdmaterials, skins, bodyparts


def _read_vvd(path: Path, checksum: int) -> np.ndarray:
    """Return LOD0 vertex floats (N,12): pos xyz, normal xyz, uv, tangent xyzw (zeros if absent)."""
    buf = path.read_bytes()
    if buf[:4] != b"IDSV":
        raise ModelFormatError(f"{path}: not a VVD")
    _version, vchecksum, _numlods = struct.unpack_from("<3i", buf, 4)
    numlodverts = struct.unpack_from("<8i", buf, 16)
    numfixups, fixupstart, vertexstart, tangentstart = struct.unpack_from("<4i", buf, 48)
    if vchecksum != checksum:
        raise ModelFormatError(f"{path}: VVD checksum does not match MDL")
    end = tangentstart if tangentstart > vertexstart else len(buf)
    total = (end - vertexstart) // VVD_VERTEX_SIZE
    raw = np.frombuffer(buf, dtype=np.uint8, count=total * VVD_VERTEX_SIZE,
                        offset=vertexstart).reshape(total, VVD_VERTEX_SIZE)
    floats = np.ascontiguousarray(raw[:, 16:48]).view(np.float32).reshape(total, 8)
    tangents = np.zeros((total, 4), dtype=np.float32)
    if tangentstart > 0 and tangentstart + total * 16 <= len(buf):
        tangents = np.frombuffer(buf, dtype=np.float32, count=total * 4, offset=tangentstart).reshape(total, 4)
    floats = np.concatenate([floats, tangents], axis=1)
    if numfixups:
        order: list[int] = []
        for i in range(numfixups):
            lod, src, count = struct.unpack_from("<3i", buf, fixupstart + i * 12)
            if lod >= 0:
                order.extend(range(src, src + count))
        floats = floats[np.asarray(order, dtype=np.int64)]
    return floats[: numlodverts[0]]


def _read_vtx(path: Path):
    """[bodypart][model][mesh] -> (T,3) array of original mesh vertex ids (LOD 0)."""
    buf = path.read_bytes()
    if struct.unpack_from("<i", buf, 0)[0] != 7:
        raise ModelFormatError(f"{path}: unsupported VTX version")
    numbodyparts, bodypartoffset = struct.unpack_from("<2i", buf, 28)
    result = []
    for b in range(numbodyparts):
        bbase = bodypartoffset + b * 8
        nummodels, modeloffset = struct.unpack_from("<2i", buf, bbase)
        models = []
        for m in range(nummodels):
            mbase = bbase + modeloffset + m * 8
            numlods, lodoffset = struct.unpack_from("<2i", buf, mbase)
            meshes = []
            if numlods > 0:
                lbase = mbase + lodoffset
                nummeshes, meshoffset = struct.unpack_from("<2i", buf, lbase)
                for k in range(nummeshes):
                    kbase = lbase + meshoffset + k * 9
                    numsg, sgoffset = struct.unpack_from("<2i", buf, kbase)
                    tris: list[np.ndarray] = []
                    for g in range(numsg):
                        gbase = kbase + sgoffset + g * VTX_STRIPGROUP_SIZE
                        nverts, voff, nidx, ioff, nstrips, soff = struct.unpack_from("<6i", buf, gbase)
                        vraw = np.frombuffer(buf, dtype=np.uint8, count=nverts * VTX_VERTEX_SIZE,
                                             offset=gbase + voff).reshape(nverts, VTX_VERTEX_SIZE)
                        orig = vraw[:, 4].astype(np.int64) | (vraw[:, 5].astype(np.int64) << 8)
                        idx = np.frombuffer(buf, dtype="<u2", count=nidx, offset=gbase + ioff).astype(np.int64)
                        for s in range(nstrips):
                            sbase = gbase + soff + s * VTX_STRIP_SIZE
                            snidx, sioff = struct.unpack_from("<2i", buf, sbase)
                            flags = buf[sbase + 18]
                            seq = idx[sioff:sioff + snidx]
                            if flags & VTX_STRIP_TRISTRIP:
                                if len(seq) < 3:
                                    continue
                                a, b2, c = seq[:-2], seq[1:-1], seq[2:]
                                odd = (np.arange(len(a)) % 2) == 1
                                a2 = np.where(odd, b2, a)
                                b3 = np.where(odd, a, b2)
                                t = np.stack([a2, b3, c], axis=1)
                                keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
                                t = t[keep]
                            else:
                                t = seq[: (len(seq) // 3) * 3].reshape(-1, 3)
                            if len(t):
                                tris.append(orig[t])
                    meshes.append(np.concatenate(tris) if tris else np.zeros((0, 3), dtype=np.int64))
            models.append(meshes)
        result.append(models)
    return result


def load_model(mdl_path: str | Path) -> SourceModel:
    mdl_path = Path(mdl_path)
    version, name, checksum, textures, cdmaterials, skins, bodyparts = _read_mdl(mdl_path)
    model = SourceModel(mdl_path, name, version, textures, cdmaterials, skins)
    stem = mdl_path.with_suffix("")
    vvd_path = stem.with_suffix(".vvd")
    vtx_path = Path(str(stem) + ".dx90.vtx")
    if not vtx_path.exists():
        vtx_path = Path(str(stem) + ".vtx")
    if not vvd_path.exists() or not vtx_path.exists():
        return model  # e.g. animation-only include models
    verts = _read_vvd(vvd_path, checksum)
    vtx = _read_vtx(vtx_path)
    if len(vtx) != len(bodyparts):
        raise ModelFormatError(f"{mdl_path}: VTX/MDL bodypart count mismatch")
    for (bname, models), vtx_models in zip(bodyparts, vtx):
        if len(models) != len(vtx_models):
            raise ModelFormatError(f"{mdl_path}: VTX/MDL model count mismatch in {bname}")
        for (mname, _numverts, vertexindex, meshes), vtx_meshes in zip(models, vtx_models):
            model_start = vertexindex // VVD_VERTEX_SIZE
            if vtx_meshes and len(vtx_meshes) != len(meshes):
                raise ModelFormatError(f"{mdl_path}: VTX/MDL mesh count mismatch in {mname}")
            for (material, mnumverts, vertexoffset), tris in zip(meshes, vtx_meshes):
                start = model_start + vertexoffset
                sub = verts[start:start + mnumverts]
                if len(sub) != mnumverts:
                    raise ModelFormatError(f"{mdl_path}: mesh vertices out of VVD range")
                if len(tris) and (tris.max() >= mnumverts or tris.min() < 0):
                    raise ModelFormatError(f"{mdl_path}: triangle index out of range")
                model.meshes.append(MeshGeometry(
                    bname, mname, material,
                    np.ascontiguousarray(sub[:, 0:3]), np.ascontiguousarray(sub[:, 3:6]),
                    np.ascontiguousarray(sub[:, 6:8]), tris, np.ascontiguousarray(sub[:, 8:12])))
    return model
