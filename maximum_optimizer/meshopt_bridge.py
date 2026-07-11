from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Iterable


LOCK = 1 << 0
PROTECT = 1 << 1
PRIORITY = 1 << 2

SIMPLIFY_LOCK_BORDER = 1 << 0
SIMPLIFY_SPARSE = 1 << 1
SIMPLIFY_ERROR_ABSOLUTE = 1 << 2
SIMPLIFY_PRUNE = 1 << 3
SIMPLIFY_REGULARIZE = 1 << 4
SIMPLIFY_PERMISSIVE = 1 << 5
SIMPLIFY_REGULARIZE_LIGHT = 1 << 6

_UINT32_MAX = (1 << 32) - 1
_DLL_CACHE: dict[Path, ctypes.WinDLL] = {}


@dataclass(frozen=True)
class MeshInput:
    positions: tuple[tuple[float, float, float], ...]
    normals: tuple[tuple[float, float, float], ...]
    uvs: tuple[tuple[float, float], ...]
    weights: tuple[tuple[float, float, float, float], ...]
    indices: tuple[int, ...]
    material_ids: tuple[int, ...]
    vertex_flags: tuple[int, ...]


@dataclass(frozen=True)
class SimplifyOptions:
    target_ratio: float
    target_error: float
    update_vertices: bool = False
    meshopt_options: int = SIMPLIFY_LOCK_BORDER | SIMPLIFY_PERMISSIVE


@dataclass(frozen=True)
class SimplifiedMesh:
    positions: tuple[tuple[float, float, float], ...]
    normals: tuple[tuple[float, float, float], ...]
    uvs: tuple[tuple[float, float], ...]
    weights: tuple[tuple[float, float, float, float], ...]
    indices: tuple[int, ...]
    material_ids: tuple[int, ...]
    result_error: float
    engine_version: int


