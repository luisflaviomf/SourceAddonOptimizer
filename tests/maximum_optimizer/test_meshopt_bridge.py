from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import unittest

from maximum_optimizer.meshopt_bridge import (
    LOCK,
    PRIORITY,
    PROTECT,
    MeshInput,
    SimplifyOptions,
    load_library,
    simplify_mesh,
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
    )


class MeshoptBridgeTests(unittest.TestCase):
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

    def test_native_abi_rejects_hostile_counts_data_and_options_with_zero_output(self) -> None:
        dll = load_library()
        positions = (ctypes.c_float * 9)(0, 0, 0, 1, 0, 0, 0, 1, 0)
        normals = (ctypes.c_float * 9)(*(0, 0, 1) * 3)
        uvs = (ctypes.c_float * 6)(0, 0, 1, 0, 0, 1)
        weights = (ctypes.c_float * 12)(*(1, 0, 0, 0) * 3)
        indices = (ctypes.c_uint32 * 3)(0, 1, 2)
        materials = (ctypes.c_uint32 * 1)(7)
        flags = (ctypes.c_ubyte * 3)(0, 0, 0)

        native_input = _MaximumMeshInput(
            ctypes.sizeof(_MaximumMeshInput), positions, normals, uvs, weights, 3,
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
