from __future__ import annotations

import ctypes
from dataclasses import dataclass
import math
from pathlib import Path
import time


_CELL_LIMIT = 1 << 28


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
    def __init__(self, dll_path: Path) -> None:
        path = Path(dll_path).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError(f"experimental silhouette DLL not built: {path}")
        try:
            self._dll = ctypes.WinDLL(str(path), winmode=0)
        except OSError as exc:
            raise RuntimeError(f"cannot load experimental silhouette DLL at {path}: {exc}") from exc
        try:
            function = self._dll.maximum_silhouette_metrics_raw_batch_v1
        except AttributeError as exc:
            raise RuntimeError(f"experimental silhouette export missing from {path}") from exc
        function.argtypes = [ctypes.POINTER(_NativeInput), ctypes.POINTER(_NativeOutput)]
        function.restype = ctypes.c_int
        self._measure = function

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
