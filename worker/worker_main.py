import argparse
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

# Ensure repo root is on sys.path when running as a script.
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import batch_decompile_organize
import batch_build_map_bsp
import batch_merge_addons
import batch_scan_map_bsp
import batch_unpack_addons
import build_optimized_addon


def _ensure_crowbar_env():
    if os.environ.get("CROWBAR_EXE"):
        return
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        crowbar = base / "CrowbarCommandLineDecomp.exe"
        if crowbar.exists():
            os.environ["CROWBAR_EXE"] = str(crowbar)
            return

    # Dev fallback: use repo root if Crowbar is present.
    base = Path(__file__).resolve().parents[1]
    crowbar = base / "CrowbarCommandLineDecomp.exe"
    if crowbar.exists():
        os.environ["CROWBAR_EXE"] = str(crowbar)


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


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


def _run_stream(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> int:
    print(f"+ {_quote_cmd(cmd)}")
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=full_env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line.rstrip("\n"))
    return proc.wait()

def _run_inprocess(func, argv: list[str]) -> int:
    old_argv = sys.argv[:]
    sys.argv = argv[:]
    try:
        return int(func() or 0)
    finally:
        sys.argv = old_argv


def _find_qc_in_src(src_dir: Path) -> Path | None:
    qcs = [p for p in src_dir.rglob("*.qc") if p.is_file() and not p.name.lower().endswith("_opt.qc")]
    if not qcs:
        return None
    qcs.sort(key=lambda p: p.name.lower())
    return qcs[0]


def _strip_comments(line: str) -> str:
    if "//" in line:
        return line.split("//", 1)[0]
    return line


def _extract_mesh_token_from_line(line: str) -> str | None:
    toks = line.replace("\t", " ").split()
    for t in reversed(toks):
        tt = t.strip().strip('"').strip()
        if tt.lower().endswith((".smd", ".dmx")):
            return tt
    for t in reversed(toks):
        tt = t.strip().strip('"').strip()
        low = tt.lower()
        if not tt:
            continue
        if low in ("studio", "blank"):
            continue
        if low.startswith("$"):
            continue
        return tt
    return None


def _extract_default_mesh_tokens_from_qc(qc_text: str) -> list[str]:
    refs = []
    in_bodygroup = False
    bodygroup_refs: list[str] = []
    brace_depth = 0

    for raw in qc_text.splitlines():
        line = _strip_comments(raw).strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("$sequence"):
            continue
        if low.startswith("$bodygroup"):
            in_bodygroup = True
            bodygroup_refs = []
            brace_depth = line.count("{") - line.count("}")
            continue
        if in_bodygroup:
            brace_depth += line.count("{") - line.count("}")
            if "studio" in low:
                token = _extract_mesh_token_from_line(line)
                if token:
                    bodygroup_refs.append(token)
            if brace_depth <= 0 or "}" in line:
                if bodygroup_refs:
                    refs.append(bodygroup_refs[0])
                in_bodygroup = False
                bodygroup_refs = []
                brace_depth = 0
            continue
        if low.startswith("$body") or low.startswith("$model"):
            token = _extract_mesh_token_from_line(line)
            if token:
                refs.append(token)
            continue

    seen = set()
    out = []
    for r in refs:
        key = r.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _normalize_qc_path_token(path_token: str) -> str:
    return path_token.replace("\\", "/").lstrip("./")


def _build_output_name(src: Path, out_fmt: str) -> str:
    base = src.stem + "_opt"
    ext = ".dmx" if out_fmt.lower() == "dmx" else ".smd"
    return base + ext

def _resolve_car_dir(addon_path: Path, car_arg: str | None) -> Path | None:
    if not car_arg:
        return None
    p = Path(car_arg).expanduser()
    if p.exists() and p.is_dir():
        return p.resolve()
    models_dir = addon_path / "models"
    cand = (models_dir / car_arg).resolve()
    if cand.exists() and cand.is_dir():
        return cand
    return None


def _list_mdls_in_dir(car_dir: Path) -> tuple[list[Path], bool]:
    mdls = sorted([p for p in car_dir.glob("*.mdl") if p.is_file()], key=lambda p: str(p).lower())
    if mdls:
        return mdls, False
    mdls = sorted([p for p in car_dir.rglob("*.mdl") if p.is_file()], key=lambda p: str(p).lower())
    return mdls, True


def _choose_base_mdl(car_dir: Path, mdls: list[Path], recursive: bool) -> tuple[Path | None, str]:
    if not mdls:
        return None, "no_mdls"

    names = {p.name.lower(): p for p in mdls}
    folder_name = car_dir.name.lower()
    candidates = [
        "base.mdl",
        "body.mdl",
        "chassis.mdl",
        "car.mdl",
        f"{folder_name}.mdl",
        "model.mdl",
    ]
    for name in candidates:
        if name in names:
            reason = f"name:{name}"
            if recursive:
                reason += " (recursive)"
            return names[name], reason

    skip_tokens = ["wheel", "wheelfr", "wheelbk", "rim", "tire", "tyre", "physics", "phys"]
    filtered = [p for p in mdls if not any(t in p.name.lower() for t in skip_tokens)]
    pool = filtered if filtered else mdls
    best = max(pool, key=lambda p: p.stat().st_size, default=None)
    reason = "largest"
    if filtered:
        reason = "largest (filtered)"
    if recursive:
        reason += " (recursive)"
    return best, reason


