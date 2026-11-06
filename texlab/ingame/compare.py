"""Compare in-game probe captures of variants against the original (FLIP mean + worst local window)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from texlab.evaluate import _flip

BG = np.array([46, 51, 56], np.float32) / 255.0


def compare(root: Path, original: str, variants: list[str]) -> dict:
    root = Path(root)
    files = sorted(p.name for p in (root / original).glob("*.png"))
    out = {v: [] for v in variants}
    for name in files:
        ref = np.asarray(Image.open(root / original / name).convert("RGB"), np.float32) / 255.0
        mask = np.abs(ref - BG).sum(axis=2) > 2.0 / 255.0
        if mask.sum() < 2000:
            continue
        for v in variants:
            p = root / v / name
            if not p.exists():
                continue
            img = np.asarray(Image.open(p).convert("RGB"), np.float32) / 255.0
            mean, p99, local = _flip(ref, img, mask)
            out[v].append({"capture": name, "flip_mean": mean, "flip_local": local})
    summary = {}
    for v, rows in out.items():
        if rows:
            summary[v] = {"captures": len(rows),
                          "flip_mean": float(np.mean([r["flip_mean"] for r in rows])),
                          "flip_local_avg": float(np.mean([r["flip_local"] for r in rows])),
                          "flip_local_worst": float(max(r["flip_local"] for r in rows)),
                          "worst_capture": max(rows, key=lambda r: r["flip_local"])["capture"]}
    return {"summary": summary, "rows": out}


if __name__ == "__main__":
    r = compare(Path(sys.argv[1]), sys.argv[2], sys.argv[3:])
    Path(sys.argv[1], "compare.json").write_text(json.dumps(r, indent=1))
    print(json.dumps(r["summary"], indent=1))
