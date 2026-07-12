from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import math
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

from maximum_optimizer.benchmarking import (
    BASELINE_LANES,
    ArtifactDeclaration,
    BenchmarkRecord,
    CorpusError,
    StrictControlRoundtripAdapter,
    build_cache_key,
    canonical_json_bytes,
    import_compiled_baseline,
    load_corpus,
    parse_control_results,
    portable_compiler_error,
    summarize_records,
    find_stem_sidecars,
    preflight_control_layout,
)


SHA = "0" * 64


def _file(path: Path, data: bytes = b"x") -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "path": path.name,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class BenchmarkCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.original = self.root / "original"
        self.source.mkdir()
        self.original.mkdir()
        (self.source / "family.qc").write_text("$modelname family.mdl\n", encoding="utf-8")
        artifacts = []
        for name in ("family.mdl", "family.vvd", "family.dx80.vtx", "family.dx90.vtx", "family.phy"):
            artifacts.append(_file(self.original / name))
        source_data = (self.source / "family.qc").read_bytes()
        self.payload = {
            "schema_version": 1,
            "corpus_id": "lvs-models-v1",
            "roots": {
                "source": {"env": "TEST_LVS_SOURCE"},
                "original": {"env": "TEST_LVS_ORIGINAL"},
            },
            "partitions": {"pressure": ["dodge_charger"], "full": ["dodge_charger"]},
            "families": [{
                "id": "dodge_charger",
                "display_name": "Dodge Charger",
                "source_qc": "family.qc",
                "source_files": [{
                    "path": "family.qc", "size_bytes": len(source_data),
                    "sha256": hashlib.sha256(source_data).hexdigest(),
                }],
                "compiled_stem": "family",
                "baselines": {"original": artifacts},
            }],
        }
        self.corpus_file = self.root / "corpus.json"
        self.corpus_file.write_text(json.dumps(self.payload), encoding="utf-8")
        self.env = {"TEST_LVS_SOURCE": str(self.source), "TEST_LVS_ORIGINAL": str(self.original)}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_loads_parameterized_corpus_and_verifies_hashes(self):
        corpus = load_corpus(self.corpus_file, environ=self.env, expected_family_ids=("dodge_charger",))
        self.assertEqual(("dodge_charger",), corpus.pressure_ids)
        self.assertEqual(("dodge_charger",), corpus.full_ids)
        self.assertEqual(self.source.resolve(), corpus.roots["source"])

    def test_rejects_absolute_committed_root_and_unexpanded_or_missing_environment(self):
        self.payload["roots"]["source"] = {"env": str(self.source)}
        self.corpus_file.write_text(json.dumps(self.payload), encoding="utf-8")
        with self.assertRaisesRegex(CorpusError, "environment variable name"):
            load_corpus(self.corpus_file, environ=self.env, expected_family_ids=("dodge_charger",))

        self.payload["roots"]["source"] = {"env": "MISSING_LVS_ROOT"}
        self.corpus_file.write_text(json.dumps(self.payload), encoding="utf-8")
        with self.assertRaisesRegex(CorpusError, "MISSING_LVS_ROOT"):
            load_corpus(self.corpus_file, environ=self.env, expected_family_ids=("dodge_charger",))

    def test_rejects_partition_drift_path_escape_hash_drift_and_unknown_sidecar(self):
        for mutate, pattern in (
            (lambda p: p["partitions"].update(pressure=[]), "pressure partition"),
            (lambda p: p["families"][0].update(source_qc="../family.qc"), "relative path"),
            (lambda p: p["families"][0]["source_files"][0].update(sha256=SHA), "hash mismatch"),
        ):
            payload = json.loads(json.dumps(self.payload))
            mutate(payload)
            self.corpus_file.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(CorpusError, pattern):
                load_corpus(self.corpus_file, environ=self.env, expected_family_ids=("dodge_charger",))

        (self.original / "family.ani").write_bytes(b"surprise")
        self.corpus_file.write_text(json.dumps(self.payload), encoding="utf-8")
        with self.assertRaisesRegex(CorpusError, "sidecar set mismatch"):
            load_corpus(self.corpus_file, environ=self.env, expected_family_ids=("dodge_charger",))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_rejects_symlink_or_reparse_components(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.source / "linked"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        self.payload["families"][0]["source_qc"] = "linked/family.qc"
        self.corpus_file.write_text(json.dumps(self.payload), encoding="utf-8")
        with self.assertRaisesRegex(CorpusError, "reparse|symlink"):
            load_corpus(self.corpus_file, environ=self.env, expected_family_ids=("dodge_charger",))


class BenchmarkRecordTests(unittest.TestCase):
    def test_record_is_immutable_and_decomposes_dx80(self):
        record = BenchmarkRecord.create(
            corpus_id="lvs-models-v1", family_id="dodge_charger", lane="blender",
            strategy="blender-050", cache_key="1" * 64,
            artifacts={".mdl": 100, ".vvd": 200, ".dx80.vtx": 40, ".dx90.vtx": 60, ".phy": 10},
            provenance={"tool": {"sha256": "2" * 64}, "scripts": {"runner.py": "3" * 64}, "settings": {"ratio": "0.50"}},
            gates={"structural": "not_run", "visual": "not_run", "runtime": "not_run"},
        )
        self.assertEqual(410, record.total_bytes)
        self.assertEqual(370, record.geometry_comparable_bytes)
        self.assertEqual(40, record.dx80_optional_bytes)
        with self.assertRaises(FrozenInstanceError):
            record.total_bytes = 1  # type: ignore[misc]

        payload = record.to_dict()
        self.assertEqual(record, BenchmarkRecord.from_dict(payload))
        payload["total_bytes"] = 999
        with self.assertRaisesRegex(ValueError, "derived byte totals"):
            BenchmarkRecord.from_dict(payload)
        for field, value in (("total_bytes", 410.0), ("geometry_comparable_bytes", True), ("dx80_optional_bytes", 40.0)):
            tampered = record.to_dict()
            tampered[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                BenchmarkRecord.from_dict(tampered)

    def test_record_schema_rejects_unknown_fields_and_incomplete_provenance(self):
        record = BenchmarkRecord.create(
            corpus_id="lvs-models-v1", family_id="car", lane="original", strategy="original",
            cache_key="4" * 64, artifacts={".mdl": 1},
            provenance={"tool": {}, "scripts": {}, "settings": {}},
            gates={"structural": "not_run", "visual": "not_run", "runtime": "not_run"},
        )
        payload = record.to_dict()
        payload["extra"] = True
        with self.assertRaisesRegex(ValueError, "record keys"):
            BenchmarkRecord.from_dict(payload)
        with self.assertRaisesRegex(ValueError, "provenance"):
            BenchmarkRecord.create(
                corpus_id="lvs-models-v1", family_id="car", lane="original", strategy="original",
                cache_key="4" * 64, artifacts={".mdl": 1}, provenance={},
                gates={"structural": "not_run", "visual": "not_run", "runtime": "not_run"},
            )

    def test_cache_key_changes_for_source_tool_script_or_settings(self):
        base = {"source": "a" * 64, "tools": {"studiomdl": "b" * 64}, "scripts": {"runner.py": "c" * 64}, "settings": {"ratio": "0.5"}}
        keys = []
        for field, value in (("source", "d" * 64), ("tools", {"studiomdl": "e" * 64}), ("scripts", {"runner.py": "f" * 64}), ("settings", {"ratio": "0.4"})):
            payload = dict(base)
            payload[field] = value
            keys.append(build_cache_key(**payload))
        self.assertEqual(4, len(set(keys)))
        self.assertNotIn(build_cache_key(**base), keys)
        with self.assertRaisesRegex(ValueError, "tools.*SHA-256"):
            build_cache_key(source="a" * 64, tools={"studiomdl": "bad"}, scripts={}, settings={})
        with self.assertRaisesRegex(ValueError, "logical relative path"):
            build_cache_key(source="a" * 64, tools={}, scripts={}, settings={"source_path": "C:/source.qc"})

    def test_canonical_json_is_stable_and_rejects_non_finite_numbers(self):
        self.assertEqual(b'{"a":2,"z":1}\n', canonical_json_bytes({"z": 1, "a": 2}))
        with self.assertRaises(ValueError):
            canonical_json_bytes({"bad": float("nan")})

    def test_summary_separates_lanes_and_does_not_claim_quality_without_gates(self):
        records = tuple(
            BenchmarkRecord.create(
                corpus_id="lvs-models-v1", family_id="family", lane=lane,
                strategy=lane, cache_key=f"{index + 1:064x}", artifacts={".vvd": 100 - index, ".dx80.vtx": 10},
                provenance={"tool": {}, "scripts": {}, "settings": {}},
                gates={"structural": "not_run", "visual": "not_run", "runtime": "not_run"},
            )
            for index, lane in enumerate(BASELINE_LANES)
        )
        report = summarize_records(records)
        self.assertEqual(set(BASELINE_LANES), set(report["lanes"]))
        self.assertEqual("unverified", report["quality_status"])
        self.assertIn("median_geometry_comparable_bytes", report["lanes"]["original"])
        self.assertIn("worst_geometry_comparable_bytes", report["lanes"]["original"])

    def test_record_rejects_wrong_scalar_types_nonfinite_elapsed_and_bad_strings(self):
        kwargs = dict(
            corpus_id="lvs-models-v1", family_id="car", lane="original", strategy="original",
            cache_key="4" * 64, artifacts={".mdl": 1},
            provenance={"tool": {}, "scripts": {}, "settings": {}},
            gates={"structural": "not_run", "visual": "not_run", "runtime": "not_run"},
        )
        for change, pattern in (
            ({"family_id": "../car"}, "family_id"),
            ({"strategy": ""}, "strategy"),
            ({"elapsed_seconds": float("inf")}, "elapsed_seconds"),
            ({"elapsed_seconds": -1.0}, "elapsed_seconds"),
            ({"failure": 3}, "failure"),
            ({"artifacts": {".mdl": True}}, "artifact"),
        ):
            with self.subTest(change=change), self.assertRaisesRegex((ValueError, TypeError), pattern):
                BenchmarkRecord.create(**{**kwargs, **change})

        raw = BenchmarkRecord.create(**kwargs).to_dict()
        raw["schema_version"] = True
        with self.assertRaisesRegex(ValueError, "schema_version"):
            BenchmarkRecord.from_dict(raw)

    def test_provenance_is_recursive_json_only_frozen_and_validates_digests_and_paths(self):
        provenance = {
            "tool": {"studiomdl_sha256": "a" * 64},
            "scripts": {"runner.py": "b" * 64},
            "settings": {"nested": [{"enabled": True, "ratio": 0.5}], "source_path": "logical/input.qc"},
        }
        record = BenchmarkRecord.create(
            corpus_id="lvs-models-v1", family_id="car", lane="original", strategy="original",
            cache_key="4" * 64, artifacts={".mdl": 1}, provenance=provenance,
            gates={"structural": "not_run", "visual": "not_run", "runtime": "not_run"},
        )
        provenance["settings"]["nested"][0]["ratio"] = 0.1
        self.assertEqual(0.5, record.provenance["settings"]["nested"][0]["ratio"])
        with self.assertRaises(TypeError):
            record.provenance["settings"]["nested"][0]["ratio"] = 0.2
        for bad, pattern in (
            ({"tool": {"sha256": "bad"}, "scripts": {}, "settings": {}}, "digest"),
            ({"tool": {}, "scripts": {}, "settings": {"source_path": "C:/secret.qc"}}, "logical relative path"),
            ({"tool": {}, "scripts": {}, "settings": {"bad": {1, 2}}}, "JSON"),
            ({"tool": {}, "scripts": {}, "settings": {"bad": float("nan")}}, "finite"),
        ):
            with self.subTest(bad=bad), self.assertRaisesRegex((ValueError, TypeError), pattern):
                BenchmarkRecord.create(
                    corpus_id="lvs-models-v1", family_id="car", lane="original", strategy="original",
                    cache_key="4" * 64, artifacts={".mdl": 1}, provenance=bad,
                    gates={"structural": "not_run", "visual": "not_run", "runtime": "not_run"},
                )

    def test_summary_rejects_duplicate_mixed_or_incomplete_records_and_excludes_failures(self):
        def record(family: str, *, failure: str | None = None, gates: str = "pass") -> BenchmarkRecord:
            return BenchmarkRecord.create(
                corpus_id="lvs-models-v1", family_id=family, lane="original", strategy="original",
                cache_key=hashlib.sha256(family.encode()).hexdigest(), artifacts={".mdl": 10, ".dx80.vtx": 3},
                provenance={"tool": {}, "scripts": {}, "settings": {}},
                gates={"structural": gates, "visual": gates, "runtime": gates}, failure=failure,
            )
        good, failed = record("a"), record("b", failure="compile failed")
        report = summarize_records((good, failed), expected_family_ids=("a", "b"), required_lanes=("original",))
        self.assertEqual(10, report["lanes"]["original"]["median_geometry_comparable_bytes"])
        self.assertEqual(3, report["lanes"]["original"]["total_dx80_optional_bytes"])
        self.assertEqual(1, report["lanes"]["original"]["failure_count"])
        self.assertEqual("unverified", report["quality_status"])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            summarize_records((good, good), expected_family_ids=("a",), required_lanes=("original",))
        mixed = BenchmarkRecord.create(
            corpus_id="other", family_id="a", lane="blender", strategy="x", cache_key="5" * 64,
            artifacts={".mdl": 1}, provenance={"tool": {}, "scripts": {}, "settings": {}},
            gates={"structural": "pass", "visual": "pass", "runtime": "pass"})
        with self.assertRaisesRegex(ValueError, "corpus"):
            summarize_records((good, mixed))
        with self.assertRaisesRegex(ValueError, "missing"):
            summarize_records((good,), expected_family_ids=("a", "b"), required_lanes=("original",))


class ControlSchemaTests(unittest.TestCase):
    def _results(self) -> list[dict[str, object]]:
        return [{
            "family_id": family_id, "status": "compiled", "returncode": 0,
            "elapsed_seconds": 1.0, "log": f"{family_id}/studiomdl.log",
            "autofixes": False, "source_mutated": False,
        } for family_id in ("a", "b")]

    def test_control_parser_requires_exact_unique_ordered_complete_evidence(self):
        payload = {"schema_version": 1, "corpus_id": "lvs-models-v1", "results": self._results()}
        parsed = parse_control_results(payload, expected_family_ids=("a", "b"))
        self.assertEqual(("a", "b"), tuple(item["family_id"] for item in parsed))
        cases = []
        duplicate = self._results(); duplicate[1]["family_id"] = "a"; cases.append((duplicate, "duplicate"))
        reversed_results = list(reversed(self._results())); cases.append((reversed_results, "order"))
        missing = self._results()[:1]; cases.append((missing, "complete"))
        for results, pattern in cases:
            with self.subTest(pattern=pattern), self.assertRaisesRegex(ValueError, pattern):
                parse_control_results({**payload, "results": results}, expected_family_ids=("a", "b"))

    def test_control_parser_rejects_tampered_scalars_flags_paths_and_extra_fields(self):
        base = self._results()
        mutations = (
            ("status", "success", "status"), ("returncode", True, "returncode"),
            ("elapsed_seconds", math.nan, "elapsed"), ("autofixes", True, "autofixes"),
            ("source_mutated", True, "source_mutated"), ("log", "C:/leak.log", "logical relative path"),
        )
        for key, value, pattern in mutations:
            results = json.loads(json.dumps(base))
            results[0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, pattern):
                parse_control_results({"schema_version": 1, "corpus_id": "lvs-models-v1", "results": results}, expected_family_ids=("a", "b"))
        results = self._results(); results[0]["extra"] = 1
        with self.assertRaisesRegex(ValueError, "keys"):
            parse_control_results({"schema_version": 1, "corpus_id": "lvs-models-v1", "results": results}, expected_family_ids=("a", "b"))

    def test_control_adapter_uses_private_copy_and_never_mutates_source(self):
        import sys
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"; source.mkdir()
            qc = source / "family.qc"; qc.write_text("original", encoding="utf-8")
            game = root / "game"; game.mkdir()
            run = root / "run"
            declaration = ArtifactDeclaration("family.qc", qc.stat().st_size, hashlib.sha256(qc.read_bytes()).hexdigest())
            adapter = StrictControlRoundtripAdapter(Path(sys.executable), game)
            result = adapter.compile(source_root=source, source_files=(declaration,), source_qc="family.qc", run_root=run, family_id="family")
            copied = run / "family" / "family.qc"
            copied.write_text("changed", encoding="utf-8")
            self.assertEqual("original", qc.read_text(encoding="utf-8"))
            self.assertFalse(result["autofixes"])
            self.assertFalse(result["source_mutated"])

    def test_control_layout_preflight_rejects_reparse_before_any_write(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            containment = root / ".superpowers"; containment.mkdir()
            jump = containment / "jump"; jump.mkdir()
            external = root / "external"; external.mkdir()
            original_guard = __import__("maximum_optimizer.benchmarking", fromlist=["_is_reparse"])._is_reparse

            def guarded(path: Path) -> bool:
                return path == jump or original_guard(path)

            with mock.patch("maximum_optimizer.benchmarking._is_reparse", side_effect=guarded):
                with self.assertRaisesRegex(CorpusError, "reparse"):
                    preflight_control_layout(jump / "run", containment, ("a", "b"))
            self.assertFalse((external / "gameinfo.txt").exists())
            self.assertFalse((jump / "run").exists())

    def test_failed_control_sidecar_scan_is_exact_and_covers_all_vtx_variants(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            parent = root / "models" / "cars"; parent.mkdir(parents=True)
            for name in ("car.sw.vtx", "car.vtx", "car.dx80.vtx", "car.ani", "car_extra.dx90.vtx"):
                (parent / name).write_bytes(b"x")
            found = {path.name for path in find_stem_sidecars(root / "models", "cars/car")}
            self.assertEqual({"car.sw.vtx", "car.vtx", "car.dx80.vtx", "car.ani"}, found)


class BaselineImportTests(unittest.TestCase):
    def test_compiler_error_removes_machine_path_but_keeps_qc_line_and_message(self):
        raw = 'ERROR: C:\\Users\\me\\run\\car.qc(2053): - Duplicate animation name "turn"'
        self.assertEqual('QC line 2053: Duplicate animation name "turn"', portable_compiler_error(raw))

    def test_import_uses_declared_hashes_without_recomputing_other_lanes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name, data in (("car.mdl", b"m"), ("car.vvd", b"vv"), ("car.dx80.vtx", b"8"), ("car.dx90.vtx", b"99"), ("car.phy", b"p")):
                (root / name).write_bytes(data)
            declared = tuple({"path": p.name, "size_bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(root.iterdir()))
            record = import_compiled_baseline(
                corpus_id="lvs-models-v1", family_id="car", lane="fidelity", root=root,
                compiled_stem="car", declared_artifacts=declared,
                provenance={"tool": {}, "scripts": {}, "settings": {}}, source_hash="a" * 64,
            )
            self.assertEqual(7, record.total_bytes)
            self.assertEqual("fidelity", record.lane)


if __name__ == "__main__":
    unittest.main()