def _name_tokens(name: str) -> list[str]:
    return re.split(r"[^a-z0-9]+", name.lower())


def _should_skip_mesh(path: Path) -> bool:
    tokens = _name_tokens(path.stem)
    skip_tokens = {"wheel", "wheelfr", "wheelbk", "rim", "tire", "tyre", "phys", "physics", "shadow"}
    if any(t in skip_tokens for t in tokens):
        return True
    return False


def _resolve_mesh_token(qc_dir: Path, token: str) -> Path | None:
    rel_norm = _normalize_qc_path_token(token)
    cand = (qc_dir / rel_norm)
    if cand.suffix:
        if cand.exists():
            return cand.resolve()
        return None
    for ext in (".smd", ".dmx"):
        with_ext = cand.with_suffix(ext)
        if with_ext.exists():
            return with_ext.resolve()
    return None


def _select_mesh_files(qc_path: Path) -> list[Path]:
    qc_text = qc_path.read_text(encoding="utf-8", errors="ignore")
    tokens = _extract_default_mesh_tokens_from_qc(qc_text)
    meshes: list[Path] = []
    seen = set()
    for token in tokens:
        resolved = _resolve_mesh_token(qc_path.parent, token)
        if not resolved:
            continue
        if _should_skip_mesh(resolved):
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        meshes.append(resolved)
    if meshes:
        return meshes

    # Fallback: largest mesh-like file in folder (ignore physics + wheels).
    candidates = []
    for p in qc_path.parent.rglob("*.*"):
        if p.suffix.lower() not in (".smd", ".dmx"):
            continue
        if _should_skip_mesh(p):
            continue
        candidates.append(p)
    if not candidates:
        return []
    best = max(candidates, key=lambda p: p.stat().st_size)
    return [best.resolve()]


