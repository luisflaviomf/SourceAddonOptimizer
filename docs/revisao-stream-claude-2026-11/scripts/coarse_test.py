"""Coarse stand-in mesh smaller than 2%: models stage of the whole bench through the lab overlay with
GMO_COARSE=<ratio>, models bytes measured (textures not run: they do not depend on the coarse mesh except
through texlab's density, which reads the stream geometry). python coarse_test.py 0.01,0.005"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CODE = Path(r"C:/GModLab/revisao/claude/code")
LAB = Path(r"C:/GModLab/revisao/claude")


def size(root, pred):
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and pred(p.relative_to(root).as_posix().lower()))


def main():
    out = {}
    for ratio in sys.argv[1].split(","):
        for g in ["m9k", "GLIDE", "LVS", "simfphys", "arc9"]:
            src = LAB / "src" / g / f"{g}_merged"
            dst = LAB / "src" / g / f"{g}_merged_optimized"
            if dst.exists():
                shutil.rmtree(dst)
            env = dict(os.environ, GMO_COARSE=ratio, PYTHONIOENCODING="utf-8")
            with open(LAB / "logs" / f"coarse_{ratio}_{g}.log", "w", encoding="utf-8", errors="replace") as fh:
                rc = subprocess.run([sys.executable, str(CODE / "worker" / "worker_main.py"), "modscreen", str(src), "--work",
                                     str(LAB / "work" / g / f"coarse_{ratio}"), "--suffix", "_optimized", "--profile", "stream",
                                     "--jobs", "0"], cwd=str(CODE), stdout=fh, stderr=subprocess.STDOUT, env=env).returncode
            assert rc == 0, (ratio, g)
            summ = json.loads((LAB / "work" / g / f"coarse_{ratio}" / "logs" / "modscreen_summary.json").read_text())
            streamed = {Path(r["model"]).relative_to(dst).as_posix().lower()[:-4] for r in summ["rows"] if r.get("stream")}

            def is_coarse(rel):
                stem = rel[:-len(".dx90.vtx")] if rel.endswith(".dx90.vtx") else rel.rsplit(".", 1)[0]
                return stem in streamed and (rel.endswith(".vvd") or rel.endswith(".vtx"))
            row = {"models": size(dst, lambda r: r.startswith("models/") or r.startswith("data_static/gmo/")),
                   "coarse": size(dst, is_coarse), "stream": summ["stream_bytes"], "streamed": summ["streamed"]}
            out[f"{ratio}/{g}"] = row
            print(ratio, g, {k: (round(v / 2**20, 2) if k != "streamed" else v) for k, v in row.items()}, flush=True)
            shutil.rmtree(dst)
        (LAB / "out" / "coarse_test.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
