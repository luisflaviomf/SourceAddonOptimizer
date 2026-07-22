from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import tempfile
import time
from typing import Protocol

from .cache import FileRegionCache
from .closure import ClosureAudit
from .compiled_validation import CompiledFamilyExpectation, validate_compiled_family
from .composition import (
    RegionReplacement,
    compose_document_regions,
    compose_precomposed_source_tree,
    isolate_compile_failure,
)
from .contracts import MaximumProfile, RegionBudget, RegionKey, ValidationDecision
from .materials import MaterialSemantics, resolve_material_semantics
from .mesh_attributes import classify_position_topology
from .meshopt_bridge import MeshInput, SimplifyOptions, simplify_mesh
from .metrics import (
    MetricContract,
    PreparedRegionReference,
    measure_region,
    measure_region_prepared,
    prepare_region_reference,
    validate_region,
)
from .qc_graph import QcOccurrence, scan_qc_occurrences
from .regions import RegionPair, SmdRegion, build_region_graph, correspond_graphs
from .rendering import RenderEvidence, RenderRequest, requires_targeted_render
from .reporting import (
    ComparableSizeReport,
    MaximumRunReport,
    RegionReport,
    RegionStatusCounts,
    StageTiming,
    comparable_model_bytes,
    write_atomic_maximum_report,
)
from .risk import budget_for_risk, measure_risk, requires_adaptive_validation
from .search import RegionDecision, RegionRequest, optimize_region
from .smd import SmdDocument, SmdInfluence, SmdTriangle, SmdVertex, parse_smd, serialize_smd


STAGES = (
    "normal-seed",
    "regional-inventory",
    "adaptive-simplification",
    "targeted-validation",
    "compile-fallback",
    "packaging",
)
_VIEWS = (
    (1.0, 0.0, 0.0),
    (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, 0.0, -1.0),
    (1.0, -1.0, 1.0),
    (-1.0, -1.0, 1.0),
)


class CancellationProbe(Protocol):
    def throw_if_cancelled(self) -> None: ...


@dataclass(frozen=True)
class CompileResult:
    success: bool
    compiled_models_root: Path
    changed_source: str | None
    log_path: Path

    def __post_init__(self) -> None:
        if type(self.success) is not bool:
            raise ValueError("compile result success must be boolean")
        object.__setattr__(self, "compiled_models_root", Path(self.compiled_models_root))
        object.__setattr__(self, "log_path", Path(self.log_path))


SimplifyRegion = Callable[[SmdRegion, float, RegionBudget], SmdRegion]
ValidateCandidate = Callable[[SmdRegion, SmdRegion, RegionBudget, RegionKey], ValidationDecision]


@dataclass(frozen=True)
class MaximumRunOptions:
    addon_root: Path
    original_source_root: Path
    normal_source_root: Path
    staging_root: Path
    cache_root: Path
    report_path: Path
    profile: MaximumProfile
    framework_resolver_root: Path | None
    blender: Path
    compile_family: Callable[[Path], CompileResult]
    render_region: Callable[[RenderRequest], RenderEvidence]
    cancel: CancellationProbe
    simplify_region: SimplifyRegion | None = None
    validate_candidate: ValidateCandidate | None = None
    compiled_expectations: tuple[CompiledFamilyExpectation, ...] = ()
    audit_closure: Callable[[Path], ClosureAudit] | None = None
    progress: Callable[[str], None] = print

    def __post_init__(self) -> None:
        for name in (
            "addon_root",
            "original_source_root",
            "normal_source_root",
            "staging_root",
            "cache_root",
            "report_path",
            "blender",
        ):
            object.__setattr__(self, name, Path(getattr(self, name)))
        if self.framework_resolver_root is not None:
            object.__setattr__(self, "framework_resolver_root", Path(self.framework_resolver_root))
        object.__setattr__(self, "compiled_expectations", tuple(self.compiled_expectations))


@dataclass(frozen=True)
class _SourceInventory:
    occurrence: QcOccurrence
    original_document: SmdDocument
    normal_document: SmdDocument
    pairs: tuple[RegionPair, ...]


@dataclass(frozen=True)
class _SelectedRegion:
    source: PurePosixPath
    original: SmdRegion
    normal: SmdRegion
    decision: RegionDecision
    semantics: MaterialSemantics
    risk_score: float
    target_ratio: float
    targeted_render: bool = False