def _run_preview(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Preview pipeline for a single model.")
    ap.add_argument("--addon", required=True, help="Path to addon folder")
    ap.add_argument("--car-dir", default=None, help="Path to car folder under models (optional)")
    ap.add_argument("--mdl", default=None, help="Path to a .mdl file (override)")
    ap.add_argument("--blender", required=True, help="Path to blender.exe")
    ap.add_argument("--ratio", type=float, default=0.50)
    ap.add_argument("--merge", type=float, default=0.0)
    ap.add_argument("--autosmooth", type=float, default=45.0)
    ap.add_argument("--format", default="smd", choices=["smd", "dmx"])
    args = ap.parse_args(argv)

    _ensure_crowbar_env()
    repo_root = _runtime_root()
    decompile_script = (repo_root / "batch_decompile_organize.py").resolve()
    optimize_script = (repo_root / "batch_optimize_qc.py").resolve()
    render_script = (repo_root / "render_previews.py").resolve()

    for p in (decompile_script, optimize_script, render_script):
        if not p.exists():
            print(f"[ERROR] Missing required script: {p}")
            return 2

    addon_path = Path(args.addon).expanduser().resolve()
    if not addon_path.exists() or not addon_path.is_dir():
        print(f"[ERROR] Addon folder not found: {addon_path}")
        return 2

    car_dir = _resolve_car_dir(addon_path, args.car_dir) if args.car_dir else None
    mdl_path = None
    reason = ""
    if args.mdl:
        mdl_path = Path(args.mdl).expanduser().resolve()
        if not mdl_path.exists():
            print(f"[ERROR] MDL not found: {mdl_path}")
            return 2
        reason = "override"
    elif car_dir:
        mdls, recursive = _list_mdls_in_dir(car_dir)
        mdl_path, reason = _choose_base_mdl(car_dir, mdls, recursive)
        if not mdl_path:
            print(f"[ERROR] No .mdl found in car dir: {car_dir}")
            return 2
    else:
        print("[ERROR] You must provide --mdl or --car-dir.")
        return 2

    blender_exe = Path(args.blender).expanduser().resolve()
    if not blender_exe.exists():
        print(f"[ERROR] Blender not found: {blender_exe}")
        return 2

    addon_name = addon_path.name
    work_root = (Path.cwd() if getattr(sys, "frozen", False) else repo_root) / "work"
    preview_root = work_root / f"{addon_name}_preview_tests"
    preview_dir = preview_root / _ts()

    print(f"Addon:  {addon_path}")
    if car_dir:
        print(f"Car:    {car_dir}")
    print(f"Model:  {mdl_path} ({reason})")
    print(f"Work:   {preview_dir}")

    preview_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: decompile only this model.
    print("\n== Preview Step 1/3: Decompile ==")
    models_dir = addon_path / "models"
    if models_dir.exists():
        try:
            mdl_rel = mdl_path.relative_to(models_dir).as_posix()
        except Exception:
            mdl_rel = mdl_path.name
    else:
        mdl_rel = mdl_path.name
    include_pattern = f"^{re.escape(mdl_rel)}$"
    rc = _run_inprocess(
        batch_decompile_organize.main,
        [
            str(decompile_script),
            str(addon_path),
            "--out",
            str(preview_dir),
            "--force",
            "--include",
            include_pattern,
            "--limit",
            "1",
        ],
    )
    if rc != 0:
        print(f"[ERROR] Decompile failed (rc={rc})")
        return rc

    manifest = preview_dir / "logs" / "decompile_manifest.json"
    if not manifest.exists():
        print(f"[ERROR] Missing decompile manifest: {manifest}")
        return 2
    data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    results = data.get("results", [])
    if not results:
        print(f"[ERROR] Decompile produced no results (see {manifest})")
        return 2
    result = results[0]
    if result.get("status") != "ok":
        print(f"[ERROR] Decompile status not ok: {result.get('status')} {result.get('message')}")
        return 2
    src_dir = Path(result.get("src_dir") or "")
    if not src_dir.exists():
        print(f"[ERROR] Missing src dir: {src_dir}")
        return 2

    # Step 2: optimize this QC.
    print("\n== Preview Step 2/3: Optimize (Blender) ==")
    rc = _run_stream(
        [
            str(blender_exe),
            "--background",
            "--python",
            str(optimize_script),
            "--",
            str(src_dir),
            "--ratio",
            f"{args.ratio:.4f}",
            "--merge",
            f"{args.merge:.6f}",
            "--autosmooth",
            f"{args.autosmooth:.1f}",
            "--format",
            args.format,
        ],
        cwd=repo_root,
    )
    if rc != 0:
        print(f"[ERROR] Optimize failed (rc={rc})")
        return rc

    # Step 3: render previews.
    print("\n== Preview Step 3/3: Render previews ==")
    qc_path = _find_qc_in_src(src_dir)
    if not qc_path:
        print(f"[ERROR] Could not find QC under: {src_dir}")
        return 2

    src_meshes = _select_mesh_files(qc_path)
    if not src_meshes:
        print(f"[ERROR] Could not find mesh file from QC: {qc_path}")
        return 2

    before_list: list[Path] = []
    after_list: list[Path] = []
    for src_mesh in src_meshes:
        out_mesh = qc_path.parent / "output" / _build_output_name(src_mesh, args.format)
        if not out_mesh.exists():
            print(f"[WARN] Optimized mesh not found, skipping: {out_mesh}")
            continue
        before_list.append(src_mesh)
        after_list.append(out_mesh)
    if not before_list:
        print("[ERROR] No optimized meshes found for preview.")
        return 2
    print(f"Preview meshes: {len(before_list)}")
    for mesh in before_list:
        print(f"  - {mesh.name}")

    render_out = preview_dir / "renders"
    render_cmd = [
        str(blender_exe),
        "--background",
        "--python",
        str(render_script),
        "--",
    ]
    for mesh in before_list:
        render_cmd.extend(["--before", str(mesh)])
    for mesh in after_list:
        render_cmd.extend(["--after", str(mesh)])
    render_cmd.extend(["--out", str(render_out), "--size", "1024"])

    rc = _run_stream(render_cmd, cwd=repo_root)
    if rc != 0:
        print(f"[ERROR] Render failed (rc={rc})")
        return rc

    summary_path = render_out / "preview_summary.json"
    if not summary_path.exists():
        print(f"[ERROR] Preview summary not found: {summary_path}")
        return 2
    print(f"PREVIEW_SUMMARY: {summary_path}")
    print(f"PREVIEW_OUTPUT_DIR: {render_out}")
    return 0


def main(argv: list[str]) -> int:
    _ensure_crowbar_env()
    if argv and argv[0] == "preview":
        return _run_preview(argv[1:])
    if argv and argv[0] == "unpack":
        return batch_unpack_addons.main(argv[1:])
    if argv and argv[0] == "addonmerge":
        return batch_merge_addons.main(argv[1:])
    if argv and argv[0] == "mapscan":
        return batch_scan_map_bsp.main(argv[1:])
    if argv and argv[0] == "mapbuild":
        return batch_build_map_bsp.main(argv[1:])
    return build_optimized_addon.main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()

        work_dir = None
        argv = sys.argv[1:]
        try:
            if "--work" in argv:
                i = argv.index("--work")
                if i + 1 < len(argv):
                    work_dir = Path(argv[i + 1]).expanduser().resolve()
        except Exception:
            work_dir = None

        crash_log_path = None
        try:
            if work_dir:
                crash_dir = work_dir / "logs"
                crash_dir.mkdir(parents=True, exist_ok=True)
                crash_log_path = crash_dir / f"worker_crash_{_ts()}.log"
                crash_log_path.write_text(tb, encoding="utf-8", errors="replace")
        except Exception:
            crash_log_path = None

        if crash_log_path:
            print(f"[ERROR] Unhandled exception. Crash log: {crash_log_path}")
        else:
            print("[ERROR] Unhandled exception. (Failed to write crash log)")
        print(tb)
        raise SystemExit(1)
