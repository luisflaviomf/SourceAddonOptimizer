import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from copy import deepcopy

import benchmarks.lvs_models.build_meshopt_direct_position_v1 as builder
from maximum_optimizer.direct_evidence import seal_evidence


class PositionEvidenceTests(unittest.TestCase):
    def test_checked_in_fixture_is_hermetic_strict_and_unverified(self):
        self.assertTrue(hasattr(builder, "build_unit_fixture"))
        evidence = importlib.import_module("maximum_optimizer.position_evidence")
        fixture = Path("benchmarks/lvs_models/fixtures/meshopt_direct_position_v1_unit.json")
        payload = evidence.load_position_evidence(json.loads(fixture.read_text(encoding="utf-8")))
        self.assertEqual(builder.build_unit_fixture(), payload)
        self.assertEqual(payload["schema_version"], 3)
        self.assertEqual(payload["evidence_scope"], "local_external_evidence")
        self.assertEqual(payload["record_kind"], "hermetic_test_fixture")
        self.assertEqual(payload["quality_status"], "unverified")
        self.assertFalse(payload["decision"]["winner"])

        mutations = {
            "locked": lambda value: value["checked_in_metrics"]["records"][0]["counts"].update(
                locked_vertices=value["checked_in_metrics"]["records"][0]["counts"]["wedge_vertices"] + 1
            ),
            "bytes": lambda value: value["local_external_evidence"]["wheel_records"][0].update(
                compiled_bytes=value["local_external_evidence"]["wheel_records"][0]["compiled_bytes"] + 1
            ),
            "charger": lambda value: value["charger_probe"].update(compiled_available=True),
        }
        for name, mutate in mutations.items():
            hostile = deepcopy(payload)
            mutate(hostile)
            hostile = seal_evidence(hostile)
            with self.subTest(mutation=name), self.assertRaises(ValueError):
                evidence.load_position_evidence(hostile)
        hostile = deepcopy(payload)
        hostile["evidence_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            evidence.load_position_evidence(hostile)

    def test_local_rehash_requires_explicit_external_root(self):
        env = dict(os.environ)
        env.pop("LVS_TASK6_EVIDENCE_ROOT", None)
        result = subprocess.run(
            [sys.executable, "benchmarks/lvs_models/build_meshopt_direct_position_v1.py"],
            cwd=Path.cwd(), env=env, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LVS_TASK6_EVIDENCE_ROOT", result.stderr)


if __name__ == "__main__":
    unittest.main()
