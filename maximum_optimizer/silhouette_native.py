from __future__ import annotations

import ctypes
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct
import time


_CELL_LIMIT = 1 << 28
EXPECTED_SILHOUETTE_API_VERSION = "1.0.0"
EXPECTED_SILHOUETTE_BUILD_ID = "maximum-silhouette-raw-v1-20260722"
_PE_MACHINE_AMD64 = 0x8664
_CAPABILITY_RAW_MASK_BATCH = 1
_CANONICAL_VIEW_COUNT = 8
_MASK_FORMAT_BINARY_L = 1
_CALLING_CONVENTION_WINDOWS_X64 = 1
_BUILD_ID_CAPACITY = 64
_LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800


@dataclass(frozen=True)
class NativeSilhouettePackage:
    dll_path: Path
    sha256: str
    size: int
    api_version: str
    build_id: str
    architecture: str

    @classmethod
    def from_file_for_test(cls, dll_path: Path) -> "NativeSilhouettePackage":
        """Build an explicit source/test contract; production must use its package manifest."""
        path = Path(dll_path)
        payload = path.read_bytes()
        return cls(
            path.resolve(),
            hashlib.sha256(payload).hexdigest(),
            len(payload),
            EXPECTED_SILHOUETTE_API_VERSION,
            EXPECTED_SILHOUETTE_BUILD_ID,
            "x64",
        )


