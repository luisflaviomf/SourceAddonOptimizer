from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import build_optimized_addon


class WorkerCliTests(unittest.TestCase):
    def test_maximum_is_public_mode_and_forces_internal_seed_only(self) -> None:
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
