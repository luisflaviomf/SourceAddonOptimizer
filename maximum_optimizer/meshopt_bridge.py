from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
import os
import struct
from pathlib import Path
from typing import Iterable, Sequence


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
_MESHOPT_COUNT_LIMIT = 1 << 28
_SOURCE_VERTEX_LIMIT = 65536
_SOURCE_TRIANGLE_LIMIT = 65536
_DISTANCE_FIELD_CELL_LIMIT = 1 << 28
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
    bone_indices: tuple[tuple[int, int, int, int], ...] = ()

    def __post_init__(self) -> None:
        for name in ("positions", "normals", "uvs", "weights", "bone_indices"):
            value = getattr(self, name)
            object.__setattr__(self, name, tuple(tuple(row) for row in value))
        for name in ("indices", "material_ids", "vertex_flags"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not self.bone_indices:
            object.__setattr__(self, "bone_indices", tuple((0, 0, 0, 0) for _ in self.positions))


@dataclass(frozen=True)
class SimplifyOptions:
    target_ratio: float
    target_error: float
    update_vertices: bool = False
    meshopt_options: int = SIMPLIFY_LOCK_BORDER | SIMPLIFY_REGULARIZE_LIGHT
    position_remap: bool = False


@dataclass(frozen=True)
class SimplifiedMesh:
    positions: tuple[tuple[float, float, float], ...]
    normals: tuple[tuple[float, float, float], ...]
    uvs: tuple[tuple[float, float], ...]
    weights: tuple[tuple[float, float, float, float], ...]
    indices: tuple[int, ...]
    material_ids: tuple[int, ...]
    bone_indices: tuple[tuple[int, int, int, int], ...]
    result_error: float
    engine_version: int
    source_vertex_indices: tuple[int, ...] = ()


def _float32_row(values: Iterable[float]) -> tuple[bytes, ...]:
    return tuple(struct.pack("=f", float(value)) for value in values)


def compact_direct_result(mesh: MeshInput, result: SimplifiedMesh) -> SimplifiedMesh:
    """Compact a no-update result without synthesizing or merging any vertex tuple."""
    if len(result.material_ids) != len(result.indices) // 3:
        raise RuntimeError("direct material ownership is invalid")
    if not (
        len(result.positions) == len(result.normals) == len(result.uvs)
        == len(result.weights) == len(result.bone_indices) == len(mesh.positions)
    ):
        raise RuntimeError("direct attribute arrays do not match the source")
    source_materials: dict[int, set[int]] = {}
    for triangle, material in enumerate(mesh.material_ids):
        for source_index in mesh.indices[triangle * 3 : triangle * 3 + 3]:
            source_materials.setdefault(source_index, set()).add(material)
    for triangle, material in enumerate(result.material_ids):
        if any(
            material not in source_materials.get(source_index, set())
            for source_index in result.indices[triangle * 3 : triangle * 3 + 3]
        ):
            raise RuntimeError("direct material ownership differs from the source tuple")
    order = tuple(dict.fromkeys(result.indices))
    for source_index in order:
        if source_index < 0 or source_index >= len(mesh.positions):
            raise RuntimeError("direct result references an invalid source tuple")
        if (
            _float32_row(result.positions[source_index]) != _float32_row(mesh.positions[source_index])
            or _float32_row(result.normals[source_index]) != _float32_row(mesh.normals[source_index])
            or _float32_row(result.uvs[source_index]) != _float32_row(mesh.uvs[source_index])
            or _float32_row(result.weights[source_index]) != _float32_row(mesh.weights[source_index])
            or result.bone_indices[source_index] != mesh.bone_indices[source_index]
        ):
            raise RuntimeError("direct attribute tuple is not bitwise source-identical")
    remap = {source_index: target for target, source_index in enumerate(order)}
    return SimplifiedMesh(
        positions=tuple(result.positions[index] for index in order),
        normals=tuple(result.normals[index] for index in order),
        uvs=tuple(result.uvs[index] for index in order),
        weights=tuple(result.weights[index] for index in order),
        bone_indices=tuple(result.bone_indices[index] for index in order),
        indices=tuple(remap[index] for index in result.indices),
        material_ids=result.material_ids,
        result_error=result.result_error,
        engine_version=result.engine_version,
        source_vertex_indices=order,
    )


class _MaximumMeshInput(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("positions", ctypes.POINTER(ctypes.c_float)),
        ("normals", ctypes.POINTER(ctypes.c_float)),
        ("uvs", ctypes.POINTER(ctypes.c_float)),
        ("weights", ctypes.POINTER(ctypes.c_float)),
        ("bone_indices", ctypes.POINTER(ctypes.c_uint32)),
        ("bone_count", ctypes.c_size_t),
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
        ("bone_indices", ctypes.POINTER(ctypes.c_uint32)),
        ("vertex_count", ctypes.c_size_t),
        ("indices", ctypes.POINTER(ctypes.c_uint32)),
        ("index_count", ctypes.c_size_t),
        ("material_ids", ctypes.POINTER(ctypes.c_uint32)),
        ("triangle_count", ctypes.c_size_t),
        ("result_error", ctypes.c_float),
        ("ownership_cookie", ctypes.c_uint64),
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
    dll.maximum_meshopt_abi_version.argtypes = []
    dll.maximum_meshopt_abi_version.restype = ctypes.c_int
    dll.maximum_meshopt_simplify.argtypes = [
        ctypes.POINTER(_MaximumMeshInput),
        ctypes.POINTER(_MaximumMeshOptions),
        ctypes.POINTER(_MaximumMeshOutput),
    ]
    dll.maximum_meshopt_simplify.restype = ctypes.c_int
    dll.maximum_meshopt_destroy.argtypes = [ctypes.POINTER(_MaximumMeshOutput)]
    dll.maximum_meshopt_destroy.restype = None
    dll.maximum_squared_euclidean_distance_field.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_size_t,
    ]
    dll.maximum_squared_euclidean_distance_field.restype = ctypes.c_int
    dll.maximum_silhouette_boundary_distances_squared.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_size_t,
    ]
    dll.maximum_silhouette_boundary_distances_squared.restype = ctypes.c_int
    if dll.maximum_meshopt_version() != 10200:
        raise RuntimeError(f"unsupported meshopt bridge version in {path}")
    if dll.maximum_meshopt_abi_version() != 4:
        raise RuntimeError(f"unsupported meshopt bridge ABI in {path}")
    if cache:
        _DLL_CACHE[path] = dll
    return dll


