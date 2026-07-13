from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Literal, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maximum_optimizer.remapped_topology import (
    MAX_TEXT_BYTES,
    RemappedTopologyProof,
    remapped_topology_proof_payload,
    validate_remapped_topology_smd,
)


_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _seal(payload: dict[str, object]) -> str:
    return _hash(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    )


@dataclass(frozen=True)
class SmokeCase:
    name: str
    requested_ratio: float
    source: Path
    output: Path

    def __post_init__(self) -> None:
        if type(self.name) is not str or not _NAME.fullmatch(self.name):
            raise ValueError("remapped smoke case name is unsafe")
        if (
            type(self.requested_ratio) not in (int, float)
            or isinstance(self.requested_ratio, bool)
            or not math.isfinite(float(self.requested_ratio))
            or not 0.0 < float(self.requested_ratio) <= 1.0
        ):
            raise ValueError("remapped smoke ratio is invalid")
        object.__setattr__(self, "requested_ratio", float(self.requested_ratio))
        for name in ("source", "output"):
            path = Path(getattr(self, name)).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f"remapped smoke {name} is not a file")
            object.__setattr__(self, name, path)


@dataclass(frozen=True)
class CompileResult:
    status: Literal["compiled", "failed"]
    returncode: int
    compiled_bytes: int
    artifact_sha256: str
    log_sha256: str

    def __post_init__(self) -> None:
        if (
            self.status not in {"compiled", "failed"}
            or type(self.returncode) is not int
            or (self.status == "compiled") != (self.returncode == 0)
            or type(self.compiled_bytes) is not int
            or self.compiled_bytes < 0
            or any(
                type(value) is not str
                or len(value) != 64
                or value != value.casefold()
                or any(character not in "0123456789abcdef" for character in value)
                for value in (self.artifact_sha256, self.log_sha256)
            )
        ):
            raise ValueError("remapped smoke compile result is invalid")


Compiler = Callable[[SmokeCase, RemappedTopologyProof], CompileResult]


def _not_run() -> dict[str, object]:
    return {
        "status": "not-run",
        "returncode": None,
        "compiled_bytes": None,
        "artifact_sha256": None,
        "log_sha256": None,
    }


def run_smoke_cases(
    cases: Sequence[SmokeCase], *, compiler: Compiler | None = None,
) -> dict[str, object]:
    frozen = tuple(cases)
    if not frozen or any(not isinstance(case, SmokeCase) for case in frozen):
        raise ValueError("remapped smoke cases are invalid")
    if len({case.name for case in frozen}) != len(frozen):
        raise ValueError("remapped smoke case names are duplicated")
    if compiler is not None and not callable(compiler):
        raise TypeError("remapped smoke compiler is invalid")
    records = []
    for case in frozen:
        if case.source.stat().st_size > MAX_TEXT_BYTES or case.output.stat().st_size > MAX_TEXT_BYTES:
            raise ValueError("remapped smoke input exceeds contract byte cap")
        source_bytes = case.source.read_bytes()
        output_bytes = case.output.read_bytes()
        record: dict[str, object] = {
            "name": case.name,
            "requested_ratio": case.requested_ratio,
            "source_path": os.fspath(case.source),
            "output_path": os.fspath(case.output),
            "source_size": len(source_bytes),
            "output_size": len(output_bytes),
            "source_sha256": _hash(source_bytes),
            "output_sha256": _hash(output_bytes),
            "quality_status": "unverified",
            "quality_claim": None,
            "winner": False,
        }
        try:
            source_text = source_bytes.decode("utf-8")
            output_text = output_bytes.decode("utf-8")
            proof = validate_remapped_topology_smd(
                source_text, output_text, case.requested_ratio
            )
        except (UnicodeDecodeError, TypeError, ValueError, RuntimeError) as exc:
            record.update({
                "structural_status": "rejected",
                "rejection_reason": f"{type(exc).__name__}: {exc}",
                "proof": None,
                "compile": _not_run(),
            })
        else:
            compile_payload = _not_run()
            if compiler is not None:
                result = compiler(case, proof)
                if not isinstance(result, CompileResult):
                    raise TypeError("remapped smoke compiler returned an invalid result")
                compile_payload = asdict(result)
            record.update({
                "structural_status": "passed",
                "rejection_reason": None,
                "proof": remapped_topology_proof_payload(proof),
                "compile": compile_payload,
            })
        records.append(record)
    payload: dict[str, object] = {
        "schema": 1,
        "record_kind": "local_experiment",
        "corpus_id": "lvs-models-v1",
        "strategy": "meshopt-remapped-topology-v1",
        "transfer": "remapped-topology-v1",
        "quality_status": "unverified",
        "quality_claim": None,
        "winner": False,
        "cases": records,
    }
    payload["evidence_sha256"] = _seal(payload)
    return payload


