from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import selective_policy_models
import vehicle_steer_turn_basis_fix


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

    if script_name == "batch_optimize_selective_policy.py":
        import batch_optimize_selective_policy

        return _run_main_with_sysargv(batch_optimize_selective_policy.main, script_path, args)

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


def _sum_numeric_payload(dst: dict, src: dict) -> None:
    for key, value in src.items():
        if isinstance(value, dict):
            child = dst.get(key)
            if not isinstance(child, dict):
                child = {}
                dst[key] = child
            _sum_numeric_payload(child, value)
            continue
        if isinstance(value, bool):
            dst[key] = value
            continue
        if isinstance(value, (int, float)):
            current = dst.get(key, 0)
            if isinstance(current, bool) or not isinstance(current, (int, float)):
                current = 0
            dst[key] = current + value
            continue
        if key not in dst:
            dst[key] = value


def _merge_round_parts_summary_parts(summary_dir: Path, out_path: Path) -> dict | None:
    if not summary_dir.exists():
        return None

    part_paths = sorted(p for p in summary_dir.glob("*.json") if p.is_file())
    if not part_paths:
        return None

    merged: dict[str, object] = {}
    for part_path in part_paths:
        payload = _read_json(part_path)
        if not merged:
            for key, value in payload.items():
                if not isinstance(value, dict):
                    merged[key] = value
            merged["summary"] = {}
        summary_src = payload.get("summary")
        if isinstance(summary_src, dict):
            summary_dst = merged.setdefault("summary", {})
            if not isinstance(summary_dst, dict):
                merged["summary"] = {}
                summary_dst = merged["summary"]
            _sum_numeric_payload(summary_dst, summary_src)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return merged


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


_ADDON_MARKER_DIRS = {
    "cfg",
    "data",
    "effects",
    "entities",
    "gamemodes",
    "lua",
    "maps",
    "materials",
    "models",
    "particles",
    "resource",
    "scripts",
    "sound",
}
_ADDON_MARKER_FILES = {
    "addon.json",
    "addon.txt",
}


def _looks_like_addon_root(root: Path) -> bool:
    if not root.is_dir():
        return False

    for marker_file in _ADDON_MARKER_FILES:
        if (root / marker_file).is_file():
            return True

    for marker_dir in _ADDON_MARKER_DIRS:
        if (root / marker_dir).is_dir():
            return True

    return False


def _discover_child_addons(root: Path) -> tuple[list[Path], list[Path]]:
    addon_units: list[Path] = []
    skipped_without_models: list[Path] = []

    try:
        children = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except Exception:
        return addon_units, skipped_without_models

    for child in children:
        if not _looks_like_addon_root(child):
            continue
        if (child / "models").is_dir():
            addon_units.append(child)
        else:
            skipped_without_models.append(child)

    return addon_units, skipped_without_models


