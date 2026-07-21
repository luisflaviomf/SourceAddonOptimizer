from __future__ import annotations

from dataclasses import replace
import math
from pathlib import PurePosixPath
import time
import unittest
from unittest import mock

from maximum_optimizer.contracts import RegionBudget
from maximum_optimizer import metrics as metrics_module
from maximum_optimizer.metrics import (
    MetricContract,
    measure_region,
    measure_region_prepared,
    prepare_region_reference,
    validate_region,
)
from maximum_optimizer.qc_graph import QcOccurrence
from maximum_optimizer.regions import SmdRegion, build_region_graph
from maximum_optimizer.smd import SmdDocument, SmdInfluence, SmdTriangle, SmdVertex


OCCURRENCE = QcOccurrence(PurePosixPath("disc.qc"), "$body", 1, PurePosixPath("disc.smd"))
CONTRACT = MetricContract(
    canonical_views=((0.0, 0.0, 1.0),),
    silhouette_resolution=256,
    sample_count=512,
    seed="metrics-test-v1",
    poses=(
        (
            (0, (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)),
            (1, (1.0, 0.0, 0.0, 0.2, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)),
        ),
    ),
)
BUDGET = RegionBudget(
    surface_p95=0.01,
    surface_max=0.025,
    normal_p95_degrees=5.0,
    normal_max_degrees=15.0,
    silhouette_iou_loss=0.01,
    silhouette_boundary_p95_px=1.5,
    uv_p95=0.01,
    material_boundary_p95_px=1.5,
    skinning_p95=0.005,
    skinning_max=0.02,
)


def make_disc(
    segments: int,
    *,
    uv_shift: float = 0.0,
    normal_sign: float = 1.0,
    weights: tuple[float, float] = (0.8, 0.2),
    material: str = "rubber",
) -> SmdRegion:
    influences = (SmdInfluence(0, weights[0]), SmdInfluence(1, weights[1]))
    center = SmdVertex(
        0,
        (0.0, 0.0, 0.0),
        (0.0, 0.0, normal_sign),
        (0.5 + uv_shift, 0.5),
        influences,
    )
    triangles = []
    for segment in range(segments):
        first_angle = 2.0 * math.pi * segment / segments
        second_angle = 2.0 * math.pi * (segment + 1) / segments
        vertices = [center]
        for angle in (first_angle, second_angle):
            x, y = math.cos(angle), math.sin(angle)
            vertices.append(
                SmdVertex(
                    0,
                    (x, y, 0.0),
                    (0.0, 0.0, normal_sign),
                    ((x + 1.0) * 0.5 + uv_shift, (y + 1.0) * 0.5),
                    influences,
                )
            )
        triangles.append(SmdTriangle(material, tuple(vertices), segment))
    graph = build_region_graph(SmdDocument(("version 1",), tuple(triangles)), OCCURRENCE)
    if len(graph.regions) != 1:
        raise AssertionError("disc fixture must produce one region")
    return graph.regions[0]


class RegionMetricsTests(unittest.TestCase):
    def test_identical_region_has_zero_error_and_passes(self) -> None:
        original = make_disc(64)

        metrics = measure_region(original, original, CONTRACT)
        decision = validate_region(metrics, BUDGET)

        self.assertTrue(decision.passed)
        self.assertEqual(metrics.surface_max, 0.0)
        self.assertEqual(metrics.normal_max_degrees, 0.0)
        self.assertEqual(metrics.silhouette_iou_loss, 0.0)
        self.assertEqual(metrics.uv_p95, 0.0)
        self.assertEqual(metrics.skinning_max, 0.0)

    def test_dense_round_candidate_passes_but_hexagon_fails_silhouette(self) -> None:
        original = make_disc(64)

        dense = validate_region(measure_region(original, make_disc(48), CONTRACT), BUDGET)
        hexagon = validate_region(measure_region(original, make_disc(6), CONTRACT), BUDGET)

        self.assertTrue(dense.passed, dense.failed_gates)
        self.assertFalse(hexagon.passed)
        self.assertIn("silhouette", hexagon.failed_gates)

    def test_normal_uv_and_pose_skinning_are_independent_gates(self) -> None:
        original = make_disc(64)

        normal = validate_region(measure_region(original, make_disc(64, normal_sign=-1.0), CONTRACT), BUDGET)
        uv = validate_region(measure_region(original, make_disc(64, uv_shift=0.1), CONTRACT), BUDGET)
        skinning = validate_region(measure_region(original, make_disc(64, weights=(0.2, 0.8)), CONTRACT), BUDGET)

        self.assertIn("normal", normal.failed_gates)
        self.assertNotIn("uv", normal.failed_gates)
        self.assertIn("uv", uv.failed_gates)
        self.assertIn("skinning", skinning.failed_gates)

    def test_material_or_new_bone_contract_fails_closed(self) -> None:
        original = make_disc(16)
        changed_material = make_disc(16, material="glass")
        new_bone = make_disc(16)
        changed_triangles = tuple(
            replace(
                triangle,
                vertices=tuple(
                    replace(vertex, influences=(SmdInfluence(2, 1.0),))
                    for vertex in triangle.vertices
                ),
            )
            for triangle in new_bone.triangles
        )
        new_bone = replace(new_bone, triangles=changed_triangles, bone_ids=(2,))

        with self.assertRaisesRegex(ValueError, "material"):
            measure_region(original, changed_material, CONTRACT)
        with self.assertRaisesRegex(ValueError, "bone"):
            measure_region(original, new_bone, CONTRACT)

    def test_measurement_is_deterministic_and_completes_under_bound(self) -> None:
        original = make_disc(128)
        candidate = make_disc(96)

        started = time.perf_counter()
        first = measure_region(original, candidate, CONTRACT)
        elapsed = time.perf_counter() - started
        second = measure_region(original, candidate, CONTRACT)

        self.assertEqual(first, second)
        self.assertLess(elapsed, 5.0)

    def test_prepared_reference_is_exact_and_reused_between_candidates(self) -> None:
        original = make_disc(64)
        dense = make_disc(48)
        lighter = make_disc(56)

        expected = measure_region(original, dense, CONTRACT)
        with mock.patch(
            "maximum_optimizer.metrics._triangle_data",
            wraps=metrics_module._triangle_data,
        ) as triangle_data:
            reference = prepare_region_reference(original, CONTRACT)
            actual = measure_region_prepared(reference, dense)
            measure_region_prepared(reference, lighter)

        self.assertEqual(actual, expected)
        self.assertEqual(triangle_data.call_count, 3)


if __name__ == "__main__":
    unittest.main()