def squared_euclidean_distance_field(
    width: int,
    height: int,
    points: Sequence[tuple[int, int]],
) -> tuple[int, ...]:
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("distance-field dimensions must be positive integers")
    cell_count = width * height
    if width > _UINT32_MAX or height > _UINT32_MAX or cell_count >= _DISTANCE_FIELD_CELL_LIMIT:
        raise ValueError("distance-field dimensions are out of range")
    point_rows = tuple(points)
    if not point_rows:
        raise ValueError("distance-field points must not be empty")
    if any(
        len(point) != 2
        or type(point[0]) is not int
        or type(point[1]) is not int
        or point[0] < 0
        or point[0] >= width
        or point[1] < 0
        or point[1] >= height
        for point in point_rows
    ):
        raise ValueError("distance-field point is outside the grid")

    point_buffer = (ctypes.c_uint32 * (len(point_rows) * 2))(
        *(coordinate for point in point_rows for coordinate in point)
    )
    output_buffer = (ctypes.c_uint32 * cell_count)()
    code = load_library().maximum_squared_euclidean_distance_field(
        width,
        height,
        point_buffer,
        len(point_rows),
        output_buffer,
        cell_count,
    )
    if code != 0:
        raise RuntimeError(f"distance field failed with native error {code}")
    return tuple(output_buffer)


def silhouette_boundary_distances_squared(
    width: int,
    height: int,
    original: Sequence[tuple[int, int]],
    candidate: Sequence[tuple[int, int]],
) -> tuple[int, ...]:
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("silhouette dimensions must be positive integers")
    cell_count = width * height
    if width > _UINT32_MAX or height > _UINT32_MAX or cell_count >= _DISTANCE_FIELD_CELL_LIMIT:
        raise ValueError("silhouette dimensions are out of range")
    original_points = tuple(original)
    candidate_points = tuple(candidate)
    if not original_points or not candidate_points:
        raise ValueError("silhouette boundaries must not be empty")
    if any(
        len(point) != 2
        or type(point[0]) is not int
        or type(point[1]) is not int
        or point[0] < 0
        or point[0] >= width
        or point[1] < 0
        or point[1] >= height
        for point in (*original_points, *candidate_points)
    ):
        raise ValueError("silhouette boundary point is outside the grid")

    original_buffer = (ctypes.c_uint32 * (len(original_points) * 2))(
        *(coordinate for point in original_points for coordinate in point)
    )
    candidate_buffer = (ctypes.c_uint32 * (len(candidate_points) * 2))(
        *(coordinate for point in candidate_points for coordinate in point)
    )
    output_count = len(original_points) + len(candidate_points)
    output_buffer = (ctypes.c_uint32 * output_count)()
    code = load_library().maximum_silhouette_boundary_distances_squared(
        width,
        height,
        original_buffer,
        len(original_points),
        candidate_buffer,
        len(candidate_points),
        output_buffer,
        output_count,
    )
    if code != 0:
        raise RuntimeError(f"silhouette boundary distance failed with native error {code}")
    return tuple(output_buffer)


