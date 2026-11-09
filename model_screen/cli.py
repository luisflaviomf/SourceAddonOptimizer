"""Worker command `modscreen`: Models "Tela" mode.

    modscreen <addon> [--work DIR] [--suffix _optimized]
              [--profile balanced|strong|strong_mirror|stream|exact] [--jobs N]

Copies the addon to <addon><suffix> (next to it), simplifies every compiled model in the copy
straight in the binaries (strong_mirror: whole-part remesh + mirror instancing, see remesh.py and
mirror.py), snaps VVD normals/tangents to the engine's own steps, removes
.dx80/.sw VTX files and strips every .dx90.vtx. A model that fails (unsupported structure,
validation error) is left exactly as it was. Progress lines follow the Models runner format.
Profile "exact" ("Sem perda") skips the per-model step: the models stay byte for byte as copied
and only the packaging steps (dx80/sw removal, engine-exact normals, strips) change them.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

PROGRESS_MODEL = "=== ({i}/{n}) MDL: {path}"
DONE = ">>> DONE ({i}/{n}) {path}"


_RESOLVERS: dict = {}


def _sibling_roots(addon_root: str, folder: str = "materials") -> list[Path]:
    """Other addons next to this one (e.g. lvs_framework beside lvs_cars_pack) that have `folder`:
    models often use materials (and Lua) another addon ships. Our own outputs (<addon><suffix>,
    work folders) are skipped."""
    src = Path(addon_root)
    out = []
    try:
        for d in sorted(src.parent.iterdir(), key=lambda q: q.name.lower()):
            if d.is_dir() and d != src and not d.name.lower().startswith(src.name.lower()) \
                    and (d / folder).is_dir():
                out.append(d)
    except OSError:
        pass
    return out


def _resolver(addon_root: str, siblings: bool = False):
    """One material index per worker process and addon: building it walks every file under
    materials/ (about a quarter of the per-model time). Only models/ changes during this step,
    so the index and the parsed materials stay valid for every model. siblings=True also looks in
    the addons beside this one, after the addon itself (remesh engine only)."""
    from texlab.materials import MaterialResolver
    key = (addon_root, siblings)
    res = _RESOLVERS.get(key)
    if res is None:
        roots = [Path(addon_root)] + (_sibling_roots(addon_root) if siblings else [])
        res = _RESOLVERS[key] = MaterialResolver(roots)
    return res


def _process_one(args):
    mdl_path, addon_root, profile_name = args[:3]
    mode = args[3] if len(args) > 3 else None          # "stream" for models the stream profile streams
    out_root = Path(args[4]) if len(args) > 4 else None
    fp_kind = args[5] if len(args) > 5 else None       # first-person model (texlab.firstperson)
    kept = args[6] if len(args) > 6 else None          # material made at run time (runtime_materials)
    from texlab.source_model import load_model
    from .binary import rewrite_model_bytes
    from .simplify import PROFILES, first_person, simplify_model
    from .uvtol import mesh_uv_tolerance
    import vvd_engine_exact
    mdl = Path(mdl_path)
    p = PROFILES[profile_name]
    if fp_kind:
        p = first_person(p, fp_kind)
    sizes0 = {ext: _size(mdl, ext) for ext in (".mdl", ".vvd", ".dx90.vtx")}
    if kept:
        # the game makes this model's look at run time (render-target picture, Lua-driven see-through
        # surface): neither streamed nor simplified, see runtime_materials.py
        return {"model": str(mdl), "ok": True, "skipped": f"material made at run time (kept): {kept}",
                "vertices_before": 0, "vertices_after": 0, "bytes_before": sum(sizes0.values()),
                "bytes_after": sum(sizes0.values())}
    mirror_stats = {}
    try:
        if p.engine != "copy":
            # models with LODs (VVD fixups, several VTX LODs): their LOD0 alone, then simplified like
            # any model (were all left as they were: 303 MiB of the 2026-11-08 test bench)
            from .binary import flatten_lods
            if flatten_lods(mdl):
                mirror_stats["lods_flattened"] = True
        streamed = None
        if p.engine == "stream" and mode == "stream":
            # original geometry -> data_static/gmo stream; the model keeps a coarse stand-in mesh
            import model_stream
            from texlab.packing import stream_extension
            resolver = _resolver(addon_root, siblings=True)
            rel = mdl.relative_to(out_root).as_posix()
            # a file type the addon's own addon.json "ignore" list keeps in the published package
            ext = stream_extension(str(addon_root), model_stream.STREAM_DIR)[0]
            name = model_stream.stream_name(rel, ext)
            try:
                mdl_b, vvd_b, vtx_b, sbytes = model_stream.stream_model(mdl, rel, out_root, resolver, ext=ext)
                st = {"vertices_before": _vvd_count(mdl), "vertices_after": _vvd_count_bytes(vvd_b)}
                streamed = {"stream": name, "stream_bytes": sbytes}
            except Exception as why:       # not streamable (weighted skinning...): Forte + espelho
                (out_root / name).unlink(missing_ok=True)
                mirror_stats = {"stream_skipped": f"{type(why).__name__}: {why}"}
        if streamed is not None:
            mirror_stats.update(streamed)
        elif p.engine in ("remesh", "stream"):
            from . import mirror, remesh
            resolver = _resolver(addon_root, siblings=True)
            uvt = mesh_uv_tolerance(mdl, resolver)
            # no mirror instancing on first-person models: extra bones on view models/hands for a small gain
            plan = remesh.build(mdl, p, uvt, remesh.mesh_uv_free(mdl, resolver), resolver, mirror=not fp_kind)
            if plan is None:
                raise ValueError("model has no triangles")
            st = plan["stats"]
            mdl_b, vvd_b, vtx_b, rst = rewrite_model_bytes(mdl, plan["main"], drop_tangents=p.drop_tangents)
            mdl_b, vtx_b, nbones = mirror.apply(mdl_b, vtx_b, plan)
            mirror_stats = {"mirrored_triangles": st["mirrored_out"], "mirror_bones": nbones}
        else:
            resolver = _resolver(addon_root)
            uvt = mesh_uv_tolerance(mdl, resolver)
            new, st = simplify_model(mdl, p, uvt)
            mdl_b, vvd_b, vtx_b, rst = rewrite_model_bytes(mdl, new, drop_tangents=p.drop_tangents)
        vvd_b, _ = vvd_engine_exact.snap_vvd(vvd_b)
        stem = Path(str(mdl.with_suffix("")))
        staged = {}
        for ext, data in ((".mdl", mdl_b), (".vvd", vvd_b), (".dx90.vtx", vtx_b)):
            tmp = Path(str(stem) + ext + ".modscreen.tmp")
            tmp.write_bytes(data)
            staged[ext] = tmp
        # validate the new files before replacing anything: same structure, readable geometry
        check_dir = stem.parent / (stem.name + ".modscreen_check")
        check_dir.mkdir(exist_ok=True)
        try:
            for ext, tmp in staged.items():
                shutil.copyfile(tmp, check_dir / (stem.name + ext))
            a = load_model(mdl)
            b = load_model(check_dir / (stem.name + ".mdl"))
            if len(a.meshes) != len(b.meshes) or any(len(mb.triangles) == 0 and len(ma.triangles) > 0
                                                        for ma, mb in zip(a.meshes, b.meshes)):
                raise ValueError("validation: a mesh lost all its triangles")
        finally:
            shutil.rmtree(check_dir, ignore_errors=True)
        for ext, tmp in staged.items():
            tmp.replace(Path(str(stem) + ext))
        return {"model": str(mdl), "ok": True, "vertices_before": st["vertices_before"],
                "vertices_after": st["vertices_after"], "bytes_before": sum(sizes0.values()),
                "bytes_after": sum(_size(mdl, e) for e in sizes0), **mirror_stats}
    except Exception as exc:
        for tmp in Path(str(mdl.with_suffix(""))).parent.glob(mdl.stem + ".*.modscreen.tmp"):
            tmp.unlink(missing_ok=True)
        if mirror_stats.get("stream") and out_root is not None:      # the model stays as it was
            (out_root / mirror_stats["stream"]).unlink(missing_ok=True)
        if str(exc) == "model has no triangles":
            # animation/attachment-only models (ARC9 hold_*): nothing to simplify, not a failure
            return {"model": str(mdl), "ok": True, "skipped": "no triangles", "vertices_before": 0,
                    "vertices_after": 0, "bytes_before": sum(sizes0.values()), "bytes_after": sum(sizes0.values())}
        return {"model": str(mdl), "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _vvd_count(mdl: Path) -> int:
    with open(mdl.with_suffix(".vvd"), "rb") as fh:     # the header is enough (numLODVertexes[0])
        raw = fh.read(20)
    return struct.unpack_from("<i", raw, 16)[0]


def _vvd_count_bytes(raw: bytes) -> int:
    return struct.unpack_from("<i", raw, 16)[0]


def _size(mdl: Path, ext: str) -> int:
    p = Path(str(mdl.with_suffix("")) + ext)
    return p.stat().st_size if p.exists() else 0


def _kept_rows(models: list[Path], out: Path) -> list[dict]:
    """Profile "exact": no per-model step (nothing is parsed or rewritten). Same progress lines and
    report rows as the other profiles; only the file sizes and the VVD header are read."""
    rows = []
    n = len(models)
    for i, mdl in enumerate(models, 1):
        size = sum(_size(mdl, e) for e in (".mdl", ".vvd", ".dx90.vtx"))
        try:
            nv = _vvd_count(mdl)
        except (OSError, struct.error):
            nv = 0
        rel = mdl.relative_to(out).as_posix()
        print(PROGRESS_MODEL.format(i=i, n=n, path=rel), flush=True)
        print(DONE.format(i=i, n=n, path=rel), flush=True)
        rows.append({"model": str(mdl), "ok": True, "vertices_before": nv, "vertices_after": nv,
                     "bytes_before": size, "bytes_after": size})
    return rows


def _copy_tree(src: Path, dst: Path, threads: int = 8, skip_top: tuple[str, ...] = ()) -> None:
    """shutil.copytree(src, dst) with the file copies (copy2) spread over threads: the same files
    and bytes, several times faster on SSDs. `skip_top`: top-level folder names left out."""
    from concurrent.futures import ThreadPoolExecutor
    pairs = []
    for dirpath, dirnames, filenames in os.walk(src, followlinks=True):
        rel = Path(dirpath).relative_to(src)
        if rel == Path(".") and skip_top:
            dirnames[:] = [d for d in dirnames if d not in skip_top]
        (dst / rel).mkdir(parents=True, exist_ok=True)
        pairs += [(Path(dirpath) / name, dst / rel / name) for name in filenames]
    pairs.sort(key=lambda pair: -pair[0].stat().st_size)  # big files first: better balance
    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        for _ in pool.map(lambda pair: shutil.copy2(*pair), pairs):
            pass


def _auto_jobs() -> int:
    return max(1, min(8, (os.cpu_count() or 2) // 2))


def _dll(name_dir: str, name: str) -> Path:
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    repo = Path(__file__).resolve().parents[1]
    roots += [repo / "tools" / name_dir, repo]
    for r in roots:
        if (r / name).is_file():
            return r / name
    raise FileNotFoundError(name)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="modscreen")
    ap.add_argument("addon")
    ap.add_argument("--work", default=None)
    ap.add_argument("--suffix", default="_optimized")
    from .simplify import PROFILES
    ap.add_argument("--profile", choices=tuple(PROFILES), default="balanced")
    ap.add_argument("--jobs", type=int, default=0, help="worker processes (0 = automatic)")
    ap.add_argument("--stream-min-kb", type=int, default=20, help="stream profile: smallest model streamed")
    ap.add_argument("--in-place", action="store_true", help="process the given folder itself (no copy)")
    ap.add_argument("--overwrite", action="store_true", help="replace an existing output folder")
    args = ap.parse_args(argv)
    src = Path(args.addon).resolve()
    if not (src / "models").is_dir():
        print(f"[ERROR] no models folder in {src} (select one addon folder that contains models/)", flush=True)
        return 2
    out = src if args.in_place else src.parent / (src.name + args.suffix)
    if not args.in_place and out.exists() and not args.overwrite:
        print(f"[ERROR] output already exists: {out} (enable Overwrite to replace it)", flush=True)
        return 2
    work = Path(args.work).resolve() if args.work else out.parent / (out.name + "_work")
    work.mkdir(parents=True, exist_ok=True)
    print(f"Work dir: {work}", flush=True)
    t0 = time.time()
    rest = copier = None
    if not args.in_place:
        print("== Step 1/3: Copy addon ==", flush=True)
        if out.exists():
            shutil.rmtree(out)
        # models/ first (needed now). Everything else is copied in the background while the models
        # are simplified; the material lookups read the source, which holds the same bytes.
        models_dir = next(p.name for p in src.iterdir() if p.is_dir() and p.name.lower() == "models")
        out.mkdir(parents=True)
        _copy_tree(src / models_dir, out / models_dir)
        copier = ThreadPoolExecutor(max_workers=1)
        rest = copier.submit(_copy_tree, src, out, 4, (models_dir,))
    models = sorted(p for p in (out / "models").rglob("*.mdl")
                    if Path(str(p.with_suffix("")) + ".dx90.vtx").exists() and p.with_suffix(".vvd").exists())
    bytes_before = sum(p.stat().st_size for p in (out / "models").rglob("*") if p.is_file())
    from .simplify import PROFILES as _P
    copy_only = _P[args.profile].engine == "copy"
    if copy_only:
        print(f"== Step 2/3: Keep models byte for byte ({args.profile}) ==", flush=True)
    else:
        print(f"== Step 2/3: Simplify models ({args.profile}) ==", flush=True)
    n = len(models)
    # Models are independent; the biggest go first so no large one is left running alone at the end.
    # Results are put back in path order, so the files and the report do not depend on scheduling.
    order = sorted(range(n), key=lambda i: -models[i].with_suffix(".vvd").stat().st_size)
    by_index = {}
    done = 0
    jobs = args.jobs if args.jobs > 0 else _auto_jobs()
    modes = [None] * n
    # first-person models (weapon view models, hands, attachments): limits of the view-model camera when
    # simplified. Streamed when rigid like any model: the runtime draws view models through a RenderOverride
    # set in PreDrawViewModel (runtime.lua) and decodes the carried weapons ahead of their first draw
    from texlab.firstperson import classify
    fp = classify(src)
    fp_kinds = [fp.get(m.relative_to(out).as_posix().lower()) for m in models]
    # models whose look the game makes at run time (render-target pictures, Lua-driven see-through
    # surfaces: scopes, sight lenses, screens) stay as authored, whatever the addon or weapon base
    kept = [None] * n
    if not copy_only:
        from texlab.materials import MaterialResolver
        from .runtime_materials import kept_reasons
        # a fresh index (not the per-process _resolver cache: this runs in the calling process)
        kept = kept_reasons(models, MaterialResolver([src] + _sibling_roots(str(src))),
                            [src] + _sibling_roots(str(src), "lua"))
    if _P[args.profile].engine == "stream":
        import model_stream
        for i, m in enumerate(models):
            size = sum(_size(m, e) for e in (".mdl", ".vvd", ".dx90.vtx"))
            rel = m.relative_to(out).as_posix()
            if model_stream.wants_stream(rel, size, args.stream_min_kb * 1024):
                modes[i] = "stream"
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        if copy_only:
            rows = _kept_rows(models, out)
        else:
            futures = {ex.submit(_process_one, (str(models[i]), str(src), args.profile, modes[i], str(out),
                                                fp_kinds[i], kept[i])): i
                       for i in order}
            for fut in as_completed(futures):
                res = fut.result()
                by_index[futures[fut]] = res
                done += 1
                rel = Path(res["model"]).relative_to(out).as_posix()
                print(PROGRESS_MODEL.format(i=done, n=n, path=rel), flush=True)
                if not res["ok"]:
                    print(f"[WARN] kept original: {rel}: {res['error']}", flush=True)
                print(DONE.format(i=done, n=n, path=rel), flush=True)
            rows = [by_index[i] for i in range(n)]
        manifest = {Path(r["model"]).relative_to(out).as_posix().lower(): r["stream"]
                    for r in rows if r.get("ok") and r.get("stream")}
        if manifest:
            import model_stream
            print(f"== Packaging: geometry streams ({len(manifest)} models) + Lua runtime ==", flush=True)
            model_stream.write_runtime(out, manifest, src.name)
            from texlab import packing
            if not packing.stream_extension(str(src), model_stream.STREAM_DIR)[1]:
                print(packing.IGNORED_WARNING.format(folder=model_stream.STREAM_DIR), flush=True)
        if rest is not None:
            rest.result()   # the rest of the addon is in place (raises if a copy failed)
            copier.shutdown()
        print("== Step 3/3: Packaging ==", flush=True)
        print("== Packaging: Remove obsolete dx80/sw vtx ==", flush=True)
        for pat in ("*.dx80.vtx", "*.sw.vtx"):
            for p in (out / "models").rglob(pat):
                p.unlink(missing_ok=True)
        print("== Packaging: VVD engine-exact normals ==", flush=True)
        import vvd_engine_exact
        vvd_engine_exact.optimize_tree(out / "models", report_path=work / "logs" / "vvd_engine_exact_summary.json",
                                       executor=ex)
        print("== Packaging: VTX TRI_STRIP exact-topology compression ==", flush=True)
        try:
            import vtx_tristrip_optimizer
            vtx_tristrip_optimizer.optimize_tree(out / "models", _dll("vtx", "meshopt_stripify.dll"),
                                                 report_path=work / "logs" / "vtx_tristrip_summary.json",
                                                 executor=ex)
        except Exception as exc:  # strips are optional: files stay as written
            print(f"[WARN] VTX strips skipped: {exc}", flush=True)
    bytes_after = sum(p.stat().st_size for p in (out / "models").rglob("*") if p.is_file())
    stream_bytes = sum(r.get("stream_bytes", 0) for r in rows if r.get("ok"))
    report = {"profile": args.profile, "models": n, "failed": sum(1 for r in rows if not r["ok"]),
              "models_bytes_before": bytes_before, "models_bytes_after": bytes_after,
              "vertices_before": sum(r.get("vertices_before", 0) for r in rows if r["ok"]),
              "vertices_after": sum(r.get("vertices_after", 0) for r in rows if r["ok"]),
              "streamed": sum(1 for r in rows if r.get("ok") and r.get("stream")), "stream_bytes": stream_bytes,
              "seconds": round(time.time() - t0, 1), "rows": rows}
    (work / "logs").mkdir(parents=True, exist_ok=True)
    (work / "logs" / "modscreen_summary.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    if stream_bytes:
        print(f"[MODSCREEN] streamed={report['streamed']} stream_bytes={stream_bytes} (data_static/gmo, drawn by "
              f"lua/autorun/client/gmo_stream_*.lua)", flush=True)
    print(f"[MODSCREEN] models={n} failed={report['failed']} bytes={bytes_before}->{bytes_after} "
          f"vertices={report['vertices_before']}->{report['vertices_after']} seconds={report['seconds']}", flush=True)
    print(f"Output addon: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
