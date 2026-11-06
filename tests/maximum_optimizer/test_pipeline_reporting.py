from __future__ import annotations

import gc
import hashlib
import json
from dataclasses import replace
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch
from uuid import UUID
import weakref

import maximum_optimizer.pipeline as pipeline_module
from maximum_optimizer.contracts import ValidationDecision
from maximum_optimizer.pipeline import (
    CompileResult,
    MaximumRunOptions,
    _pose_contract,
    _write_region_smd,
    run_maximum_adaptive,
    simplify_smd_region,
)
from maximum_optimizer.profile import load_profile
from maximum_optimizer.qc_graph import QcOccurrence, scan_qc_occurrences
from maximum_optimizer.qc_runtime import QcRuntimeBinding, QcRuntimeBindings
from maximum_optimizer.regions import build_region_graph
from maximum_optimizer.rendering import RenderEvidence
from maximum_optimizer.runtime_profile import RuntimeEvidenceMap
from maximum_optimizer.smd import SmdDocument, SmdInfluence, SmdTriangle, SmdVertex, parse_smd, serialize_smd


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROFILE = load_profile(REPO_ROOT / "maximum_optimizer" / "profiles" / "maximum-adaptive-v2.json")
OCCURRENCE = QcOccurrence(
    PurePosixPath("vehicle.qc"), "$body", 2, PurePosixPath("vehicle.smd")
)


class NoCancellation:
    def throw_if_cancelled(self) -> None:
        return None


def flat_document(size: int = 9, material: str = "plain") -> SmdDocument:
    triangles = []
    for y in range(size - 1):
        for x in range(size - 1):
            points = (
                (float(x), float(y), 0.0),
                (float(x + 1), float(y), 0.0),
                (float(x + 1), float(y + 1), 0.0),
                (float(x), float(y + 1), 0.0),
            )
            for indices in ((0, 1, 2), (0, 2, 3)):
                vertices = tuple(
                    SmdVertex(
                        0,
                        points[index],
                        (0.0, 0.0, 1.0),
                        (points[index][0] / size, points[index][1] / size),
                        (SmdInfluence(0, 1.0),),
                    )
                    for index in indices
                )
                triangles.append(SmdTriangle(material, vertices, len(triangles)))
    return SmdDocument(("version 1",), tuple(triangles))


def runtime_map(
    occurrence: QcOccurrence | None = None,
    *,
    model_importance: float = 0.0,
    bodygroup: tuple[str, int] | None = None,
    bodygroup_importance: float = 0.0,
) -> RuntimeEvidenceMap:
    model_path = "models/test/vehicle.mdl"
    by_occurrence = {}
    if occurrence is not None:
        by_occurrence[occurrence] = QcRuntimeBinding(
            model_path,
            bodygroup,
            (),
            occurrence.material_directories,
        )
    bodygroups = {}
    if bodygroup is not None and bodygroup_importance > 0.0:
        bodygroups[(model_path, bodygroup[0].casefold(), bodygroup[1])] = bodygroup_importance
    return RuntimeEvidenceMap(
        "a" * 64,
        UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        QcRuntimeBindings(MappingProxyType(by_occurrence)),
        MappingProxyType({model_path: model_importance} if model_importance > 0.0 else {}),
        MappingProxyType(bodygroups),
        MappingProxyType({}),
        MappingProxyType({}),
    )