def load_native_silhouette_package(
    manifest_path: Path, package_root: Path
) -> NativeSilhouettePackage:
    manifest = Path(manifest_path)
    root = Path(package_root)
    if not manifest.is_absolute() or not root.is_absolute():
        raise ValueError("silhouette manifest and package root must be absolute")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        silhouette = payload["silhouette"]
        relative = str(silhouette["dllPath"]).replace("\\", "/")
        expected_hash = str(silhouette["sha256"])
        expected_size = int(silhouette["size"])
        api_version = str(silhouette["apiVersion"])
        build_id = str(silhouette["buildId"])
        architecture = str(silhouette["architecture"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid silhouette tool manifest at {manifest}: {exc}") from exc
    if payload.get("schemaVersion") != 1 or payload.get("toolVersion") != "0.1.18":
        raise RuntimeError(f"unsupported silhouette tool manifest contract at {manifest}")
    relative_path = Path(relative)
    if relative_path.is_absolute() or not relative or any(part in ("", ".", "..") for part in relative_path.parts):
        raise RuntimeError(f"unsafe silhouette DLL path in {manifest}: {relative}")
    resolved_root = root.resolve()
    dll_path = (resolved_root / relative_path).resolve()
    try:
        dll_path.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"silhouette DLL escapes package root: {relative}") from exc
    declared_files = {
        str(item.get("path", "")).replace("\\", "/"): item
        for item in payload.get("files", [])
        if isinstance(item, dict)
    }
    declared = declared_files.get(relative)
    if (
        declared is None
        or str(declared.get("sha256", "")).lower() != expected_hash.lower()
        or int(declared.get("size", -1)) != expected_size
    ):
        raise RuntimeError(f"silhouette DLL declaration disagrees with file manifest at {manifest}")
    return NativeSilhouettePackage(
        dll_path,
        expected_hash,
        expected_size,
        api_version,
        build_id,
        architecture,
    )


@dataclass(frozen=True)
class MaskBatch:
    buffer: bytearray
    width: int
    height: int
    view_count: int
    row_stride: int
    view_stride: int
    offset: int = 0


@dataclass(frozen=True)
class RawMaskBatchDiagnostics:
    input_marshaling_ns: int
    native_call_ns: int
    boundary_extraction_ns: int
    distance_calculation_ns: int
    metric_production_ns: int
    output_marshaling_ns: int
    caller_postprocessing_ns: int
    native_allocation_count: int
    native_allocation_bytes: int
    native_peak_scratch_bytes: int


@dataclass(frozen=True)
class RawMaskBatchDebug:
    original_boundaries: tuple[tuple[tuple[int, int], ...], ...]
    candidate_boundaries: tuple[tuple[tuple[int, int], ...], ...]
    distances_by_view: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class RawMaskBatchResult:
    intersections: tuple[int, ...]
    unions: tuple[int, ...]
    distance_count: int
    p95_distance_squared: int
    worst_iou_loss: float
    boundary_p95_px: float
    diagnostics: RawMaskBatchDiagnostics
    debug: RawMaskBatchDebug | None = None


class _NativeInput(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("view_count", ctypes.c_uint32),
        ("empty_distance", ctypes.c_uint32),
        ("original_masks", ctypes.POINTER(ctypes.c_ubyte)),
        ("original_row_stride", ctypes.c_size_t),
        ("original_view_stride", ctypes.c_size_t),
        ("candidate_masks", ctypes.POINTER(ctypes.c_ubyte)),
        ("candidate_row_stride", ctypes.c_size_t),
        ("candidate_view_stride", ctypes.c_size_t),
    ]


class _NativeOutput(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("p95_distance_squared", ctypes.c_uint32),
        ("distance_count", ctypes.c_uint64),
        ("intersections", ctypes.POINTER(ctypes.c_uint64)),
        ("unions", ctypes.POINTER(ctypes.c_uint64)),
        ("view_capacity", ctypes.c_size_t),
        ("original_boundary_xy", ctypes.POINTER(ctypes.c_uint32)),
        ("original_boundary_capacity", ctypes.c_size_t),
        ("original_boundary_count", ctypes.c_size_t),
        ("candidate_boundary_xy", ctypes.POINTER(ctypes.c_uint32)),
        ("candidate_boundary_capacity", ctypes.c_size_t),
        ("candidate_boundary_count", ctypes.c_size_t),
        ("distances_squared", ctypes.POINTER(ctypes.c_uint32)),
        ("distance_capacity", ctypes.c_size_t),
        ("distances_written", ctypes.c_size_t),
        ("original_boundary_offsets", ctypes.POINTER(ctypes.c_uint64)),
        ("candidate_boundary_offsets", ctypes.POINTER(ctypes.c_uint64)),
        ("distance_offsets", ctypes.POINTER(ctypes.c_uint64)),
        ("offset_capacity", ctypes.c_size_t),
        ("boundary_extraction_ns", ctypes.c_uint64),
        ("distance_calculation_ns", ctypes.c_uint64),
        ("metric_production_ns", ctypes.c_uint64),
        ("allocation_count", ctypes.c_uint64),
        ("allocation_bytes", ctypes.c_uint64),
        ("peak_scratch_bytes", ctypes.c_uint64),
    ]


class _NativeAbiInfo(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("api_major", ctypes.c_uint32),
        ("api_minor", ctypes.c_uint32),
        ("api_patch", ctypes.c_uint32),
        ("architecture", ctypes.c_uint32),
        ("canonical_view_count", ctypes.c_uint32),
        ("mask_format", ctypes.c_uint32),
        ("calling_convention", ctypes.c_uint32),
        ("pointer_size", ctypes.c_uint32),
        ("size_t_size", ctypes.c_uint32),
        ("uint32_size", ctypes.c_uint32),
        ("uint64_size", ctypes.c_uint32),
        ("input_struct_size", ctypes.c_uint32),
        ("input_struct_alignment", ctypes.c_uint32),
        ("output_struct_size", ctypes.c_uint32),
        ("output_struct_alignment", ctypes.c_uint32),
        ("abi_struct_size", ctypes.c_uint32),
        ("abi_struct_alignment", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint64),
        ("build_id", ctypes.c_char * _BUILD_ID_CAPACITY),
    ]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pe_machine(path: Path) -> int:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError(f"silhouette DLL is not a PE file: {path}")
        stream.seek(0x3C)
        offset_data = stream.read(4)
        if len(offset_data) != 4:
            raise RuntimeError(f"silhouette DLL has a truncated DOS header: {path}")
        pe_offset = struct.unpack("<I", offset_data)[0]
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            raise RuntimeError(f"silhouette DLL has an invalid PE signature: {path}")
        machine_data = stream.read(2)
        if len(machine_data) != 2:
            raise RuntimeError(f"silhouette DLL has a truncated PE header: {path}")
        return struct.unpack("<H", machine_data)[0]


def _alignment(structure: type[ctypes.Structure]) -> int:
    return ctypes.alignment(structure)


def _validate_batch(batch: MaskBatch, name: str) -> None:
    if not isinstance(batch.buffer, bytearray):
        raise TypeError(f"{name} buffer must be a bytearray")
    if (
        type(batch.width) is not int
        or type(batch.height) is not int
        or type(batch.view_count) is not int
        or batch.width <= 0
        or batch.height <= 0
        or batch.view_count <= 0
        or batch.width * batch.height >= _CELL_LIMIT
    ):
        raise ValueError(f"{name} dimensions are out of range")
    if type(batch.row_stride) is not int or batch.row_stride < batch.width:
        raise ValueError(f"{name} row stride is smaller than the width")
    minimum_view_stride = batch.row_stride * batch.height
    if type(batch.view_stride) is not int or batch.view_stride < minimum_view_stride:
        raise ValueError(f"{name} view stride is smaller than the image span")
    if type(batch.offset) is not int or batch.offset < 0:
        raise ValueError(f"{name} offset is out of range")
    required = (
        batch.offset
        + (batch.view_count - 1) * batch.view_stride
        + (batch.height - 1) * batch.row_stride
        + batch.width
    )
    if required > len(batch.buffer):
        raise ValueError(f"{name} buffer is smaller than its declared layout")


def pack_masks(images: tuple[object, ...], reusable: bytearray | None = None) -> MaskBatch:
    if not images:
        raise ValueError("at least one silhouette mask is required")
    first = images[0]
    if getattr(first, "mode", None) != "L":
        raise ValueError("silhouette masks must use Pillow L mode")
    width, height = first.size
    if any(getattr(image, "mode", None) != "L" or image.size != (width, height) for image in images):
        raise ValueError("silhouette masks must share L mode and dimensions")
    view_stride = width * height
    required = view_stride * len(images)
    storage = reusable if reusable is not None and len(reusable) >= required else bytearray(required)
    for view, image in enumerate(images):
        storage[view * view_stride : (view + 1) * view_stride] = image.tobytes()
    return MaskBatch(storage, width, height, len(images), width, view_stride)


def _split_points(
    flat: ctypes.Array[ctypes.c_uint32], offsets: ctypes.Array[ctypes.c_uint64], views: int
) -> tuple[tuple[tuple[int, int], ...], ...]:
    return tuple(
        tuple(
            (int(flat[index * 2]), int(flat[index * 2 + 1]))
            for index in range(int(offsets[view]), int(offsets[view + 1]))
        )
        for view in range(views)
    )


def _split_values(
    flat: ctypes.Array[ctypes.c_uint32], offsets: ctypes.Array[ctypes.c_uint64], views: int
) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(int(flat[index]) for index in range(int(offsets[view]), int(offsets[view + 1])))
        for view in range(views)
    )


