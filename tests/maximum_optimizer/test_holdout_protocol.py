from __future__ import annotations

from dataclasses import replace
import unittest

from maximum_optimizer.holdout_protocol import (
    BASE_LADDER,
    REGION_LADDER,
    REQUIRED_GATES,
    CompiledArtifact,
    HoldoutFreezeBindings,
    base_recipe,
    build_attempt,
    build_compiled_proof,
    build_holdout_calibration_approval,
    build_holdout_run_evidence,
    candidate_recipe_id,
    composition_recipe,
    exact_recipe,
    frozen_holdout_protocol,
    holdout_calibration_approval_from_payload,
    holdout_calibration_approval_payload,
    holdout_protocol_from_payload,
    holdout_protocol_payload,
    holdout_run_evidence_from_payload,
    holdout_run_evidence_payload,
    region_tournament_options,
    select_base_finalists,
    select_holdout_winner,
)


H = tuple(f"{index:064x}" for index in range(1, 32))
R0 = "r-" + "a" * 64
R1 = "r-" + "b" * 64


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
        gates = tuple((name, H[(marker + index) % len(H)]) for index, name in enumerate(
            REQUIRED_GATES
        ))
        return build_attempt(
            protocol=self.protocol,
            input_commitment_sha256=self.input_commitment,
            family_commitment_sha256=self.family_commitment,
            recipe=recipe,
            compiled=_compiled(size, marker),
            terminal_status=status,
            gate_seals=gates if status == "authorized" else gates[:-1],
            failed_gate=None if status == "authorized" else REQUIRED_GATES[-1],
        )

    def test_protocol_is_exact_reparsable_and_contains_frozen_algorithm(self) -> None:
        self.assertEqual(self.protocol.focused_top_k, 3)
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
        payload["focused_top_k"] = 4
        with self.assertRaisesRegex(ValueError, "frozen"):
            holdout_protocol_from_payload(
                payload,
                expected_calibration_approval_sha256=(
                    self.protocol.bindings.reviewed_calibration_approval_sha256
                ),
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
