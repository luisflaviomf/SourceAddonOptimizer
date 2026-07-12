import math
import unittest


def _closed_cylinder(sides=16, *, offset=(0.0, 0.0, 0.0)):
    positions = []
    for z in (-0.2, 0.2):
        positions.extend(
            (
                offset[0] + math.cos(2.0 * math.pi * index / sides),
                offset[1] + math.sin(2.0 * math.pi * index / sides),
                offset[2] + z,
            )
            for index in range(sides)
        )
    positions.extend((offset[0], offset[1], offset[2] + z) for z in (-0.2, 0.2))
    bottom_center, top_center = sides * 2, sides * 2 + 1
    triangles = []
    for index in range(sides):
        following = (index + 1) % sides
        bottom, bottom_next = index, following
        top, top_next = sides + index, sides + following
        triangles.extend(((bottom, bottom_next, top_next), (bottom, top_next, top)))
        triangles.append((bottom_center, bottom_next, bottom))
        triangles.append((top_center, top, top_next))
    return tuple(positions), tuple(triangles)


def _as_split_wedges(positions, triangles, *, displacement=5e-7):
    split_positions = []
    split_triangles = []
    for face_index, triangle in enumerate(triangles):
        split_triangle = []
        for corner_index, vertex_index in enumerate(triangle):
            direction = -1.0 if (face_index + corner_index) % 2 else 1.0
            row = positions[vertex_index]
            split_triangle.append(len(split_positions))
            split_positions.append((row[0] + direction * displacement, row[1], row[2]))
        split_triangles.append(tuple(split_triangle))
    return tuple(split_positions), tuple(split_triangles)