@dataclass(frozen=True)
class _RegionState:
    source: PurePosixPath
    key: RegionKey
    material: str
    original_ordinals: tuple[int, ...]
    original_triangles: int
    original_vertices: int
    normal_triangles: int
    selected_triangles: int
    selected_vertices: int
    representation: str
    risk_score: float
    target_ratio: float
    evaluations: int
    cache_hits: int
    failed_gates: tuple[str, ...]
    reason: str
    selected_path: Path | None
    render_request: RenderRequest | None = None
    fallback_path: Path | None = None
    fallback_representation: str | None = None
    fallback_triangles: int = 0
    fallback_vertices: int = 0
    targeted_render: bool = False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _attribute_contract_sha256() -> str:
    digest = hashlib.sha256()
    for path in (
        Path(__file__).with_name("mesh_attributes.py"),
        Path(__file__).with_name("meshopt_bridge.py"),
        Path(__file__).with_name("native") / "bin" / "win-x64" / "meshopt_bridge.dll",
    ):
        digest.update(path.name.encode("ascii"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _source_triangle_total(root: Path) -> int:
    total = 0
    for path in sorted(Path(root).rglob("*.smd"), key=lambda value: value.as_posix().casefold()):
        if "output" in {part.casefold() for part in path.relative_to(root).parts[:-1]}:
            continue
        try:
            total += len(parse_smd(path.read_text(encoding="utf-8", errors="strict")).triangles)
        except (OSError, UnicodeError, ValueError):
            continue
    return total


def _region_stats(key: RegionKey, template: SmdRegion, triangles: tuple[SmdTriangle, ...]) -> SmdRegion:
    positions = tuple(vertex.position for triangle in triangles for vertex in triangle.vertices)
    if not positions:
        raise ValueError("simplifier returned an empty region")
    centroid = tuple(sum(value[axis] for value in positions) / len(positions) for axis in range(3))
    bounds_min = tuple(min(value[axis] for value in positions) for axis in range(3))
    bounds_max = tuple(max(value[axis] for value in positions) for axis in range(3))
    bones = tuple(
        sorted(
            {
                influence.bone
                for triangle in triangles
                for vertex in triangle.vertices
                for influence in vertex.influences
                if influence.weight > 0.0
            }
        )
    )
    return SmdRegion(
        key,
        template.material,
        template.local_ordinal,
        template.triangle_ordinals,
        triangles,
        centroid,  # type: ignore[arg-type]
        bounds_min,  # type: ignore[arg-type]
        bounds_max,  # type: ignore[arg-type]
        bones,
    )


def _skin_slots(vertex: SmdVertex) -> tuple[tuple[float, float, float, float], tuple[int, int, int, int]]:
    combined: dict[int, float] = {}
    for influence in vertex.influences:
        if influence.weight > 0.0:
            combined[influence.bone] = combined.get(influence.bone, 0.0) + influence.weight
    selected = sorted(combined.items(), key=lambda item: (-item[1], item[0]))[:4]
    total = sum(weight for _bone, weight in selected)
    if total <= 1e-12:
        raise ValueError("region vertex has no skinning weights")
    weights = tuple(weight / total for _bone, weight in selected) + (0.0,) * (4 - len(selected))
    bones = tuple(bone for bone, _weight in selected) + (0,) * (4 - len(selected))
    return weights, bones  # type: ignore[return-value]


def simplify_smd_region(region: SmdRegion, ratio: float, budget: RegionBudget) -> SmdRegion:
    positions_list = []
    normals_list = []
    uvs_list = []
    weights_list = []
    bones_list = []
    indices_list = []
    by_attributes: dict[tuple[object, ...], int] = {}
    for triangle in region.triangles:
        for vertex in triangle.vertices:
            weights, bones = _skin_slots(vertex)
            key = (vertex.position, vertex.normal, vertex.uv, weights, bones)
            index = by_attributes.get(key)
            if index is None:
                index = len(positions_list)
                by_attributes[key] = index
                positions_list.append(vertex.position)
                normals_list.append(vertex.normal)
                uvs_list.append(vertex.uv)
                weights_list.append(weights)
                bones_list.append(bones)
            indices_list.append(index)
    positions = tuple(positions_list)
    normals = tuple(normals_list)
    uvs = tuple(uvs_list)
    weights = tuple(weights_list)
    bones = tuple(bones_list)
    indices = tuple(indices_list)
    materials = (0,) * len(region.triangles)
    topology = classify_position_topology(
        positions,
        normals,
        uvs,
        indices,
        materials,
        weights,
        bones,
    )
    mesh = MeshInput(
        positions,
        normals,
        uvs,
        weights,
        indices,
        materials,
        topology.vertex_flags,
        bones,
    )
    simplified = simplify_mesh(
        mesh,
        SimplifyOptions(
            target_ratio=ratio,
            target_error=max(0.1, budget.surface_max),
            position_remap=True,
        ),
    )
    triangles = []
    for ordinal in range(len(simplified.indices) // 3):
        corners = []
        for index in simplified.indices[ordinal * 3 : ordinal * 3 + 3]:
            positive = [
                (bone, weight)
                for bone, weight in zip(simplified.bone_indices[index], simplified.weights[index])
                if weight > 1e-8
            ]
            positive.sort(key=lambda item: (-item[1], item[0]))
            positive = positive[:3]
            total = sum(weight for _bone, weight in positive)
            influences = tuple(SmdInfluence(bone, weight / total) for bone, weight in positive)
            corners.append(
                SmdVertex(
                    influences[0].bone,
                    simplified.positions[index],
                    simplified.normals[index],
                    simplified.uvs[index],
                    influences,
                )
            )
        triangles.append(SmdTriangle(region.material, tuple(corners), ordinal))  # type: ignore[arg-type]
    return _region_stats(region.key, region, tuple(triangles))


def _pose_contract(region: SmdRegion, profile: MaximumProfile) -> MetricContract:
    diagonal = math.sqrt(
        sum((region.bounds_max[axis] - region.bounds_min[axis]) ** 2 for axis in range(3))
    )
    amount = max(diagonal, 1.0) * 0.05
    pose = []
    for bone in region.bone_ids:
        axis = bone % 3
        translation = [0.0, 0.0, 0.0]
        translation[axis] = amount * (1.0 if bone % 2 else -1.0)
        pose.append(
            (
                bone,
                (
                    1.0, 0.0, 0.0, translation[0],
                    0.0, 1.0, 0.0, translation[1],
                    0.0, 0.0, 1.0, translation[2],
                ),
            )
        )
    samples = min(profile.sample_count, max(64, len(region.triangles) * 8))
    return MetricContract(
        _VIEWS,
        profile.silhouette_resolution,
        samples,
        region.key.value,
        (tuple(pose),) if pose else (),
    )


def _default_validate_candidate(
    original: SmdRegion,
    candidate: SmdRegion,
    budget: RegionBudget,
    _key: RegionKey,
    profile: MaximumProfile,
) -> ValidationDecision:
    if original.triangles == candidate.triangles:
        return ValidationDecision(True, (), 1.0)
    try:
        return validate_region(measure_region(original, candidate, _pose_contract(original, profile)), budget)
    except (ArithmeticError, ValueError):
        return ValidationDecision(False, ("validator-error",), 0.0)


def _peak_working_set() -> int:
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return int(counters.PeakWorkingSetSize)
    except (AttributeError, OSError):
        pass
    return 0


def _emit(options: MaximumRunOptions, stage: str, current: int, detail: str) -> None:
    options.progress(f"[MAXIMUM] stage={stage} current={current} total={len(STAGES)} detail={detail}")


def _run_stage(options: MaximumRunOptions, timings: list[StageTiming], stage: str, function):
    options.cancel.throw_if_cancelled()
    current = STAGES.index(stage) + 1
    _emit(options, stage, current, "start")
    wall = time.perf_counter()
    cpu = time.process_time()
    result = function()
    timing = StageTiming(
        stage,
        time.perf_counter() - wall,
        time.process_time() - cpu,
        _peak_working_set(),
    )
    timings.append(timing)
    _emit(options, stage, current, f"done wall={timing.wall_seconds:.3f}s")
    return result


def _inventory(options: MaximumRunOptions) -> tuple[QcOccurrence, ...]:
    occurrences = scan_qc_occurrences(options.original_source_root)
    first_by_source: dict[PurePosixPath, QcOccurrence] = {}
    for occurrence in occurrences:
        first_by_source.setdefault(occurrence.source_path, occurrence)
    return tuple(
        occurrence
        for _source, occurrence in sorted(
            first_by_source.items(),
            key=lambda item: item[0].as_posix().casefold(),
        )
    )


def _inventory_source(
    options: MaximumRunOptions,
    occurrence: QcOccurrence,
) -> tuple[_SourceInventory | None, int]:
    source = occurrence.source_path
    original_path = options.original_source_root / Path(*source.parts)
    normal_path = options.normal_source_root / Path(*source.parts)
    try:
        original_document = parse_smd(original_path.read_text(encoding="utf-8", errors="strict"))
        normal_document = parse_smd(normal_path.read_text(encoding="utf-8", errors="strict"))
        original_graph = build_region_graph(original_document, occurrence)
        normal_graph = build_region_graph(normal_document, occurrence)
        correspondence = correspond_graphs(original_graph, normal_graph)
        if correspondence.status != "mapped":
            return None, max(1, len(original_graph.regions))
        return (
            _SourceInventory(
                occurrence,
                original_document,
                normal_document,
                correspondence.pairs,
            ),
            0,
        )
    except (OSError, UnicodeError, ValueError):
        return None, 1


def _write_render_request(
    options: MaximumRunOptions,
    inventory: _SourceInventory,
    selected: _SelectedRegion,
) -> RenderRequest:
    root = options.cache_root / "renders" / selected.original.key.value
    root.mkdir(parents=True, exist_ok=True)
    before = root / "original-region.smd"
    after = root / "candidate-region.smd"
    camera = root / "camera.json"
    before.write_text(
        serialize_smd(SmdDocument(inventory.original_document.header_lines, selected.original.triangles)),
        encoding="utf-8",
        newline="\n",
    )
    after.write_text(
        serialize_smd(SmdDocument(inventory.original_document.header_lines, selected.decision.selected.triangles)),
        encoding="utf-8",
        newline="\n",
    )
    camera.write_text(json.dumps({"angles": ["front", "left", "top", "iso1"]}) + "\n", encoding="utf-8")
    material_roots = (options.addon_root,) + (
        (options.framework_resolver_root,) if options.framework_resolver_root is not None else ()
    )
    return RenderRequest(
        before,
        after,
        camera,
        root / "evidence",
        options.blender,
        "reference",
        material_roots=material_roots,
        material_directories=inventory.occurrence.material_directories,
    )


def _representation_counts(selected: list[_RegionState], ambiguous: int, failed: int) -> RegionStatusCounts:
    values = [item.representation for item in selected]
    return RegionStatusCounts(
        values.count("aggressive"),
        values.count("lighter"),
        values.count("normal"),
        values.count("original"),
        ambiguous,
        failed,
    )


def _vertex_count(region: SmdRegion) -> int:
    return len(
        {
            (vertex.position, vertex.normal, vertex.uv, vertex.influences)
            for triangle in region.triangles
            for vertex in triangle.vertices
        }
    )


def _write_region_smd(path: Path, header_lines: tuple[str, ...], region: SmdRegion) -> Path:
    lines = [*header_lines, "triangles"]
    for triangle in region.triangles:
        lines.append(triangle.material)
        for vertex in triangle.vertices:
            values = [
                str(vertex.primary_bone),
                *(repr(float(value)) for value in vertex.position),
                *(repr(float(value)) for value in vertex.normal),
                *(repr(float(value)) for value in vertex.uv),
                str(len(vertex.influences)),
            ]
            for influence in vertex.influences:
                values.extend((str(influence.bone), repr(float(influence.weight))))
            lines.append(" ".join(values))
    lines.append("end")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _selected_requires_render(selected: _SelectedRegion) -> bool:
    decision = selected.decision
    return decision.representation != "original" and requires_targeted_render(
        selected.semantics,
        decision.validation.margin_fraction,
        selected.semantics.confidence,
        1.0 - len(decision.selected.triangles) / len(selected.original.triangles),
    )


def _region_state_without_spool(selected: _SelectedRegion) -> _RegionState:
    decision = selected.decision
    return _RegionState(
        selected.source,
        selected.original.key,
        selected.original.material,
        selected.original.triangle_ordinals,
        len(selected.original.triangles),
        _vertex_count(selected.original),
        len(selected.normal.triangles),
        len(decision.selected.triangles),
        _vertex_count(decision.selected),
        decision.representation,
        selected.risk_score,
        selected.target_ratio,
        decision.evaluations,
        decision.cache_hits,
        decision.validation.failed_gates,
        decision.reason,
        None,
    )


def _persist_region_state(
    options: MaximumRunOptions,
    inventory: _SourceInventory,
    selected: _SelectedRegion,
    regions_root: Path,
) -> _RegionState:
    decision = selected.decision
    render_request: RenderRequest | None = None
    fallback_path: Path | None = None
    fallback_representation: str | None = None
    fallback_triangles = 0
    fallback_vertices = 0
    region_root = regions_root / selected.original.key.value
    selected_path = _write_region_smd(
        region_root / "selected-region.smd",
        inventory.original_document.header_lines,
        decision.selected,
    )
    should_render = _selected_requires_render(selected)
    if should_render:
        render_request = _write_render_request(options, inventory, selected)
        fallback_region = selected.normal if decision.representation == "aggressive" else selected.original
        fallback_representation = "normal" if fallback_region is selected.normal else "original"
        fallback_path = _write_region_smd(
            region_root / "fallback-region.smd",
            inventory.original_document.header_lines,
            fallback_region,
        )
        fallback_triangles = len(fallback_region.triangles)
        fallback_vertices = _vertex_count(fallback_region)
    return _RegionState(
        selected.source,
        selected.original.key,
        selected.original.material,
        selected.original.triangle_ordinals,
        len(selected.original.triangles),
        _vertex_count(selected.original),
        len(selected.normal.triangles),
        len(decision.selected.triangles),
        _vertex_count(decision.selected),
        decision.representation,
        selected.risk_score,
        selected.target_ratio,
        decision.evaluations,
        decision.cache_hits,
        decision.validation.failed_gates,
        decision.reason,
        selected_path,
        render_request,
        fallback_path,
        fallback_representation,
        fallback_triangles,
        fallback_vertices,
    )


def _optimize_source(
    options: MaximumRunOptions,
    occurrence: QcOccurrence,
    simplify: SimplifyRegion,
    validate_injected: ValidateCandidate | None,
    cache: FileRegionCache,
    attribute_hash: str,
) -> tuple[_SourceInventory | None, tuple[_SelectedRegion, ...], int]:
    inventory, ambiguous_regions = _inventory_source(options, occurrence)
    if inventory is None:
        return None, (), ambiguous_regions
    source = inventory.occurrence.source_path
    source_path = options.original_source_root / Path(*source.parts)
    source_hash = _sha256(source_path)
    selected: list[_SelectedRegion] = []
    for pair in inventory.pairs:
        options.cancel.throw_if_cancelled()
        semantics = resolve_material_semantics(
            pair.original.material,
            options.addon_root,
            options.framework_resolver_root,
            inventory.occurrence.material_directories,
        )
        features = measure_risk(pair.original, semantics, _VIEWS)
        budget = budget_for_risk(options.profile, features)
        metric_reference: PreparedRegionReference | None = None

        if not pair.confident:
            decision = RegionDecision(
                pair.original,
                "original",
                1.0,
                0,
                0,
                (),
                ValidationDecision(True, (), 1.0),
                f"uncertain regional correspondence; restored original region: {pair.reason}",
            )
            selected.append(
                _SelectedRegion(
                    source,
                    pair.original,
                    pair.normal,
                    decision,
                    semantics,
                    features.score,
                    features.target_ratio,
                )
            )
            continue

        needs_validation = validate_injected is not None or requires_adaptive_validation(
            source.as_posix(),
            pair.original.material,
            len(pair.original.triangles),
            features,
            semantics,
        )
        if not needs_validation:
            selected_region = pair.normal if len(pair.normal.triangles) <= len(pair.original.triangles) else pair.original
            representation = "normal" if selected_region is pair.normal else "original"
            decision = RegionDecision(
                selected_region,
                representation,  # type: ignore[arg-type]
                len(selected_region.triangles) / len(pair.original.triangles),
                0,
                0,
                (),
                ValidationDecision(True, (), 1.0),
                "aggressive Normal seed accepted by low-risk classifier",
            )
            selected.append(
                _SelectedRegion(
                    source,
                    pair.original,
                    pair.normal,
                    decision,
                    semantics,
                    features.score,
                    features.target_ratio,
                )
            )
            continue

        def validator(candidate: SmdRegion) -> ValidationDecision:
            nonlocal metric_reference
            try:
                if validate_injected is not None:
                    return validate_injected(pair.original, candidate, budget, pair.original.key)
                if pair.original.triangles == candidate.triangles:
                    return ValidationDecision(True, (), 1.0)
                if metric_reference is None:
                    metric_reference = prepare_region_reference(
                        pair.original,
                        _pose_contract(pair.original, options.profile),
                    )
                return validate_region(measure_region_prepared(metric_reference, candidate), budget)
            except Exception:
                return ValidationDecision(False, ("validator-error",), 0.0)

        if len(pair.normal.triangles) > len(pair.original.triangles):
            decision = RegionDecision(
                pair.original,
                "original",
                1.0,
                0,
                0,
                (),
                ValidationDecision(True, (), 1.0),
                "normal region increased triangle count",
            )
        else:
            normal_validation = (
                validator(pair.normal)
                if validate_injected is not None
                else ValidationDecision(False, ("priority-seed-requires-lighter-candidate",), 0.0)
            )
            request = RegionRequest(
                pair.original,
                pair.normal,
                features.target_ratio,
                normal_validation,
                len(pair.original.triangles),
                len(pair.normal.triangles),
                source_hash,
                options.profile.sha256,
                attribute_hash,
                10200,
            )
            base_ratio = (
                len(pair.normal.triangles) / len(pair.original.triangles)
                if normal_validation.passed
                else 1.0
            )
            decision = optimize_region(
                request,
                lambda region, absolute_ratio: simplify(
                    region,
                    min(1.0, absolute_ratio / max(base_ratio, 1e-12)),
                    budget,
                ),
                validator,
                cache,
            )
        selected.append(
            _SelectedRegion(
                source,
                pair.original,
                pair.normal,
                decision,
                semantics,
                features.score,
                features.target_ratio,
            )
        )
    return inventory, tuple(selected), 0


def _composition_region(
    state: _RegionState,
    triangles: tuple[SmdTriangle, ...],
    ordinals: tuple[int, ...],
) -> SmdRegion:
    zero = (0.0, 0.0, 0.0)
    return SmdRegion(
        state.key,
        state.material,
        0,
        ordinals,
        triangles,
        zero,
        zero,
        zero,
        (),
    )


def _compose_candidate_source(
    options: MaximumRunOptions,
    source: PurePosixPath,
    states: tuple[_RegionState, ...],
    candidate_root: Path,
) -> None:
    original_path = options.original_source_root / Path(*source.parts)
    document = parse_smd(original_path.read_text(encoding="utf-8", errors="strict"))
    replacements = []
    for state in states:
        if state.selected_path is None:
            raise RuntimeError("deferred candidate region has no spool path")
        selected_document = parse_smd(state.selected_path.read_text(encoding="utf-8", errors="strict"))
        original_triangles = tuple(document.triangles[index] for index in state.original_ordinals)
        replacements.append(
            RegionReplacement(
                source,
                _composition_region(state, original_triangles, state.original_ordinals),
                _composition_region(state, selected_document.triangles, state.original_ordinals),
            )
        )
    composed = compose_document_regions(document, tuple(replacements))
    destination = candidate_root / Path(*source.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(serialize_smd(composed), encoding="utf-8", newline="\n")


def _compose_selected_source(
    inventory: _SourceInventory,
    selected: tuple[_SelectedRegion, ...],
    candidate_root: Path,
) -> None:
    source = inventory.occurrence.source_path
    replacements = tuple(
        RegionReplacement(source, item.original, item.decision.selected)
        for item in selected
    )
    composed = compose_document_regions(inventory.original_document, replacements)
    destination = candidate_root / Path(*source.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(serialize_smd(composed), encoding="utf-8", newline="\n")


def _discard_spooled_regions(states: tuple[_RegionState, ...], regions_root: Path) -> None:
    resolved_root = regions_root.resolve()
    for state in states:
        if state.selected_path is None:
            continue
        path = state.selected_path.resolve()
        try:
            path.relative_to(resolved_root)
        except ValueError:
            continue
        path.unlink(missing_ok=True)


def run_maximum_adaptive(options: MaximumRunOptions) -> MaximumRunReport:
    options.staging_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".maximum-regional-",
        dir=options.staging_root.parent,
    ) as raw_spool:
        return _run_maximum_adaptive(options, Path(raw_spool))


def _run_maximum_adaptive(options: MaximumRunOptions, spool_root: Path) -> MaximumRunReport:
    timings: list[StageTiming] = []
    failures: list[str] = []
    options.cancel.throw_if_cancelled()
    _run_stage(options, timings, "normal-seed", lambda: None)
    occurrences = _run_stage(
        options,
        timings,
        "regional-inventory",
        lambda: _inventory(options),
    )

    simplify = options.simplify_region or simplify_smd_region
    validate_injected = options.validate_candidate
    cache = FileRegionCache(options.cache_root / "regions")
    attribute_hash = _attribute_contract_sha256()
    selected: list[_RegionState] = []
    ambiguous_regions = 0
    regions_root = spool_root / "regions"
    candidate_root = spool_root / "candidates"
    candidate_root.mkdir(parents=True, exist_ok=True)
    composed_sources: set[PurePosixPath] = set()

    def optimize_all() -> None:
        nonlocal ambiguous_regions
        total = len(occurrences)
        interval = max(1, total // 100)
        for index, occurrence in enumerate(occurrences, start=1):
            options.cancel.throw_if_cancelled()
            if index == 1 or index == total or index % interval == 0:
                _emit(
                    options,
                    "adaptive-simplification",
                    3,
                    f"source={index}/{total} path={occurrence.source_path.as_posix()}",
                )
            inventory, source_selected, source_ambiguous = _optimize_source(
                options,
                occurrence,
                simplify,
                validate_injected,
                cache,
                attribute_hash,
            )
            ambiguous_regions += source_ambiguous
            if inventory is None:
                continue
            if any(_selected_requires_render(item) for item in source_selected):
                source_states = tuple(
                    _persist_region_state(options, inventory, item, regions_root)
                    for item in source_selected
                )
            else:
                _compose_selected_source(inventory, source_selected, candidate_root)
                source_states = tuple(_region_state_without_spool(item) for item in source_selected)
                composed_sources.add(occurrence.source_path)
            selected.extend(source_states)
            del inventory, source_selected, source_states

    _run_stage(options, timings, "adaptive-simplification", optimize_all)

    targeted_renders = 0

    def targeted_validation() -> None:
        nonlocal targeted_renders
        for index, item in enumerate(tuple(selected)):
            options.cancel.throw_if_cancelled()
            if item.render_request is None:
                continue
            targeted_renders += 1
            try:
                evidence = options.render_region(item.render_request)
            except Exception:
                evidence = None
            if evidence is not None and evidence.passed:
                selected[index] = replace(item, targeted_render=True)
                continue
            if item.fallback_path is None or item.fallback_representation is None:
                raise RuntimeError("targeted region has no local fallback")
            selected[index] = replace(
                item,
                selected_path=item.fallback_path,
                representation=item.fallback_representation,
                selected_triangles=item.fallback_triangles,
                selected_vertices=item.fallback_vertices,
                failed_gates=(),
                reason="targeted render failed; applied local fallback",
                targeted_render=True,
            )

        pending_by_source: dict[PurePosixPath, list[_RegionState]] = {}
        for item in selected:
            if item.source not in composed_sources:
                pending_by_source.setdefault(item.source, []).append(item)
        for source, source_states_list in sorted(
            pending_by_source.items(),
            key=lambda value: value[0].as_posix().casefold(),
        ):
            options.cancel.throw_if_cancelled()
            source_states = tuple(source_states_list)
            _compose_candidate_source(options, source, source_states, candidate_root)
            composed_sources.add(source)
            _discard_spooled_regions(source_states, regions_root)

    _run_stage(options, timings, "targeted-validation", targeted_validation)

    adaptive_sources = {
        item.source
        for item in selected
        if item.representation in ("aggressive", "lighter", "normal", "original")
    }
    compile_count = 0
    compiled: CompileResult | None = None
    compiled_source_root: Path | None = None

    def compile_with_fallback() -> None:
        nonlocal compile_count, compiled, compiled_source_root, adaptive_sources
        composition = compose_precomposed_source_tree(
            options.original_source_root,
            candidate_root,
            tuple(adaptive_sources),
            options.staging_root,
        )
        compiled_source_root = composition.root
        compiled = options.compile_family(composition.root)
        compile_count += 1
        if compiled.success:
            return

        bad_source: PurePosixPath | None = None
        if compiled.changed_source:
            candidate = PurePosixPath(compiled.changed_source.replace("\\", "/"))
            if candidate in adaptive_sources:
                bad_source = candidate
        if bad_source is None and adaptive_sources:
            isolation_index = 0

            def compile_active(values: tuple[str, ...]) -> bool:
                nonlocal compile_count, isolation_index, compiled, compiled_source_root
                isolation_index += 1
                active = {PurePosixPath(value) for value in values}
                stage = options.staging_root.with_name(f"{options.staging_root.name}.isolate-{isolation_index}")
                composition_try = compose_precomposed_source_tree(
                    options.original_source_root,
                    candidate_root,
                    tuple(active),
                    stage,
                )
                compiled_source_root = composition_try.root
                compiled = options.compile_family(composition_try.root)
                compile_count += 1
                return compiled.success

            isolation = isolate_compile_failure(
                tuple(path.as_posix() for path in adaptive_sources),
                compile_active,
            )
            bad_source = PurePosixPath(isolation.reverted_sources[0])
        if bad_source is None:
            failures.append("compile_failure_not_isolated")
            return
        adaptive_sources.remove(bad_source)
        for index, item in enumerate(tuple(selected)):
            if item.source == bad_source:
                selected[index] = replace(
                    item,
                    representation="original",
                    selected_triangles=item.original_triangles,
                    selected_vertices=item.original_vertices,
                    failed_gates=(),
                    reason="source-local compile fallback",
                )
        final_stage = options.staging_root.with_name(f"{options.staging_root.name}.compile-fallback")
        final_composition = compose_precomposed_source_tree(
            options.original_source_root,
            candidate_root,
            tuple(adaptive_sources),
            final_stage,
        )
        compiled_source_root = final_composition.root
        compiled = options.compile_family(final_composition.root)
        compile_count += 1
        if not compiled.success:
            failures.append("compile_failed_after_local_fallback")

    _run_stage(options, timings, "compile-fallback", compile_with_fallback)
    assert compiled is not None
    assert compiled_source_root is not None

    def package_validation() -> None:
        if not compiled.success:
            failures.append("studiomdl_failed")
            return
        for expected in options.compiled_expectations:
            validation = validate_compiled_family(compiled.compiled_models_root, expected)
            failures.extend(f"{expected.mdl_relative.as_posix()}:{value}" for value in validation.failures)
        if options.audit_closure is not None:
            audit = options.audit_closure(compiled.compiled_models_root)
            failures.extend(f"external:{value}" for value in audit.external_references)
            failures.extend(f"missing:{value}" for value in audit.missing_references)

    _run_stage(options, timings, "packaging", package_validation)

    original_sizes = comparable_model_bytes(options.addon_root / "models")
    final_sizes = comparable_model_bytes(compiled.compiled_models_root)
    size_report = ComparableSizeReport(
        original_sizes.comparable_bytes,
        final_sizes.comparable_bytes,
        original_sizes.comparable_bytes - final_sizes.comparable_bytes,
        original_sizes.dx80_bytes - final_sizes.dx80_bytes,
    )
    region_details = tuple(
        RegionReport(
            item.key.value,
            item.source.as_posix(),
            item.material,
            item.representation,
            item.original_triangles,
            item.normal_triangles,
            item.selected_triangles,
            item.selected_vertices,
            item.risk_score,
            item.target_ratio,
            item.evaluations,
            item.cache_hits,
            item.targeted_render,
            item.failed_gates,
            item.reason,
        )
        for item in sorted(selected, key=lambda value: value.key.value)
    )
    status = "failed" if failures else "optimized"
    report = MaximumRunReport(
        status,
        _representation_counts(selected, ambiguous_regions, len(failures)),
        size_report,
        0,
        targeted_renders,
        compile_count,
        sum(item.evaluations for item in selected),
        sum(item.cache_hits for item in selected),
        _source_triangle_total(options.original_source_root),
        _source_triangle_total(options.normal_source_root),
        _source_triangle_total(compiled_source_root),
        tuple(timings),
        region_details,
        tuple(dict.fromkeys(failures)),
        options.profile.sha256,
        options.profile.version,
        options.report_path,
    )
    write_atomic_maximum_report(report)
    return report