def _sum_tree_bytes(root: Path) -> int:
    if not root.exists():
        return 0

    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except Exception:
            continue

        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    stack.append(entry)
                    continue
                if entry.is_file():
                    total += entry.stat().st_size
            except Exception:
                continue

    return total


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _run_single_addon(
    args: argparse.Namespace,
    *,
    repo_root: Path,
    addon_path: Path,
    out_addon_dir: Path,
    work_dir: Path,
    decompile_script: Path,
    optimize_script: Path,
    compile_script: Path,
    selective_optimize_script: Path,
    round_parts_optimize_script: Path,
    emit_path_header: bool = True,
    emit_result_lines: bool = True,
) -> int:
    if emit_path_header:
        print(f"Addon input:  {addon_path}", flush=True)
        print(f"Addon output: {out_addon_dir}", flush=True)
        print(f"Work dir:     {work_dir}", flush=True)

    t_all = time.monotonic()
    orig_models_dir = addon_path / "models"

    try:
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

        optimize_extra_args: list[str] = []
        round_parts_summary_dir: Path | None = None
        round_parts_summary_path: Path | None = None
        active_optimize_script = optimize_script
        if args.experimental_ground_policy or args.experimental_round_parts_policy:
            logs_dir = work_dir / "logs"
            policy_map_path, policy_summary_path = selective_policy_models.write_final_policy_files(
                addon_path=addon_path,
                decompile_results=list(d.get("results", [])),
                src_root=work_dir / "src",
                logs_dir=logs_dir,
            )
            policy_summary = _read_json(policy_summary_path)
            interpretation = policy_summary.get("interpretation", {})
            counts = policy_summary.get("summary", {}).get("counts", {})
            counts_order = [
                "experimental_ground_main",
                "baseline_aircraft",
                "baseline_wheel",
                "baseline_rotor",
                "baseline_attachment",
                "baseline_detached",
                "baseline_uncertain_main",
                "baseline_small_unknown",
                "baseline_other",
            ]
            counts_text = " ".join(
                f"{name}={int(counts.get(name, 0))}"
                for name in counts_order
                if int(counts.get(name, 0)) > 0
            )
            print("[POLICY] Experimental selective ground policy: ENABLED")
            print(
                "[POLICY] "
                f"addon_shape={interpretation.get('label', 'unknown')} "
                f"why={interpretation.get('why', 'n/a')}"
            )
            print(f"[POLICY] counts {counts_text}" if counts_text else "[POLICY] counts (all baseline)")
            print(f"[POLICY] summary_json={policy_summary_path}")

            optimize_extra_args = [
                "--heuristic-map",
                str(policy_map_path),
                "--ground-final-autosmooth",
                "35",
                "--ground-weighted-mode",
                "FACE_AREA_WITH_ANGLE",
                "--ground-weighted-weight",
                "50",
                "--ground-shade-smooth",
            ]

            if args.experimental_round_parts_policy:
                active_optimize_script = round_parts_optimize_script
                round_parts_summary_dir = logs_dir / "round_parts_policy_parts"
                round_parts_summary_path = logs_dir / "round_parts_policy_summary.json"
                if round_parts_summary_dir.exists():
                    shutil.rmtree(round_parts_summary_dir, ignore_errors=True)
                if round_parts_summary_path.exists():
                    round_parts_summary_path.unlink(missing_ok=True)
                optimize_extra_args.extend(
                    [
                        "--wheel-variant",
                        "silhouette_floor_20",
                        "--embedded-variant",
                        "floor_24",
                        "--summary-dir",
                        str(round_parts_summary_dir),
                    ]
                )
                print("[ROUNDPOLICY] Experimental round-parts policy: ENABLED")
            else:
                active_optimize_script = selective_optimize_script

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
                str(active_optimize_script),
                "--",
                str(src_root),
                "--ratio",
                str(args.ratio),
                "--merge",
                str(args.merge),
                "--autosmooth",
                str(args.autosmooth),
                *(["--use-planar", "--planar-angle", str(args.planar_angle)] if args.use_planar else []),
                "--format",
                str(args.format),
                *optimize_extra_args,
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
                *(["--use-planar", "--planar-angle", str(args.planar_angle)] if args.use_planar else []),
                "--format",
                str(args.format),
                "--jobs",
                str(jobs),
                "--optimize-script",
                str(active_optimize_script),
            ]
            for extra_arg in optimize_extra_args:
                cmd.append(f"--optimize-extra-arg={extra_arg}")
            if args.resume_opt:
                cmd.append("--resume")
            _run(cmd)

        opt_qcs = [p for p in src_root.rglob("*_OPT.qc") if p.is_file()]
        if not opt_qcs:
            print(f"[ERROR] No *_OPT.qc files were generated under: {src_root}")
            return 2
        print(f"[OK] Generated {len(opt_qcs)} *_OPT.qc file(s).")

        if args.experimental_round_parts_policy and round_parts_summary_dir and round_parts_summary_path:
            merged_round_parts = _merge_round_parts_summary_parts(round_parts_summary_dir, round_parts_summary_path)
            if merged_round_parts:
                round_summary = merged_round_parts.get("summary", {})
                print("[ROUNDPOLICY] summary_json={}".format(round_parts_summary_path))
                print(
                    "[ROUNDPOLICY] "
                    f"wheel_models={int(round_summary.get('wheel_models', 0))} "
                    f"wheel_round_objects={int(round_summary.get('wheel_round_objects', 0))} "
                    f"wheel_adaptive_floor_hits={int(round_summary.get('wheel_adaptive_floor_hits', 0))} "
                    f"embedded_candidate_objects={int(round_summary.get('embedded_candidate_objects', 0))} "
                    f"embedded_qualified_round_parts={int(round_summary.get('embedded_qualified_round_parts', 0))} "
                    f"embedded_rejected_candidate_faces={int(round_summary.get('embedded_rejected_candidate_faces', 0))} "
                    f"embedded_adaptive_floor_hits={int(round_summary.get('embedded_adaptive_floor_hits', 0))}"
                )

        if args.experimental_steer_turn_basis_fix:
            print("\n== Experimental post-step: Vehicle steer turn basis fix ==")
            turn_basis_report = work_dir / "logs" / "vehicle_steer_turn_basis_fix_summary.json"
            turn_basis_payload = vehicle_steer_turn_basis_fix.apply_under_root(src_root, report_path=turn_basis_report)
            turn_basis_summary = turn_basis_payload.get("summary", {})
            print("[STEERBASIS] Experimental vehicle steer turn basis fix: ENABLED")
            print(
                "[STEERBASIS] "
                f"detected_qcs={int(turn_basis_summary.get('detected_qc_count', 0))} "
                f"patched_turn_files={int(turn_basis_summary.get('patched_turn_file_count', 0))}"
            )
            print(f"[STEERBASIS] summary_json={turn_basis_report}")

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
            print("== Packaging: Remove previous output folder ==")
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

        print("== Packaging: Copy addon files ==")
        shutil.copytree(addon_path, out_addon_dir, ignore=ignore_top_level_models)

        out_models_dir = out_addon_dir / "models"
        out_models_dir.mkdir(parents=True, exist_ok=True)
        print("== Packaging: Copy compiled models ==")
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
            print("== Packaging: Restore original models for decompile failures ==")
            _copy_original_models(decompile_failed, "Decompile failures")

        # If compile failed for some QCs, copy original models for those paths.
        if int(c.get("fail", 0)) > 0:
            print("== Packaging: Restore original models for compile failures ==")
            failed = [r for r in c.get("results", []) if r.get("status") != "ok"]
            _copy_original_models(failed, "Compile failures")

        # Safety net: copy originals for any missing .mdl in the output.
        print("== Packaging: Scan for missing optimized models ==")
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
            print("== Packaging: Restore missing optimized models from original addon ==")
            _copy_original_models(missing_mdls, "Missing optimized models")

        # Remove obsolete dx80 vtx from final addon.
        print("== Packaging: Remove obsolete dx80.vtx ==")
        removed_dx80 = 0
        for p in out_models_dir.rglob("*.dx80.vtx"):
            try:
                p.unlink()
                removed_dx80 += 1
            except Exception:
                pass
        if removed_dx80:
            print(f"[INFO] Removed .dx80.vtx files: {removed_dx80}")

        if args.cleanup_work_model_artifacts:
            print("\n== Finalize: Cleanup heavy work folders ==")
            cleanup_targets = [compiled_dir, src_root]
            removed_targets = 0
            failed_targets: list[tuple[Path, str]] = []
            for cleanup_target in cleanup_targets:
                if not cleanup_target.exists():
                    continue
                try:
                    shutil.rmtree(cleanup_target)
                    removed_targets += 1
                    print(f"[OK] Removed: {cleanup_target}")
                except Exception as ex:
                    failed_targets.append((cleanup_target, str(ex)))
                    print(f"[WARN] Failed to remove {cleanup_target}: {ex}")
            if removed_targets == 0 and not failed_targets:
                print("[INFO] Cleanup targets were already absent.")

        if emit_result_lines:
            dt = time.monotonic() - t_all
            print("\nDone.", flush=True)
            print(f"Output addon: {out_addon_dir}", flush=True)
            print(f"Work dir:     {work_dir}", flush=True)
            print(f"Elapsed:      {dt:.1f}s", flush=True)
        return 0
    except SystemExit as ex:
        if isinstance(ex.code, str) and ex.code:
            print(ex.code)
            return 1
        if ex.code is None:
            return 0
        try:
            return int(ex.code)
        except Exception:
            return 1
    except Exception:
        print("[ERROR] Unhandled exception while optimizing addon:")
        print(f"[ERROR] Addon: {addon_path}")
        print(traceback.format_exc())
        return 1


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
    ap.add_argument(
        "--use-planar",
        action="store_true",
        help="Apply an optional planar decimate pass before the current collapse decimate.",
    )
    ap.add_argument(
        "--planar-angle",
        type=float,
        default=2.0,
        help="Planar decimate angle in degrees when --use-planar is enabled.",
    )
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
        "--cleanup-work-model-artifacts",
        action="store_true",
        help="After a successful run, remove only work/src and work/compiled to reduce disk usage while keeping logs and other artifacts.",
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
    ap.add_argument(
        "--experimental-ground-policy",
        action="store_true",
        help="Experimental opt-in: apply selective weighted-normal repair only on confident ground main bodies; aircraft and unknown stay baseline.",
    )
    ap.add_argument(
        "--experimental-steer-turn-basis-fix",
        action="store_true",
        help="Experimental opt-in: detect the vehicle_steer steering-wheel basis signature and precompensate steering turn animations before compile.",
    )
    ap.add_argument(
        "--experimental-round-parts-policy",
        action="store_true",
        help="Experimental opt-in: preserve wheels and embedded round parts such as steering wheels/gauges using the current ratio, while keeping the standard pipeline unchanged when disabled.",
    )
    ap.add_argument(
        "--single-addon-only",
        action="store_true",
        help="Reject folders that contain multiple addon subfolders instead of running batch mode.",
    )
    args = ap.parse_args(argv)

    repo_root = _runtime_root()
    decompile_script = (repo_root / "batch_decompile_organize.py").resolve()
    optimize_script = (repo_root / "batch_optimize_qc.py").resolve()
    compile_script = (repo_root / "batch_compile_opt_qc.py").resolve()
    selective_optimize_script = (repo_root / "batch_optimize_selective_policy.py").resolve()
    round_parts_optimize_script = (repo_root / "batch_optimize_round_parts_policy.py").resolve()

    required_scripts = [decompile_script, optimize_script, compile_script]
    if args.experimental_ground_policy:
        required_scripts.append(selective_optimize_script)
    if args.experimental_round_parts_policy:
        required_scripts.append(round_parts_optimize_script)

    for p in required_scripts:
        if not p.exists():
            print(f"[ERROR] Missing required script: {p}")
            return 2

    addon_path = Path(args.addon).expanduser().resolve()
    if not addon_path.exists() or not addon_path.is_dir():
        print(f"[ERROR] Addon folder not found: {addon_path}")
        return 2

    if args.work:
        work_base = Path(args.work).expanduser().resolve()
    else:
        work_root = Path.cwd() if getattr(sys, "frozen", False) else repo_root
        work_base = work_root / "work" / f"{addon_path.name}{args.suffix}"
    work_dir = _choose_work_dir(work_base, overwrite=bool(args.overwrite_work))

    if _looks_like_addon_root(addon_path):
        addon_name = addon_path.name
        out_addon_base = addon_path.parent / f"{addon_name}{args.suffix}"
        out_addon_dir = _choose_dest_dir(out_addon_base, overwrite=bool(args.overwrite))
        return _run_single_addon(
            args,
            repo_root=repo_root,
            addon_path=addon_path,
            out_addon_dir=out_addon_dir,
            work_dir=work_dir,
            decompile_script=decompile_script,
            optimize_script=optimize_script,
            compile_script=compile_script,
            selective_optimize_script=selective_optimize_script,
            round_parts_optimize_script=round_parts_optimize_script,
        )

    addon_units, skipped_without_models = _discover_child_addons(addon_path)
    if args.single_addon_only and addon_units:
        print(
            f"[ERROR] Selected folder contains {len(addon_units)} addon folder(s). "
            "This command currently supports only a single addon root."
        )
        return 2

    if not addon_units:
        if skipped_without_models:
            print(
                "[ERROR] Selected folder contains addon-like subfolders, "
                "but none of them has a top-level models folder."
            )
        else:
            print(
                "[ERROR] Selected folder is neither a single addon root nor "
                "a folder containing addon roots with models."
            )
        return 2

    print(f"Addon input:  {addon_path}", flush=True)
    print(f"Addon output: {addon_path}", flush=True)
    print(f"Work dir:     {work_dir}", flush=True)
    print(f"[BATCH] Mode: folder of addons", flush=True)
    print(f"[BATCH] Addons detected: {len(addon_units)}", flush=True)
    if skipped_without_models:
        print(f"[BATCH] Skipping addon folders without models: {len(skipped_without_models)}", flush=True)
        for skipped_dir in skipped_without_models:
            print(f"[BATCH] Skipped (no models): {skipped_dir.name}", flush=True)

    t_all = time.monotonic()
    logs_dir = work_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    summary_results: list[dict[str, object]] = []
    ok_count = 0
    fail_count = 0
    total_before_bytes = 0
    total_after_bytes = 0

    for index, unit_addon_path in enumerate(addon_units, start=1):
        unit_out_base = unit_addon_path.parent / f"{unit_addon_path.name}{args.suffix}"
        unit_out_dir = _choose_dest_dir(unit_out_base, overwrite=bool(args.overwrite))
        unit_work_dir = work_dir / "units" / unit_addon_path.name
        unit_before_bytes = _sum_tree_bytes(unit_addon_path)
        total_before_bytes += unit_before_bytes

        print(f"\n== Batch addon {index}/{len(addon_units)}: {unit_addon_path.name} ==", flush=True)
        print(f"[BATCH] Source: {unit_addon_path}", flush=True)
        print(f"[BATCH] Output: {unit_out_dir}", flush=True)

        unit_start = time.monotonic()
        unit_exit_code = _run_single_addon(
            args,
            repo_root=repo_root,
            addon_path=unit_addon_path,
            out_addon_dir=unit_out_dir,
            work_dir=unit_work_dir,
            decompile_script=decompile_script,
            optimize_script=optimize_script,
            compile_script=compile_script,
            selective_optimize_script=selective_optimize_script,
            round_parts_optimize_script=round_parts_optimize_script,
            emit_path_header=False,
            emit_result_lines=False,
        )
        unit_elapsed_seconds = time.monotonic() - unit_start
        unit_after_bytes = _sum_tree_bytes(unit_out_dir) if unit_out_dir.exists() else 0
        total_after_bytes += unit_after_bytes
        unit_status = "ok" if unit_exit_code == 0 else "fail"
        if unit_exit_code == 0:
            ok_count += 1
        else:
            fail_count += 1

        print(
            f"[BATCH] Result: {unit_addon_path.name} | status={unit_status} "
            f"exit={unit_exit_code} elapsed={unit_elapsed_seconds:.1f}s",
            flush=True,
        )

        summary_results.append(
            {
                "name": unit_addon_path.name,
                "status": unit_status,
                "exit_code": unit_exit_code,
                "input_path": str(unit_addon_path),
                "output_path": str(unit_out_dir),
                "work_dir": str(unit_work_dir),
                "logs_dir": str(unit_work_dir / "logs"),
                "before_bytes": unit_before_bytes,
                "after_bytes": unit_after_bytes,
                "elapsed_seconds": round(unit_elapsed_seconds, 3),
            }
        )

    dt = time.monotonic() - t_all
    batch_summary = {
        "mode": "batch_folder_of_addons",
        "input_root": str(addon_path),
        "output_root": str(addon_path),
        "work_dir": str(work_dir),
        "suffix": args.suffix,
        "total_units": len(addon_units),
        "ok": ok_count,
        "fail": fail_count,
        "skipped_without_models": [str(p) for p in skipped_without_models],
        "totals": {
            "before_bytes": total_before_bytes,
            "after_bytes": total_after_bytes,
            "delta_bytes": total_after_bytes - total_before_bytes,
        },
        "results": summary_results,
        "elapsed_seconds": round(dt, 3),
    }
    batch_summary_path = logs_dir / "models_batch_summary.json"
    _write_json(batch_summary_path, batch_summary)

    print(f"[BATCH] summary_json={batch_summary_path}", flush=True)
    print("\nDone.", flush=True)
    print(f"Output addon: {addon_path}", flush=True)
    print(f"Work dir:     {work_dir}", flush=True)
    print(f"Elapsed:      {dt:.1f}s", flush=True)
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
