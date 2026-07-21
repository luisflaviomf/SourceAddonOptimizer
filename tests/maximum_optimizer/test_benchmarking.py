from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from maximum_optimizer.benchmarking import (
    BenchmarkFamily,
    FamilyResult,
    aggregate_results,
    assert_isolated_lane_paths,
    copy_family_input,
    load_corpus,
    load_family_results,
    scan_compiled_models,
    tree_fingerprint,
    verify_family_input,
)


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "benchmarks" / "lvs_models_adaptive" / "corpus.json"


class BenchmarkingTests(unittest.TestCase):
    def test_corpus_has_five_development_and_three_holdout_families(self) -> None:
        corpus = load_corpus(CORPUS)
        self.assertEqual(len(corpus.development), 5)
        self.assertEqual(len(corpus.holdout), 3)
        self.assertEqual(
            {family.id for family in corpus.holdout},
            {"dodge_charger", "toyota_supra", "pontiac_transam_wheel"},
        )

    def test_aggregate_recomputes_and_excludes_dx80(self) -> None:
        families = (
            FamilyResult.fixture("a", original=1000, final=400, dx80=100),
            FamilyResult.fixture("b", original=2000, final=1200, dx80=200),
        )
        result = aggregate_results("normal-safe", families)
        self.assertEqual(result.original_comparable, 3000)
        self.assertEqual(result.final_comparable, 1600)
        self.assertEqual(result.saved_comparable, 1400)
        self.assertEqual(result.dx80_removed, 300)
        self.assertAlmostEqual(result.reduction_percent, 46.6666666667)
        self.assertNotIn(result.dx80_removed, (result.original_comparable, result.final_comparable))

    def test_model_scan_requires_dx90_and_never_counts_dx80_as_comparable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            models = Path(temporary)
            (models / "car.mdl").write_bytes(b"m" * 10)
            (models / "car.vvd").write_bytes(b"v" * 20)
            (models / "car.dx90.vtx").write_bytes(b"9" * 30)
            (models / "car.dx80.vtx").write_bytes(b"8" * 40)
            inventory = scan_compiled_models(models)
        self.assertEqual(inventory.comparable_bytes, 60)
        self.assertEqual(inventory.dx80_bytes, 40)
        self.assertEqual(inventory.dx90_count, 1)
        self.assertEqual(inventory.dx80_count, 1)

    def test_tree_hash_detects_any_input_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "models" / "cars" / "wheel.mdl"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"stable")
            family_payload = {
                "id": "wheel",
                "partition": "development",
                "include": ["models/cars/wheel"],
                "focus_regions": ["wheels_tires"],
                "file_count": 1,
                "total_bytes": 6,
                "tree_sha256": "0" * 64,
            }
            corpus_path = root / "corpus.json"
            corpus_path.write_text(
                json.dumps({"schema": 1, "families": [family_payload]}),
                encoding="utf-8",
            )
            family = load_corpus(corpus_path).development[0]
            with self.assertRaisesRegex(ValueError, "tree SHA-256"):
                verify_family_input(root, family)

    def test_lane_paths_must_not_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assert_isolated_lane_paths(root / "input", root / "work", root / "output")
            with self.assertRaisesRegex(ValueError, "overlap"):
                assert_isolated_lane_paths(root / "input", root / "input" / "work", root / "output")

    def test_family_fixture_includes_small_vmt_resolver_without_changing_frozen_model_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            model = source / "models" / "cars" / "wheel.mdl"
            material = source / "materials" / "models" / "cars" / "rubber.vmt"
            model.parent.mkdir(parents=True)
            material.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            material.write_text('"VertexLitGeneric" {}\n', encoding="utf-8")
            count, total, digest = tree_fingerprint(source, (model,))
            family = BenchmarkFamily(
                "wheel", "development", ("models/cars/wheel",), ("wheels_tires",),
                count, total, digest,
            )
            destination = root / "fixture"

            copy_family_input(source, family, destination)

            self.assertTrue((destination / "models" / "cars" / "wheel.mdl").is_file())
            self.assertTrue((destination / "materials" / "models" / "cars" / "rubber.vmt").is_file())

    def test_result_loader_ignores_archived_aborted_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "development" / "wheel" / "normal-safe" / "result.json"
            archived = root / ".archive" / "wheel-normal-safe-aborted" / "result.json"
            prototype = root / "prototypes" / "wheel-normal-safe" / "result.json"
            active.parent.mkdir(parents=True)
            archived.parent.mkdir(parents=True)
            prototype.parent.mkdir(parents=True)
            payload = FamilyResult.fixture("wheel", original=1000, final=400, dx80=100).__dict__
            active.write_text(json.dumps(payload), encoding="utf-8")
            archived_payload = dict(payload, status="failed", exit_code=-1)
            archived.write_text(json.dumps(archived_payload), encoding="utf-8")
            prototype.write_text(json.dumps(payload), encoding="utf-8")

            results = load_family_results(root)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "ok")


if __name__ == "__main__":
    unittest.main()
