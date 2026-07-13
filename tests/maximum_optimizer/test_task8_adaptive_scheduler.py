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
    _snapshot_authorized_compile,
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

    def _authorized_callbacks(
        self, root: Path, fixture, *, event=None,
        after_compile=None, after_structural=None, after_final=None,
    ):
        event = event or threading.Event()
        fresh = base_record(root / "base-focus")
        base = CandidateEvaluation(
            fixture.base_spec, _empty_size(fixture.base_build.compiled_models_dir),
            ValidationResult(True), ValidationResult(True),
            fixture.base_build.compiled_models_dir, ValidationResult(True),
            {fresh.target.region_key: FocusRegionResult(
                fresh.target, ValidationResult(True), "a" * 64, False,
            )},
        )

        def compose(scheduled, workspace, current):
            return compose_candidate_sources(
                fixture.base_build, scheduled.spec.composite_recipe,
                fixture.resolver, workspace, current,
                coverage_manifest=fixture.coverage,
            )

        def compile_candidate(scheduled, composed, current):
            target = composed.workspace / "compiled/models/task6.mdl"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"compiled")
            proof = CompileFileProof(
                "task6.mdl", ".mdl", len(b"compiled"),
                hashlib.sha256(b"compiled").hexdigest(),
            )
            result = AdaptiveDirectCompileResult.create(CandidateBuild(
                scheduled.spec, composed.workspace, composed.optimized_qc,
                target.parent, {}, {}, (), None,
            ), (proof,), composed.composition.evidence_sha256)
            if after_compile is not None:
                after_compile(composed, result, current)
            return result

        def structural_callback(scheduled, composed, compiled, current):
            result = structural(composed.composition, compiled.compile_files)
            if after_structural is not None:
                after_structural(composed, compiled, current)
            return result

        def final_callback(scheduled, composed, compiled, struct, current):
            result = build_final_whole_authorization_evidence(
                scheduled.spec.candidate_id, struct.candidate_cache_digest,
                scheduled.spec.composite_recipe.recipe_sha256,
                composed.composition.evidence_sha256,
                struct.compile_manifest_sha256,
                "logs/whole-visual-index.json", "a" * 64, "b" * 64,
                ValidationResult(True),
            )
            if after_final is not None:
                after_final(composed, compiled, current)
            return result

        return self._callbacks(
            root, fixture, base_proof=self._proof(fixture, base),
            compose_candidate=compose, compile_candidate=compile_candidate,
            authorize_structural=structural_callback,
            rerender_base_focus=lambda *_: (fresh,),
            render_source_union=lambda _o, _c, source, *_: _union_record(
                source, fixture
            ),
            authorize_final_whole=final_callback, cancel_event=event,
        ), base

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
            occupied = root / "adaptive"
            occupied.mkdir()
            sentinel = occupied / "foreign.txt"
            sentinel.write_text("foreign", encoding="utf-8")
            result = execute_adaptive_direct_schedule(**self._callbacks(
                root, fixture, remaining_candidates=2,
                reserve_ratio=lambda ordinal, ratio: (
                    reservations.append((ordinal, ratio)) or f"slot-{ordinal}"
                ),
                access_ratio=lambda *_: self.fail("cancelled schedule performed ratio I/O"),
                cancel_event=event,
            ))
            preserved = sentinel.read_text(encoding="utf-8")
        self.assertEqual(reservations, [(0, 0.50), (1, 0.45)])
        self.assertEqual(tuple(item.status for item in result.attempts), ("cancelled", "cancelled"))
        self.assertIs(result.selected, result.base)
        self.assertEqual(preserved, "foreign")
        self._authority_mock.assert_called_once()

    def test_revalidates_composed_source_after_compile_structural_and_final_callbacks(self) -> None:
        for stage in ("compile", "structural", "final"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture_root = root / "fixture"; fixture_root.mkdir()
                fixture = DirectCompositorFixture(fixture_root, visual_count=1)

                def mutate(composed, *_args):
                    source = next((composed.workspace / "src").rglob("*.smd"))
                    source.write_bytes(source.read_bytes() + b"\nforeign-mutation")

                callbacks, base = self._authorized_callbacks(
                    root, fixture,
                    after_compile=mutate if stage == "compile" else None,
                    after_structural=mutate if stage == "structural" else None,
                    after_final=mutate if stage == "final" else None,
                )
                result = execute_adaptive_direct_schedule(**callbacks)
                expected = {
                    "compile": "compile_failed",
                    "structural": "structural_failed",
                    "final": "final_whole_failed",
                }[stage]
                self.assertEqual(result.attempts[0].status, expected)
                self.assertIs(result.selected, base)

    def test_private_compile_is_revalidated_immediately_after_snapshot_wrapper(self) -> None:
        from maximum_optimizer import adaptive_direct_scheduler as module

        for action in ("mutate", "cancel"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture_root = root / "fixture"; fixture_root.mkdir()
                fixture = DirectCompositorFixture(fixture_root, visual_count=1)
                event = threading.Event()
                callbacks, base = self._authorized_callbacks(
                    root, fixture, event=event,
                )
                original = module._snapshot_authorized_compile

                def wrapped(compiled, current):
                    result = original(compiled, current)
                    if action == "mutate":
                        artifact = (
                            result.build.compiled_models_dir
                            / result.compile_files[0].relative_path
                        )
                        artifact.write_bytes(b"mutated-private-snapshot")
                    else:
                        current.set()
                    return result

                with mock.patch.object(
                    module, "_snapshot_authorized_compile", side_effect=wrapped,
                ):
                    result = execute_adaptive_direct_schedule(**callbacks)
                self.assertEqual(
                    result.attempts[0].status,
                    "final_whole_failed" if action == "mutate" else "cancelled",
                )
                self.assertIs(result.selected, base)

    def test_cancel_during_private_size_construction_cannot_authorize(self) -> None:
        from maximum_optimizer import adaptive_direct_scheduler as module

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            event = threading.Event()
            callbacks, base = self._authorized_callbacks(
                root, fixture, event=event,
            )
            original = module._size_from_compile_files

            def cancel_after_size(build, files):
                result = original(build, files)
                event.set()
                return result

            with mock.patch.object(
                module, "_size_from_compile_files", side_effect=cancel_after_size,
            ):
                result = execute_adaptive_direct_schedule(**callbacks)
            self.assertEqual(result.attempts[0].status, "cancelled")
            self.assertIs(result.selected, base)

    def test_mutation_during_size_construction_revokes_selected_direct_attempt(self) -> None:
        from maximum_optimizer import adaptive_direct_scheduler as module

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            callbacks, base = self._authorized_callbacks(root, fixture)
            base = replace(base, size=CompiledSizeSnapshot(
                base.size.root, 100, {".mdl": 100}, {},
                (ArtifactStat("base.mdl", ".mdl", 100),),
            ))
            callbacks["base_proof"] = self._proof(fixture, base)
            original = module._size_from_compile_files

            def mutate_after_size(build, files):
                result = original(build, files)
                artifact = build.compiled_models_dir / files[0].relative_path
                artifact.write_bytes(b"mutated-during-size")
                return result

            with mock.patch.object(
                module, "_size_from_compile_files", side_effect=mutate_after_size,
            ):
                result = execute_adaptive_direct_schedule(**callbacks)
            attempt = result.attempts[0]
            self.assertEqual(attempt.status, "final_whole_failed")
            self.assertIsNone(attempt.build)
            self.assertIsNone(attempt.evidence)
            self.assertIs(result.selected, base)

    def test_mutation_after_attempt_create_revokes_selected_direct_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            callbacks, base = self._authorized_callbacks(root, fixture)
            base = replace(base, size=CompiledSizeSnapshot(
                base.size.root, 100, {".mdl": 100}, {},
                (ArtifactStat("base.mdl", ".mdl", 100),),
            ))
            callbacks["base_proof"] = self._proof(fixture, base)
            original = AdaptiveDirectExecutionAttempt.create.__func__

            def mutate_after_create(cls, ratio, status, **values):
                attempt = original(cls, ratio, status, **values)
                if status == "authorized":
                    artifact = (
                        attempt.build.compiled_models_dir
                        / attempt.evidence.compile_files[0].relative_path
                    )
                    artifact.write_bytes(b"mutated-after-attempt-create")
                return attempt

            with mock.patch.object(
                AdaptiveDirectExecutionAttempt, "create",
                new=classmethod(mutate_after_create),
            ):
                result = execute_adaptive_direct_schedule(**callbacks)
            attempt = result.attempts[0]
            self.assertEqual(attempt.status, "final_whole_failed")
            self.assertIsNone(attempt.build)
            self.assertIsNone(attempt.evidence)
            self.assertIs(result.selected, base)

    def test_base_selected_after_callback_is_revalidated_immediately_before_return(self) -> None:
        from maximum_optimizer import adaptive_direct_scheduler as module

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            callbacks, base = self._authorized_callbacks(root, fixture)
            original = module._size_from_compile_files
            callback_completed = False

            def make_direct_larger(build, files):
                nonlocal callback_completed
                result = original(build, files)
                callback_completed = True
                return result

            def current_authority(proof, _event=None):
                if callback_completed:
                    raise ValueError("base changed during size callback")
                return proof

            self._authority_mock.side_effect = current_authority
            with mock.patch.object(
                module, "_size_from_compile_files", side_effect=make_direct_larger,
            ):
                with self.assertRaisesRegex(ValueError, "base changed during size callback"):
                    execute_adaptive_direct_schedule(**callbacks)
            self.assertIsNotNone(base)

    def test_failed_terminal_stages_never_seal_mutated_compile_build_or_evidence(self) -> None:
        for stage in ("structural", "focused", "final"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture_root = root / "fixture"; fixture_root.mkdir()
                fixture = DirectCompositorFixture(fixture_root, visual_count=1)
                callbacks, base = self._authorized_callbacks(root, fixture)

                def mutate(compiled):
                    artifact = (
                        compiled.build.compiled_models_dir
                        / compiled.compile_files[0].relative_path
                    )
                    artifact.write_bytes(f"mutated-{stage}".encode("ascii"))

                if stage == "structural":
                    def fail_structural(_scheduled, composed, compiled, _event):
                        result = structural(
                            composed.composition, compiled.compile_files,
                            passed=False,
                        )
                        mutate(compiled)
                        return result
                    callbacks["authorize_structural"] = fail_structural
                elif stage == "focused":
                    def fail_focus(_outcome, compiled, source, *_args):
                        mutate(compiled)
                        return _union_record(source, fixture, passed=False)
                    callbacks["render_source_union"] = fail_focus
                else:
                    def fail_final(scheduled, composed, compiled, struct, _event):
                        result = build_final_whole_authorization_evidence(
                            scheduled.spec.candidate_id,
                            struct.candidate_cache_digest,
                            scheduled.spec.composite_recipe.recipe_sha256,
                            composed.composition.evidence_sha256,
                            struct.compile_manifest_sha256,
                            "logs/whole-visual-index.json", "a" * 64, "b" * 64,
                            ValidationResult(False),
                        )
                        mutate(compiled)
                        return result
                    callbacks["authorize_final_whole"] = fail_final

                result = execute_adaptive_direct_schedule(**callbacks)
                attempt = result.attempts[0]
                self.assertEqual(
                    attempt.status,
                    {"structural": "structural_failed",
                     "focused": "focused_failed",
                     "final": "final_whole_failed"}[stage],
                )
                self.assertIsNone(attempt.build)
                self.assertIsNone(attempt.evidence)
                self.assertIs(result.selected, base)

    def test_compile_exception_never_seals_a_mutated_composition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            callbacks, base = self._authorized_callbacks(root, fixture)

            def mutate_then_raise(_scheduled, composed, _event):
                source = next((composed.workspace / "src").rglob("*.smd"))
                source.write_bytes(source.read_bytes() + b"\ncompile-mutation")
                raise RuntimeError("compiler failed after mutating composition")

            callbacks["compile_candidate"] = mutate_then_raise
            result = execute_adaptive_direct_schedule(**callbacks)
            attempt = result.attempts[0]
            self.assertEqual(attempt.status, "compile_failed")
            self.assertIsNone(attempt.build)
            self.assertIsNone(attempt.evidence)
            self.assertIs(result.selected, base)

    def test_private_snapshot_cleanup_preserves_injected_foreign_descendant(self) -> None:
        from maximum_optimizer import adaptive_direct_scheduler as module

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            workspace = root / "workspace"; compiled_root = workspace / "compiled/models"
            compiled_root.mkdir(parents=True)
            artifact = compiled_root / "task6.mdl"; artifact.write_bytes(b"compiled")
            proof = CompileFileProof(
                "task6.mdl", ".mdl", len(b"compiled"),
                hashlib.sha256(b"compiled").hexdigest(),
            )
            compiled = AdaptiveDirectCompileResult.create(CandidateBuild(
                fixture.base_spec, workspace, workspace / "source.qc",
                compiled_root, {}, {}, (), None,
            ), (proof,), "a" * 64)
            original_copy = module._copy_file_no_follow
            injected = {}

            def inject(source, destination, current, **kwargs):
                original_copy(source, destination, current, **kwargs)
                private_root = next(
                    parent for parent in destination.parents
                    if parent.name.startswith(".authorized-compile-")
                )
                foreign = private_root / "foreign"
                foreign.mkdir()
                sentinel = foreign / "sentinel.txt"
                sentinel.write_text("foreign", encoding="utf-8")
                injected["root"] = private_root
                injected["sentinel"] = sentinel
                raise RuntimeError("injected copy failure")

            with mock.patch.object(module, "_copy_file_no_follow", side_effect=inject):
                with self.assertRaisesRegex(RuntimeError, "injected copy failure"):
                    _snapshot_authorized_compile(compiled, threading.Event())
            self.assertTrue(injected["root"].is_dir())
            self.assertEqual(
                injected["sentinel"].read_text(encoding="utf-8"), "foreign"
            )

    def test_private_snapshot_cleanup_never_unlinks_descendants_by_path(self) -> None:
        from maximum_optimizer import adaptive_direct_scheduler as module

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            root = parent / "owned-private"
            root.mkdir()
            artifact = root / "artifact.mdl"
            artifact.write_bytes(b"owned")
            ownership = module._PrivateSnapshotOwnership(
                module._snapshot_identity(root, directory=True),
                {"artifact.mdl": module._snapshot_identity(
                    artifact, directory=False,
                )},
                {"artifact.mdl": (
                    len(b"owned"), hashlib.sha256(b"owned").hexdigest(),
                )},
            )
            with mock.patch.object(
                Path, "unlink",
                side_effect=AssertionError("pathname unlink is forbidden"),
            ):
                cleaned = module._cleanup_private_snapshot(root, ownership)
            self.assertFalse(cleaned)
            self.assertFalse(root.exists())
            quarantines = tuple(parent.glob(
                ".owned-private.adaptive-snapshot-cleanup-*"
            ))
            self.assertEqual(len(quarantines), 1)
            self.assertEqual(
                (quarantines[0] / "artifact.mdl").read_bytes(), b"owned"
            )

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
            compiled_roots = {}
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
                compiled_roots[composed.workspace.parent.name] = compiled
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

            def execute(name: str, event: threading.Event | None = None):
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
                    cancel_event=event or threading.Event(),
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
            late_event = threading.Event()
            original_create = AdaptiveDirectExecutionAttempt.create.__func__
            def late_create(cls, ratio, status, **values):
                attempt = original_create(cls, ratio, status, **values)
                if status == "authorized":
                    late_event.set()
                return attempt
            with mock.patch.object(
                AdaptiveDirectExecutionAttempt, "create",
                new=classmethod(late_create),
            ):
                late_cancel_race = execute("late-cancel-race", late_event)
            original_authorized = compiled_roots["adaptive"] / "task6.mdl"
            original_authorized.write_bytes(b"mutated-after-private-snapshot")
            selected_artifact = (
                result.selected.compiled_models_dir
                / result.selected.size.artifacts[0].relative_path
            )
            private_snapshot_bytes = selected_artifact.read_bytes()

        self.assertEqual(
            result.attempts[0].status, "authorized", result.attempts[0].error
        )
        self.assertIs(result.selected, result.attempts[0].evaluation)
        self.assertEqual(result.selected.size.total_bytes, 40)
        self.assertNotEqual(
            result.attempts[0].build.compiled_models_dir,
            root / "adaptive/compiled/models",
        )
        self.assertEqual(private_snapshot_bytes, b"x" * 40)
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
        self.assertEqual(late_cancel_race.attempts[0].status, "authorized")
        self.assertFalse(late_cancel_race.cancelled)
        self.assertTrue(late_event.is_set())
        self.assertNotIn(("structural", True), events)
        self.assertNotIn(("final-whole", True), events)
        self.assertEqual(sum(item[0] == "final-whole" for item in events), 5)
        self.assertEqual(events.count(("source-union", "meshes/part-00.smd")), 5)
        self.assertEqual(events.count(("source-union", "meshes/part-01.smd")), 5)

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

    def test_rejects_foreign_composition_and_compile_bindings_before_structural(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            structural_calls = []
            def foreign_compose(scheduled, _workspace, event):
                return compose_candidate_sources(
                    fixture.base_build, scheduled.spec.composite_recipe,
                    fixture.resolver, root / "foreign-composition", event,
                    coverage_manifest=fixture.coverage,
                )
            foreign = execute_adaptive_direct_schedule(**self._callbacks(
                root, fixture, compose_candidate=foreign_compose,
                compile_candidate=lambda *_: self.fail("foreign composition compiled"),
                authorize_structural=lambda *_: structural_calls.append("foreign"),
            ))
            self.assertEqual(foreign.attempts[0].status, "composition_failed")

        for forged_hash in (False, True):
            with self.subTest(forged_hash=forged_hash), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve(); fixture_root = root / "fixture"; fixture_root.mkdir()
                fixture = DirectCompositorFixture(fixture_root, visual_count=1)
                structural_calls = []
                def compose(scheduled, workspace, event):
                    return compose_candidate_sources(
                        fixture.base_build, scheduled.spec.composite_recipe,
                        fixture.resolver, workspace, event,
                        coverage_manifest=fixture.coverage,
                    )
                def compile_candidate(scheduled, composed, _event):
                    build_workspace = composed.workspace if forged_hash else root / "foreign-build"
                    compiled = build_workspace / "compiled/models"; compiled.mkdir(parents=True)
                    model = compiled / "task6.mdl"; model.write_bytes(b"compiled")
                    proof = CompileFileProof("task6.mdl", ".mdl", 8, hashlib.sha256(b"compiled").hexdigest())
                    build = CandidateBuild(
                        scheduled.spec, build_workspace, composed.optimized_qc,
                        compiled, {}, {}, (), None,
                    )
                    return AdaptiveDirectCompileResult.create(
                        build, (proof,),
                        "f" * 64 if forged_hash else composed.composition.evidence_sha256,
                    )
                result = execute_adaptive_direct_schedule(**self._callbacks(
                    root, fixture, compose_candidate=compose,
                    compile_candidate=compile_candidate,
                    authorize_structural=lambda *_: structural_calls.append("ran"),
                ))
                self.assertEqual(result.attempts[0].status, "compile_failed")
                self.assertEqual(structural_calls, [])

    def test_revalidates_retained_base_after_ratio_callbacks_before_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            base = self._callbacks(root, fixture)["base_proof"]
            self._authority_mock.side_effect = (base, ValueError("base changed during ratio access"))
            with self.assertRaisesRegex(ValueError, "base changed"):
                execute_adaptive_direct_schedule(**self._callbacks(
                    root, fixture, base_proof=base,
                    compose_candidate=lambda *_: self.fail("composition ran"),
                ))
            self.assertEqual(self._authority_mock.call_count, 2)
            self.assertFalse((root / "adaptive").exists())
            self.assertEqual(tuple(root.glob(".adaptive.adaptive-reservation-*")), ())

    def test_ratio_callback_workspace_collision_is_terminal_and_not_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve(); fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            collision = root / "adaptive"
            def access(_ratio):
                collision.mkdir()
                (collision / "foreign.txt").write_text("foreign", encoding="utf-8")
                return fixture.requests, fixture.snapshots
            result = execute_adaptive_direct_schedule(**self._callbacks(
                root, fixture, access_ratio=access,
                compose_candidate=lambda *_: self.fail("collision composed"),
            ))
            self.assertEqual(result.attempts[0].status, "composition_failed")
            self.assertRegex(result.attempts[0].attempt_sha256, r"^[0-9a-f]{64}$")
            self.assertEqual((collision / "foreign.txt").read_text(encoding="utf-8"), "foreign")
            self.assertEqual(tuple(root.glob(".adaptive.adaptive-reservation-*")), ())

    def test_attempt_cross_invariants_and_normative_tie_break(self) -> None:
        with self.assertRaises((TypeError, ValueError)):
            AdaptiveDirectExecutionAttempt(0.5, "authorized", candidate_id="wrong")


if __name__ == "__main__":
    unittest.main()
