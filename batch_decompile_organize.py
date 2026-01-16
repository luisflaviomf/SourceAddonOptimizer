#!/usr/bin/env python3
# Batch decompile Source models (.mdl) using Crowbar CLI, organize outputs per-model,
# and back up original .phy (optionally full binaries) for later restore after compile.

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
_CROWBAR_ENV = os.environ.get("CROWBAR_EXE")
DEFAULT_CROWBAR = Path(_CROWBAR_ENV).expanduser().resolve() if _CROWBAR_ENV else (SCRIPT_DIR / "CrowbarCommandLineDecomp.exe")

MODELNAME_RE = re.compile(r'^\s*\$modelname\s+(".*?"|\S+)', re.IGNORECASE)
ERROR_RE = re.compile(r"(error|fatal|cannot|can't|could not|failed|missing)", re.IGNORECASE)


def strip_line_comments(line: str) -> str:
    out = []
    in_quote = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            in_quote = not in_quote
        if not in_quote and ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            break
        out.append(ch)
        i += 1
    return "".join(out).strip()


def parse_modelname(qc_path: Path):
    try:
        text = qc_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    for raw_line in text.splitlines():
        line = strip_line_comments(raw_line)
        if not line:
            continue
        m = MODELNAME_RE.match(line)
        if m:
            return m.group(1).strip()
    return None


def normalize_modelname(raw: str):
    if not raw:
        return None, "missing_modelname"
    name = raw.strip()
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        name = name[1:-1]
    name = name.strip().replace("\\", "/")

    while name.startswith("./"):
        name = name[2:]
    if name.startswith("/"):
        name = name[1:]

    lower = name.lower()
    idx = lower.find("models/")
    if idx != -1:
        name = name[idx + len("models/") :]
        if name.startswith("/"):
            name = name[1:]

    if re.match(r"^[a-zA-Z]:", name):
        return None, "absolute_modelname"
    if not name:
        return None, "empty_modelname"
    if not name.lower().endswith(".mdl"):
        name += ".mdl"
    return name, None


def safe_slug(text: str, max_len: int = 120) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", text.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "item"
    if len(s) > max_len:
        s = s[:max_len]
    return s


def pick_models_dir(input_path: Path):
    if input_path.is_file():
        if input_path.suffix.lower() == ".mdl":
            return input_path.parent, input_path.stem
        raise ValueError(f"Unsupported input file: {input_path}")

    if not input_path.is_dir():
        raise ValueError(f"Input path not found: {input_path}")

    if input_path.name.lower() == "models":
        return input_path, input_path.parent.name

    candidate = input_path / "models"
    if candidate.is_dir():
        return candidate, input_path.name

    # Fallback: accept any folder that contains .mdl files.
    try:
        next(input_path.rglob("*.mdl"))
        return input_path, input_path.name
    except StopIteration:
        raise ValueError(f"Could not find a 'models' folder (or any .mdl) under: {input_path}")


def find_mdls(models_dir: Path):
    mdls = [p for p in models_dir.rglob("*.mdl") if p.is_file()]
    return sorted(mdls, key=lambda p: str(p).lower())


def _match_any(patterns, text: str) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def run_crowbar(crowbar_exe: Path, mdl_path: Path, out_dir: Path, log_path: Path):
    cmd = [str(crowbar_exe), "-p", str(mdl_path), "-o", str(out_dir)]
    last_lines = deque(maxlen=60)
    error_lines = []

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"MDL: {mdl_path}\n")
        log.write(f"OUT: {out_dir}\n")
        log.write("CMD: " + " ".join(cmd) + "\n\n")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            last_lines.append(line.rstrip("\n"))
            if ERROR_RE.search(line):
                err = re.sub(r"\s+", " ", line.strip())
                if err:
                    error_lines.append(err)
        rc = proc.wait()
        log.write(f"\n[EXIT] returncode={rc}\n")

    return rc, list(last_lines), error_lines


def backup_file(src: Path, dst: Path, errors: list, dry_run: bool) -> bool:
    try:
        if not src.exists():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dry_run:
            shutil.copy2(src, dst)
        return True
    except Exception as e:
        errors.append(f"{src} -> {dst}: {e}")
        return False