class RawMaskSilhouetteKernel:
    def __init__(self, package: NativeSilhouettePackage) -> None:
        if not isinstance(package, NativeSilhouettePackage):
            raise TypeError("silhouette native loading requires a package contract")
        configured = Path(package.dll_path).expanduser()
        if not configured.is_absolute():
            raise ValueError("silhouette DLL path must be absolute")
        path = configured.resolve()
        if not path.is_file():
            raise RuntimeError(f"silhouette DLL is missing: {path}")
        actual_size = path.stat().st_size
        if type(package.size) is not int or package.size < 1 or actual_size != package.size:
            raise RuntimeError(
                f"silhouette DLL size mismatch at {path}: expected {package.size}, got {actual_size}"
            )
        actual_hash = _sha256_file(path)
        if actual_hash.lower() != package.sha256.lower():
            raise RuntimeError(
                f"silhouette DLL SHA-256 mismatch at {path}: expected {package.sha256}, got {actual_hash}"
            )
        if package.architecture != "x64":
            raise RuntimeError(f"unsupported silhouette package architecture: {package.architecture}")
        if struct.calcsize("P") != 8:
            raise RuntimeError("silhouette backend requires a 64-bit Python process")
        machine = _pe_machine(path)
        if machine != _PE_MACHINE_AMD64:
            raise RuntimeError(
                f"silhouette DLL is not AMD64 at {path}: PE machine 0x{machine:04x}"
            )
        try:
            self._dll = ctypes.WinDLL(
                str(path),
                winmode=_LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | _LOAD_LIBRARY_SEARCH_SYSTEM32,
            )
        except OSError as exc:
            raise RuntimeError(f"cannot load silhouette DLL at {path}: {exc}") from exc
        try:
            abi_function = self._dll.maximum_silhouette_get_abi_info_v1
        except AttributeError as exc:
            raise RuntimeError(f"silhouette ABI export missing from {path}") from exc
        abi_function.argtypes = [ctypes.POINTER(_NativeAbiInfo)]
        abi_function.restype = ctypes.c_int
        abi = _NativeAbiInfo(struct_size=ctypes.sizeof(_NativeAbiInfo))
        abi_code = abi_function(ctypes.byref(abi))
        if abi_code != 0:
            raise RuntimeError(f"silhouette ABI query failed with native error {abi_code}")
        self._validate_abi(package, abi, path)
        try:
            function = self._dll.maximum_silhouette_metrics_raw_batch_v1
        except AttributeError as exc:
            raise RuntimeError(f"silhouette metric export missing from {path}") from exc
        function.argtypes = [ctypes.POINTER(_NativeInput), ctypes.POINTER(_NativeOutput)]
        function.restype = ctypes.c_int
        self._measure = function
        self.dll_path = path
        self.api_version = f"{abi.api_major}.{abi.api_minor}.{abi.api_patch}"
        self.build_id = bytes(abi.build_id).split(b"\0", 1)[0].decode("ascii")
        self.architecture = "x64"

    @staticmethod
    def _validate_abi(
        package: NativeSilhouettePackage, abi: _NativeAbiInfo, path: Path
    ) -> None:
        actual_version = f"{abi.api_major}.{abi.api_minor}.{abi.api_patch}"
        actual_build = bytes(abi.build_id).split(b"\0", 1)[0].decode("ascii", "strict")
        expected_values = {
            "API version": (package.api_version, actual_version),
            "build ID": (package.build_id, actual_build),
            "architecture": (_PE_MACHINE_AMD64, abi.architecture),
            "canonical view count": (_CANONICAL_VIEW_COUNT, abi.canonical_view_count),
            "mask format": (_MASK_FORMAT_BINARY_L, abi.mask_format),
            "calling convention": (
                _CALLING_CONVENTION_WINDOWS_X64,
                abi.calling_convention,
            ),
            "pointer size": (ctypes.sizeof(ctypes.c_void_p), abi.pointer_size),
            "size_t size": (ctypes.sizeof(ctypes.c_size_t), abi.size_t_size),
            "uint32 size": (ctypes.sizeof(ctypes.c_uint32), abi.uint32_size),
            "uint64 size": (ctypes.sizeof(ctypes.c_uint64), abi.uint64_size),
            "input struct size": (ctypes.sizeof(_NativeInput), abi.input_struct_size),
            "input struct alignment": (_alignment(_NativeInput), abi.input_struct_alignment),
            "output struct size": (ctypes.sizeof(_NativeOutput), abi.output_struct_size),
            "output struct alignment": (_alignment(_NativeOutput), abi.output_struct_alignment),
            "ABI struct size": (ctypes.sizeof(_NativeAbiInfo), abi.abi_struct_size),
            "ABI struct alignment": (_alignment(_NativeAbiInfo), abi.abi_struct_alignment),
        }
        for name, (expected, actual) in expected_values.items():
            if expected != actual:
                raise RuntimeError(
                    f"silhouette {name} mismatch in {path}: expected {expected}, got {actual}"
                )
        if abi.struct_size != ctypes.sizeof(_NativeAbiInfo):
            raise RuntimeError(f"silhouette ABI response struct size mismatch in {path}")
        if abi.capabilities & _CAPABILITY_RAW_MASK_BATCH != _CAPABILITY_RAW_MASK_BATCH:
            raise RuntimeError(f"silhouette raw-mask capability missing from {path}")

    def measure(
        self,
        original: MaskBatch,
        candidate: MaskBatch,
        *,
        empty_distance: int,
        debug: bool = False,
    ) -> RawMaskBatchResult:
        _validate_batch(original, "original")
        _validate_batch(candidate, "candidate")
        if (original.width, original.height, original.view_count) != (
            candidate.width,
            candidate.height,
            candidate.view_count,
        ):
            raise ValueError("silhouette batches have different dimensions or view counts")
        if type(empty_distance) is not int or empty_distance <= 0 or empty_distance > 65535:
            raise ValueError("empty silhouette distance is out of range")

        marshal_start = time.perf_counter_ns()
        original_storage = (ctypes.c_ubyte * len(original.buffer)).from_buffer(original.buffer)
        candidate_storage = (ctypes.c_ubyte * len(candidate.buffer)).from_buffer(candidate.buffer)
        original_pointer = ctypes.cast(
            ctypes.byref(original_storage, original.offset), ctypes.POINTER(ctypes.c_ubyte)
        )
        candidate_pointer = ctypes.cast(
            ctypes.byref(candidate_storage, candidate.offset), ctypes.POINTER(ctypes.c_ubyte)
        )
        views = original.view_count
        intersections = (ctypes.c_uint64 * views)()
        unions = (ctypes.c_uint64 * views)()

        cell_views = original.width * original.height * views
        if debug:
            original_points = (ctypes.c_uint32 * (cell_views * 2))()
            candidate_points = (ctypes.c_uint32 * (cell_views * 2))()
            distances = (ctypes.c_uint32 * (cell_views * 2))()
            original_offsets = (ctypes.c_uint64 * (views + 1))()
            candidate_offsets = (ctypes.c_uint64 * (views + 1))()
            distance_offsets = (ctypes.c_uint64 * (views + 1))()
        else:
            original_points = None
            candidate_points = None
            distances = None
            original_offsets = None
            candidate_offsets = None
            distance_offsets = None

        native_input = _NativeInput(
            ctypes.sizeof(_NativeInput),
            original.width,
            original.height,
            views,
            empty_distance,
            original_pointer,
            original.row_stride,
            original.view_stride,
            candidate_pointer,
            candidate.row_stride,
            candidate.view_stride,
        )
        native_output = _NativeOutput(
            struct_size=ctypes.sizeof(_NativeOutput),
            intersections=intersections,
            unions=unions,
            view_capacity=views,
            original_boundary_xy=original_points,
            original_boundary_capacity=cell_views if debug else 0,
            candidate_boundary_xy=candidate_points,
            candidate_boundary_capacity=cell_views if debug else 0,
            distances_squared=distances,
            distance_capacity=cell_views * 2 if debug else 0,
            original_boundary_offsets=original_offsets,
            candidate_boundary_offsets=candidate_offsets,
            distance_offsets=distance_offsets,
            offset_capacity=views + 1 if debug else 0,
        )
        marshaled_ns = time.perf_counter_ns() - marshal_start

        native_start = time.perf_counter_ns()
        code = self._measure(ctypes.byref(native_input), ctypes.byref(native_output))
        native_ns = time.perf_counter_ns() - native_start
        if code == -4:
            raise ValueError("silhouette masks must be binary 0/255 images")
        if code != 0:
            raise RuntimeError(f"raw-mask silhouette batch failed with native error {code}")

        output_start = time.perf_counter_ns()
        intersection_values = tuple(int(intersections[index]) for index in range(views))
        union_values = tuple(int(unions[index]) for index in range(views))
        if debug:
            assert original_points is not None
            assert candidate_points is not None
            assert distances is not None
            assert original_offsets is not None
            assert candidate_offsets is not None
            assert distance_offsets is not None
            debug_result = RawMaskBatchDebug(
                _split_points(original_points, original_offsets, views),
                _split_points(candidate_points, candidate_offsets, views),
                _split_values(distances, distance_offsets, views),
            )
        else:
            debug_result = None
        output_ns = time.perf_counter_ns() - output_start

        post_start = time.perf_counter_ns()
        worst_iou = 0.0
        for intersection, union in zip(intersection_values, union_values):
            worst_iou = max(worst_iou, 0.0 if union == 0 else 1.0 - intersection / union)
        boundary_p95 = (
            math.sqrt(int(native_output.p95_distance_squared))
            if native_output.distance_count
            else 0.0
        )
        post_ns = time.perf_counter_ns() - post_start

        return RawMaskBatchResult(
            intersection_values,
            union_values,
            int(native_output.distance_count),
            int(native_output.p95_distance_squared),
            worst_iou,
            boundary_p95,
            RawMaskBatchDiagnostics(
                marshaled_ns,
                native_ns,
                int(native_output.boundary_extraction_ns),
                int(native_output.distance_calculation_ns),
                int(native_output.metric_production_ns),
                output_ns,
                post_ns,
                int(native_output.allocation_count),
                int(native_output.allocation_bytes),
                int(native_output.peak_scratch_bytes),
            ),
            debug_result,
        )
