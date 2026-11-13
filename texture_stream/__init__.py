"""Texture stream: VTFs stored as GMOT wavelet blobs, decoded by a Lua runtime in game.

convert() turns the DXT textures of the addon into:
- data_static/gmot/<slug>/<i>.dat: the GMOT blob (codec.py), at the largest quantization step that keeps
  the target PSNR (choose.py; 34 dB by default, with noise filling of the two finest levels);
- a placeholder VTF at the original path: the texture box-filtered to <= 16 px with full mips (what the
  game shows for the first seconds);
- lua/autorun/!gmot_<slug>.lua: manifest (texture -> blob, materials and parameters that use it; model ->
  textures) + the shared runtime (decoder.lua + runtime.lua). In game the runtime decodes what models,
  materials and Lua calls need into DXT VTFs (same VRAM as before), mounts them from data/gmot and
  points the materials at them.
Every DXT texture is streamed. The runtime learns what is needed from the models it sees (entities, static
props), from the material calls of Lua (wrapped) and the map; effects the engine draws by itself are
prefetched after the map loads, the rest later in the background (classify()). Non-DXT, animated and small
textures stay VTFs. See docs/texturas-stream-2026-11-09.md.
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import as_completed
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
CORE_VERSION = 3
DEFAULT_TARGET_DB = 34.0
DEFAULT_FILL_LEVELS = 2
PLACEHOLDER_SIDE = 16
PREVIEW_SIDE = 512
BLOB_DIR = "data_static/gmot"


def slug_for(addon_name: str) -> str:
    return hashlib.sha1(addon_name.lower().encode("utf-8")).hexdigest()[:10]


def runtime_path(root: Path, slug: str) -> Path:
    return Path(root) / "lua" / "autorun" / f"!gmot_{slug}.lua"


def placeholder(v, rgba, side=PLACEHOLDER_SIDE) -> bytes:
    """the texture box-filtered to <= side texels with a full mip chain, DXT5 when the source format has
    alpha, else DXT1."""
    import numpy as np

    from texlab.vtf import write_vtf

    from .codec import blocks, lua_alpha, lua_dxt1
    img = np.asarray(rgba, np.float64)
    while img.shape[0] > side and img.shape[1] > side:
        img = 0.25 * (img[0::2, 0::2] + img[1::2, 0::2] + img[0::2, 1::2] + img[1::2, 1::2])
    h0, w0 = img.shape[:2]
    h, w = h0, w0
    fmt = 15 if v.fmt in (15, 14, 0, 1, 12) else 13
    levels = []
    while True:
        cb = lua_dxt1(blocks(img[..., :3], w, h))
        if fmt == 15:
            ab = lua_alpha(blocks(img[..., 3:4], w, h)[..., 0])
            levels.append(np.concatenate([ab, cb], 1).tobytes())
        else:
            levels.append(cb.tobytes())
        if w == 1 and h == 1:
            break
        if w > 1 and h > 1:
            img = 0.25 * (img[0::2, 0::2] + img[1::2, 0::2] + img[0::2, 1::2] + img[1::2, 1::2])
        elif h > 1:
            img = 0.5 * (img[0::2] + img[1::2])
        else:
            img = 0.5 * (img[:, 0::2] + img[:, 1::2])
        w, h = max(1, w // 2), max(1, h // 2)
    flags = v.flags & ~0x100
    return write_vtf(v, fmt, w0, h0, levels, flags=flags if fmt == 15 else flags & ~0x2000)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".gmot_tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def encode_one(task) -> dict:
    """worker process: choose + encode one VTF; writes the blob to a temporary file next to its final
    place. The caller replaces the VTF with the placeholder once it numbered the blob."""
    try:
        return _encode_one(*task)
    except Exception as e:  # one bad file keeps its VTF, the rest goes on
        return {"path": task[0], "keep": f"error: {type(e).__name__}: {e}"[:200]}


def direct_key(path) -> str:
    """One spelling per file for the `direct` textures of convert()."""
    return os.path.normcase(os.path.abspath(str(path)))


def _encode_one(path, use, target, fill, tmp_blob, direct=None) -> dict:
    from texlab.vtf import decode_level, read_vtf

    from .choose import choose, eligible
    from .codec import encode
    p = Path(path)
    size = p.stat().st_size
    try:
        v = read_vtf(p)
    except Exception:
        return {"path": path, "keep": "unreadable"}
    if direct is not None:
        # The texture engine planned this texture reduced and hands over the reduced image itself (see
        # convert): the header and the size to beat are those of the file it was going to write.
        from texlab.vtf import planned_reduced_vtf
        png, out_fmt, drop, kind = direct
        planned = planned_reduced_vtf(v, out_fmt, drop, kind)
        v, size = read_vtf(planned), len(planned)
    why = eligible(v)
    if why:
        return {"path": path, "keep": why}
    if direct is not None:
        import numpy as np
        from PIL import Image
        rgba = np.asarray(Image.open(png).convert("RGBA")).astype("float64")
        if rgba.shape[:2] != (v.height, v.width):
            raise ValueError("the reduced image does not have the planned size")
    else:
        rgba = decode_level(v, 0).astype("float64")
    transforms = []
    c = choose(v, rgba, use, target, transforms)
    data = encode(v, rgba, c["step"], alpha_step=c["alpha_step"], chroma_drop=c["drop"], normal=c["normal"],
                  keep_alpha=c["alpha"], fill_levels=fill, preview_side=PREVIEW_SIDE, transforms=transforms)
    ph = placeholder(v, rgba)
    if len(data) + len(ph) >= size:
        return {"path": path, "keep": "no_gain"}
    _write_atomic(Path(tmp_blob), data)
    return {"path": path, "blob": len(data), "sha1": hashlib.sha1(data).digest(), "placeholder": ph, "before": size,
            "side": max(v.width, v.height),
            **{k: c[k] for k in ("normal", "alpha", "step", "drop", "alpha_step", "rgb_db", "alpha_db")}}


def _model_textures(mdl: Path, resolver) -> set[str]:
    from texlab.source_model import _read_mdl
    try:
        _v, _n, _c, textures, cdmaterials, _s, _b = _read_mdl(mdl)
    except Exception:
        return set()
    out = set()
    for tex in textures:
        m = resolver.model_material(cdmaterials, tex)
        if m:
            out.update(ref for _key, ref in m.all_textures())
    return out


# How the runtime finds out a texture is needed (manifest field 5). 0: a model of this addon or a neighbour
# draws it (seen on entities, static props, Lua model calls); 1: an effect the engine draws by itself
# (particles, sprites, decals: no Lua call to see), prefetched a few seconds after the map loads;
# 2: named in Lua text (camos, stickers, menus: the material calls the runtime wraps see it); 3: anything
# else. 2 and 3 are prefetched later in the background as a safety net (gmot_prefetch).
CLASS_MODEL, CLASS_EFFECT, CLASS_LUA, CLASS_OTHER = 0, 1, 2, 3
EFFECT_DIRS = ("effects/", "particle/", "particles/", "sprites/", "decals/", "overlays/", "trails/", "fx/")
EFFECT_SHADERS = {"sprite", "spritecard", "splinecard", "splinerope", "unlittwotexture", "refract", "cable"}


def _texts(roots, pattern, binary=False) -> str:
    out = []
    for root in roots:
        for p in Path(root).rglob(pattern):
            try:
                data = p.read_bytes()
            except OSError:
                continue
            out.append(data.decode("latin-1" if binary else "utf-8", errors="replace").lower().replace("\\", "/"))
    return "\n".join(out)


def classify(ours, reached, refs, shaders, roots) -> dict:
    """texture ref -> CLASS_*, from the models, VMT shaders, particle files and Lua text of the roots."""
    lua = _texts(roots, "*.lua")
    pcf = _texts(roots, "*.pcf", binary=True)
    out = {}
    for ref in ours:
        vmts = [v for v, _p in refs.get(ref, [])]
        if ref in reached:
            out[ref] = CLASS_MODEL
        elif ref.startswith(EFFECT_DIRS) or any(shaders.get(v) in EFFECT_SHADERS or v in pcf for v in vmts):
            out[ref] = CLASS_EFFECT
        elif ref in lua or any(v in lua for v in vmts):
            out[ref] = CLASS_LUA
        else:
            out[ref] = CLASS_OTHER
    return out


def auto_jobs(minimum: int = 1) -> int:
    """worker processes for the encoder: every logical CPU but two, as long as each can have ~0.8 GB of free
    memory (a 2048 texture peaks around 0.6 GB); never fewer than `minimum`."""
    from texlab.jobs import auto_jobs as shared
    return shared(0.8, minimum)


def _run_tasks(tasks, order, jobs, root, emit) -> dict:
    """encode_one over the tasks in worker processes. A worker that dies (out of memory on a huge texture...)
    breaks the pool: what did not finish is done again one at a time in a fresh pool, so one bad file keeps
    its VTF instead of failing the whole addon."""
    from concurrent.futures.process import BrokenProcessPool

    from texlab.jobs import process_pool
    emit("PROGRESS", "stream", 0, len(tasks), "")
    results, done = {}, 0
    todo = list(range(len(tasks)))
    workers = max(1, jobs)
    while todo:
        broken = False
        with process_pool(workers) as ex:
            futures = {ex.submit(encode_one, tasks[k]): k for k in todo}
            for fut in as_completed(futures):
                k = futures[fut]
                try:
                    r = fut.result()
                except BrokenProcessPool:
                    broken = True
                    continue
                except Exception as e:  # noqa: BLE001
                    r = {"path": tasks[k][0], "keep": f"error: {type(e).__name__}"}
                results[order[k]] = r
                done += 1
                emit("PROGRESS", "stream", done, len(tasks), Path(r["path"]).relative_to(root).as_posix())
        todo = [k for k in todo if order[k] not in results]
        if not todo or not broken:
            break
        if workers == 1:
            # one worker runs the tasks in order: the first unfinished one killed it; it stays a VTF
            k = todo.pop(0)
            results[order[k]] = {"path": tasks[k][0], "keep": "error: worker died"}
            done += 1
        workers = 1
    return results


def compose_runtime(manifest: dict) -> str:
    """the addon's autorun file: manifest + shared runtime (loaded once, newest core wins)."""
    payload = json.dumps(manifest, separators=(",", ":"), sort_keys=True)
    level = "=="
    while f"]{level}]" in payload:
        level += "="
    decoder = (PACKAGE / "decoder.lua").read_text(encoding="utf-8")
    runtime = (PACKAGE / "runtime.lua").read_text(encoding="utf-8")
    return ("-- Texture stream of this addon (GMod Addon Optimizer): textures decoded in game, see the runtime below.\n"
            "if SERVER then AddCSLuaFile() return end\n"
            f"local MANIFEST = util.JSONToTable([{level}[{payload}]{level}])\n"
            f"if (GMOT and GMOT.CORE or 0) < {CORE_VERSION} then\n"
            f"{decoder}\n{runtime}\nGMOT.CORE = {CORE_VERSION}\nend\n"
            "GMOT.Register(MANIFEST)\n")


