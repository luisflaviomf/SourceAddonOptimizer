"""How many worker processes a stage may start: the machine's logical CPUs but two, as long as each can
have its share of the memory that is free right now.

The stages used to stop at 8 (half the CPUs, at most 8), chosen for a 16 GB machine; on a 20-thread one
that left most of it idle. The result of a stage never depends on the count: every model and every texture
is worked on alone.
"""
from __future__ import annotations

import os


def free_memory_gb() -> float | None:
    """Free physical memory, or None when it cannot be asked (not Windows)."""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return st.ullAvailPhys / 2 ** 30
    except Exception:  # noqa: BLE001 - not Windows or no ctypes: CPU count only
        pass
    return None


def auto_jobs(per_process_gb: float, minimum: int = 1) -> int:
    """Every logical CPU but two, while each process can have `per_process_gb` of the free memory; never
    fewer than `minimum`."""
    cpus = max(1, (os.cpu_count() or 2) - 2)
    free = free_memory_gb()
    n = cpus if free is None else min(cpus, max(1, int(free / per_process_gb)))
    return max(minimum, n)
