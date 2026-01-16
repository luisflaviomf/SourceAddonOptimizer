from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path


def find_qcs(root: Path, include_opt: bool) -> list[Path]:
    qcs: list[Path] = []
    for p in root.rglob("*.qc"):
        name = p.name.lower()
        if not include_opt and name.endswith("_opt.qc"):
            continue
        if "output" in [part.lower() for part in p.parts]:
            continue
        qcs.append(p)
    return sorted(qcs)


def _auto_jobs() -> int:
    count = os.cpu_count() or 1
    jobs = max(1, count - 1)
    return min(jobs, 6)


def _reader(idx: int, proc: subprocess.Popen, done_cb):
    if proc.stdout is None:
        return
    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        if not line:
            continue
        if line.startswith("QC_DONE:"):
            rel = line.split(":", 1)[1].strip()
            done_cb(rel)
            continue
        if line.startswith("QC_SKIP:"):
            rel = line.split(":", 1)[1].strip()
            done_cb(rel)
            continue
        print(f"[B{idx}] {line}", flush=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("root", type=str, help="Root folder for QC search (recursive).")
    ap.add_argument("--blender", required=True, help="Path to blender.exe")
    ap.add_argument("--ratio", type=float, default=0.75, help="Decimate ratio (1.0 = no decimate)")
    ap.add_argument("--merge", type=float, default=0.0001, help="Merge by distance")
    ap.add_argument("--autosmooth", type=float, default=45.0, help="Auto smooth angle in degrees")
    ap.add_argument("--format", type=str, default="smd", choices=["smd", "dmx"], help="Export format")
    ap.add_argument(
        "--fix-physics",
        type=str,
        default="auto",
        choices=["auto", "force", "off"],
        help="Physics fix mode (auto|force|off).",
    )
    ap.add_argument("--include-opt", action="store_true", help="Also process *_OPT.qc files.")
    ap.add_argument("--jobs", type=int, default=0, help="Parallel Blender jobs (0 = auto).")
    ap.add_argument("--resume", action="store_true", help="Skip QCs already optimized.")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        print(f"[ERROR] ROOT not found: {root}", flush=True)
        return 2

    blender = Path(args.blender).expanduser().resolve()
    if not blender.exists():
        print(f"[ERROR] Blender not found: {blender}", flush=True)
        return 2

    optimize_script = Path(__file__).resolve().parent / "batch_optimize_qc.py"
    if not optimize_script.exists():
        print(f"[ERROR] Missing script: {optimize_script}", flush=True)
        return 2

    qcs = find_qcs(root, args.include_opt)
    total = len(qcs)
    print(f"Encontrados {total} QC(s) (busca recursiva).", flush=True)
    if total <= 0:
        print("[ERROR] No QC files found.", flush=True)
        return 2

    jobs = args.jobs
    if jobs < 0:
        print("[ERROR] jobs must be >= 0", flush=True)
        return 2
    if jobs == 0:
        jobs = _auto_jobs()
    jobs = min(jobs, total)
    if jobs <= 0:
        print("[ERROR] No jobs to run.", flush=True)
        return 2

    print(f"[INFO] Jobs: {jobs}", flush=True)

    done = 0
    lock = threading.Lock()

    def on_done(rel: str):
        nonlocal done
        with lock:
            done += 1
            idx = done
        print(f"=== ({idx}/{total}) QC: {rel} ===", flush=True)

    procs: list[subprocess.Popen] = []
    threads: list[threading.Thread] = []

    for idx in range(jobs):
        cmd = [
            str(blender),
            "--background",
            "--python",
            str(optimize_script),
            "--",
            str(root),
            "--ratio",
            str(args.ratio),
            "--merge",
            str(args.merge),
            "--autosmooth",
            str(args.autosmooth),
            "--format",
            str(args.format),
            "--fix-physics",
            str(args.fix_physics),
        ]
        if args.include_opt:
            cmd.append("--include-opt")
        if args.resume:
            cmd.append("--resume")
        if jobs > 1:
            cmd.extend(["--shard-count", str(jobs), "--shard-index", str(idx)])

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        t = threading.Thread(target=_reader, args=(idx, proc, on_done), daemon=True)
        t.start()
        procs.append(proc)
        threads.append(t)

    exit_code = 0
    try:
        for proc in procs:
            rc = proc.wait()
            if rc != 0 and exit_code == 0:
                exit_code = rc
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted. Terminating Blender jobs...", flush=True)
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass
        return 130
    finally:
        for t in threads:
            t.join(timeout=2.0)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
