from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
from typing import Callable, Literal, Sequence

from PIL import Image, UnidentifiedImageError


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


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
class CompileArtifact:
    path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.path) is not str
            or not self.path
            or "\\" in self.path
            or self.path.startswith("/")
            or ".." in self.path.split("/")
            or type(self.size) is not int
            or self.size < 0
            or type(self.sha256) is not str
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("remapped smoke compile artifact is invalid")


@dataclass(frozen=True)
class CompileResult:
    status: Literal["compiled", "failed"]
    returncode: int
    compiled_bytes: int
    artifacts: tuple[CompileArtifact, ...]
    artifact_sha256: str
    log_sha256: str
    input_sha256: str

    def __post_init__(self) -> None:
        if (
            self.status not in {"compiled", "failed"}
            or type(self.returncode) is not int
            or (self.status == "compiled") != (self.returncode == 0)
            or type(self.compiled_bytes) is not int
            or self.compiled_bytes < 0
            or type(self.artifacts) is not tuple
            or any(not isinstance(item, CompileArtifact) for item in self.artifacts)
            or self.compiled_bytes != sum(item.size for item in self.artifacts)
            or len({item.path for item in self.artifacts}) != len(self.artifacts)
            or self.artifact_sha256 != compile_artifact_manifest_sha256(self.artifacts)
            or any(
                type(value) is not str
                or len(value) != 64
                or value != value.casefold()
                or any(character not in "0123456789abcdef" for character in value)
                for value in (self.artifact_sha256, self.log_sha256, self.input_sha256)
            )
        ):
            raise ValueError("remapped smoke compile result is invalid")


def compile_artifact_manifest_sha256(
    artifacts: tuple[CompileArtifact, ...],
) -> str:
    if type(artifacts) is not tuple or any(
        not isinstance(item, CompileArtifact) for item in artifacts
    ):
        raise TypeError("remapped compile artifacts are invalid")
    return _seal({"artifacts": [asdict(item) for item in artifacts]})


@dataclass(frozen=True)
class PairedCompileResult:
    status: Literal["compiled", "failed"]
    source: CompileResult
    candidate: CompileResult
    delta_bytes: int | None
    reduction_ratio: float | None
    evidence_sha256: str

    def __post_init__(self) -> None:
        compiled = self.source.status == self.candidate.status == "compiled"
        if (
            self.status not in {"compiled", "failed"}
            or not isinstance(self.source, CompileResult)
            or not isinstance(self.candidate, CompileResult)
            or (self.status == "compiled") != compiled
            or (compiled and type(self.delta_bytes) is not int)
            or (not compiled and self.delta_bytes is not None)
            or (compiled and self.source.compiled_bytes <= 0)
            or (
                compiled
                and self.delta_bytes
                != self.source.compiled_bytes - self.candidate.compiled_bytes
            )
            or (compiled and type(self.reduction_ratio) is not float)
            or (not compiled and self.reduction_ratio is not None)
            or (
                compiled
                and self.reduction_ratio
                != 1.0 - self.candidate.compiled_bytes / self.source.compiled_bytes
            )
            or type(self.evidence_sha256) is not str
            or len(self.evidence_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.evidence_sha256)
            or self.evidence_sha256 != _seal({
                "status": self.status,
                "source": asdict(self.source),
                "candidate": asdict(self.candidate),
                "delta_bytes": self.delta_bytes,
                "reduction_ratio": self.reduction_ratio,
            })
        ):
            raise ValueError("remapped paired compile result is invalid")


def pair_compile_results(
    source: CompileResult, candidate: CompileResult,
) -> PairedCompileResult:
    if not isinstance(source, CompileResult) or not isinstance(candidate, CompileResult):
        raise TypeError("remapped compile pair inputs are invalid")
    compiled = source.status == candidate.status == "compiled"
    if compiled and source.compiled_bytes <= 0:
        raise ValueError("remapped compiled source is empty")
    delta = source.compiled_bytes - candidate.compiled_bytes if compiled else None
    reduction = 1.0 - candidate.compiled_bytes / source.compiled_bytes if compiled else None
    values = {
        "status": "compiled" if compiled else "failed",
        "source": asdict(source),
        "candidate": asdict(candidate),
        "delta_bytes": delta,
        "reduction_ratio": reduction,
    }
    return PairedCompileResult(
        status=values["status"],
        source=source,
        candidate=candidate,
        delta_bytes=delta,
        reduction_ratio=reduction,
        evidence_sha256=_seal(values),
    )


