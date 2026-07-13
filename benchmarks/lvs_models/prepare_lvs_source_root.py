from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from maximum_optimizer.benchmarking import CorpusError
from maximum_optimizer.lvs_source_prep import (
    load_lvs_source_manifest,
    prepare_lvs_source_root,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Prepare the exact canonical LVS 5-calibration + 5-holdout source root "
            "from strict-control workspaces without running optimization."
        )
    )
    result.add_argument(
        "--corpus",
        type=Path,
        default=_REPO_ROOT / "benchmarks" / "lvs_models" / "corpus.json",
    )
    result.add_argument(
        "--control-root",
        type=Path,
        default=_REPO_ROOT / ".superpowers" / "lvs-task2-control-final",
    )
    result.add_argument("--output", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = load_lvs_source_manifest(args.corpus)
        prepared = prepare_lvs_source_root(
            manifest=manifest,
            control_root=args.control_root,
            output_root=args.output,
        )
    except (CorpusError, OSError, TypeError, ValueError) as exc:
        print(f"[ERROR] LVS source preparation failed: {exc}", file=sys.stderr)
        return 2
    summary = {
        "schema_version": 1,
        "kind": "lvs-source-root-preparation-v1",
        "source_root": str(prepared.source_root),
        "calibration_family_ids": list(prepared.calibration_family_ids),
        "holdout_family_ids": list(prepared.holdout_family_ids),
        "file_count": prepared.file_count,
        "total_bytes": prepared.total_bytes,
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