class _MaximumMeshInput(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("positions", ctypes.POINTER(ctypes.c_float)),
        ("normals", ctypes.POINTER(ctypes.c_float)),
        ("uvs", ctypes.POINTER(ctypes.c_float)),
        ("weights", ctypes.POINTER(ctypes.c_float)),
        ("vertex_count", ctypes.c_size_t),
        ("indices", ctypes.POINTER(ctypes.c_uint32)),
        ("index_count", ctypes.c_size_t),
        ("material_ids", ctypes.POINTER(ctypes.c_uint32)),
        ("triangle_count", ctypes.c_size_t),
        ("vertex_flags", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _MaximumMeshOptions(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("target_ratio", ctypes.c_float),
        ("target_error", ctypes.c_float),
        ("meshopt_options", ctypes.c_uint32),
        ("update_vertices", ctypes.c_uint32),
    ]


class _MaximumMeshOutput(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("positions", ctypes.POINTER(ctypes.c_float)),
        ("normals", ctypes.POINTER(ctypes.c_float)),
        ("uvs", ctypes.POINTER(ctypes.c_float)),
        ("weights", ctypes.POINTER(ctypes.c_float)),
        ("vertex_count", ctypes.c_size_t),
        ("indices", ctypes.POINTER(ctypes.c_uint32)),
        ("index_count", ctypes.c_size_t),
        ("material_ids", ctypes.POINTER(ctypes.c_uint32)),
        ("triangle_count", ctypes.c_size_t),
        ("result_error", ctypes.c_float),
    ]


def _default_dll_path() -> Path:
    configured = os.environ.get("MAXIMUM_MESHOPT_DLL")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent / "native" / "bin" / "win-x64" / "meshopt_bridge.dll"


def load_library(*, cache: bool = True) -> ctypes.WinDLL:
    path = _default_dll_path()
    if not path.is_file():
        raise RuntimeError(f"meshopt_bridge.dll not built: {path}")
    if cache and path in _DLL_CACHE:
        return _DLL_CACHE[path]
    try:
        dll = ctypes.WinDLL(str(path), winmode=0)
    except OSError as exc:
        raise RuntimeError(f"cannot load meshopt_bridge.dll at {path}: {exc}") from exc
    dll.maximum_meshopt_version.argtypes = []
    dll.maximum_meshopt_version.restype = ctypes.c_int
    dll.maximum_meshopt_simplify.argtypes = [
        ctypes.POINTER(_MaximumMeshInput),
        ctypes.POINTER(_MaximumMeshOptions),
        ctypes.POINTER(_MaximumMeshOutput),
    ]
    dll.maximum_meshopt_simplify.restype = ctypes.c_int
    dll.maximum_meshopt_destroy.argtypes = [ctypes.POINTER(_MaximumMeshOutput)]
    dll.maximum_meshopt_destroy.restype = None
    if dll.maximum_meshopt_version() != 10200:
        raise RuntimeError(f"unsupported meshopt bridge version in {path}")
    if cache:
        _DLL_CACHE[path] = dll
    return dll


def _finite_rows(name: str, rows: Iterable[tuple[float, ...]], width: int) -> None:
    for row in rows:
        if len(row) != width or not all(math.isfinite(float(value)) for value in row):
            raise ValueError(f"{name} must contain finite float{width} rows")


def _validate(mesh: MeshInput, options: SimplifyOptions) -> None:
    count = len(mesh.positions)
    if count == 0 or count > _UINT32_MAX:
        raise ValueError("vertex_count is out of range")
    _finite_rows("positions", mesh.positions, 3)
    _finite_rows("normals", mesh.normals, 3)
    _finite_rows("uvs", mesh.uvs, 2)
    _finite_rows("weights", mesh.weights, 4)
    if any(sum(min(1.0, max(0.0, float(value))) for value in row) <= 1e-12 for row in mesh.weights):
        raise ValueError("weights contain a zero-sum vertex")
    if not (len(mesh.normals) == len(mesh.uvs) == len(mesh.weights) == len(mesh.vertex_flags) == count):
        raise ValueError("all vertex attributes must match vertex_count")
    if len(mesh.indices) < 3 or len(mesh.indices) % 3:
        raise ValueError("indices must contain complete triangles")
    if len(mesh.indices) > _UINT32_MAX:
        raise ValueError("index_count is out of range")
    if any(type(index) is not int or index < 0 or index >= count for index in mesh.indices):
        raise ValueError("indices contain an invalid vertex")
    if len(mesh.material_ids) != len(mesh.indices) // 3:
        raise ValueError("material_ids must contain one id per triangle")
    if any(type(value) is not int or value < 0 or value > _UINT32_MAX for value in mesh.material_ids):
        raise ValueError("material id is out of range")
    if any(type(flag) is not int or flag < 0 or flag & ~(LOCK | PROTECT | PRIORITY) for flag in mesh.vertex_flags):
        raise ValueError("vertex flag is invalid")
    if type(options.update_vertices) is not bool:
        raise ValueError("update_vertices must be bool")
    if not math.isfinite(float(options.target_ratio)) or not 0.0 < options.target_ratio <= 1.0:
        raise ValueError("target_ratio must be in (0, 1]")
    if not math.isfinite(float(options.target_error)) or options.target_error < 0.0:
        raise ValueError("target_error must be finite and non-negative")
    if type(options.meshopt_options) is not int or options.meshopt_options < 0 or options.meshopt_options > _UINT32_MAX:
        raise ValueError("meshopt_options is out of range")


def _flat(rows: Iterable[tuple[float, ...]]) -> list[float]:
    return [float(value) for row in rows for value in row]


def simplify_mesh(mesh: MeshInput, options: SimplifyOptions) -> SimplifiedMesh:
    _validate(mesh, options)
    dll = load_library()

    position_buffer = (ctypes.c_float * (len(mesh.positions) * 3))(*_flat(mesh.positions))
    normal_buffer = (ctypes.c_float * (len(mesh.normals) * 3))(*_flat(mesh.normals))
    uv_buffer = (ctypes.c_float * (len(mesh.uvs) * 2))(*_flat(mesh.uvs))
    weight_buffer = (ctypes.c_float * (len(mesh.weights) * 4))(*_flat(mesh.weights))
    index_buffer = (ctypes.c_uint32 * len(mesh.indices))(*mesh.indices)
    material_buffer = (ctypes.c_uint32 * len(mesh.material_ids))(*mesh.material_ids)
    flag_buffer = (ctypes.c_ubyte * len(mesh.vertex_flags))(*mesh.vertex_flags)

    native_input = _MaximumMeshInput(
        ctypes.sizeof(_MaximumMeshInput),
        position_buffer,
        normal_buffer,
        uv_buffer,
        weight_buffer,
        len(mesh.positions),
        index_buffer,
        len(mesh.indices),
        material_buffer,
        len(mesh.material_ids),
        flag_buffer,
    )
    native_options = _MaximumMeshOptions(
        ctypes.sizeof(_MaximumMeshOptions),
        options.target_ratio,
        options.target_error,
        options.meshopt_options,
        int(options.update_vertices),
    )
    output = _MaximumMeshOutput(struct_size=ctypes.sizeof(_MaximumMeshOutput))
    code = dll.maximum_meshopt_simplify(
        ctypes.byref(native_input), ctypes.byref(native_options), ctypes.byref(output)
    )
    if code != 0:
        raise RuntimeError(f"meshopt simplification failed with native error {code}")
    try:
        positions = tuple(tuple(output.positions[i * 3 + j] for j in range(3)) for i in range(output.vertex_count))
        normals = tuple(tuple(output.normals[i * 3 + j] for j in range(3)) for i in range(output.vertex_count))
        uvs = tuple(tuple(output.uvs[i * 2 + j] for j in range(2)) for i in range(output.vertex_count))
        weights = tuple(tuple(output.weights[i * 4 + j] for j in range(4)) for i in range(output.vertex_count))
        indices = tuple(output.indices[i] for i in range(output.index_count))
        materials = tuple(output.material_ids[i] for i in range(output.triangle_count))
        return SimplifiedMesh(
            positions=positions,
            normals=normals,
            uvs=uvs,
            weights=weights,
            indices=indices,
            material_ids=materials,
            result_error=float(output.result_error),
            engine_version=dll.maximum_meshopt_version(),
        )
    finally:
        dll.maximum_meshopt_destroy(ctypes.byref(output))