Compiler = Callable[[SmokeCase, RemappedTopologyProof], PairedCompileResult]


def _compile_not_run() -> dict[str, object]:
    return {
        "status": "not-run",
        "source": None,
        "candidate": None,
        "delta_bytes": None,
        "reduction_ratio": None,
        "evidence_sha256": None,
    }


def _render_not_run() -> dict[str, object]:
    return {
        "status": "not-run",
        "quality_status": "unverified",
        "quality_claim": None,
        "evidence_sha256": None,
    }


_ANGLES = ("front", "back", "left", "right", "top", "bottom", "iso1", "iso2")


def _percentile(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def _render_evidence(
    case: SmokeCase,
    proof: RemappedTopologyProof,
    *,
    render_root: Path,
    renderer: Path,
    render_script: Path,
) -> dict[str, object]:
    if (
        _hash_file(case.source) != proof.source_sha256
        or _hash_file(case.output) != proof.output_sha256
    ):
        raise RuntimeError("remapped render inputs changed after structural validation")
    case_root = (render_root / case.name).resolve()
    summary_path = case_root / "preview_summary.json"
    if not summary_path.is_file():
        raise ValueError("remapped smoke render summary is missing")
    summary_bytes = summary_path.read_bytes()
    if len(summary_bytes) > 1024 * 1024:
        raise ValueError("remapped smoke render summary exceeds byte cap")
    summary = json.loads(summary_bytes.decode("utf-8"))
    if type(summary) is not dict or set(summary) != {"angles", "size", "before", "after"}:
        raise ValueError("remapped smoke render summary fields are invalid")
    if summary["angles"] != list(_ANGLES) or type(summary["size"]) is not int:
        raise ValueError("remapped smoke render matrix is invalid")
    size = summary["size"]
    if not 1 <= size <= 4096:
        raise ValueError("remapped smoke render size is invalid")
    for side, expected_path, expected_sha256, expected_triangles, directory in (
        ("before", case.source, proof.source_sha256, proof.triangles_before, "original"),
        ("after", case.output, proof.output_sha256, proof.triangles_after, "optimized"),
    ):
        value = summary[side]
        expected_images = {angle: f"{directory}/{angle}.png" for angle in _ANGLES}
        if (
            type(value) is not dict
            or set(value) != {"file", "files", "sha256s", "tris", "images"}
            or Path(value["file"]).expanduser().resolve() != expected_path
            or value["files"] != [value["file"]]
            or value["sha256s"] != [expected_sha256]
            or value["tris"] != expected_triangles
            or value["images"] != expected_images
        ):
            raise ValueError("remapped smoke render side binding is invalid")
    views = []
    for angle in _ANGLES:
        source_path = (case_root / "original" / f"{angle}.png").resolve()
        candidate_path = (case_root / "optimized" / f"{angle}.png").resolve()
        if source_path.parent != case_root / "original" or candidate_path.parent != case_root / "optimized":
            raise ValueError("remapped smoke render path escaped case root")
        try:
            source_png = source_path.read_bytes()
            candidate_png = candidate_path.read_bytes()
            with Image.open(io.BytesIO(source_png)) as image:
                source_image = image.convert("RGB")
                source_image.load()
            with Image.open(io.BytesIO(candidate_png)) as image:
                candidate_image = image.convert("RGB")
                candidate_image.load()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise ValueError("remapped smoke render image is invalid") from exc
        if source_image.size != (size, size) or candidate_image.size != (size, size):
            raise ValueError("remapped smoke render dimensions are invalid")
        source_pixels = tuple(source_image.get_flattened_data())
        candidate_pixels = tuple(candidate_image.get_flattened_data())
        per_pixel = []
        channel_total = 0
        for source_pixel, candidate_pixel in zip(source_pixels, candidate_pixels):
            differences = tuple(abs(left - right) for left, right in zip(source_pixel, candidate_pixel))
            per_pixel.append(max(differences))
            channel_total += sum(differences)
        changed = sum(value > 0 for value in per_pixel)
        view = {
            "angle": angle,
            "source_sha256": _hash(source_png),
            "candidate_sha256": _hash(candidate_png),
            "changed_pixels": changed,
            "changed_pixel_fraction": changed / len(per_pixel),
            "mean_abs_channel_delta_8bit": channel_total / (len(per_pixel) * 3),
            "p95_max_channel_delta": _percentile(per_pixel, 0.95),
            "p99_max_channel_delta": _percentile(per_pixel, 0.99),
            "max_channel_delta": max(per_pixel),
        }
        views.append(view)
    payload: dict[str, object] = {
        "status": "rendered",
        "quality_status": "unverified",
        "quality_claim": None,
        "renderer_sha256": _hash_file(renderer),
        "render_script_sha256": _hash_file(render_script),
        "summary_sha256": _hash(summary_bytes),
        "source_sha256": proof.source_sha256,
        "candidate_sha256": proof.output_sha256,
        "angles": list(_ANGLES),
        "size": size,
        "views": views,
    }
    payload["evidence_sha256"] = _seal(payload)
    return payload


def run_smoke_cases(
    cases: Sequence[SmokeCase],
    *,
    compiler: Compiler | None = None,
    render_root: Path | None = None,
    renderer: Path | None = None,
    render_script: Path | None = None,
) -> dict[str, object]:
    frozen = tuple(cases)
    if not frozen or any(not isinstance(case, SmokeCase) for case in frozen):
        raise ValueError("remapped smoke cases are invalid")
    if len({case.name for case in frozen}) != len(frozen):
        raise ValueError("remapped smoke case names are duplicated")
    if compiler is not None and not callable(compiler):
        raise TypeError("remapped smoke compiler is invalid")
    render_values = (render_root, renderer, render_script)
    if any(value is not None for value in render_values) != all(
        value is not None for value in render_values
    ):
        raise ValueError("remapped smoke render evidence inputs are incomplete")
    if render_root is not None:
        render_root = Path(render_root).expanduser().resolve()
        renderer = Path(renderer).expanduser().resolve()
        render_script = Path(render_script).expanduser().resolve()
        if not render_root.is_dir() or not renderer.is_file() or not render_script.is_file():
            raise ValueError("remapped smoke render evidence inputs are unavailable")
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
                "compile": _compile_not_run(),
                "render": _render_not_run(),
            })
        else:
            compile_payload = _compile_not_run()
            if compiler is not None:
                result = compiler(case, proof)
                if not isinstance(result, PairedCompileResult):
                    raise TypeError("remapped smoke compiler returned an invalid result")
                if (
                    result.source.input_sha256 != proof.source_sha256
                    or result.candidate.input_sha256 != proof.output_sha256
                ):
                    raise RuntimeError("remapped compile pair is not bound to structural proof")
                expected_artifacts = {
                    f"maximum/remapped/{case.name}.mdl",
                    f"maximum/remapped/{case.name}.vvd",
                    f"maximum/remapped/{case.name}.dx80.vtx",
                    f"maximum/remapped/{case.name}.dx90.vtx",
                }
                if result.status == "compiled" and any(
                    {item.path for item in side.artifacts} != expected_artifacts
                    for side in (result.source, result.candidate)
                ):
                    raise RuntimeError("remapped compile pair artifact set is invalid")
                compile_payload = asdict(result)
            render_payload = _render_not_run()
            if render_root is not None:
                render_payload = _render_evidence(
                    case,
                    proof,
                    render_root=render_root,
                    renderer=renderer,
                    render_script=render_script,
                )
            record.update({
                "structural_status": "passed",
                "rejection_reason": None,
                "proof": remapped_topology_proof_payload(proof),
                "compile": compile_payload,
                "render": render_payload,
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

    def _compile(
        self,
        case: SmokeCase,
        side: str,
        source_path: Path,
        expected_sha256: str,
    ) -> CompileResult:
        run = Path(tempfile.mkdtemp(prefix=f"{case.name}-{side}-", dir=self.work_root))
        source_bytes = source_path.read_bytes()
        input_sha256 = _hash(source_bytes)
        if input_sha256 != expected_sha256:
            raise RuntimeError("remapped smoke input changed after structural validation")
        source = run / "source.smd"
        source.write_bytes(source_bytes)
        source_text = source_bytes.decode("utf-8")
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
        artifacts: list[CompileArtifact] = []
        artifact_bytes: dict[str, bytes] = {}
        model_root = compiled / "models"
        if model_root.is_dir():
            for path in sorted(
                (item for item in model_root.rglob("*") if item.is_file()),
                key=lambda item: item.relative_to(model_root).as_posix(),
            ):
                data = path.read_bytes()
                relative = path.relative_to(model_root).as_posix()
                artifact_bytes[relative] = data
                artifacts.append(CompileArtifact(relative, len(data), _hash(data)))
        prefix = f"maximum/remapped/{case.name}"
        expected = {
            f"{prefix}.mdl",
            f"{prefix}.vvd",
            f"{prefix}.dx80.vtx",
            f"{prefix}.dx90.vtx",
        }
        coherent = False
        if set(artifact_bytes) == expected:
            mdl = artifact_bytes[f"{prefix}.mdl"]
            vvd = artifact_bytes[f"{prefix}.vvd"]
            dx80 = artifact_bytes[f"{prefix}.dx80.vtx"]
            dx90 = artifact_bytes[f"{prefix}.dx90.vtx"]
            coherent = (
                len(mdl) >= 408
                and mdl[:4] == b"IDST"
                and struct.unpack_from("<I", mdl, 4)[0] == 48
                and len(vvd) >= 64
                and vvd[:4] == b"IDSV"
                and struct.unpack_from("<I", vvd, 4)[0] == 4
                and len(dx80) >= 36
                and len(dx90) >= 36
                and struct.unpack_from("<I", dx80, 0)[0] == 7
                and struct.unpack_from("<I", dx90, 0)[0] == 7
                and mdl[8:12] == vvd[8:12] == dx80[16:20] == dx90[16:20]
            )
        succeeded = completed.returncode == 0 and coherent
        return CompileResult(
            status="compiled" if succeeded else "failed",
            returncode=completed.returncode if completed.returncode != 0 or succeeded else -1,
            compiled_bytes=sum(item.size for item in artifacts),
            artifacts=tuple(artifacts),
            artifact_sha256=compile_artifact_manifest_sha256(tuple(artifacts)),
            log_sha256=_hash(completed.stdout),
            input_sha256=input_sha256,
        )

    def __call__(self, case: SmokeCase, proof: RemappedTopologyProof) -> PairedCompileResult:
        if not isinstance(case, SmokeCase) or not isinstance(proof, RemappedTopologyProof):
            raise TypeError("remapped smoke compile inputs are invalid")
        source = self._compile(case, "source", case.source, proof.source_sha256)
        candidate = self._compile(case, "candidate", case.output, proof.output_sha256)
        return pair_compile_results(source, candidate)


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
    parser.add_argument("--render-root")
    parser.add_argument("--renderer")
    parser.add_argument("--render-script", default=str(ROOT / "render_previews.py"))
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)
    if bool(args.studiomdl) != bool(args.compile_work_root):
        parser.error("--studiomdl and --compile-work-root must be provided together")
    if bool(args.render_root) != bool(args.renderer):
        parser.error("--render-root and --renderer must be provided together")
    compiler = None
    if args.studiomdl:
        compiler = StudioMdlCompiler(
            studiomdl=Path(args.studiomdl),
            work_root=Path(args.compile_work_root),
        )
    render_values = {}
    if args.render_root:
        render_values = {
            "render_root": Path(args.render_root),
            "renderer": Path(args.renderer),
            "render_script": Path(args.render_script),
        }
    payload = run_smoke_cases(tuple(args.case), compiler=compiler, **render_values)
    rendered = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.output_json:
        destination = Path(args.output_json).expanduser().resolve()
        destination.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
