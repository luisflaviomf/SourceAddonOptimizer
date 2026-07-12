from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from maximum_optimizer.domain import (
    ArtifactStat,
    CandidateEvaluation,
    CandidateSpec,
    CompiledSizeSnapshot,
    SearchBudget,
    ValidationResult,
)
from maximum_optimizer.search import (
    choose_next,
    initial_candidates,
    pareto_frontier,
    select_winner,
)


def evaluation_at(
    ratio: float,
    passed: bool,
    *,
    total_bytes: int = 100,
    fidelity_score: float | None = None,
    candidate_id: str | None = None,
    engine: str = "meshoptimizer",
    structural_passed: bool = True,
    worst_scope: str = "",
    region_overrides: tuple[tuple[str, float], ...] = (),
) -> CandidateEvaluation:
    visual_metrics = {} if fidelity_score is None else {"fidelity_score": fidelity_score}
    spec = CandidateSpec(
        candidate_id or f"attempt-{ratio:.6f}-{len(region_overrides)}",
        engine,
        ratio,
        0.01,
        "transfer-v1",
        region_overrides,
    )
    artifact = ArtifactStat("model.mdl", "mdl", total_bytes)
    size = CompiledSizeSnapshot(
        Path("compiled"),
        total_bytes,
        {"mdl": total_bytes},
        {},
        (artifact,),
    )
    return CandidateEvaluation(
        spec,
        size,
        ValidationResult(structural_passed),
        ValidationResult(passed, metrics=visual_metrics, worst_scope=worst_scope),
        Path("compiled"),
    )


