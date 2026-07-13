from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from dataclasses import replace
from unittest import mock

from maximum_optimizer.composite import (
    build_adaptive_direct_coverage_manifest,
    build_adaptive_candidate_metrics_proof,
    build_adaptive_direct_state_inventory,
    build_direct_prefilter_proof,
    build_direct_source_request,
    build_direct_source_snapshot,
    candidate_spec_sha256,
    optimizer_contract_sha256,
)
from maximum_optimizer.adaptive_direct_evidence import build_adaptive_direct_evidence
from maximum_optimizer.domain import (
    CandidateSpec, ChangedSourceProof, CompositionProof, DirectDroppedTriangleProof,
    DirectInputMaterialProof, DirectMaterialTriangleProof,
    ValidationResult,
)
from maximum_optimizer.focused_cache import build_final_whole_authorization_evidence
from maximum_optimizer.monaco_schedule import (
    MONACO_DIRECT_RATIOS,
    MonacoCancelledReservation,
    MonacoFailedReservation,
    MonacoScheduleResult,
    build_monaco_schedule,
)
from maximum_optimizer.processes import ProcessCancelledError
from tests.maximum_optimizer.test_task6_contracts import (
    H, coverage_source, inventory_for_coverage, metrics_for_coverage,
)
from tests.maximum_optimizer.test_task6_adaptive_evidence import (
    base_record, compile_files, direct_record, structural,
)


class MonacoScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base_spec = CandidateSpec(
            "base", "blender", 0.8, 0.0, "blender-adaptive-v1",
            strategy="blender-adaptive-v1", transfer="blender-native-v1",
        )
        sources = (coverage_source("body.smd"), coverage_source("wheel.smd"))
        old_metrics = metrics_for_coverage(sources)
        old_inventory = inventory_for_coverage(sources)
        spec_sha256 = candidate_spec_sha256(self.base_spec)
        metrics = build_adaptive_candidate_metrics_proof(
            family_id=H["0"], family_input_sha256=H["1"], candidate_id="base",
            candidate_cache_digest=H["3"], base_spec_sha256=spec_sha256,
            source_manifest_sha256=H["4"], source_snapshot_sha256=H["5"],
            original_graph_sha256=H["6"], candidate_graph_sha256=H["7"],
            raw_metrics_sha256=H["8"], sources=old_metrics.sources,
        )
        inventory = build_adaptive_direct_state_inventory(
            family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
            base_spec_sha256=spec_sha256, base_cache_digest=H["3"],
            base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
            complete_source_identities=old_inventory.complete_source_identities,
            rows=old_inventory.rows,
        )
        self.coverage = build_adaptive_direct_coverage_manifest(
            family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
            base_spec_sha256=spec_sha256, base_cache_digest=H["3"],
            base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
            metrics_proof=metrics, state_inventory=inventory,
        )

    def _ratio_payload(self, root: Path, ratio: float):
        requests = []
        snapshots = []
        for ordinal, source in enumerate(self.coverage.sources):
            prefilter = build_direct_prefilter_proof(
                source_triangle_count=10,
                triangles=(DirectDroppedTriangleProof(
                    0, "paint", ("root",), "cross-squared-at-most-1e-30", source.source_sha256,
                ),),
            )
            request = build_direct_source_request(
                family_id=self.coverage.family_id,
                family_input_sha256=self.coverage.family_input_sha256,
                base_candidate_id=self.coverage.base_candidate_id,
                base_spec_sha256=self.coverage.base_spec_sha256,
                base_cache_digest=self.coverage.base_cache_digest,
                base_source_manifest_sha256=self.coverage.base_source_manifest_sha256,
                base_source_snapshot_sha256=self.coverage.base_source_snapshot_sha256,
                coverage_manifest_sha256=self.coverage.coverage_manifest_sha256,
                source_coverage_sha256=source.source_coverage_sha256,
                optimizer_contract_sha256=optimizer_contract_sha256(self.base_spec),
                whole_profile_sha256=H["7"], focused_profile_sha256=H["8"],
                dependency_proof_sha256=H["9"], source_identity=source.source_identity,
                source_relative_path=source.source_identity, source_size=source.source_size,
                source_sha256=source.source_sha256, direct_ratio=ratio,
                expected_prefilter=prefilter,
                expected_materials=(DirectInputMaterialProof(0, "paint", 9, H["c"]),),
            )
            ratio_root = root / f"r{int(ratio * 100):03d}-{ordinal}"
            ratio_root.mkdir()
            after = max(1, int(9 * ratio))
            rows = []
            for triangle in range(after):
                x = triangle * 2
                rows.append(
                    f"paint\n0 {x} 0 0 0 0 1 0 0\n0 {x + 1} 0 0 0 0 1 1 0\n0 {x} 1 0 0 0 1 0 1\n"
                )
            output = (
                'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
                '0 0 0 0 0 0 0\nend\ntriangles\n' + "".join(rows) + "end\n"
            ).encode()
            (ratio_root / "output.smd").write_bytes(output)
            snapshot = build_direct_source_snapshot(
                request=request, source_root=ratio_root, output_relative_path="output.smd",
                output_size=len(output), output_sha256=hashlib.sha256(output).hexdigest(),
                triangles_before=9,
                triangles_after=after,
                material_triangles=(DirectMaterialTriangleProof(
                    0, "paint", 9, after, after,
                ),),
                prefilter=prefilter,
            )
            requests.append(request); snapshots.append(snapshot)
        return tuple(requests), tuple(snapshots)

    def test_fixed_ratios_and_reserves_complete_remaining_prefix_before_access(self) -> None:
        self.assertEqual(MONACO_DIRECT_RATIOS, (0.50, 0.45, 0.40, 0.35))
        events = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payloads = {ratio: self._ratio_payload(root, ratio) for ratio in MONACO_DIRECT_RATIOS[:2]}
            result = build_monaco_schedule(
                base_spec=self.base_spec, coverage=self.coverage, remaining_candidates=2,
                reserve=lambda ordinal, ratio: events.append(("reserve", ordinal, ratio)) or f"token-{ordinal}",
                access_ratio=lambda ratio: events.append(("access", ratio)) or payloads[ratio],
            )
        self.assertEqual([item.ratio for item in result], [0.50, 0.45])
        self.assertEqual(events, [
            ("reserve", 0, 0.50), ("reserve", 1, 0.45),
            ("access", 0.50), ("access", 0.45),
        ])

    def test_zero_budget_touches_nothing_and_never_generates_midpoint(self) -> None:
        touched = []
        result = build_monaco_schedule(
            base_spec=self.base_spec, coverage=self.coverage, remaining_candidates=0,
            reserve=lambda *_: touched.append("reserve"),
            access_ratio=lambda *_: touched.append("access"),
        )
        self.assertEqual(tuple(result), ())
        self.assertEqual(touched, [])
        with self.assertRaises(ValueError):
            build_monaco_schedule(
                base_spec=self.base_spec, coverage=self.coverage, remaining_candidates=True,
                reserve=lambda *_: "token", access_ratio=lambda *_: (),
            )
        with self.assertRaises(ValueError):
            build_monaco_schedule(
                base_spec=replace(self.base_spec, candidate_id="stale-base"),
                coverage=self.coverage, remaining_candidates=1,
                reserve=lambda *_: touched.append("reserve") or "token",
                access_ratio=lambda *_: touched.append("access"),
            )
        self.assertEqual(touched, [])

    def test_budget_larger_than_schedule_still_produces_exactly_four_fixed_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payloads = {ratio: self._ratio_payload(root, ratio) for ratio in MONACO_DIRECT_RATIOS}
            result = build_monaco_schedule(
                base_spec=self.base_spec, coverage=self.coverage, remaining_candidates=99,
                reserve=lambda ordinal, ratio: f"token-{ordinal}",
                access_ratio=lambda ratio: payloads[ratio],
            )
        self.assertEqual(tuple(item.ratio for item in result), MONACO_DIRECT_RATIOS)

    def test_failed_reserved_ratio_is_terminal_and_later_reserved_ratio_continues(self) -> None:
        events = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = self._ratio_payload(root, 0.45)
            def access(ratio):
                events.append(("access", ratio))
                if ratio == 0.50:
                    raise ValueError("ratio failed")
                return valid
            result = build_monaco_schedule(
                base_spec=self.base_spec, coverage=self.coverage, remaining_candidates=2,
                reserve=lambda ordinal, ratio: events.append(("reserve", ratio)) or f"token-{ordinal}",
                access_ratio=access,
            )
        self.assertIsInstance(result[0], MonacoFailedReservation)
        self.assertEqual(result[0].terminal_status, "failed")
        self.assertIsNotNone(result[1].spec)
        self.assertEqual(events, [
            ("reserve", 0.50), ("reserve", 0.45),
            ("access", 0.50), ("access", 0.45),
        ])

    def test_stale_snapshot_becomes_typed_terminal_after_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            requests, snapshots = self._ratio_payload(Path(temporary), 0.50)
            (snapshots[0].source_root / snapshots[0].output_relative_path).write_bytes(b"stale")
            result = build_monaco_schedule(
                base_spec=self.base_spec, coverage=self.coverage, remaining_candidates=1,
                reserve=lambda *_: "token", access_ratio=lambda _: (requests, snapshots),
            )
        self.assertIsInstance(result[0], MonacoFailedReservation)

    def test_schedule_rejects_snapshot_whose_sealed_ratio_matrix_is_bypassed(self) -> None:
        def unsafe_copy(value, **changes):
            copied = object.__new__(type(value))
            for name in value.__dataclass_fields__:
                object.__setattr__(copied, name, changes.get(name, getattr(value, name)))
            return copied
        with tempfile.TemporaryDirectory() as temporary:
            requests, snapshots = self._ratio_payload(Path(temporary), 0.35)
            forged = unsafe_copy(snapshots[0], triangles_after=5)
            result = build_monaco_schedule(
                base_spec=self.base_spec, coverage=self.coverage,
                remaining_candidates=1, reserve=lambda *_: "token",
                access_ratio=lambda _: (requests, (forged, snapshots[1])),
            )
        self.assertIsInstance(result[0], MonacoFailedReservation)

    def test_cancellation_terminalizes_every_reserved_ratio(self) -> None:
        accesses = []

        def access(ratio):
            accesses.append(ratio)
            raise ProcessCancelledError("cancelled")

        result = build_monaco_schedule(
            base_spec=self.base_spec, coverage=self.coverage,
            remaining_candidates=2, reserve=lambda *_: "token",
            access_ratio=access,
        )
        self.assertEqual(accesses, [0.50])
        self.assertIsInstance(result, MonacoScheduleResult)
        self.assertTrue(result.cancelled)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(isinstance(item, MonacoCancelledReservation) for item in result))

    def test_pre_cancelled_schedule_never_accesses_reserved_ratio(self) -> None:
        cancelled = threading.Event(); cancelled.set()
        access = mock.Mock(side_effect=AssertionError("ratio accessed after cancellation"))
        result = build_monaco_schedule(
            base_spec=self.base_spec, coverage=self.coverage,
            remaining_candidates=2, reserve=lambda *_: "token",
            access_ratio=access, cancel_event=cancelled,
        )
        access.assert_not_called()
        self.assertTrue(result.cancelled)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(isinstance(item, MonacoCancelledReservation) for item in result))

    def test_rejects_per_ratio_aggregate_over_two_gibibytes(self) -> None:
        def unsafe_copy(value, **changes):
            copied = object.__new__(type(value))
            for name in value.__dataclass_fields__:
                object.__setattr__(copied, name, changes.get(name, getattr(value, name)))
            return copied

        with tempfile.TemporaryDirectory() as temporary:
            requests, snapshots = self._ratio_payload(Path(temporary), 0.50)
            oversized_requests = tuple(
                unsafe_copy(item, source_size=1024 ** 3 + 1) for item in requests
            )
            oversized_snapshots = tuple(
                unsafe_copy(item, request=request)
                for item, request in zip(snapshots, oversized_requests)
            )
            validator = mock.Mock(side_effect=AssertionError("hashed oversized set"))
            with mock.patch(
                "maximum_optimizer.monaco_schedule.revalidate_direct_source_snapshot",
                validator,
            ):
                result = build_monaco_schedule(
                    base_spec=self.base_spec, coverage=self.coverage,
                    remaining_candidates=1, reserve=lambda *_: "token",
                    access_ratio=lambda _: (oversized_requests, oversized_snapshots),
                )
        self.assertIsInstance(result[0], MonacoFailedReservation)
        validator.assert_not_called()

    def test_request_snapshot_sets_and_candidate_cache_are_canonical_and_fully_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            requests, snapshots = self._ratio_payload(Path(temporary), 0.50)
            first = build_monaco_schedule(
                base_spec=self.base_spec, coverage=self.coverage, remaining_candidates=1,
                reserve=lambda *_: "token",
                access_ratio=lambda _: (requests, snapshots),
            )[0]
            second = build_monaco_schedule(
                base_spec=self.base_spec, coverage=self.coverage, remaining_candidates=1,
                reserve=lambda *_: "token",
                access_ratio=lambda _: (tuple(reversed(requests)), tuple(reversed(snapshots))),
            )[0]
        self.assertEqual(first.request_set_sha256, second.request_set_sha256)
        self.assertEqual(first.snapshot_set_sha256, second.snapshot_set_sha256)
        self.assertEqual(first.spec.cache_payload(), second.spec.cache_payload())
        self.assertEqual(first.spec.candidate_id, "recovery-" + first.spec.composite_recipe.recipe_sha256)
        recipe = first.spec.composite_recipe
        self.assertEqual(recipe.coverage_manifest_sha256, self.coverage.coverage_manifest_sha256)
        self.assertEqual(recipe.direct_request_set_sha256, first.request_set_sha256)
        self.assertEqual(recipe.direct_snapshot_set_sha256, first.snapshot_set_sha256)
        self.assertEqual(tuple(item.effective_ratio for item in recipe.overlays), (0.50, 0.50))
        with self.assertRaises(ValueError):
            replace(first.spec, candidate_id="filename-or-env-activated")
        with self.assertRaises(ValueError):
            replace(first, request_set_sha256=H["f"])
        with self.assertRaises(ValueError):
            replace(first, requests=tuple(reversed(first.requests)))

    def test_scheduled_candidate_identity_can_authorize_adaptive_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            requests, snapshots = self._ratio_payload(root, 0.50)
            scheduled = build_monaco_schedule(
                base_spec=self.base_spec, coverage=self.coverage, remaining_candidates=1,
                reserve=lambda *_: "token",
                access_ratio=lambda _: (requests, snapshots),
            )[0]
            recipe = scheduled.spec.composite_recipe
            changed = tuple(ChangedSourceProof(
                request.source_identity, request.source_identity,
                request.source_size, request.source_sha256,
                snapshot.output_size, snapshot.output_sha256,
                H["3"], snapshot.snapshot_sha256,
            ) for request, snapshot in zip(scheduled.requests, scheduled.snapshots))
            composition = CompositionProof.create(
                "adaptive-direct-fallback-v1", recipe.recipe_sha256,
                H["5"], H["6"], changed,
            )
            files = compile_files()
            structural_evidence = structural(composition, files)
            direct = tuple(direct_record(
                source_identity=item.source_identity,
                coverage=self.coverage.coverage_manifest_sha256,
            ) for item in changed)
            final = build_final_whole_authorization_evidence(
                scheduled.spec.candidate_id, structural_evidence.candidate_cache_digest,
                recipe.recipe_sha256, composition.evidence_sha256,
                structural_evidence.compile_manifest_sha256,
                "logs/whole-visual-index.json", H["a"], H["b"], ValidationResult(True),
            )
            evidence = build_adaptive_direct_evidence(
                terminal_status="authorized", recipe=recipe, composition=composition,
                changed_sources=changed, compile_files=files,
                structural=structural_evidence,
                base_focus_records=(base_record(root / "base-focus"),),
                direct_focus_records=direct, final_whole=final,
            )
        self.assertEqual(evidence.final_whole.candidate_id, scheduled.spec.candidate_id)


if __name__ == "__main__":
    unittest.main()