class RoundPlanarPriorityTests(unittest.TestCase):
    def test_closed_rigid_round_component_reports_axis_and_priority_rings(self):
        from maximum_optimizer.round_planar_priority import classify_round_component

        positions, triangles = _closed_cylinder()
        influences = ((('wheel', 1.0),),) * len(positions)

        result = classify_round_component(positions, triangles, influences)

        self.assertTrue(result.eligible)
        self.assertEqual(result.reason, "eligible")
        self.assertEqual(result.axis, 2)
        self.assertGreaterEqual(len(result.priority_vertices), 16)
        self.assertNotIn(len(positions) - 1, result.priority_vertices)

    def test_nonrigid_component_fails_closed(self):
        from maximum_optimizer.round_planar_priority import classify_round_component

        positions, triangles = _closed_cylinder()
        influences = [(('wheel', 1.0),) for _ in positions]
        influences[0] = (('wheel', 0.5), ('suspension', 0.5))

        result = classify_round_component(positions, triangles, tuple(influences))

        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "not-rigid")
        self.assertEqual(result.priority_vertices, ())

    def test_open_component_fails_closed(self):
        from maximum_optimizer.round_planar_priority import classify_round_component

        positions, triangles = _closed_cylinder()
        influences = ((('wheel', 1.0),),) * len(positions)

        result = classify_round_component(positions, triangles[:-1], influences)

        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "not-closed-manifold")

    def test_box_fails_angular_coverage(self):
        from maximum_optimizer.round_planar_priority import classify_round_component

        positions = (
            (-1, -1, -0.2), (1, -1, -0.2), (1, 1, -0.2), (-1, 1, -0.2),
            (-1, -1, 0.2), (1, -1, 0.2), (1, 1, 0.2), (-1, 1, 0.2),
        )
        triangles = (
            (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        )
        influences = ((('wheel', 1.0),),) * len(positions)

        result = classify_round_component(positions, triangles, influences)

        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "insufficient-angular-coverage")

    def test_split_wedges_are_reconnected_with_audited_position_tolerance(self):
        from maximum_optimizer.round_planar_priority import classify_round_component
        from maximum_optimizer.smd_contract import POSITION_SERIALIZATION_TOLERANCE

        positions, triangles = _closed_cylinder()
        positions, triangles = _as_split_wedges(positions, triangles)
        influences = ((('wheel', 1.0),),) * len(positions)

        result = classify_round_component(positions, triangles, influences)

        self.assertTrue(result.eligible)
        self.assertEqual(result.position_tolerance, POSITION_SERIALIZATION_TOLERANCE)
        self.assertEqual(len(result.components), 1)
        self.assertLess(result.components[0].canonical_vertices, len(positions))
        self.assertEqual(result.components[0].source_vertices, len(positions))

    def test_split_wedges_beyond_position_tolerance_are_not_joined(self):
        from maximum_optimizer.round_planar_priority import classify_round_component

        positions, triangles = _closed_cylinder()
        positions, triangles = _as_split_wedges(positions, triangles, displacement=3e-6)
        influences = ((('wheel', 1.0),),) * len(positions)

        result = classify_round_component(positions, triangles, influences)

        self.assertFalse(result.eligible)

    def test_disconnected_round_components_are_admitted_as_a_union(self):
        from maximum_optimizer.round_planar_priority import classify_round_component

        first_positions, first_triangles = _closed_cylinder()
        second_positions, second_triangles = _closed_cylinder(offset=(3.0, 0.0, 0.0))
        offset = len(first_positions)
        positions = first_positions + second_positions
        triangles = first_triangles + tuple(
            tuple(index + offset for index in triangle) for triangle in second_triangles
        )
        influences = ((('wheel', 1.0),),) * len(positions)

        result = classify_round_component(positions, triangles, influences)

        self.assertTrue(result.eligible)
        self.assertEqual(result.reason, "eligible")
        self.assertEqual(len(result.components), 2)
        self.assertEqual(len(result.priority_vertices), 32 * 2)

    def test_disconnected_round_components_may_be_rigid_to_different_bones(self):
        from maximum_optimizer.round_planar_priority import classify_round_component

        first_positions, first_triangles = _closed_cylinder()
        second_positions, second_triangles = _closed_cylinder(offset=(3.0, 0.0, 0.0))
        offset = len(first_positions)
        positions = first_positions + second_positions
        triangles = first_triangles + tuple(
            tuple(index + offset for index in triangle) for triangle in second_triangles
        )
        influences = (
            ((('front-wheel', 1.0),),) * len(first_positions)
            + ((('rear-wheel', 1.0),),) * len(second_positions)
        )

        result = classify_round_component(positions, triangles, influences)

        self.assertTrue(result.eligible)
        self.assertEqual(len(result.components), 2)

    def test_significant_nonround_component_fails_closed(self):
        from maximum_optimizer.round_planar_priority import classify_round_component

        round_positions, round_triangles = _closed_cylinder()
        box_positions = (
            (-1, -1, 2.0), (1, -1, 2.0), (1, 1, 2.0), (-1, 1, 2.0),
            (-1, -1, 2.4), (1, -1, 2.4), (1, 1, 2.4), (-1, 1, 2.4),
        )
        box_triangles = (
            (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        )
        offset = len(round_positions)
        positions = round_positions + box_positions
        triangles = round_triangles + tuple(
            tuple(index + offset for index in triangle) for triangle in box_triangles
        )
        influences = ((('wheel', 1.0),),) * len(positions)

        result = classify_round_component(positions, triangles, influences)

        self.assertFalse(result.eligible)
        self.assertIn("significant", result.reason)
        self.assertEqual(len(result.components), 2)

    def test_insignificant_nonround_component_is_fully_protected(self):
        from maximum_optimizer.round_planar_priority import classify_round_component

        round_positions, round_triangles = _closed_cylinder()
        tiny_positions = (
            (0.0, 0.0, 3.0), (0.01, 0.0, 3.0),
            (0.0, 0.01, 3.0), (0.0, 0.0, 3.01),
        )
        tiny_triangles = ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3))
        offset = len(round_positions)
        positions = round_positions + tiny_positions
        triangles = round_triangles + tuple(
            tuple(index + offset for index in triangle) for triangle in tiny_triangles
        )
        influences = ((('wheel', 1.0),),) * len(positions)

        result = classify_round_component(positions, triangles, influences)

        self.assertTrue(result.eligible)
        tiny_audit = result.components[1]
        self.assertFalse(tiny_audit.eligible)
        self.assertFalse(tiny_audit.significant)
        self.assertEqual(tiny_audit.priority_vertices, len(tiny_positions))
        self.assertTrue(set(range(offset, len(positions))).issubset(result.priority_vertices))


if __name__ == "__main__":
    unittest.main()
