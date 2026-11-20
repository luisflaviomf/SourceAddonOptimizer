"""Addons with the alpha target 1.5 dB lower (no alpha noise), for the in-game comparison (review/claude lab).
Code: lab/code_anf15 = branch feature/alfa-sem-ruido + GMOT_ALPHA_OFFSET (lab only). Output lab/cb_anf15/<g>/.
python alpha_variant.py LVS,GLIDE,simfphys,m9k,arc9"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

CODE = Path(r"C:/GModLab/revisao/claude/code_anf15")
LAB = Path(r"C:/GModLab/revisao/claude")
VTFCMD = LAB / "tools" / "VTFEdit" / "VTFCmd.exe"


def worker(args, log, env=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8", **(env or {}))
    with open(log, "a", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run([sys.executable, str(CODE / "worker" / "worker_main.py"), *map(str, args)], cwd=str(CODE),
                            stdout=fh, stderr=subprocess.STDOUT, env=e).returncode
    assert rc == 0, args


for g in sys.argv[1].split(","):
    log = LAB / "logs" / f"alpha_variant_{g}.log"
    src = LAB / "src" / g / f"{g}_merged"
    out = LAB / "src" / g / f"{g}_merged_optimized"
    if out.exists():
        shutil.rmtree(out)
    worker(["modscreen", src, "--work", LAB / "work" / g / "anf15", "--suffix", "_optimized", "--profile", "stream",
            "--jobs", "0"], log)
    dst = LAB / "cb_anf15" / g / f"{g}_merged_optimized"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(out), str(dst))
    worker(["texscreen", dst, "--vtfcmd", VTFCMD, "--profile", "stream", "--jobs", "0",
            "--report", LAB / "out" / f"texscreen_cb_anf15_{g}.json"], log, {"GMOT_ALPHA_OFFSET": "1.5"})
    print(g, "done", flush=True)
