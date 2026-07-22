from __future__ import annotations

from collections.abc import Callable
import math
import threading
import time
from typing import Any

from .silhouette_native import (
    MaskBatch,
    NativeSilhouettePackage,
    RawMaskBatchResult,
    RawMaskSilhouetteKernel,
)


KernelFactory = Callable[[NativeSilhouettePackage], RawMaskSilhouetteKernel]


class SilhouetteBackend:
    """Process-sticky native accelerator with exact-legacy failure semantics."""

    def __init__(
        self,
        package: NativeSilhouettePackage | None,
        *,
        kernel_factory: KernelFactory = RawMaskSilhouetteKernel,
    ) -> None:
        self._package = package
        self._kernel_factory = kernel_factory
        self._kernel: RawMaskSilhouetteKernel | Any | None = None
        self._lock = threading.RLock()
        self._state = "uninitialized" if package is not None else "legacy"
        self._fallback = False
        self._fallback_stage = ""
        self._fallback_reason = ""
        self._api_version = ""
        self._build_id = ""
        self._diagnostics: dict[str, int] = {}
        self._buffers = threading.local()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def native_enabled(self) -> bool:
        with self._lock:
            return self._state == "native"

    def initialize(self) -> bool:
        with self._lock:
            if self._state != "uninitialized":
                return self._state == "native"
            try:
                assert self._package is not None
                kernel = self._kernel_factory(self._package)
                self._kernel = kernel
                self._api_version = str(kernel.api_version)
                self._build_id = str(kernel.build_id)
                self._state = "native"
                return True
            except Exception as exc:
                self._fail_locked("initialize", exc)
                return False

    def candidate_buffer(self) -> bytearray | None:
        return getattr(self._buffers, "candidate", None)

    def set_candidate_buffer(self, value: bytearray) -> None:
        self._buffers.candidate = value

    def record(self, **values: int) -> None:
        with self._lock:
            for name, value in values.items():
                if name == "native_peak_scratch_bytes":
                    self._diagnostics[name] = max(self._diagnostics.get(name, 0), int(value))
                else:
                    self._diagnostics[name] = self._diagnostics.get(name, 0) + int(value)

    def reset_diagnostics(self) -> None:
        with self._lock:
            self._diagnostics.clear()

    def measure(
        self,
        original: MaskBatch,
        candidate: MaskBatch,
        *,
        empty_distance: int,
    ) -> RawMaskBatchResult:
        with self._lock:
            if self._state != "native" or self._kernel is None:
                raise RuntimeError("native silhouette backend is not available")
            kernel = self._kernel
        total_start = time.perf_counter_ns()
        try:
            result = kernel.measure(original, candidate, empty_distance=empty_distance)
            self._validate_result(result, original, empty_distance)
        except Exception as exc:
            with self._lock:
                self._fail_locked("measure", exc)
                self._diagnostics["calls"] = self._diagnostics.get("calls", 0) + 1
                self._diagnostics["silhouette_total_ns"] = (
                    self._diagnostics.get("silhouette_total_ns", 0)
                    + time.perf_counter_ns()
                    - total_start
                )
            raise RuntimeError(f"native silhouette measure failed: {exc}") from exc
        diagnostics = result.diagnostics
        self.record(
            calls=1,
            input_marshaling_ns=diagnostics.input_marshaling_ns,
            native_call_ns=diagnostics.native_call_ns,
            boundary_extraction_ns=diagnostics.boundary_extraction_ns,
            distance_calculation_ns=diagnostics.distance_calculation_ns,
            metric_production_ns=diagnostics.metric_production_ns,
            output_marshaling_ns=diagnostics.output_marshaling_ns,
            caller_postprocessing_ns=diagnostics.caller_postprocessing_ns,
            native_allocation_count=diagnostics.native_allocation_count,
            native_allocation_bytes=diagnostics.native_allocation_bytes,
            native_peak_scratch_bytes=diagnostics.native_peak_scratch_bytes,
            silhouette_total_ns=time.perf_counter_ns() - total_start,
        )
        return result

    @staticmethod
    def _validate_result(
        result: RawMaskBatchResult, original: MaskBatch, empty_distance: int
    ) -> None:
        views = original.view_count
        cells = original.width * original.height
        if len(result.intersections) != views or len(result.unions) != views:
            raise RuntimeError("native silhouette output has the wrong view count")
        if any(
            intersection < 0 or union < intersection or union > cells
            for intersection, union in zip(result.intersections, result.unions)
        ):
            raise RuntimeError("native silhouette output has invalid intersection/union counts")
        maximum_squared = max(
            (original.width - 1) ** 2 + (original.height - 1) ** 2,
            empty_distance**2,
        )
        if (
            result.distance_count < 0
            or result.distance_count > cells * views * 2
            or result.p95_distance_squared < 0
            or result.p95_distance_squared > maximum_squared
            or not math.isfinite(result.worst_iou_loss)
            or not 0.0 <= result.worst_iou_loss <= 1.0
            or not math.isfinite(result.boundary_p95_px)
            or result.boundary_p95_px < 0.0
        ):
            raise RuntimeError("native silhouette output violates the result contract")

    def fail(self, stage: str, error: Exception | str) -> None:
        with self._lock:
            self._fail_locked(stage, error)

    def _fail_locked(self, stage: str, error: Exception | str) -> None:
        if self._state == "legacy" and self._fallback:
            return
        self._kernel = None
        self._state = "legacy"
        self._fallback = True
        self._fallback_stage = stage
        self._fallback_reason = str(error).replace("\r", " ").replace("\n", " ")[:1000]

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            result: dict[str, object] = {
                "backend": self._state,
                "api_version": self._api_version,
                "build_id": self._build_id,
                "fallback": self._fallback,
                "fallback_stage": self._fallback_stage,
                "fallback_reason": self._fallback_reason,
            }
            for name in (
                "calls",
                "mask_preparation_ns",
                "input_marshaling_ns",
                "native_call_ns",
                "boundary_extraction_ns",
                "distance_calculation_ns",
                "metric_production_ns",
                "output_marshaling_ns",
                "caller_postprocessing_ns",
                "native_allocation_count",
                "native_allocation_bytes",
                "native_peak_scratch_bytes",
                "python_buffer_growth_count",
                "python_buffer_growth_bytes",
                "silhouette_total_ns",
            ):
                result[name] = self._diagnostics.get(name, 0)
            return result


_BACKEND_LOCK = threading.RLock()
_BACKEND = SilhouetteBackend(None)


def configure_silhouette_backend(
    package: NativeSilhouettePackage,
    *,
    kernel_factory: KernelFactory = RawMaskSilhouetteKernel,
) -> SilhouetteBackend:
    global _BACKEND
    backend = SilhouetteBackend(package, kernel_factory=kernel_factory)
    with _BACKEND_LOCK:
        _BACKEND = backend
    return backend


def configure_legacy_silhouette_backend(stage: str, error: Exception | str) -> SilhouetteBackend:
    global _BACKEND
    backend = SilhouetteBackend(None)
    backend.fail(stage, error)
    with _BACKEND_LOCK:
        _BACKEND = backend
    return backend


def reset_silhouette_backend() -> None:
    global _BACKEND
    with _BACKEND_LOCK:
        _BACKEND = SilhouetteBackend(None)


def current_silhouette_backend() -> SilhouetteBackend:
    with _BACKEND_LOCK:
        return _BACKEND
