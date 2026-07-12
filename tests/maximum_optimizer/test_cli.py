from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import build_optimized_addon
from maximum_optimizer.orchestrator import run_maximum_from_existing_args
from worker import worker_main


class MaximumCliTests(unittest.TestCase):
    def test_maximum_overwrite_selection_never_deletes_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "existing"
            output.mkdir()
            sentinel = output / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            self.assertEqual(
                build_optimized_addon._choose_maximum_dest_dir(output, overwrite=True),
                output,
            )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_parser_accepts_maximum_and_routes_only_that_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addon = root / "addon"
            (addon / "models").mkdir(parents=True)
            for name in (
                "batch_decompile_organize.py", "batch_optimize_qc.py",
                "batch_compile_opt_qc.py", "batch_optimize_selective_policy.py",
                "batch_optimize_round_parts_policy.py", "batch_optimize_maximum.py",
                "render_previews.py",
            ):
                (root / name).write_text("", encoding="utf-8")
            with (
                patch.object(build_optimized_addon, "_runtime_root", return_value=root),
                patch.object(build_optimized_addon, "_run_single_addon", return_value=0) as run_one,
            ):
                rc = build_optimized_addon.main([str(addon), "--optimizer-mode", "maximum"])
            self.assertEqual(rc, 0)
            self.assertEqual(run_one.call_args.args[0].optimizer_mode, "maximum")

    def test_parser_keeps_normal_and_fidelity_defaults_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addon = root / "addon"
            (addon / "models").mkdir(parents=True)
            for name in (
                "batch_decompile_organize.py", "batch_optimize_qc.py",
                "batch_compile_opt_qc.py", "batch_optimize_selective_policy.py",
                "batch_optimize_round_parts_policy.py",
            ):
                (root / name).write_text("", encoding="utf-8")
            with (
                patch.object(build_optimized_addon, "_runtime_root", return_value=root),
                patch.object(build_optimized_addon, "_run_single_addon", return_value=0) as run_one,
            ):
                self.assertEqual(build_optimized_addon.main([str(addon)]), 0)
                self.assertEqual(run_one.call_args.args[0].optimizer_mode, "normal")
                self.assertEqual(
                    build_optimized_addon.main([str(addon), "--optimizer-mode", "fidelity"]), 0
                )
                self.assertEqual(run_one.call_args.args[0].optimizer_mode, "fidelity")

    def test_worker_forwards_maximum_arguments_untouched(self):
        argv = ["addon", "--optimizer-mode", "maximum", "--maximum-resume"]
        with patch.object(worker_main.build_optimized_addon, "main", return_value=7) as target:
            self.assertEqual(worker_main.main(argv), 7)
        target.assert_called_once_with(argv)

    def test_invalid_optimizer_mode_is_argparse_exit_two(self):
        with self.assertRaises(SystemExit) as raised:
            build_optimized_addon.main(["missing", "--optimizer-mode", "invalid"])
        self.assertEqual(raised.exception.code, 2)

    def test_production_uncalibrated_profile_exits_two_before_decompile_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addon = root / "addon"
            (addon / "models").mkdir(parents=True)
            output = root / "output"
            work = root / "work"
            args = Namespace(
                maximum_profile=Path(__file__).parents[2]
                / "maximum_optimizer" / "profiles" / "maximum-experimental-v1.json",
                blender=None,
                studiomdl=None,
            )
            rc = run_maximum_from_existing_args(
                args,
                repo_root=Path(__file__).parents[2],
                addon_path=addon,
                out_addon_dir=output,
                work_dir=work,
            )
            self.assertEqual(rc, 2)
            self.assertFalse(output.exists())
            self.assertFalse(work.exists())


if __name__ == "__main__":
    unittest.main()
