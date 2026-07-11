from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor

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
)


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
