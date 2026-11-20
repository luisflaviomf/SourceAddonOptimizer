"""Texture stage time, old code (lab/code = f507f07, GMOT_DUMP unset) against new code (the worktree), back to
back on the same models output, twice each in alternating order. python time_anf.py m9k,LVS"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LAB = Path(r"C:/GModLab/revisao/claude")
CODES = {"old": Path(r"C:/GModLab/revisao/claude/code"),
         "new": Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")}
VTFCMD = LAB / "tools" / "VTFEdit" / "VTFCmd.exe"


def worker(code, args, log):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("GMOT_DUMP", None)
    t0 = time.time()
    with open(log, "a", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run([sys.executable, str(code / "worker" / "worker_main.py"), *map(str, args)], cwd=str(code),
                            stdout=fh, stderr=subprocess.STDOUT, env=env).returncode
    assert rc == 0, args
    return time.time() - t0


def main():
    res = {}
    for g in sys.argv[1].split(","):
        src = LAB / "src" / g / f"{g}_merged"
        out = LAB / "src" / g / f"{g}_merged_optimized"
        if out.exists():
            shutil.rmtree(out)
        log = LAB / "logs" / f"time_anf_{g}.log"
        worker(CODES["new"], ["modscreen", src, "--work", LAB / "work" / g / "time_anf", "--suffix", "_optimized",
                              "--profile", "stream", "--jobs", "0"], log)
        models = LAB / "timing" / g / "models_only"
        if models.parent.exists():
            shutil.rmtree(models.parent)
        models.parent.mkdir(parents=True)
        shutil.move(str(out), str(models))
        for run, name in enumerate(["old", "new", "new", "old"]):
            dst = LAB / "timing" / g / f"run{run}_{name}" / f"{g}_merged_optimized"
            shutil.copytree(models, dst)
            t = worker(CODES[name], ["texscreen", dst, "--vtfcmd", VTFCMD, "--profile", "stream", "--jobs", "0",
                                     "--report", LAB / "out" / f"time_anf_{g}_{run}_{name}.json"], log)
            res.setdefault(f"{g}/{name}", []).append(round(t, 1))
            print(g, run, name, round(t, 1), flush=True)
            shutil.rmtree(dst.parent)
        shutil.rmtree(models.parent)
    (LAB / "out" / "time_anf.json").write_text(json.dumps(res, indent=1))
    print(res)


if __name__ == "__main__":
    main()
