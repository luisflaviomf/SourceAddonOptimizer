"""Texture stream inputs of the whole bench, for offline encoder experiments (review/claude lab).

Per group: modscreen --profile stream (product code) on lab/src/<g>/<g>_merged, the output moved to
lab/dump/<g>/<g>_merged_optimized (alone), then texscreen --profile stream through the LAB overlay
(lab/code: texture_stream._encode_one also saves the encoder input when GMOT_DUMP is set) -> lab/dumpimg/<g>/
<key>.png + .json. The blobs written there must equal the baseline (lab/cb): checked by compare().
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"C:/Users/luisf/Music/teste/.claude/worktrees/review-claude-stream-888116")
CODE = Path(r"C:/GModLab/revisao/claude/code")
LAB = Path(r"C:/GModLab/revisao/claude")
VTFCMD = LAB / "tools" / "VTFEdit" / "VTFCmd.exe"
GROUPS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]


def run(root, args, log, env=None):
    e = dict(os.environ, PYTHONIOENCODING="utf-8", **(env or {}))
    with open(log, "a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"\n### {' '.join(map(str, args))}\n")
        fh.flush()
        rc = subprocess.run([sys.executable, str(root / "worker" / "worker_main.py"), *map(str, args)], cwd=str(root),
                            stdout=fh, stderr=subprocess.STDOUT, env=e).returncode
    if rc:
        raise SystemExit(f"rc={rc} {args}")


def tree_hash(root):
    h = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h[p.relative_to(root).as_posix()] = hashlib.sha1(p.read_bytes()).hexdigest()
    return h


def main():
    for g in GROUPS:
        log = LAB / "logs" / f"dump_{g}.log"
        src = LAB / "src" / g / f"{g}_merged"
        out = LAB / "src" / g / f"{g}_merged_optimized"
        if out.exists():
            shutil.rmtree(out)
        t0 = time.time()
        run(REPO, ["modscreen", src, "--work", LAB / "work" / g / "modscreen_dump", "--suffix", "_optimized",
                   "--profile", "stream", "--jobs", "0"], log)
        dst = LAB / "dump" / g / f"{g}_merged_optimized"
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out), str(dst))
        run(CODE, ["texscreen", dst, "--vtfcmd", VTFCMD, "--profile", "stream", "--jobs", "0",
                   "--report", LAB / "out" / f"texscreen_dump_{g}.json"], log, {"GMOT_DUMP": str(LAB / "dumpimg" / g)})
        a = tree_hash(dst)
        b = tree_hash(LAB / "cb" / g / f"{g}_merged_optimized")
        png = {k for k in set(a) | set(b) if k.lower().endswith(".png")}   # cb also ran pnglossless
        diff = sorted(k for k in (set(a) | set(b)) - png if a.get(k) != b.get(k))
        print(f"{g}: {time.time() - t0:.0f} s, files differing from cb (PNG aside): {len(diff)} {diff[:5]}", flush=True)
    print("dump done", flush=True)


if __name__ == "__main__":
    main()