def backup_full_binaries(mdl_path: Path, model_rel_path: Path, backup_models_dir: Path, errors: list, dry_run: bool):
    ok = []

    # Canonical targets based on $modelname.
    ok.append(
        backup_file(
            mdl_path,
            backup_models_dir / model_rel_path,
            errors,
            dry_run,
        )
    )
    ok.append(
        backup_file(
            mdl_path.with_suffix(".vvd"),
            backup_models_dir / model_rel_path.with_suffix(".vvd"),
            errors,
            dry_run,
        )
    )
    ok.append(
        backup_file(
            mdl_path.with_suffix(".phy"),
            backup_models_dir / model_rel_path.with_suffix(".phy"),
            errors,
            dry_run,
        )
    )

    # Copy all VTX variants (e.g. .dx90.vtx, .dx80.vtx) for this stem.
    stem_lower = mdl_path.stem.lower()
    try:
        for p in mdl_path.parent.iterdir():
            if not p.is_file():
                continue
            name_lower = p.name.lower()
            if not name_lower.startswith(stem_lower + "."):
                continue
            if p.suffix.lower() != ".vtx":
                continue
            ok.append(backup_file(p, backup_models_dir / model_rel_path.parent / p.name, errors, dry_run))
    except Exception as e:
        errors.append(f"iterdir({mdl_path.parent}): {e}")

    return any(ok)


def rewrite_qc_modelname(qc_path: Path, model_rel: str) -> bool:
    """
    Ensures QC has $modelname "<model_rel>" so downstream compile outputs to the expected models/<...> path.
    Returns True if the file was modified.
    """
    try:
        text = qc_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False

    line_re = re.compile(r'^(\s*\$modelname\s+)(\".*?\"|\S+)(.*)$', re.IGNORECASE)
    out_lines = []
    changed = False
    replaced = False
    for raw in text.splitlines(keepends=True):
        if not replaced:
            m = line_re.match(raw)
            if m:
                prefix, _, suffix = m.groups()
                out_lines.append(f'{prefix}"{model_rel}"{suffix}')
                changed = True
                replaced = True
                continue
        out_lines.append(raw)

    if not replaced:
        # Insert at top if missing.
        out_lines.insert(0, f'$modelname "{model_rel}"\n')
        changed = True

    if not changed:
        return False

    try:
        qc_path.write_text("".join(out_lines), encoding="utf-8", errors="replace")
    except Exception:
        return False
    return True


def process_one_mdl(
    idx: int,
    mdl: Path,
    models_dir: Path,
    src_root: Path,
    backup_models_dir: Path,
    logs_dir: Path,
    tmp_root: Path,
    crowbar_exe: Path,
    args,
):
    log_lines = []

    def log(line: str) -> None:
        log_lines.append(line)

    try:
        mdl_rel_fs = mdl.relative_to(models_dir)
    except Exception:
        mdl_rel_fs = Path(mdl.name)
    mdl_rel_s = mdl_rel_fs.as_posix()
    slug = safe_slug(mdl_rel_s)

    tmp_dir = tmp_root / f"{idx:05d}_{slug}"
    crowbar_log = logs_dir / "crowbar" / f"{idx:05d}_{slug}.log"

    status = "fail"
    message = ""
    rc = None
    last_lines = []
    error_lines = []
    qc_paths = []
    qc_chosen = None
    qc_chosen_rel = None
    modelname_raw = None
    model_rel = None
    model_rel_path = None
    src_dir = None

    backup_errors = []
    backup_phy_ok = False
    backup_full_ok = False
    phy_src = mdl.with_suffix(".phy")
    phy_backup = None
    skip_decompile = False
    skip_move = False

    try:
        model_rel_path = Path(*mdl_rel_s.split("/"))
        if not model_rel_path.name.lower().endswith(".mdl"):
            model_rel_path = model_rel_path.with_suffix(".mdl")
        src_dir = src_root / model_rel_path.parent / model_rel_path.stem

        phy_backup = backup_models_dir / model_rel_path.with_suffix(".phy")
        backup_phy_ok = backup_file(phy_src, phy_backup, backup_errors, args.dry_run)

        if args.backup_full:
            backup_full_ok = backup_full_binaries(
                mdl_path=mdl,
                model_rel_path=model_rel_path,
                backup_models_dir=backup_models_dir,
                errors=backup_errors,
                dry_run=args.dry_run,
            )

        if src_dir.exists() and not args.force:
            status = "skipped_exists"
            message = "src_exists"
            rc = 0
            skip_decompile = True
            skip_move = True

        if not skip_decompile:
            if tmp_dir.exists() and not args.dry_run:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            if not args.dry_run:
                tmp_dir.mkdir(parents=True, exist_ok=True)

            if args.dry_run:
                rc = 0
            else:
                rc, last_lines, error_lines = run_crowbar(crowbar_exe, mdl, tmp_dir, crowbar_log)

            if rc != 0:
                message = f"crowbar_rc={rc}"
                raise RuntimeError(message)

            qc_paths = sorted([p for p in tmp_dir.rglob("*.qc") if p.is_file()], key=lambda p: p.name.lower())
            if not qc_paths:
                message = "missing_qc"
                raise RuntimeError(message)

            for qc in qc_paths:
                raw = parse_modelname(qc)
                rel_norm, rel_err = normalize_modelname(raw)
                if rel_norm:
                    qc_chosen = qc
                    try:
                        qc_chosen_rel = qc.relative_to(tmp_dir)
                    except Exception:
                        qc_chosen_rel = None
                    modelname_raw = raw
                    model_rel = rel_norm
                    break
                if not message:
                    message = f"modelname_error={rel_err or 'unknown'}"

        if not skip_decompile and src_dir.exists():
            if args.force:
                if not args.dry_run:
                    shutil.rmtree(src_dir, ignore_errors=True)
            else:
                status = "skipped_exists"
                message = (message + "; " if message else "") + "src_exists"
                skip_move = True

        if not skip_decompile and not skip_move and not args.dry_run:
            src_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_dir), str(src_dir))

            if qc_chosen_rel is not None:
                qc_in_src = src_dir / qc_chosen_rel
                if qc_in_src.exists():
                    changed = rewrite_qc_modelname(qc_in_src, model_rel_path.as_posix())
                    if changed:
                        message = (message + "; " if message else "") + "qc_modelname_rewritten"
        if not skip_decompile and not skip_move:
            status = "ok"

    except Exception as e:
        if not message:
            message = str(e)
    finally:
        if not args.keep_tmp and not args.dry_run:
            try:
                if tmp_dir.exists():
                    shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    result = {
        "mdl_path": str(mdl),
        "mdl_rel_fs": mdl_rel_s,
        "status": status,
        "message": message,
        "returncode": rc,
        "crowbar_log": str(crowbar_log),
        "qc_paths": [str(p) for p in qc_paths],
        "qc_chosen": str(qc_chosen) if qc_chosen else None,
        "modelname_raw": modelname_raw,
        "model_rel": str(model_rel) if model_rel else None,
        "model_rel_fallback": str(model_rel_path.as_posix()) if model_rel_path else None,
        "src_dir": str(src_dir) if src_dir else None,
        "phy_src": str(phy_src) if phy_src else None,
        "phy_backup": str(phy_backup) if phy_backup else None,
        "backup_phy_ok": backup_phy_ok,
        "backup_full_ok": backup_full_ok if args.backup_full else None,
        "backup_errors": backup_errors,
        "last_lines": last_lines,
        "error_lines": error_lines,
    }

    log(f"Status: {status.upper()} | Log: {crowbar_log}")
    if message:
        log(f"Note: {message}")
    if not backup_phy_ok:
        log("[WARN] .phy backup not found or failed (see manifest).")

    return result, log_lines


