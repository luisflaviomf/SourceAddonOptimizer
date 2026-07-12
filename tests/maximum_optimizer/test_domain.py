import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from maximum_optimizer.domain import (
    ArtifactStat,
    CandidateSpec,
    CompiledSizeSnapshot,
    SearchBudget,
    ValidationResult,
)


class DomainTests(unittest.TestCase):
    def test_candidate_is_immutable_and_has_stable_key(self):
        candidate = CandidateSpec("meshopt-r035", "meshoptimizer", 0.35, 0.01, "transfer-v1")
        self.assertEqual(candidate.cache_payload()["target_ratio"], 0.35)
        with self.assertRaises(FrozenInstanceError):
            candidate.target_ratio = 0.5

    def test_candidate_cache_payload_uses_structured_region_overrides(self):
        candidate = CandidateSpec(
            "regional", "meshoptimizer", 0.25, 0.01, "transfer-v1",
            (("r-" + "a" * 64, 0.5),),
        )
        self.assertEqual(
            candidate.cache_payload()["region_overrides"],
            [{"region_key": "r-" + "a" * 64, "ratio": 0.5}],
        )

    def test_meshopt_direct_strategy_is_explicit_and_cache_distinct(self):
        candidate = CandidateSpec(
            "meshopt-direct-r055",
            "meshoptimizer",
            0.55,
            0.01,
            "meshopt-direct-v1",
            strategy="meshopt-direct-v1",
            update_vertices=False,
            transfer="direct-v1",
        )

        self.assertEqual(
            candidate.cache_payload(),
            {
                "candidate_id": "meshopt-direct-r055",
                "engine": "meshoptimizer",
                "target_ratio": 0.55,
                "target_error": 0.01,
                "repair_profile": "meshopt-direct-v1",
                "region_overrides": [],
                "strategy": "meshopt-direct-v1",
                "update_vertices": False,
                "transfer": "direct-v1",
            },
        )

    def test_meshopt_direct_strategy_rejects_mutable_or_mismatched_contract(self):
        for values in (
            {"strategy": "meshopt-direct-v1", "update_vertices": True, "transfer": "direct-v1"},
            {"strategy": "meshopt-direct-v1", "update_vertices": False, "transfer": "project-v1"},
            {"strategy": "unknown", "update_vertices": False, "transfer": "direct-v1"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                CandidateSpec(
                    "meshopt-direct-r055", "meshoptimizer", 0.55, 0.01,
                    "meshopt-direct-v1", **values,
                )

    def test_snapshot_rejects_inconsistent_total(self):
        with self.assertRaisesRegex(ValueError, "total_bytes"):
            CompiledSizeSnapshot(Path("models"), 7, {".mdl": 3}, {}, ())

    def test_snapshot_mappings_are_read_only(self):
        snapshot = CompiledSizeSnapshot(
            Path("models"),
            3,
            {".mdl": 3},
            {0: 10},
            (ArtifactStat("example.mdl", ".mdl", 3),),
        )

        with self.assertRaises(TypeError):
            snapshot.bytes_by_kind[".vvd"] = 4
        with self.assertRaises(TypeError):
            snapshot.vertices_by_lod[1] = 5

    def test_snapshot_copies_input_mappings(self):
        bytes_by_kind = {".mdl": 3}
        vertices_by_lod = {0: 10}
        snapshot = CompiledSizeSnapshot(
            Path("models"),
            3,
            bytes_by_kind,
            vertices_by_lod,
            (ArtifactStat("example.mdl", ".mdl", 3),),
        )

        bytes_by_kind[".vvd"] = 4
        vertices_by_lod[1] = 5

        self.assertEqual(snapshot.bytes_by_kind, {".mdl": 3})
        self.assertEqual(snapshot.vertices_by_lod, {0: 10})

    def test_snapshot_to_dict_uses_plain_dicts_and_string_root(self):
        snapshot = CompiledSizeSnapshot(
            Path("models"),
            3,
            {".mdl": 3},
            {0: 10},
            (ArtifactStat("example.mdl", ".mdl", 3),),
        )

        payload = snapshot.to_dict()

        self.assertEqual(payload["root"], "models")
        self.assertIs(type(payload["bytes_by_kind"]), dict)
        self.assertIs(type(payload["vertices_by_lod"]), dict)
        self.assertIs(type(payload["artifacts"][0]), dict)

    def test_snapshot_to_dict_preserves_artifact_tuple(self):
        snapshot = CompiledSizeSnapshot(
            Path("models"),
            3,
            {".mdl": 3},
            {0: 10},
            (ArtifactStat("example.mdl", ".mdl", 3),),
        )

        self.assertEqual(
            snapshot.to_dict()["artifacts"],
            (
                {
                    "relative_path": "example.mdl",
                    "kind": ".mdl",
                    "size_bytes": 3,
                    "lod_vertices": (),
                },
            ),
        )

    def test_validation_metrics_are_read_only(self):
        validation = ValidationResult(True, metrics={"fidelity_score": 0.9})

        with self.assertRaises(TypeError):
            validation.metrics["fidelity_score"] = 0.5

    def test_validation_copies_input_metrics(self):
        metrics = {"fidelity_score": 0.9}
        validation = ValidationResult(True, metrics=metrics)

        metrics["fidelity_score"] = 0.5

        self.assertEqual(validation.metrics, {"fidelity_score": 0.9})

    def test_budget_defaults_are_bounded(self):
        budget = SearchBudget.experimental_default()
        self.assertEqual(budget.max_candidates, 18)
        self.assertGreater(budget.min_ratio_step, 0)


if __name__ == "__main__":
    unittest.main()
