from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from maximum_optimizer.domain import CandidateEvaluation, CandidateSpec, SearchBudget
from maximum_optimizer.regions import parse_region_scope


_INITIAL_RATIOS = (0.85, 0.70, 0.55, 0.40, 0.25)
_MIN_RATIO = 0.01
_BLENDER_ADAPTIVE_RATIOS = (0.45, 0.40, 0.35, 0.30, 0.25)
_BLENDER_IMPORTANCE_RATIOS = (0.35, 0.30, 0.25, 0.20)


def _trail_key(spec: CandidateSpec) -> tuple[object, ...]:
    return (
        spec.engine,
        spec.target_error,
        spec.repair_profile,
        spec.region_overrides,
        spec.strategy,
        spec.update_vertices,
        spec.transfer,
    )


def _recovery_key(spec: CandidateSpec) -> tuple[object, ...]:
    """Comparable optimizer contract, deliberately excluding regional overrides."""
    return (
        spec.engine,
        spec.target_error,
        spec.repair_profile,
        spec.strategy,
        spec.update_vertices,
        spec.transfer,
    )


def _strategy_suffix(key: tuple[object, ...]) -> str:
    raw = json.dumps(key, ensure_ascii=True, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _ratio_id(ratio: float) -> str:
    text = f"{round(ratio, 6):.6f}".rstrip("0")
    whole, fraction = text.split(".")
    fraction = fraction.ljust(2, "0")
    return f"{whole}{fraction}"


def _candidate_id(engine: str, ratio: float, strategy: str = "") -> str:
    prefix = "meshopt" if engine == "meshoptimizer" else engine
    if strategy == "meshopt-direct-v1":
        prefix = "meshopt-direct"
    elif strategy == "meshopt-direct-position-v1":
        prefix = "meshopt-direct-position"
    elif strategy == "blender-importance-map-v1":
        prefix = "blender-importance"
    return f"{prefix}-r{_ratio_id(ratio)}"


def initial_candidates() -> list[CandidateSpec]:
    candidates = [
        CandidateSpec(
            "fidelity-baseline",
            "fidelity",
            0.50,
            0.0,
            "fidelity-current",
        )
    ]
    candidates.extend(
        CandidateSpec(
            _candidate_id("meshoptimizer", ratio, "meshopt-direct-v1"),
            "meshoptimizer",
            ratio,
            0.01,
            "meshopt-direct-v1",
            strategy="meshopt-direct-v1",
            update_vertices=False,
            transfer="direct-v1",
        )
        for ratio in _INITIAL_RATIOS
    )
    return candidates


def position_remap_candidates() -> tuple[CandidateSpec, ...]:
    return tuple(
        CandidateSpec(
            _candidate_id("meshoptimizer", ratio, "meshopt-direct-position-v1"),
            "meshoptimizer", ratio, 0.01, "meshopt-direct-position-v1",
            strategy="meshopt-direct-position-v1", update_vertices=False, transfer="direct-v1",
        )
        for ratio in _INITIAL_RATIOS
    )


def blender_adaptive_candidates() -> tuple[CandidateSpec, ...]:
    """Compiler-aware Blender probes below the frozen b050 topology target.

    Stable-region overrides are introduced only by measured gate recovery; this
    deliberately avoids the legacy filename-token floors that silently raised many
    bodygroups to 0.90-0.98.
    """
    return tuple(
        CandidateSpec(
            _candidate_id("blender", ratio),
            "blender", ratio, 0.0, "blender-adaptive-v1",
            strategy="blender-adaptive-v1", update_vertices=True,
            transfer="blender-native-v1",
        )
        for ratio in _BLENDER_ADAPTIVE_RATIOS
    )


def blender_importance_candidates() -> tuple[CandidateSpec, ...]:
    """Explicit R&D probes; never included in the production/default schedule."""
    return tuple(
        CandidateSpec(
            _candidate_id("blender", ratio, "blender-importance-map-v1"),
            "blender", ratio, 0.0, "blender-importance-map-v1",
            strategy="blender-importance-map-v1", update_vertices=True,
            transfer="blender-native-v1",
        )
        for ratio in _BLENDER_IMPORTANCE_RATIOS
    )


def _fidelity_score(evaluation: CandidateEvaluation) -> float:
    return evaluation.visual.metrics.get("fidelity_score", 0.0)


def pareto_frontier(
    evaluations: list[CandidateEvaluation],
) -> list[CandidateEvaluation]:
    passing = [evaluation for evaluation in evaluations if evaluation.passed]
    frontier = []
    for candidate in passing:
        candidate_bytes = candidate.size.total_bytes
        candidate_score = _fidelity_score(candidate)
        dominated = any(
            other is not candidate
            and other.size.total_bytes <= candidate_bytes
            and _fidelity_score(other) >= candidate_score
            and (
                other.size.total_bytes < candidate_bytes
                or _fidelity_score(other) > candidate_score
            )
            for other in passing
        )
        if not dominated:
            frontier.append(candidate)
    return sorted(
        frontier,
        key=lambda item: (
            item.size.total_bytes,
            -_fidelity_score(item),
            item.spec.candidate_id,
        ),
    )


def select_winner(
    evaluations: list[CandidateEvaluation],
) -> CandidateEvaluation | None:
    passing = [evaluation for evaluation in evaluations if evaluation.passed]
    if not passing:
        return None
    return min(
        passing,
        key=lambda item: (
            item.size.total_bytes,
            -_fidelity_score(item),
            item.spec.candidate_id,
        ),
    )


def _trail_should_stop_for_marginal_saving(
    evaluations: list[CandidateEvaluation], budget: SearchBudget
) -> bool:
    if not evaluations or not evaluations[-1].passed:
        return False
    previous = next(
        (item for item in reversed(evaluations[:-1]) if item.passed),
        None,
    )
    if previous is None:
        return False
    if previous.size.total_bytes <= 0:
        return True
    saving = (
        previous.size.total_bytes - evaluations[-1].size.total_bytes
    ) / previous.size.total_bytes
    return saving < budget.min_marginal_saving


def _regional_recovery(
    evaluations: list[CandidateEvaluation], used_ids: set[str]
) -> CandidateSpec | None:
    """Add or raise one failed region while retaining the complete recovery set."""
    failed = next(
        (evaluation for evaluation in reversed(evaluations) if not evaluation.passed),
        None,
    )
    if (
        failed is None
        or not (
            failed.spec.engine == "meshoptimizer"
            or (
                failed.spec.engine == "blender"
                and failed.spec.strategy == "blender-adaptive-v1"
            )
        )
        or not failed.structural.passed
        or failed.visual.passed
        or not failed.visual.worst_scope
    ):
        return None

    contract = _recovery_key(failed.spec)
    passing_donors = [
        evaluation
        for evaluation in evaluations
        if evaluation.passed
        and _recovery_key(evaluation.spec) == contract
        and evaluation.spec.target_ratio > failed.spec.target_ratio
    ]
    if not passing_donors:
        return None

    parsed_scope = parse_region_scope(failed.visual.worst_scope)
    if parsed_scope is None:
        return None
    region_key, _pose = parsed_scope
    existing: dict[str, float] = {}
    for key, ratio in failed.spec.region_overrides:
        if key in existing or ratio < failed.spec.target_ratio:
            return None
        existing[key] = ratio
    current_ratio = existing.get(region_key, failed.spec.target_ratio)
    effective_ratios = set()
    for donor in passing_donors:
        donor_overrides = dict(donor.spec.region_overrides)
        if len(donor_overrides) != len(donor.spec.region_overrides):
            continue
        effective_ratios.add(
            donor_overrides.get(region_key, donor.spec.target_ratio)
        )
    donor_ratio = next(
        (ratio for ratio in sorted(effective_ratios) if ratio > current_ratio), None
    )
    if donor_ratio is None:
        return None
    existing[region_key] = donor_ratio
    overrides = tuple(sorted(existing.items(), key=lambda item: item[0]))
    identity = {
        "contract": contract,
        "target_ratio": failed.spec.target_ratio,
        "region_overrides": overrides,
    }
    encoded = json.dumps(
        identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    override_hash = hashlib.sha256(encoded).hexdigest()
    candidate_id = (
        f"{_candidate_id(failed.spec.engine, failed.spec.target_ratio, failed.spec.strategy)}"
        f"-regions-{override_hash}"
    )
    if candidate_id in used_ids:
        return None
    return CandidateSpec(
        candidate_id,
        failed.spec.engine,
        failed.spec.target_ratio,
        failed.spec.target_error,
        failed.spec.repair_profile,
        overrides,
        strategy=failed.spec.strategy,
        update_vertices=failed.spec.update_vertices,
        transfer=failed.spec.transfer,
    )


def _narrowest_bracket(
    evaluations: list[CandidateEvaluation],
) -> tuple[CandidateEvaluation, CandidateEvaluation] | None:
    pairs = [
        (passing, failed)
        for passing in evaluations
        if passing.passed and passing.spec.engine != "fidelity"
        for failed in evaluations
        if not failed.passed
        and failed.spec.engine == passing.spec.engine
        and failed.spec.target_ratio < passing.spec.target_ratio
    ]
    if not pairs:
        return None
    return min(
        pairs,
        key=lambda pair: (
            pair[0].spec.target_ratio - pair[1].spec.target_ratio,
            pair[0].spec.engine,
            pair[1].spec.target_ratio,
            pair[0].spec.target_ratio,
            pair[0].spec.candidate_id,
            pair[1].spec.candidate_id,
        ),
    )


def choose_next(
    evaluations: list[CandidateEvaluation],
    budget: SearchBudget,
    *,
    initial: Sequence[CandidateSpec] | None = None,
    attempted_ids: set[str] | None = None,
) -> CandidateSpec | None:
    attempted = set(attempted_ids or ())
    if max(len(evaluations), len(attempted)) >= budget.max_candidates:
        return None
    used_ids = {evaluation.spec.candidate_id for evaluation in evaluations} | attempted

    schedule = tuple(initial) if initial is not None else tuple(initial_candidates())
    ordered_keys = list(dict.fromkeys(_trail_key(item) for item in schedule))
    for evaluation in evaluations:
        key = _trail_key(evaluation.spec)
        if key not in ordered_keys:
            ordered_keys.append(key)
    trails = {
        key: [item for item in evaluations if _trail_key(item.spec) == key]
        for key in ordered_keys
    }
    recovery_specs = (*schedule, *(evaluation.spec for evaluation in evaluations))
    recovery_keys = list(dict.fromkeys(_recovery_key(item) for item in recovery_specs))
    recovery_trails = {
        key: [item for item in evaluations if _recovery_key(item.spec) == key]
        for key in recovery_keys
    }
    retired: set[tuple[object, ...]] = set()
    engine_trails: dict[str, int] = {}
    for key in ordered_keys:
        engine_trails[str(key[0])] = engine_trails.get(str(key[0]), 0) + 1

    # Recovery and boundary refinement stay inside one comparable strategy trail.
    for key in recovery_keys:
        trail = recovery_trails[key]
        recovery = _regional_recovery(trail, used_ids)
        if recovery is not None:
            return recovery
    for key in ordered_keys:
        trail = trails[key]
        bracket = _narrowest_bracket(trail)
        if bracket is None:
            continue
        passing, failed = bracket
        interval = passing.spec.target_ratio - failed.spec.target_ratio
        if interval < budget.min_ratio_step:
            retired.add(key)
            continue
        midpoint = round((passing.spec.target_ratio + failed.spec.target_ratio) / 2, 6)
        if not failed.spec.target_ratio < midpoint < passing.spec.target_ratio:
            retired.add(key)
            continue
        candidate_id = _candidate_id(passing.spec.engine, midpoint, passing.spec.strategy)
        if engine_trails[passing.spec.engine] > 1:
            candidate_id += "-s" + _strategy_suffix(key)
        if midpoint >= _MIN_RATIO and candidate_id not in used_ids:
            return CandidateSpec(
                candidate_id,
                passing.spec.engine,
                midpoint,
                passing.spec.target_error,
                passing.spec.repair_profile,
                passing.spec.region_overrides,
                strategy=passing.spec.strategy,
                update_vertices=passing.spec.update_vertices,
                transfer=passing.spec.transfer,
            )
        retired.add(key)

    for key in ordered_keys:
        if _trail_should_stop_for_marginal_saving(trails[key], budget):
            retired.add(key)
    return next(
        (
            candidate for candidate in schedule
            if _trail_key(candidate) not in retired and candidate.candidate_id not in used_ids
        ),
        None,
    )
