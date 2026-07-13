from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from maximum_optimizer.benchmarking import FULL_FAMILY_IDS, PRESSURE_FAMILY_IDS, CorpusError
from maximum_optimizer.lvs_source_prep import (
    CALIBRATION_FAMILY_IDS,
    HOLDOUT_FAMILY_IDS,
    load_lvs_source_manifest,
    prepare_lvs_source_root,
    verify_prepared_lvs_source_root,
)


def _declaration(path: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class LvsSourcePrepFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.control = self.root / "control"
        self.control.mkdir()
        self.output = self.root / "prepared"
        self.families: list[dict[str, object]] = []
        for index, family_id in enumerate(FULL_FAMILY_IDS):
            relative = f"diggercars/{family_id}/model/model.qc"
            data = f"source-{index}-{family_id}".encode("ascii")
            source = (
                self.control / family_id / "workspace" / family_id
                / Path(*relative.split("/"))
            )
            source.parent.mkdir(parents=True)
            source.write_bytes(data)
            self.families.append({
                "id": family_id,
                "display_name": family_id,
                "source_qc": relative,
                "source_files": [_declaration(relative, data)],
                "compiled_stem": f"diggercars/{family_id}/model/model",
                "baselines": {"original": [_declaration(
                    f"diggercars/{family_id}/model/model.mdl", b"unused"
                )]},
            })
        self.payload = {
            "schema_version": 1,
            "corpus_id": "lvs-models-v1",
            "roots": {
                "source": {"env": "LVS_SOURCE_ROOT"},
                "original": {"env": "LVS_ORIGINAL_MODELS_ROOT"},
                "blender": {"env": "LVS_BLENDER_MODELS_ROOT"},
                "fidelity": {"env": "LVS_FIDELITY_MODELS_ROOT"},
            },
            "partitions": {
                "pressure": list(PRESSURE_FAMILY_IDS),
                "full": list(FULL_FAMILY_IDS),
            },
            "families": self.families,
        }
        self.corpus = self.root / "corpus.json"
        self._write_corpus()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_corpus(self) -> None:
        self.corpus.write_text(json.dumps(self.payload), encoding="utf-8")

    def test_declares_disjoint_canonical_calibration_then_holdout_not_pressure(self):
        self.assertEqual(
            (
                "pontiac_transam_wheel",
                "dodge_charger",
                "toyota_supra",
                "nissan_skyline_gtr32",
                "dodge_monaco_police",
            ),
            CALIBRATION_FAMILY_IDS,
        )
        self.assertEqual(
            (
                "ford_fairlane",
                "vw_beetle",
                "vw_touareg",
                "ferrari_365_fullrig",
                "caterham_620r",
            ),
            HOLDOUT_FAMILY_IDS,
        )
        self.assertNotEqual(PRESSURE_FAMILY_IDS, CALIBRATION_FAMILY_IDS)
        self.assertEqual(set(FULL_FAMILY_IDS), set(CALIBRATION_FAMILY_IDS + HOLDOUT_FAMILY_IDS))
        self.assertFalse(set(CALIBRATION_FAMILY_IDS) & set(HOLDOUT_FAMILY_IDS))

    def test_prepares_all_ten_families_and_revalidates_exact_output(self):
        manifest = load_lvs_source_manifest(self.corpus)
        result = prepare_lvs_source_root(
            manifest=manifest,
            control_root=self.control,
            output_root=self.output,
        )

        self.assertEqual(CALIBRATION_FAMILY_IDS, result.calibration_family_ids)
        self.assertEqual(HOLDOUT_FAMILY_IDS, result.holdout_family_ids)
        self.assertEqual(10, result.file_count)
        self.assertEqual(
            sum(item["source_files"][0]["size_bytes"] for item in self.families),
            result.total_bytes,
        )
        self.assertEqual(result, verify_prepared_lvs_source_root(manifest, self.output))
        self.assertEqual(
            {item["source_files"][0]["path"] for item in self.families},
            {path.relative_to(self.output).as_posix() for path in self.output.rglob("*") if path.is_file()},
        )

    def test_final_verifier_failure_rolls_back_owned_output_and_allows_retry(self):
        from maximum_optimizer import lvs_source_prep as module

        manifest = load_lvs_source_manifest(self.corpus)
        original_verify = module.verify_prepared_lvs_source_root

        def fail_only_published(candidate_manifest, root):
            if Path(root).absolute() == self.output.absolute():
                raise CorpusError("forced final verification failure")
            return original_verify(candidate_manifest, root)

        with mock.patch.object(
            module,
            "verify_prepared_lvs_source_root",
            side_effect=fail_only_published,
        ):
            with self.assertRaisesRegex(
                CorpusError, "forced final verification failure.*preserved"
            ) as raised:
                prepare_lvs_source_root(
                    manifest=manifest,
                    control_root=self.control,
                    output_root=self.output,
                )

        self.assertFalse(os.path.lexists(self.output))
        quarantines = tuple(self.root.glob(f".{self.output.name}.cleanup-*"))
        self.assertEqual(1, len(quarantines))
        self.assertIn(str(quarantines[0]), str(raised.exception))
        self.assertEqual(
            10,
            verify_prepared_lvs_source_root(manifest, quarantines[0]).file_count,
        )
        self.assertEqual((), tuple(self.root.glob(f".{self.output.name}.lvs-source-prep-*")))
        retry = prepare_lvs_source_root(
            manifest=manifest,
            control_root=self.control,
            output_root=self.output,
        )
        self.assertEqual(10, retry.file_count)

    def test_final_verifier_hostile_mutation_preserves_published_output(self):
        from maximum_optimizer import lvs_source_prep as module

        manifest = load_lvs_source_manifest(self.corpus)
        original_verify = module.verify_prepared_lvs_source_root
        hostile = self.output / "hostile-after-publish.txt"

        def mutate_only_published(candidate_manifest, root):
            root = Path(root).absolute()
            if root == self.output.absolute():
                hostile.write_bytes(b"foreign")
                raise CorpusError("forced hostile final verification failure")
            return original_verify(candidate_manifest, root)

        with mock.patch.object(
            module,
            "_quarantine_private_staging",
            wraps=module._quarantine_private_staging,
        ) as cleanup, mock.patch.object(
            module,
            "verify_prepared_lvs_source_root",
            side_effect=mutate_only_published,
        ):
            with self.assertRaisesRegex(
                CorpusError, "forced hostile final verification failure"
            ):
                prepare_lvs_source_root(
                    manifest=manifest,
                    control_root=self.control,
                    output_root=self.output,
                )

        self.assertTrue(self.output.is_dir())
        self.assertEqual(b"foreign", hostile.read_bytes())
        self.assertTrue(any(
            Path(call.args[0]).absolute() == self.output.absolute()
            for call in cleanup.call_args_list
        ))

    def test_final_verifier_hostile_root_replacement_is_left_at_output(self):
        from maximum_optimizer import lvs_source_prep as module

        manifest = load_lvs_source_manifest(self.corpus)
        original_verify = module.verify_prepared_lvs_source_root
        displaced_owned = self.root / "displaced-owned-output"
        sentinel = self.output / "foreign-root.txt"

        def replace_published_root(candidate_manifest, root):
            root = Path(root).absolute()
            if root == self.output.absolute():
                os.rename(self.output, displaced_owned)
                self.output.mkdir()
                sentinel.write_bytes(b"foreign-root")
                raise CorpusError("forced hostile root replacement")
            return original_verify(candidate_manifest, root)

        with mock.patch.object(
            module,
            "verify_prepared_lvs_source_root",
            side_effect=replace_published_root,
        ):
            with self.assertRaisesRegex(CorpusError, "hostile root replacement"):
                prepare_lvs_source_root(
                    manifest=manifest,
                    control_root=self.control,
                    output_root=self.output,
                )

        self.assertEqual(b"foreign-root", sentinel.read_bytes())
        self.assertTrue(displaced_owned.is_dir())
        self.assertEqual((), tuple(self.root.glob(f".{self.output.name}.cleanup-*")))

    def test_cleanup_has_no_identity_check_then_unlink_window(self):
        from maximum_optimizer import lvs_source_prep as module

        staging = self.root / "owned-staging"
        staging.mkdir()
        owned_file = staging / "owned.bin"
        owned_file.write_bytes(b"owned")
        ownership = module._StagingOwnership(
            module._owned_identity(staging, directory=True),
            {"owned.bin": module._owned_identity(owned_file, directory=False)},
        )
        original_identity = module._owned_identity
        descendant_checks = 0
        replacement: Path | None = None

        def swap_after_identity_check(path, *, directory=None):
            nonlocal descendant_checks, replacement
            identity = original_identity(path, directory=directory)
            if Path(path).name == "owned.bin":
                descendant_checks += 1
                if descendant_checks == 2:
                    Path(path).unlink()
                    Path(path).write_bytes(b"foreign")
                    replacement = Path(path)
            return identity

        with mock.patch.object(
            module, "_owned_identity", side_effect=swap_after_identity_check
        ):
            preserved = module._quarantine_private_staging(staging, ownership)

        self.assertIsNotNone(replacement)
        self.assertTrue(os.path.lexists(replacement))
        self.assertEqual(b"foreign", replacement.read_bytes())
        quarantines = tuple(self.root.glob(".owned-staging.cleanup-*"))
        self.assertEqual(1, len(quarantines))
        self.assertEqual(quarantines[0], preserved)
        self.assertEqual(replacement, quarantines[0] / "owned.bin")

    def test_ignores_undeclared_regular_input_artifacts_but_publishes_exact_manifest(self):
        first = self.families[0]
        extra = (
            self.control / first["id"] / "workspace" / first["id"]
            / Path(*first["source_qc"].split("/"))
        ).parent / "maximum_region_manifest.task8a-extra.json"
        extra.write_text('{"research":true}', encoding="utf-8")

        result = prepare_lvs_source_root(
            manifest=load_lvs_source_manifest(self.corpus),
            control_root=self.control,
            output_root=self.output,
        )

        self.assertEqual(10, result.file_count)
        self.assertFalse((self.output / extra.relative_to(
            self.control / first["id"] / "workspace" / first["id"]
        )).exists())
        self.assertEqual(result, verify_prepared_lvs_source_root(
            load_lvs_source_manifest(self.corpus), self.output
        ))

    def test_fails_before_writing_on_hash_drift_or_divergent_collision(self):
        manifest = load_lvs_source_manifest(self.corpus)
        first = self.families[0]
        source = (
            self.control / first["id"] / "workspace" / first["id"]
            / Path(*first["source_qc"].split("/"))
        )
        source.write_bytes(b"tampered")
        with self.assertRaisesRegex(CorpusError, "size mismatch|hash mismatch"):
            prepare_lvs_source_root(
                manifest=manifest, control_root=self.control, output_root=self.output
            )
        self.assertFalse(self.output.exists())

        source.write_bytes(f"source-0-{first['id']}".encode("ascii"))
        second = self.families[1]
        second_original = second["source_files"][0]
        second["source_files"] = [{
            **second_original,
            "path": first["source_qc"],
        }]
        second["source_qc"] = first["source_qc"]
        second_source = (
            self.control / second["id"] / "workspace" / second["id"]
            / Path(*first["source_qc"].split("/"))
        )
        second_source.parent.mkdir(parents=True, exist_ok=True)
        second_source.write_bytes(f"source-1-{second['id']}".encode("ascii"))
        self._write_corpus()
        with self.assertRaisesRegex(CorpusError, "divergent collision"):
            prepare_lvs_source_root(
                manifest=load_lvs_source_manifest(self.corpus),
                control_root=self.control,
                output_root=self.output,
            )
        self.assertFalse(self.output.exists())

    def test_rejects_reparse_input_and_existing_or_nonexact_output(self):
        manifest = load_lvs_source_manifest(self.corpus)
        guarded = (
            self.control / FULL_FAMILY_IDS[0] / "workspace" / FULL_FAMILY_IDS[0]
            / Path(*self.families[0]["source_qc"].split("/"))
        )
        from maximum_optimizer import lvs_source_prep as module
        original = module._is_reparse

        with mock.patch.object(
            module, "_is_reparse", side_effect=lambda path: path == guarded or original(path)
        ):
            with self.assertRaisesRegex(CorpusError, "reparse"):
                prepare_lvs_source_root(
                    manifest=manifest, control_root=self.control, output_root=self.output
                )
        self.assertFalse(self.output.exists())

        self.output.mkdir()
        sentinel = self.output / "sentinel"
        sentinel.write_bytes(b"owned")
        with self.assertRaisesRegex(CorpusError, "already exists"):
            prepare_lvs_source_root(
                manifest=manifest, control_root=self.control, output_root=self.output
            )
        self.assertEqual(b"owned", sentinel.read_bytes())

        sentinel.unlink()
        (self.output / "unexpected.txt").write_bytes(b"extra")
        with self.assertRaisesRegex(CorpusError, "file set mismatch"):
            verify_prepared_lvs_source_root(manifest, self.output)

    def test_rejects_output_or_staging_overlap_with_control_sources_before_write(self):
        nested_output = self.control / "prepared"
        with self.assertRaisesRegex(CorpusError, "overlap"):
            prepare_lvs_source_root(
                manifest=load_lvs_source_manifest(self.corpus),
                control_root=self.control,
                output_root=nested_output,
            )
        self.assertFalse(nested_output.exists())
        self.assertEqual((), tuple(self.control.glob(".prepared.lvs-source-prep-*")))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_parent_chain_reparse_is_not_followed_and_hostile_staging_is_preserved(self):
        from maximum_optimizer import lvs_source_prep as module

        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_bytes(b"foreign")
        original_copy = module._copy_file_no_follow
        injected = False

        def inject_reparse(source, destination, declaration):
            nonlocal injected
            original_copy(source, destination, declaration)
            if injected:
                return
            injected = True
            staging = next(
                parent for parent in destination.parents
                if parent.name.startswith(f".{self.output.name}.lvs-source-prep-")
            )
            hostile = staging / "diggercars" / "dodge_monaco_police"
            try:
                os.symlink(outside, hostile, target_is_directory=True)
            except OSError as exc:
                self.skipTest(str(exc))

        with mock.patch.object(module, "_copy_file_no_follow", side_effect=inject_reparse):
            with self.assertRaisesRegex(CorpusError, "reparse|ownership|unsafe"):
                prepare_lvs_source_root(
                    manifest=load_lvs_source_manifest(self.corpus),
                    control_root=self.control,
                    output_root=self.output,
                )
        self.assertFalse(self.output.exists())
        self.assertEqual({"sentinel"}, {path.name for path in outside.iterdir()})
        preserved = tuple(self.root.glob(f".{self.output.name}.lvs-source-prep-*"))
        self.assertEqual(1, len(preserved))
        self.assertTrue(os.path.lexists(
            preserved[0] / "diggercars" / "dodge_monaco_police"
        ))

    def test_mocked_hostile_reparse_descendant_blocks_copy_and_cleanup(self):
        from maximum_optimizer import lvs_source_prep as module

        original_copy = module._copy_file_no_follow
        original_reparse = module._is_reparse
        hostile: Path | None = None

        def inject_hostile(source, destination, declaration):
            nonlocal hostile
            original_copy(source, destination, declaration)
            if hostile is None:
                staging = next(
                    parent for parent in destination.parents
                    if parent.name.startswith(f".{self.output.name}.lvs-source-prep-")
                )
                hostile = staging / "diggercars" / "dodge_monaco_police"
                hostile.mkdir()

        def guarded_reparse(path):
            return (hostile is not None and path == hostile) or original_reparse(path)

        with mock.patch.object(module, "_is_reparse", side_effect=guarded_reparse), mock.patch.object(
            module, "_copy_file_no_follow", side_effect=inject_hostile
        ):
            with self.assertRaisesRegex(CorpusError, "reparse|ownership|unsafe"):
                prepare_lvs_source_root(
                    manifest=load_lvs_source_manifest(self.corpus),
                    control_root=self.control,
                    output_root=self.output,
                )
        self.assertFalse(self.output.exists())
        preserved = tuple(self.root.glob(f".{self.output.name}.lvs-source-prep-*"))
        self.assertEqual(1, len(preserved))
        self.assertTrue((preserved[0] / "diggercars" / "dodge_monaco_police").exists())

    def test_rejects_incomplete_or_drifted_family_membership(self):
        self.payload["families"] = self.families[:-1]
        self._write_corpus()
        with self.assertRaisesRegex(CorpusError, "family order|family membership"):
            load_lvs_source_manifest(self.corpus)

    def test_cli_prepares_fresh_root_and_prints_bounded_canonical_summary(self):
        from benchmarks.lvs_models.prepare_lvs_source_root import main

        output = io.StringIO()
        with redirect_stdout(output):
            returncode = main([
                "--corpus", str(self.corpus),
                "--control-root", str(self.control),
                "--output", str(self.output),
            ])
        summary = json.loads(output.getvalue())
        self.assertEqual(0, returncode)
        self.assertEqual(1, summary["schema_version"])
        self.assertEqual("lvs-source-root-preparation-v1", summary["kind"])
        self.assertEqual(list(CALIBRATION_FAMILY_IDS), summary["calibration_family_ids"])
        self.assertEqual(list(HOLDOUT_FAMILY_IDS), summary["holdout_family_ids"])
        self.assertEqual(10, summary["file_count"])
        self.assertEqual(str(self.output.resolve()), summary["source_root"])

    def test_cli_fails_closed_without_json_success_summary(self):
        from benchmarks.lvs_models.prepare_lvs_source_root import main

        self.output.mkdir()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            returncode = main([
                "--corpus", str(self.corpus),
                "--control-root", str(self.control),
                "--output", str(self.output),
            ])
        self.assertEqual(2, returncode)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("already exists", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
