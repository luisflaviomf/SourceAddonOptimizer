from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _quote_cmd(cmd: list[str]) -> str:
    def q(s: str) -> str:
        if not s:
            return '""'
        if any(ch in s for ch in (' ', "\t", '"')):
            return '"' + s.replace('"', '\\"') + '"'
        return s

    return " ".join(q(c) for c in cmd)


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print(f"+ {_quote_cmd(cmd)}")

    if _try_run_inprocess(cmd):
        return

    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def _try_run_inprocess(cmd: list[str]) -> bool:
    """
    When frozen (PyInstaller), sys.executable points to the worker EXE (not a Python interpreter).
    Some steps call: [sys.executable, <script.py>, ...]. In that case we run the script in-process.
    """
    if not getattr(sys, "frozen", False):
        return False

    if len(cmd) < 2:
        return False

    try:
        exe0 = Path(cmd[0]).resolve()
        sys_exe = Path(sys.executable).resolve()
    except Exception:
        return False

    if exe0 != sys_exe:
        return False

    script_path = Path(cmd[1])
    if script_path.suffix.lower() != ".py":
        return False

    script_name = script_path.name.lower()
    args = cmd[2:]

    if script_name == "batch_decompile_organize.py":
        import batch_decompile_organize

        return _run_main_with_sysargv(batch_decompile_organize.main, script_path, args)

    if script_name == "batch_compile_opt_qc.py":
        import batch_compile_opt_qc

        return _run_main_with_sysargv(batch_compile_opt_qc.main, script_path, args)

    if script_name == "batch_optimize_parallel.py":
        import batch_optimize_parallel

        try:
            code = int(batch_optimize_parallel.main(args) or 0)
        except SystemExit as ex:
            code = int(ex.code or 0)
        if code != 0:
            raise SystemExit(code)
        return True

    return False


def _run_main_with_sysargv(main_func, script_path: Path, args: list[str]) -> bool:
    old_argv = sys.argv[:]
    sys.argv = [str(script_path), *args]
    try:
        try:
            result = main_func()
            code = int(result or 0)
        except SystemExit as ex:
            code = int(ex.code or 0)
        except Exception:
            import traceback

            print("[ERROR] Unhandled exception while running script in-process:")
            print(f"[ERROR] Script: {script_path}")
            print(traceback.format_exc())
            code = 1
    finally:
        sys.argv = old_argv

    if code != 0:
        raise SystemExit(code)
    return True


def _detect_blender(blender_arg: str | None) -> Path:
    if blender_arg:
        p = Path(blender_arg).expanduser().resolve()
        if not p.exists():
            raise SystemExit(f"[ERROR] Blender not found: {p}")
        return p

    which = shutil.which("blender") or shutil.which("blender.exe")
    if which:
        return Path(which).resolve()

    candidates = [
        r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
    ]
    for c in candidates:
        p = Path(c)
        if p.exists():
            return p.resolve()

    raise SystemExit(
        "[ERROR] Could not find Blender. Pass --blender \"C:\\path\\to\\blender.exe\"."
    )


def _choose_dest_dir(base: Path, *, overwrite: bool) -> Path:
    if not base.exists():
        return base
    if overwrite:
        shutil.rmtree(base, ignore_errors=True)
        return base
    return base.parent / f"{base.name}_{_ts()}"


