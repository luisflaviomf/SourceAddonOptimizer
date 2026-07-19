from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil


def _safe_source(source_root: Path, source_identity: str) -> tuple[Path, str]:
    if type(source_identity) is not str:
        raise ValueError("source identity is invalid")
    identity = source_identity.replace("\\", "/")
    posix = PurePosixPath(identity)
    windows = PureWindowsPath(identity)
    if (
        not identity
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or any(part in {"", "."} for part in posix.parts)
        or '"' in identity
        or "\n" in identity
        or "\r" in identity
    ):
        raise ValueError("source identity is unsafe")
    root = source_root.resolve(strict=True)
    source = root.joinpath(*posix.parts).resolve(strict=True)
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise ValueError("source identity escapes source root") from exc
    if not source.is_file() or source.suffix.casefold() != ".smd":
        raise ValueError("source identity is not an SMD file")
    return source, posix.as_posix()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def prepare_workspace(
    *, source_root: Path, source_identity: str, ratio: float,
    candidate_id: str, strategy: str, out: Path,
    target_error: float | None = None,
) -> dict[str, object]:
    source, identity = _safe_source(source_root, source_identity)
    if type(ratio) is not float or not 0.0 < ratio < 1.0:
        raise ValueError("ratio must be a float between zero and one")
    if (
        type(candidate_id) is not str
        or not candidate_id.strip()
        or any(character in candidate_id for character in "\r\n")
    ):
        raise ValueError("candidate id is invalid")
    if strategy not in {
        "blender-adaptive-v1",
        "blender-importance-map-v1",
        "meshopt-direct-position-v1",
    }:
        raise ValueError("candidate strategy is unsupported")
    direct = strategy == "meshopt-direct-position-v1"
    if target_error is None:
        target_error = 0.01 if direct else 0.0
    if (
        type(target_error) is not float
        or not 0.0 <= target_error <= 1.0
        or (not direct and target_error != 0.0)
    ):
        raise ValueError("target error is invalid for candidate strategy")
    out = out.absolute()
    if out.exists():
        raise ValueError("candidate workspace output already exists")

    destination = out.joinpath(*PurePosixPath(identity).parts)
    destination.parent.mkdir(parents=True)
    shutil.copy2(source, destination)
    qc = out / "calibration.qc"
    qc.write_text(
        f'$body "maximum_calibration" "{identity}"\n',
        encoding="utf-8",
        newline="\n",
    )
    candidate = {
        "candidate_id": candidate_id,
        "engine": "meshoptimizer" if direct else "blender",
        "ratio": ratio,
        "target_error": target_error,
        "update_vertices": not direct,
        "region_overrides": [],
        "strategy": strategy,
        "transfer": "direct-v1" if direct else "blender-native-v1",
    }
    if direct:
        candidate["direct_degenerate_prefilter"] = "direct-degenerate-prefilter-v1"
    _write_json(out / "candidate.json", candidate)
    summary = {
        "schema": 1,
        "kind": "isolated-region-candidate-workspace-v1",
        "source_root": str(source_root.resolve(strict=True)),
        "source_identity": identity,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_bytes": source.stat().st_size,
        "ratio": ratio,
        "candidate_id": candidate_id,
        "strategy": strategy,
        "target_error": target_error,
    }
    _write_json(out / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-identity", required=True)
    parser.add_argument("--ratio", type=float, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--target-error", type=float)
    parser.add_argument(
        "--strategy",
        choices=(
            "blender-adaptive-v1",
            "blender-importance-map-v1",
            "meshopt-direct-position-v1",
        ),
        default="blender-adaptive-v1",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = prepare_workspace(
        source_root=args.source_root,
        source_identity=args.source_identity,
        ratio=args.ratio,
        candidate_id=args.candidate_id,
        strategy=args.strategy,
        out=args.out,
        target_error=args.target_error,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
