from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from maximum_optimizer.domain import ValidationResult
from maximum_optimizer.processes import ProcessCancelledError
from maximum_optimizer.source_union import (
    SourceUnionMaterialBinding,
    SourceUnionMaskObservation,
    SourceUnionRenderOutput,
    validate_adaptive_direct_source_union,
)
from maximum_optimizer.visual_validation import FidelityProfile, REQUIRED_METRICS
from tests.maximum_optimizer.test_task6_direct_compositor import DirectCompositorFixture


H = {letter: letter * 64 for letter in "0123456789abcdef"}
CAMERAS = tuple(f"camera-{index:02d}" for index in range(8))
PASSES = ("clay", "textured")


def _profile() -> FidelityProfile:
    return FidelityProfile(1, "source-union-test", True, H["f"], {
        metric: 1.0 for metric in REQUIRED_METRICS
    })


class HermeticRenderer:
    def __init__(
        self, *, missing: str | None = None, extra_file: bool = False,
        occluded: str | None = None, extra_observation: bool = False,
        set_cancel: threading.Event | None = None,
    ) -> None:
        self.missing = missing
        self.extra_file = extra_file
        self.occluded = occluded
        self.extra_observation = extra_observation
        self.set_cancel = set_cancel
        self.requests = []

    def __call__(self, request, output_root: Path, cancel_event):
        self.requests.append(request)
        root = output_root / "renders"
        for side in ("candidate", "reference"):
            for pose in request.target.pose_keys:
                for render_pass in PASSES:
                    for camera in CAMERAS:
                        relative = (
                            f"source-union/{request.target.union_key}/{side}/"
                            f"{pose}/{render_pass}/{camera}.png"
                        )
                        if relative == self.missing:
                            continue
                        path = root / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        Image.new("RGBA", (4, 4), (255, 255, 255, 255)).save(path)
        if self.extra_file:
            (root / "extra.bin").write_bytes(b"extra")
        observations = []
        for side in ("candidate", "reference"):
            for component in request.target.component_keys:
                for pose in request.target.pose_keys:
                    for index, camera in enumerate(CAMERAS):
                        pixels = 0 if component == self.occluded else (3 if index == 1 else 0)
                        observations.append(SourceUnionMaskObservation(
                            side, component, pose, camera, pixels,
                        ))
        if self.extra_observation:
            observations.append(SourceUnionMaskObservation(
                "reference", "extra-component", "bind", "camera-00", 1,
            ))
        if self.set_cancel is not None:
            self.set_cancel.set()
        return SourceUnionRenderOutput(root, tuple(observations))