def _finite_rows(name: str, rows: Iterable[tuple[float, ...]], width: int) -> None:
    for row in rows:
        if len(row) != width or not all(math.isfinite(float(value)) for value in row):
            raise ValueError(f"{name} must contain finite float{width} rows")


def _canonicalize_skin_slots(
    weights: Iterable[float], bone_indices: Iterable[int]
) -> tuple[tuple[float, float, float, float], tuple[int, int, int, int]]:
    weight_row = tuple(float(value) for value in weights)
    bone_row = tuple(bone_indices)
    if len(weight_row) != 4 or len(bone_row) != 4:
        raise ValueError("skin slots must contain four weights and four bone ids")
    if not all(math.isfinite(value) for value in weight_row):
        raise ValueError("skin weights must be finite")
    if any(type(bone) is not int or bone < 0 or bone >= _UINT32_MAX for bone in bone_row):
        raise ValueError("skin bone ids must be uint32 values")
    combined: dict[int, float] = {}
    for bone, weight in zip(bone_row, weight_row):
        clamped = min(1.0, max(0.0, weight))
        if clamped:
            combined[bone] = combined.get(bone, 0.0) + clamped
    selected = sorted(combined.items(), key=lambda item: (-item[1], item[0]))[:4]
    total = sum(weight for _bone, weight in selected)
    if total <= 1e-12 or not math.isfinite(total):
        raise ValueError("skin weights contain a zero-sum vertex")
    canonical = sorted(((bone, weight / total) for bone, weight in selected), key=lambda item: item[0])
    out_bones = tuple(bone for bone, _weight in canonical) + (0,) * (4 - len(canonical))
    out_weights = tuple(weight for _bone, weight in canonical) + (0.0,) * (4 - len(canonical))
    return out_weights, out_bones  # type: ignore[return-value]


def _validate(mesh: MeshInput, options: SimplifyOptions) -> None:
    count = len(mesh.positions)
    if count == 0 or count >= _MESHOPT_COUNT_LIMIT:
        raise ValueError("vertex_count is out of range")
    if count > _SOURCE_VERTEX_LIMIT:
        raise ValueError("Source vertex limit exceeded")
    _finite_rows("positions", mesh.positions, 3)
    _finite_rows("normals", mesh.normals, 3)
    _finite_rows("uvs", mesh.uvs, 2)
    _finite_rows("weights", mesh.weights, 4)
    if any(sum(min(1.0, max(0.0, float(value))) for value in row) <= 1e-12 for row in mesh.weights):
        raise ValueError("weights contain a zero-sum vertex")
    if not (
        len(mesh.normals) == len(mesh.uvs) == len(mesh.weights)
        == len(mesh.vertex_flags) == len(mesh.bone_indices) == count
    ):
        raise ValueError("all vertex attributes must match vertex_count")
    if any(
        len(row) != 4 or any(type(value) is not int or value < 0 or value >= _UINT32_MAX for value in row)
        for row in mesh.bone_indices
    ):
        raise ValueError("bone_indices must contain uint32x4 rows")
    if len(mesh.indices) < 3 or len(mesh.indices) % 3:
        raise ValueError("indices must contain complete triangles")
    if len(mesh.indices) // 3 > _SOURCE_TRIANGLE_LIMIT:
        raise ValueError("Source triangle limit exceeded")
    if len(mesh.indices) >= _MESHOPT_COUNT_LIMIT:
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
    if options.update_vertices:
        rigid_bones: set[int] = set()
        for weights, bones in zip(mesh.weights, mesh.bone_indices):
            positive = tuple(
                (bone, float(weight))
                for weight, bone in zip(weights, bones)
                if float(weight) > 1e-8
            )
            if len(positive) != 1 or abs(positive[0][1] - 1.0) > 1e-4:
                raise ValueError("non-rigid skinned regions cannot update vertices")
            rigid_bones.add(positive[0][0])
        if len(rigid_bones) != 1:
            raise ValueError("non-rigid skinned regions cannot update vertices")
    if type(options.position_remap) is not bool or (options.position_remap and options.update_vertices):
        raise ValueError("position_remap requires the direct no-update path")
    if not math.isfinite(float(options.target_ratio)) or not 0.0 < options.target_ratio <= 1.0:
        raise ValueError("target_ratio must be in (0, 1]")
    if not math.isfinite(float(options.target_error)) or options.target_error < 0.0:
        raise ValueError("target_error must be finite and non-negative")
    if (
        type(options.meshopt_options) is not int
        or options.meshopt_options < 0
        or options.meshopt_options & ~((1 << 7) - 1)
    ):
        raise ValueError("meshopt_options is out of range")


def _flat(rows: Iterable[tuple[float, ...]]) -> list[float]:
    return [float(value) for row in rows for value in row]


