from __future__ import annotations

import math
from pathlib import Path, PurePosixPath
import tempfile
import unittest

from maximum_optimizer.materials import MaterialSemantics, resolve_material_semantics
from maximum_optimizer.profile import load_profile
from maximum_optimizer.qc_graph import QcOccurrence
from maximum_optimizer.regions import SmdRegion, build_region_graph
from maximum_optimizer.risk import CANONICAL_VIEWS, budget_for_risk, measure_risk
from maximum_optimizer.smd import SmdDocument, SmdInfluence, SmdTriangle, SmdVertex


PROFILE = load_profile(
    Path(__file__).resolve().parents[2]
    / "maximum_optimizer"
    / "profiles"
    / "maximum-adaptive-v2.json"
)
OCCURRENCE = QcOccurrence(
    PurePosixPath("risk.qc"), "$body", 1, PurePosixPath("risk.smd")
)


def vertex(position, normal, uv=(0.0, 0.0), influences=None) -> SmdVertex:
    return SmdVertex(
        primary_bone=0,
        position=position,
        normal=normal,
        uv=uv,
        influences=tuple(influences or (SmdInfluence(0, 1.0),)),
    )


def region_from_triangles(triangles: list[SmdTriangle]) -> SmdRegion:
    document = SmdDocument(("version 1",), tuple(triangles))
    graph = build_region_graph(document, OCCURRENCE)
    if len(graph.regions) != 1:
        raise AssertionError(f"fixture produced {len(graph.regions)} regions")
    return graph.regions[0]


def plane(size: int = 8) -> SmdRegion:
    triangles = []
    ordinal = 0
    for y in range(size - 1):
        for x in range(size - 1):
            points = (
                (float(x), float(y), 0.0),
                (float(x + 1), float(y), 0.0),
                (float(x + 1), float(y + 1), 0.0),
                (float(x), float(y + 1), 0.0),
            )
            for indices in ((0, 1, 2), (0, 2, 3)):
                triangles.append(
                    SmdTriangle(
                        "paint",
                        tuple(vertex(points[index], (0.0, 0.0, 1.0)) for index in indices),
                        ordinal,
                    )
                )
                ordinal += 1
    return region_from_triangles(triangles)


def cylinder(segments: int = 32) -> SmdRegion:
    triangles = []
    ordinal = 0
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        a0 = 2.0 * math.pi * segment / segments
        a1 = 2.0 * math.pi * next_segment / segments
        points = (
            (math.cos(a0), math.sin(a0), -1.0),
            (math.cos(a1), math.sin(a1), -1.0),
            (math.cos(a1), math.sin(a1), 1.0),
            (math.cos(a0), math.sin(a0), 1.0),
        )
        normals = (
            (math.cos(a0), math.sin(a0), 0.0),
            (math.cos(a1), math.sin(a1), 0.0),
            (math.cos(a1), math.sin(a1), 0.0),
            (math.cos(a0), math.sin(a0), 0.0),
        )
        for indices in ((0, 1, 2), (0, 2, 3)):
            triangles.append(
                SmdTriangle(
                    "rubber",
                    tuple(vertex(points[index], normals[index]) for index in indices),
                    ordinal,
                )
            )
            ordinal += 1
    return region_from_triangles(triangles)


def two_bone_strip() -> SmdRegion:
    left = (SmdInfluence(0, 1.0),)
    middle = (SmdInfluence(0, 0.5), SmdInfluence(1, 0.5))
    right = (SmdInfluence(1, 1.0),)
    points = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0), (2.0, 1.0, 0.0))
    skins = (left, middle, right, left, middle, right)
    triangles = []
    for ordinal, indices in enumerate(((0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4))):
        triangles.append(
            SmdTriangle(
                "cloth",
                tuple(vertex(points[index], (0.0, 0.0, 1.0), influences=skins[index]) for index in indices),
                ordinal,
            )
        )
    return region_from_triangles(triangles)


