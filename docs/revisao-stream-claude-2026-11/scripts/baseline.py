"""Baseline of the assistant's Custo-benefício (texscreen stream) and Máximo (stream_max) on the merged bench,
through the worker modules of review/claude-stream (same code as publish/2026-03 f507f07).

Per group: modscreen --profile stream on lab/src/<g>/<g>_merged (alone in its folder), then the output is
copied to lab/cb/<g>/ and lab/max/<g>/ (each alone, so no neighbour changes the analysis), texscreen and
pnglossless run on each. Sounds are not run (C# pipeline; taken from baseline_mcb14.json for the inventory).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
LAB = Path(r"C:/GModLab/revisao/claude")
VTFCMD = LAB / "tools" / "VTFEdit" / "VTFCmd.exe"
GROUPS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]
PY = sys.executable
TIMES = LAB / "out" / "baseline_times.json"


def run(args, log):
    t0 = time.time()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with open(log, "a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n### {' '.join(map(str, args))}\n")
        fh.flush()
        rc = subprocess.run([PY, str(REPO / "worker" / "worker_main.py"), *map(str, args)], cwd=str(REPO),
                            stdout=fh, stderr=subprocess.STDOUT, env=env).returncode
    if rc != 0:
        raise SystemExit(f"failed rc={rc}: {args} (see {log})")
    return round(time.time() - t0, 1)


def main():
    times = json.loads(TIMES.read_text()) if TIMES.exists() else {}
    for g in GROUPS:
        src = LAB / "src" / g / f"{g}_merged"
        out = LAB / "src" / g / f"{g}_merged_optimized"
        log = LAB / "logs" / f"baseline_{g}.log"
        row = times.setdefault(g, {})
        if out.exists():
            shutil.rmtree(out)
        row["models"] = run(["modscreen", src, "--work", LAB / "work" / g / "modscreen", "--suffix", "_optimized",
                             "--profile", "stream", "--jobs", "0"], log)
        for tag, prof in (("cb", "stream"), ("max", "stream_max")):
            dst = LAB / tag / g / f"{g}_merged_optimized"
            if dst.exists():
                shutil.rmtree(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(out, dst)
            row[f"tex_{tag}"] = run(["texscreen", dst, "--vtfcmd", VTFCMD, "--profile", prof, "--jobs", "0",
                                     "--report", LAB / "out" / f"texscreen_{tag}_{g}.json"], log)
            row[f"png_{tag}"] = run(["pnglossless", dst, "--work", LAB / "work" / g / f"png_{tag}",
                                     "--report", LAB / "out" / f"png_{tag}_{g}.json"], log)
            TIMES.write_text(json.dumps(times, indent=1))
        shutil.copy2(LAB / "work" / g / "modscreen" / "logs" / "modscreen_summary.json",
                     LAB / "out" / f"modscreen_{g}.json")
        shutil.rmtree(out)
        print(g, row, flush=True)
    print("baseline done", flush=True)


if __name__ == "__main__":
    main()
