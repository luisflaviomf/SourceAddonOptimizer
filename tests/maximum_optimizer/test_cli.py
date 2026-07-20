from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
import json
from pathlib import Path
from unittest.mock import patch

import build_optimized_addon
from maximum_optimizer import processes
from maximum_optimizer.orchestrator import (
    MaximumConfigError,
    _promote_verified_tree,
    _tree_manifest,
    run_maximum_from_existing_args,
)
from worker import worker_main


class MaximumCliTests(unittest.TestCase):
    def test_frozen_python_children_are_routed_through_the_worker_dispatch(self):
        executable = Path(r"C:\tools\SourceAddonOptimizerWorker.exe")
        command = (
            executable,
            Path(r"C:\tools\_internal\batch_decompile_organize.py"),
            "addon",
            "--force",
        )
        with (
            patch.object(processes.sys, "frozen", True, create=True),
            patch.object(processes.sys, "executable", str(executable)),
        ):
            routed = processes._prepare_command_for_runtime(command)
        self.assertEqual(
            routed,
            (
                str(executable.resolve()),
                "__worker_script__",
                "batch_decompile_organize.py",
                "addon",
                "--force",
            ),
        )

    def test_worker_dispatches_only_known_internal_scripts(self):
        with patch.object(
            worker_main.batch_decompile_organize, "main", return_value=7
        ) as target:
            self.assertEqual(
                worker_main.main([
                    "__worker_script__", "batch_decompile_organize.py", "addon", "--force"
                ]),
                7,
            )
        self.assertEqual(
            target.call_args.args,
            (),
        )
        self.assertEqual(
            worker_main.main(["__worker_script__", "not-authorized.py"]),
            2,
        )

    def test_maximum_work_selection_never_deletes_existing_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "existing-work"
            work.mkdir()
            sentinel = work / "cache.bin"
            sentinel.write_bytes(b"cache")
            selected = build_optimized_addon._choose_maximum_work_dir(
                work, overwrite=True, resume=False
            )
            self.assertEqual(selected, work)
            self.assertEqual(sentinel.read_bytes(), b"cache")

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
            self.assertEqual(run_one.call_args.args[0].maximum_jobs, 0)

    def test_parser_accepts_explicit_maximum_jobs(self):
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
                rc = build_optimized_addon.main([
                    str(addon), "--optimizer-mode", "maximum", "--maximum-jobs", "7",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(run_one.call_args.args[0].maximum_jobs, 7)

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

    def test_normal_batch_overwrite_does_not_delete_later_outputs_in_prepass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            container = root / "addons"
            for unit in ("one", "two"):
                (container / unit / "models").mkdir(parents=True)
            first_output = container / "one_OPT"
            second_output = container / "two_OPT"
            first_output.mkdir()
            second_output.mkdir()
            (first_output / "sentinel.txt").write_text("first", encoding="utf-8")
            second_sentinel = second_output / "sentinel.txt"
            second_sentinel.write_text("second", encoding="utf-8")
            for name in (
                "batch_decompile_organize.py", "batch_optimize_qc.py",
                "batch_compile_opt_qc.py", "batch_optimize_selective_policy.py",
                "batch_optimize_round_parts_policy.py",
            ):
                (root / name).write_text("", encoding="utf-8")
            observed = []
            def run_one(*_args, **_kwargs):
                observed.append(second_sentinel.exists())
                return 1
            with (
                patch.object(build_optimized_addon, "_runtime_root", return_value=root),
                patch.object(build_optimized_addon, "_run_single_addon", side_effect=run_one),
            ):
                rc = build_optimized_addon.main([
                    str(container), "--optimizer-mode", "normal", "--suffix", "_OPT",
                    "--overwrite", "--work", str(root / "work-normal"),
                ])
            self.assertEqual(rc, 1)
            self.assertTrue(observed[0], "later output was deleted before the first unit ran")

    def test_maximum_cli_preserves_lexical_path_until_reparse_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addon = root / "addon-link"
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
                patch("maximum_optimizer.orchestrator._is_reparse", side_effect=lambda path: Path(path) == addon),
                patch.object(build_optimized_addon, "_run_single_addon") as run_one,
            ):
                rc = build_optimized_addon.main([str(addon), "--optimizer-mode", "maximum"])
            self.assertEqual(rc, 2)
            run_one.assert_not_called()

    def test_maximum_single_recovers_canonical_output_before_chooser(self):
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
            canonical = root / "addon_OPT"
            order = []
            with (
                patch.object(build_optimized_addon, "_runtime_root", return_value=root),
                patch("maximum_optimizer.orchestrator._recover_output_transaction", side_effect=lambda path: order.append(("recover", Path(path))) or True),
                patch.object(build_optimized_addon, "_choose_maximum_dest_dir", side_effect=lambda path, **kwargs: order.append(("choose", Path(path), kwargs["overwrite"])) or Path(path)),
                patch.object(build_optimized_addon, "_run_single_addon", side_effect=lambda *args, **kwargs: order.append(("run", kwargs["out_addon_dir"])) or 0),
            ):
                rc = build_optimized_addon.main([
                    str(addon), "--optimizer-mode", "maximum", "--suffix", "_OPT",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(order[0], ("recover", canonical))
            self.assertEqual(order[1], ("choose", canonical, True))
            self.assertEqual(order[2], ("run", canonical))

    def test_maximum_single_restores_real_canonical_transaction_without_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addon = root / "addon"
            (addon / "models").mkdir(parents=True)
            canonical = root / "addon_OPT"
            staging = root / (".addon_OPT.maximum-staging-" + "9" * 32)
            canonical.mkdir()
            staging.mkdir()
            (canonical / "old.bin").write_bytes(b"old")
            (staging / "new.bin").write_bytes(b"new")
            class Crash(BaseException):
                pass
            with self.assertRaises(Crash):
                _promote_verified_tree(
                    staging, canonical, _tree_manifest(staging),
                    crash_hook=lambda phase: (_ for _ in ()).throw(Crash())
                    if phase == "backup_renamed" else None,
                )
            for name in (
                "batch_decompile_organize.py", "batch_optimize_qc.py",
                "batch_compile_opt_qc.py", "batch_optimize_selective_policy.py",
                "batch_optimize_round_parts_policy.py", "batch_optimize_maximum.py",
                "render_previews.py",
            ):
                (root / name).write_text("", encoding="utf-8")
            captured = []
            with (
                patch.object(build_optimized_addon, "_runtime_root", return_value=root),
                patch.object(build_optimized_addon, "_run_single_addon", side_effect=lambda args, **kwargs: captured.append((kwargs["out_addon_dir"], args.overwrite)) or 0),
            ):
                rc = build_optimized_addon.main([
                    str(addon), "--optimizer-mode", "maximum", "--suffix", "_OPT",
                ])
            self.assertEqual(rc, 0)
            self.assertEqual(captured, [(canonical, True)])
            self.assertEqual((canonical / "old.bin").read_bytes(), b"old")
            self.assertFalse(tuple(root.glob("addon_OPT_*")))

    def test_maximum_canonical_orphan_failure_precedes_chooser_and_work(self):
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
            work = root / "work"
            with (
                patch.object(build_optimized_addon, "_runtime_root", return_value=root),
                patch("maximum_optimizer.orchestrator._recover_output_transaction", side_effect=ValueError("orphan without marker")),
                patch.object(build_optimized_addon, "_choose_maximum_dest_dir") as chooser,
                patch.object(build_optimized_addon, "_run_single_addon") as run_one,
            ):
                rc = build_optimized_addon.main([
                    str(addon), "--optimizer-mode", "maximum", "--suffix", "_OPT", "--work", str(work),
                ])
            self.assertEqual(rc, 2)
            chooser.assert_not_called()
            run_one.assert_not_called()
            self.assertFalse(work.exists())

    def test_maximum_batch_recovers_every_canonical_output_before_any_chooser(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            container = root / "addons"
            for unit in ("one", "two"):
                (container / unit / "models").mkdir(parents=True)
            for name in (
                "batch_decompile_organize.py", "batch_optimize_qc.py",
                "batch_compile_opt_qc.py", "batch_optimize_selective_policy.py",
                "batch_optimize_round_parts_policy.py", "batch_optimize_maximum.py",
                "render_previews.py",
            ):
                (root / name).write_text("", encoding="utf-8")
            order = []
            with (
                patch.object(build_optimized_addon, "_runtime_root", return_value=root),
                patch("maximum_optimizer.orchestrator._recover_output_transaction", side_effect=lambda path: order.append(("recover", Path(path).name)) or False),
                patch.object(build_optimized_addon, "_choose_maximum_dest_dir", side_effect=lambda path, **kwargs: order.append(("choose", Path(path).name)) or Path(path)),
                patch.object(build_optimized_addon, "_run_single_addon", return_value=0),
            ):
                rc = build_optimized_addon.main([
                    str(container), "--optimizer-mode", "maximum", "--suffix", "_OPT", "--work", str(root / "work"),
                ])
            self.assertEqual(rc, 0)
            self.assertEqual([item[0] for item in order], ["recover", "recover", "choose", "choose"])

    def test_maximum_batch_preflights_all_units_before_creating_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            container = root / "addons"
            for unit in ("one", "two"):
                (container / unit / "models").mkdir(parents=True)
            for name in (
                "batch_decompile_organize.py", "batch_optimize_qc.py",
                "batch_compile_opt_qc.py", "batch_optimize_selective_policy.py",
                "batch_optimize_round_parts_policy.py", "batch_optimize_maximum.py",
                "render_previews.py",
            ):
                (root / name).write_text("", encoding="utf-8")
            work = container / "unsafe-work"
            with (
                patch.object(build_optimized_addon, "_runtime_root", return_value=root),
                patch.object(build_optimized_addon, "_run_single_addon") as run_one,
            ):
                rc = build_optimized_addon.main([
                    str(container), "--optimizer-mode", "maximum", "--work", str(work),
                ])
            self.assertEqual(rc, 2)
            run_one.assert_not_called()
            self.assertFalse(work.exists())

    def test_maximum_batch_rejects_cross_unit_output_source_overlap_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            container = root / "addons"
            for unit in ("one", "one_OPT"):
                (container / unit / "models").mkdir(parents=True)
            for name in (
                "batch_decompile_organize.py", "batch_optimize_qc.py",
                "batch_compile_opt_qc.py", "batch_optimize_selective_policy.py",
                "batch_optimize_round_parts_policy.py", "batch_optimize_maximum.py",
                "render_previews.py",
            ):
                (root / name).write_text("", encoding="utf-8")
            work = root / "work-safe"
            with (
                patch.object(build_optimized_addon, "_runtime_root", return_value=root),
                patch.object(build_optimized_addon, "_run_single_addon") as run_one,
            ):
                rc = build_optimized_addon.main([
                    str(container), "--optimizer-mode", "maximum", "--suffix", "_OPT",
                    "--overwrite", "--work", str(work),
                ])
            self.assertEqual(rc, 2)
            run_one.assert_not_called()
            self.assertFalse(work.exists())

    def test_worker_forwards_maximum_arguments_untouched(self):
        argv = ["addon", "--optimizer-mode", "maximum", "--maximum-resume"]
        with patch.object(worker_main.build_optimized_addon, "main", return_value=7) as target:
            self.assertEqual(worker_main.main(argv), 7)
        target.assert_called_once_with(argv)

    def test_invalid_optimizer_mode_is_argparse_exit_two(self):
        with self.assertRaises(SystemExit) as raised:
            build_optimized_addon.main(["missing", "--optimizer-mode", "invalid"])
        self.assertEqual(raised.exception.code, 2)

    def test_production_profile_passes_calibration_gate_before_tool_preflight(self):
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
            with patch(
                "maximum_optimizer.orchestrator._resolved_blender",
                side_effect=MaximumConfigError("tool-stop"),
            ) as tool_preflight:
                rc = run_maximum_from_existing_args(
                    args,
                    repo_root=Path(__file__).parents[2],
                    addon_path=addon,
                    out_addon_dir=output,
                    work_dir=work,
                )
            self.assertEqual(rc, 2)
            tool_preflight.assert_called_once()
            self.assertFalse(output.exists())
            self.assertFalse(work.exists())

    def test_unsafe_work_overlap_returns_two_before_decompile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            addon = root / "addon"
            (addon / "models").mkdir(parents=True)
            sentinel = addon / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            blender = root / "blender.exe"
            studiomdl = root / "studiomdl.exe"
            blender.write_bytes(b"tool")
            studiomdl.write_bytes(b"tool")
            profile = root / "profile.json"
            profile.write_text(json.dumps({
                "schema": 1,
                "version": "test",
                "calibrated": True,
                "corpus_hash": "a" * 64,
                "limits": {
                    "silhouette_iou": 1, "rgb_mae": 1, "edge_error": 1,
                    "surface_bidirectional_p95": 1, "surface_max": 1,
                    "normal_angle_p95": 180, "uv_error_p95": 1,
                    "skinning_error_p95": 1,
                },
            }), encoding="utf-8")
            args = Namespace(
                maximum_profile=profile, blender=str(blender), studiomdl=str(studiomdl),
                maximum_max_candidates=2, maximum_min_ratio_step=0.1,
                maximum_min_marginal_saving=0.0, maximum_resume=False,
                resume_opt=False, overwrite=True, decompile_jobs=1,
            )
            with patch("maximum_optimizer.orchestrator.run_process") as decompile:
                rc = run_maximum_from_existing_args(
                    args,
                    repo_root=root,
                    addon_path=addon,
                    out_addon_dir=root / "output",
                    work_dir=addon,
                )
            self.assertEqual(rc, 2)
            decompile.assert_not_called()
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