def simplify_mesh(mesh: MeshInput, options: SimplifyOptions) -> SimplifiedMesh:
    _validate(mesh, options)
    dll = load_library()

    canonical_skin = tuple(
        _canonicalize_skin_slots(weights, bones)
        for weights, bones in zip(mesh.weights, mesh.bone_indices)
    )
    canonical_weights = tuple(weights for weights, _bones in canonical_skin)
    canonical_bones = tuple(bones for _weights, bones in canonical_skin)
    input_weights = canonical_weights if options.update_vertices else mesh.weights
    input_bones = canonical_bones if options.update_vertices else mesh.bone_indices

    position_buffer = (ctypes.c_float * (len(mesh.positions) * 3))(*_flat(mesh.positions))
    normal_buffer = (ctypes.c_float * (len(mesh.normals) * 3))(*_flat(mesh.normals))
    uv_buffer = (ctypes.c_float * (len(mesh.uvs) * 2))(*_flat(mesh.uvs))
    weight_buffer = (ctypes.c_float * (len(input_weights) * 4))(*_flat(input_weights))
    bone_buffer = (ctypes.c_uint32 * (len(input_bones) * 4))(
        *(value for row in input_bones for value in row)
    )
    index_buffer = (ctypes.c_uint32 * len(mesh.indices))(*mesh.indices)
    material_buffer = (ctypes.c_uint32 * len(mesh.material_ids))(*mesh.material_ids)
    flag_buffer = (ctypes.c_ubyte * len(mesh.vertex_flags))(*mesh.vertex_flags)

    native_input = _MaximumMeshInput(
        ctypes.sizeof(_MaximumMeshInput),
        position_buffer,
        normal_buffer,
        uv_buffer,
        weight_buffer,
        bone_buffer,
        max((value for row in input_bones for value in row), default=0) + 1,
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
        2 if options.position_remap else int(options.update_vertices),
    )
    output = _MaximumMeshOutput(struct_size=ctypes.sizeof(_MaximumMeshOutput))
    code = dll.maximum_meshopt_simplify(
        ctypes.byref(native_input), ctypes.byref(native_options), ctypes.byref(output)
    )
    if code != 0:
        raise RuntimeError(f"meshopt simplification failed with native error {code}")
    try:
        if (
            output.vertex_count == 0
            or output.vertex_count > len(mesh.positions)
            or output.index_count == 0
            or output.index_count > len(mesh.indices)
            or output.index_count % 3
            or output.triangle_count != output.index_count // 3
            or not output.positions
            or not output.normals
            or not output.uvs
            or not output.weights
            or not output.bone_indices
            or not output.indices
            or not output.material_ids
            or not math.isfinite(float(output.result_error))
            or output.ownership_cookie == 0
        ):
            raise RuntimeError("invalid native output contract")
        returned_indices = tuple(output.indices[i] for i in range(output.index_count))
        if any(index >= output.vertex_count for index in returned_indices):
            raise RuntimeError("invalid native output indices")
        positions = tuple(tuple(output.positions[i * 3 + j] for j in range(3)) for i in range(output.vertex_count))
        normals = tuple(tuple(output.normals[i * 3 + j] for j in range(3)) for i in range(output.vertex_count))
        uvs = tuple(tuple(output.uvs[i * 2 + j] for j in range(2)) for i in range(output.vertex_count))
        weights = tuple(tuple(output.weights[i * 4 + j] for j in range(4)) for i in range(output.vertex_count))
        bone_indices = tuple(
            tuple(output.bone_indices[i * 4 + j] for j in range(4))
            for i in range(output.vertex_count)
        )
        indices = returned_indices
        materials = tuple(output.material_ids[i] for i in range(output.triangle_count))
        if not all(
            math.isfinite(float(value))
            for rows in (positions, normals, uvs, weights)
            for row in rows
            for value in row
        ):
            raise RuntimeError("non-finite native output attribute")
        if any(
            any(value < 0.0 or value > 1.0 for value in row)
            or abs(sum(row) - 1.0) > 1e-4
            for row in weights
        ):
            raise RuntimeError("invalid native output weights")
        bone_count = max((value for row in input_bones for value in row), default=0) + 1
        if any(
            bone >= bone_count
            for row_weights, row_bones in zip(weights, bone_indices)
            for weight, bone in zip(row_weights, row_bones)
            if weight > 0.0
        ):
            raise RuntimeError("invalid native output bone index")
        return SimplifiedMesh(
            positions=positions,
            normals=normals,
            uvs=uvs,
            weights=weights,
            indices=indices,
            material_ids=materials,
            bone_indices=bone_indices,
            result_error=float(output.result_error),
            engine_version=dll.maximum_meshopt_version(),
        )
    finally:
        dll.maximum_meshopt_destroy(ctypes.byref(output))
