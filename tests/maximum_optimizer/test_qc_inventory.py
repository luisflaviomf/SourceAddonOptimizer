import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maximum_optimizer.qc_inventory import (
    build_family_manifests,
    parse_qc_fingerprint,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "maximum"
MONACO_QC = Path(
    r"C:\Users\luisf\Music\teste\experiments\wpf-mainapp-validation"
    r"\20260312_110200_models_planar\planar_on_work\src\diggercars"
    r"\dodge_monaco\monaco_police\monaco_police.qc"
)


class QcFingerprintTests(unittest.TestCase):
    def test_adjacent_directives_survive_whitespace_comments_quoted_args_and_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qc = root / "adjacent.qc"
            qc.write_text(
                '$modelname "vehicles/quoted model.mdl" // keep quoted whitespace\n'
                '\n'
                '// a comment between adjacent directives\n'
                '$cdmaterials "models\\quoted path\\"\n'
                '$texturegroup "skin families"\n'
                '{\n'
                '    { "skin one" }\n'
                '    { "skin two" }\n'
                '}\n'
                '$attachment "first light" "first bone" 0 0 0\n'
                '$attachment "second light" "second bone" 0 0 0 // trailing comment\n'
                '$sequence "idle sequence"\n'
                '{\n'
                '    "idle animation.smd"\n'
                '}\n'
                '$collisionmodel "physics mesh.smd"\n'
                '{\n'
                '    $mass 1000\n'
                '}\n',
                encoding="utf-8",
            )

            fp = parse_qc_fingerprint(qc)

            self.assertEqual(fp.model_name, "vehicles/quoted model.mdl")
            self.assertEqual(fp.skin_families, (("skin one",), ("skin two",)))
            self.assertEqual(fp.attachments, ("first light", "second light"))
            self.assertEqual(fp.sequences, ("idle sequence",))
            self.assertEqual(fp.physics_mesh, "physics mesh.smd")

    @unittest.skipUnless(MONACO_QC.is_file(), f"real Monaco QC not available: {MONACO_QC}")
    def test_real_monaco_qc_inventories_all_structural_directives(self):
        fp = parse_qc_fingerprint(MONACO_QC)

        self.assertEqual(len(fp.skin_families), 9)
        self.assertEqual(len(fp.attachments), 12)
        self.assertEqual(len(fp.sequences), 22)
        self.assertEqual(fp.physics_mesh, "monaco_police_physics.smd")

    def test_fingerprint_keeps_source_order_without_case_duplicates(self):
        fp = parse_qc_fingerprint(FIXTURES / "family.qc")

        self.assertEqual(fp.model_name, "vehicles/test.mdl")
        self.assertEqual(fp.bodygroups, ("body", "wheels"))
        self.assertEqual(fp.attachments, ("eyes",))
        self.assertEqual(fp.mesh_files, ("reference.smd", "wheel.smd"))
        self.assertEqual(fp.lod_mesh_files, ("wheel.smd",))
        self.assertEqual(fp.physics_mesh, "reference.smd")
        self.assertEqual(fp.sequences, ("idle",))

    def test_fingerprint_populates_skins_materials_bones_and_directives(self):
        fp = parse_qc_fingerprint(FIXTURES / "family.qc")

        self.assertEqual(
            fp.skin_families,
            (("paint/red", "Trim/Black"), ("PAINT/RED", "trim/chrome")),
        )
        self.assertEqual(
            fp.materials,
            ("paint/red", "Trim/Black", "trim/chrome", "Detail/Metal"),
        )
        self.assertEqual(fp.bones, ("root", "spine", "wheel"))
        self.assertEqual(fp.bone_parents, (("spine", "root"), ("wheel", "ROOT")))
        self.assertEqual(
            fp.hitboxes,
            ("$hboxset default", "$hbox 0 root -1 -2 -3 1 2 3"),
        )

    def test_local_includes_are_followed_once_and_comments_inside_quotes_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.qc").write_text(
                '$modelname "models/has//slashes.mdl"\n$include "parts/extra.qci"\n',
                encoding="utf-8",
            )
            parts = root / "parts"
            parts.mkdir()
            (parts / "extra.qci").write_text(
                '$body "included" "extra.smd"\n$include "../main.qc"\n',
                encoding="utf-8",
            )
            (parts / "extra.smd").write_text(
                'version 1\nnodes\n0 "included_root" -1\nend\n',
                encoding="utf-8",
            )

            fp = parse_qc_fingerprint(root / "main.qc")

            self.assertEqual(fp.model_name, "models/has//slashes.mdl")
            self.assertEqual(fp.bodygroups, ("included",))
            self.assertEqual(fp.bones, ("included_root",))

    def test_include_cannot_escape_initial_qc_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            family = root / "family"
            family.mkdir()
            (root / "outside.qci").write_text("$body outside outside.smd\n", encoding="utf-8")
            qc = family / "main.qc"
            qc.write_text('$include "../outside.qci"\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "include"):
                parse_qc_fingerprint(qc)

    def test_model_collisionjoints_and_block_sequence_are_inventory_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qc = root / "aliases.qc"
            qc.write_text(
                '$model "character" "model.smd"\n'
                '$collisionjoints "physics.smd"\n'
                '$sequence "walk"\n{\n    "animation.smd"\n}\n',
                encoding="utf-8",
            )
            for name, bone in (
                ("model.smd", "model_bone"),
                ("physics.smd", "physics_bone"),
                ("animation.smd", "animation_bone"),
            ):
                (root / name).write_text(
                    f'version 1\nnodes\n0 "{bone}" -1\nend\n', encoding="utf-8"
                )

            fp = parse_qc_fingerprint(qc)

            self.assertEqual(fp.bodygroups, ("character",))
            self.assertEqual(fp.physics_mesh, "physics.smd")
            self.assertEqual(fp.sequences, ("walk",))
            self.assertEqual(fp.bones, ("model_bone", "physics_bone", "animation_bone"))

    def test_sequence_reference_does_not_hide_later_mesh_material_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qc = root / "roles.qc"
            qc.write_text(
                '$sequence "idle" "shared.smd"\n$body "body" "shared.smd"\n',
                encoding="utf-8",
            )
            (root / "shared.smd").write_text(
                'version 1\nnodes\n0 "root" -1\nend\ntriangles\nmesh/material\n'
                + "0 0 0 0 0 0 1 0 0\n" * 3
                + "end\n",
                encoding="utf-8",
            )

            fp = parse_qc_fingerprint(qc)

            self.assertEqual(fp.materials, ("mesh/material",))

    def test_absolute_include_and_source_declarations_are_rejected_before_resolution(self):
        declarations = ("/escape", "C:/escape", "//server/share/escape")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, declaration in enumerate(declarations):
                with self.subTest(kind="include", declaration=declaration):
                    qc = root / f"include-{index}.qc"
                    qc.write_text(f'$include "{declaration}.qci"\n', encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "absolute"):
                        parse_qc_fingerprint(qc)
                with self.subTest(kind="source", declaration=declaration):
                    qc = root / f"source-{index}.qc"
                    qc.write_text(f'$body "body" "{declaration}.smd"\n', encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "absolute"):
                        parse_qc_fingerprint(qc)

    def test_compact_blocks_split_each_studio_and_replacemodel_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qc = root / "compact.qc"
            qc.write_text(
                '$bodygroup "parts" { studio "a.smd" studio "b.smd" }\n'
                '$lod 10 { replacemodel "a.smd" "a_lod.smd" replacemodel "b.smd" "b_lod.smd" }\n',
                encoding="utf-8",
            )
            for name in ("a.smd", "b.smd", "a_lod.smd", "b_lod.smd"):
                (root / name).write_text("version 1\n", encoding="utf-8")

            fp = parse_qc_fingerprint(qc)

            self.assertEqual(fp.mesh_files, ("a.smd", "b.smd"))
            self.assertEqual(fp.lod_mesh_files, ("a_lod.smd", "b_lod.smd"))

    def test_duplicate_skin_rows_are_preserved_as_structural_families(self):
        with tempfile.TemporaryDirectory() as tmp:
            qc = Path(tmp) / "skins.qc"
            qc.write_text(
                '$texturegroup "skins" { { "mat/a" } { "MAT/A" } { "mat/a" } }\n',
                encoding="utf-8",
            )

            fp = parse_qc_fingerprint(qc)

            self.assertEqual(fp.skin_families, (("mat/a",), ("MAT/A",), ("mat/a",)))
            self.assertEqual(fp.materials, ("mat/a",))


class FamilyManifestTests(unittest.TestCase):
    def _write_family(self, root, model_rel, source_name="main.qc", material="one"):
        src_root = root / "src"
        src_dir = src_root / Path(model_rel).parent / Path(model_rel).stem
        src_dir.mkdir(parents=True, exist_ok=True)
        qc = src_dir / source_name
        qc.write_text(
            f'$modelname "{model_rel}"\n$body "body" "mesh.smd"\n',
            encoding="utf-8",
        )
        (src_dir / "mesh.smd").write_text(
            f'version 1\nnodes\n0 "root" -1\nend\ntriangles\n{material}\n'
            + "0 0 0 0 0 0 1 0 0\n" * 3
            + "end\n",
            encoding="utf-8",
        )
        return src_root, src_dir, qc

    @staticmethod
    def _write_manifest(path, results):
        path.write_text(json.dumps({"results": results}), encoding="utf-8")

    def test_builds_only_ok_records_with_stable_ids_artifacts_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            src_root, zulu_src, zulu_qc = self._write_family(root, "zulu/test.mdl")
            _, alpha_src, alpha_qc = self._write_family(root, "Alpha/other.mdl")
            for name in (
                "test.mdl",
                "test.vvd",
                "test.ani",
                "test.phy",
                "test.dx90.vtx",
                "test.dx80.vtx",
                "test.vtx",
                "test.sw.vtx",
                "not-test.mdl",
            ):
                path = models / "zulu" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode("ascii"))
            (models / "Alpha").mkdir()
            (models / "Alpha" / "other.mdl").write_bytes(b"other")
            manifest_path = root / "decompile_manifest.json"
            self._write_manifest(
                manifest_path,
                [
                    {
                        "status": "ok",
                        "model_rel": "zulu\\test.mdl",
                        "src_dir": str(zulu_src),
                        "qc_chosen": str(Path("stale", zulu_qc.name)),
                    },
                    {"status": "failed", "model_rel": "ignored.mdl"},
                    {
                        "status": "ok",
                        "model_rel": "Alpha/other.mdl",
                        "src_dir": str(alpha_src),
                        "qc_chosen": str(alpha_qc),
                    },
                ],
            )

            manifests = build_family_manifests(models, manifest_path, src_root)

            self.assertEqual(tuple(item.model_rel for item in manifests), ("Alpha/other.mdl", "zulu/test.mdl"))
            zulu = manifests[1]
            self.assertEqual(
                zulu.family_id,
                hashlib.sha256("zulu/test.mdl".casefold().encode("utf-8")).hexdigest(),
            )
            self.assertEqual(
                zulu.required_artifact_kinds,
                (".ani", ".dx80.vtx", ".dx90.vtx", ".mdl", ".phy", ".vtx", ".vvd"),
            )
            self.assertEqual(zulu.source_dir, zulu_src.resolve())
            self.assertEqual(zulu.original_models_dir, models.resolve())

    def test_input_hash_changes_with_referenced_source_and_original_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            original = models / "family" / "test.mdl"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"original-one")
            src_root, src_dir, qc = self._write_family(root, "family/test.mdl")
            included = src_dir / "extra.qci"
            included.write_text('$sequence "idle" "anim.dmx"\n', encoding="utf-8")
            qc.write_text(qc.read_text(encoding="utf-8") + '$include "extra.qci"\n', encoding="utf-8")
            (src_dir / "anim.dmx").write_bytes(b"dmx-one")
            manifest_path = root / "decompile_manifest.json"
            self._write_manifest(
                manifest_path,
                [{"status": "ok", "model_rel": "family/test.mdl", "src_dir": str(src_dir), "qc_chosen": str(qc)}],
            )

            first = build_family_manifests(models, manifest_path, src_root)[0].input_hash
            (src_dir / "mesh.smd").write_text(
                (src_dir / "mesh.smd").read_text(encoding="utf-8") + "// changed\n",
                encoding="utf-8",
            )
            source_changed = build_family_manifests(models, manifest_path, src_root)[0].input_hash
            original.write_bytes(b"original-two")
            original_changed = build_family_manifests(models, manifest_path, src_root)[0].input_hash

            self.assertNotEqual(first, source_changed)
            self.assertNotEqual(source_changed, original_changed)

    def test_falls_back_to_first_qc_and_rejects_source_outside_src_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            src_root, src_dir, _ = self._write_family(root, "family/test.mdl", "z-last.qc")
            (src_dir / "a-first.qc").write_text(
                '$modelname "family/test.mdl"\n$body first mesh.smd\n', encoding="utf-8"
            )
            manifest_path = root / "decompile_manifest.json"
            self._write_manifest(
                manifest_path,
                [{"status": "ok", "model_rel": "family/test.mdl", "src_dir": str(src_dir), "qc_chosen": "missing.qc"}],
            )
            self.assertEqual(
                build_family_manifests(models, manifest_path, src_root)[0].fingerprint.bodygroups,
                ("first",),
            )

            outside = root / "outside"
            outside.mkdir()
            (outside / "outside.qc").write_text('$modelname "bad.mdl"\n', encoding="utf-8")
            self._write_manifest(
                manifest_path,
                [{"status": "ok", "model_rel": "bad.mdl", "src_dir": str(outside), "qc_chosen": "outside.qc"}],
            )
            with self.assertRaisesRegex(ValueError, "src_root"):
                build_family_manifests(models, manifest_path, src_root)

    def test_manifest_uses_source_dir_as_family_root_for_nested_qc_and_shared_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            (models / "family").mkdir(parents=True)
            (models / "family" / "test.mdl").write_bytes(b"mdl")
            src_root = root / "src"
            source_dir = src_root / "family" / "test"
            nested = source_dir / "qc"
            shared = source_dir / "shared"
            nested.mkdir(parents=True)
            shared.mkdir()
            qc = nested / "main.qc"
            qc.write_text('$modelname "family/test.mdl"\n$include "../shared/body.qci"\n', encoding="utf-8")
            (shared / "body.qci").write_text('$body "shared" "shared.smd"\n', encoding="utf-8")
            (shared / "shared.smd").write_text(
                'version 1\nnodes\n0 "shared_root" -1\nend\n', encoding="utf-8"
            )
            manifest_path = root / "decompile_manifest.json"
            self._write_manifest(
                manifest_path,
                [{"status": "ok", "model_rel": "family/test.mdl", "src_dir": str(source_dir), "qc_chosen": str(qc)}],
            )

            manifest = build_family_manifests(models, manifest_path, src_root)[0]

            self.assertEqual(manifest.fingerprint.bodygroups, ("shared",))
            self.assertEqual(manifest.fingerprint.bones, ("shared_root",))

    def test_manifest_rejects_posix_drive_and_unc_model_paths(self):
        invalid_paths = ("/escape.mdl", "C:/escape.mdl", "//server/share/escape.mdl")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            src_root, src_dir, qc = self._write_family(root, "safe/test.mdl")
            manifest_path = root / "decompile_manifest.json"
            for model_rel in invalid_paths:
                with self.subTest(model_rel=model_rel):
                    self._write_manifest(
                        manifest_path,
                        [{"status": "ok", "model_rel": model_rel, "src_dir": str(src_dir), "qc_chosen": str(qc)}],
                    )
                    with self.assertRaisesRegex(ValueError, "model_rel"):
                        build_family_manifests(models, manifest_path, src_root)

    def test_records_without_model_rel_sort_by_model_rel_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            src_root, zulu_src, zulu_qc = self._write_family(root, "zulu/test.mdl")
            _, alpha_src, alpha_qc = self._write_family(root, "alpha/test.mdl")
            manifest_path = root / "decompile_manifest.json"
            self._write_manifest(
                manifest_path,
                [
                    {
                        "status": "ok",
                        "model_rel_fallback": "zulu/test.mdl",
                        "src_dir": str(zulu_src),
                        "qc_chosen": str(zulu_qc),
                    },
                    {
                        "status": "ok",
                        "model_rel_fallback": "alpha/test.mdl",
                        "src_dir": str(alpha_src),
                        "qc_chosen": str(alpha_qc),
                    },
                ],
            )

            manifests = build_family_manifests(models, manifest_path, src_root)

            self.assertEqual(tuple(item.model_rel for item in manifests), ("alpha/test.mdl", "zulu/test.mdl"))


if __name__ == "__main__":
    unittest.main()
