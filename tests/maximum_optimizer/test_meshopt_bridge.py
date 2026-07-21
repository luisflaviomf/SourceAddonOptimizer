from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import unittest

from maximum_optimizer.mesh_attributes import classify_position_topology
from maximum_optimizer.meshopt_bridge import (
    SIMPLIFY_LOCK_BORDER,
    SIMPLIFY_PERMISSIVE,
    SIMPLIFY_REGULARIZE_LIGHT,
    MeshInput,
    SimplifyOptions,
    load_library,
    silhouette_boundary_distances_squared,
    simplify_mesh,
    squared_euclidean_distance_field,
)


def make_grid(size: int = 12, *, two_bones: bool = False) -> MeshInput:
    positions = []
    normals = []
    uvs = []
    weights = []
    bones = []
    for y in range(size):
        for x in range(size):
            positions.append((float(x), float(y), 0.0))
            normals.append((0.0, 0.0, 1.0))
            uvs.append((x / (size - 1), y / (size - 1)))
            if two_bones and x >= size // 2:
                weights.append((0.5, 0.5, 0.0, 0.0))
                bones.append((0, 1, 0, 0))
            else:
                weights.append((1.0, 0.0, 0.0, 0.0))
                bones.append((0, 0, 0, 0))

    indices = []
    materials = []
    for y in range(size - 1):
        for x in range(size - 1):
            a = y * size + x
            b = a + 1
            c = a + size
            d = c + 1
            indices.extend((a, b, d, a, d, c))
            material = 0 if x < (size - 1) // 2 else 1
            materials.extend((material, material))
    topology = classify_position_topology(
        positions, normals, uvs, indices, materials, weights, bones
    )
    return MeshInput(
        positions=tuple(positions),
        normals=tuple(normals),
        uvs=tuple(uvs),
        weights=tuple(weights),
        indices=tuple(indices),
        material_ids=tuple(materials),
        vertex_flags=topology.vertex_flags,
        bone_indices=tuple(bones),
    )


def _brute_squared_distance(
    width: int,
    height: int,
    points: tuple[tuple[int, int], ...],
) -> tuple[int, ...]:
    return tuple(
        min((x - point_x) ** 2 + (y - point_y) ** 2 for point_x, point_y in points)
        for y in range(height)
        for x in range(width)
    )


class TopologyTests(unittest.TestCase):
    def test_open_border_and_attribute_discontinuities_are_protected(self) -> None:
        mesh = make_grid()
        topology = classify_position_topology(
            mesh.positions,
            mesh.normals,
            mesh.uvs,
            mesh.indices,
            mesh.material_ids,
            mesh.weights,
            mesh.bone_indices,
        )

        self.assertEqual(topology.open_edge_count, 4 * 11)
        self.assertGreater(topology.locked_vertices, 0)
        self.assertGreater(topology.material_seam_vertices, 0)
        self.assertGreater(topology.protected_vertices, 0)


class MeshoptBridgeTests(unittest.TestCase):
    def test_native_boundary_distances_match_brute_force(self) -> None:
        original = ((0, 0), (5, 1), (2, 4))
        candidate = ((1, 0), (6, 4))
        expected = tuple(
            min((x - target_x) ** 2 + (y - target_y) ** 2 for target_x, target_y in candidate)
            for x, y in original
        ) + tuple(
            min((x - target_x) ** 2 + (y - target_y) ** 2 for target_x, target_y in original)
            for x, y in candidate
        )

        self.assertEqual(
            silhouette_boundary_distances_squared(7, 5, original, candidate),
            expected,
        )
        for left, right in (
            ((), candidate),
            (original, ()),
            (((7, 0),), candidate),
            (original, ((0, -1),)),
        ):
            with self.subTest(left=left, right=right):
                with self.assertRaises(ValueError):
                    silhouette_boundary_distances_squared(7, 5, left, right)

    def test_native_squared_distance_field_matches_brute_force(self) -> None:
        for width, height, points in (
            (7, 5, ((0, 0), (5, 1), (2, 4))),
            (4, 6, ((3, 5),)),
            (9, 3, ((0, 1), (8, 1))),
        ):
            with self.subTest(width=width, height=height, points=points):
                self.assertEqual(
                    squared_euclidean_distance_field(width, height, points),
                    _brute_squared_distance(width, height, points),
                )

        for width, height, points in (
            (0, 5, ((0, 0),)),
            (5, 0, ((0, 0),)),
            (5, 5, ()),
            (5, 5, ((5, 0),)),
            (5, 5, ((0, -1),)),
        ):
            with self.subTest(invalid=(width, height, points)):
                with self.assertRaises(ValueError):
                    squared_euclidean_distance_field(width, height, points)

    def test_reviewed_abi_and_engine_version_load(self) -> None:
        library = load_library(cache=False)

        self.assertEqual(library.maximum_meshopt_abi_version(), 4)
        self.assertEqual(library.maximum_meshopt_version(), 10200)

    def test_default_options_lock_borders_without_permissive_collapses(self) -> None:
        options = SimplifyOptions(target_ratio=0.3, target_error=0.01)

        self.assertTrue(options.meshopt_options & SIMPLIFY_LOCK_BORDER)
        self.assertTrue(options.meshopt_options & SIMPLIFY_REGULARIZE_LIGHT)
        self.assertFalse(options.meshopt_options & SIMPLIFY_PERMISSIVE)

    def test_real_dll_reduces_grid_and_preserves_material_ownership(self) -> None:
        mesh = make_grid()

        result = simplify_mesh(mesh, SimplifyOptions(0.35, 0.05))

        self.assertLess(len(result.indices), len(mesh.indices))
        self.assertEqual(len(result.indices) % 3, 0)
        self.assertEqual(len(result.material_ids), len(result.indices) // 3)
        self.assertEqual(set(result.material_ids), {0, 1})

    def test_non_rigid_skinning_forbids_vertex_update(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-rigid"):
            simplify_mesh(make_grid(two_bones=True), SimplifyOptions(0.5, 0.01, update_vertices=True))

    def test_source_vertex_limit_fails_before_native_call(self) -> None:
        mesh = make_grid(2)
        oversized = replace(
            mesh,
            positions=mesh.positions + ((0.0, 0.0, 0.0),) * 65533,
            normals=mesh.normals + ((0.0, 0.0, 1.0),) * 65533,
            uvs=mesh.uvs + ((0.0, 0.0),) * 65533,
            weights=mesh.weights + ((1.0, 0.0, 0.0, 0.0),) * 65533,
            vertex_flags=mesh.vertex_flags + (0,) * 65533,
            bone_indices=mesh.bone_indices + ((0, 0, 0, 0),) * 65533,
        )

        with self.assertRaisesRegex(ValueError, "Source vertex limit"):
            simplify_mesh(oversized, SimplifyOptions(0.5, 0.01))

    def test_source_triangle_limit_fails_before_native_call(self) -> None:
        mesh = make_grid(2)
        oversized = replace(
            mesh,
            indices=(0, 1, 2) * 65537,
            material_ids=(0,) * 65537,
        )

        with self.assertRaisesRegex(ValueError, "Source triangle limit"):
            simplify_mesh(oversized, SimplifyOptions(0.5, 0.01))

    def test_parallel_calls_are_deterministic(self) -> None:
        mesh = make_grid(8)
        options = SimplifyOptions(0.45, 0.05)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = tuple(pool.map(lambda _index: simplify_mesh(mesh, options), range(32)))

        self.assertTrue(all(result == results[0] for result in results))


if __name__ == "__main__":
    unittest.main()
