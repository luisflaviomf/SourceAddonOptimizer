from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock


class RoundPlanarPriorityExperimentTests(unittest.TestCase):
    def test_builder_prepares_wheel_and_steering_ablation_arms_without_execution(self):
        from benchmarks.lvs_models.run_round_planar_priority_v1 import build_experiment_arms

        with mock.patch("subprocess.run") as run:
            arms = build_experiment_arms(
                repo=Path("C:/repo"),
                source_root=Path("C:/source"),
                output_root=Path("C:/runs"),
                blender_exe=Path("C:/tools/blender.exe"),
                studiomdl_exe=Path("C:/tools/studiomdl.exe"),
                python_exe=Path("C:/tools/python.exe"),
            )

        run.assert_not_called()
        self.assertEqual(
            tuple((arm.subject, arm.name) for arm in arms),
            (
                ("wheel", "b050"),
                ("wheel", "planar-only"),
                ("wheel", "round-planar-priority"),
                ("steering-wheel", "b050"),
                ("steering-wheel", "planar-only"),
                ("steering-wheel", "round-planar-priority"),
            ),
        )
        by_name = {(arm.subject, arm.name): arm for arm in arms}
        self.assertEqual(by_name[("wheel", "b050")].candidate["strategy"], "blender-adaptive-v1")
        self.assertEqual(by_name[("wheel", "b050")].candidate["ratio"], 0.5)
        self.assertEqual(
            by_name[("wheel", "planar-only")].candidate,
            {
                "candidate_id": "wheel-round-planar-only",
                "engine": "blender",
                "ratio": 1.0,
                "target_error": 0.0,
                "update_vertices": True,
                "region_overrides": [],
                "strategy": "round-planar-priority-v1",
                "transfer": "blender-native-v1",
            },
        )
        self.assertEqual(
            by_name[("steering-wheel", "round-planar-priority")].candidate["ratio"],
            0.35,
        )
        self.assertTrue(
            str(by_name[("wheel", "b050")].source).replace("\\", "/").endswith(
                "diggercars/pontiac_transam3/wheel"
            )
        )
        self.assertTrue(
            str(by_name[("steering-wheel", "b050")].source).replace("\\", "/").endswith(
                "diggercars/dodge_charger/charger"
            )
        )
        self.assertEqual(len({arm.workspace for arm in arms}), 6)
        for arm in arms:
            self.assertIn(str(arm.workspace), arm.optimize_command)
            self.assertIn(str(arm.candidate_json), arm.optimize_command)
            self.assertIn(str(arm.workspace / "compiled"), arm.compile_command)

    def test_planar_only_round_candidate_does_not_short_circuit_to_exact_source(self):
        import batch_optimize_maximum as maximum

        candidate = maximum.CandidateConfig(
            "round-planar-only",
            "blender",
            1.0,
            0.0,
            True,
            (),
            strategy="round-planar-priority-v1",
            transfer="blender-native-v1",
        )

        self.assertFalse(maximum.should_preserve_exact(candidate, (1.0,)))

    def test_execution_requires_exclusive_lock_and_stages_immutable_arm(self):
        from benchmarks.lvs_models.run_round_planar_priority_v1 import (
            ExperimentArm,
            execute_experiment,
            exclusive_blender_lock,
        )

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "fixture-source"
            source.mkdir()
            (source / "wheel.qc").write_text("fixture", encoding="utf-8")
            workspace = root / "runs" / "wheel" / "b050" / "source"
            payload = {
                "candidate_id": "wheel-b050",
                "engine": "blender",
                "ratio": 0.5,
                "target_error": 0.0,
                "update_vertices": True,
                "region_overrides": [],
                "strategy": "blender-adaptive-v1",
                "transfer": "blender-native-v1",
            }
            arm = ExperimentArm(
                "wheel",
                "b050",
                source,
                workspace,
                workspace / "candidate.json",
                payload,
                ("blender", "optimize"),
                ("python", "compile"),
            )
            lock = root / "blender.lock"
            calls = []

            with exclusive_blender_lock(lock):
                with self.assertRaisesRegex(RuntimeError, "busy"):
                    with exclusive_blender_lock(lock):
                        pass
            self.assertTrue(lock.exists())

            execute_experiment((arm,), lock_path=lock, runner=lambda command: calls.append(command))

            self.assertEqual(calls, [arm.optimize_command, arm.compile_command])
            self.assertEqual((workspace / "wheel.qc").read_text(encoding="utf-8"), "fixture")
            self.assertEqual(
                json.loads((workspace / "candidate.json").read_text(encoding="utf-8")),
                payload,
            )
            self.assertTrue(lock.exists())

            with self.assertRaisesRegex(FileExistsError, "workspace"):
                execute_experiment((arm,), lock_path=lock, runner=lambda command: None)

    def test_lock_release_never_unlinks_persistent_lock_file(self):
        from benchmarks.lvs_models.run_round_planar_priority_v1 import exclusive_blender_lock

        with tempfile.TemporaryDirectory() as raw:
            lock = Path(raw) / "blender.lock"
            with exclusive_blender_lock(lock):
                self.assertTrue(lock.exists())

            self.assertTrue(lock.exists())
            with exclusive_blender_lock(lock):
                self.assertTrue(lock.exists())
            self.assertTrue(lock.exists())


if __name__ == "__main__":
    unittest.main()
