from __future__ import annotations

from dataclasses import replace
import unittest

from maximum_optimizer.holdout_protocol import (
    BASE_LADDER,
    MAX_FOCUS_OCCURRENCES,
    MAX_FOCUS_REGIONS,
    MAX_REGION_OPTION_PROOFS,
    REGION_LADDER,
    REQUIRED_GATES,
    CompiledArtifact,
    ExactFocusRegion,
    HoldoutFreezeBindings,
    base_recipe,
    build_attempt,
    build_compiled_proof,
    build_focus_universe,
    build_focused_coverage,
    build_holdout_calibration_approval,
    build_holdout_run_evidence,
    build_local_region_option_proof,
    build_local_region_option_proof_cache,
    candidate_recipe_id,
    composition_recipe,
    exact_recipe,
    focus_universe_from_payload,
    focus_universe_payload,
    focused_coverage_from_payload,
    focused_coverage_payload,
    frozen_holdout_protocol,
    holdout_calibration_approval_from_payload,
    holdout_calibration_approval_payload,
    holdout_protocol_from_payload,
    holdout_protocol_payload,
    holdout_run_evidence_from_payload,
    holdout_run_evidence_payload,
    region_tournament_options,
    required_gates_for_stage,
    select_base_finalists,
    select_holdout_winner,
)


H = tuple(f"{index:064x}" for index in range(1, 32))
R0 = "r-" + "a" * 64
R1 = "r-" + "b" * 64
R2 = "r-" + "c" * 64
R3 = "r-" + "d" * 64


def _approval():
    return build_holdout_calibration_approval(
        calibration_bundle_sha256=H[0],
        whole_profile_sha256=H[1],
        focused_profile_sha256=H[2],
        profile_approval_sha256=H[3],
    )


def _bindings() -> HoldoutFreezeBindings:
    approval = _approval()
    return HoldoutFreezeBindings(
        calibration_approval=approval,
        reviewed_calibration_approval_sha256=approval.approval_sha256,
        optimizer_contract_sha256=H[4],
        renderer_sha256=H[5],
        compiler_sha256=H[6],
        toolchain_sha256=H[7],
    )


def _protocol():
    bindings = _bindings()
    return frozen_holdout_protocol(
        bindings,
        expected_calibration_approval_sha256=(
            bindings.reviewed_calibration_approval_sha256
        ),
    )


def _compiled(size: int, marker: int):
    first = size // 2
    return build_compiled_proof((
        CompiledArtifact("models/example.mdl", first, H[marker]),
        CompiledArtifact("models/example.vvd", size - first, H[marker + 1]),
    ))


class HoldoutProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = _protocol()
        self.input_commitment = H[20]
        self.family_commitment = H[21]

    def _attempt(
        self, recipe, size: int, marker: int, *, status: str = "authorized",
    ):
        stage = "base-terminal" if recipe.kind == "base" else "final-retained"
        required = required_gates_for_stage(stage)
        gates = tuple((name, H[(marker + index) % len(H)]) for index, name in enumerate(
            required
        ))
        universe = build_focus_universe(
            family_commitment_sha256=self.family_commitment,
            regions=(ExactFocusRegion(
                R0, "body.smd", H[28], H[29], 0, 1, "renderable",
            ),),
        )
        proof = build_local_region_option_proof(
            protocol=self.protocol,
            universe=universe,
            region_key=R0,
            candidate_source_sha256=H[28],
            option_identity_sha256=H[30],
            terminal_status="exact-unchanged",
        )
        coverage = build_focused_coverage(
            protocol=self.protocol, universe=universe, recipe=recipe, proofs=(proof,),
        )
        return build_attempt(
            protocol=self.protocol,
            input_commitment_sha256=self.input_commitment,
            family_commitment_sha256=self.family_commitment,
            recipe=recipe,
            compiled=_compiled(size, marker),
            attempt_stage=stage,
            terminal_status=status,
            gate_seals=gates if status == "authorized" else gates[:-1],
            failed_gate=None if status == "authorized" else required[-1],
            focused_coverage=coverage,
        )

    def test_protocol_is_exact_reparsable_and_contains_frozen_algorithm(self) -> None:
        self.assertEqual(
            self.protocol.focused_selector, "exact-source-union-all-regions-v1",
        )
        self.assertEqual(self.protocol.max_focus_regions, MAX_FOCUS_REGIONS)
        self.assertEqual(self.protocol.max_focus_occurrences, MAX_FOCUS_OCCURRENCES)
        self.assertEqual(
            self.protocol.max_region_option_proofs, MAX_REGION_OPTION_PROOFS,
        )
        self.assertEqual(
            self.protocol.focused_evaluation_mode,
            "isolated-once-per-region-option-v1",
        )
        self.assertFalse(self.protocol.focused_prefix_rerender)
        self.assertEqual(
            self.protocol.whole_visual_stages,
            ("base-terminal", "final-retained"),
        )
        self.assertEqual(self.protocol.finalist_base_count, 2)
        self.assertEqual(self.protocol.beam_width, 4)
        self.assertEqual(self.protocol.camera_count, 8)
        self.assertEqual(self.protocol.passes, ("textured", "clay"))
        self.assertEqual(self.protocol.base_ladder, BASE_LADDER)
        self.assertEqual(self.protocol.region_ladder, REGION_LADDER)
        self.assertEqual(
            holdout_protocol_from_payload(
                holdout_protocol_payload(self.protocol),
                expected_calibration_approval_sha256=(
                    self.protocol.bindings.reviewed_calibration_approval_sha256
                ),
            ),
            self.protocol,
        )

        payload = holdout_protocol_payload(self.protocol)
        payload["max_focus_regions"] = 3
        with self.assertRaisesRegex(ValueError, "frozen"):
            holdout_protocol_from_payload(
                payload,
                expected_calibration_approval_sha256=(
                    self.protocol.bindings.reviewed_calibration_approval_sha256
                ),
            )

    def test_all_region_universe_is_candidate_independent_and_risk_only_orders(self) -> None:
        universe = build_focus_universe(
            family_commitment_sha256=self.family_commitment,
            regions=(
                ExactFocusRegion(R0, "body.smd", H[8], H[9], 1, 2, "renderable"),
                ExactFocusRegion(R1, "glass.smd", H[10], H[11], 0, 1, "renderable"),
            ),
        )
        self.assertEqual(tuple(item.region_key for item in universe.regions), (R0, R1))
        self.assertEqual(universe.risk_order, (R1, R0))
        self.assertEqual(universe.selector, "exact-source-union-all-regions-v1")
        self.assertEqual(
            focus_universe_from_payload(focus_universe_payload(universe)), universe,
        )

        recipe = base_recipe(self.protocol, BASE_LADDER[0])
        unchanged = build_local_region_option_proof(
            protocol=self.protocol,
            universe=universe,
            region_key=R0,
            candidate_source_sha256=H[8],
            option_identity_sha256=H[12],
            terminal_status="exact-unchanged",
        )
        changed = build_local_region_option_proof(
            protocol=self.protocol,
            universe=universe,
            region_key=R1,
            candidate_source_sha256=H[13],
            option_identity_sha256=H[14],
            terminal_status="focused-pass",
            focused_evidence_sha256=H[15],
        )
        coverage = build_focused_coverage(
            protocol=self.protocol,
            universe=universe,
            recipe=recipe,
            proofs=(unchanged, changed),
        )
        self.assertTrue(coverage.authorized)
        self.assertEqual(coverage.changed_region_count, 1)
        self.assertEqual(coverage.terminal_changed_count, 1)
        self.assertEqual(
            tuple(item.region_key for item in coverage.proofs), (R1, R0),
        )
        self.assertEqual(coverage.evaluation_order, universe.risk_order)
        self.assertEqual(
            focused_coverage_from_payload(focused_coverage_payload(coverage)),
            coverage,
        )

    def test_changed_region_coverage_is_complete_and_unknown_or_unrenderable_rejects(self) -> None:
        universe = build_focus_universe(
            family_commitment_sha256=self.family_commitment,
            regions=(
                ExactFocusRegion(R0, "body.smd", H[8], H[9], 0, 1, "renderable"),
                ExactFocusRegion(R1, "glass.smd", H[10], H[11], 1, 1, "unrenderable"),
            ),
        )
        recipe = base_recipe(self.protocol, BASE_LADDER[0])
        passing = build_local_region_option_proof(
            protocol=self.protocol, universe=universe, region_key=R0,
            candidate_source_sha256=H[12], option_identity_sha256=H[13],
            terminal_status="focused-pass", focused_evidence_sha256=H[14],
        )
        unrenderable = build_local_region_option_proof(
            protocol=self.protocol, universe=universe, region_key=R1,
            candidate_source_sha256=H[15], option_identity_sha256=H[16],
            terminal_status="unrenderable-reject",
            focused_evidence_sha256=H[17],
        )
        coverage = build_focused_coverage(
            protocol=self.protocol, universe=universe, recipe=recipe,
            proofs=(passing, unrenderable),
        )
        self.assertFalse(coverage.authorized)
        self.assertEqual(coverage.changed_region_count, 2)
        self.assertEqual(coverage.terminal_changed_count, 2)
        with self.assertRaisesRegex(ValueError, "complete"):
            build_focused_coverage(
                protocol=self.protocol, universe=universe, recipe=recipe,
                proofs=(passing,),
            )
        with self.assertRaisesRegex(ValueError, "unrenderable"):
            build_local_region_option_proof(
                protocol=self.protocol, universe=universe, region_key=R1,
                candidate_source_sha256=H[15], option_identity_sha256=H[16],
                terminal_status="focused-pass", focused_evidence_sha256=H[17],
            )

        unknown_universe = build_focus_universe(
            family_commitment_sha256=self.family_commitment,
            regions=(ExactFocusRegion(
                R0, "unknown.smd", H[8], H[9], 0, 1, "unknown",
            ),),
        )
        unknown = build_local_region_option_proof(
            protocol=self.protocol, universe=unknown_universe, region_key=R0,
            candidate_source_sha256=H[12], option_identity_sha256=H[13],
            terminal_status="unknown-reject", focused_evidence_sha256=H[14],
        )
        self.assertEqual(unknown.terminal_status, "unknown-reject")

    def test_focus_universe_safety_caps_fail_closed(self) -> None:
        too_many = tuple(
            ExactFocusRegion(
                "r-" + f"{index:064x}", f"part{index}.smd", H[8], H[9],
                index, 1, "renderable",
            )
            for index in range(MAX_FOCUS_REGIONS + 1)
        )
        with self.assertRaisesRegex(ValueError, "universe"):
            build_focus_universe(
                family_commitment_sha256=self.family_commitment,
                regions=too_many,
            )
        too_many_occurrences = (ExactFocusRegion(
            R0, "body.smd", H[8], H[9], 0,
            MAX_FOCUS_OCCURRENCES + 1, "renderable",
        ),)
        with self.assertRaisesRegex(ValueError, "universe"):
            build_focus_universe(
                family_commitment_sha256=self.family_commitment,
                regions=too_many_occurrences,
            )

    def test_local_proof_cache_identity_reuses_only_exact_comparison_inputs(self) -> None:
        universe = build_focus_universe(
            family_commitment_sha256=self.family_commitment,
            regions=(ExactFocusRegion(
                R0, "body.smd", H[8], H[9], 0, 1, "renderable",
            ),),
        )
        first = build_local_region_option_proof(
            protocol=self.protocol, universe=universe, region_key=R0,
            candidate_source_sha256=H[10], option_identity_sha256=H[11],
            terminal_status="focused-pass", focused_evidence_sha256=H[12],
        )
        second = build_local_region_option_proof(
            protocol=self.protocol, universe=universe, region_key=R0,
            candidate_source_sha256=H[10], option_identity_sha256=H[11],
            terminal_status="focused-pass", focused_evidence_sha256=H[12],
        )
        self.assertEqual(first.cache_identity_sha256, second.cache_identity_sha256)
        changed_option = build_local_region_option_proof(
            protocol=self.protocol, universe=universe, region_key=R0,
            candidate_source_sha256=H[10], option_identity_sha256=H[13],
            terminal_status="focused-pass", focused_evidence_sha256=H[12],
        )
        self.assertNotEqual(first.cache_identity_sha256, changed_option.cache_identity_sha256)
        self.assertEqual(
            build_local_region_option_proof_cache((second, first)), (first,),
        )
        conflicting = build_local_region_option_proof(
            protocol=self.protocol, universe=universe, region_key=R0,
            candidate_source_sha256=H[10], option_identity_sha256=H[11],
            terminal_status="focused-reject", focused_evidence_sha256=H[20],
        )
        with self.assertRaisesRegex(ValueError, "conflicting"):
            build_local_region_option_proof_cache((first, conflicting))

        with self.assertRaisesRegex(ValueError, "equality"):
            build_local_region_option_proof(
                protocol=self.protocol, universe=universe, region_key=R0,
                candidate_source_sha256=H[8], option_identity_sha256=H[11],
                terminal_status="exact-unchanged", focused_evidence_sha256=H[12],
            )

    def test_compositions_are_not_limited_to_three_regions_but_remain_bounded(self) -> None:
        focuses = ((0, R0), (1, R1), (2, R2), (3, R3))
        options = region_tournament_options(self.protocol, focuses)
        selected = tuple(options[index * 9 + 1] for index in range(4))
        recipe = composition_recipe(self.protocol, BASE_LADDER[0], selected)
        self.assertEqual(len(recipe.regions), 4)
        self.assertEqual(required_gates_for_stage("region-intermediate"), (
            "source_lineage", "structural", "compile",
            "focused_all_changed", "source_union",
        ))
        self.assertNotIn(
            "whole_visual", required_gates_for_stage("region-intermediate"),
        )
        self.assertIn("whole_visual", required_gates_for_stage("base-terminal"))
        self.assertIn("final_whole_visual", required_gates_for_stage("final-retained"))

    def test_intermediate_attempts_use_linear_focus_proofs_and_cannot_authorize(self) -> None:
        universe = build_focus_universe(
            family_commitment_sha256=self.family_commitment,
            regions=(ExactFocusRegion(
                R0, "body.smd", H[8], H[9], 0, 1, "renderable",
            ),),
        )
        option = region_tournament_options(self.protocol, ((0, R0),))[1]
        recipe = composition_recipe(self.protocol, BASE_LADDER[0], (option,))
        proof = build_local_region_option_proof(
            protocol=self.protocol, universe=universe, region_key=R0,
            candidate_source_sha256=H[10], option_identity_sha256=H[11],
            terminal_status="focused-pass", focused_evidence_sha256=H[12],
        )
        coverage = build_focused_coverage(
            protocol=self.protocol, universe=universe, recipe=recipe,
            proofs=(proof,),
        )
        gates = tuple(
            (name, H[index + 1])
            for index, name in enumerate(required_gates_for_stage("region-intermediate"))
        )
        intermediate = build_attempt(
            protocol=self.protocol,
            input_commitment_sha256=self.input_commitment,
            family_commitment_sha256=self.family_commitment,
            recipe=recipe,
            compiled=_compiled(700, 1),
            attempt_stage="region-intermediate",
            terminal_status="eligible",
            gate_seals=gates,
            failed_gate=None,
            focused_coverage=coverage,
        )
        self.assertEqual(intermediate.attempt_stage, "region-intermediate")
        self.assertEqual(intermediate.terminal_status, "eligible")
        self.assertNotIn("whole_visual", tuple(name for name, _ in intermediate.gate_seals))
        with self.assertRaisesRegex(ValueError, "intermediate"):
            build_attempt(
                protocol=self.protocol,
                input_commitment_sha256=self.input_commitment,
                family_commitment_sha256=self.family_commitment,
                recipe=recipe,
                compiled=_compiled(700, 1),
                attempt_stage="region-intermediate",
                terminal_status="authorized",
                gate_seals=gates,
                failed_gate=None,
                focused_coverage=coverage,
            )

    def test_authorized_final_requires_complete_authorized_focused_coverage(self) -> None:
        universe = build_focus_universe(
            family_commitment_sha256=self.family_commitment,
            regions=(ExactFocusRegion(
                R0, "body.smd", H[8], H[9], 0, 1, "renderable",
            ),),
        )
        recipe = exact_recipe(self.protocol)
        proof = build_local_region_option_proof(
            protocol=self.protocol, universe=universe, region_key=R0,
            candidate_source_sha256=H[8], option_identity_sha256=H[10],
            terminal_status="exact-unchanged",
        )
        coverage = build_focused_coverage(
            protocol=self.protocol, universe=universe, recipe=recipe,
            proofs=(proof,),
        )
        gates = tuple(
            (name, H[index + 1])
            for index, name in enumerate(required_gates_for_stage("final-retained"))
        )
        result = build_attempt(
            protocol=self.protocol,
            input_commitment_sha256=self.input_commitment,
            family_commitment_sha256=self.family_commitment,
            recipe=recipe,
            compiled=_compiled(1000, 1),
            attempt_stage="final-retained",
            terminal_status="authorized",
            gate_seals=gates,
            failed_gate=None,
            focused_coverage=coverage,
        )
        self.assertEqual(result.focused_coverage.evidence_sha256, coverage.evidence_sha256)
        with self.assertRaisesRegex(ValueError, "focused coverage"):
            build_attempt(
                protocol=self.protocol,
                input_commitment_sha256=self.input_commitment,
                family_commitment_sha256=self.family_commitment,
                recipe=recipe,
                compiled=_compiled(1000, 1),
                attempt_stage="final-retained",
                terminal_status="authorized",
                gate_seals=gates,
                failed_gate=None,
                focused_coverage=None,
            )

    def test_new_visual_calibration_approval_is_exact_and_externally_authenticated(self) -> None:
        approval = _approval()
        self.assertEqual(
            holdout_calibration_approval_from_payload(
                holdout_calibration_approval_payload(approval)
            ),
            approval,
        )
        tampered = holdout_calibration_approval_payload(approval)
        tampered["whole_profile_sha256"] = H[19]
        with self.assertRaisesRegex(ValueError, "seal"):
            holdout_calibration_approval_from_payload(tampered)

        bindings = _bindings()
        with self.assertRaisesRegex(ValueError, "reviewed freeze seal"):
            frozen_holdout_protocol(
                bindings, expected_calibration_approval_sha256=H[19],
            )
        with self.assertRaises(TypeError):
            frozen_holdout_protocol(bindings)

    def test_region_options_are_deterministic_and_exact_fallback_is_first(self) -> None:
        first = region_tournament_options(self.protocol, ((1, R1), (0, R0)))
        second = region_tournament_options(self.protocol, ((0, R0), (1, R1)))
        self.assertEqual(first, second)
        self.assertEqual(tuple(item.region_rank for item in first[:9]), (0,) * 9)
        self.assertEqual(first[0].strategy, "exact-source-v1")
        self.assertEqual(
            tuple((item.ratio, item.target_error) for item in first[1:9]),
            REGION_LADDER,
        )

    def test_selection_uses_actual_compiled_bytes_only_after_all_gates(self) -> None:
        original = self._attempt(exact_recipe(self.protocol), 1000, 1)
        base = self.protocol.base_ladder[0]
        option = region_tournament_options(self.protocol, ((0, R0),))[1]
        passing_recipe = composition_recipe(self.protocol, base, (option,))
        passing = self._attempt(passing_recipe, 600, 8)
        rejected_recipe = composition_recipe(
            self.protocol,
            self.protocol.base_ladder[1],
            (region_tournament_options(self.protocol, ((0, R0),))[2],),
        )
        rejected = self._attempt(rejected_recipe, 400, 12, status="rejected")

        winner = select_holdout_winner(
            self.protocol, (rejected, original, passing),
        )
        self.assertEqual(winner.candidate_id, candidate_recipe_id(passing_recipe))
        self.assertEqual(winner.compiled.total_bytes, 600)

        no_saving = self._attempt(passing_recipe, 1000, 8)
        fallback = select_holdout_winner(self.protocol, (no_saving, original))
        self.assertEqual(fallback.candidate_id, candidate_recipe_id(exact_recipe(self.protocol)))

        base_terminal = self._attempt(
            base_recipe(self.protocol, BASE_LADDER[0]), 500, 4,
        )
        not_a_final = select_holdout_winner(
            self.protocol, (original, base_terminal),
        )
        self.assertEqual(
            not_a_final.candidate_id,
            candidate_recipe_id(exact_recipe(self.protocol)),
        )

    def test_base_finalists_require_complete_ladder_and_use_compiled_bytes(self) -> None:
        attempts = tuple(
            self._attempt(base_recipe(self.protocol, base), 900 - index * 50, 1 + index * 2)
            for index, base in enumerate(BASE_LADDER)
        )
        finalists = select_base_finalists(self.protocol, tuple(reversed(attempts)))
        self.assertEqual(len(finalists), 2)
        self.assertEqual(
            tuple(item.compiled.total_bytes for item in finalists), (500, 550),
        )
        with self.assertRaisesRegex(ValueError, "coverage"):
            select_base_finalists(self.protocol, attempts[:-1])

    def test_ties_are_candidate_id_order_and_input_order_independent(self) -> None:
        original = self._attempt(exact_recipe(self.protocol), 1000, 1)
        options = region_tournament_options(self.protocol, ((0, R0),))
        first = self._attempt(
            composition_recipe(self.protocol, BASE_LADDER[0], (options[1],)), 600, 8,
        )
        second = self._attempt(
            composition_recipe(self.protocol, BASE_LADDER[1], (options[2],)), 600, 12,
        )
        expected = min(first.candidate_id, second.candidate_id)
        self.assertEqual(
            select_holdout_winner(self.protocol, (original, first, second)).candidate_id,
            expected,
        )
        self.assertEqual(
            select_holdout_winner(self.protocol, (second, first, original)).candidate_id,
            expected,
        )

    def test_missing_gate_stale_protocol_and_nonrepeatable_compile_fail_closed(self) -> None:
        original = self._attempt(exact_recipe(self.protocol), 1000, 1)
        with self.assertRaisesRegex(ValueError, "gate"):
            replace(original, gate_seals=original.gate_seals[:-1])
        with self.assertRaisesRegex(ValueError, "replay"):
            replace(original, replay_compiled_manifest_sha256=H[19])
        with self.assertRaisesRegex(ValueError, "protocol"):
            replace(original, protocol_sha256=H[19])

    def test_run_evidence_seals_complete_attempts_and_no_retune_declaration(self) -> None:
        original = self._attempt(exact_recipe(self.protocol), 1000, 1)
        option = region_tournament_options(self.protocol, ((0, R0),))[1]
        base_attempts = tuple(
            self._attempt(
                base_recipe(self.protocol, base), 800 + index * 50, 1 + index * 2,
            )
            for index, base in enumerate(BASE_LADDER)
        )
        candidate = self._attempt(
            composition_recipe(self.protocol, BASE_LADDER[0], (option,)), 600, 8,
        )
        other_finalist = self._attempt(
            composition_recipe(self.protocol, BASE_LADDER[1], (option,)), 900, 12,
        )
        attempts = (candidate, other_finalist, original, *base_attempts)
        result = build_holdout_run_evidence(
            protocol=self.protocol,
            input_commitment_sha256=self.input_commitment,
            family_commitment_sha256=self.family_commitment,
            attempts=attempts,
            tournament_trace_sha256=H[22],
            fresh_run_sha256=H[23],
            replay_run_sha256=H[23],
        )
        self.assertEqual(result.winner_candidate_id, candidate.candidate_id)
        self.assertEqual(result.no_retune_rule, "frozen-before-input-v1")
        self.assertFalse(result.authorizing_production)
        self.assertEqual(result.quality_scope, "holdout-evaluation-only")
        self.assertEqual(
            holdout_run_evidence_from_payload(holdout_run_evidence_payload(result)),
            result,
        )
        malformed = holdout_run_evidence_payload(result)
        malformed["unknown"] = True
        with self.assertRaisesRegex(ValueError, "fields"):
            holdout_run_evidence_from_payload(malformed)

        with self.assertRaisesRegex(ValueError, "finalist"):
            build_holdout_run_evidence(
                protocol=self.protocol,
                input_commitment_sha256=self.input_commitment,
                family_commitment_sha256=self.family_commitment,
                attempts=tuple(item for item in attempts if item is not other_finalist),
                tournament_trace_sha256=H[22],
                fresh_run_sha256=H[23],
                replay_run_sha256=H[23],
            )

        with self.assertRaisesRegex(ValueError, "fresh/replay"):
            build_holdout_run_evidence(
                protocol=self.protocol,
                input_commitment_sha256=self.input_commitment,
                family_commitment_sha256=self.family_commitment,
                attempts=attempts,
                tournament_trace_sha256=H[22],
                fresh_run_sha256=H[23],
                replay_run_sha256=H[24],
            )


if __name__ == "__main__":
    unittest.main()
