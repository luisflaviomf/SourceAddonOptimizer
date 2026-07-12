from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest

from maximum_optimizer.importance_evidence import (
    canonical_importance_evidence_hash,
    canonical_implementation_snapshot_hash,
    parse_importance_evidence,
)


class ImportanceEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(Path(
            "benchmarks/lvs_models/blender_importance_map_v1.json"
        ).read_text(encoding="utf-8"))

    def test_committed_experiment_is_smaller_but_honestly_rejected(self) -> None:
        evidence = parse_importance_evidence(self.payload)
        self.assertEqual(evidence["schema_version"], 3)
        self.assertEqual(evidence["baseline"]["ratio"], 0.35)
        self.assertEqual(evidence["candidate"]["ratio"], 0.35)
        self.assertEqual(
            evidence["baseline"]["render"]["reference_manifest_sha256"],
            evidence["candidate"]["render"]["reference_manifest_sha256"],
        )
        self.assertEqual(
            evidence["baseline"]["render"]["reference_image_set_sha256"],
            evidence["candidate"]["render"]["reference_image_set_sha256"],
        )
        self.assertEqual(
            evidence["evidence_sha256"], canonical_importance_evidence_hash(evidence)
        )
        implementation = evidence["implementation"]
        self.assertEqual(implementation["scope"], "archived-execution-snapshot")
        self.assertIn("render_previews.py", implementation["files"])
        self.assertIn("maximum_optimizer/visual_validation.py", implementation["files"])
        self.assertEqual(
            implementation["snapshot_sha256"],
            canonical_implementation_snapshot_hash(implementation["files"]),
        )
        determinism = evidence["quality"]["render_determinism"]
        self.assertEqual(determinism["status"], "decoded-rgba-identical-across-repeat")
        self.assertEqual(
            determinism["repeat_reference_image_set_sha256"],
            evidence["candidate"]["render"]["reference_image_set_sha256"],
        )
        self.assertEqual(
            determinism["repeat_candidate_image_set_sha256"],
            evidence["candidate"]["render"]["candidate_image_set_sha256"],
        )
        self.assertLess(evidence["candidate"]["compiled"]["total_bytes"],
                        evidence["baseline"]["compiled"]["total_bytes"])
        self.assertGreater(evidence["candidate"]["raw_clay"]["max_edge_error"],
                           evidence["baseline"]["raw_clay"]["max_edge_error"])
        self.assertFalse(evidence["decision"]["winner"])
        self.assertEqual(evidence["decision"]["status"], "rejected-visible-boundary-regression")

    def test_mutating_metric_sidecar_implementation_or_manifest_invalidates_seal(self) -> None:
        mutations = []
        metric = copy.deepcopy(self.payload)
        metric["candidate"]["raw_clay"]["max_edge_error"] += 1e-6
        mutations.append(metric)
        sidecar = copy.deepcopy(self.payload)
        sidecar["candidate"]["compiled"]["artifacts"][0]["sha256"] = "0" * 64
        mutations.append(sidecar)
        implementation = copy.deepcopy(self.payload)
        implementation["implementation"]["files"]["render_previews.py"] = "0" * 64
        mutations.append(implementation)
        manifest = copy.deepcopy(self.payload)
        manifest["candidate"]["render"]["candidate_manifest_sha256"] = "0" * 64
        mutations.append(manifest)
        for payload in mutations:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_importance_evidence(payload)

    def test_canonical_hash_is_independent_of_json_formatting_and_excludes_its_field(self) -> None:
        first = canonical_importance_evidence_hash(self.payload)
        reparsed = json.loads(json.dumps(self.payload, indent=7, sort_keys=False))
        reparsed["evidence_sha256"] = "f" * 64
        self.assertEqual(first, canonical_importance_evidence_hash(reparsed))

    def test_archived_execution_snapshot_is_not_a_current_worktree_claim(self) -> None:
        execution = self.payload["implementation"]
        current = {
            relative: hashlib.sha256(Path(relative).read_bytes()).hexdigest()
            for relative in execution["files"]
        }
        self.assertNotEqual(current, execution["files"])
        self.assertEqual(execution["scope"], "archived-execution-snapshot")
        parse_importance_evidence(self.payload)

    def test_resealed_snapshot_with_wrong_snapshot_digest_is_rejected(self) -> None:
        mutation = copy.deepcopy(self.payload)
        mutation["implementation"]["snapshot_sha256"] = "0" * 64
        mutation["evidence_sha256"] = canonical_importance_evidence_hash(mutation)
        with self.assertRaisesRegex(ValueError, "snapshot digest"):
            parse_importance_evidence(mutation)

    def test_resealed_evidence_still_rejects_different_reference_artifacts(self) -> None:
        mutation = copy.deepcopy(self.payload)
        mutation["candidate"]["render"]["reference_manifest_sha256"] = "0" * 64
        mutation["evidence_sha256"] = canonical_importance_evidence_hash(mutation)
        with self.assertRaisesRegex(ValueError, "identical reference"):
            parse_importance_evidence(mutation)


if __name__ == "__main__":
    unittest.main()
