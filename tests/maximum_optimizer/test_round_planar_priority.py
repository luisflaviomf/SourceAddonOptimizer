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

    def test_disconnected_round_mesh_fails_closed(self):
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

        self.assertFalse(result.eligible)
        self.assertEqual(result.reason, "disconnected")


if __name__ == "__main__":
    unittest.main()