class StudioMdlCompiler:
    def __init__(self, *, studiomdl: Path, work_root: Path, repo_root: Path = ROOT) -> None:
        self.studiomdl = Path(studiomdl).expanduser().resolve()
        self.work_root = Path(work_root).expanduser().resolve()
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.script = self.repo_root / "batch_compile_opt_qc.py"
        if not self.studiomdl.is_file() or not self.script.is_file():
            raise ValueError("remapped smoke compile tools are unavailable")
        self.work_root.mkdir(parents=True, exist_ok=True)
        if not self.work_root.is_dir():
            raise ValueError("remapped smoke compile work root is unavailable")

    def __call__(self, case: SmokeCase, proof: RemappedTopologyProof) -> CompileResult:
        if not isinstance(case, SmokeCase) or not isinstance(proof, RemappedTopologyProof):
            raise TypeError("remapped smoke compile inputs are invalid")
        run = Path(tempfile.mkdtemp(prefix=f"{case.name}-", dir=self.work_root))
        source = run / "source.smd"
        shutil.copyfile(case.output, source)
        source_text = case.output.read_bytes().decode("utf-8")
        lines = source_text.splitlines(keepends=True)
        triangle_index = next(
            (index for index, line in enumerate(lines) if line.strip().casefold() == "triangles"),
            None,
        )
        if triangle_index is None:
            raise ValueError("remapped smoke compile source has no triangles section")
        idle = run / "idle.smd"
        idle.write_text("".join(lines[:triangle_index]), encoding="utf-8", newline="\n")
        qc = run / "model_OPT.qc"
        qc.write_text(
            f'$modelname "maximum/remapped/{case.name}.mdl"\n'
            '$body "body" "source.smd"\n'
            '$sequence "idle" "idle.smd" fps 1\n',
            encoding="utf-8",
        )
        compiled = run / "compiled"
        command = (
            sys.executable,
            str(self.script),
            str(run),
            "--out",
            str(compiled),
            "--studiomdl",
            str(self.studiomdl),
            "--no-restore-phy",
        )
        completed = subprocess.run(
            command,
            cwd=run,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log = run / "smoke_compile.log"
        log.write_bytes(completed.stdout)
        artifacts = []
        model_root = compiled / "models"
        if model_root.is_dir():
            for path in sorted(
                (
                    item for item in model_root.rglob("*")
                    if item.is_file() and item.suffix.casefold() in {".mdl", ".vvd", ".vtx", ".phy", ".ani"}
                ),
                key=lambda item: item.relative_to(model_root).as_posix(),
            ):
                data = path.read_bytes()
                artifacts.append({
                    "path": path.relative_to(model_root).as_posix(),
                    "size": len(data),
                    "sha256": _hash(data),
                })
        has_mdl = any(item["path"].casefold().endswith(".mdl") for item in artifacts)
        succeeded = completed.returncode == 0 and has_mdl
        return CompileResult(
            status="compiled" if succeeded else "failed",
            returncode=completed.returncode if completed.returncode != 0 or succeeded else -1,
            compiled_bytes=sum(item["size"] for item in artifacts),
            artifact_sha256=_seal({"artifacts": artifacts}),
            log_sha256=_hash(completed.stdout),
        )


def _case(value: str) -> SmokeCase:
    fields = value.split("|", 3)
    if len(fields) != 4:
        raise argparse.ArgumentTypeError("case must be NAME|RATIO|SOURCE|OUTPUT")
    try:
        return SmokeCase(fields[0], float(fields[1]), Path(fields[2]), Path(fields[3]))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate remapped topology LVS R&D cases")
    parser.add_argument("--case", action="append", type=_case, required=True)
    parser.add_argument("--studiomdl")
    parser.add_argument("--compile-work-root")
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)
    if bool(args.studiomdl) != bool(args.compile_work_root):
        parser.error("--studiomdl and --compile-work-root must be provided together")
    compiler = None
    if args.studiomdl:
        compiler = StudioMdlCompiler(
            studiomdl=Path(args.studiomdl),
            work_root=Path(args.compile_work_root),
        )
    payload = run_smoke_cases(tuple(args.case), compiler=compiler)
    rendered = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output_json:
        destination = Path(args.output_json).expanduser().resolve()
        destination.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
