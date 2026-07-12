from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from types import MappingProxyType

from maximum_optimizer.benchmarking import (
    ArtifactDeclaration,
    BASELINE_LANES,
    BenchmarkRecord,
    Corpus,
    FamilySpec,
)
from maximum_optimizer.dx90_optional import (
    DX90_OPTIONAL_LANE,
    Dx90PolicyError,
    RuntimeEvidence,
    build_dx90_optional_experiment,
    write_dx90_optional_experiment,
    build_dx90_optional_candidate,
    validate_dx90_optional_candidate,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Dx90OptionalCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.candidate = self.root / "candidate"
        self.source.mkdir()
        self.payloads = {
            "models/car.mdl": b"mdl",
            "models/car.vvd": b"vvd-data",
            "models/car.dx80.vtx": b"dx80-data",
            "models/car.dx90.vtx": b"dx90-data",
            "models/car.phy": b"physics",
        }
        for relative, payload in self.payloads.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        self.declarations = tuple(
            ArtifactDeclaration(relative, len(payload), _digest(payload))
            for relative, payload in self.payloads.items()
        )

    def test_requires_explicit_gmod_dynamic_runtime_target(self) -> None:
        for target in (None, "universal", "source_engine", "gmod_static_prop"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(Dx90PolicyError, "gmod_dynamic_runtime"):
                    build_dx90_optional_candidate(
                        source_root=self.source,
                        candidate_root=self.candidate,
                        compiled_stem="models/car",
                        declarations=self.declarations,
                        target=target,
                    )

    def test_copies_every_other_artifact_identically_and_accounts_exact_dx80_saving(self) -> None:
        result = build_dx90_optional_candidate(
            source_root=self.source,
            candidate_root=self.candidate,
            compiled_stem="models/car",
            declarations=self.declarations,
            target="gmod_dynamic_runtime",
        )

        self.assertEqual(result.lane, DX90_OPTIONAL_LANE)
        self.assertEqual(result.omitted_paths, ("models/car.dx80.vtx",))
        self.assertEqual(result.saved_bytes, len(self.payloads["models/car.dx80.vtx"]))
        self.assertEqual(result.source_bytes - result.candidate_bytes, result.saved_bytes)
        expected = set(self.payloads) - {"models/car.dx80.vtx"}
        self.assertEqual({item.path for item in result.artifacts}, expected)
        for item in result.artifacts:
            self.assertEqual(item.sha256, _digest(self.payloads[item.path]))
            self.assertEqual((self.candidate / item.path).read_bytes(), self.payloads[item.path])

    def test_rejects_missing_required_sidecar_and_nonexact_dx80_omission(self) -> None:
        required = {".mdl": "models/car.mdl", ".vvd": "models/car.vvd", ".dx90": "models/car.dx90.vtx"}
        for label, relative in required.items():
            with self.subTest(label=label):
                declarations = tuple(item for item in self.declarations if item.path != relative)
                with self.assertRaisesRegex(Dx90PolicyError, "requires.*mdl.*vvd.*dx90"):
                    build_dx90_optional_candidate(
                        source_root=self.source,
                        candidate_root=self.root / f"candidate-{label}",
                        compiled_stem="models/car",
                        declarations=declarations,
                        target="gmod_dynamic_runtime",
                    )
        generic = self.source / "models/car.vtx"
        generic.write_bytes(b"generic")
        declarations = self.declarations + (
            ArtifactDeclaration("models/car.vtx", len(b"generic"), _digest(b"generic")),
        )
        result = build_dx90_optional_candidate(
            source_root=self.source,
            candidate_root=self.root / "candidate-generic",
            compiled_stem="models/car",
            declarations=declarations,
            target="gmod_dynamic_runtime",
        )
        self.assertIn("models/car.vtx", {item.path for item in result.artifacts})

    def test_rejects_source_extra_missing_hash_drift_and_candidate_tamper(self) -> None:
        (self.source / "models/car.ani").write_bytes(b"undeclared")
        with self.assertRaisesRegex(Dx90PolicyError, "sidecar set"):
            build_dx90_optional_candidate(
                source_root=self.source,
                candidate_root=self.candidate,
                compiled_stem="models/car",
                declarations=self.declarations,
                target="gmod_dynamic_runtime",
            )
        (self.source / "models/car.ani").unlink()
        drifted = list(self.declarations)
        drifted[0] = ArtifactDeclaration(drifted[0].path, drifted[0].size_bytes, "0" * 64)
        with self.assertRaisesRegex(Dx90PolicyError, "hash mismatch"):
            build_dx90_optional_candidate(
                source_root=self.source,
                candidate_root=self.candidate,
                compiled_stem="models/car",
                declarations=drifted,
                target="gmod_dynamic_runtime",
            )
        result = build_dx90_optional_candidate(
            source_root=self.source,
            candidate_root=self.candidate,
            compiled_stem="models/car",
            declarations=self.declarations,
            target="gmod_dynamic_runtime",
        )
        (self.candidate / "models/car.vvd").write_bytes(b"tampered")
        with self.assertRaisesRegex(Dx90PolicyError, "mismatch"):
            validate_dx90_optional_candidate(self.candidate, "models/car", result.artifacts)

    def test_rejects_reparse_points_when_supported(self) -> None:
        link = self.source / "models/car.ani"
        try:
            link.symlink_to(self.source / "models/car.phy")
        except OSError as exc:
            self.skipTest(f"symlink privilege unavailable: {exc}")
        declarations = self.declarations + (
            ArtifactDeclaration("models/car.ani", len(b"physics"), _digest(b"physics")),
        )
        with self.assertRaisesRegex(Dx90PolicyError, "reparse|symlink"):
            build_dx90_optional_candidate(
                source_root=self.source,
                candidate_root=self.candidate,
                compiled_stem="models/car",
                declarations=declarations,
                target="gmod_dynamic_runtime",
            )


class RuntimeEvidenceTests(unittest.TestCase):
    def test_pending_is_not_runtime_proof_and_static_tools_cannot_prove(self) -> None:
        pending = RuntimeEvidence.pending(
            reason="hidden runtime automation unavailable",
            manual_procedure=("launch Garry's Mod", "spawn bodygroup/animated/physics representatives", "inspect logs"),
        )
        self.assertEqual(pending.status, "pending")
        self.assertFalse(pending.runtime_proven)
        with self.assertRaisesRegex(Dx90PolicyError, "Garry's Mod runtime"):
            RuntimeEvidence.proven(
                tool_name="Crowbar",
                tool_sha256="1" * 64,
                build_id="0.74",
                log_sha256="2" * 64,
                capabilities=("static_decompile",),
            )

    def test_proven_requires_hashed_gmod_runtime_log_and_all_capabilities(self) -> None:
        evidence = RuntimeEvidence.proven(
            tool_name="Garry's Mod",
            tool_sha256="1" * 64,
            build_id="2026.07.12",
            log_sha256="2" * 64,
            capabilities=("bodygroups", "animation", "physics", "dynamic_model_load"),
        )
        self.assertTrue(evidence.runtime_proven)
        self.assertEqual(RuntimeEvidence.from_dict(evidence.to_dict()), evidence)
        for capabilities in (("bodygroups",), ("bodygroups", "animation", "physics")):
            with self.subTest(capabilities=capabilities):
                with self.assertRaisesRegex(Dx90PolicyError, "capabilities"):
                    RuntimeEvidence.proven(
                        tool_name="Garry's Mod",
                        tool_sha256="1" * 64,
                        build_id="2026.07.12",
                        log_sha256="2" * 64,
                        capabilities=capabilities,
                    )

    def test_strict_schema_rejects_unknown_and_tampered_fields(self) -> None:
        raw = RuntimeEvidence.pending(reason="not run", manual_procedure=("step",)).to_dict()
        raw["extra"] = True
        with self.assertRaisesRegex(Dx90PolicyError, "keys"):
            RuntimeEvidence.from_dict(raw)


class Dx90BenchmarkLaneTests(unittest.TestCase):
    def test_dx90_optional_lane_is_explicit_and_keeps_optional_saving_out_of_geometry(self) -> None:
        record = BenchmarkRecord.create(
            corpus_id="lvs-models-v1",
            family_id="dodge_charger",
            lane=DX90_OPTIONAL_LANE,
            strategy="omit-dx80-gmod-dynamic-runtime",
            cache_key="a" * 64,
            artifacts={".mdl": 10, ".vvd": 20, ".dx90.vtx": 30, ".phy": 5},
            provenance={"tool": {}, "scripts": {}, "settings": {"target": "gmod_dynamic_runtime"}},
            gates={"structural": "pass", "visual": "not_run", "runtime": "not_run"},
        )
        self.assertEqual(record.lane, "dx90_optional")
        self.assertNotIn(record.lane, BASELINE_LANES)
        self.assertEqual(record.geometry_comparable_bytes, 65)
        self.assertEqual(record.dx80_optional_bytes, 0)

    def test_pressure_experiment_records_optional_saving_with_pending_runtime_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            payloads = {
                "models/car.mdl": b"mdl",
                "models/car.vvd": b"vvd",
                "models/car.dx80.vtx": b"dx80",
                "models/car.dx90.vtx": b"dx90",
            }
            declarations = []
            for relative, payload in payloads.items():
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                declarations.append(ArtifactDeclaration(relative, len(payload), _digest(payload)))
            family = FamilySpec(
                "car",
                "Car",
                "car.qc",
                (),
                "models/car",
                MappingProxyType({"original": tuple(declarations)}),
            )
            corpus = Corpus(
                1,
                "lvs-models-v1",
                MappingProxyType({"original": source}),
                ("car",),
                ("car",),
                (family,),
            )
            pending = RuntimeEvidence.pending(reason="not run", manual_procedure=("manual runtime load",))

            payload = build_dx90_optional_experiment(
                corpus=corpus,
                candidate_root=root / "candidates",
                runtime_evidence=pending,
                script_hash="b" * 64,
            )

            self.assertEqual(payload["lane"], "dx90_optional")
            self.assertEqual(payload["runtime_evidence"]["status"], "pending")
            self.assertEqual(payload["accounting"], {
                "source_bytes": sum(map(len, payloads.values())),
                "candidate_bytes": sum(map(len, payloads.values())) - len(b"dx80"),
                "saved_dx80_bytes": len(b"dx80"),
            })
            record = BenchmarkRecord.from_dict(payload["records"][0])
            self.assertEqual(record.lane, "dx90_optional")
            self.assertEqual(record.gates["runtime"], "not_run")
            self.assertEqual(payload["summary"]["quality_status"], "unverified")
            output = root / "experiment.json"
            write_dx90_optional_experiment(output, payload)
            self.assertTrue(output.read_bytes().endswith(b"\n"))
            with self.assertRaisesRegex(Dx90PolicyError, "already exists"):
                write_dx90_optional_experiment(output, payload)


if __name__ == "__main__":
    unittest.main()
