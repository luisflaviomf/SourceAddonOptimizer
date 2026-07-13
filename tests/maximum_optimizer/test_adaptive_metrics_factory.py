from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest

from maximum_optimizer.adaptive_metrics_factory import (
    build_production_adaptive_candidate_metrics_proof,
    canonical_qc_graph_payload,
    load_candidate_metrics_json,
    qc_graph_sha256,
)
from maximum_optimizer.composite import (
    build_recovery_source_snapshot,
    build_source_tree_manifest,
    candidate_spec_sha256,
    optimizer_contract_sha256,
)
from maximum_optimizer.domain import CandidateSpec, FamilyManifest, StructuralFingerprint
from maximum_optimizer.qc_graph import parse_qc_graph


H = {character: character * 64 for character in "0123456789abcdef"}


def _fingerprint() -> StructuralFingerprint:
    return StructuralFingerprint(
        "models/test.mdl", ("wheels",), (), (), (), (), (), (), (),
        ("body.smd", "wheel.smd"), (), None,
    )


def _write_tree(root: Path, *, optimized: bool, wheel_bytes: bytes = b"wheel") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if optimized:
        (root / "output").mkdir()
        (root / "main_OPT.qc").write_text(
            '$modelname "models/test.mdl"\n$include "parts_OPT.qci"\n'
            '$body "main" "output/body_opt.smd"\n', encoding="utf-8",
        )
        (root / "parts_OPT.qci").write_text(
            '$bodygroup "wheels"\n{\n studio "output/wheel_opt.smd"\n blank\n}\n',
            encoding="utf-8",
        )
        (root / "output" / "body_opt.smd").write_bytes(b"body")
        (root / "output" / "wheel_opt.smd").write_bytes(wheel_bytes)
        return root / "main_OPT.qc"
    (root / "main.qc").write_text(
        '$modelname "models/test.mdl"\n$include "parts.qci"\n$body "main" "body.smd"\n',
        encoding="utf-8",
    )
    (root / "parts.qci").write_text(
        '$bodygroup "wheels"\n{\n studio "wheel.smd"\n blank\n}\n', encoding="utf-8",
    )
    (root / "body.smd").write_bytes(b"body")
    (root / "wheel.smd").write_bytes(b"wheel")
    return root / "main.qc"


def _spec() -> CandidateSpec:
    return CandidateSpec(
        "adaptive-r040", "blender", 0.4, 0.0, "blender-adaptive-v1",
        strategy="blender-adaptive-v1", transfer="blender-native-v1",
    )


