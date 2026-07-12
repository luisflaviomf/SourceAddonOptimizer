from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.lvs_models import build_adaptive_ratio_sweep_v1 as evidence_builder
from benchmarks.lvs_models.run_adaptive_ratio_sweep import build_commands


class AdaptiveRatioSweepHarnessTests(unittest.TestCase):
    def test_optimizer_and_compiler_receive_workspace_directory_not_qc_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = root / "candidate"
            workspace.mkdir()
            (workspace / "car.qc").write_text('$modelname "cars/car.mdl"\n', encoding="utf-8")
            candidate = workspace / "candidate.json"
            candidate.write_text("{}", encoding="utf-8")

            optimize, compile_command = build_commands(
                repo=root,
                workspace=workspace,
                candidate_json=candidate,
                blender_exe=root / "blender.exe",
                studiomdl_exe=root / "studiomdl.exe",
                python_exe=root / "python.exe",
            )

            self.assertEqual(optimize[-3], str(workspace))
            self.assertEqual(compile_command[2], str(workspace))
            self.assertNotIn(str(workspace / "car.qc"), optimize)
            self.assertNotIn(str(workspace / "car.qc"), compile_command)

    def test_evidence_builder_fails_when_frozen_execution_blob_is_unavailable(self) -> None:
        with patch.object(evidence_builder.subprocess, "check_output", return_value="0" * 40):
            with self.assertRaisesRegex(SystemExit, "snapshot blob drift"):
                evidence_builder._validate_frozen_execution_snapshot()


if __name__ == "__main__":
    unittest.main()
