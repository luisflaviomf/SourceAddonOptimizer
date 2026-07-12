from __future__ import annotations

import ctypes
import math
import os
import struct
from pathlib import Path
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from maximum_optimizer.meshopt_bridge import (
    LOCK,
    PRIORITY,
    PROTECT,
    MeshInput,
    SimplifyOptions,
    load_library,
    simplify_mesh,
    MESHOPT_ENGINE_PREFERRED,
    _MaximumMeshInput,
    _MaximumMeshOptions,
    _MaximumMeshOutput,
    _canonicalize_skin_slots,
    _validate,
    compact_direct_result,
)
import maximum_optimizer.mesh_attributes as mesh_attributes
from maximum_optimizer.mesh_attributes import build_wedge_mesh


def _grid(size: int = 10) -> MeshInput:
    positions = []
    normals = []
    uvs = []
    weights = []
    flags = []
    for y in range(size):
        for x in range(size):
            positions.append((float(x), float(y), 0.0))
            normals.append((0.0, 0.0, 1.0))
            uvs.append((x / (size - 1), y / (size - 1)))
            weights.append((1.0, 0.0, 0.0, 0.0))
            border = x in (0, size - 1) or y in (0, size - 1)
            seam = x in (size // 2 - 1, size // 2)
            flags.append((LOCK if border else 0) | (PROTECT if seam else 0))

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
    return MeshInput(
        positions=tuple(positions),
        normals=tuple(normals),
        uvs=tuple(uvs),
        weights=tuple(weights),
        indices=tuple(indices),
        material_ids=tuple(materials),
        vertex_flags=tuple(flags),
        bone_indices=tuple((0 if x < size // 2 else 1, 0, 0, 0) for y in range(size) for x in range(size)),
    )


class MeshoptBridgeTests(unittest.TestCase):
    def test_exact_float32_wedge_mode_does_not_merge_near_zero_positions(self) -> None:
        self.assertIn("exact_float32", __import__("inspect").signature(build_wedge_mesh).parameters)
        mesh = build_wedge_mesh(
            ((1e-10, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
             (2e-10, 0.0, 0.0)),
            ((0, 1, 2), (3, 2, 1)), ((0.0, 0.0, 1.0),) * 6,
            ((0.0, 0.0),) * 6, (0, 0), ((('root', 1.0),),) * 4,
            exact_float32=True,
        )
        self.assertEqual(len(mesh.positions), 4)

    def test_position_topology_connects_uv_normal_and_material_wedges(self) -> None:
        self.assertIn("position_remap", SimplifyOptions.__dataclass_fields__)
        self.assertTrue(hasattr(mesh_attributes, "classify_position_topology"))
        positions = (
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0),
            (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0),
            (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0),
        )
        normals = tuple((0.0, 0.0, 1.0) if i < 3 else (1.0, 0.0, 0.0) for i in range(12))
        uvs = tuple((float(i % 3), float((i + 1) % 3)) for i in range(12))
        result = mesh_attributes.classify_position_topology(
            positions, normals, uvs, tuple(range(12)), (0, 1, 0, 1),
            ((1.0, 0.0, 0.0, 0.0),) * 12, ((0, 0, 0, 0),) * 12,
        )
        self.assertEqual(result.canonical_position_count, 4)
        self.assertEqual(result.open_edge_count, 0)
        self.assertEqual(result.nonmanifold_edge_count, 0)
        self.assertEqual(result.locked_vertices, 0)
        self.assertGreater(result.uv_seam_vertices, 0)
        self.assertGreater(result.normal_seam_vertices, 0)
        self.assertGreater(result.material_seam_vertices, 0)

    def test_position_topology_treats_signed_zero_as_one_meshopt_position(self) -> None:
        result = mesh_attributes.classify_position_topology(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
             (-0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0),
             (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0),
             (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)),
            ((0.0, 0.0, 1.0),) * 12, ((0.0, 0.0),) * 12,
            tuple(range(12)), (0, 0, 0, 0), ((1.0, -0.0, 0.0, 0.0),) * 12,
            ((0, 0, 0, 0),) * 12,
        )
        self.assertEqual(result.canonical_position_count, 4)
        self.assertEqual(result.open_edge_count, 0)

    def test_skin_same_bones_different_float32_weights_protects_shared_position(self) -> None:
        positions = ((0.0, 0.0, 0.0),) * 2 + ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                    (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
        result = mesh_attributes.classify_position_topology(
            positions, ((0.0, 0.0, 1.0),) * 6, ((0.0, 0.0),) * 6,
            (0, 2, 3, 1, 4, 5), (0, 0),
            ((0.75, 0.25, 0.0, 0.0), (0.5, 0.5, 0.0, 0.0)) + ((1.0, 0.0, 0.0, 0.0),) * 4,
            ((0, 1, 0, 0),) * 2 + ((0, 0, 0, 0),) * 4,
        )
        self.assertGreater(result.skin_transition_vertices, 0)
        self.assertTrue(result.vertex_flags[0] & PROTECT)
        self.assertTrue(result.vertex_flags[1] & PROTECT)

    def test_position_topology_locks_real_open_edges_and_nonmanifold_fail_closed(self) -> None:
        self.assertTrue(hasattr(mesh_attributes, "classify_position_topology"))
        common = dict(
            normals=((0.0, 0.0, 1.0),) * 5,
            uvs=((0.0, 0.0),) * 5,
            material_ids=(0, 0, 0),
            weights=((1.0, 0.0, 0.0, 0.0),) * 5,
            bone_indices=((0, 0, 0, 0),) * 5,
        )
        nonmanifold = mesh_attributes.classify_position_topology(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
             (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
            indices=(0, 1, 2, 1, 0, 3, 0, 1, 4), **common,
        )
        self.assertEqual(nonmanifold.nonmanifold_edge_count, 1)
        self.assertTrue(nonmanifold.vertex_flags[0] & LOCK)
        self.assertTrue(nonmanifold.vertex_flags[1] & LOCK)
        triangle = mesh_attributes.classify_position_topology(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0.0, 0.0, 1.0),) * 3, ((0.0, 0.0),) * 3, (0, 1, 2), (0,),
            ((1.0, 0.0, 0.0, 0.0),) * 3, ((0, 0, 0, 0),) * 3,
        )
        self.assertEqual(triangle.open_edge_count, 3)
        self.assertEqual(triangle.locked_vertices, 3)

    def test_skin_regions_protect_only_transition_boundary_without_locking_region(self) -> None:
        self.assertTrue(hasattr(mesh_attributes, "classify_position_topology"))
        positions = (
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0),
            (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0),
            (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0),
        )
        result = mesh_attributes.classify_position_topology(
            positions, ((0.0, 0.0, 1.0),) * 12, ((0.0, 0.0),) * 12,
            tuple(range(12)), (0, 0, 0, 0), ((1.0, 0.0, 0.0, 0.0),) * 12,
            ((0, 0, 0, 0),) * 6 + ((1, 0, 0, 0),) * 6,
        )
        self.assertGreater(result.skin_transition_vertices, 0)
        self.assertEqual(result.locked_vertices, 0)
        self.assertTrue(any(flag & PROTECT for flag in result.vertex_flags))

    def test_direct_result_compacts_only_referenced_source_tuples_exactly(self) -> None:
        source = _grid(4)
        result = simplify_mesh(source, SimplifyOptions(0.7, 1.0, update_vertices=False))
        compact = compact_direct_result(source, result)

        self.assertLessEqual(len(compact.positions), len(source.positions))
        self.assertEqual(compact.material_ids, result.material_ids)
        self.assertEqual(set(compact.indices), set(range(len(compact.positions))))
        source_float32 = {
            (
                tuple(struct.pack("=f", value) for value in position),
                tuple(struct.pack("=f", value) for value in normal),
                tuple(struct.pack("=f", value) for value in uv),
                tuple(struct.pack("=f", value) for value in weights),
                bones,
            )
            for position, normal, uv, weights, bones in zip(
                source.positions, source.normals, source.uvs, source.weights, source.bone_indices
            )
        }
        for retained in zip(
            compact.positions, compact.normals, compact.uvs, compact.weights, compact.bone_indices
        ):
            signature = tuple(
                tuple(struct.pack("=f", value) for value in row) if index < 4 else row
                for index, row in enumerate(retained)
            )
            self.assertIn(signature, source_float32)

    def test_direct_compaction_rejects_non_direct_or_mutated_attributes(self) -> None:
        source = _grid(4)
        direct = simplify_mesh(source, SimplifyOptions(0.7, 1.0, update_vertices=False))
        changed = list(direct.positions)
        changed[direct.indices[0]] = (999.0, 0.0, 0.0)
        hostile = type(direct)(**{**direct.__dict__, "positions": tuple(changed)})
        with self.assertRaisesRegex(RuntimeError, "direct attribute"):
            compact_direct_result(source, hostile)
        changed_materials = (99,) * len(direct.material_ids)
        hostile_material = type(direct)(**{**direct.__dict__, "material_ids": changed_materials})
        with self.assertRaisesRegex(RuntimeError, "material ownership"):
            compact_direct_result(source, hostile_material)

    def test_no_update_native_output_preserves_raw_normal_weight_and_bone_slots(self) -> None:
        source = _grid(4)
        normals = list(source.normals)
        weights = list(source.weights)
        bones = list(source.bone_indices)
        normals[5] = (0.0, 0.0, 2.0)
        weights[5] = (0.2, 0.8, 0.0, 0.0)
        bones[5] = (7, 2, 0, 0)
        source = MeshInput(**{
            **source.__dict__, "normals": tuple(normals), "weights": tuple(weights),
            "bone_indices": tuple(bones),
        })

        result = simplify_mesh(source, SimplifyOptions(0.95, 1.0, update_vertices=False))
        compact = compact_direct_result(source, result)

        retained = compact.source_vertex_indices.index(5)
        self.assertEqual(compact.normals[retained], (0.0, 0.0, 2.0))
        self.assertEqual(
            tuple(struct.pack("=f", value) for value in compact.weights[retained]),
            tuple(struct.pack("=f", value) for value in (0.2, 0.8, 0.0, 0.0)),
        )
        self.assertEqual(compact.bone_indices[retained], (7, 2, 0, 0))

    def test_checkpoint_keeps_meshoptimizer_non_preferred(self) -> None:
        self.assertIs(MESHOPT_ENGINE_PREFERRED, False)
    def test_real_dll_simplifies_grid_and_preserves_locked_border_and_materials(self) -> None:
        source = _grid()
        result = simplify_mesh(
            source,
            SimplifyOptions(target_ratio=0.35, target_error=1.0, update_vertices=False),
        )

        self.assertEqual(result.engine_version, 10200)
        self.assertLess(len(result.indices), len(source.indices))
        self.assertEqual(len(result.indices) % 3, 0)
        self.assertTrue(all(0 <= index < len(result.positions) for index in result.indices))
        self.assertEqual(set(result.material_ids), {0, 1})
        self.assertEqual(len(result.material_ids), len(result.indices) // 3)
        self.assertEqual(len(result.bone_indices), len(result.positions))

        used = set(result.indices)
        border_positions = {
            source.positions[index]
            for index, flag in enumerate(source.vertex_flags)
            if flag & LOCK
        }
        result_positions = {result.positions[index] for index in used}
        self.assertTrue(border_positions.issubset(result_positions))

    def test_position_remap_global_material_split_preserves_exact_ownership(self) -> None:
        source = _grid(12)
        remap = {}
        rows = {name: [] for name in ("positions", "normals", "uvs", "weights", "bone_indices")}
        indices = []
        for triangle, material in enumerate(source.material_ids):
            for old in source.indices[triangle * 3 : triangle * 3 + 3]:
                key = (old, material)
                if key not in remap:
                    remap[key] = len(rows["positions"])
                    for name in rows:
                        rows[name].append(getattr(source, name)[old])
                indices.append(remap[key])
        split = MeshInput(
            tuple(rows["positions"]), tuple(rows["normals"]), tuple(rows["uvs"]),
            tuple(rows["weights"]), tuple(indices), source.material_ids,
            (0,) * len(rows["positions"]), tuple(rows["bone_indices"]),
        )
        topology = mesh_attributes.classify_position_topology(
            split.positions, split.normals, split.uvs, split.indices, split.material_ids,
            split.weights, split.bone_indices,
        )
        split = MeshInput(**{**split.__dict__, "vertex_flags": topology.vertex_flags})
        result = simplify_mesh(split, SimplifyOptions(0.25, 1.0, position_remap=True))
        compact_direct_result(split, result)
        self.assertLessEqual(len(result.indices) / len(split.indices), 0.27)
        self.assertEqual(set(result.material_ids), {0, 1})
        locked_positions = {
            topology.canonical_position_ids[index]
            for index, flag in enumerate(topology.vertex_flags) if flag & LOCK
        }
        self.assertEqual(len(locked_positions), (12 - 1) * 4)

    def test_position_remap_global_ambiguous_material_ownership_fails_closed(self) -> None:
        source = _grid(12)
        topology = mesh_attributes.classify_position_topology(
            source.positions, source.normals, source.uvs, source.indices, source.material_ids,
            source.weights, source.bone_indices,
        )
        source = MeshInput(**{**source.__dict__, "vertex_flags": topology.vertex_flags})
        with self.assertRaisesRegex(RuntimeError, "native error -7"):
            simplify_mesh(source, SimplifyOptions(0.25, 1.0, position_remap=True))

    def test_update_path_returns_finite_normalized_attributes(self) -> None:
        source = _grid(8)
        malformed_weights = list(source.weights)
        malformed_weights[9] = (0.8, 0.4, -0.2, 0.0)
        source = MeshInput(**{**source.__dict__, "weights": tuple(malformed_weights)})

        result = simplify_mesh(
            source,
            SimplifyOptions(target_ratio=0.55, target_error=1.0, update_vertices=True),
        )

        self.assertLess(len(result.indices), len(source.indices))
        for normal in result.normals:
            self.assertTrue(all(math.isfinite(value) for value in normal))
            self.assertAlmostEqual(math.sqrt(sum(value * value for value in normal)), 1.0, places=5)
        for uv in result.uvs:
            self.assertTrue(all(math.isfinite(value) for value in uv))
        for weights in result.weights:
            self.assertTrue(all(0.0 <= value <= 1.0 for value in weights))
            self.assertAlmostEqual(sum(weights), 1.0, places=5)

    def test_repeated_calls_and_destroy_are_safe(self) -> None:
        source = _grid(6)
        options = SimplifyOptions(target_ratio=0.6, target_error=1.0)
        baseline = simplify_mesh(source, options)
        for _ in range(40):
            current = simplify_mesh(source, options)
            self.assertEqual(current, baseline)

        dll = load_library()
        self.assertEqual(dll.maximum_meshopt_simplify(None, None, None), -1)
        dll.maximum_meshopt_destroy(None)
        dll.maximum_meshopt_destroy(None)

        wrong_layout = _MaximumMeshOutput(struct_size=0, vertex_count=91, index_count=92, triangle_count=93)
        dll.maximum_meshopt_destroy(ctypes.byref(wrong_layout))
        self.assertEqual((wrong_layout.vertex_count, wrong_layout.index_count, wrong_layout.triangle_count), (91, 92, 93))

        forged = _MaximumMeshOutput(
            struct_size=ctypes.sizeof(_MaximumMeshOutput), vertex_count=71, index_count=72, triangle_count=73
        )
        forged.positions = ctypes.cast(ctypes.c_void_p(1), ctypes.POINTER(ctypes.c_float))
        forged.ownership_cookie = 0xDEADBEEF
        dll.maximum_meshopt_destroy(ctypes.byref(forged))
        self.assertEqual((forged.vertex_count, forged.index_count, forged.triangle_count), (71, 72, 73))
        self.assertEqual(ctypes.cast(forged.positions, ctypes.c_void_p).value, 1)

    def test_parallel_native_calls_are_deterministic_and_registry_safe(self) -> None:
        source = _grid(7)
        options = SimplifyOptions(0.5, 1.0, update_vertices=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = tuple(pool.map(lambda _item: simplify_mesh(source, options), range(64)))
        self.assertTrue(all(result == results[0] for result in results))

    def test_same_output_registry_linearizes_build_destroy_and_reuse(self) -> None:
        dll = load_library()
        for name, argtypes, restype in (
            ("maximum_meshopt_test_pause_at", [ctypes.c_uint32], ctypes.c_int),
            ("maximum_meshopt_test_wait_paused", [ctypes.c_uint32, ctypes.c_uint32], ctypes.c_int),
            ("maximum_meshopt_test_release", [ctypes.c_uint32], ctypes.c_int),
            ("maximum_meshopt_test_registry_count", [], ctypes.c_size_t),
        ):
            function = getattr(dll, name)
            function.argtypes = argtypes
            function.restype = restype

        source = _grid(5)
        positions = (ctypes.c_float * (len(source.positions) * 3))(*(
            value for row in source.positions for value in row
        ))
        normals = (ctypes.c_float * (len(source.normals) * 3))(*(
            value for row in source.normals for value in row
        ))
        uvs = (ctypes.c_float * (len(source.uvs) * 2))(*(
            value for row in source.uvs for value in row
        ))
        weights = (ctypes.c_float * (len(source.weights) * 4))(*(
            value for row in source.weights for value in row
        ))
        bones = (ctypes.c_uint32 * (len(source.bone_indices) * 4))(*(
            value for row in source.bone_indices for value in row
        ))
        indices = (ctypes.c_uint32 * len(source.indices))(*source.indices)
        materials = (ctypes.c_uint32 * len(source.material_ids))(*source.material_ids)
        flags = (ctypes.c_ubyte * len(source.vertex_flags))(*source.vertex_flags)
        native_input = _MaximumMeshInput(
            ctypes.sizeof(_MaximumMeshInput), positions, normals, uvs, weights, bones, 2,
            len(source.positions), indices, len(source.indices), materials,
            len(source.material_ids), flags,
        )
        options = _MaximumMeshOptions(ctypes.sizeof(_MaximumMeshOptions), 0.55, 1.0, 0, 1)
        output = _MaximumMeshOutput(struct_size=ctypes.sizeof(_MaximumMeshOutput))

        self.assertEqual(dll.maximum_meshopt_test_pause_at(1), 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            building = pool.submit(
                dll.maximum_meshopt_simplify,
                ctypes.byref(native_input), ctypes.byref(options), ctypes.byref(output),
            )
            self.assertEqual(dll.maximum_meshopt_test_wait_paused(1, 5000), 1)
            self.assertEqual(dll.maximum_meshopt_test_registry_count(), 1)
            dll.maximum_meshopt_destroy(ctypes.byref(output))
            self.assertFalse(output.positions)
            self.assertEqual(output.ownership_cookie, 0)
            self.assertEqual(dll.maximum_meshopt_test_release(1), 1)
            self.assertEqual(building.result(timeout=5), 0)

            self.assertTrue(output.positions)
            self.assertEqual(dll.maximum_meshopt_test_pause_at(2), 1)
            destroying = pool.submit(dll.maximum_meshopt_destroy, ctypes.byref(output))
            self.assertEqual(dll.maximum_meshopt_test_wait_paused(2, 5000), 1)
            self.assertFalse(output.positions)
            self.assertEqual(
                dll.maximum_meshopt_simplify(
                    ctypes.byref(native_input), ctypes.byref(options), ctypes.byref(output)
                ),
                -9,
            )
            self.assertEqual(dll.maximum_meshopt_test_release(2), 1)
            destroying.result(timeout=5)

        dll.maximum_meshopt_destroy(ctypes.byref(output))
        self.assertEqual(dll.maximum_meshopt_test_registry_count(), 0)

    def test_registry_emplace_failures_never_cross_abi_or_leave_state(self) -> None:
        dll = load_library()
        fail_next = dll.maximum_meshopt_test_fail_next
        fail_next.argtypes = [ctypes.c_uint32]
        fail_next.restype = ctypes.c_int
        registry_count = dll.maximum_meshopt_test_registry_count
        registry_count.argtypes = []
        registry_count.restype = ctypes.c_size_t

        positions = (ctypes.c_float * 9)(0, 0, 0, 1, 0, 0, 0, 1, 0)
        normals = (ctypes.c_float * 9)(*(0, 0, 1) * 3)
        uvs = (ctypes.c_float * 6)(0, 0, 1, 0, 0, 1)
        weights = (ctypes.c_float * 12)(*(1, 0, 0, 0) * 3)
        bones = (ctypes.c_uint32 * 12)(*(0, 0, 0, 0) * 3)
        indices = (ctypes.c_uint32 * 3)(0, 1, 2)
        materials = (ctypes.c_uint32 * 1)(0)
        flags = (ctypes.c_ubyte * 3)(0, 0, 0)
        native_input = _MaximumMeshInput(
            ctypes.sizeof(_MaximumMeshInput), positions, normals, uvs, weights, bones, 1,
            3, indices, 3, materials, 1, flags,
        )
        options = _MaximumMeshOptions(ctypes.sizeof(_MaximumMeshOptions), 0.5, 1.0, 0, 0)

        for failure, expected in ((1, -6), (2, -8)):
            with self.subTest(failure=failure):
                output = _MaximumMeshOutput(
                    struct_size=ctypes.sizeof(_MaximumMeshOutput),
                    vertex_count=99,
                    index_count=99,
                    triangle_count=99,
                )
                self.assertEqual(fail_next(failure), 1)
                code = dll.maximum_meshopt_simplify(
                    ctypes.byref(native_input), ctypes.byref(options), ctypes.byref(output)
                )
                self.assertEqual(code, expected)
                self.assertFalse(output.positions)
                self.assertFalse(output.indices)
                self.assertEqual(
                    (output.vertex_count, output.index_count, output.triangle_count),
                    (0, 0, 0),
                )
                self.assertEqual(registry_count(), 0)

    def test_skin_slots_are_canonical_by_bone_id_after_weight_selection(self) -> None:
        weights, bones = _canonicalize_skin_slots(
            (0.9, 0.1, 0.0, 0.0), (7, 2, 999999, 4000000)
        )
        self.assertEqual(bones, (2, 7, 0, 0))
        self.assertEqual(weights, (0.1, 0.9, 0.0, 0.0))

        dense_weights, dense_bones = _canonicalize_skin_slots(
            (0.2, 0.4, 0.3, 0.1), (1000000, 2, 700000, 9)
        )
        self.assertEqual(dense_bones, (2, 9, 700000, 1000000))
        for actual, expected in zip(dense_weights, (0.4, 0.1, 0.3, 0.2)):
            self.assertAlmostEqual(actual, expected)

    def test_native_skin_metric_matches_python_canonical_slots(self) -> None:
        source = _grid(4)
        weights = []
        bones = []
        for index in range(len(source.positions)):
            if index % 2:
                weights.append((0.1, 0.9, 0.0, 0.0))
                bones.append((2, 7, 1000000, 900000))
            else:
                weights.append((0.9, 0.1, 0.0, 0.0))
                bones.append((7, 2, 900000, 1000000))
        mesh = MeshInput(**{
            **source.__dict__, "weights": tuple(weights), "bone_indices": tuple(bones)
        })
        result = simplify_mesh(mesh, SimplifyOptions(0.7, 1.0, update_vertices=True))
        for row_weights, row_bones in zip(result.weights, result.bone_indices):
            self.assertEqual(row_bones, (2, 7, 0, 0))
            self.assertAlmostEqual(row_weights[0], 0.1, places=5)
            self.assertAlmostEqual(row_weights[1], 0.9, places=5)

    def test_python_rejects_meshoptimizer_count_limit_before_iteration(self) -> None:
        class HugeCount:
            def __len__(self) -> int:
                return 1 << 28

            def __iter__(self):
                raise AssertionError("must reject before iterating")

        fake = SimpleNamespace(positions=HugeCount())
        with self.assertRaisesRegex(ValueError, "vertex_count is out of range"):
            _validate(fake, SimplifyOptions(0.5, 0.1))

    def test_mesh_input_deep_freezes_lists_and_bone_ids(self) -> None:
        source = _grid(4)
        positions = [list(row) for row in source.positions]
        bone_indices = [list(row) for row in source.bone_indices]
        mutable = MeshInput(**{**source.__dict__, "positions": positions, "bone_indices": bone_indices})
        positions[0][0] = 999
        bone_indices[0][0] = 999
        self.assertEqual(mutable.positions[0], (0.0, 0.0, 0.0))
        self.assertEqual(mutable.bone_indices[0], source.bone_indices[0])

    def test_ctypes_facade_rejects_hostile_success_output_before_dereference(self) -> None:
        source = _grid(4)

        class FakeDll:
            def maximum_meshopt_simplify(self, _input, _options, output_pointer):
                output = ctypes.cast(output_pointer, ctypes.POINTER(_MaximumMeshOutput)).contents
                output.vertex_count = len(source.positions) + 1
                output.index_count = 2
                output.triangle_count = 9
                output.result_error = float("nan")
                return 0

            def maximum_meshopt_destroy(self, _output):
                return None

            def maximum_meshopt_version(self):
                return 10200

        with mock.patch("maximum_optimizer.meshopt_bridge.load_library", return_value=FakeDll()):
            with self.assertRaisesRegex(RuntimeError, "invalid native output"):
                simplify_mesh(source, SimplifyOptions(0.5, 1.0))

        class NonFiniteFakeDll(FakeDll):
            def __init__(self):
                self.positions = (ctypes.c_float * (len(source.positions) * 3))(*([0.0] * (len(source.positions) * 3)))
                self.positions[0] = float("nan")
                self.normals = (ctypes.c_float * (len(source.positions) * 3))(*([0.0, 0.0, 1.0] * len(source.positions)))
                self.uvs = (ctypes.c_float * (len(source.positions) * 2))(*([0.0] * (len(source.positions) * 2)))
                self.weights = (ctypes.c_float * (len(source.positions) * 4))(*([1.0, 0.0, 0.0, 0.0] * len(source.positions)))
                self.bones = (ctypes.c_uint32 * (len(source.positions) * 4))(*([0, 0, 0, 0] * len(source.positions)))
                self.indices = (ctypes.c_uint32 * 3)(0, 1, 2)
                self.materials = (ctypes.c_uint32 * 1)(0)

            def maximum_meshopt_simplify(self, _input, _options, output_pointer):
                output = ctypes.cast(output_pointer, ctypes.POINTER(_MaximumMeshOutput)).contents
                output.positions, output.normals, output.uvs = self.positions, self.normals, self.uvs
                output.weights, output.bone_indices = self.weights, self.bones
                output.vertex_count = len(source.positions)
                output.indices, output.index_count = self.indices, 3
                output.material_ids, output.triangle_count = self.materials, 1
                output.result_error = 0.0
                output.ownership_cookie = 1
                return 0

        with mock.patch("maximum_optimizer.meshopt_bridge.load_library", return_value=NonFiniteFakeDll()):
            with self.assertRaisesRegex(RuntimeError, "non-finite"):
                simplify_mesh(source, SimplifyOptions(0.5, 1.0))

    def test_native_abi_rejects_hostile_counts_data_and_options_with_zero_output(self) -> None:
        dll = load_library()
        positions = (ctypes.c_float * 9)(0, 0, 0, 1, 0, 0, 0, 1, 0)
        normals = (ctypes.c_float * 9)(*(0, 0, 1) * 3)
        uvs = (ctypes.c_float * 6)(0, 0, 1, 0, 0, 1)
        weights = (ctypes.c_float * 12)(*(1, 0, 0, 0) * 3)
        bones = (ctypes.c_uint32 * 12)(*(0, 0, 0, 0) * 3)
        indices = (ctypes.c_uint32 * 3)(0, 1, 2)
        materials = (ctypes.c_uint32 * 1)(7)
        flags = (ctypes.c_ubyte * 3)(0, 0, 0)

        native_input = _MaximumMeshInput(
            ctypes.sizeof(_MaximumMeshInput), positions, normals, uvs, weights, bones, 1, 3,
            indices, 3, materials, 1, flags,
        )
        options = _MaximumMeshOptions(ctypes.sizeof(_MaximumMeshOptions), 0.5, 1.0, 0, 0)

        def invoke() -> tuple[int, _MaximumMeshOutput]:
            output = _MaximumMeshOutput(
                struct_size=ctypes.sizeof(_MaximumMeshOutput),
                vertex_count=99,
                index_count=99,
                triangle_count=99,
            )
            code = dll.maximum_meshopt_simplify(ctypes.byref(native_input), ctypes.byref(options), ctypes.byref(output))
            self.assertFalse(output.positions)
            self.assertFalse(output.indices)
            self.assertEqual((output.vertex_count, output.index_count, output.triangle_count), (0, 0, 0))
            return code, output

        native_input.vertex_count = ctypes.c_size_t(-1).value
        self.assertEqual(invoke()[0], -3)
        native_input.vertex_count = 1 << 28
        self.assertEqual(invoke()[0], -3)
        native_input.vertex_count = 3
        native_input.triangle_count = 0
        self.assertEqual(invoke()[0], -3)
        native_input.triangle_count = 1
        indices[2] = 9
        self.assertEqual(invoke()[0], -4)
        indices[2] = 2
        positions[0] = float("nan")
        self.assertEqual(invoke()[0], -4)
        positions[0] = 0
        options.target_ratio = 0
        self.assertEqual(invoke()[0], -5)

        output = _MaximumMeshOutput(struct_size=0, vertex_count=81)
        self.assertEqual(
            dll.maximum_meshopt_simplify(ctypes.byref(native_input), ctypes.byref(options), ctypes.byref(output)),
            -2,
        )
        self.assertEqual(output.vertex_count, 81)

    def test_rejects_hostile_python_inputs_before_crossing_abi(self) -> None:
        source = _grid(4)
        cases = (
            MeshInput(**{**source.__dict__, "indices": (0, 1)}),
            MeshInput(**{**source.__dict__, "indices": (0, 1, 999)}),
            MeshInput(**{**source.__dict__, "positions": source.positions[:-1]}),
            MeshInput(**{**source.__dict__, "positions": ((float("nan"), 0.0, 0.0),) + source.positions[1:]}),
            MeshInput(**{**source.__dict__, "material_ids": ()}),
            MeshInput(**{**source.__dict__, "vertex_flags": source.vertex_flags + (PRIORITY,)}),
            MeshInput(**{**source.__dict__, "weights": ((0.0, 0.0, 0.0, 0.0),) + source.weights[1:]}),
        )
        for mesh in cases:
            with self.subTest(mesh=mesh):
                with self.assertRaises(ValueError):
                    simplify_mesh(mesh, SimplifyOptions(0.5, 0.1))

        for options in (
            SimplifyOptions(0.0, 0.1),
            SimplifyOptions(1.1, 0.1),
            SimplifyOptions(0.5, -1.0),
            SimplifyOptions(0.5, float("nan")),
            SimplifyOptions(0.5, 0.1, meshopt_options=1 << 20),
        ):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    simplify_mesh(source, options)

    def test_missing_dll_error_names_expected_path(self) -> None:
        previous = os.environ.get("MAXIMUM_MESHOPT_DLL")
        missing = Path(__file__).with_name("definitely-missing-meshopt.dll")
        os.environ["MAXIMUM_MESHOPT_DLL"] = str(missing)
        try:
            with self.assertRaisesRegex(RuntimeError, "meshopt_bridge.dll not built"):
                load_library(cache=False)
        finally:
            if previous is None:
                os.environ.pop("MAXIMUM_MESHOPT_DLL", None)
            else:
                os.environ["MAXIMUM_MESHOPT_DLL"] = previous


if __name__ == "__main__":
    unittest.main()