class SearchTests(unittest.TestCase):
    def test_custom_schedule_compares_marginal_savings_within_engine_trail(self):
        schedule = (
            CandidateSpec("fidelity-baseline", "fidelity", 0.5, 0.0, "fidelity"),
            CandidateSpec("blender-r075", "blender", 0.75, 0.01, "transfer"),
            CandidateSpec("blender-r050", "blender", 0.50, 0.01, "transfer"),
            CandidateSpec("blender-r025", "blender", 0.25, 0.01, "transfer"),
            CandidateSpec("mesh-r075", "meshoptimizer", 0.75, 0.01, "transfer"),
        )
        budget = SearchBudget(10, 0.025, 0.05)
        evaluations = [
            evaluation_at(0.5, True, total_bytes=100, candidate_id="fidelity-baseline", engine="fidelity"),
            evaluation_at(0.75, True, total_bytes=130, candidate_id="blender-r075", engine="blender"),
            evaluation_at(0.50, True, total_bytes=120, candidate_id="blender-r050", engine="blender"),
        ]

        self.assertEqual(
            choose_next(evaluations, budget, initial=schedule).candidate_id,
            "blender-r025",
        )
        evaluations.append(
            evaluation_at(0.25, True, total_bytes=119, candidate_id="blender-r025", engine="blender")
        )
        self.assertEqual(
            choose_next(evaluations, budget, initial=schedule).candidate_id,
            "mesh-r075",
        )

    def test_regional_recovery_is_only_generated_for_capable_meshoptimizer_engine(self):
        region = "r-" + "4" * 64
        schedule = (
            CandidateSpec("blender-r050", "blender", 0.5, 0.01, "transfer"),
            CandidateSpec("mesh-r050", "meshoptimizer", 0.5, 0.01, "transfer"),
        )
        evaluations = [
            evaluation_at(0.75, True, candidate_id="blender-pass", engine="blender"),
            evaluation_at(
                0.25,
                False,
                candidate_id="blender-fail",
                engine="blender",
                worst_scope=f"{region}/bind",
            ),
        ]

        next_candidate = choose_next(evaluations, SearchBudget.experimental_default(), initial=schedule)

        self.assertFalse(next_candidate.region_overrides)
        self.assertNotIn("region", next_candidate.candidate_id)

    def test_default_schedule_does_not_stop_when_unscheduled_engine_trail_is_worse(self):
        evaluations = [
            evaluation_at(0.5, True, total_bytes=100, candidate_id="fidelity-baseline", engine="fidelity"),
            evaluation_at(0.75, True, total_bytes=101, candidate_id="external-blender", engine="blender"),
        ]
        self.assertEqual(
            choose_next(evaluations, SearchBudget.experimental_default()).candidate_id,
            "meshopt-direct-position-r085",
        )

    def test_profiles_and_regional_overrides_are_separate_search_trails(self):
        schedule = (
            CandidateSpec("a-next", "blender", 0.25, 0.01, "profile-a"),
            CandidateSpec("b-next", "blender", 0.25, 0.01, "profile-b"),
            CandidateSpec("mesh-next", "meshoptimizer", 0.5, 0.01, "mesh"),
        )
        evaluations = [
            CandidateEvaluation(
                CandidateSpec("a-pass", "blender", 0.75, 0.01, "profile-a"),
                evaluation_at(0.75, True, total_bytes=100).size,
                ValidationResult(True), ValidationResult(True), Path("compiled"),
            ),
            CandidateEvaluation(
                CandidateSpec("b-pass", "blender", 0.50, 0.01, "profile-b"),
                evaluation_at(0.50, True, total_bytes=100).size,
                ValidationResult(True), ValidationResult(True), Path("compiled"),
            ),
        ]
        self.assertEqual(
            choose_next(evaluations, SearchBudget(10, 0.025, 0.05), initial=schedule).candidate_id,
            "a-next",
        )

        region = "r-" + "5" * 64
        base = evaluation_at(0.5, True, candidate_id="base", engine="meshoptimizer")
        regional = evaluation_at(
            0.25,
            False,
            candidate_id="regional",
            engine="meshoptimizer",
            region_overrides=((region, 0.5),),
        )
        next_candidate = choose_next(
            [base, regional],
            SearchBudget.experimental_default(),
            initial=(CandidateSpec("scheduled", "meshoptimizer", 0.1, 0.01, "transfer-v1"),),
        )
        self.assertEqual(next_candidate.candidate_id, "scheduled")
    def test_initial_candidates_have_exact_order_and_profiles(self):
        candidates = initial_candidates()

        self.assertEqual(
            [(item.candidate_id, item.engine, item.target_ratio) for item in candidates],
            [
                ("fidelity-baseline", "fidelity", 0.50),
                ("meshopt-direct-position-r085", "meshoptimizer", 0.85),
                ("meshopt-direct-position-r070", "meshoptimizer", 0.70),
                ("meshopt-direct-position-r055", "meshoptimizer", 0.55),
                ("meshopt-direct-position-r040", "meshoptimizer", 0.40),
                ("meshopt-direct-position-r025", "meshoptimizer", 0.25),
            ],
        )
        self.assertEqual(candidates[0].target_error, 0.0)
        self.assertEqual(candidates[0].repair_profile, "fidelity-current")
        self.assertTrue(
            all(item.target_error == 0.01 for item in candidates[1:])
        )
        self.assertTrue(
            all(item.repair_profile == "meshopt-direct-position-v1" for item in candidates[1:])
        )
        self.assertTrue(all(item.strategy == "meshopt-direct-position-v1" for item in candidates[1:]))
        self.assertTrue(all(item.update_vertices is False for item in candidates[1:]))
        self.assertTrue(all(item.transfer == "direct-v1" for item in candidates[1:]))

    def test_pareto_frontier_excludes_failures_and_dominated_candidates(self):
        evaluations = [
            evaluation_at(0.75, True, total_bytes=80, fidelity_score=0.80, candidate_id="b"),
            evaluation_at(0.50, True, total_bytes=60, fidelity_score=0.70, candidate_id="a"),
            evaluation_at(0.35, True, total_bytes=70, fidelity_score=0.60, candidate_id="dominated"),
            evaluation_at(0.25, False, total_bytes=40, fidelity_score=0.95, candidate_id="visual-fail"),
            evaluation_at(
                0.15,
                True,
                total_bytes=30,
                fidelity_score=0.99,
                candidate_id="structural-fail",
                structural_passed=False,
            ),
        ]

        self.assertEqual(
            [item.spec.candidate_id for item in pareto_frontier(evaluations)],
            ["a", "b"],
        )

    def test_pareto_frontier_defaults_missing_fidelity_score_to_zero(self):
        evaluations = [
            evaluation_at(0.50, True, total_bytes=60, candidate_id="missing"),
            evaluation_at(0.75, True, total_bytes=60, fidelity_score=-0.1, candidate_id="negative"),
        ]

        self.assertEqual(
            [item.spec.candidate_id for item in pareto_frontier(evaluations)],
            ["missing"],
        )

    def test_pareto_frontier_keeps_equal_points_in_candidate_id_order(self):
        evaluations = [
            evaluation_at(0.50, True, total_bytes=60, fidelity_score=0.8, candidate_id="z"),
            evaluation_at(0.75, True, total_bytes=60, fidelity_score=0.8, candidate_id="a"),
        ]

        self.assertEqual(
            [item.spec.candidate_id for item in pareto_frontier(evaluations)],
            ["a", "z"],
        )

    def test_smallest_passing_candidate_wins_with_deterministic_ties(self):
        evaluations = [
            evaluation_at(0.75, True, total_bytes=100, fidelity_score=0.99, candidate_id="large"),
            evaluation_at(0.50, True, total_bytes=60, fidelity_score=0.70, candidate_id="lower-score"),
            evaluation_at(0.35, True, total_bytes=60, fidelity_score=0.80, candidate_id="z"),
            evaluation_at(0.25, True, total_bytes=60, fidelity_score=0.80, candidate_id="a"),
            evaluation_at(0.15, False, total_bytes=40, fidelity_score=1.0, candidate_id="failed"),
        ]

        self.assertEqual(select_winner(evaluations).spec.candidate_id, "a")
        self.assertIsNone(select_winner([evaluations[-1]]))

    def test_quality_break_creates_rounded_midpoint_from_narrowest_bracket(self):
        evaluations = [
            evaluation_at(0.75, True, candidate_id="pass-wide"),
            evaluation_at(0.25, False, candidate_id="fail-wide"),
            evaluation_at(0.500001, True, candidate_id="pass-near"),
            evaluation_at(0.333333, False, candidate_id="fail-near"),
        ]

        nxt = choose_next(
            evaluations,
            SearchBudget(max_candidates=18, min_ratio_step=0.01, min_marginal_saving=0.0),
        )

        self.assertEqual(nxt.target_ratio, 0.416667)
        self.assertEqual(nxt.engine, "meshoptimizer")
        self.assertEqual(nxt.target_error, 0.01)
        self.assertEqual(nxt.repair_profile, "transfer-v1")

    def test_brackets_only_candidates_from_the_same_engine(self):
        evaluations = [
            evaluation_at(0.50, True, candidate_id="mesh-pass"),
            evaluation_at(0.25, False, candidate_id="blender-fail", engine="blender"),
        ]

        nxt = choose_next(evaluations, SearchBudget.experimental_default())

        self.assertEqual(nxt.candidate_id, "fidelity-baseline")

    def test_choose_next_stops_at_budget(self):
        evaluations = [evaluation_at(0.50, True, candidate_id="only")]
        budget = SearchBudget(max_candidates=1, min_ratio_step=0.025, min_marginal_saving=0.005)

        self.assertIsNone(choose_next(evaluations, budget, initial=()))

    def test_choose_next_does_not_generate_below_ratio_floor(self):
        evaluations = [
            evaluation_at(0.011, True, candidate_id="pass"),
            evaluation_at(0.005, False, candidate_id="fail"),
        ]
        budget = SearchBudget(max_candidates=18, min_ratio_step=0.001, min_marginal_saving=0.0)

        self.assertIsNone(choose_next(evaluations, budget, initial=()))

    def test_choose_next_does_not_repeat_a_midpoint_candidate_id(self):
        evaluations = [
            evaluation_at(
                0.90,
                False,
                candidate_id="meshopt-r0375",
                engine="blender",
            ),
            evaluation_at(0.50, True, candidate_id="pass"),
            evaluation_at(0.25, False, candidate_id="fail"),
        ]

        self.assertIsNone(
            choose_next(evaluations, SearchBudget.experimental_default(), initial=())
        )

    def test_choose_next_stops_when_narrowest_bracket_is_too_small(self):
        evaluations = [
            evaluation_at(0.50, True, candidate_id="pass"),
            evaluation_at(0.49, False, candidate_id="fail"),
        ]

        self.assertIsNone(choose_next(evaluations, SearchBudget.experimental_default(), initial=()))

    def test_choose_next_rejects_a_rounded_midpoint_that_collapses_an_endpoint(self):
        evaluations = [
            evaluation_at(0.3333334, True, candidate_id="noncanonical-pass"),
            evaluation_at(0.3333331, False, candidate_id="noncanonical-fail"),
        ]
        budget = SearchBudget(
            max_candidates=18,
            min_ratio_step=0.00000001,
            min_marginal_saving=0.0,
        )

        self.assertIsNone(choose_next(evaluations, budget, initial=()))

    def test_marginal_stop_uses_previous_winner_and_only_after_a_pass(self):
        budget = SearchBudget(max_candidates=18, min_ratio_step=0.025, min_marginal_saving=0.05)
        low_saving = [
            evaluation_at(0.50, True, total_bytes=100, candidate_id="previous"),
            evaluation_at(0.40, True, total_bytes=96, candidate_id="current"),
        ]
        latest_failure = [
            evaluation_at(0.50, True, total_bytes=100, candidate_id="previous"),
            evaluation_at(0.25, False, total_bytes=80, candidate_id="failure"),
        ]

        self.assertIsNone(choose_next(low_saving, budget, initial=()))
        pending = (
            CandidateSpec("pending", "meshoptimizer", 0.15, 0.01, "transfer-v1"),
        )
        self.assertIsNotNone(choose_next(latest_failure, budget, initial=pending))

    def test_marginal_stop_protects_zero_previous_bytes(self):
        budget = SearchBudget(max_candidates=18, min_ratio_step=0.025, min_marginal_saving=0.05)
        evaluations = [
            evaluation_at(0.50, True, total_bytes=0, candidate_id="previous"),
            evaluation_at(0.40, True, total_bytes=0, candidate_id="current"),
        ]

        self.assertIsNone(choose_next(evaluations, budget, initial=()))

    def test_marginal_saving_equal_to_threshold_does_not_stop(self):
        budget = SearchBudget(
            max_candidates=18,
            min_ratio_step=0.025,
            min_marginal_saving=0.005,
        )
        evaluations = [
            evaluation_at(0.50, True, total_bytes=1000, candidate_id="previous"),
            evaluation_at(0.40, True, total_bytes=995, candidate_id="current"),
        ]

        nxt = choose_next(evaluations, budget)

        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.candidate_id, "fidelity-baseline")

    def test_recent_visual_failure_recovers_only_worst_scope_at_nearest_passing_ratio(self):
        region = "r-" + "1" * 64
        scope = f"{region}/bind"
        evaluations = [
            evaluation_at(0.75, True, candidate_id="far-pass"),
            evaluation_at(0.50, True, candidate_id="near-pass"),
            evaluation_at(0.25, False, candidate_id="failure", worst_scope=scope),
        ]

        nxt = choose_next(evaluations, SearchBudget.experimental_default())

        scope_hash = hashlib.sha256(region.encode("utf-8")).hexdigest()[:8]
        self.assertEqual(nxt.target_ratio, 0.25)
        self.assertEqual(nxt.region_overrides, ((region, 0.50),))
        self.assertIn(scope_hash, nxt.candidate_id)

    def test_pending_regional_recovery_precedes_marginal_stop(self):
        region = "r-" + "2" * 64
        scope = f"{region}/action"
        evaluations = [
            evaluation_at(0.75, True, total_bytes=1000, candidate_id="previous"),
            evaluation_at(
                0.25,
                False,
                total_bytes=900,
                candidate_id="regional-failure",
                worst_scope=scope,
            ),
            evaluation_at(0.50, True, total_bytes=999, candidate_id="newest-pass"),
        ]

        nxt = choose_next(evaluations, SearchBudget.experimental_default())

        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.target_ratio, 0.25)
        self.assertEqual(nxt.region_overrides, ((region, 0.50),))

    def test_regional_recovery_requires_no_existing_overrides_and_never_duplicates(self):
        region = "r-" + "3" * 64
        scope = f"{region}/bind"
        recovery_id = f"meshopt-r025-region-{hashlib.sha256(region.encode('utf-8')).hexdigest()[:8]}"
        evaluations = [
            evaluation_at(0.50, True, candidate_id="pass"),
            evaluation_at(0.25, False, candidate_id=recovery_id, worst_scope=scope),
            evaluation_at(
                0.25,
                False,
                candidate_id="already-repaired",
                worst_scope=scope,
                region_overrides=((region, 0.50),),
            ),
        ]

        nxt = choose_next(evaluations, SearchBudget.experimental_default())

        self.assertNotEqual(nxt.candidate_id, recovery_id)
        self.assertEqual(nxt.region_overrides, ())

    def test_without_bracket_or_recovery_returns_first_untried_initial_candidate(self):
        self.assertEqual(
            choose_next([], SearchBudget.experimental_default()),
            initial_candidates()[0],
        )
        fidelity = evaluation_at(
            0.50,
            True,
            candidate_id="fidelity-baseline",
            engine="fidelity",
        )

        self.assertEqual(
            choose_next([fidelity], SearchBudget.experimental_default()),
            initial_candidates()[1],
        )


if __name__ == "__main__":
    unittest.main()
