from __future__ import annotations

from pathlib import Path
from contextlib import redirect_stdout
import builtins
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import build_optimized_addon
from maximum_optimizer.silhouette_backend import reset_silhouette_backend


class WorkerCliTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_silhouette_backend()

    def test_maximum_is_public_mode_and_starts_from_profiled_normal_seed(self) -> None:
        args = build_optimized_addon.parse_args(
            ["addon", "--optimizer-mode", "maximum", "--ratio", "0.75"]
        )

        self.assertEqual(args.optimizer_mode, "maximum")
        self.assertEqual(build_optimized_addon.effective_normal_ratio(args), 0.35)

    def test_normal_and_fidelity_ratios_remain_user_values(self) -> None:
        for mode in ("normal", "fidelity"):
            with self.subTest(mode=mode):
                args = build_optimized_addon.parse_args(
                    ["addon", "--optimizer-mode", mode, "--ratio", "0.75"]
                )
                self.assertEqual(build_optimized_addon.effective_normal_ratio(args), 0.75)

    def test_normal_and_fidelity_never_import_or_initialize_silhouette_backend(self) -> None:
        real_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.startswith("maximum_optimizer.silhouette"):
                raise AssertionError(f"silhouette backend imported for non-Maximum mode: {name}")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            for mode in ("normal", "fidelity"):
                self.assertIsNone(
                    build_optimized_addon._initialize_silhouette_backend_for_mode(
                        mode, Path(__file__).resolve().parents[2]
                    )
                )

    def test_maximum_uses_repo_absolute_dll_and_ignores_retired_environment_override(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"MAXIMUM_SILHOUETTE_EXPERIMENT_DLL": str(repo_root / "fake-old.dll")},
            clear=False,
        ), redirect_stdout(output):
            backend = build_optimized_addon._initialize_silhouette_backend_for_mode(
                "maximum", repo_root
            )
            build_optimized_addon._print_silhouette_backend_summary(backend)

        self.assertIsNotNone(backend)
        self.assertEqual(backend.state, "native")
        text = output.getvalue()
        self.assertIn("selected=native", text)
        self.assertIn("api=1.0.0", text)
        self.assertIn("build=maximum-silhouette-raw-v1-20260722", text)
        self.assertIn("silhouette_backend_summary", text)
        self.assertNotIn("fake-old.dll", text)

    def test_missing_maximum_native_package_selects_exact_fallback_without_job_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw, redirect_stdout(io.StringIO()) as output:
            backend = build_optimized_addon._initialize_silhouette_backend_for_mode(
                "maximum", Path(raw)
            )
        self.assertIsNotNone(backend)
        self.assertEqual(backend.state, "legacy")
        self.assertTrue(backend.snapshot()["fallback"])
        self.assertIn("selected=legacy", output.getvalue())

    def test_maximum_writes_a_machine_readable_backend_report(self) -> None:
        snapshot = {
            "backend": "native",
            "api_version": "1.0.0",
            "build_id": "maximum-silhouette-raw-v1-20260722",
            "dll_path": "C:/validated/tools/meshopt_bridge.dll",
            "calls": 3,
            "silhouette_total_ns": 2_500_000,
            "fallback": False,
        }
        backend = mock.Mock()
        backend.snapshot.return_value = snapshot

        with tempfile.TemporaryDirectory() as raw:
            work_dir = Path(raw)
            build_optimized_addon._write_silhouette_backend_report(backend, work_dir)
            report = json.loads(
                (work_dir / "logs" / "maximum_silhouette_backend.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(report["schema"], 1)
        self.assertEqual(report["backend"], snapshot)
        self.assertEqual(report["timing_ms"]["silhouette_total"], 2.5)

    def test_framework_resolver_is_optional_and_never_changes_addon_path(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            resolver = Path(raw) / "framework"
            args = build_optimized_addon.parse_args(
                [
                    "C:/addon",
                    "--optimizer-mode",
                    "maximum",
                    "--maximum-framework-resolver",
                    str(resolver),
                ]
            )

        self.assertEqual(args.addon, "C:/addon")
        self.assertEqual(Path(args.maximum_framework_resolver), resolver)

    def test_normal_mirror_maps_opt_sources_without_mutating_original_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original = root / "original"
            live = root / "live"
            normal = root / "normal"
            for path in (original, live / "output"):
                path.mkdir(parents=True)
            qc = '$modelname "cars/test.mdl"\n$body "body" "mesh.smd"\n'
            opt_qc = '$modelname "cars/test.mdl"\n$body "body" "output/mesh_opt.smd"\n'
            (original / "model.qc").write_text(qc, encoding="utf-8")
            (original / "mesh.smd").write_text("original", encoding="utf-8")
            (live / "model.qc").write_text(qc, encoding="utf-8")
            (live / "model_OPT.qc").write_text(opt_qc, encoding="utf-8")
            (live / "mesh.smd").write_text("original", encoding="utf-8")
            (live / "output" / "mesh_opt.smd").write_text("normal", encoding="utf-8")

            mapped = build_optimized_addon._build_maximum_normal_source_tree(
                original,
                live,
                normal,
            )
            normal_text = (normal / "mesh.smd").read_text(encoding="utf-8")
            original_text = (original / "mesh.smd").read_text(encoding="utf-8")

        self.assertEqual(mapped, 1)
        self.assertEqual(normal_text, "normal")
        self.assertEqual(original_text, "original")


if __name__ == "__main__":
    unittest.main()
