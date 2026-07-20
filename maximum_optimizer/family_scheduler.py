from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class FamilyWorkItem(Generic[T]):
    index: int
    estimate: int
    value: T

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("family work index must be a non-negative integer")
        if type(self.estimate) is not int or self.estimate < 0:
            raise ValueError("family work estimate must be a non-negative integer")


@dataclass(frozen=True)
class FamilySchedulerUpdate:
    active: int
    completed: int
    total: int
    capacity: int
    memory_throttled: bool

    def __post_init__(self) -> None:
        for name in ("active", "completed", "total", "capacity"):
            value = getattr(self, name)
            minimum = 1 if name == "capacity" else 0
            if type(value) is not int or value < minimum:
                raise ValueError(f"scheduler {name} is invalid")
        if self.active + self.completed > self.total:
            raise ValueError("scheduler counts exceed total work")
        if type(self.memory_throttled) is not bool:
            raise TypeError("scheduler memory_throttled must be bool")


def run_family_jobs(
    items: Iterable[FamilyWorkItem[T]],
    *,
    max_workers: int,
    worker: Callable[[T], R],
    cancel_event: threading.Event | None = None,
    memory_limit: Callable[[], int] | None = None,
    on_update: Callable[[FamilySchedulerUpdate], object] | None = None,
) -> tuple[R, ...]:
    if type(max_workers) is not int or max_workers < 1:
        raise ValueError("max_workers must be a positive integer")
    if not callable(worker):
        raise TypeError("worker must be callable")
    if memory_limit is not None and not callable(memory_limit):
        raise TypeError("memory_limit must be callable")
    if on_update is not None and not callable(on_update):
        raise TypeError("on_update must be callable")

    work = tuple(items)
    if any(not isinstance(item, FamilyWorkItem) for item in work):
        raise TypeError("items must contain FamilyWorkItem values")
    indexes = tuple(item.index for item in work)
    if len(set(indexes)) != len(indexes):
        raise ValueError("duplicate family work index")

    ordered = deque(sorted(work, key=lambda item: (-item.estimate, item.index)))
    results: dict[int, R] = {}
    pending: dict[Future[R], FamilyWorkItem[T]] = {}
    cancel = cancel_event or threading.Event()

    def capacity() -> int:
        if memory_limit is None:
            return max_workers
        current = memory_limit()
        if type(current) is not int or current < 1:
            raise ValueError("memory limit must be a positive integer")
        return min(max_workers, current)

    def publish(current_capacity: int) -> None:
        if on_update is not None:
            on_update(FamilySchedulerUpdate(
                active=len(pending),
                completed=len(results),
                total=len(work),
                capacity=current_capacity,
                memory_throttled=current_capacity < max_workers,
            ))

    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="maximum-family",
    ) as pool:
        while ordered or pending:
            current_capacity = capacity()
            if cancel.is_set():
                ordered.clear()
                for future in pending:
                    future.cancel()
            else:
                while ordered and len(pending) < current_capacity:
                    item = ordered.popleft()
                    pending[pool.submit(worker, item.value)] = item

            publish(current_capacity)
            if not pending:
                break

            done, _ = wait(
                tuple(pending),
                timeout=0.25,
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                item = pending.pop(future)
                if not future.cancelled():
                    results[item.index] = future.result()
            if done:
                publish(current_capacity)

    return tuple(results[index] for index in sorted(results))
