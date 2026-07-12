from __future__ import annotations

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
    blender_adaptive_candidates,
    blender_importance_candidates,
    choose_next,
    initial_candidates,
    position_remap_candidates,
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
    strategy: str = "legacy-v1",
    repair_profile: str = "transfer-v1",
    transfer: str = "projection-v1",
    target_error: float = 0.01,
    update_vertices: bool = True,
) -> CandidateEvaluation:
    visual_metrics = {} if fidelity_score is None else {"fidelity_score": fidelity_score}
    spec = CandidateSpec(
        candidate_id or f"attempt-{ratio:.6f}-{len(region_overrides)}",
        engine,
        ratio,
        target_error,
        repair_profile,
        region_overrides,
        strategy=strategy,
        update_vertices=update_vertices,
        transfer=transfer,
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
    def test_blender_adaptive_schedule_searches_below_frozen_half_ratio(self):
        candidates = blender_adaptive_candidates()
        self.assertEqual(tuple(item.target_ratio for item in candidates), (0.45, 0.40, 0.35, 0.30, 0.25))
        self.assertTrue(all(item.engine == "blender" for item in candidates))
        self.assertTrue(all(item.strategy == "blender-adaptive-v1" for item in candidates))

    def test_blender_importance_schedule_is_more_aggressive_and_r_and_d_only(self):
        candidates = blender_importance_candidates()
        self.assertEqual(tuple(item.target_ratio for item in candidates), (0.35, 0.3, 0.25, 0.2))
        self.assertTrue(all(item.strategy == "blender-importance-map-v1" for item in candidates))
        self.assertTrue(all(item.transfer == "blender-native-v1" for item in candidates))
        self.assertTrue(all(item.transfer == "blender-native-v1" for item in candidates))

    def test_blender_adaptive_visual_failure_generates_stable_region_recovery(self):
        region = "r-" + "a" * 64
        passing = evaluation_at(
            0.45, True, candidate_id="adaptive-pass", engine="blender",
            strategy="blender-adaptive-v1", repair_profile="blender-adaptive-v1",
            transfer="blender-native-v1", target_error=0.0,
        )
        failed = evaluation_at(
            0.35, False, candidate_id="adaptive-fail", engine="blender",
            worst_scope=f"{region}/bind", strategy="blender-adaptive-v1",
            repair_profile="blender-adaptive-v1", transfer="blender-native-v1", target_error=0.0,
        )

        nxt = choose_next((passing, failed), SearchBudget.experimental_default(), initial=())

        self.assertEqual(nxt.engine, "blender")
        self.assertEqual(nxt.region_overrides, ((region, 0.45),))
        self.assertEqual(nxt.strategy, "blender-adaptive-v1")

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
            "meshopt-direct-r085",
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
                ("meshopt-direct-r085", "meshoptimizer", 0.85),
                ("meshopt-direct-r070", "meshoptimizer", 0.70),
                ("meshopt-direct-r055", "meshoptimizer", 0.55),
                ("meshopt-direct-r040", "meshoptimizer", 0.40),
                ("meshopt-direct-r025", "meshoptimizer", 0.25),
            ],
        )
        self.assertEqual(candidates[0].target_error, 0.0)
        self.assertEqual(candidates[0].repair_profile, "fidelity-current")
        self.assertTrue(
            all(item.target_error == 0.01 for item in candidates[1:])
        )
        self.assertTrue(
            all(item.repair_profile == "meshopt-direct-v1" for item in candidates[1:])
        )
        self.assertTrue(all(item.strategy == "meshopt-direct-v1" for item in candidates[1:]))
        self.assertTrue(all(item.update_vertices is False for item in candidates[1:]))
        self.assertTrue(all(item.transfer == "direct-v1" for item in candidates[1:]))
        self.assertFalse(any(item.strategy == "meshopt-direct-position-v1" for item in candidates))
        self.assertTrue(all(item.strategy == "meshopt-direct-position-v1" for item in position_remap_candidates()))

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

        self.assertEqual(nxt.target_ratio, 0.25)
        self.assertEqual(nxt.region_overrides, ((region, 0.50),))
        self.assertIn("-regions-", nxt.candidate_id)

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

    def test_regional_recovery_accumulates_second_and_third_failed_regions(self):
        grille = "r-" + "1" * 64
        headlight = "r-" + "2" * 64
        mirror = "r-" + "3" * 64
        common = {
            "engine": "blender", "strategy": "blender-adaptive-v1",
            "repair_profile": "blender-adaptive-v1", "transfer": "blender-native-v1",
            "target_error": 0.0,
        }
        evaluations = [
            evaluation_at(0.35, True, candidate_id="donor", **common),
            evaluation_at(0.30, False, candidate_id="global-fail", worst_scope=f"{grille}/bind", **common),
        ]

        first = choose_next(evaluations, SearchBudget.experimental_default(), initial=())
        self.assertEqual(first.region_overrides, ((grille, 0.35),))
        evaluations.append(evaluation_at(
            0.30, False, candidate_id=first.candidate_id,
            worst_scope=f"{headlight}/bind", region_overrides=first.region_overrides, **common,
        ))
        second = choose_next(evaluations, SearchBudget.experimental_default(), initial=())
        self.assertEqual(second.region_overrides, ((grille, 0.35), (headlight, 0.35)))
        evaluations.append(evaluation_at(
            0.30, False, candidate_id=second.candidate_id,
            worst_scope=f"{mirror}/bind", region_overrides=second.region_overrides, **common,
        ))
        third = choose_next(evaluations, SearchBudget.experimental_default(), initial=())
        self.assertEqual(
            third.region_overrides,
            ((grille, 0.35), (headlight, 0.35), (mirror, 0.35)),
        )
        self.assertEqual(len({first.candidate_id, second.candidate_id, third.candidate_id}), 3)

    def test_regional_recovery_raises_existing_region_without_duplicate_and_stops_at_largest_donor(self):
        region = "r-" + "4" * 64
        evaluations = [
            evaluation_at(0.35, True, candidate_id="near-donor"),
            evaluation_at(0.50, True, candidate_id="far-donor"),
            evaluation_at(
                0.25, False, candidate_id="repaired-near", worst_scope=f"{region}/bind",
                region_overrides=((region, 0.35),),
            ),
        ]
        raised = choose_next(evaluations, SearchBudget.experimental_default(), initial=())
        self.assertEqual(raised.region_overrides, ((region, 0.50),))
        evaluations.append(evaluation_at(
            0.25, False, candidate_id=raised.candidate_id, worst_scope=f"{region}/bind",
            region_overrides=raised.region_overrides,
        ))
        self.assertIsNone(choose_next(evaluations, SearchBudget.experimental_default(), initial=()))

    def test_regional_recovery_donor_must_match_complete_strategy_contract(self):
        region = "r-" + "5" * 64
        evaluations = [
            evaluation_at(
                0.35, True, candidate_id="wrong-transfer", strategy="strategy-a",
                repair_profile="repair-a", transfer="other-transfer",
            ),
            evaluation_at(
                0.40, True, candidate_id="compatible", strategy="strategy-a",
                repair_profile="repair-a", transfer="transfer-a",
            ),
            evaluation_at(
                0.25, False, candidate_id="failure", worst_scope=f"{region}/bind",
                strategy="strategy-a", repair_profile="repair-a", transfer="transfer-a",
            ),
        ]
        recovered = choose_next(evaluations, SearchBudget.experimental_default(), initial=())
        self.assertEqual(recovered.region_overrides, ((region, 0.40),))

    def test_regional_recovery_donor_matching_ignores_donor_region_overrides(self):
        donor_region = "r-" + "a" * 64
        failed_region = "r-" + "b" * 64
        evaluations = [
            evaluation_at(
                0.35, True, candidate_id="regional-donor",
                region_overrides=((donor_region, 0.50),),
            ),
            evaluation_at(
                0.30, False, candidate_id="failure", worst_scope=f"{failed_region}/bind",
            ),
        ]
        recovered = choose_next(evaluations, SearchBudget.experimental_default(), initial=())
        self.assertEqual(recovered.region_overrides, ((failed_region, 0.35),))

    def test_regional_recovery_uses_donor_effective_ratio_for_same_region(self):
        region = "r-" + "d" * 64
        donor = evaluation_at(
            0.35, True, candidate_id="regional-donor",
            region_overrides=((region, 0.50),),
        )
        failure = evaluation_at(
            0.30, False, candidate_id="global-failure", worst_scope=f"{region}/bind",
        )

        recovered = choose_next(
            [donor, failure], SearchBudget.experimental_default(), initial=(),
        )

        self.assertEqual(recovered.region_overrides, ((region, 0.50),))
        repeated_failure = evaluation_at(
            0.30, False, candidate_id=recovered.candidate_id,
            worst_scope=f"{region}/bind", region_overrides=recovered.region_overrides,
        )
        after_maximum = choose_next(
            [donor, failure, repeated_failure],
            SearchBudget.experimental_default(), initial=(),
        )
        self.assertNotIn("-regions-", after_maximum.candidate_id)
        self.assertEqual(after_maximum.region_overrides, ((region, 0.50),))

    def test_regional_recovery_accepts_same_target_pass_with_higher_effective_region(self):
        region = "r-" + "e" * 64
        donor = evaluation_at(
            0.30, True, candidate_id="same-target-pass",
            region_overrides=((region, 0.50),),
        )
        failed = evaluation_at(
            0.30, False, candidate_id="same-target-fail",
            worst_scope=f"{region}/bind", region_overrides=((region, 0.35),),
        )

        recovered = choose_next(
            [donor, failed], SearchBudget.experimental_default(), initial=(),
        )

        self.assertEqual(recovered.region_overrides, ((region, 0.50),))

    def test_bracket_and_recovery_never_mix_strategies(self):
        region = "r-" + "c" * 64
        evaluations = [
            evaluation_at(
                0.50, True, candidate_id="strategy-a-pass", strategy="strategy-a",
                repair_profile="repair", transfer="transfer",
            ),
            evaluation_at(
                0.25, False, candidate_id="strategy-b-fail", strategy="strategy-b",
                repair_profile="repair", transfer="transfer", worst_scope=f"{region}/bind",
            ),
        ]
        self.assertIsNone(choose_next(
            evaluations, SearchBudget.experimental_default(), initial=(),
        ))

    def test_regional_recovery_ids_are_deterministic_for_full_set_and_strategy_isolated(self):
        first = "r-" + "6" * 64
        second = "r-" + "7" * 64
        third = "r-" + "9" * 64

        def recover(strategy, overrides):
            return choose_next([
                evaluation_at(
                    0.50, True, candidate_id=f"{strategy}-pass", strategy=strategy,
                    repair_profile="repair", transfer="transfer",
                ),
                evaluation_at(
                    0.25, False, candidate_id=f"{strategy}-fail",
                    worst_scope=f"{second}/bind", region_overrides=overrides,
                    strategy=strategy, repair_profile="repair", transfer="transfer",
                ),
            ], SearchBudget.experimental_default(), initial=())

        ordered = recover("strategy-a", ((first, 0.50), (third, 0.50)))
        reordered = recover("strategy-a", ((third, 0.50), (first, 0.50)))
        other_strategy = recover("strategy-b", ((first, 0.50), (third, 0.50)))
        self.assertEqual(ordered.candidate_id, reordered.candidate_id)
        self.assertNotEqual(ordered.candidate_id, other_strategy.candidate_id)
        self.assertEqual(
            ordered.region_overrides,
            ((first, 0.50), (second, 0.50), (third, 0.50)),
        )

    def test_regional_recovery_fails_closed_for_scope_structure_budget_and_attempted_loop(self):
        region = "r-" + "8" * 64
        donor = evaluation_at(0.50, True, candidate_id="donor")
        malformed = evaluation_at(0.49, False, candidate_id="malformed", worst_scope="not-a-region/bind")
        structural = evaluation_at(
            0.49, False, candidate_id="structural", worst_scope=f"{region}/bind",
            structural_passed=False,
        )
        self.assertIsNone(choose_next([donor, malformed], SearchBudget.experimental_default(), initial=()))
        self.assertIsNone(choose_next([donor, structural], SearchBudget.experimental_default(), initial=()))
        valid = evaluation_at(0.49, False, candidate_id="valid", worst_scope=f"{region}/bind")
        generated = choose_next([donor, valid], SearchBudget.experimental_default(), initial=())
        self.assertIsNone(choose_next(
            [donor, valid], SearchBudget.experimental_default(), initial=(),
            attempted_ids={generated.candidate_id},
        ))
        self.assertIsNone(choose_next(
            [donor, valid], SearchBudget(max_candidates=2, min_ratio_step=0.025, min_marginal_saving=0.0),
            initial=(),
        ))

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