def convert(root: Path, jobs: int = 4, target: float = DEFAULT_TARGET_DB, fill: int = DEFAULT_FILL_LEVELS,
            siblings: list[Path] | None = None, emit=None, direct: dict | None = None) -> dict:
    """Stream the textures of the addon folder `root` in place. Returns a summary (sizes, counts, rows).
    direct: {VTF path: (PNG of the reduced image, DXT format, levels dropped, kind)} for textures
    the texture engine reduced but did not write: the file on disk is still the original and the blob is
    coded from the PNG, with the header of the reduced file (texlab.vtf.planned_reduced_vtf). The ones that
    stay a VTF after all come back in summary["direct_kept"] for the caller to write the regular way."""
    from texlab.density import sibling_roots
    from texlab.materials import MaterialResolver

    from .choose import alpha_uses, read_vmts
    from texlab.packing import stream_extension
    root = Path(root)
    emit = emit or (lambda *a: None)
    slug = slug_for(root.name)
    # a file type the addon's own addon.json "ignore" list keeps in the published package
    ext, packed = stream_extension(str(root), BLOB_DIR)
    summary = {"slug": slug, "target_db": target, "fill_levels": fill, "streamed": 0, "before": 0, "blobs": 0,
               "placeholders": 0, "runtime": 0, "kept": {}, "rows": []}
    mat_dir = root / "materials"
    if (root / BLOB_DIR).exists() or not mat_dir.is_dir():
        summary["skipped"] = "already streamed" if (root / BLOB_DIR).exists() else "no materials"
        return summary
    if siblings is None:
        siblings = sibling_roots(root)
    roots = [root] + list(siblings)
    resolver = MaterialResolver(roots)
    vmts = read_vmts(resolver)
    uses = alpha_uses(vmts, roots, resolver)
    ours = {}
    for p in sorted(mat_dir.rglob("*.vtf")):
        ours[p.relative_to(mat_dir).as_posix().lower()[:-4]] = p
    # textures a map of this addon draws at their pixel size stay VTF: the 16 px placeholder would set the
    # world's texture coordinates when the map loads, and swapping the texture later does not move them
    from texlab.size_bound import size_bound_refs
    bound = size_bound_refs(root) & ours.keys()
    for ref in bound:
        del ours[ref]
    if bound:
        summary["kept"]["size_bound"] = len(bound)
    # materials (this addon's and its neighbours') -> parameters that name one of our textures: every value
    # of the VMT that names one, whatever the parameter (the runtime only acts where the game really holds
    # that texture under that parameter)
    refs = {}
    for vref, _sh, params in vmts:
        m = resolver.material(vref)
        for param, tref in (m.all_textures() if m else ()):
            if tref in ours:
                refs.setdefault(tref, [])
                if (vref, param) not in refs[tref]:
                    refs[tref].append((vref, param))
    # models that draw them: this addon's own and the neighbours' (with their own materials first)
    model_tex = {}
    for mdl in sorted((root / "models").rglob("*.mdl")) if (root / "models").is_dir() else []:
        t = _model_textures(mdl, resolver) & ours.keys()
        if t:
            model_tex[mdl.relative_to(root).as_posix().lower()] = t
    for sib in siblings:
        sres = MaterialResolver([sib, root])
        for mdl in sorted((Path(sib) / "models").rglob("*.mdl")):
            t = _model_textures(mdl, sres) & ours.keys()
            if t:
                key = mdl.relative_to(sib).as_posix().lower()
                model_tex[key] = model_tex.get(key, set()) | t
    reached = set().union(*model_tex.values()) if model_tex else set()
    classes = classify(ours, reached, refs, {vref: sh for vref, sh, _p in vmts}, roots)
    order = sorted(ours)
    tmp_dir = root / BLOB_DIR / ("tmp_" + slug)
    direct = {direct_key(p): d for p, d in (direct or {}).items()}
    tasks = [(str(ours[ref]), uses.get(ref), target, fill, str(tmp_dir / f"{k}.tmp"), direct.get(direct_key(ours[ref])))
             for k, ref in enumerate(order)]
    results = _run_tasks(tasks, order, jobs, root, emit)
    summary["direct"] = sum(1 for t in tasks if t[5] is not None)
    summary["direct_kept"] = [tasks[k][0] for k, ref in enumerate(order)
                              if tasks[k][5] is not None and "keep" in results[ref]]
    # the slug also carries the content: two different addons whose folders have the same name never share
    # data_static/gmot/<slug>/ or materials/gmot/<slug>/ (the game would serve one addon's files to the other)
    # (the hash of each blob comes from the process that wrote it: opening a thousand files that were just
    # written, only to hash them, cost 10 s on a large addon, most of it the antivirus looking at each)
    content = hashlib.sha1()
    for k, ref in enumerate(order):
        if "keep" not in results[ref]:
            content.update(ref.encode("utf-8") + b"\0" + results[ref].pop("sha1"))
    slug = slug[:5] + content.hexdigest()[:7]
    summary["slug"] = slug
    summary["blob_ext"], summary["packed"] = ext, packed
    blob_dir = root / BLOB_DIR / slug
    blob_dir.mkdir(parents=True, exist_ok=True)
    index = {}
    tex = []
    for k, ref in enumerate(order):
        r = results[ref]
        if "keep" in r:
            summary["kept"][r["keep"]] = summary["kept"].get(r["keep"], 0) + 1
            continue
        i = len(tex) + 1
        index[ref] = i
        os.replace(tmp_dir / f"{k}.tmp", blob_dir / f"{i}{ext}")
        _write_atomic(ours[ref], r.pop("placeholder"))
        ph = ours[ref].stat().st_size
        flat = [x for pair in refs.get(ref, []) for x in pair]
        tex.append([f"{BLOB_DIR}/{slug}/{i}{ext}", ref, r["side"], flat, classes[ref]])
        summary["streamed"] += 1
        summary["before"] += r["before"]
        summary["blobs"] += r["blob"]
        summary["placeholders"] += ph
        summary["rows"].append({"path": ours[ref].relative_to(root).as_posix(), "i": i, "class": classes[ref],
                                "before": r["before"],
                                "blob": r["blob"], "placeholder": ph,
                                **{k2: r[k2] for k2 in ("normal", "alpha", "step", "drop", "alpha_step", "rgb_db", "alpha_db")}})
    if tmp_dir.is_dir():
        for leftover in tmp_dir.glob("*"):
            leftover.unlink()
        tmp_dir.rmdir()
    if not tex:
        for d in (blob_dir, blob_dir.parent, blob_dir.parent.parent):
            try:
                d.rmdir()
            except OSError:
                pass
        return summary
    models = {}
    for model, t in sorted(model_tex.items()):
        ids = sorted(index[x] for x in t if x in index)
        if ids:
            models[model] = ids
    manifest = {"slug": slug, "tex": tex, "models": models}
    lua = runtime_path(root, slug)
    lua.parent.mkdir(parents=True, exist_ok=True)
    lua.write_text(compose_runtime(manifest), encoding="utf-8", newline="\n")
    summary["runtime"] = lua.stat().st_size
    summary["models"] = len(models)
    return summary


def main(argv=None) -> int:
    """python -m texture_stream <addon> [--jobs N] [--target DB] [--fill N] [--report PATH]"""
    import argparse
    ap = argparse.ArgumentParser(prog="texture_stream")
    ap.add_argument("addon")
    ap.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2) // 2)))
    ap.add_argument("--target", type=float, default=DEFAULT_TARGET_DB)
    ap.add_argument("--fill", type=int, default=DEFAULT_FILL_LEVELS)
    ap.add_argument("--report")
    args = ap.parse_args(argv)
    s = convert(Path(args.addon), args.jobs, args.target, args.fill)
    if args.report:
        Path(args.report).write_text(json.dumps(s, indent=1), encoding="utf-8")
    print(f"texture stream: {s['streamed']} textures {s['before']:,} -> blobs {s['blobs']:,} + placeholders "
          f"{s['placeholders']:,} + runtime {s['runtime']:,} bytes; kept {s['kept']}", flush=True)
    return 0