class FactoryFixture:
    def __init__(self, root: Path, *, mutate=None, wheel_bytes: bytes = b"wheel") -> None:
        self.original_root = root / "original"
        self.candidate_root = root / "candidate"
        original_qc = _write_tree(self.original_root, optimized=False)
        candidate_qc = _write_tree(
            self.candidate_root, optimized=True, wheel_bytes=wheel_bytes,
        )
        self.spec = _spec()
        self.cache_digest = H["c"]
        self.manifest = FamilyManifest(
            H["a"], "models/test.mdl", self.original_root, root / "models",
            _fingerprint(), H["b"], (".mdl",),
        )
        payload = {
            "schema_version": 1,
            "candidate_id": self.spec.candidate_id,
            "engine": "blender",
            "strategy": self.spec.strategy,
            "files": [
                {
                    "source": "C:/forged/source/body.smd",
                    "output": "C:/forged/output/body.smd",
                    "source_sha256": H["0"],
                    "output_sha256": H["f"],
                    "adaptive_exact_preservation": {
                        "schema": 1, "source_identity": "body.smd",
                        "kind": "eligible-exact-v1", "preserved_exact": True,
                        "reason": "ratio-preserved-exact-v1",
                    },
                },
                {
                    "source": "C:/forged/source/wheel.smd",
                    "output": "C:/forged/output/wheel.smd",
                    "adaptive_exact_preservation": {
                        "schema": 1, "source_identity": "wheel.smd",
                        "kind": (
                            "eligible-exact-v1" if wheel_bytes == b"wheel"
                            else "ineligible-changed-v1"
                        ),
                        "preserved_exact": wheel_bytes == b"wheel",
                        "reason": (
                            "approved-exact-source-fallback-v1"
                            if wheel_bytes == b"wheel" else "adaptive-output-changed-v1"
                        ),
                    },
                },
            ],
            "provenance": [
                {
                    "graph_file": "main.qc", "directive": "$body", "line": 3,
                    "logical_path": "body.smd", "role": "visual",
                    "source_sha256": H["0"], "output_sha256": H["f"],
                },
                {
                    "graph_file": "parts.qci", "directive": "$bodygroup/studio",
                    "line": 3, "logical_path": "wheel.smd", "role": "visual",
                    "source_sha256": H["0"], "output_sha256": H["f"],
                },
            ],
        }
        if mutate is not None:
            mutate(payload)
        self.metrics_path = self.candidate_root / "candidate_metrics.json"
        self.metrics_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8",
        )
        original_graph = parse_qc_graph(original_qc, self.original_root)
        candidate_graph = parse_qc_graph(candidate_qc, self.candidate_root)
        original_manifest = build_source_tree_manifest(
            self.original_root, original_graph, "original-source-v1", None,
        )
        candidate_manifest = build_source_tree_manifest(
            self.candidate_root, candidate_graph, "candidate-source-v1", None,
        )
        common = dict(
            family_id=self.manifest.family_id,
            family_input_sha256=self.manifest.input_hash,
            optimizer_contract_sha256=optimizer_contract_sha256(self.spec),
            whole_profile_sha256=H["2"],
            focused_profile_sha256=H["3"], dependency_proof_sha256=H["4"],
            focused_evidence=(),
        )
        self.original_snapshot = build_recovery_source_snapshot(
            kind="original", candidate_id=None, candidate_cache_digest=None,
            source_root=self.original_root, source_manifest=original_manifest, **common,
        )
        self.candidate_snapshot = build_recovery_source_snapshot(
            kind="candidate", candidate_id=self.spec.candidate_id,
            candidate_cache_digest=self.cache_digest, source_root=self.candidate_root,
            source_manifest=candidate_manifest, **common,
        )

    def build(self):
        return build_production_adaptive_candidate_metrics_proof(
            manifest=self.manifest, spec=self.spec,
            candidate_cache_digest=self.cache_digest,
            original_snapshot=self.original_snapshot,
            candidate_snapshot=self.candidate_snapshot,
            candidate_metrics_path=self.metrics_path,
            cancel_event=threading.Event(),
        )


class QcGraphDigestTests(unittest.TestCase):
    def test_digest_is_root_independent_and_covers_current_files_and_graph_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as first_raw, tempfile.TemporaryDirectory() as second_raw:
            first = Path(first_raw); second = Path(second_raw)
            first_qc = _write_tree(first, optimized=False)
            second_qc = _write_tree(second, optimized=False)
            first_graph = parse_qc_graph(first_qc, first)
            second_graph = parse_qc_graph(second_qc, second)
            payload = canonical_qc_graph_payload(first_graph)
            self.assertEqual(payload, canonical_qc_graph_payload(second_graph))
            self.assertEqual(qc_graph_sha256(first_graph), qc_graph_sha256(second_graph))
            self.assertEqual(payload["root_qc"], "main.qc")
            self.assertEqual({item["path"] for item in payload["files"]}, {"main.qc", "parts.qci"})
            self.assertEqual(len(payload["occurrences"]), 2)
            self.assertEqual(len(payload["bodygroups"]), 1)
            serialized = json.dumps(payload, sort_keys=True)
            self.assertNotIn(first.as_posix(), serialized)
            self.assertNotIn(second.as_posix(), serialized)
            (second / "parts.qci").write_text(
                '$bodygroup "doors"\n{\n studio "wheel.smd"\n blank\n}\n', encoding="utf-8",
            )
            changed = parse_qc_graph(second_qc, second)
            self.assertNotEqual(qc_graph_sha256(first_graph), qc_graph_sha256(changed))


