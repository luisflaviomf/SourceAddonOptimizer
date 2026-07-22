from __future__ import annotations

import argparse
from dataclasses import asdict
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import random
import struct
import sys
import time

from PIL import Image, ImageChops


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from maximum_optimizer import metrics as metrics_module  # noqa: E402
from maximum_optimizer.cache import _region_from_payload, _region_payload  # noqa: E402
from maximum_optimizer.contracts import RegionBudget  # noqa: E402
from maximum_optimizer.pipeline import (  # noqa: E402
    CompileResult,
    MaximumRunOptions,
    _pose_contract,
    run_maximum_adaptive,
    simplify_smd_region,
)
from maximum_optimizer.profile import load_profile  # noqa: E402
from maximum_optimizer.qc_graph import scan_qc_occurrences  # noqa: E402
from maximum_optimizer.regions import build_region_graph, correspond_graphs  # noqa: E402
from maximum_optimizer.rendering import RenderEvidence  # noqa: E402
from maximum_optimizer.reporting import maximum_report_payload  # noqa: E402
from maximum_optimizer.silhouette_native import (  # noqa: E402
    MaskBatch,
    RawMaskSilhouetteKernel,
)
from maximum_optimizer.smd import parse_smd  # noqa: E402


def write_json(path: Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def image_from_bits(width: int, height: int, bits: bytes) -> Image.Image:
    return Image.frombytes("L", (width, height), bits)


def random_mask(rng: random.Random, width: int, height: int, density: int) -> bytes:
    return bytes(255 if rng.randrange(100) < density else 0 for _ in range(width * height))


def padded_batch(
    raw: bytes,
    width: int,
    height: int,
    *,
    row_padding: int,
    view_padding: int,
    offset: int,
) -> MaskBatch:
    row_stride = width + row_padding
    view_stride = row_stride * height + view_padding
    storage = bytearray(offset + view_stride)
    for y in range(height):
        start = offset + y * row_stride
        storage[start : start + width] = raw[y * width : (y + 1) * width]
    return MaskBatch(storage, width, height, 1, row_stride, view_stride, offset)


def mask_oracle(original: Image.Image, candidate: Image.Image, empty_distance: int) -> dict[str, object]:
    original_boundary = metrics_module._boundary_points(original)
    candidate_boundary = metrics_module._boundary_points(candidate)
    intersection = ImageChops.multiply(original, candidate).histogram()[255]
    union = ImageChops.lighter(original, candidate).histogram()[255]
    distances: list[int] = []
    if original_boundary or candidate_boundary:
        if not original_boundary or not candidate_boundary:
            distances.append(empty_distance * empty_distance)
        else:
            candidate_tree = metrics_module._kd_tree(candidate_boundary)
            original_tree = metrics_module._kd_tree(original_boundary)
            distances.extend(
                int(metrics_module._kd_distance(point, candidate_tree)) for point in original_boundary
            )
            distances.extend(
                int(metrics_module._kd_distance(point, original_tree)) for point in candidate_boundary
            )
    p95_squared = (
        sorted(distances)[min(len(distances) - 1, max(0, int(math.ceil(len(distances) * 0.95)) - 1))]
        if distances
        else 0
    )
    return {
        "original_boundary": original_boundary,
        "candidate_boundary": candidate_boundary,
        "distances": tuple(distances),
        "intersection": intersection,
        "union": union,
        "distance_count": len(distances),
        "p95_squared": p95_squared,
        "worst_iou_bytes": struct.pack("=d", 0.0 if union == 0 else 1.0 - intersection / union),
        "boundary_p95_bytes": struct.pack("=d", math.sqrt(p95_squared)),
    }


def seeded_cases(count: int) -> list[tuple[int, int, bytes, bytes, tuple[int, int, int, int, int, int]]]:
    cases: list[tuple[int, int, bytes, bytes, tuple[int, int, int, int, int, int]]] = []
    fixed = (
        (1, 1, b"\0", b"\0"),
        (1, 1, b"\xff", b"\0"),
        (3, 5, bytes([255] + [0] * 14), bytes([0] * 14 + [255])),
        (9, 7, bytes([255] * 63), bytes([255] * 63)),
        (11, 9, bytes(255 if index in (12, 13, 74, 75) else 0 for index in range(99)),
         bytes(255 if index in (47, 49) else 0 for index in range(99))),
    )
    for index, (width, height, original, candidate) in enumerate(fixed):
        cases.append((width, height, original, candidate, (index % 4, index % 5, index % 7, 3, 5, 1)))

    rng = random.Random(0x4D4158494D554D32)
    densities = (0, 1, 3, 7, 15, 30, 50, 70, 93, 99, 100)
    while len(cases) < count:
        width = rng.randrange(1, 24)
        height = rng.randrange(1, 24)
        original = random_mask(rng, width, height, rng.choice(densities))
        candidate = random_mask(rng, width, height, rng.choice(densities))
        if original == candidate and width * height > 1:
            changed = bytearray(candidate)
            point = rng.randrange(len(changed))
            changed[point] = 0 if changed[point] else 255
            candidate = bytes(changed)
        padding = (
            rng.randrange(0, 8),
            rng.randrange(0, 12),
            rng.randrange(0, 8),
            rng.randrange(0, 8),
            rng.randrange(0, 12),
            rng.randrange(0, 8),
        )
        cases.append((width, height, original, candidate, padding))
    return cases


def run_mask_equivalence(dll: Path, count: int, output: Path) -> None:
    kernel = RawMaskSilhouetteKernel(dll)
    digest = hashlib.sha256()
    started = time.perf_counter()
    maximum_distance_squared = 0
    maximum_boundary_count = 0
    for index, (width, height, original_raw, candidate_raw, padding) in enumerate(seeded_cases(count)):
        empty_distance = max(width, height)
        original_image = image_from_bits(width, height, original_raw)
        candidate_image = image_from_bits(width, height, candidate_raw)
        expected = mask_oracle(original_image, candidate_image, empty_distance)
        original_batch = padded_batch(
            original_raw,
            width,
            height,
            row_padding=padding[0],
            view_padding=padding[1],
            offset=padding[2],
        )
        candidate_batch = padded_batch(
            candidate_raw,
            width,
            height,
            row_padding=padding[3],
            view_padding=padding[4],
            offset=padding[5],
        )
        actual = kernel.measure(original_batch, candidate_batch, empty_distance=empty_distance, debug=True)
        observed = {
            "original_boundary": actual.debug.original_boundaries[0] if actual.debug else None,
            "candidate_boundary": actual.debug.candidate_boundaries[0] if actual.debug else None,
            "distances": actual.debug.distances_by_view[0] if actual.debug else None,
            "intersection": actual.intersections[0],
            "union": actual.unions[0],
            "distance_count": actual.distance_count,
            "p95_squared": actual.p95_distance_squared,
            "worst_iou_bytes": struct.pack("=d", actual.worst_iou_loss),
            "boundary_p95_bytes": struct.pack("=d", actual.boundary_p95_px),
        }
        if observed != expected:
            raise AssertionError(
                f"mask case {index} differs: size={width}x{height}, padding={padding}, "
                f"expected={expected}, observed={observed}"
            )
        maximum_distance_squared = max(maximum_distance_squared, actual.p95_distance_squared)
        maximum_boundary_count = max(
            maximum_boundary_count,
            len(expected["original_boundary"]),
            len(expected["candidate_boundary"]),
        )
        digest.update(struct.pack("=III", index, width, height))
        digest.update(original_raw)
        digest.update(candidate_raw)
        digest.update(json.dumps(
            {
                "original_boundary": expected["original_boundary"],
                "candidate_boundary": expected["candidate_boundary"],
                "distances": expected["distances"],
                "intersection": expected["intersection"],
                "union": expected["union"],
                "p95_squared": expected["p95_squared"],
            },
            separators=(",", ":"),
        ).encode("ascii"))
        if (index + 1) % 500 == 0:
            print(f"mask-equivalence {index + 1}/{count}", flush=True)
    payload = {
        "schema": 1,
        "status": "exact",
        "case_count": count,
        "seed": "0x4D4158494D554D32",
        "elapsed_seconds": time.perf_counter() - started,
        "contract_sha256": digest.hexdigest(),
        "maximum_p95_distance_squared": maximum_distance_squared,
        "maximum_boundary_count": maximum_boundary_count,
        "dll": str(Path(dll).resolve()),
        "dll_sha256": hashlib.sha256(Path(dll).read_bytes()).hexdigest(),
        "comparisons": [
            "input mask bytes",
            "four-neighbour row-major boundaries",
            "ordered bidirectional squared distances",
            "intersection and union counts",
            "global p95 squared distance",
            "float64 IoU and sqrt bytes",
        ],
    }
    write_json(output, payload)
    print(json.dumps(payload, sort_keys=True), flush=True)


class NoCancellation:
    def throw_if_cancelled(self) -> None:
        return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_contract(root: Path) -> dict[str, object]:
    root = Path(root)
    manifest = [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in sorted(
            (value for value in root.rglob("*") if value.is_file()),
            key=lambda value: value.relative_to(root).as_posix().casefold(),
        )
    ]
    return {
        "files": len(manifest),
        "bytes": sum(int(item["bytes"]) for item in manifest),
        "sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest(),
        "manifest": manifest,
    }


def semantic_contract(report_payload: dict[str, object], staging: dict[str, object]) -> dict[str, object]:
    profile = report_payload["profile"]
    assert isinstance(profile, dict)
    return {
        "family_status": report_payload["family_status"],
        "regions": report_payload["regions"],
        "failures": report_payload["failures"],
        "original_triangles": report_payload["original_triangles"],
        "normal_triangles": report_payload["normal_triangles"],
        "final_triangles": report_payload["final_triangles"],
        "targeted_renders": report_payload["targeted_renders"],
        "studiomdl_compiles": report_payload["studiomdl_compiles"],
        "simplifier_evaluations": report_payload["simplifier_evaluations"],
        "cache_hits": report_payload["cache_hits"],
        "region_details": report_payload["region_details"],
        "profile_version": profile["version"],
        "profile_sha256": profile["sha256"],
        "staging_files": staging["files"],
        "staging_bytes": staging["bytes"],
        "staging_tree_sha256": staging["sha256"],
        "compiled_tree_sha256": "fb83a6b549963073245a9bb637be0de733c2fd6eba74328c322f323addf3d04b",
    }


def run_pipeline(args: argparse.Namespace) -> None:
    frozen_root = Path(r"C:\gaco-max-v2-bench\holdout\pontiac_transam_wheel\maximum-adaptive-v2")
    addon_root = frozen_root / "source" / "pontiac_transam_wheel"
    original_root = frozen_root / "work" / "maximum" / "original-source"
    normal_root = frozen_root / "work" / "maximum" / "normal-source"
    compiled_root = frozen_root / "source" / "pontiac_transam_wheel_benchmark_output" / "models"
    profile = load_profile(REPO_ROOT / "maximum_optimizer" / "profiles" / "maximum-adaptive-v2.json")
    framework = Path(r"C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\2912816023\lvs_framework")
    blender = Path(r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe")

    for required in (addon_root, original_root, normal_root, compiled_root, framework, blender):
        if not required.exists():
            raise FileNotFoundError(required)
    if args.lane == "experiment":
        if args.dll is None or not args.dll.is_file():
            raise FileNotFoundError(args.dll)
        os.environ["MAXIMUM_SILHOUETTE_EXPERIMENT_DLL"] = str(args.dll.resolve())
    else:
        os.environ.pop("MAXIMUM_SILHOUETTE_EXPERIMENT_DLL", None)

    args.staging_root.mkdir(parents=True, exist_ok=False)
    args.cache_root.mkdir(parents=True, exist_ok=True)
    metrics_module._reset_silhouette_experiment_diagnostics()
    silhouette_ns = 0
    original_prepare = metrics_module._prepare_silhouettes
    candidate_function_name = (
        "_silhouette_metrics_native_prepared" if args.lane == "experiment"
        else "_silhouette_metrics_prepared"
    )
    original_candidate = getattr(metrics_module, candidate_function_name)

    @functools.wraps(original_prepare)
    def timed_prepare(*positional, **keywords):
        nonlocal silhouette_ns
        started = time.perf_counter_ns()
        try:
            return original_prepare(*positional, **keywords)
        finally:
            silhouette_ns += time.perf_counter_ns() - started

    @functools.wraps(original_candidate)
    def timed_candidate(*positional, **keywords):
        nonlocal silhouette_ns
        started = time.perf_counter_ns()
        try:
            return original_candidate(*positional, **keywords)
        finally:
            silhouette_ns += time.perf_counter_ns() - started

    metrics_module._prepare_silhouettes = timed_prepare
    setattr(metrics_module, candidate_function_name, timed_candidate)

    compile_log = args.staging_root / "compile-stub.json"

    def compile_family(_source: Path) -> CompileResult:
        compile_log.write_text('{"success":true}\n', encoding="ascii")
        return CompileResult(True, compiled_root, None, compile_log)

    def render_region(request) -> RenderEvidence:
        return RenderEvidence(True, 0.0, 0.0, request.output_dir, "e" * 64, False)

    options = MaximumRunOptions(
        addon_root=addon_root,
        original_source_root=original_root,
        normal_source_root=normal_root,
        staging_root=args.staging_root / "adaptive-source",
        cache_root=args.cache_root,
        report_path=args.staging_root / "maximum-report.json",
        profile=profile,
        framework_resolver_root=framework,
        blender=blender,
        compile_family=compile_family,
        render_region=render_region,
        cancel=NoCancellation(),
        progress=lambda _message: None,
    )

    before_blocks = sys.getallocatedblocks()
    before_cpu = time.process_time()
    started = time.perf_counter()
    try:
        report = run_maximum_adaptive(options)
    finally:
        metrics_module._prepare_silhouettes = original_prepare
        setattr(metrics_module, candidate_function_name, original_candidate)
    wall_seconds = time.perf_counter() - started
    cpu_seconds = time.process_time() - before_cpu
    after_blocks = sys.getallocatedblocks()
    report_payload = maximum_report_payload(report)
    staging = tree_contract(args.staging_root / "adaptive-source")
    semantic = semantic_contract(report_payload, staging)
    adaptive_seconds = next(
        stage.wall_seconds for stage in report.stages if stage.stage == "adaptive-simplification"
    )
    diagnostics = metrics_module._get_silhouette_experiment_diagnostics()
    payload = {
        "schema": 1,
        "run_id": args.run_id,
        "lane": args.lane,
        "cache_root": str(args.cache_root.resolve()),
        "staging_root": str(args.staging_root.resolve()),
        "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds,
        "adaptive_wall_seconds": adaptive_seconds,
        "silhouette_wall_seconds": silhouette_ns / 1_000_000_000.0,
        "allocated_blocks_before": before_blocks,
        "allocated_blocks_after": after_blocks,
        "allocated_blocks_delta": after_blocks - before_blocks,
        "silhouette_diagnostics": diagnostics,
        "report": report_payload,
        "semantic_contract": semantic,
        "semantic_sha256": hashlib.sha256(
            json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    write_json(args.out, payload)
    print(json.dumps({
        "run_id": args.run_id,
        "lane": args.lane,
        "adaptive": adaptive_seconds,
        "silhouette": silhouette_ns / 1_000_000_000.0,
        "wall": wall_seconds,
        "semantic": payload["semantic_sha256"],
    }, sort_keys=True), flush=True)


def normalized(value: object) -> object:
    return json.loads(json.dumps(value, sort_keys=True))


def region_payload_sha256(region) -> str:
    return hashlib.sha256(
        json.dumps(
            _region_payload(region),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()


def load_cross_regions(family: str, source: str, region_key: str):
    root = (
        Path(r"C:\gaco-max-v2-bench\holdout")
        / family
        / "maximum-adaptive-v2"
        / "work"
        / "maximum"
    )
    original_root = root / "original-source"
    adaptive_root = root / "adaptive-source"
    occurrences = tuple(
        occurrence
        for occurrence in scan_qc_occurrences(original_root)
        if occurrence.source_path.as_posix().casefold() == source.casefold()
    )
    if not occurrences:
        raise ValueError(f"no QC occurrence for {family}:{source}")
    original_document = parse_smd((original_root / Path(source)).read_text(encoding="utf-8"))
    candidate_document = parse_smd((adaptive_root / Path(source)).read_text(encoding="utf-8"))
    for occurrence in occurrences:
        original_graph = build_region_graph(original_document, occurrence)
        candidate_graph = build_region_graph(candidate_document, occurrence)
        mapping = correspond_graphs(original_graph, candidate_graph)
        for pair in mapping.pairs:
            if pair.original.key.value == region_key:
                return pair.original, pair.normal
    raise ValueError(f"region {region_key} not found for {family}:{source}")


def cached_cross_candidate(family: str, region_key: str, triangle_count: int):
    roots = []
    if family == "pontiac_transam_wheel":
        roots.append(Path(r"D:\gaco-max-v2-rigorous-profile-20260721\cache\cold-02\regions"))
    else:
        roots.append(
            Path(r"C:\gaco-max-v2-bench\holdout")
            / family
            / "maximum-adaptive-v2"
            / "work"
            / "maximum"
            / "cache"
            / "regions"
        )
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            region_payload = payload.get("region")
            if (
                isinstance(region_payload, dict)
                and region_payload.get("key") == region_key
                and len(region_payload.get("triangles", ())) == triangle_count
            ):
                return _region_from_payload(region_payload)
    return None


def run_cross_equivalence(dll: Path, output: Path) -> None:
    expected_root = Path(r"D:\gaco-max-v2-rigorous-profile-20260721\cross-checks.json")
    expected_payload = json.loads(expected_root.read_text(encoding="utf-8"))
    profile = load_profile(REPO_ROOT / "maximum_optimizer" / "profiles" / "maximum-adaptive-v2.json")
    checks = []
    for expected in expected_payload["checks"]:
        original, serialized_candidate = load_cross_regions(
            expected["family"], expected["source"], expected["region_key"]
        )
        budget = RegionBudget(**expected["budget"])
        candidate = cached_cross_candidate(
            expected["family"], expected["region_key"], expected["candidate_triangles"]
        )
        if candidate is None and expected["label"] == "skinned-mixed-weights":
            candidate = simplify_smd_region(original, 0.85, budget)
        if candidate is None:
            candidate = serialized_candidate
        if len(original.triangles) != expected["original_triangles"]:
            raise AssertionError(f"{expected['label']}: original triangle count changed")
        if len(candidate.triangles) != expected["candidate_triangles"]:
            raise AssertionError(f"{expected['label']}: candidate triangle count changed")
        contract = _pose_contract(original, profile)
        array_sha256 = region_payload_sha256(candidate)

        os.environ.pop("MAXIMUM_SILHOUETTE_EXPERIMENT_DLL", None)
        baseline_started = time.perf_counter()
        baseline_reference = metrics_module.prepare_region_reference(original, contract)
        baseline = metrics_module.measure_region_prepared(baseline_reference, candidate)
        baseline_seconds = time.perf_counter() - baseline_started
        baseline_decision = metrics_module.validate_region(baseline, budget)

        os.environ["MAXIMUM_SILHOUETTE_EXPERIMENT_DLL"] = str(Path(dll).resolve())
        metrics_module._reset_silhouette_experiment_diagnostics()
        experiment_started = time.perf_counter()
        experiment_reference = metrics_module.prepare_region_reference(original, contract)
        experiment = metrics_module.measure_region_prepared(experiment_reference, candidate)
        experiment_seconds = time.perf_counter() - experiment_started
        experiment_decision = metrics_module.validate_region(experiment, budget)
        diagnostics = metrics_module._get_silhouette_experiment_diagnostics()

        if baseline != experiment:
            raise AssertionError(f"{expected['label']}: metric dataclasses differ")
        for field in baseline.__dataclass_fields__:
            if struct.pack("=d", getattr(baseline, field)) != struct.pack("=d", getattr(experiment, field)):
                raise AssertionError(f"{expected['label']}: float bytes differ for {field}")
        if baseline_decision != experiment_decision:
            raise AssertionError(f"{expected['label']}: gate decision differs")
        if asdict(baseline) != expected["metrics"]:
            raise AssertionError(f"{expected['label']}: frozen metric values changed")
        if normalized(asdict(baseline_decision)) != normalized(expected["decision"]):
            raise AssertionError(f"{expected['label']}: frozen gate values changed")
        if array_sha256 != region_payload_sha256(candidate):
            raise AssertionError(f"{expected['label']}: candidate arrays mutated")
        metrics_sha256 = hashlib.sha256(
            json.dumps(asdict(baseline), sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        expected_metrics_sha256 = expected["timing"]["metric_fingerprints"][0]["metrics_sha256"]
        if metrics_sha256 != expected_metrics_sha256:
            raise AssertionError(f"{expected['label']}: frozen metric hash changed")
        check = {
            "label": expected["label"],
            "family": expected["family"],
            "source": expected["source"],
            "region_key": expected["region_key"],
            "original_triangles": len(original.triangles),
            "candidate_triangles": len(candidate.triangles),
            "frozen_original_sha256": expected["original_sha256"],
            "frozen_candidate_sha256": expected["candidate_sha256"],
            "array_payload_sha256": array_sha256,
            "metrics_sha256": metrics_sha256,
            "decision": normalized(asdict(baseline_decision)),
            "baseline_seconds": baseline_seconds,
            "experiment_seconds": experiment_seconds,
            "speedup": baseline_seconds / experiment_seconds,
            "experiment_diagnostics": diagnostics,
            "bitwise_equal": True,
        }
        checks.append(check)
        print(json.dumps(check, sort_keys=True), flush=True)
    os.environ.pop("MAXIMUM_SILHOUETTE_EXPERIMENT_DLL", None)
    payload = {
        "schema": 1,
        "status": "exact",
        "source_evidence": str(expected_root),
        "checks": checks,
    }
    write_json(output, payload)


def verify_full_compile(models_root: Path, report_path: Path, output: Path) -> None:
    compiled = tree_contract(models_root)
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    dx80 = tuple(path for path in Path(models_root).rglob("*") if path.name.casefold().endswith(".dx80.vtx"))
    expected_manifest = json.loads(
        Path(r"D:\gaco-max-v2-rigorous-profile-20260721\baseline-contract.json").read_text(encoding="utf-8")
    )["trees"]["compiled"]["manifest"]
    checks = {
        "compiled_tree_sha256": compiled["sha256"] == "fb83a6b549963073245a9bb637be0de733c2fd6eba74328c322f323addf3d04b",
        "compiled_manifest": compiled["manifest"] == expected_manifest,
        "compiled_bytes": compiled["bytes"] == 833184,
        "compiled_files": compiled["files"] == 4,
        "final_triangles": report["final_triangles"] == 13530,
        "original_triangles": report["original_triangles"] == 16304,
        "simplifier_evaluations": report["simplifier_evaluations"] == 19,
        "targeted_renders": report["targeted_renders"] == 1,
        "studiomdl_compiles": report["studiomdl_compiles"] == 1,
        "integrity": report["failures"] == [],
        "dx80_absent": not dx80,
    }
    if not all(checks.values()):
        raise AssertionError(checks)
    payload = {
        "schema": 1,
        "status": "exact",
        "models_root": str(Path(models_root).resolve()),
        "report_path": str(Path(report_path).resolve()),
        "checks": checks,
        "compiled": compiled,
        "report": report,
    }
    write_json(output, payload)
    print(json.dumps({"status": "exact", "checks": checks}, sort_keys=True), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    masks = subparsers.add_parser("mask-equivalence")
    masks.add_argument("--dll", type=Path, required=True)
    masks.add_argument("--count", type=int, default=10000)
    masks.add_argument("--out", type=Path, required=True)
    pipeline = subparsers.add_parser("run-pipeline")
    pipeline.add_argument("--run-id", required=True)
    pipeline.add_argument("--lane", choices=("baseline", "experiment"), required=True)
    pipeline.add_argument("--dll", type=Path)
    pipeline.add_argument("--cache-root", type=Path, required=True)
    pipeline.add_argument("--staging-root", type=Path, required=True)
    pipeline.add_argument("--out", type=Path, required=True)
    cross = subparsers.add_parser("cross-equivalence")
    cross.add_argument("--dll", type=Path, required=True)
    cross.add_argument("--out", type=Path, required=True)
    compile_check = subparsers.add_parser("verify-compile")
    compile_check.add_argument("--models-root", type=Path, required=True)
    compile_check.add_argument("--report", type=Path, required=True)
    compile_check.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "mask-equivalence":
        run_mask_equivalence(args.dll, args.count, args.out)
        return 0
    if args.command == "run-pipeline":
        run_pipeline(args)
        return 0
    if args.command == "cross-equivalence":
        run_cross_equivalence(args.dll, args.out)
        return 0
    if args.command == "verify-compile":
        verify_full_compile(args.models_root, args.report, args.out)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
