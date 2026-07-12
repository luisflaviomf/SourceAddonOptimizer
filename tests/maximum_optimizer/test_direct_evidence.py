from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from benchmarks.lvs_models.build_meshopt_direct_v1 import build
from maximum_optimizer.direct_evidence import load_meshopt_direct_evidence, seal_evidence


EVIDENCE = Path(__file__).resolve().parents[2] / "benchmarks/lvs_models/meshopt_direct_v1.json"


class DirectEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_committed_evidence_is_canonical_and_recomputed_from_real_artifacts(self) -> None:
        self.assertEqual(load_meshopt_direct_evidence(self.valid), self.valid)
        self.assertEqual(build(), self.valid)

    def test_single_field_mutations_from_valid_payload_fail_closed(self) -> None:
        mutations = []
        missing_raw = copy.deepcopy(self.valid); del missing_raw["records"][0]["smd"][0]["raw_sha256"]; mutations.append(missing_raw)
        ratio = copy.deepcopy(self.valid); ratio["records"][0]["ratio"] = 0.84; mutations.append(seal_evidence(ratio))
        metric = copy.deepcopy(self.valid); metric["records"][0]["smd"][0]["output_vertices"] = metric["records"][0]["smd"][0]["wedge_vertices"] + 1; mutations.append(seal_evidence(metric))
        size = copy.deepcopy(self.valid); size["records"][0]["compiled"]["candidate"][0]["size_bytes"] += 1; mutations.append(seal_evidence(size))
        path = copy.deepcopy(self.valid); path["records"][0]["compiled"]["candidate"][0]["path"] = "candidate-r085/models/wrong.mdl"; mutations.append(seal_evidence(path))
        control = copy.deepcopy(self.valid); control["records"][1]["compiled"]["control"][0]["sha256"] = "0" * 64; mutations.append(seal_evidence(control))
        restored = copy.deepcopy(self.valid); restored["records"][0]["smd"][0]["restored_sha256"] = restored["records"][0]["smd"][0]["raw_sha256"]; mutations.append(seal_evidence(restored))
        source = copy.deepcopy(self.valid); source["sources"][0]["sha256"] = "0" * 64; mutations.append(seal_evidence(source))
        tool = copy.deepcopy(self.valid); tool["tools"]["meshopt_bridge"]["sha256"] = "X" * 64; mutations.append(seal_evidence(tool))
        digest = copy.deepcopy(self.valid); digest["evidence_sha256"] = "0" * 64; mutations.append(digest)
        for payload in mutations:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                load_meshopt_direct_evidence(payload)
