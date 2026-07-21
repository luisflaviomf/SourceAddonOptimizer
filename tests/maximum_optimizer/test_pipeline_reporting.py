from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import unittest

from maximum_optimizer.contracts import ValidationDecision
from maximum_optimizer.pipeline import (
    CompileResult,
    MaximumRunOptions,
    _pose_contract,
    run_maximum_adaptive,
    simplify_smd_region,
)
from maximum_optimizer.profile import load_profile
from maximum_optimizer.qc_graph import QcOccurrence
from maximum_optimizer.regions import build_region_graph
from maximum_optimizer.rendering import RenderEvidence
from maximum_optimizer.smd import SmdDocument, SmdInfluence, SmdTriangle, SmdVertex, parse_smd


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROFILE = load_profile(REPO_ROOT / "maximum_optimizer" / "profiles" / "maximum-adaptive-v2.json")
OCCURRENCE = QcOccurrence(
    PurePosixPath("vehicle.qc"), "$body", 1, PurePosixPath("vehicle.smd")
)


class NoCancellation:
    def throw_if_cancelled(self) -> None:
        return None


class AdaptivePipelineTests(unittest.TestCase):
    def test_small_regions_use_a_bounded_sample_budget(self) -> None:
        graph = build_region_graph(
            parse_smd((FIXTURES / "two_components.smd").read_text(encoding="utf-8")),
            OCCURRENCE,
        )

        contract = _pose_contract(graph.regions[0], PROFILE)

        self.assertEqual(contract.sample_count, 64)

    def test_real_native_adapter_reduces_planar_region_and_preserves_attributes(self) -> None:
        triangles = []

        def vertex(x, y):
            return SmdVertex(
                0,
                (float(x), float(y), 0.0),
                (0.0, 0.0, 1.0),
                (x / 6.0, y / 6.0),
                (SmdInfluence(0, 1.0),),
            )

        for y in range(6):
            for x in range(6):
                triangles.append(SmdTriangle("paint", (vertex(x, y), vertex(x + 1, y), vertex(x + 1, y + 1)), len(triangles)))
                triangles.append(SmdTriangle("paint", (vertex(x, y), vertex(x + 1, y + 1), vertex(x, y + 1)), len(triangles)))
        document = SmdDocument(("version 1",), tuple(triangles))
        region = build_region_graph(document, OCCURRENCE).regions[0]

        simplified = simplify_smd_region(region, 0.35, PROFILE.limits)

        self.assertLess(len(simplified.triangles), len(region.triangles))
        self.assertEqual(simplified.material, region.material)
        self.assertEqual(simplified.bone_ids, region.bone_ids)

    def _fixture_options(self, root: Path, *, reject_key: str | None = None) -> MaximumRunOptions:
        addon = root / "addon"
        original = root / "original"
        normal = root / "normal"
        final_models = root / "compiled-models"
        for path in (addon / "models", addon / "materials", original, normal, final_models):
            path.mkdir(parents=True, exist_ok=True)
        (addon / "models" / "vehicle.mdl").write_bytes(b"o" * 100)
        (addon / "models" / "vehicle.dx80.vtx").write_bytes(b"8" * 40)
        (final_models / "vehicle.mdl").write_bytes(b"n" * 50)
        for material in ("paint", "glass"):
            (addon / "materials" / f"{material}.vmt").write_text(
                '"VertexLitGeneric"\n{\n  "$basetexture" "cars/test"\n}\n',
                encoding="utf-8",
            )
        shutil.copy2(FIXTURES / "two_components.smd", original / "vehicle.smd")
        shutil.copy2(FIXTURES / "two_components_OPT.smd", normal / "vehicle.smd")
        qc = '$body "body" "vehicle.smd"\n'
        (original / "vehicle.qc").write_text(qc, encoding="utf-8")
        (normal / "vehicle.qc").write_text(qc, encoding="utf-8")

        def validate(_original, _candidate, _budget, key):
            if key.value == reject_key:
                return ValidationDecision(False, ("silhouette",), 0.0)
            return ValidationDecision(True, (), 0.5)

        return MaximumRunOptions(
            addon_root=addon,
            original_source_root=original,
            normal_source_root=normal,
            staging_root=root / "staging",
            cache_root=root / "cache",
            report_path=root / "maximum-report.json",
            profile=PROFILE,
            framework_resolver_root=None,
            blender=Path("C:/Blender/blender.exe"),
            compile_family=lambda _tree: CompileResult(True, final_models, None, root / "compile.log"),
            render_region=lambda _request: self.fail("high-margin opaque regions must not render"),
            cancel=NoCancellation(),
            simplify_region=lambda region, _ratio, _budget: replace(
                region,
                triangles=region.triangles[: max(1, len(region.triangles) - 1)],
            ),
            validate_candidate=validate,
        )

    def test_one_failed_wheel_does_not_preserve_family(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original_document = parse_smd((FIXTURES / "two_components.smd").read_text(encoding="utf-8"))
            graph = build_region_graph(original_document, OCCURRENCE)
            wheel_key = next(region.key.value for region in graph.regions if region.centroid[0] > 4.0)
            report = run_maximum_adaptive(self._fixture_options(root, reject_key=wheel_key))

        self.assertEqual(report.family_status, "optimized")
        self.assertGreater(report.regions.aggressive, 0)
        self.assertEqual(report.regions.original_fallback, 1)
        self.assertEqual(report.full_family_renders, 0)
        self.assertLessEqual(report.studiomdl_compiles, 2)

    def test_low_risk_region_accepts_aggressive_normal_seed_without_metric_search(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            options = replace(self._fixture_options(root), validate_candidate=None)

            report = run_maximum_adaptive(options)
            paint = next(item for item in report.region_details if item.material == "paint")

        self.assertEqual(paint.representation, "normal")
        self.assertEqual(paint.simplifier_evaluations, 0)
        self.assertIn("low-risk classifier", paint.reason)

    def test_report_is_atomic_recomputable_and_dx80_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            options = self._fixture_options(root)
            report = run_maximum_adaptive(options)
            payload = json.loads(options.report_path.read_text(encoding="utf-8"))
            temporary_reports = tuple(options.report_path.parent.glob("*.tmp"))

        self.assertEqual(
            report.sizes.original_comparable - report.sizes.final_comparable,
            report.sizes.saved_comparable,
        )
        self.assertNotEqual(report.sizes.dx80_removed, report.sizes.saved_comparable)
        self.assertEqual(payload["sizes"]["saved_comparable"], report.sizes.saved_comparable)
        self.assertEqual(payload["profile"]["sha256"], PROFILE.sha256)
        self.assertEqual(temporary_reports, ())

    def test_failed_render_after_lighter_recovery_restores_only_that_region(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            graph = build_region_graph(
                parse_smd((FIXTURES / "two_components.smd").read_text(encoding="utf-8")),
                OCCURRENCE,
            )
            target = next(region for region in graph.regions if len(region.triangles) == 2)
            options = self._fixture_options(root)
            (options.addon_root / "materials" / "paint.vmt").write_text(
                '"VertexLitGeneric"\n{\n  "$translucent" "1"\n}\n',
                encoding="utf-8",
            )

            def validate(_original, candidate, _budget, key):
                if key == target.key and len(candidate.triangles) == 2:
                    return ValidationDecision(False, ("silhouette",), 0.0)
                return ValidationDecision(True, (), 0.5)

            def render(request):
                passed = target.key.value not in request.output_dir.as_posix()
                return RenderEvidence(passed, 0.0 if passed else 1.0, 0.0, request.output_dir, "e" * 64, False)

            report = run_maximum_adaptive(
                replace(options, validate_candidate=validate, render_region=render)
            )
            detail = next(value for value in report.region_details if value.key == target.key.value)

        self.assertEqual(detail.representation, "original")
        self.assertIn("targeted render failed", detail.reason)

    def test_normal_only_extra_component_is_ignored_without_inflating_triangle_totals(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            options = self._fixture_options(root)
            original_text = (FIXTURES / "two_components.smd").read_text(encoding="utf-8")
            extra = "paint\n0 8.02 0 0 0 0 1 0 0\n0 9.02 0 0 0 0 1 1 0\n0 8.52 1 0 0 0 1 0.5 1\n"
            normal_text = original_text.replace("glass\n", extra + "glass\n")
            (options.original_source_root / "ambiguous.smd").write_text(original_text, encoding="utf-8")
            (options.normal_source_root / "ambiguous.smd").write_text(normal_text, encoding="utf-8")
            for source_root in (options.original_source_root, options.normal_source_root):
                qc = source_root / "vehicle.qc"
                qc.write_text(
                    qc.read_text(encoding="utf-8") + '$body "ambiguous" "ambiguous.smd"\n',
                    encoding="utf-8",
                )

            report = run_maximum_adaptive(options)

        selected_regions = sum(item.selected_triangles for item in report.region_details)
        self.assertEqual(report.original_triangles, 8)
        self.assertEqual(report.normal_triangles, 9)
        self.assertEqual(report.final_triangles, selected_regions)
        self.assertEqual(report.regions.ambiguous, 0)
        self.assertLessEqual(report.final_triangles, report.original_triangles)


if __name__ == "__main__":
    unittest.main()
