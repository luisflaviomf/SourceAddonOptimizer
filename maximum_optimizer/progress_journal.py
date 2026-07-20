from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any


class DurableProgressJournal:
    def __init__(
        self,
        writer: Callable[[Any], object],
        *,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(writer):
            raise TypeError("journal writer must be callable")
        if (
            isinstance(min_interval, bool)
            or type(min_interval) not in (int, float)
            or not math.isfinite(float(min_interval))
            or min_interval < 0
        ):
            raise ValueError("journal minimum interval must be finite and non-negative")
        if not callable(clock):
            raise TypeError("journal clock must be callable")
        self._writer = writer
        self._min_interval = float(min_interval)
        self._clock = clock
        self._lock = threading.RLock()
        self._last_write_at: float | None = None

    def publish(
        self,
        snapshot_factory: Callable[[], Any],
        *,
        force: bool = False,
    ) -> bool:
        if not callable(snapshot_factory):
            raise TypeError("journal snapshot factory must be callable")
        if type(force) is not bool:
            raise TypeError("journal force flag must be bool")
        with self._lock:
            now = self._clock()
            if (
                isinstance(now, bool)
                or type(now) not in (int, float)
                or not math.isfinite(float(now))
            ):
                raise ValueError("journal clock returned an invalid value")
            current = float(now)
            if (
                not force
                and self._last_write_at is not None
                and current - self._last_write_at < self._min_interval
            ):
                return False
            payload = snapshot_factory()
            self._writer(payload)
            self._last_write_at = current
            return True
