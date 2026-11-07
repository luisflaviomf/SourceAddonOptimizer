"""Worker command `modscreen`: Models "Tela" mode.

    modscreen <addon> [--work DIR] [--suffix _optimized] [--profile balanced|strong] [--jobs N]

Copies the addon to <addon><suffix> (next to it), simplifies every compiled model in the copy
straight in the binaries, snaps VVD normals/tangents to the engine's own steps, removes
.dx80/.sw VTX files and strips every .dx90.vtx. A model that fails (unsupported structure,
validation error) is left exactly as it was. Progress lines follow the Models runner format.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROGRESS_MODEL = "=== ({i}/{n}) MDL: {path}"
DONE = ">>> DONE ({i}/{n}) {path}"


def _process_one(args):
    mdl_path, addon_root, profile_name = args
    from texlab.materials import MaterialResolver
    from texlab.source_model import load_model
    from .binary import rewrite_model_bytes
    from .simplify import PROFILES, simplify_model
    from .uvtol import mesh_uv_tolerance
    import vvd_engine_exact
    mdl = Path(mdl_path)
    p = PROFILES[profile_name]
    sizes0 = {ext: _size(mdl, ext) for ext in (".mdl", ".vvd", ".dx90.vtx")}
    try:
        resolver = MaterialResolver([Path(addon_root)])
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
                "bytes_after": sum(_size(mdl, e) for e in sizes0)}
    except Exception as exc:
        for tmp in Path(str(mdl.with_suffix(""))).parent.glob(mdl.stem + ".*.modscreen.tmp"):
            tmp.unlink(missing_ok=True)
        return {"model": str(mdl), "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _size(mdl: Path, ext: str) -> int:
    p = Path(str(mdl.with_suffix("")) + ext)
    return p.stat().st_size if p.exists() else 0


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
    ap.add_argument("--profile", choices=("balanced", "strong"), default="balanced")
    ap.add_argument("--jobs", type=int, default=max(1, min(6, (os.cpu_count() or 2) // 2)))
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
    if not args.in_place:
        print("== Step 1/3: Copy addon ==", flush=True)
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(src, out)
    models = sorted(p for p in (out / "models").rglob("*.mdl")
                    if Path(str(p.with_suffix("")) + ".dx90.vtx").exists() and p.with_suffix(".vvd").exists())
    bytes_before = sum(p.stat().st_size for p in (out / "models").rglob("*") if p.is_file())
    print(f"== Step 2/3: Simplify models ({args.profile}) ==", flush=True)
    rows = []
    n = len(models)
    jobs = [(str(m), str(out), args.profile) for m in models]
    done = 0
    with ProcessPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        for res in ex.map(_process_one, jobs, chunksize=1):
            done += 1
            rel = Path(res["model"]).relative_to(out).as_posix()
            print(PROGRESS_MODEL.format(i=done, n=n, path=rel), flush=True)
            if not res["ok"]:
                print(f"[WARN] kept original: {rel}: {res['error']}", flush=True)
            print(DONE.format(i=done, n=n, path=rel), flush=True)
            rows.append(res)
    print("== Step 3/3: Packaging ==", flush=True)
    print("== Packaging: Remove obsolete dx80/sw vtx ==", flush=True)
    for pat in ("*.dx80.vtx", "*.sw.vtx"):
        for p in (out / "models").rglob(pat):
            p.unlink(missing_ok=True)
    print("== Packaging: VVD engine-exact normals ==", flush=True)
    import vvd_engine_exact
    vvd_engine_exact.optimize_tree(out / "models", report_path=work / "logs" / "vvd_engine_exact_summary.json")
    print("== Packaging: VTX TRI_STRIP exact-topology compression ==", flush=True)
    try:
        import vtx_tristrip_optimizer
        vtx_tristrip_optimizer.optimize_tree(out / "models", _dll("vtx", "meshopt_stripify.dll"),
                                             report_path=work / "logs" / "vtx_tristrip_summary.json")
    except Exception as exc:  # strips are optional: files stay as written
        print(f"[WARN] VTX strips skipped: {exc}", flush=True)
    bytes_after = sum(p.stat().st_size for p in (out / "models").rglob("*") if p.is_file())
    report = {"profile": args.profile, "models": n, "failed": sum(1 for r in rows if not r["ok"]),
              "models_bytes_before": bytes_before, "models_bytes_after": bytes_after,
              "vertices_before": sum(r.get("vertices_before", 0) for r in rows if r["ok"]),
              "vertices_after": sum(r.get("vertices_after", 0) for r in rows if r["ok"]),
              "seconds": round(time.time() - t0, 1), "rows": rows}
    (work / "logs").mkdir(parents=True, exist_ok=True)
    (work / "logs" / "modscreen_summary.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"[MODSCREEN] models={n} failed={report['failed']} bytes={bytes_before}->{bytes_after} "
          f"vertices={report['vertices_before']}->{report['vertices_after']} seconds={report['seconds']}", flush=True)
    print(f"Output addon: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
