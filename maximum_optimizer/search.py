from __future__ import annotations

import hashlib

from maximum_optimizer.domain import CandidateEvaluation, CandidateSpec, SearchBudget
from maximum_optimizer.regions import parse_region_scope


_INITIAL_RATIOS = (0.75, 0.50, 0.35, 0.25, 0.15, 0.10, 0.05)
_MIN_RATIO = 0.01


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


def _should_stop_for_marginal_saving(
    evaluations: list[CandidateEvaluation], budget: SearchBudget
) -> bool:
    if not evaluations or not evaluations[-1].passed:
        return False
    previous_winner = select_winner(evaluations[:-1])
    if previous_winner is None:
        return False
    previous_bytes = previous_winner.size.total_bytes
    if previous_bytes <= 0:
        return True
    current_winner = select_winner(evaluations)
    if current_winner is None:
        return False
    saving = (previous_bytes - current_winner.size.total_bytes) / previous_bytes
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
        or failed.spec.engine == "fidelity"
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
    evaluations: list[CandidateEvaluation], budget: SearchBudget
) -> CandidateSpec | None:
    if len(evaluations) >= budget.max_candidates:
        return None
    used_ids = {evaluation.spec.candidate_id for evaluation in evaluations}

    recovery = _regional_recovery(evaluations, used_ids)
    if recovery is not None:
        return recovery

    if _should_stop_for_marginal_saving(evaluations, budget):
        return None

    bracket = _narrowest_bracket(evaluations)
    if bracket is not None:
        passing, failed = bracket
        interval = passing.spec.target_ratio - failed.spec.target_ratio
        if interval < budget.min_ratio_step:
            return None
        midpoint = round(
            (passing.spec.target_ratio + failed.spec.target_ratio) / 2,
            6,
        )
        if not failed.spec.target_ratio < midpoint < passing.spec.target_ratio:
            return None
        candidate_id = _candidate_id(passing.spec.engine, midpoint)
        if midpoint < _MIN_RATIO or candidate_id in used_ids:
            return None
        return CandidateSpec(
            candidate_id,
            passing.spec.engine,
            midpoint,
            passing.spec.target_error,
            passing.spec.repair_profile,
        )

    return next(
        (
            candidate
            for candidate in initial_candidates()
            if candidate.candidate_id not in used_ids
        ),
        None,
    )
