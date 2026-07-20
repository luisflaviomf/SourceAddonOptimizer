from __future__ import annotations

import ctypes
import math
import os
from dataclasses import dataclass


GIB = 1024 ** 3
FAMILY_MEMORY_BYTES = 768 * 1024 ** 2
UNKNOWN_MEMORY_AUTO_CAP = 4


@dataclass(frozen=True)
class MemorySnapshot:
    total_bytes: int
    available_bytes: int

    def __post_init__(self) -> None:
        if (
            type(self.total_bytes) is not int
            or type(self.available_bytes) is not int
            or self.total_bytes <= 0
            or self.available_bytes < 0
            or self.available_bytes > self.total_bytes
        ):
            raise ValueError("memory snapshot values are invalid")


@dataclass(frozen=True)
class MaximumParallelismPlan:
    requested_jobs: int
    logical_processors: int
    cpu_target_jobs: int
    effective_jobs: int
    memory_limit_jobs: int
    memory_throttled: bool
    blender_threads: int

    def __post_init__(self) -> None:
        for name in (
            "requested_jobs",
            "logical_processors",
            "cpu_target_jobs",
            "effective_jobs",
            "memory_limit_jobs",
            "blender_threads",
        ):
            value = getattr(self, name)
            minimum = 0 if name in {"requested_jobs", "blender_threads"} else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} is invalid")
        if type(self.memory_throttled) is not bool:
            raise TypeError("memory_throttled must be bool")
        if self.cpu_target_jobs > self.logical_processors:
            raise ValueError("CPU target exceeds logical processor count")
        if self.effective_jobs > self.cpu_target_jobs:
            raise ValueError("effective jobs exceed CPU target")
        if self.blender_threads not in {0, 1}:
            raise ValueError("blender_threads must be zero or one")
        if (self.effective_jobs > 1) != (self.blender_threads == 1):
            raise ValueError("Blender thread policy differs from effective jobs")

    @classmethod
    def serial(cls) -> MaximumParallelismPlan:
        return cls(1, 1, 1, 1, 1, False, 0)


def _memory_limit(memory: MemorySnapshot) -> int:
    reserve = max(2 * GIB, math.ceil(memory.total_bytes * 0.10))
    usable = max(0, memory.available_bytes - reserve)
    return max(1, usable // FAMILY_MEMORY_BYTES)


def resolve_maximum_parallelism(
    requested_jobs: int,
    *,
    logical_processors: int | None = None,
    memory: MemorySnapshot | None = None,
) -> MaximumParallelismPlan:
    if type(requested_jobs) is not int:
        raise TypeError("maximum jobs must be an integer")
    if requested_jobs < 0:
        raise ValueError("maximum jobs must be zero or a positive integer")
    detected_processors = os.cpu_count() if logical_processors is None else logical_processors
    if type(detected_processors) is not int or detected_processors < 1:
        detected_processors = 1
    logical = detected_processors
    cpu_target = (
        max(1, logical // 2)
        if requested_jobs == 0
        else min(requested_jobs, logical)
    )
    memory_limit = (
        _memory_limit(memory)
        if memory is not None
        else (UNKNOWN_MEMORY_AUTO_CAP if requested_jobs == 0 else logical)
    )
    effective = max(1, min(cpu_target, memory_limit))
    return MaximumParallelismPlan(
        requested_jobs=requested_jobs,
        logical_processors=logical,
        cpu_target_jobs=cpu_target,
        effective_jobs=effective,
        memory_limit_jobs=memory_limit,
        memory_throttled=effective < cpu_target,
        blender_threads=1 if effective > 1 else 0,
    )


def current_memory_job_limit(
    plan: MaximumParallelismPlan,
    memory: MemorySnapshot | None,
) -> int:
    if not isinstance(plan, MaximumParallelismPlan):
        raise TypeError("plan must be a MaximumParallelismPlan")
    if memory is None:
        return plan.effective_jobs
    return max(1, min(plan.effective_jobs, _memory_limit(memory)))


def _windows_memory_snapshot() -> MemorySnapshot | None:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = (
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        )

    try:
        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        query = ctypes.windll.kernel32.GlobalMemoryStatusEx
        query.argtypes = (ctypes.POINTER(MemoryStatusEx),)
        query.restype = ctypes.c_int
        if not query(ctypes.byref(status)):
            return None
        return MemorySnapshot(int(status.ullTotalPhys), int(status.ullAvailPhys))
    except (AttributeError, OSError, ValueError):
        return None


def _posix_memory_snapshot() -> MemorySnapshot | None:
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        return MemorySnapshot(
            total_pages * page_size,
            available_pages * page_size,
        )
    except (AttributeError, OSError, ValueError):
        return None


def detect_memory_snapshot() -> MemorySnapshot | None:
    return _windows_memory_snapshot() if os.name == "nt" else _posix_memory_snapshot()
