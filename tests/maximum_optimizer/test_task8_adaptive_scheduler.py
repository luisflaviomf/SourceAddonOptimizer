from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import hashlib
import tempfile
import threading
import unittest
from unittest import mock

from maximum_optimizer.candidates import CandidateBuild
from maximum_optimizer.composite import (
    build_adaptive_direct_source_union_record,
    build_adaptive_direct_source_union_target,
    compose_candidate_sources,
)
from maximum_optimizer.domain import (
    ArtifactStat,
    CandidateEvaluation,
    CompileFileProof,
    CompiledSizeSnapshot,
    FocusRegionResult,
    GateFailure,
    StructuralAuthorizationEvidence,
    ValidationResult,
)
from maximum_optimizer.focused_cache import (
    RenderFileProof,
    build_final_whole_authorization_evidence,
    build_focused_render_evidence,
)
from maximum_optimizer.production_adapters import AdaptiveDirectCompileResult
from maximum_optimizer.adaptive_direct_scheduler import (
    AdaptiveDirectExecutionAttempt,
    execute_adaptive_direct_schedule,
    require_adaptive_direct_source_count,
)
from maximum_optimizer.monaco_selection import RetainedMonacoBaseProof
from tests.maximum_optimizer.test_task6_adaptive_evidence import (
    base_record,
    structural,
)
from tests.maximum_optimizer.test_orchestrator import _focus_target
from tests.maximum_optimizer.test_task6_direct_compositor import (
    DirectCompositorFixture,
)


def _empty_size(root: Path) -> CompiledSizeSnapshot:
    return CompiledSizeSnapshot(root, 0, {}, {}, ())


def _union_record(source, fixture, *, passed: bool = True):
    target = build_adaptive_direct_source_union_target(
        source_proof=source,
        coverage_manifest_sha256=fixture.coverage.coverage_manifest_sha256,
    )
    files = tuple(sorted((
        RenderFileProof(
            side, "image",
            f"source-union/{target.union_key}/{side}/bind/{render_pass}/camera-{camera:02d}.png",
            1, "1" * 64, 1, 1,
        )
        for side in ("candidate", "reference")
        for render_pass in ("clay", "textured") for camera in range(8)
    ), key=lambda item: item.path))
    return build_adaptive_direct_source_union_record(
        target=target,
        validation=(ValidationResult(True) if passed else ValidationResult(
            False, (GateFailure("focused", source.source_identity, 1.0, 0.0, "failed"),))),
        files=files,
        visibility=((source.component_keys[0], "bind", "camera-00", 1, 1),),
    )


class AdaptiveDirectSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._authority_patch = mock.patch(
            "maximum_optimizer.adaptive_direct_scheduler.require_current_retained_monaco_base",
            side_effect=lambda proof, _event=None: proof,
        )
        self._authority_mock = self._authority_patch.start()

    def tearDown(self) -> None:
        self._authority_patch.stop()

    @staticmethod
    def _proof(fixture, evaluation):
        proof = object.__new__(RetainedMonacoBaseProof)
        for name, value in {
            "evaluation": evaluation, "build": fixture.base_build,
            "metrics": fixture.coverage.metrics_proof,
            "state_inventory": fixture.coverage.state_inventory,
            "candidate_id": evaluation.spec.candidate_id,
            "candidate_cache_digest": fixture.coverage.base_cache_digest,
            "base_spec_sha256": fixture.coverage.base_spec_sha256,
            "source_manifest_sha256": fixture.coverage.base_source_manifest_sha256,
            "source_snapshot_sha256": fixture.coverage.base_source_snapshot_sha256,
        }.items():
            object.__setattr__(proof, name, value)
        return proof

    def _callbacks(self, root: Path, fixture, **overrides):
        base = CandidateEvaluation(
            fixture.base_spec, _empty_size(fixture.base_build.compiled_models_dir),
            ValidationResult(True), ValidationResult(True),
            fixture.base_build.compiled_models_dir, ValidationResult(True),
            {_focus_target().region_key: FocusRegionResult(
                _focus_target(), ValidationResult(True), "a" * 64, False,
            )},
        )
        values = {
            "base_proof": self._proof(fixture, base),
            "coverage": fixture.coverage,
            "remaining_candidates": 1,
            "reserve_ratio": lambda ordinal, _ratio: f"slot-{ordinal}",
            "access_ratio": lambda _ratio: (fixture.requests, fixture.snapshots),
            "workspace_root": root / "adaptive",
            "compose_candidate": lambda *_: (_ for _ in ()).throw(RuntimeError("stop")),
            "compile_candidate": lambda *_: self.fail("compile unexpectedly ran"),
            "authorize_structural": lambda *_: self.fail("structural unexpectedly ran"),
            "rerender_base_focus": lambda *_: self.fail("focus unexpectedly ran"),
            "render_source_union": lambda *_: self.fail("union unexpectedly ran"),
            "authorize_final_whole": lambda *_: self.fail("final unexpectedly ran"),
            "cancel_event": threading.Event(),
        }
        values.update(overrides)
        return values

    def test_zero_or_nine_eligible_sources_fail_before_any_direct_io(self) -> None:
        class Source:
            def __init__(self, kind: str, identity: str) -> None:
                self.kind = kind
                self.source_identity = identity

        touched = []
        for sources in ((), tuple(Source("eligible-exact-v1", f"s-{i}") for i in range(9))):
            with self.subTest(count=len(sources)), self.assertRaises(ValueError):
                require_adaptive_direct_source_count(
                    sources, before_io=lambda: touched.append("io")
                )
        self.assertEqual(touched, [])

    def test_pre_cancel_terminalizes_reserved_prefix_without_ratio_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            event = threading.Event(); event.set()
            reservations = []
            result = execute_adaptive_direct_schedule(**self._callbacks(
                root, fixture, remaining_candidates=2,
                reserve_ratio=lambda ordinal, ratio: (
                    reservations.append((ordinal, ratio)) or f"slot-{ordinal}"
                ),
                access_ratio=lambda *_: self.fail("cancelled schedule performed ratio I/O"),
                cancel_event=event,
            ))
        self.assertEqual(reservations, [(0, 0.50), (1, 0.45)])
        self.assertEqual(tuple(item.status for item in result.attempts), ("cancelled", "cancelled"))
        self.assertIs(result.selected, result.base)
        self.assertFalse((root / "adaptive").exists())

    def test_schedule_never_reserves_more_than_four_fixed_ratios(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            reservations = []
            result = execute_adaptive_direct_schedule(**self._callbacks(
                root, fixture, remaining_candidates=99,
                reserve_ratio=lambda ordinal, ratio: (
                    reservations.append((ordinal, ratio)) or f"slot-{ordinal}"
                ),
                access_ratio=lambda *_: (_ for _ in ()).throw(ValueError("failed input")),
            ))
        self.assertEqual(tuple(ratio for _, ratio in reservations), (0.50, 0.45, 0.40, 0.35))
        self.assertEqual(len(result.attempts), 4)
        self.assertTrue(all(item.status == "schedule_failed" for item in result.attempts))
        self.assertFalse((root / "adaptive").exists())

    def test_rejects_base_without_fresh_whole_and_focused_approval_before_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            invalid = CandidateEvaluation(fixture.base_spec, _empty_size(
                fixture.base_build.compiled_models_dir), ValidationResult(True),
                ValidationResult(True), fixture.base_build.compiled_models_dir)
            reserved = []
            with self.assertRaisesRegex(ValueError, "approved ordinary"):
                execute_adaptive_direct_schedule(**self._callbacks(
                    root, fixture, base_proof=self._proof(fixture, invalid),
                    reserve_ratio=lambda *_: reserved.append("reserved") or "slot",
                ))
        self.assertEqual(reserved, [])

    def test_reserves_fixed_prefix_before_ratio_io_and_preserves_base_on_compile_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"
            fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            base = CandidateEvaluation(
                fixture.base_spec, _empty_size(fixture.base_build.compiled_models_dir),
                ValidationResult(True), ValidationResult(True),
                fixture.base_build.compiled_models_dir,
                ValidationResult(True),
                {_focus_target().region_key: FocusRegionResult(
                    _focus_target(), ValidationResult(True), "a" * 64, False,
                )},
            )
            events: list[tuple] = []

            def access_ratio(ratio: float):
                events.append(("access", ratio))
                if ratio != 0.50:
                    raise ValueError("synthetic later-ratio failure")
                return fixture.requests, fixture.snapshots

            def compile_candidate(*_args):
                events.append(("compile",))
                raise RuntimeError("synthetic compile failure")

            result = execute_adaptive_direct_schedule(
                base_proof=self._proof(fixture, base),
                coverage=fixture.coverage,
                remaining_candidates=2,
                reserve_ratio=lambda ordinal, ratio: (
                    events.append(("reserve", ordinal, ratio)) or f"slot-{ordinal}"
                ),
                access_ratio=access_ratio,
                workspace_root=root / "adaptive",
                compose_candidate=lambda scheduled, workspace, cancel: (
                    events.append(("compose", scheduled.ratio))
                    or compose_candidate_sources(
                        fixture.base_build, scheduled.spec.composite_recipe,
                        {item.snapshot_sha256: item for item in scheduled.snapshots},
                        workspace, cancel, coverage_manifest=fixture.coverage,
                    )
                ),
                compile_candidate=compile_candidate,
                authorize_structural=lambda *_: self.fail("structural ran after compile failure"),
                rerender_base_focus=lambda *_: self.fail("focus ran after compile failure"),
                render_source_union=lambda *_: self.fail("source union ran after compile failure"),
                authorize_final_whole=lambda *_: self.fail("final whole ran after compile failure"),
                cancel_event=threading.Event(),
            )

        self.assertIs(result.selected, base)
        self.assertEqual(tuple(item.status for item in result.attempts), ("compile_failed", "schedule_failed"))
        self.assertEqual(events[:4], [
            ("reserve", 0, 0.50), ("reserve", 1, 0.45),
            ("access", 0.50), ("access", 0.45),
        ])
        self.assertEqual(events[4:], [("compose", 0.50), ("compile",)])
        first = result.attempts[0]
        self.assertIsNotNone(first.evidence)
        self.assertEqual(first.evidence.terminal_status, "compile_failed")
        self.assertRegex(first.attempt_sha256, r"^[0-9a-f]{64}$")

    def test_only_fully_authorized_terminal_candidate_can_replace_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"
            fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=2)
            fresh_base_record = base_record(root / "base-focus")
            base_size = CompiledSizeSnapshot(
                fixture.base_build.compiled_models_dir, 100, {"mdl": 100}, {},
                (ArtifactStat("task6.mdl", ".mdl", 100),),
            )
            base = CandidateEvaluation(
                fixture.base_spec, base_size, ValidationResult(True),
                ValidationResult(True), fixture.base_build.compiled_models_dir,
                ValidationResult(True),
                {fresh_base_record.target.region_key: FocusRegionResult(
                    fresh_base_record.target, ValidationResult(True), "a" * 64, False,
                )},
            )
            events: list[tuple] = []
            mode = {"size": 40, "structural": True, "focus": True, "union": True,
                    "final": True, "cache": False, "score": 0.0}

            def compose(scheduled, workspace, cancel):
                events.append(("compose", scheduled.ratio))
                return compose_candidate_sources(
                    fixture.base_build, scheduled.spec.composite_recipe,
                    {item.snapshot_sha256: item for item in scheduled.snapshots},
                    workspace, cancel, coverage_manifest=fixture.coverage,
                )

            def compile_candidate(scheduled, composed, _cancel):
                events.append(("compile", scheduled.ratio))
                compiled = composed.workspace / "compiled/models"
                compiled.mkdir(parents=True)
                model = compiled / "task6.mdl"
                model.write_bytes(b"x" * mode["size"])
                proof = CompileFileProof(
                    "task6.mdl", ".mdl", mode["size"],
                    hashlib.sha256(model.read_bytes()).hexdigest(),
                )
                build = CandidateBuild(
                    scheduled.spec, composed.workspace, composed.optimized_qc,
                    compiled, {}, {"task6.mdl": "candidate-compile"}, (), None,
                )
                return AdaptiveDirectCompileResult.create(
                    build, (proof,), composed.composition.evidence_sha256,
                )

            def structural_authorization(_scheduled, composed, compiled, _event):
                events.append(("structural", _event.is_set()))
                return structural(
                    composed.composition, compiled.compile_files,
                    passed=mode["structural"],
                )

            def rerender(*_args):
                events.append(("base-focus",))
                if mode["focus"]:
                    if mode["cache"]:
                        return (build_focused_render_evidence(
                            fresh_base_record.target, fresh_base_record.validation,
                            fresh_base_record.expected, fresh_base_record.files,
                            fresh_base_record.material_proof_sha256, True,
                        ),)
                    return (fresh_base_record,)
                return (base_record(root / "failed-base-focus", passed=False),)

            def source_union(_scheduled, _compiled, source, _snapshot, _workspace, _cancel):
                events.append(("source-union", source.source_identity))
                target = build_adaptive_direct_source_union_target(
                    source_proof=source,
                    coverage_manifest_sha256=fixture.coverage.coverage_manifest_sha256,
                )
                files = tuple(sorted((
                    RenderFileProof(
                        side, "image",
                        f"source-union/{target.union_key}/{side}/bind/{render_pass}/camera-{camera:02d}.png",
                        1, "1" * 64, 1, 1,
                    )
                    for side in ("candidate", "reference")
                    for render_pass in ("clay", "textured")
                    for camera in range(8)
                ), key=lambda item: item.path))
                return build_adaptive_direct_source_union_record(
                    target=target,
                    validation=(
                        ValidationResult(True) if mode["union"] else
                        ValidationResult(False, (GateFailure(
                            "focused", source.source_identity, 1.0, 0.0, "failed",
                        ),))
                    ),
                    files=files,
                    visibility=((source.component_keys[0], "bind", "camera-00", 1, 1),),
                )

            def final_whole(scheduled, composed, compiled, structural_evidence, _event):
                events.append(("final-whole", _event.is_set()))
                return build_final_whole_authorization_evidence(
                    scheduled.spec.candidate_id,
                    structural_evidence.candidate_cache_digest,
                    scheduled.spec.composite_recipe.recipe_sha256,
                    composed.composition.evidence_sha256,
                    structural_evidence.compile_manifest_sha256,
                    "logs/whole-visual-index.json", "a" * 64, "b" * 64,
                    (
                        ValidationResult(True, metrics={"fidelity_score": mode["score"]}) if mode["final"] else
                        ValidationResult(False, (GateFailure(
                            "whole", "candidate", 1.0, 0.0, "failed",
                        ),))
                    ),
                )

            def execute(name: str):
                return execute_adaptive_direct_schedule(
                    base_proof=self._proof(fixture, base),
                    coverage=fixture.coverage, remaining_candidates=1,
                    reserve_ratio=lambda ordinal, ratio: f"slot-{ordinal}",
                    access_ratio=lambda _ratio: (fixture.requests, fixture.snapshots),
                    workspace_root=root / name, compose_candidate=compose,
                    compile_candidate=compile_candidate,
                    authorize_structural=structural_authorization,
                    rerender_base_focus=rerender,
                    render_source_union=source_union,
                    authorize_final_whole=final_whole,
                    cancel_event=threading.Event(),
                )

            result = execute("adaptive")
            mode["structural"] = False
            structural_failed = execute("structural-failed")
            mode.update(structural=True, focus=False)
            focused_failed = execute("focused-failed")
            mode.update(focus=True, final=False)
            final_failed = execute("final-failed")
            mode.update(final=True, size=140)
            larger_authorized = execute("larger-authorized")
            mode.update(size=40, cache=True)
            cached_focus = execute("cached-focus")
            mode.update(cache=False, size=100, score=0.8)
            higher_fidelity_tie = execute("higher-fidelity-tie")

        self.assertEqual(
            result.attempts[0].status, "authorized", result.attempts[0].error
        )
        self.assertIs(result.selected, result.attempts[0].evaluation)
        self.assertEqual(result.selected.size.total_bytes, 40)
        valid_attempt = result.attempts[0]
        forged_validation = ValidationResult(
            True, metrics={"fidelity_score": 0.999}
        )
        forged_evaluation = replace(
            valid_attempt.evaluation, visual=forged_validation,
            whole_visual=forged_validation,
        )
        with self.assertRaises(ValueError):
            AdaptiveDirectExecutionAttempt(
                valid_attempt.ratio, valid_attempt.status,
                valid_attempt.candidate_id, forged_evaluation,
                valid_attempt.build, valid_attempt.evidence,
                valid_attempt.error, valid_attempt.recipe,
                valid_attempt.attempt_sha256,
            )
        self.assertEqual(structural_failed.attempts[0].status, "structural_failed")
        self.assertEqual(
            focused_failed.attempts[0].status, "focused_failed",
            focused_failed.attempts[0].error,
        )
        self.assertEqual(final_failed.attempts[0].status, "final_whole_failed")
        self.assertEqual(larger_authorized.attempts[0].status, "authorized")
        self.assertIs(larger_authorized.selected, base)
        self.assertEqual(cached_focus.attempts[0].status, "focused_failed")
        self.assertRegex(cached_focus.attempts[0].attempt_sha256, r"^[0-9a-f]{64}$")
        self.assertIs(higher_fidelity_tie.selected, higher_fidelity_tie.attempts[0].evaluation)
        self.assertNotIn(("structural", True), events)
        self.assertNotIn(("final-whole", True), events)
        self.assertEqual(sum(item[0] == "final-whole" for item in events), 4)
        self.assertEqual(events.count(("source-union", "meshes/part-00.smd")), 4)
        self.assertEqual(events.count(("source-union", "meshes/part-01.smd")), 4)

    def test_requires_current_retained_proof_and_exact_bound_coverage_before_reservation(self) -> None:
        from maximum_optimizer.composite import build_adaptive_direct_coverage_manifest
        from tests.maximum_optimizer.test_task6_monaco_selection import _retained
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            evaluation, _build, proof = _retained(root)
            coverage = build_adaptive_direct_coverage_manifest(
                family_id=proof.state_inventory.family_id,
                family_input_sha256=proof.state_inventory.family_input_sha256,
                base_candidate_id=proof.state_inventory.base_candidate_id,
                base_spec_sha256=proof.state_inventory.base_spec_sha256,
                base_cache_digest=proof.state_inventory.base_cache_digest,
                base_source_manifest_sha256=proof.state_inventory.base_source_manifest_sha256,
                base_source_snapshot_sha256=proof.state_inventory.base_source_snapshot_sha256,
                metrics_proof=proof.metrics, state_inventory=proof.state_inventory,
            )
            reserved = []
            result = execute_adaptive_direct_schedule(
                **self._callbacks(root, type("Fixture", (), {
                    "base_spec": evaluation.spec, "base_build": proof.build,
                    "coverage": coverage, "requests": (), "snapshots": (),
                })(), base_proof=proof, coverage=coverage,
                remaining_candidates=0,
                reserve_ratio=lambda *_: reserved.append("reserved") or "slot")
            )
            self.assertIs(result.base, evaluation)
            from maximum_optimizer.monaco_selection import require_current_retained_monaco_base
            self.assertIs(require_current_retained_monaco_base(proof), proof)
            self._authority_mock.assert_called()
            self.assertEqual(reserved, [])
            (proof.build.compiled_models_dir / proof.compiled_artifacts[0].relative_path).write_bytes(
                b"stale-retained-compile"
            )
            self._authority_patch.stop()
            try:
                with self.assertRaises(ValueError):
                    execute_adaptive_direct_schedule(**self._callbacks(
                        root / "stale", type("Fixture", (), {
                            "base_spec": evaluation.spec, "base_build": proof.build,
                            "coverage": coverage, "requests": (), "snapshots": (),
                        })(), base_proof=proof, coverage=coverage,
                        remaining_candidates=0,
                        reserve_ratio=lambda *_: reserved.append("stale") or "slot",
                    ))
            finally:
                self._authority_mock = self._authority_patch.start()
            self.assertEqual(reserved, [])
            forged = object.__new__(type(coverage))
            for field in coverage.__dataclass_fields__:
                object.__setattr__(forged, field, getattr(coverage, field))
            object.__setattr__(forged, "base_candidate_id", "other")
            with self.assertRaises(ValueError):
                execute_adaptive_direct_schedule(**self._callbacks(
                    root / "other", type("Fixture", (), {
                        "base_spec": evaluation.spec, "base_build": proof.build,
                        "coverage": forged, "requests": (), "snapshots": (),
                    })(), base_proof=proof, coverage=forged,
                    reserve_ratio=lambda *_: reserved.append("bad") or "slot",
                ))
            self.assertEqual(reserved, [])

    def test_post_final_compile_mutation_cannot_authorize_and_attempt_is_sealed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            fresh = base_record(root / "base-focus")
            base = CandidateEvaluation(
                fixture.base_spec, _empty_size(fixture.base_build.compiled_models_dir),
                ValidationResult(True), ValidationResult(True), fixture.base_build.compiled_models_dir,
                ValidationResult(True), {fresh.target.region_key: FocusRegionResult(
                    fresh.target, ValidationResult(True), "a" * 64, False)},
            )
            compiled_path = {}
            def compose(scheduled, workspace, event):
                return compose_candidate_sources(fixture.base_build, scheduled.spec.composite_recipe,
                    fixture.resolver, workspace, event, coverage_manifest=fixture.coverage)
            def compile_candidate(scheduled, composed, _event):
                target = composed.workspace / "compiled/models/task6.mdl"; target.parent.mkdir(parents=True)
                target.write_bytes(b"current-compiled"); compiled_path["path"] = target
                proof = CompileFileProof("task6.mdl", ".mdl", target.stat().st_size,
                    hashlib.sha256(target.read_bytes()).hexdigest())
                build = CandidateBuild(scheduled.spec, composed.workspace, composed.optimized_qc,
                    target.parent, {}, {}, (), None)
                return AdaptiveDirectCompileResult.create(build, (proof,), composed.composition.evidence_sha256)
            def structural_callback(_scheduled, composed, compiled, _event):
                return structural(composed.composition, compiled.compile_files)
            def final_callback(scheduled, composed, _compiled, struct, _event):
                evidence = build_final_whole_authorization_evidence(
                    scheduled.spec.candidate_id, struct.candidate_cache_digest,
                    scheduled.spec.composite_recipe.recipe_sha256,
                    composed.composition.evidence_sha256, struct.compile_manifest_sha256,
                    "logs/whole-visual-index.json", "a" * 64, "b" * 64, ValidationResult(True))
                compiled_path["path"].write_bytes(b"mutated-after-final")
                return evidence
            result = execute_adaptive_direct_schedule(**self._callbacks(
                root, fixture, base_proof=self._proof(fixture, base), compose_candidate=compose,
                compile_candidate=compile_candidate, authorize_structural=structural_callback,
                rerender_base_focus=lambda *_: (fresh,),
                render_source_union=lambda _o, _c, source, *_: _union_record(source, fixture),
                authorize_final_whole=final_callback,
            ))
            attempt = result.attempts[0]
            self.assertNotEqual(attempt.status, "authorized")
            self.assertRegex(attempt.attempt_sha256, r"^[0-9a-f]{64}$")
            self.assertIs(result.selected, base)

    def test_workspace_and_cached_base_focus_fail_closed_and_callbacks_observe_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            occupied = root / "occupied"; occupied.mkdir()
            touched = []
            with self.assertRaises(ValueError):
                execute_adaptive_direct_schedule(**self._callbacks(
                    root, fixture, workspace_root=occupied,
                    reserve_ratio=lambda *_: touched.append("reserve") or "slot"))
            self.assertEqual(touched, [])
            with self.assertRaises(ValueError):
                execute_adaptive_direct_schedule(**self._callbacks(
                    root, fixture, workspace_root=Path("relative")))

    def test_callback_setting_cancel_then_raising_terminalizes_as_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            event = threading.Event()
            def compose(scheduled, workspace, current):
                return compose_candidate_sources(fixture.base_build, scheduled.spec.composite_recipe,
                    fixture.resolver, workspace, current, coverage_manifest=fixture.coverage)
            def compile_candidate(scheduled, composed, _event):
                target = composed.workspace / "compiled/models/task6.mdl"; target.parent.mkdir(parents=True)
                target.write_bytes(b"compiled")
                proof = CompileFileProof("task6.mdl", ".mdl", 8,
                    hashlib.sha256(b"compiled").hexdigest())
                return AdaptiveDirectCompileResult.create(CandidateBuild(
                    scheduled.spec, composed.workspace, composed.optimized_qc,
                    target.parent, {}, {}, (), None), (proof,),
                    composed.composition.evidence_sha256)
            def cancel_structural(*_args):
                event.set()
                raise RuntimeError("callback noticed cancellation")
            result = execute_adaptive_direct_schedule(**self._callbacks(
                root, fixture, compose_candidate=compose,
                compile_candidate=compile_candidate,
                authorize_structural=cancel_structural, cancel_event=event,
            ))
            self.assertEqual(result.attempts[0].status, "cancelled")
            self.assertTrue(result.cancelled)
            self.assertRegex(result.attempts[0].attempt_sha256, r"^[0-9a-f]{64}$")

    def test_attempt_cross_invariants_and_normative_tie_break(self) -> None:
        with self.assertRaises((TypeError, ValueError)):
            AdaptiveDirectExecutionAttempt(0.5, "authorized", candidate_id="wrong")


if __name__ == "__main__":
    unittest.main()
