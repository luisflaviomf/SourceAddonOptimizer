from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from maximum_optimizer.domain import CandidateEvaluation, CandidateSpec, SearchBudget
from maximum_optimizer.regions import parse_region_scope


_INITIAL_RATIOS = (0.75, 0.50, 0.35, 0.25, 0.15, 0.10, 0.05)
_MIN_RATIO = 0.01


def _trail_key(spec: CandidateSpec) -> tuple[object, ...]:
    return (
        spec.engine,
        spec.target_error,
        spec.repair_profile,
        spec.region_overrides,
    )


def _strategy_suffix(key: tuple[object, ...]) -> str:
    raw = json.dumps(key, ensure_ascii=True, separators=(",", ":"), sort_keys=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _ratio_id(ratio: float) -> str:
    text = f"{round(ratio, 6):.6f}".rstrip("0")
    whole, fraction = text.split(".")
    fraction = fraction.ljust(2, "0")
    return f"{whole}{fraction}"


def _candidate_id(engine: str, ratio: float) -> str:
    prefix = "meshopt" if engine == "meshoptimizer" else engine
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
            _candidate_id("meshoptimizer", ratio),
            "meshoptimizer",
            ratio,
            0.01,
            "transfer-v1",
        )
        for ratio in _INITIAL_RATIOS
    )
    return candidates


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
    failed = next(
        (evaluation for evaluation in reversed(evaluations) if not evaluation.passed),
        None,
    )
    if (
        failed is None
        or failed.spec.engine != "meshoptimizer"
        or not failed.structural.passed
        or failed.visual.passed
        or not failed.visual.worst_scope
        or failed.spec.region_overrides
    ):
        return None

    passing_ratios = [
        evaluation.spec.target_ratio
        for evaluation in evaluations
        if evaluation.passed
        and evaluation.spec.engine == failed.spec.engine
        and evaluation.spec.target_ratio > failed.spec.target_ratio
    ]
    if not passing_ratios:
        return None

    parsed_scope = parse_region_scope(failed.visual.worst_scope)
    if parsed_scope is None:
        return None
    region_key, _pose = parsed_scope
    scope_hash = hashlib.sha256(region_key.encode("utf-8")).hexdigest()[:8]
    candidate_id = (
        f"{_candidate_id(failed.spec.engine, failed.spec.target_ratio)}"
        f"-region-{scope_hash}"
    )
    if candidate_id in used_ids:
        return None
    return CandidateSpec(
        candidate_id,
        failed.spec.engine,
        failed.spec.target_ratio,
        failed.spec.target_error,
        failed.spec.repair_profile,
        ((region_key, min(passing_ratios)),),
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
    retired: set[tuple[object, ...]] = set()
    engine_trails: dict[str, int] = {}
    for key in ordered_keys:
        engine_trails[str(key[0])] = engine_trails.get(str(key[0]), 0) + 1

    # Recovery and boundary refinement stay inside one comparable strategy trail.
    for key in ordered_keys:
        trail = trails[key]
        recovery = _regional_recovery(trail, used_ids)
        if recovery is not None:
            if engine_trails[recovery.engine] > 1:
                recovery = CandidateSpec(
                    recovery.candidate_id + "-s" + _strategy_suffix(key),
                    recovery.engine,
                    recovery.target_ratio,
                    recovery.target_error,
                    recovery.repair_profile,
                    recovery.region_overrides,
                )
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
        candidate_id = _candidate_id(passing.spec.engine, midpoint)
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
