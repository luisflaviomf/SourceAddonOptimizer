from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import batch_optimize_parallel


class _FakeProcess:
    stdout = ()

    def wait(self):
        return 0


class _FakeThread:
    created: list["_FakeThread"] = []

    def __init__(self, *args, **kwargs):
        self.daemon = bool(kwargs.get("daemon", False))
        self.join_timeouts: list[float | None] = []
        self.created.append(self)

    def start(self):
        return None

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)


class ParallelOptimizerShutdownTests(unittest.TestCase):
    def test_stdout_readers_are_non_daemon_and_fully_joined(self) -> None:
        _FakeThread.created.clear()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "model.qc").write_text("$body body model.smd\n", encoding="utf-8")
            blender = root / "blender.exe"
            script = root / "optimize.py"
            blender.touch()
            script.touch()
            with (
                patch.object(batch_optimize_parallel.subprocess, "Popen", return_value=_FakeProcess()),
                patch.object(batch_optimize_parallel.threading, "Thread", _FakeThread),
            ):
                result = batch_optimize_parallel.main(
                    [
                        str(root),
                        "--blender", str(blender),
                        "--jobs", "1",
                        "--optimize-script", str(script),
                    ]
                )

        self.assertEqual(result, 0)
        self.assertEqual(len(_FakeThread.created), 1)
        self.assertFalse(_FakeThread.created[0].daemon)
        self.assertEqual(_FakeThread.created[0].join_timeouts, [None])


if __name__ == "__main__":
    unittest.main()