class ProductionAdaptiveMetricsFactoryTests(unittest.TestCase):
    def test_factory_builds_complete_proof_from_typed_current_source_proofs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = FactoryFixture(Path(raw))
            proof = fixture.build()
            self.assertEqual(proof.base_spec_sha256, candidate_spec_sha256(fixture.spec))
            self.assertEqual(proof.candidate_cache_digest, fixture.cache_digest)
            self.assertEqual(proof.source_manifest_sha256, fixture.candidate_snapshot.source_manifest.digest)
            self.assertEqual(proof.source_snapshot_sha256, fixture.candidate_snapshot.snapshot_sha256)
            self.assertEqual(tuple(item.source_identity for item in proof.sources), ("body.smd", "wheel.smd"))
            source_files = {
                item.file_identity: item for item in fixture.original_snapshot.source_manifest.files
                if item.kind == "visual-source"
            }
            candidate_files = {
                item.file_identity: item for item in fixture.candidate_snapshot.source_manifest.files
                if item.kind == "visual-source"
            }
            for item in proof.sources:
                self.assertEqual(
                    (item.source_relative_path, item.source_size, item.source_sha256),
                    (source_files[item.source_identity].relative_path,
                     source_files[item.source_identity].size,
                     source_files[item.source_identity].sha256),
                )
                self.assertEqual(
                    (item.output_relative_path, item.output_size, item.output_sha256),
                    (candidate_files[item.source_identity].relative_path,
                     candidate_files[item.source_identity].size,
                     candidate_files[item.source_identity].sha256),
                )
            self.assertEqual(proof.sources[1].eligibility_reason, "approved-exact-source-fallback-v1")

    def test_factory_accepts_changed_closed_source_and_rejects_unauthorized_or_incomplete_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            changed = FactoryFixture(Path(raw) / "changed", wheel_bytes=b"smaller")
            proof = changed.build()
            self.assertEqual(proof.sources[1].kind, "ineligible-changed-v1")

        def unauthorized(payload):
            payload["files"][0]["adaptive_exact_preservation"] = {
                "schema": 1, "source_identity": "body.smd", "kind": "unauthorized-v1",
                "preserved_exact": True, "reason": "unproven-exact-preservation-v1",
            }

        def missing_occurrence(payload):
            payload["provenance"].pop()

        def unknown_reason(payload):
            payload["files"][0]["adaptive_exact_preservation"]["reason"] = "looks-perfect"

        for label, mutate in (
            ("unauthorized", unauthorized), ("unknown-reason", unknown_reason),
            ("provenance", missing_occurrence),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as raw:
                fixture = FactoryFixture(Path(raw), mutate=mutate)
                with self.assertRaises(ValueError):
                    fixture.build()

    def test_factory_rejects_metrics_strategy_different_from_typed_spec(self) -> None:
        def wrong_strategy(payload):
            payload["strategy"] = "legacy-v1"

        with tempfile.TemporaryDirectory() as raw:
            fixture = FactoryFixture(Path(raw), mutate=wrong_strategy)
            with self.assertRaisesRegex(ValueError, "identity"):
                fixture.build()

    def test_read_only_loader_parses_a_real_superpowers_json_without_mutation(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        paths = sorted((repository / ".superpowers").rglob("candidate_metrics.json"))
        if not paths:
            self.skipTest("real .superpowers candidate_metrics.json is unavailable")
        selected = paths[0]
        before = selected.read_bytes()
        payload, size, digest = load_candidate_metrics_json(selected, selected.parent, None)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(size, len(before))
        self.assertEqual(digest, hashlib.sha256(before).hexdigest())
        self.assertEqual(selected.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