class AdaptivePipelineTests(unittest.TestCase):
    @staticmethod
    def _tree_sha256(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix().encode("utf-8")
            raw = path.read_bytes()
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
        return digest.hexdigest()

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

    def test_region_spool_does_not_renormalize_skin_weights_before_final_serialization(self) -> None:
        weights = (0.48236792663216077, 0.07435777584206046, 0.44327429752577874)
        vertex = SmdVertex(
            0,
            (0.123456789123456, 2.34567891234567, 3.45678912345678),
            (0.123456789123, 0.234567891234, 0.967890123456),
            (0.123456789123, 0.987654321987),
            tuple(SmdInfluence(index, weight) for index, weight in enumerate(weights)),
        )
        triangle = SmdTriangle("paint", (vertex, vertex, vertex), 0)
        template = build_region_graph(
            parse_smd((FIXTURES / "two_components.smd").read_text(encoding="utf-8")),
            OCCURRENCE,
        ).regions[0]
        region = replace(template, triangle_ordinals=(0,), triangles=(triangle,))
        expected = serialize_smd(SmdDocument(("version 1",), region.triangles))

        with tempfile.TemporaryDirectory() as raw:
            spool = _write_region_smd(Path(raw) / "region.smd", ("version 1",), region)
            actual = serialize_smd(parse_smd(spool.read_text(encoding="utf-8")))

        self.assertEqual(actual, expected)

    def _fixture_options(self, root: Path, *, reject_key: str | None = None) -> MaximumRunOptions:
        addon = root / "addon"
        original = root / "original"
        normal = root / "normal"
        final_models = root / "compiled-models"
        for path in (addon / "models", addon / "materials", original, normal, final_models):
            path.mkdir(parents=True, exist_ok=True)
        (addon / "models" / "vehicle.mdl").write_bytes(b"o" * 100)
        (addon / "models" / "vehicle.phy").write_bytes(b"p" * 24)
        (addon / "models" / "vehicle.dx80.vtx").write_bytes(b"8" * 40)
        (final_models / "vehicle.mdl").write_bytes(b"n" * 50)
        (final_models / "vehicle.vvd").write_bytes(b"v" * 30)
        (final_models / "vehicle.dx90.vtx").write_bytes(b"9" * 20)
        (final_models / "vehicle.phy").write_bytes(b"p" * 24)
        for material in ("paint", "glass"):
            (addon / "materials" / f"{material}.vmt").write_text(
                '"VertexLitGeneric"\n{\n  "$basetexture" "cars/test"\n}\n',
                encoding="utf-8",
            )
        shutil.copy2(FIXTURES / "two_components.smd", original / "vehicle.smd")
        shutil.copy2(FIXTURES / "two_components_OPT.smd", normal / "vehicle.smd")
        qc = '$modelname "models/vehicle.mdl"\n$body "body" "vehicle.smd"\n'
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

    def _install_flat_source(
        self,
        options: MaximumRunOptions,
        *,
        bodygroup: bool,
        grid_size: int = 9,
    ) -> QcOccurrence:
        document = flat_document(grid_size)
        text = serialize_smd(document)
        for source_root in (options.original_source_root, options.normal_source_root):
            (source_root / "vehicle.smd").write_text(text, encoding="utf-8")
            qc = (
                '$modelname "models/test/vehicle.mdl"\n'
                '$bodygroup "wheels"\n{\n studio "vehicle.smd"\n}\n'
                if bodygroup
                else '$modelname "models/test/vehicle.mdl"\n$body "body" "vehicle.smd"\n'
            )
            (source_root / "vehicle.qc").write_text(qc, encoding="utf-8")
        (options.addon_root / "materials" / "plain.vmt").write_text(
            '"VertexLitGeneric"\n{\n  "$basetexture" "test/plain"\n}\n',
            encoding="utf-8",
        )
        return scan_qc_occurrences(options.original_source_root)[0]

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
            paint = next(
                item
                for item in report.region_details
                if item.material == "paint" and "low-risk classifier" in item.reason
            )

        self.assertEqual(paint.representation, "normal")
        self.assertEqual(paint.simplifier_evaluations, 0)
        self.assertIn("low-risk classifier", paint.reason)

    def test_absent_and_empty_runtime_profiles_produce_identical_report_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shared_report = root / "report.json"
            absent = replace(
                self._fixture_options(root / "absent"),
                report_path=shared_report,
                validate_candidate=None,
                runtime_evidence=None,
            )
            empty = replace(
                self._fixture_options(root / "empty"),
                report_path=shared_report,
                validate_candidate=None,
                runtime_evidence=runtime_map(),
            )
            selected_sources = {}

            def capture(options, label):
                compile_family = options.compile_family

                def compile_and_capture(tree):
                    selected_sources[label] = tuple(
                        (
                            path.relative_to(tree).as_posix(),
                            path.read_bytes(),
                        )
                        for path in sorted(tree.rglob("*.smd"), key=lambda value: value.as_posix().casefold())
                    )
                    return compile_family(tree)

                return replace(options, compile_family=compile_and_capture)

            absent = capture(absent, "absent")
            empty = capture(empty, "empty")
            with patch.object(pipeline_module.time, "perf_counter", return_value=10.0), patch.object(
                pipeline_module.time, "process_time", return_value=5.0
            ), patch.object(pipeline_module, "_peak_working_set", return_value=123):
                run_maximum_adaptive(absent)
                absent_bytes = shared_report.read_bytes()
                run_maximum_adaptive(empty)
                empty_bytes = shared_report.read_bytes()

        self.assertEqual(empty_bytes, absent_bytes)
        self.assertEqual(selected_sources["empty"], selected_sources["absent"])
        self.assertNotIn(b'"runtime_profile"', absent_bytes)

    def test_no_profile_maximum_matches_frozen_decision_and_output_golden(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            options = replace(
                self._fixture_options(root),
                validate_candidate=None,
                runtime_evidence=None,
            )
            selected: dict[str, object] = {}
            compile_family = options.compile_family

            def compile_and_capture(tree: Path):
                selected["smd"] = tuple(
                    (path.relative_to(tree).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
                    for path in sorted(tree.rglob("*.smd"), key=lambda value: value.as_posix().casefold())
                )
                selected["tree"] = self._tree_sha256(tree)
                return compile_family(tree)

            with patch.object(pipeline_module.time, "perf_counter", return_value=10.0), patch.object(
                pipeline_module.time, "process_time", return_value=5.0
            ), patch.object(pipeline_module, "_peak_working_set", return_value=123):
                report = run_maximum_adaptive(replace(options, compile_family=compile_and_capture))
            payload = json.loads(options.report_path.read_text(encoding="utf-8"))
            selected["compiled_tree"] = self._tree_sha256(root / "compiled-models")

        decisions = tuple(
            (
                detail.material,
                detail.representation,
                detail.original_triangles,
                detail.selected_triangles,
                round(detail.risk_score, 12),
                detail.simplifier_evaluations,
            )
            for detail in report.region_details
        )
        self.assertEqual(
            decisions,
            (
                ("paint", "original", 2, 2, 0.172264172636, 1),
                ("glass", "original", 1, 1, 0.18, 1),
                ("paint", "normal", 1, 1, 0.18, 0),
            ),
        )
        self.assertEqual(selected["smd"], (("vehicle.smd", "3769c9872899192005f685b00873fb6419d3bd60eecb2beab6c146277fc25941"),))
        self.assertEqual(selected["tree"], "173623295e1fce7ac91d5163343b515ac3f451beb0810ca4e91cf713f3ff95e0")
        self.assertEqual(selected["compiled_tree"], "eae68b2235930e9cd6ac3b45185d6e73229a2432c5e07bc35de6120bb5ad3e28")
        self.assertEqual((report.original_triangles, report.normal_triangles, report.final_triangles), (4, 4, 4))
        self.assertEqual((report.simplifier_evaluations, report.studiomdl_compiles, report.full_family_renders), (2, 1, 0))
        self.assertEqual(report.family_status, "optimized")
        self.assertEqual(
            {key: payload[key] for key in ("schema", "family_status", "original_triangles", "normal_triangles", "final_triangles", "simplifier_evaluations", "studiomdl_compiles", "full_family_renders")},
            {
                "schema": 1,
                "family_status": "optimized",
                "original_triangles": 4,
                "normal_triangles": 4,
                "final_triangles": 4,
                "simplifier_evaluations": 2,
                "studiomdl_compiles": 1,
                "full_family_renders": 0,
            },
        )
        self.assertNotIn("runtime_profile", payload)

    def test_model_baseline_tightens_headroom_but_does_not_schedule_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            options = replace(self._fixture_options(root), validate_candidate=None)
            occurrence = self._install_flat_source(options, bodygroup=False)
            calls = []

            def simplify(region, _ratio, _budget):
                calls.append(region.key.value)
                return replace(region, triangles=region.triangles[:64])

            report = run_maximum_adaptive(
                replace(
                    options,
                    simplify_region=simplify,
                    runtime_evidence=runtime_map(occurrence, model_importance=1.0),
                )
            )

        self.assertEqual(calls, [])
        self.assertEqual(report.simplifier_evaluations, 0)
        self.assertEqual(report.family_status, "optimized")
        detail = report.region_details[0]
        self.assertGreater(detail.target_ratio, 0.08 + 0.57 * detail.risk_score)

    def test_direct_state_schedules_one_bounded_region_and_failure_restores_only_it(self) -> None:
        self.assertEqual(pipeline_module._MIN_RUNTIME_VALIDATION_TRIANGLES, 5000)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            options = replace(self._fixture_options(root), validate_candidate=None)
            occurrence = self._install_flat_source(options, bodygroup=True, grid_size=51)
            calls = []

            def simplify(region, _ratio, _budget):
                calls.append(region.key.value)
                return replace(region, triangles=region.triangles[:64])

            with patch.object(
                pipeline_module,
                "validate_region",
                return_value=ValidationDecision(False, ("silhouette",), 0.0),
            ):
                report = run_maximum_adaptive(
                    replace(
                        options,
                        simplify_region=simplify,
                        runtime_evidence=runtime_map(
                            occurrence,
                            model_importance=1.0,
                            bodygroup=("wheels", 0),
                            bodygroup_importance=0.50,
                        ),
                    )
                )
            payload = json.loads(options.report_path.read_text(encoding="utf-8"))

        self.assertEqual(len(calls), 1)
        self.assertEqual(report.regions.original_fallback, 1)
        self.assertEqual(report.family_status, "optimized")
        self.assertLessEqual(report.simplifier_evaluations, PROFILE.max_simplifier_evaluations)
        self.assertEqual(report.studiomdl_compiles, 1)
        self.assertEqual(payload["runtime_profile"]["matched_regions"], 1)
        runtime_region = payload["runtime_profile"]["regions"][0]
        self.assertGreater(runtime_region["effective_risk"], runtime_region["geometric_risk"])
        self.assertEqual(runtime_region["runtime_importance"], 0.5)
        self.assertTrue(runtime_region["scheduled_validation"])
        self.assertTrue(runtime_region["regional_recovery"])
        self.assertEqual(payload["runtime_profile"]["scheduled_validations"], 1)
        self.assertEqual(payload["runtime_profile"]["regional_recoveries"], 1)

    def test_direct_state_does_not_schedule_small_region(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            options = replace(self._fixture_options(root), validate_candidate=None)
            occurrence = self._install_flat_source(options, bodygroup=True)
            calls = []

            def simplify(region, _ratio, _budget):
                calls.append(region.key.value)
                return replace(region, triangles=region.triangles[:64])

            report = run_maximum_adaptive(
                replace(
                    options,
                    simplify_region=simplify,
                    runtime_evidence=runtime_map(
                        occurrence,
                        model_importance=1.0,
                        bodygroup=("wheels", 0),
                        bodygroup_importance=1.0,
                    ),
                )
            )

        self.assertEqual(calls, [])
        self.assertEqual(report.runtime_profile.scheduled_validations, 0)
        self.assertEqual(report.regions.normal_fallback, 1)

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

    def test_report_exposes_only_virtual_model_validation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            options = self._fixture_options(root)
            report = run_maximum_adaptive(options)
            payload = json.loads(options.report_path.read_text(encoding="utf-8"))
            encoded = options.report_path.read_text(encoding="utf-8")

        self.assertEqual(len(report.model_details), 1)
        model = payload["model_details"][0]
        self.assertEqual(model["model_path"], "models/vehicle.mdl")
        self.assertTrue(model["expected_phy"])
        self.assertTrue(model["changed"])
        self.assertEqual(model["expected_bytes"], 124)
        self.assertEqual(model["skins"], [0])
        self.assertEqual(model["bodygroups"], [])
        self.assertEqual(
            model["materials"],
            ["materials/glass.vmt", "materials/paint.vmt"],
        )
        self.assertGreaterEqual(model["maximum_risk"], 0.0)
        self.assertNotIn(str(root), encoded)

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

    def test_pipeline_releases_source_geometry_before_loading_later_sources(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            options = self._fixture_options(root)
            original_fixture = FIXTURES / "two_components.smd"
            normal_fixture = FIXTURES / "two_components_OPT.smd"
            qc_lines = ['$body "body" "vehicle.smd"']
            for index in range(1, 8):
                source = f"vehicle-{index}.smd"
                shutil.copy2(original_fixture, options.original_source_root / source)
                shutil.copy2(normal_fixture, options.normal_source_root / source)
                qc_lines.append(f'$body "body-{index}" "{source}"')
            qc = "\n".join(qc_lines) + "\n"
            (options.original_source_root / "vehicle.qc").write_text(qc, encoding="utf-8")
            (options.normal_source_root / "vehicle.qc").write_text(qc, encoding="utf-8")

            real_parse = pipeline_module.parse_smd
            triangle_refs: list[weakref.ReferenceType[SmdTriangle]] = []
            peak_live_triangles = 0

            def tracking_parse(text: str):
                nonlocal peak_live_triangles
                gc.collect()
                alive_before = sum(reference() is not None for reference in triangle_refs)
                document = real_parse(text)
                triangle_refs.append(weakref.ref(document.triangles[0]))
                peak_live_triangles = max(peak_live_triangles, alive_before + 1)
                return document

            with patch.object(pipeline_module, "parse_smd", side_effect=tracking_parse):
                report = run_maximum_adaptive(options)

        self.assertEqual(report.family_status, "optimized")
        self.assertLessEqual(
            peak_live_triangles,
            6,
            f"pipeline retained geometry from too many sources (peak={peak_live_triangles})",
        )


if __name__ == "__main__":
    unittest.main()
