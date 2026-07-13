from __future__ import annotations

from pathlib import Path
import hashlib
import tempfile
import threading
import unittest

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
)
from maximum_optimizer.production_adapters import AdaptiveDirectCompileResult
from maximum_optimizer.adaptive_direct_scheduler import (
    execute_adaptive_direct_schedule,
    require_adaptive_direct_source_count,
)
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


class AdaptiveDirectSchedulerTests(unittest.TestCase):
    def _callbacks(self, root: Path, fixture, **overrides):
        values = {
            "base_evaluation": CandidateEvaluation(
                fixture.base_spec, _empty_size(fixture.base_build.compiled_models_dir),
                ValidationResult(True), ValidationResult(True),
                fixture.base_build.compiled_models_dir, ValidationResult(True),
                {_focus_target().region_key: FocusRegionResult(
                    _focus_target(), ValidationResult(True), "a" * 64, False,
                )},
            ),
            "base_build": fixture.base_build,
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

    def test_rejects_base_without_fresh_whole_and_focused_approval_before_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture_root = root / "fixture"; fixture_root.mkdir()
            fixture = DirectCompositorFixture(fixture_root, visual_count=1)
            invalid = CandidateEvaluation(
                fixture.base_spec, _empty_size(fixture.base_build.compiled_models_dir),
                ValidationResult(True), ValidationResult(True),
                fixture.base_build.compiled_models_dir,
            )
            reserved = []
            with self.assertRaisesRegex(ValueError, "approved ordinary"):
                execute_adaptive_direct_schedule(**self._callbacks(
                    root, fixture, base_evaluation=invalid,
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
                base_evaluation=base,
                base_build=fixture.base_build,
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
            mode = {"size": 40, "structural": True, "focus": True, "union": True, "final": True}

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

            def structural_authorization(_scheduled, composed, compiled):
                events.append(("structural",))
                return structural(
                    composed.composition, compiled.compile_files,
                    passed=mode["structural"],
                )

            def rerender(*_args):
                events.append(("base-focus",))
                if mode["focus"]:
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

            def final_whole(scheduled, composed, compiled, structural_evidence):
                events.append(("final-whole",))
                return build_final_whole_authorization_evidence(
                    scheduled.spec.candidate_id,
                    structural_evidence.candidate_cache_digest,
                    scheduled.spec.composite_recipe.recipe_sha256,
                    composed.composition.evidence_sha256,
                    structural_evidence.compile_manifest_sha256,
                    "logs/whole-visual-index.json", "a" * 64, "b" * 64,
                    (
                        ValidationResult(True) if mode["final"] else
                        ValidationResult(False, (GateFailure(
                            "whole", "candidate", 1.0, 0.0, "failed",
                        ),))
                    ),
                )

            def execute(name: str):
                return execute_adaptive_direct_schedule(
                    base_evaluation=base, base_build=fixture.base_build,
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

        self.assertEqual(
            result.attempts[0].status, "authorized", result.attempts[0].error
        )
        self.assertIs(result.selected, result.attempts[0].evaluation)
        self.assertEqual(result.selected.size.total_bytes, 40)
        self.assertEqual(structural_failed.attempts[0].status, "structural_failed")
        self.assertEqual(
            focused_failed.attempts[0].status, "focused_failed",
            focused_failed.attempts[0].error,
        )
        self.assertEqual(final_failed.attempts[0].status, "final_whole_failed")
        self.assertEqual(larger_authorized.attempts[0].status, "authorized")
        self.assertIs(larger_authorized.selected, base)
        self.assertEqual(events.count(("final-whole",)), 3)
        self.assertEqual(events.count(("source-union", "meshes/part-00.smd")), 3)
        self.assertEqual(events.count(("source-union", "meshes/part-01.smd")), 3)


if __name__ == "__main__":
    unittest.main()