class MaterialTests(unittest.TestCase):
    def test_addon_material_semantics_detect_transparency_and_two_sided(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            addon = Path(raw) / "addon"
            vmt = addon / "materials" / "cars" / "glass.vmt"
            vmt.parent.mkdir(parents=True)
            vmt.write_text('"VertexLitGeneric" { "$translucent" "1" "$nocull" "1" }', encoding="utf-8")

            semantics = resolve_material_semantics("cars/glass", addon, None)

        self.assertTrue(semantics.translucent)
        self.assertTrue(semantics.two_sided)
        self.assertTrue(semantics.requires_render)
        self.assertEqual(semantics.resolver, "addon")

    def test_framework_is_optional_read_only_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            addon = root / "addon"
            framework = root / "framework"
            addon.mkdir()
            vmt = framework / "materials" / "shared" / "lens.vmt"
            vmt.parent.mkdir(parents=True)
            vmt.write_text('"Refract" { "$refracttexture" "_rt_WaterRefraction" }', encoding="utf-8")

            resolved = resolve_material_semantics("shared/lens", addon, framework)
            unresolved = resolve_material_semantics("shared/lens", addon, None)

        self.assertTrue(resolved.refractive)
        self.assertEqual(resolved.resolver, "framework")
        self.assertEqual(unresolved.resolver, "missing")
        self.assertLess(unresolved.confidence, resolved.confidence)


class RiskTests(unittest.TestCase):
    def test_uv_and_hard_normal_discontinuities_are_measured_at_either_edge_endpoint(self) -> None:
        high_a = vertex((0.1, 0.1, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0))
        high_b = vertex((0.1, 0.1, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0))
        a = vertex((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        b = vertex((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        c = vertex((0.0, 1.0, 0.0), (0.0, 1.0, 0.0))
        mesh = region_from_triangles(
            [
                SmdTriangle("paint", (high_a, a, b), 0),
                SmdTriangle("paint", (high_b, b, c), 1),
            ]
        )

        features = measure_risk(mesh, MaterialSemantics.opaque(), CANONICAL_VIEWS)

        self.assertGreater(features.hard_boundary_density, 0.0)
        self.assertGreater(features.uv_seam_density, 0.0)

    def test_curved_silhouette_is_riskier_and_gets_higher_target_ratio(self) -> None:
        opaque = MaterialSemantics.opaque()

        flat = measure_risk(plane(), opaque, CANONICAL_VIEWS)
        curved = measure_risk(cylinder(), opaque, CANONICAL_VIEWS)

        self.assertGreater(curved.curvature_p95_norm, flat.curvature_p95_norm)
        self.assertGreater(curved.score, flat.score)
        self.assertGreater(curved.target_ratio, flat.target_ratio)

    def test_transparency_and_skin_gradient_tighten_budgets(self) -> None:
        rigid = measure_risk(plane(), MaterialSemantics.opaque(), CANONICAL_VIEWS)
        skinned = measure_risk(
            two_bone_strip(),
            MaterialSemantics(translucent=True, resolver="addon", confidence=1.0),
            CANONICAL_VIEWS,
        )

        rigid_budget = budget_for_risk(PROFILE, rigid)
        skinned_budget = budget_for_risk(PROFILE, skinned)

        self.assertGreater(skinned.skinning_risk, rigid.skinning_risk)
        self.assertLess(skinned_budget.silhouette_iou_loss, rigid_budget.silhouette_iou_loss)
        self.assertLess(skinned_budget.skinning_p95, rigid_budget.skinning_p95)

    def test_classifier_is_deterministic(self) -> None:
        mesh = cylinder()
        semantics = MaterialSemantics.opaque()

        self.assertEqual(
            measure_risk(mesh, semantics, CANONICAL_VIEWS),
            measure_risk(mesh, semantics, CANONICAL_VIEWS),
        )


if __name__ == "__main__":
    unittest.main()
