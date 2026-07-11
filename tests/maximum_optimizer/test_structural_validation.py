from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from maximum_optimizer.domain import FamilyManifest, StructuralFingerprint
from maximum_optimizer.qc_inventory import parse_qc_fingerprint
from maximum_optimizer.structural_validation import validate_structure


FIXTURES = Path(__file__).parents[1] / "fixtures" / "maximum"
ARTIFACT_PATHS = {
    ".mdl": "vehicles/test.mdl",
    ".vvd": "vehicles/test.vvd",
    ".ani": "vehicles/test.ani",
    ".phy": "vehicles/test.phy",
    ".dx90.vtx": "vehicles/test.dx90.vtx",
    ".dx80.vtx": "vehicles/test.dx80.vtx",
    ".vtx": "vehicles/test.vtx",
}


def _case_variant(value):
    if isinstance(value, str):
        return value.swapcase()
    return tuple(_case_variant(item) for item in value)


class StructuralValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sources = self.root / "candidate-sources"
        self.sources.mkdir()
        for fixture in FIXTURES.iterdir():
            if fixture.is_file():
                shutil.copy2(fixture, self.sources / fixture.name)
        self.candidate_qc = self.sources / "family.qc"
        self.models = self.root / "candidate" / "models"
        for relative_path in ARTIFACT_PATHS.values():
            artifact = self.models / Path(relative_path)
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(relative_path.encode("ascii"))

        fingerprint = parse_qc_fingerprint(self.candidate_qc)
        self.manifest = FamilyManifest(
            family_id="family",
            model_rel="vehicles/test.mdl",
            source_dir=self.sources,
            original_models_dir=self.root / "original" / "models",
            fingerprint=fingerprint,
            input_hash="hash",
            required_artifact_kinds=tuple(ARTIFACT_PATHS),
        )
        self.compile_record = {"status": "ok", "model_rel": "VEHICLES/TEST.MDL"}
        self.provenance = {
            relative_path: "candidate-compile"
            for relative_path in ARTIFACT_PATHS.values()
        }

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, **overrides):
        arguments = {
            "manifest": self.manifest,
            "candidate_qc": self.candidate_qc,
            "candidate_models_dir": self.models,
            "compile_record": self.compile_record,
            "provenance": self.provenance,
        }
        arguments.update(overrides)
        return validate_structure(**arguments)

    def test_matching_candidate_passes_with_zero_failures(self):
        result = self.validate()

        self.assertTrue(result.passed)
        self.assertEqual(result.failures, ())
        self.assertEqual(result.metrics, {"failure_count": 0.0})
        self.assertEqual(result.worst_scope, "")

    def test_every_semantic_field_is_a_case_insensitive_exact_gate(self):
        baseline = self.manifest.fingerprint
        changed_values = {
            "model_name": "vehicles/other.mdl",
            "bodygroups": baseline.bodygroups + ("spoiler",),
            "materials": baseline.materials + ("paint/extra",),
            "skin_families": baseline.skin_families + (("paint/extra",),),
            "bones": baseline.bones + ("extra_bone",),
            "bone_parents": baseline.bone_parents + (("extra_bone", "root"),),
            "attachments": baseline.attachments + ("camera",),
            "hitboxes": baseline.hitboxes + ("$hbox 1 root 0 0 0 1 1 1",),
            "sequences": baseline.sequences + ("drive",),
        }

        for field_name, changed in changed_values.items():
            with self.subTest(field=field_name):
                manifest = replace(
                    self.manifest,
                    fingerprint=replace(baseline, **{field_name: changed}),
                )
                result = self.validate(manifest=manifest)

                self.assertFalse(result.passed)
                self.assertEqual([failure.gate for failure in result.failures], [field_name])

    def test_semantic_comparison_accepts_case_changes_in_all_fields(self):
        baseline = self.manifest.fingerprint
        semantic_fields = (
            "model_name",
            "bodygroups",
            "materials",
            "skin_families",
            "bones",
            "bone_parents",
            "attachments",
            "hitboxes",
            "sequences",
        )
        changed = {
            field_name: _case_variant(getattr(baseline, field_name))
            for field_name in semantic_fields
        }
        manifest = replace(self.manifest, fingerprint=replace(baseline, **changed))

        self.assertTrue(self.validate(manifest=manifest).passed)

    def test_semantic_tuple_and_row_order_is_preserved(self):
        baseline = self.manifest.fingerprint
        ordered_fields = (
            "bodygroups",
            "materials",
            "skin_families",
            "bones",
            "bone_parents",
            "hitboxes",
        )
        for field_name in ordered_fields:
            with self.subTest(field=field_name):
                original = getattr(baseline, field_name)
                reordered = (original[1], original[0], *original[2:])
                manifest = replace(
                    self.manifest,
                    fingerprint=replace(baseline, **{field_name: reordered}),
                )

                result = self.validate(manifest=manifest)

                self.assertIn(field_name, {failure.gate for failure in result.failures})

    def test_single_value_fields_and_values_within_skin_rows_preserve_order(self):
        ordered_qc = self.sources / "ordered.qc"
        ordered_qc.write_text(
            self.candidate_qc.read_text(encoding="utf-8")
            + '$attachment "camera" "root" 0 0 0\n'
            + '$sequence "drive" "reference.smd"\n',
            encoding="utf-8",
        )
        candidate = parse_qc_fingerprint(ordered_qc)
        changed_values = {
            "attachments": (candidate.attachments[1], candidate.attachments[0]),
            "sequences": (candidate.sequences[1], candidate.sequences[0]),
            "skin_families": (
                (candidate.skin_families[0][1], candidate.skin_families[0][0]),
                *candidate.skin_families[1:],
            ),
        }

        for field_name, changed in changed_values.items():
            with self.subTest(field=field_name):
                manifest = replace(
                    self.manifest,
                    fingerprint=replace(candidate, **{field_name: changed}),
                )

                result = self.validate(manifest=manifest, candidate_qc=ordered_qc)

                self.assertEqual([failure.gate for failure in result.failures], [field_name])

    def test_source_renames_are_allowed_when_role_counts_and_semantics_match(self):
        qc_text = self.candidate_qc.read_text(encoding="utf-8")
        qc_text = (
            qc_text.replace("reference.smd", "reference_OPT.smd")
            .replace("wheel.smd", "wheel_OPT.smd")
            .replace("WHEEL.smd", "WHEEL_OPT.smd")
        )
        renamed_qc = self.sources / "renamed.qc"
        renamed_qc.write_text(qc_text, encoding="utf-8")
        shutil.copy2(self.sources / "reference.smd", self.sources / "reference_OPT.smd")
        shutil.copy2(self.sources / "wheel.smd", self.sources / "wheel_OPT.smd")

        result = self.validate(candidate_qc=renamed_qc)

        self.assertTrue(result.passed)

    def test_mesh_role_counts_are_hard_gates(self):
        baseline = self.manifest.fingerprint
        for field_name in ("mesh_files", "lod_mesh_files"):
            with self.subTest(field=field_name):
                expected = getattr(baseline, field_name) + ("", "another_OPT.smd")
                manifest = replace(
                    self.manifest,
                    fingerprint=replace(baseline, **{field_name: expected}),
                )

                result = self.validate(manifest=manifest)

                self.assertEqual([failure.gate for failure in result.failures], [field_name])

    def test_physics_mesh_compares_presence_not_filename(self):
        baseline = self.manifest.fingerprint
        renamed = replace(baseline, physics_mesh="physics_OPT.smd")
        self.assertTrue(
            self.validate(
                manifest=replace(self.manifest, fingerprint=renamed)
            ).passed
        )

        absent = replace(baseline, physics_mesh=None)
        result = self.validate(manifest=replace(self.manifest, fingerprint=absent))
        self.assertEqual([failure.gate for failure in result.failures], ["physics_mesh"])

        qc_without_physics = self.sources / "no-physics.qc"
        qc_without_physics.write_text(
            "\n".join(
                line
                for line in self.candidate_qc.read_text(encoding="utf-8").splitlines()
                if not line.casefold().startswith("$collisionmodel")
            ),
            encoding="utf-8",
        )
        result = self.validate(candidate_qc=qc_without_physics)
        self.assertEqual([failure.gate for failure in result.failures], ["physics_mesh"])

    def test_each_required_compiled_artifact_uses_the_model_relative_stem(self):
        for kind, relative_path in ARTIFACT_PATHS.items():
            with self.subTest(kind=kind):
                artifact = self.models / Path(relative_path)
                original = artifact.read_bytes()
                artifact.unlink()

                result = self.validate()

                missing = [failure for failure in result.failures if failure.gate == "missing_artifact"]
                self.assertEqual(len(missing), 1)
                self.assertEqual(missing[0].scope, relative_path)
                artifact.write_bytes(original)

    def test_original_artifact_outside_candidate_workspace_cannot_satisfy_gate(self):
        candidate_mdl = self.models / "vehicles" / "test.mdl"
        candidate_mdl.unlink()
        original_mdl = self.manifest.original_models_dir / "vehicles" / "test.mdl"
        original_mdl.parent.mkdir(parents=True)
        original_mdl.write_bytes(b"original")

        result = self.validate()

        missing = [failure for failure in result.failures if failure.gate == "missing_artifact"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].scope, "vehicles/test.mdl")

    def test_compile_status_and_model_are_independent_accumulating_gates(self):
        records = (
            ({"status": "failed", "model_rel": self.manifest.model_rel}, {"compile_status"}),
            ({"status": "ok", "model_rel": "vehicles/other.mdl"}, {"compile_model"}),
            ({}, {"compile_status", "compile_model"}),
            (None, {"compile_status", "compile_model"}),
        )
        for record, expected in records:
            with self.subTest(record=record):
                gates = {failure.gate for failure in self.validate(compile_record=record).failures}
                self.assertEqual(gates, expected)

    def test_missing_provenance_is_a_hard_failure(self):
        provenance = dict(self.provenance)
        provenance.pop("vehicles/test.ani")

        result = self.validate(provenance=provenance)

        self.assertIn("missing_provenance", {failure.gate for failure in result.failures})

    def test_only_exact_candidate_compile_provenance_is_accepted(self):
        for value in ("original-copy", "candidate-Compile", "", None):
            with self.subTest(value=value):
                provenance = dict(self.provenance)
                provenance["vehicles/test.mdl"] = value

                result = self.validate(provenance=provenance)

                self.assertIn("hidden_fallback", {failure.gate for failure in result.failures})

    def test_all_failures_accumulate_and_metrics_describe_the_result(self):
        baseline = self.manifest.fingerprint
        manifest = replace(
            self.manifest,
            fingerprint=replace(
                baseline,
                bodygroups=baseline.bodygroups + ("missing",),
                sequences=baseline.sequences + ("missing",),
            ),
        )
        (self.models / "vehicles" / "test.phy").unlink()
        provenance = dict(self.provenance)
        provenance.pop("vehicles/test.ani")
        provenance["vehicles/test.mdl"] = "original-copy"

        result = self.validate(
            manifest=manifest,
            compile_record={"status": "failed", "model_rel": "wrong.mdl"},
            provenance=provenance,
        )

        self.assertEqual(
            [failure.gate for failure in result.failures],
            [
                "compile_status",
                "compile_model",
                "bodygroups",
                "sequences",
                "hidden_fallback",
                "missing_provenance",
                "missing_artifact",
            ],
        )
        self.assertEqual(result.metrics["failure_count"], float(len(result.failures)))
        self.assertEqual(result.worst_scope, result.failures[0].scope)

    def test_absolute_and_traversal_model_paths_are_rejected(self):
        invalid_paths = (
            "../escape.mdl",
            "vehicles/../escape.mdl",
            "/absolute.mdl",
            "C:/absolute.mdl",
            "//server/share/absolute.mdl",
        )
        for model_rel in invalid_paths:
            with self.subTest(model_rel=model_rel):
                manifest = replace(self.manifest, model_rel=model_rel)
                with self.assertRaisesRegex(ValueError, "model_rel"):
                    self.validate(manifest=manifest)


if __name__ == "__main__":
    unittest.main()
