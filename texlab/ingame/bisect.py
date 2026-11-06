"""Find which textures cause a local in-game difference by swapping subsets for another variant."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from texlab.evaluate import _flip
from texlab.ingame.run_probe import run

LAB = Path(r"C:/GModLab/texlab/v1")
BG = np.array([46, 51, 56], np.float32) / 255.0


def local_error(run_name: str, capture: str) -> float:
    ref = np.asarray(Image.open(LAB / "ingame" / "bisect_original" / capture).convert("RGB"), np.float32) / 255.0
    img = np.asarray(Image.open(LAB / "ingame" / run_name / capture).convert("RGB"), np.float32) / 255.0
    mask = np.abs(ref - BG).sum(axis=2) > 2.0 / 255.0
    return _flip(ref, img, mask)[2]


def main(test_variant: str, good_variant: str, model: str, tag: str, capture: str, prefixes: list[str]) -> None:
    test_root = LAB / test_variant / "lvs_texlab_sample"
    good_root = LAB / good_variant / "lvs_texlab_sample"
    files = sorted({p.relative_to(test_root).as_posix() for pre in prefixes
                    for p in (test_root / "materials" / pre).glob("*.vtf")})
    models = [{"mdl": model, "tag": tag}]
    run(LAB / "original" / "lvs_texlab_sample", "bisect_original", models, LAB / "ingame")
    run(test_root, "bisect_test", models, LAB / "ingame")
    run(good_root, "bisect_good", models, LAB / "ingame")
    print(f"test {local_error('bisect_test', capture):.3f} good {local_error('bisect_good', capture):.3f}", flush=True)
    candidates = files
    step = 0
    while len(candidates) > 1:
        half = candidates[: len(candidates) // 2]
        work = LAB / "bisect" / f"s{step}"
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(test_root, work)
        for rel in half:
            shutil.copy2(good_root / rel, work / rel)
        name = f"bisect_s{step}"
        run(work, name, models, LAB / "ingame")
        err = local_error(name, capture)
        print(f"step {step}: swapped {len(half)} of {len(candidates)} -> local {err:.3f}", flush=True)
        candidates = candidates[len(candidates) // 2:] if err > THRESHOLD else half
        step += 1
    print("culprit:", candidates)


THRESHOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 0.25

if __name__ == "__main__":
    main("tela_v5", "baseline_2x8", "models/diggercars/caterham_620r/caterham.mdl", "caterham",
         "01_caterham_v6.png",
         ["models/diggercars/caterham_620r", "models/diggercars/shared"])