def main():
    ap = argparse.ArgumentParser(
        description="Batch decompile .mdl using Crowbar CLI, organize per-model, and back up original .phy."
    )
    ap.add_argument("input", help="Addon folder OR models folder OR a single .mdl file")
    ap.add_argument("--out", default=None, help="Work output dir (default: ./work/<addon_name>)")
    ap.add_argument("--crowbar", default=str(DEFAULT_CROWBAR), help="Path to CrowbarCommandLineDecomp.exe")
    ap.add_argument("--force", action="store_true", help="Overwrite existing per-model src folders")
    ap.add_argument("--dry-run", action="store_true", help="Do not run Crowbar or write files")
    ap.add_argument("--keep-tmp", action="store_true", help="Keep temporary folders under <out>/_tmp")
    ap.add_argument("--backup-full", action="store_true", help="Also back up .mdl/.vvd and all .vtx variants")
    ap.add_argument("--include", action="append", default=[], help="Regex include filter (can repeat)")
    ap.add_argument("--exclude", action="append", default=[], help="Regex exclude filter (can repeat)")
    ap.add_argument("--limit", type=int, default=None, help="Max models to process (debug)")
    ap.add_argument("--jobs", type=int, default=1, help="Parallel Crowbar jobs (default: 1)")
    args = ap.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    crowbar_exe = Path(args.crowbar).expanduser().resolve()
    if not crowbar_exe.exists():
        print(f"[ERROR] Crowbar CLI not found: {crowbar_exe}")
        return 2

    try:
        models_dir, addon_name = pick_models_dir(input_path)
    except Exception as e:
        print(f"[ERROR] {e}")
        return 2

    out_dir = Path(args.out).expanduser().resolve() if args.out else (Path.cwd() / "work" / addon_name)
    src_root = out_dir / "src"
    backup_models_dir = out_dir / "original" / "models"
    logs_dir = out_dir / "logs"
    tmp_root = out_dir / "_tmp"

    if not args.dry_run:
        src_root.mkdir(parents=True, exist_ok=True)
        backup_models_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        tmp_root.mkdir(parents=True, exist_ok=True)

    mdls = [input_path] if input_path.is_file() else find_mdls(models_dir)

    filtered = []
    for mdl in mdls:
        rel = None
        try:
            rel = mdl.relative_to(models_dir)
        except Exception:
            rel = Path(mdl.name)
        rel_s = rel.as_posix()
        if args.include and not _match_any(args.include, rel_s):
            continue
        if args.exclude and _match_any(args.exclude, rel_s):
            continue
        filtered.append(mdl)
        if args.limit is not None and len(filtered) >= args.limit:
            break

    mdls = filtered

    print(f"Input: {input_path}")
    print(f"Models dir: {models_dir}")
    print(f"Crowbar: {crowbar_exe}")
    print(f"Out dir: {out_dir}")
    print(f"Found {len(mdls)} .mdl file(s) to decompile.")
    if args.jobs > 1:
        print(f"Jobs: {args.jobs}")

    start_all = time.monotonic()
    results = []
    top_errors = {}

    if args.jobs <= 1 or len(mdls) <= 1:
        for idx, mdl in enumerate(mdls, start=1):
            try:
                mdl_rel_fs = mdl.relative_to(models_dir)
            except Exception:
                mdl_rel_fs = Path(mdl.name)
            mdl_rel_s = mdl_rel_fs.as_posix()
            print(f"\n=== ({idx}/{len(mdls)}) MDL: {mdl_rel_s} ===")
            result, log_lines = process_one_mdl(
                idx,
                mdl,
                models_dir,
                src_root,
                backup_models_dir,
                logs_dir,
                tmp_root,
                crowbar_exe,
                args,
            )
            for line in log_lines:
                print(line)
            for line in result.get("error_lines", []) or []:
                top_errors[line] = top_errors.get(line, 0) + 1
            results.append(result)
    else:
        jobs = max(1, args.jobs)
        total = len(mdls)
        results_by_idx = {}
        done = 0
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            future_map = {
                pool.submit(
                    process_one_mdl,
                    idx,
                    mdl,
                    models_dir,
                    src_root,
                    backup_models_dir,
                    logs_dir,
                    tmp_root,
                    crowbar_exe,
                    args,
                ): idx
                for idx, mdl in enumerate(mdls, start=1)
            }
            for fut in as_completed(future_map):
                idx = future_map[fut]
                result, log_lines = fut.result()
                done += 1
                print(f"\n=== ({done}/{total}) MDL: {result['mdl_rel_fs']} ===")
                for line in log_lines:
                    print(line)
                for line in result.get("error_lines", []) or []:
                    top_errors[line] = top_errors.get(line, 0) + 1
                results_by_idx[idx] = result
        results = [results_by_idx[i] for i in sorted(results_by_idx)]

    duration_all = time.monotonic() - start_all
    ok_count = sum(1 for r in results if r["status"] == "ok")
    skipped_count = sum(1 for r in results if r["status"].startswith("skipped"))
    fail_count = len(results) - ok_count - skipped_count

    top_error_list = sorted(
        [{"line": k, "count": v} for k, v in top_errors.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    summary = {
        "input": str(input_path),
        "models_dir": str(models_dir),
        "crowbar": str(crowbar_exe),
        "out_dir": str(out_dir),
        "src_root": str(src_root),
        "backup_models_dir": str(backup_models_dir),
        "dry_run": bool(args.dry_run),
        "backup_full": bool(args.backup_full),
        "total": len(results),
        "ok": ok_count,
        "skipped": skipped_count,
        "fail": fail_count,
        "duration_sec": round(duration_all, 3),
        "top_errors": top_error_list,
        "results": results,
    }

    if not args.dry_run:
        logs_dir.mkdir(parents=True, exist_ok=True)
        summary_json = logs_dir / "decompile_manifest.json"
        summary_txt = logs_dir / "decompile_manifest.txt"
        summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8", errors="replace")

        lines = []
        lines.append("Decompile manifest")
        lines.append(f"Input: {summary['input']}")
        lines.append(f"Models dir: {summary['models_dir']}")
        lines.append(f"Out dir: {summary['out_dir']}")
        lines.append(f"Total: {summary['total']}  OK: {summary['ok']}  SKIP: {summary['skipped']}  FAIL: {summary['fail']}")
        lines.append(f"Duration sec: {summary['duration_sec']:.2f}")
        lines.append("")
        lines.append("Top errors:")
        if summary["top_errors"]:
            for item in summary["top_errors"]:
                lines.append(f"- ({item['count']}) {item['line']}")
        else:
            lines.append("- (none)")
        lines.append("")
        lines.append("Per MDL:")
        for item in results:
            line = f"- {item['status'].upper()}: {item['mdl_rel_fs']}"
            if item.get("model_rel"):
                line += f" -> {item['model_rel']}"
            if item.get("message"):
                line += f" | {item['message']}"
            lines.append(line)
        summary_txt.write_text("\n".join(lines), encoding="utf-8", errors="replace")

        print("\nDone.")
        print(f"Manifest: {summary_json}")
    else:
        print("\nDone (dry-run).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