def _choose_work_dir(base: Path, *, overwrite: bool) -> Path:
    if not base.exists():
        return base
    if overwrite:
        shutil.rmtree(base, ignore_errors=True)
        return base
    return base.parent / f"{base.name}_{_ts()}"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="End-to-end: decompile+organize -> Blender optimize -> compile+restore .phy -> create new addon folder."
    )
    ap.add_argument("addon", help="Path to original addon folder")
    ap.add_argument("--suffix", default="_otimizado", help="Suffix for output addon folder")
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output addon folder if it already exists (otherwise add timestamp).",
    )
    ap.add_argument(
        "--work",
        default=None,
        help="Work dir (default: ./work/<addon_name><suffix>).",
    )
    ap.add_argument(
        "--overwrite-work",
        action="store_true",
        help="Overwrite work dir if it already exists (otherwise add timestamp).",
    )
    ap.add_argument("--blender", default=None, help="Path to blender.exe (auto-detect if omitted).")
    ap.add_argument(
        "--decompile-jobs",
        type=int,
        default=1,
        help="Parallel Crowbar jobs for decompile step (default: 1).",
    )
    ap.add_argument("--ratio", type=float, default=0.50, help="Decimate ratio (1.0 = no decimate).")
    ap.add_argument(
        "--merge",
        type=float,
        default=0.0,
        help="Merge-by-distance (0 disables; recommended for animated/rigid parts).",
    )
    ap.add_argument("--autosmooth", type=float, default=45.0, help="Auto smooth angle in degrees.")
    ap.add_argument("--format", default="smd", choices=["smd", "dmx"], help="Export format.")
    ap.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="Parallel Blender jobs for optimize step (0 = auto, 1 = single process).",
    )
    ap.add_argument(
        "--studiomdl",
        default=None,
        help="Path to studiomdl.exe (optional; defaults to batch_compile_opt_qc.py default).",
    )
    ap.add_argument(
        "--compile-jobs",
        type=int,
        default=1,
        help="Parallel compile jobs for studiomdl (default: 1, 0 = auto).",
    )
    ap.add_argument(
        "--no-restore-phy",
        action="store_true",
        help="Disable restoring original .phy from work/<addon>/original/models after compile.",
    )
    ap.add_argument(
        "--require-phy-backup",
        action="store_true",
        help="Fail compile if .phy backup is missing when restore is enabled.",
    )
    ap.add_argument(
        "--restore-skins",
        dest="restore_skins",
        action="store_true",
        default=True,
        help="Restore original skinfamilies table from the original addon models (slower; use when skins break).",
    )
    ap.add_argument(
        "--no-restore-skins",
        dest="restore_skins",
        action="store_false",
        help="Disable skinfamilies restore (faster).",
    )
    ap.add_argument(
        "--compile-verbose",
        action="store_true",
        help="Enable verbose studiomdl output and full per-QC logs (slower; use for debugging).",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Abort if any compile failures occur (default: package anyway, using original models for failures).",
    )
    ap.add_argument(
        "--resume-opt",
        action="store_true",
        help="Skip QCs already optimized in the Blender step.",
    )
    args = ap.parse_args(argv)

    repo_root = _runtime_root()
    decompile_script = (repo_root / "batch_decompile_organize.py").resolve()
    optimize_script = (repo_root / "batch_optimize_qc.py").resolve()
    compile_script = (repo_root / "batch_compile_opt_qc.py").resolve()

    for p in (decompile_script, optimize_script, compile_script):
        if not p.exists():
            print(f"[ERROR] Missing required script: {p}")
            return 2

    addon_path = Path(args.addon).expanduser().resolve()
    if not addon_path.exists() or not addon_path.is_dir():
        print(f"[ERROR] Addon folder not found: {addon_path}")
        return 2

    addon_name = addon_path.name
    out_addon_base = addon_path.parent / f"{addon_name}{args.suffix}"
    out_addon_dir = _choose_dest_dir(out_addon_base, overwrite=bool(args.overwrite))

    if args.work:
        work_base = Path(args.work).expanduser().resolve()
    else:
        work_root = Path.cwd() if getattr(sys, "frozen", False) else repo_root
        work_base = work_root / "work" / f"{addon_name}{args.suffix}"
    work_dir = _choose_work_dir(work_base, overwrite=bool(args.overwrite_work))

    print(f"Addon input:  {addon_path}")
    print(f"Addon output: {out_addon_dir}")
    print(f"Work dir:     {work_dir}")

    t_all = time.monotonic()

    # 1) Decompile + organize + backup .phy
    print("\n== Step 1/3: Decompile + organize + backup .phy ==")
    decomp_cmd = [
        sys.executable,
        str(decompile_script),
        str(addon_path),
        "--out",
        str(work_dir),
        "--force",
        "--jobs",
        str(args.decompile_jobs),
    ]
    _run(decomp_cmd)
    decompile_manifest = work_dir / "logs" / "decompile_manifest.json"
    if not decompile_manifest.exists():
        print(f"[ERROR] Missing decompile manifest: {decompile_manifest}")
        return 2
    d = _read_json(decompile_manifest)
    print(
        f"[OK] Decompile: total={d.get('total')} ok={d.get('ok')} skipped={d.get('skipped')} fail={d.get('fail')}"
    )
    if int(d.get("total", 0)) <= 0:
        print(f"[ERROR] Decompile produced no results (see {decompile_manifest})")
        return 2
    decompile_failed = []
    if int(d.get("fail", 0)) > 0:
        decompile_failed = [r for r in d.get("results", []) if r.get("status") != "ok"]
        print(
            f"[WARN] Decompile had failures: fail={d.get('fail')} (see {decompile_manifest}). "
            "Will keep original models for these."
        )

    # 2) Blender optimize (generates *_OPT.qc)
    print("\n== Step 2/3: Blender optimize ==")
    blender_exe = _detect_blender(args.blender)
    src_root = work_dir / "src"
    if not src_root.exists():
        print(f"[ERROR] Missing src root: {src_root}")
        return 2

    jobs = 1 if args.jobs is None else args.jobs
    if jobs < 0:
        print("[ERROR] --jobs must be >= 0")
        return 2

    if jobs == 1:
        cmd = [
            str(blender_exe),
            "--background",
            "--python",
            str(optimize_script),
            "--",
            str(src_root),
            "--ratio",
            str(args.ratio),
            "--merge",
            str(args.merge),
            "--autosmooth",
            str(args.autosmooth),
            "--format",
            str(args.format),
        ]
        if args.resume_opt:
            cmd.append("--resume")
        _run(cmd)
    else:
        parallel_script = (repo_root / "batch_optimize_parallel.py").resolve()
        if not parallel_script.exists():
            print(f"[ERROR] Missing required script: {parallel_script}")
            return 2
        cmd = [
            sys.executable,
            str(parallel_script),
            str(src_root),
            "--blender",
            str(blender_exe),
            "--ratio",
            str(args.ratio),
            "--merge",
            str(args.merge),
            "--autosmooth",
            str(args.autosmooth),
            "--format",
            str(args.format),
            "--jobs",
            str(jobs),
        ]
        if args.resume_opt:
            cmd.append("--resume")
        _run(cmd)

    opt_qcs = [p for p in src_root.rglob("*_OPT.qc") if p.is_file()]
    if not opt_qcs:
        print(f"[ERROR] No *_OPT.qc files were generated under: {src_root}")
        return 2
    print(f"[OK] Generated {len(opt_qcs)} *_OPT.qc file(s).")

    # 3) Compile + (optional) restore .phy to compiled/models
    print("\n== Step 3/3: Compile + restore .phy ==")
    compiled_dir = work_dir / "compiled"
    cmd = [sys.executable, str(compile_script), str(src_root), "--out", str(compiled_dir)]
    if args.studiomdl:
        cmd.extend(["--studiomdl", str(Path(args.studiomdl).expanduser().resolve())])
    if args.compile_jobs is not None:
        cmd.extend(["--compile-jobs", str(int(args.compile_jobs))])
    if args.compile_verbose:
        cmd.extend(["--studiomdl-verbose", "--log-detail", "full"])
    if args.no_restore_phy:
        cmd.append("--no-restore-phy")
    else:
        cmd.extend(["--restore-phy-from", str((work_dir / "original" / "models").resolve())])
        if args.require_phy_backup:
            cmd.append("--require-phy-backup")
    if args.restore_skins:
        orig_models_dir = addon_path / "models"
        if orig_models_dir.exists():
            cmd.extend(["--restore-skin-from", str(orig_models_dir.resolve())])
        else:
            print(f"[WARN] Original models folder not found; skin restore disabled: {orig_models_dir}")
    _run(cmd)

    compile_summary = compiled_dir / "compile_summary.json"
    if not compile_summary.exists():
        print(f"[ERROR] Missing compile summary: {compile_summary}")
        return 2
    c = _read_json(compile_summary)
    print(f"[OK] Compile summary: total={c.get('total')} ok={c.get('ok')} fail={c.get('fail')}")
    if int(c.get("total", 0)) <= 0:
        print(f"[ERROR] Compile summary has total=0 (see {compile_summary})")
        return 2
    if int(c.get("fail", 0)) > 0 and args.strict:
        print(f"[ERROR] Compile had failures: fail={c.get('fail')} (see {compile_summary})")
        return 2
    if int(c.get("total", 0)) < len(opt_qcs):
        print(
            f"[ERROR] Compile scanned fewer QCs than expected: compile_total={c.get('total')} opt_qcs={len(opt_qcs)}"
        )
        return 2

    compiled_models = compiled_dir / "models"
    if not compiled_models.exists():
        print(f"[ERROR] Missing compiled models folder: {compiled_models}")
        return 2
    compiled_mdl_count = sum(1 for _ in compiled_models.rglob("*.mdl"))
    if compiled_mdl_count <= 0:
        if args.strict:
            print(f"[ERROR] No .mdl files found under: {compiled_models}")
            return 2
        print(f"[WARN] No .mdl files found under: {compiled_models}")
    else:
        print(f"[OK] Compiled .mdl files: {compiled_mdl_count}")

    # Create output addon
    print("\n== Packaging: Create optimized addon folder ==")
    if out_addon_dir.exists():
        # If overwrite was false, out_addon_dir is timestamped and should not exist.
        # If overwrite was true, it should have been removed already.
        shutil.rmtree(out_addon_dir, ignore_errors=True)

    # IMPORTANT: ignore only the *top-level* "models" folder. Many addons have materials/models/,
    # which must be preserved (VMT/VTF live there).
    addon_root_resolved = addon_path.resolve()

    def ignore_top_level_models(dirpath: str, names: list[str]):
        try:
            cur = Path(dirpath).resolve()
        except Exception:
            cur = Path(dirpath)
        if cur == addon_root_resolved and "models" in names:
            return {"models"}
        return set()

    shutil.copytree(addon_path, out_addon_dir, ignore=ignore_top_level_models)

    out_models_dir = out_addon_dir / "models"
    out_models_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(compiled_models, out_models_dir, dirs_exist_ok=True)

    def _copy_original_models(items: list[dict], reason: str) -> None:
        if not items:
            return
        print(f"[WARN] {reason}: {len(items)}. Using original models for those.")
        allowed_ext = {".mdl", ".vvd", ".vtx", ".phy", ".ani"}
        copied = 0
        skipped = 0
        for item in items:
            model_rel = (
                item.get("model_rel")
                or item.get("model_rel_fallback")
                or item.get("mdl_rel_fs")
            )
            if not model_rel:
                skipped += 1
                continue
            src_mdl = addon_path / "models" / model_rel
            if not src_mdl.exists():
                skipped += 1
                continue
            stem = src_mdl.stem.lower()
            src_dir = src_mdl.parent
            dst_dir = out_models_dir / Path(model_rel).parent
            dst_dir.mkdir(parents=True, exist_ok=True)
            for p in src_dir.iterdir():
                if not p.is_file():
                    continue
                name_lower = p.name.lower()
                if not name_lower.startswith(stem + "."):
                    continue
                if p.suffix.lower() not in allowed_ext:
                    continue
                shutil.copy2(p, dst_dir / p.name)
                copied += 1
        print(f"[INFO] Original model files copied: {copied} (skipped={skipped})")

    # Copy originals for decompile failures (no QC produced).
    if decompile_failed:
        _copy_original_models(decompile_failed, "Decompile failures")

    # If compile failed for some QCs, copy original models for those paths.
    if int(c.get("fail", 0)) > 0:
        failed = [r for r in c.get("results", []) if r.get("status") != "ok"]
        _copy_original_models(failed, "Compile failures")

    # Safety net: copy originals for any missing .mdl in the output.
    missing_mdls = []
    if orig_models_dir.exists():
        orig_mdl_map: dict[str, str] = {}
        for p in orig_models_dir.rglob("*.mdl"):
            try:
                rel = p.relative_to(orig_models_dir).as_posix()
            except Exception:
                rel = p.name
            orig_mdl_map[rel.lower()] = rel
        out_mdl_set = {
            p.relative_to(out_models_dir).as_posix().lower()
            for p in out_models_dir.rglob("*.mdl")
        }
        for key, rel in orig_mdl_map.items():
            if key not in out_mdl_set:
                missing_mdls.append({"model_rel": rel})
    if missing_mdls:
        _copy_original_models(missing_mdls, "Missing optimized models")

    # Remove obsolete dx80 vtx from final addon.
    removed_dx80 = 0
    for p in out_models_dir.rglob("*.dx80.vtx"):
        try:
            p.unlink()
            removed_dx80 += 1
        except Exception:
            pass
    if removed_dx80:
        print(f"[INFO] Removed .dx80.vtx files: {removed_dx80}")

    dt = time.monotonic() - t_all
    print("\nDone.")
    print(f"Output addon: {out_addon_dir}")
    print(f"Work dir:     {work_dir}")
    print(f"Elapsed:      {dt:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