class SourceUnionRuntimeTests(unittest.TestCase):
    def _run(
        self, fixture, renderer, workspace, *, comparator=None, event=None,
        dependency=None, bindings=None,
    ):
        source = next(
            item for item in fixture.coverage.sources
            if item.source_identity == fixture.requests[0].source_identity
        )
        return validate_adaptive_direct_source_union(
            coverage=fixture.coverage, source_proof=source,
            snapshot=fixture.snapshots[0], workspace=workspace,
            dependency_proof_sha256=(
                fixture.requests[0].dependency_proof_sha256
                if dependency is None else dependency
            ),
            material_bindings=bindings or (SourceUnionMaterialBinding(
                source.material_region_keys[0], source.witnesses[0].material_contract_sha256,
            ),),
            profile=_profile(), renderer=renderer,
            comparator=comparator or (lambda *_: ValidationResult(True)),
            cancel_event=event,
        )

    def test_renders_exact_source_local_union_and_first_bilateral_camera(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture = DirectCompositorFixture(
                root, components=("component-a", "component-b"),
                poses=("bind", "turn"),
            )
            renderer = HermeticRenderer()
            record = self._run(fixture, renderer, root / "union", comparator=lambda *_: ValidationResult(True))
            self.assertEqual(len(record.files), 64)
            self.assertEqual(len(record.visibility), 4)
            self.assertTrue(all(item.camera_key == "camera-01" for item in record.visibility))
            request = renderer.requests[0]
            self.assertEqual(request.reference_source, fixture.base_root / fixture.requests[0].source_relative_path)
            self.assertEqual(request.candidate_source, fixture.snapshots[0].source_root / "output.smd")
            self.assertFalse(hasattr(request, "state_key"))
            self.assertFalse(hasattr(request, "bodygroup_key"))
            self.assertFalse(hasattr(request, "lod_key"))

    def test_material_dependency_bindings_and_fresh_execution_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture = DirectCompositorFixture(root)
            renderer = HermeticRenderer()
            first = self._run(fixture, renderer, root / "first")
            second = self._run(fixture, renderer, root / "second")
            self.assertEqual(len(renderer.requests), 2)
            self.assertEqual(first.evidence_sha256, second.evidence_sha256)
            for label, changes in (
                ("dependency", {"dependency": H["0"]}),
                ("material", {"bindings": (
                    SourceUnionMaterialBinding("material-000", H["0"]),
                )}),
                ("region", {"bindings": (
                    SourceUnionMaterialBinding("wrong-region", H["4"]),
                )}),
            ):
                guarded = HermeticRenderer()
                with self.subTest(label=label), self.assertRaises(ValueError):
                    self._run(fixture, guarded, root / f"bad-{label}", **changes)
                self.assertEqual(guarded.requests, [])

    def test_missing_extra_occluded_or_extra_visibility_fails_closed(self) -> None:
        variants = (
            HermeticRenderer(missing="source-union/{key}/candidate/bind/clay/camera-00.png"),
            HermeticRenderer(extra_file=True),
            HermeticRenderer(occluded="component-000"),
            HermeticRenderer(extra_observation=True),
        )
        for ordinal, renderer in enumerate(variants):
            with self.subTest(ordinal=ordinal), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                fixture = DirectCompositorFixture(root)
                if renderer.missing:
                    renderer.missing = renderer.missing.format(
                        key=next(item for item in fixture.coverage.sources if item.eligibility_kind == "eligible-exact-v1").source_coverage_sha256[:32]
                    ).replace("source-union/", "source-union/source-union-")
                workspace = root / "union"
                with self.assertRaises(ValueError):
                    self._run(fixture, renderer, workspace)
                self.assertFalse(workspace.exists())

    def test_same_size_post_compare_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture = DirectCompositorFixture(root)

            def mutate(reference, candidate, _profile):
                path = next(candidate.rglob("*.png"))
                data = path.read_bytes()
                path.write_bytes(data[:-1] + bytes((data[-1] ^ 1,)))
                return ValidationResult(True)

            workspace = root / "union"
            with self.assertRaises(ValueError):
                self._run(fixture, HermeticRenderer(), workspace, comparator=mutate)
            self.assertFalse(workspace.exists())

    def test_lexical_dotdot_render_root_cannot_escape_private_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture = DirectCompositorFixture(root)
            base_renderer = HermeticRenderer()

            def escaping_renderer(request, workspace, event):
                outside_owner = workspace.parent / "outside-owner"
                rendered = base_renderer(request, outside_owner, event)
                lexical = workspace / ".." / "outside-owner" / "renders"
                return SourceUnionRenderOutput(lexical, rendered.observations)

            workspace = root / "union"
            with self.assertRaises(ValueError):
                self._run(fixture, escaping_renderer, workspace)
            self.assertFalse(workspace.exists())

    def test_stale_snapshot_bindings_cancel_and_reparse_fail_before_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture = DirectCompositorFixture(root)
            (fixture.snapshots[0].source_root / "output.smd").write_bytes(b"stale")
            renderer = HermeticRenderer()
            with self.assertRaises(ValueError):
                self._run(fixture, renderer, root / "stale")
            self.assertEqual(renderer.requests, [])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture = DirectCompositorFixture(root)
            event = threading.Event()
            renderer = HermeticRenderer(set_cancel=event)
            with self.assertRaises(ProcessCancelledError):
                self._run(fixture, renderer, root / "cancel", event=event)
            self.assertFalse((root / "cancel").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            fixture = DirectCompositorFixture(root)
            workspace = root / "reparse"
            detected = False

            def one_reparse(path):
                nonlocal detected
                if not detected and Path(path).name == "renders":
                    detected = True
                    return True
                return False

            with mock.patch(
                "maximum_optimizer.source_union._is_reparse",
                side_effect=one_reparse,
            ):
                with self.assertRaises(ValueError):
                    self._run(fixture, HermeticRenderer(), workspace)
            self.assertFalse(workspace.exists())


if __name__ == "__main__":
    unittest.main()
