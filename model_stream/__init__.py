"""Models "Tela" profile "stream" (experimental): compressed original geometry drawn by a Lua runtime.

A streamed model keeps its MDL (bones, bodygroups, attachments, physics) with a coarse stand-in mesh
(~2% of the triangles, what the engine draws for shadows, far away and as fallback). Its full LOD0
geometry is stored compressed (GMOS, encoder.py) in data_static/gmo/, and lua/autorun/client/
gmo_stream_<addon>.lua (runtime.lua + manifest) decodes it into IMeshes and draws them in place of the
model with the engine's own lighting (one-triangle light probe per entity), skins, bodygroups,
(sub)materials, colour, translucency and flashlights. See docs/modelos-stream-2026-11-08.md.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
PROBE_NAME = "probe_v1"
DEFAULT_MIN_BYTES = 20_000          # smaller models are not worth a stream
DEFAULT_ALWAYS_BYTES = 400_000      # models at least this big are always streamed (when eligible)
# models drawn all the time in numbers (vehicle wheels) stay ordinary models unless big
DEFAULT_KEEP = r"wheel|tire|tyre|^(fw|rw|wh)\.mdl$"
COARSE_RATIO = 0.02


def stream_name(rel_model: str) -> str:
    """data_static path of a model's stream (same for every addon that ships this model path)."""
    return "data_static/gmo/" + hashlib.sha1(rel_model.lower().encode("utf-8")).hexdigest()[:16] + ".txt"


def wants_stream(rel_model: str, size: int, min_bytes: int = DEFAULT_MIN_BYTES,
                 always_bytes: int = DEFAULT_ALWAYS_BYTES, keep: str = DEFAULT_KEEP) -> bool:
    if size >= always_bytes:
        return True
    if size < min_bytes:
        return False
    return not re.search(keep, Path(rel_model).name.lower())


def stream_model(mdl: Path, rel_model: str, out_root: Path, resolver, ratio: float = COARSE_RATIO):
    """Encode mdl (original files) into out_root/<stream_name> and return the coarse stand-in bytes
    (mdl, vvd, vtx) and stream size. Raises ValueError when the model cannot be streamed."""
    from model_screen.binary import rewrite_model_bytes
    from . import coarse, encoder
    ok, why = encoder.eligible(mdl)
    if not ok:
        raise ValueError(why)
    data = encoder.encode(mdl, resolver)
    tris = coarse.coarse_tris(mdl, ratio)
    m, v, x, _ = rewrite_model_bytes(mdl, tris, drop_tangents=True)
    target = out_root / stream_name(rel_model)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return m, v, x, len(data)


def write_runtime(out_root: Path, manifest: dict, addon_name: str) -> list[Path]:
    """Light probe model + runtime with this addon's manifest. Returns the written files."""
    written = []
    probe_dir = out_root / "models" / "gmo_stream"
    probe_dir.mkdir(parents=True, exist_ok=True)
    for ext in (".mdl", ".vvd", ".dx90.vtx"):
        dst = probe_dir / (PROBE_NAME + ext)
        shutil.copyfile(PACKAGE / "probe" / (PROBE_NAME + ext), dst)
        written.append(dst)
    # its material: lit and bumped like typical model materials (the engine picks the lighting path from
    # the model's own materials; an unlit or flat probe would not set the state the IMeshes need)
    for name in (PROBE_NAME + ".vmt", PROBE_NAME + "_normal.vtf"):
        dst = out_root / "materials" / "gmo_stream" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACKAGE / "probe" / name, dst)
        written.append(dst)
    slug = hashlib.sha1(addon_name.lower().encode("utf-8")).hexdigest()[:10]
    lua = out_root / "lua" / "autorun" / "client" / f"gmo_stream_{slug}.lua"
    lua.parent.mkdir(parents=True, exist_ok=True)
    body = (PACKAGE / "runtime.lua").read_text(encoding="utf-8")
    text = ("-- streamed models of this addon (GMod Addon Optimizer, merged into the shared GMOS runtime)\n"
            "GMOS_PENDING_MANIFEST = util.JSONToTable([[" + json.dumps(manifest, sort_keys=True) + "]])\n" + body)
    lua.write_text(text, encoding="utf-8", newline="\n")
    written.append(lua)
    return written
