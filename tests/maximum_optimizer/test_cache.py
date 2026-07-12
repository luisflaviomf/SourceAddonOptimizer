from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from maximum_optimizer import cache as cache_module
from maximum_optimizer.cache import (
    AtomicReplaceError,
    CacheKey,
    CandidateCache,
    atomic_replace_tree,
)


VALID_DIGEST = "a" * 64


def write_tree(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative_path, content in files.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def symlink_or_skip(
    test_case: unittest.TestCase,
    link: Path,
    target: Path,
    *,
    target_is_directory: bool,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        test_case.skipTest(f"symlink creation is unavailable: {exc}")


class CacheKeyTests(unittest.TestCase):
    def test_meshopt_direct_strategy_fields_each_invalidate_cache(self):
        candidate = {
            "strategy": "meshopt-direct-v1",
            "update_vertices": False,
            "transfer": "direct-v1",
        }
        base = CacheKey.build("input", candidate, {"meshopt": "1.2"}, "profile")
        variants = (
            {**candidate, "strategy": "meshopt-direct-v2"},
            {**candidate, "update_vertices": True},
            {**candidate, "transfer": "projection-v1"},
        )
        self.assertEqual(
            len({base.digest, *(CacheKey.build("input", item, {"meshopt": "1.2"}, "profile").digest for item in variants)}),
            4,
        )

    def test_rejects_non_sha256_digest(self):
        invalid = (
            "",
            "a" * 63,
            "a" * 65,
            "A" * 64,
            "g" * 64,
            123,
        )

        for digest in invalid:
            with self.subTest(digest=digest):
                with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
                    CacheKey(digest)  # type: ignore[arg-type]

    def test_build_changes_with_each_input_dimension(self):
        base = CacheKey.build(
            "input-a",
            {"id": "candidate-a", "ratio": 0.5},
            {"blender": "4.3"},
            "profile-a",
        )

        variants = (
            CacheKey.build(
                "input-b",
                {"id": "candidate-a", "ratio": 0.5},
                {"blender": "4.3"},
                "profile-a",
            ),
            CacheKey.build(
                "input-a",
                {"id": "candidate-b", "ratio": 0.5},
                {"blender": "4.3"},
                "profile-a",
            ),
            CacheKey.build(
                "input-a",
                {"id": "candidate-a", "ratio": 0.5},
                {"blender": "4.4"},
                "profile-a",
            ),
            CacheKey.build(
                "input-a",
                {"id": "candidate-a", "ratio": 0.5},
                {"blender": "4.3"},
                "profile-b",
            ),
        )

        self.assertEqual(len({base.digest, *(item.digest for item in variants)}), 5)

    def test_build_is_independent_of_dictionary_order(self):
        a = CacheKey.build(
            "input",
            {"id": "candidate", "settings": {"ratio": 0.5, "error": 0.01}},
            {"blender": "4.3", "crowbar": "0.74"},
            "profile",
        )
        b = CacheKey.build(
            "input",
            {"settings": {"error": 0.01, "ratio": 0.5}, "id": "candidate"},
            {"crowbar": "0.74", "blender": "4.3"},
            "profile",
        )

        self.assertEqual(a, b)

    def test_build_reports_non_serializable_payload(self):
        with self.assertRaisesRegex(TypeError, "not JSON serializable"):
            CacheKey.build("input", {"bad": object()}, {}, "profile")


class CandidateCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)
        self.root = self.base / "cache"
        self.source = self.base / "source"
        write_tree(self.source, {"model.mdl": b"compiled", "nested/data.bin": b"data"})
        self.key = CacheKey(VALID_DIGEST)
        self.cache = CandidateCache(self.root)

    def create_final_entry(self, marker: object | None) -> Path:
        final = self.root / self.key.digest
        write_tree(final / "payload", {"model.mdl": b"cached"})
        (final / "metadata.json").write_text("{}", encoding="utf-8")
        if marker is not None:
            if isinstance(marker, str):
                (final / "complete.json").write_text(marker, encoding="utf-8")
            else:
                (final / "complete.json").write_text(
                    json.dumps(marker), encoding="utf-8"
                )
        return final

    def test_lookup_accepts_only_valid_complete_entry(self):
        final = self.create_final_entry({"digest": self.key.digest})

        self.assertEqual(self.cache.lookup(self.key), final)

    def test_lookup_valid_hit_is_strictly_read_only(self):
        final = self.create_final_entry({"digest": self.key.digest})
        quarantine = self.root / f"{self.key.digest}.quarantine-recovery"
        cleanup_pending = self.root / f"{self.key.digest}.cleanup-pending-old"
        write_tree(quarantine, {"original.bin": b"original"})
        write_tree(cleanup_pending, {"old.bin": b"old"})

        with mock.patch(
            "maximum_optimizer.cache._remove_direct_child"
        ) as remove, mock.patch("maximum_optimizer.cache.os.replace") as replace:
            self.assertEqual(self.cache.lookup(self.key), final)

        remove.assert_not_called()
        replace.assert_not_called()
        self.assertTrue(quarantine.is_dir())
        self.assertTrue(cleanup_pending.is_dir())

    def test_lookup_rejects_missing_corrupt_and_mismatched_marker_without_deleting(self):
        cases = (
            None,
            "not-json",
            {"digest": "b" * 64},
        )

        for marker in cases:
            with self.subTest(marker=marker):
                if self.root.exists():
                    shutil.rmtree(self.root)
                final = self.create_final_entry(marker)

                self.assertIsNone(self.cache.lookup(self.key))
                self.assertTrue(final.is_dir())

    def test_lookup_rejects_missing_payload_and_non_directory_final(self):
        final = self.create_final_entry({"digest": self.key.digest})
        shutil.rmtree(final / "payload")
        self.assertIsNone(self.cache.lookup(self.key))

        shutil.rmtree(final)
        final.write_text("not a directory", encoding="utf-8")
        self.assertIsNone(self.cache.lookup(self.key))
        self.assertTrue(final.is_file())

    def test_lookup_does_not_follow_final_payload_or_marker_symlinks(self):
        outside = self.base / "outside"
        write_tree(outside / "payload", {"outside.bin": b"outside"})
        (outside / "complete.json").write_text(
            json.dumps({"digest": self.key.digest}), encoding="utf-8"
        )

        final_link = self.root / self.key.digest
        self.root.mkdir()
        symlink_or_skip(
            self,
            final_link,
            outside,
            target_is_directory=True,
        )
        self.assertIsNone(self.cache.lookup(self.key))
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.cache.store(self.key, self.source, {})

        final_link.unlink()
        final = self.root / self.key.digest
        final.mkdir()
        symlink_or_skip(
            self,
            final / "payload",
            outside / "payload",
            target_is_directory=True,
        )
        (final / "complete.json").write_text(
            json.dumps({"digest": self.key.digest}), encoding="utf-8"
        )
        self.assertIsNone(self.cache.lookup(self.key))
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.cache.store(self.key, self.source, {})

        (final / "payload").unlink()
        (final / "payload").mkdir()
        (final / "complete.json").unlink()
        symlink_or_skip(
            self,
            final / "complete.json",
            outside / "complete.json",
            target_is_directory=False,
        )
        self.assertIsNone(self.cache.lookup(self.key))
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.cache.store(self.key, self.source, {})

    def test_store_rejects_source_directory_symlink(self):
        source_link = self.base / "source-link"
        symlink_or_skip(
            self,
            source_link,
            self.source,
            target_is_directory=True,
        )

        with self.assertRaisesRegex(ValueError, "symlink"):
            self.cache.store(self.key, source_link, {})

    def test_lookup_and_store_reject_broken_cache_symlinks(self):
        self.root.mkdir()
        missing = self.base / "missing-target"
        final = self.root / self.key.digest
        symlink_or_skip(self, final, missing, target_is_directory=True)

        self.assertIsNone(self.cache.lookup(self.key))
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.cache.store(self.key, self.source, {})

        final.unlink()
        final.mkdir()
        symlink_or_skip(
            self,
            final / "payload",
            missing,
            target_is_directory=True,
        )
        self.assertIsNone(self.cache.lookup(self.key))
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.cache.store(self.key, self.source, {})

        (final / "payload").unlink()
        (final / "payload").mkdir()
        symlink_or_skip(
            self,
            final / "complete.json",
            missing,
            target_is_directory=False,
        )
        self.assertIsNone(self.cache.lookup(self.key))
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.cache.store(self.key, self.source, {})

        broken_source = self.base / "broken-source"
        symlink_or_skip(
            self,
            broken_source,
            missing,
            target_is_directory=True,
        )
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.cache.store(self.key, broken_source, {})

    def test_symlink_guards_are_deterministic_without_platform_privileges(self):
        final = self.create_final_entry({"digest": self.key.digest})
        payload = final / "payload"
        marker = final / "complete.json"

        for guarded, expected_calls in (
            (final, [mock.call(final)]),
            (payload, [mock.call(final), mock.call(payload)]),
            (marker, [mock.call(final), mock.call(payload), mock.call(marker)]),
        ):
            with self.subTest(guarded=guarded), mock.patch(
                "maximum_optimizer.cache._is_symlink",
                side_effect=lambda path, guarded=guarded: path == guarded,
            ) as is_symlink:
                self.assertIsNone(self.cache.lookup(self.key))
                self.assertEqual(is_symlink.call_args_list, expected_calls)

        with mock.patch(
            "maximum_optimizer.cache._is_symlink",
            side_effect=lambda path: path == self.source,
        ) as is_symlink:
            with self.assertRaisesRegex(ValueError, "symlink"):
                self.cache.store(self.key, self.source, {})
            self.assertEqual(is_symlink.call_args_list, [mock.call(self.source)])

    def test_store_requires_source_directory(self):
        missing = self.base / "missing"
        file_source = self.base / "source-file"
        file_source.write_text("x", encoding="utf-8")

        for source in (missing, file_source):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "source_dir"):
                    self.cache.store(self.key, source, {})

    def test_store_creates_expected_layout_and_round_trips_metadata(self):
        metadata = {"candidate": "meshopt-r050", "score": 0.99}

        final = self.cache.store(self.key, self.source, metadata)

        self.assertEqual(final, self.root / self.key.digest)
        self.assertEqual((final / "payload/model.mdl").read_bytes(), b"compiled")
        self.assertEqual((final / "payload/nested/data.bin").read_bytes(), b"data")
        self.assertEqual(
            json.loads((final / "metadata.json").read_text(encoding="utf-8")),
            metadata,
        )
        self.assertEqual(
            json.loads((final / "complete.json").read_text(encoding="utf-8")),
            {"digest": self.key.digest},
        )
        self.assertEqual(self.cache.lookup(self.key), final)

    def test_store_writes_complete_marker_last_before_promotion(self):
        events: list[tuple[str, str]] = []

        from maximum_optimizer import cache as cache_module

        real_write_json = cache_module._write_json
        real_replace = cache_module.os.replace

        def observing_write(path: Path, payload: object) -> None:
            events.append(("write", Path(path).name))
            real_write_json(path, payload)

        def observing_replace(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
            source_path = Path(source)
            self.assertTrue(source_path.name.startswith(f"{self.key.digest}.tmp-{os.getpid()}"))
            self.assertTrue((source_path / "complete.json").is_file())
            events.append(("replace", source_path.name))
            real_replace(source, destination)

        with mock.patch.object(cache_module, "_write_json", side_effect=observing_write), mock.patch.object(
            cache_module.os, "replace", side_effect=observing_replace
        ):
            self.cache.store(self.key, self.source, {"id": "candidate"})

        self.assertEqual([item[1] for item in events[:2]], ["metadata.json", "complete.json"])
        self.assertEqual(events[2][0], "replace")

    def test_store_reuses_existing_valid_entry(self):
        final = self.create_final_entry({"digest": self.key.digest})
        original_metadata = (final / "metadata.json").read_bytes()

        returned = self.cache.store(self.key, self.source, {"new": "metadata"})

        self.assertEqual(returned, final)
        self.assertEqual((final / "metadata.json").read_bytes(), original_metadata)
        self.assertEqual((final / "payload/model.mdl").read_bytes(), b"cached")

    def test_store_with_ownership_distinguishes_new_and_existing_entries(self):
        first, first_owned = self.cache.store_with_ownership(
            self.key, self.source, {"generation": 1}
        )
        second, second_owned = self.cache.store_with_ownership(
            self.key, self.source, {"generation": 2}
        )

        self.assertEqual(first, second)
        self.assertTrue(first_owned)
        self.assertFalse(second_owned)

    def test_validated_store_finalizes_private_staging_before_complete_marker(self):
        observed = []

        def finalize(staging):
            observed.append((staging.name, (staging / "complete.json").exists()))
            self.assertTrue((staging / "payload/model.mdl").is_file())
            (staging / "maximum_integrity.json").write_text(
                '{"sealed":true}', encoding="utf-8"
            )

        final, owned = self.cache.store_validated(
            self.key, self.source, {"candidate": "recovery"},
            finalize_staging=finalize,
            validate_existing=lambda _entry: self.fail("no existing entry expected"),
        )

        self.assertTrue(owned)
        self.assertEqual(observed, [(observed[0][0], False)])
        self.assertTrue((final / "complete.json").is_file())
        self.assertTrue((final / "maximum_integrity.json").is_file())

    def test_validated_store_failure_never_publishes_or_leaves_staging(self):
        with self.assertRaisesRegex(ValueError, "semantic failure"):
            self.cache.store_validated(
                self.key, self.source, {},
                finalize_staging=lambda _staging: (_ for _ in ()).throw(
                    ValueError("semantic failure")
                ),
                validate_existing=lambda _entry: None,
            )

        self.assertIsNone(self.cache.lookup(self.key))
        self.assertFalse(any(".tmp-" in item.name for item in self.root.iterdir()))

    def test_validated_store_concurrent_valid_winner_is_never_replaced(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def worker():
            try:
                barrier.wait()
                result = self.cache.store_validated(
                    self.key, self.source, {},
                    finalize_staging=lambda staging: (
                        staging / "maximum_integrity.json"
                    ).write_text('{"sealed":true}', encoding="utf-8"),
                    validate_existing=lambda entry: self.assertTrue(
                        (entry / "complete.json").is_file()
                    ),
                )
                results.append(result)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(sorted(owned for _path, owned in results), [False, True])
        self.assertEqual(len({path for path, _owned in results}), 1)
        self.assertEqual(cache_module._VALIDATED_LOCKS, {})

    def test_validated_store_cross_process_same_key_has_one_winner(self):
        gate = self.base / "cross-process-go"
        script = f'''\
import json, time
from pathlib import Path
from maximum_optimizer.cache import CacheKey, CandidateCache
root = Path({str(self.root)!r})
source = Path({str(self.source)!r})
gate = Path({str(gate)!r})
while not gate.exists():
    time.sleep(0.01)
cache = CandidateCache(root)
def finalize(staging):
    (staging / "maximum_integrity.json").write_text('{{"sealed":true}}', encoding="utf-8")
entry, owned = cache.store_validated(
    CacheKey({self.key.digest!r}), source, {{}},
    finalize_staging=finalize,
    validate_existing=lambda entry: None,
)
print(json.dumps({{"owned": owned, "entry": str(entry)}}))
'''
        processes = [subprocess.Popen(
            [sys.executable, "-c", script], cwd=Path(__file__).parents[2],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) for _ in range(2)]
        gate.write_text("go", encoding="utf-8")
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout.strip()))

        self.assertEqual(sorted(item["owned"] for item in results), [False, True])
        self.assertEqual(len({item["entry"] for item in results}), 1)

    def test_validated_store_flushes_tree_before_rename_and_root_after(self):
        events = []
        original_tree = cache_module._flush_tree
        original_directory = cache_module._flush_directory
        original_replace = cache_module._durable_replace

        def flush_tree(path):
            events.append(("tree", Path(path)))
            return original_tree(path)

        def replace(source, destination):
            events.append(("rename", Path(destination)))
            return original_replace(source, destination)

        def flush_directory(path):
            events.append(("directory", Path(path)))
            return original_directory(path)

        with mock.patch.object(cache_module, "_flush_tree", side_effect=flush_tree), \
             mock.patch.object(cache_module, "_durable_replace", side_effect=replace), \
             mock.patch.object(cache_module, "_flush_directory", side_effect=flush_directory):
            self.cache.store_validated(
                self.key, self.source, {},
                finalize_staging=lambda staging: (
                    staging / "maximum_integrity.json"
                ).write_text('{"sealed":true}', encoding="utf-8"),
                validate_existing=lambda _entry: None,
            )

        tree_index = next(i for i, event in enumerate(events) if event[0] == "tree")
        rename_index = next(i for i, event in enumerate(events) if event[0] == "rename")
        root_flushes = [
            i for i, event in enumerate(events)
            if event == ("directory", self.root)
        ]
        self.assertLess(tree_index, rename_index)
        self.assertTrue(root_flushes)
        self.assertGreater(root_flushes[-1], rename_index)

    def test_store_quarantines_invalid_final_and_promotes_without_merging(self):
        final = self.create_final_entry(None)
        write_tree(final / "payload", {"stale.bin": b"stale"})

        returned = self.cache.store(self.key, self.source, {"fresh": True})

        self.assertEqual(returned, final)
        self.assertFalse((final / "payload/stale.bin").exists())
        self.assertEqual((final / "payload/model.mdl").read_bytes(), b"compiled")
        self.assertFalse(any(".quarantine-" in path.name for path in self.root.iterdir()))

    def test_store_restores_invalid_final_if_promotion_fails(self):
        final = self.create_final_entry(None)
        original = (final / "payload/model.mdl").read_bytes()

        from maximum_optimizer import cache as cache_module

        real_replace = cache_module.os.replace
        calls = 0

        def fail_promotion(source: os.PathLike[str], destination: os.PathLike[str]) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected promotion failure")
            real_replace(source, destination)

        with mock.patch.object(cache_module.os, "replace", side_effect=fail_promotion):
            with self.assertRaisesRegex(OSError, "injected promotion failure"):
                self.cache.store(self.key, self.source, {})

        self.assertEqual((final / "payload/model.mdl").read_bytes(), original)
        self.assertFalse((final / "complete.json").exists())

    def test_store_exposes_quarantine_if_promotion_and_restore_both_fail(self):
        final = self.create_final_entry(None)
        original = (final / "payload/model.mdl").read_bytes()

        from maximum_optimizer import cache as cache_module

        real_replace = cache_module.os.replace
        calls = 0

        def fail_promotion_and_restore(
            source: os.PathLike[str], destination: os.PathLike[str]
        ) -> None:
            nonlocal calls
            calls += 1
            if calls in (2, 3):
                raise OSError(f"injected replace failure {calls}")
            real_replace(source, destination)

        with mock.patch.object(
            cache_module.os,
            "replace",
            side_effect=fail_promotion_and_restore,
        ):
            with self.assertRaisesRegex(AtomicReplaceError, "recovery") as raised:
                self.cache.store(self.key, self.source, {})

        error = raised.exception
        self.assertEqual(error.destination, final)
        self.assertIsInstance(error.promotion_error, OSError)
        self.assertIsInstance(error.restore_error, OSError)
        self.assertTrue(error.recovery_path.is_dir())
        self.assertEqual(
            (error.recovery_path / "payload/model.mdl").read_bytes(), original
        )

    def test_store_succeeds_when_pending_cleanup_fails_and_mutating_retry_cleans_it(self):
        final = self.create_final_entry(None)

        from maximum_optimizer import cache as cache_module

        real_remove = cache_module._remove_direct_child
        with mock.patch.object(
            cache_module,
            "_remove_direct_child",
            side_effect=OSError("locked quarantine"),
        ):
            returned = self.cache.store(self.key, self.source, {"fresh": True})

        cleanup_pending = next(
            path for path in self.root.iterdir() if ".cleanup-pending-" in path.name
        )
        self.assertEqual(returned, final)
        self.assertEqual((final / "payload/model.mdl").read_bytes(), b"compiled")
        self.assertTrue(cleanup_pending.is_dir())

        with mock.patch.object(
            cache_module,
            "_remove_direct_child",
            wraps=real_remove,
        ) as remove_spy:
            self.assertEqual(self.cache.lookup(self.key), final)

        self.assertTrue(cleanup_pending.is_dir())
        remove_spy.assert_not_called()

        self.assertEqual(self.cache.store(self.key, self.source, {}), final)
        self.assertFalse(cleanup_pending.exists())

    def test_store_preserves_success_and_quarantine_if_post_commit_rename_fails(self):
        final = self.create_final_entry(None)

        from maximum_optimizer import cache as cache_module

        real_replace = cache_module.os.replace
        calls = 0

        def fail_cleanup_rename(
            source: os.PathLike[str], destination: os.PathLike[str]
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("locked quarantine rename")
            real_replace(source, destination)

        with mock.patch.object(
            cache_module.os,
            "replace",
            side_effect=fail_cleanup_rename,
        ):
            returned = self.cache.store(self.key, self.source, {"fresh": True})

        quarantine = next(
            path for path in self.root.iterdir() if ".quarantine-" in path.name
        )
        self.assertEqual(returned, final)
        self.assertEqual((final / "payload/model.mdl").read_bytes(), b"compiled")
        self.assertEqual(self.cache.store(self.key, self.source, {}), final)
        self.assertTrue(quarantine.is_dir())

    def test_store_preserves_success_when_pending_removal_raises_value_error(self):
        final = self.create_final_entry(None)

        with mock.patch(
            "maximum_optimizer.cache._remove_direct_child",
            side_effect=ValueError("containment changed"),
        ):
            returned = self.cache.store(self.key, self.source, {"fresh": True})

        self.assertEqual(returned, final)
        pending = next(
            path for path in self.root.iterdir() if ".cleanup-pending-" in path.name
        )
        self.assertTrue(pending.is_dir())

    def test_recovery_quarantine_survives_lookup_and_store_cleanup(self):
        final = self.create_final_entry(None)

        from maximum_optimizer import cache as cache_module

        real_replace = cache_module.os.replace
        calls = 0

        def fail_promotion_and_restore(
            source: os.PathLike[str], destination: os.PathLike[str]
        ) -> None:
            nonlocal calls
            calls += 1
            if calls in (2, 3):
                raise OSError(f"injected replace failure {calls}")
            real_replace(source, destination)

        with mock.patch.object(
            cache_module.os,
            "replace",
            side_effect=fail_promotion_and_restore,
        ):
            with self.assertRaises(AtomicReplaceError) as raised:
                self.cache.store(self.key, self.source, {})

        recovery = raised.exception.recovery_path
        self.assertTrue(recovery.is_dir())
        self.assertIsNone(self.cache.lookup(self.key))
        self.assertTrue(recovery.is_dir())

        self.assertEqual(self.cache.store(self.key, self.source, {}), final)
        self.assertEqual(self.cache.lookup(self.key), final)
        self.assertEqual(self.cache.store(self.key, self.source, {}), final)
        self.assertTrue(recovery.is_dir())

    def test_cleanup_incomplete_removes_only_direct_staging_directories(self):
        self.root.mkdir()
        staging_a = self.root / f"{self.key.digest}.tmp-100"
        staging_b = self.root / "other.tmp-200-extra"
        final = self.root / self.key.digest
        quarantine = self.root / f"{self.key.digest}.quarantine-1"
        staging_file = self.root / "file.tmp-300"
        outside = self.base / "outside.tmp-400"
        for path in (staging_a, staging_b, final, quarantine, outside):
            write_tree(path, {"data": b"x"})
        staging_file.write_bytes(b"x")

        removed = self.cache.cleanup_incomplete()

        self.assertEqual(removed, 2)
        self.assertFalse(staging_a.exists())
        self.assertFalse(staging_b.exists())
        for path in (final, quarantine, staging_file, outside):
            self.assertTrue(path.exists())

    def test_cleanup_incomplete_returns_zero_when_root_is_absent(self):
        self.assertEqual(self.cache.cleanup_incomplete(), 0)

    def test_cleanup_incomplete_does_not_follow_or_remove_symlinks(self):
        self.root.mkdir()
        outside = self.base / "outside"
        write_tree(outside, {"data": b"outside"})
        staging_link = self.root / f"{self.key.digest}.tmp-100"
        broken_link = self.root / f"{self.key.digest}.tmp-200"
        symlink_or_skip(
            self,
            staging_link,
            outside,
            target_is_directory=True,
        )
        symlink_or_skip(
            self,
            broken_link,
            self.base / "missing-target",
            target_is_directory=True,
        )

        self.assertEqual(self.cache.cleanup_incomplete(), 0)
        self.assertTrue(staging_link.is_symlink())
        self.assertTrue(broken_link.is_symlink())
        self.assertEqual((outside / "data").read_bytes(), b"outside")

    def test_cleanup_checks_symlink_before_directory_type(self):
        self.root.mkdir()
        apparent_link = self.root / f"{self.key.digest}.tmp-100"
        apparent_link.mkdir()
        real_is_dir = Path.is_dir
        real_is_symlink = Path.is_symlink

        def is_symlink(path: Path) -> bool:
            if path == apparent_link:
                return True
            return real_is_symlink(path)

        def is_dir(path: Path) -> bool:
            if path == apparent_link:
                raise AssertionError("cleanup followed a symlink before rejecting it")
            return real_is_dir(path)

        with mock.patch.object(
            Path,
            "is_symlink",
            autospec=True,
            side_effect=is_symlink,
        ), mock.patch.object(
            Path,
            "is_dir",
            autospec=True,
            side_effect=is_dir,
        ):
            self.assertEqual(self.cache.cleanup_incomplete(), 0)

    def test_recursive_remove_refuses_symlink_after_containment_check(self):
        from maximum_optimizer import cache as cache_module

        target = self.base / "apparent-directory"
        write_tree(target, {"data": b"preserved"})

        with mock.patch.object(
            cache_module,
            "_is_symlink",
            return_value=True,
        ):
            with self.assertRaisesRegex(ValueError, "symlink"):
                cache_module._remove_direct_child(target, self.base)

        self.assertEqual((target / "data").read_bytes(), b"preserved")

    def test_store_does_not_collide_with_broken_symlink_staging_name(self):
        self.root.mkdir()
        collision = self.root / f"{self.key.digest}.tmp-{os.getpid()}-collision"
        symlink_or_skip(
            self,
            collision,
            self.base / "missing-target",
            target_is_directory=True,
        )

        with mock.patch(
            "maximum_optimizer.cache.uuid.uuid4",
            side_effect=(
                SimpleNamespace(hex="collision"),
                SimpleNamespace(hex="available"),
            ),
        ):
            returned = self.cache.store(self.key, self.source, {})

        self.assertEqual(returned, self.root / self.key.digest)
        self.assertTrue(collision.is_symlink())

    def test_unique_sibling_uses_lexists_for_collision_detection(self):
        from maximum_optimizer import cache as cache_module

        with mock.patch.object(
            cache_module.uuid,
            "uuid4",
            side_effect=(
                SimpleNamespace(hex="collision"),
                SimpleNamespace(hex="available"),
            ),
        ), mock.patch.object(
            cache_module.os.path,
            "lexists",
            side_effect=(True, False),
        ) as lexists:
            sibling = cache_module._unique_sibling(self.root, "entry-")

        self.assertEqual(sibling, self.root / "entry-available")
        self.assertEqual(lexists.call_count, 2)


class AtomicReplaceTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_promotes_when_destination_does_not_exist(self):
        staging = self.root / "staging"
        destination = self.root / "destination"
        write_tree(staging, {"new.bin": b"new"})

        returned = atomic_replace_tree(staging, destination)

        self.assertEqual(returned, destination)
        self.assertEqual((destination / "new.bin").read_bytes(), b"new")
        self.assertFalse(staging.exists())

    def test_replaces_existing_destination_and_removes_backup(self):
        staging = self.root / "staging"
        destination = self.root / "destination"
        write_tree(staging, {"new.bin": b"new"})
        write_tree(destination, {"old.bin": b"old"})

        atomic_replace_tree(staging, destination)

        self.assertEqual((destination / "new.bin").read_bytes(), b"new")
        self.assertFalse((destination / "old.bin").exists())
        self.assertFalse(any(".backup-" in path.name for path in self.root.iterdir()))

    def test_restores_original_bytes_when_promotion_fails_after_backup(self):
        staging = self.root / "staging"
        destination = self.root / "destination"
        write_tree(staging, {"new.bin": b"new"})
        write_tree(destination, {"old.bin": b"original"})
        calls = 0

        def flaky_replace(source: os.PathLike[str], target: os.PathLike[str]) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected failure")
            os.replace(source, target)

        with self.assertRaisesRegex(OSError, "injected failure"):
            atomic_replace_tree(staging, destination, replace=flaky_replace)

        self.assertEqual((destination / "old.bin").read_bytes(), b"original")
        self.assertFalse((destination / "new.bin").exists())
        self.assertEqual((staging / "new.bin").read_bytes(), b"new")

    def test_exposes_backup_if_promotion_and_restore_both_fail(self):
        staging = self.root / "staging"
        destination = self.root / "destination"
        write_tree(staging, {"new.bin": b"new"})
        write_tree(destination, {"old.bin": b"original"})
        calls = 0

        def fail_promotion_and_restore(
            source: os.PathLike[str], target: os.PathLike[str]
        ) -> None:
            nonlocal calls
            calls += 1
            if calls in (2, 3):
                raise OSError(f"injected replace failure {calls}")
            os.replace(source, target)

        with self.assertRaisesRegex(AtomicReplaceError, "recovery") as raised:
            atomic_replace_tree(
                staging,
                destination,
                replace=fail_promotion_and_restore,
            )

        error = raised.exception
        self.assertEqual(error.destination, destination)
        self.assertIsInstance(error.promotion_error, OSError)
        self.assertIsInstance(error.restore_error, OSError)
        self.assertTrue(error.recovery_path.is_dir())
        self.assertEqual((error.recovery_path / "old.bin").read_bytes(), b"original")
        self.assertEqual((staging / "new.bin").read_bytes(), b"new")

    def test_success_is_preserved_when_pending_cleanup_fails(self):
        staging = self.root / "staging"
        destination = self.root / "destination"
        write_tree(staging, {"new.bin": b"new"})
        write_tree(destination, {"old.bin": b"old"})

        with mock.patch(
            "maximum_optimizer.cache._remove_direct_child",
            side_effect=OSError("locked backup"),
        ):
            returned = atomic_replace_tree(staging, destination)

        self.assertEqual(returned, destination)
        self.assertEqual((destination / "new.bin").read_bytes(), b"new")
        pending = next(
            path for path in self.root.iterdir() if ".cleanup-pending-" in path.name
        )
        self.assertEqual((pending / "old.bin").read_bytes(), b"old")

    def test_success_preserves_backup_if_post_commit_rename_fails(self):
        staging = self.root / "staging"
        destination = self.root / "destination"
        write_tree(staging, {"new.bin": b"new"})
        write_tree(destination, {"old.bin": b"old"})
        calls = 0

        def fail_cleanup_rename(
            source: os.PathLike[str], target: os.PathLike[str]
        ) -> None:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ValueError("containment changed")
            os.replace(source, target)

        returned = atomic_replace_tree(
            staging,
            destination,
            replace=fail_cleanup_rename,
        )

        self.assertEqual(returned, destination)
        self.assertEqual((destination / "new.bin").read_bytes(), b"new")
        backup = next(path for path in self.root.iterdir() if ".backup-" in path.name)
        self.assertEqual((backup / "old.bin").read_bytes(), b"old")
        self.assertFalse(
            any(".cleanup-pending-" in path.name for path in self.root.iterdir())
        )

    def test_success_survives_value_error_removing_pending_backup(self):
        staging = self.root / "staging"
        destination = self.root / "destination"
        write_tree(staging, {"new.bin": b"new"})
        write_tree(destination, {"old.bin": b"old"})

        with mock.patch(
            "maximum_optimizer.cache._remove_direct_child",
            side_effect=ValueError("containment changed"),
        ):
            returned = atomic_replace_tree(staging, destination)

        self.assertEqual(returned, destination)
        pending = next(
            path for path in self.root.iterdir() if ".cleanup-pending-" in path.name
        )
        self.assertEqual((pending / "old.bin").read_bytes(), b"old")

    def test_rejects_cross_volume_promotion_before_moving_anything(self):
        staging = self.root / "staging"
        destination = self.root / "destination"
        write_tree(staging, {"new.bin": b"new"})
        write_tree(destination, {"old.bin": b"old"})

        with mock.patch(
            "maximum_optimizer.cache._device_id", side_effect=(1, 2)
        ):
            with self.assertRaisesRegex(ValueError, "same volume"):
                atomic_replace_tree(staging, destination)

        self.assertTrue(staging.is_dir())
        self.assertEqual((destination / "old.bin").read_bytes(), b"old")

    def test_rejects_invalid_paths(self):
        valid_staging = self.root / "valid-staging"
        write_tree(valid_staging, {"new.bin": b"new"})
        file_staging = self.root / "file-staging"
        file_staging.write_bytes(b"x")
        file_destination = self.root / "file-destination"
        file_destination.write_bytes(b"x")
        missing_parent_destination = self.root / "missing" / "destination"

        cases = (
            (self.root / "missing-staging", self.root / "destination"),
            (file_staging, self.root / "destination"),
            (valid_staging, file_destination),
            (valid_staging, missing_parent_destination),
            (valid_staging, valid_staging),
        )

        for staging, destination in cases:
            with self.subTest(staging=staging, destination=destination):
                with self.assertRaises(ValueError):
                    atomic_replace_tree(staging, destination)

    def test_rejects_staging_and_destination_symlinks_including_broken(self):
        real_staging = self.root / "real-staging"
        write_tree(real_staging, {"new.bin": b"new"})
        staging_link = self.root / "staging-link"
        symlink_or_skip(
            self,
            staging_link,
            real_staging,
            target_is_directory=True,
        )

        with self.assertRaisesRegex(ValueError, "symlink"):
            atomic_replace_tree(staging_link, self.root / "destination")

        staging_link.unlink()
        symlink_or_skip(
            self,
            staging_link,
            self.root / "missing-staging",
            target_is_directory=True,
        )
        with self.assertRaisesRegex(ValueError, "symlink"):
            atomic_replace_tree(staging_link, self.root / "destination")
        staging_link.unlink()

        destination_target = self.root / "destination-target"
        write_tree(destination_target, {"old.bin": b"old"})
        destination_link = self.root / "destination-link"
        symlink_or_skip(
            self,
            destination_link,
            destination_target,
            target_is_directory=True,
        )
        with self.assertRaisesRegex(ValueError, "symlink"):
            atomic_replace_tree(real_staging, destination_link)

        destination_link.unlink()
        symlink_or_skip(
            self,
            destination_link,
            self.root / "missing-target",
            target_is_directory=True,
        )
        with self.assertRaisesRegex(ValueError, "symlink"):
            atomic_replace_tree(real_staging, destination_link)

    def test_atomic_symlink_guards_are_deterministic_without_privileges(self):
        staging = self.root / "staging"
        destination = self.root / "destination"
        write_tree(staging, {"new.bin": b"new"})
        write_tree(destination, {"old.bin": b"old"})

        with mock.patch(
            "maximum_optimizer.cache._is_symlink",
            side_effect=lambda path: path == staging,
        ) as is_symlink:
            with self.assertRaisesRegex(ValueError, "symlink"):
                atomic_replace_tree(staging, destination)
            self.assertEqual(is_symlink.call_args_list, [mock.call(staging)])

        with mock.patch(
            "maximum_optimizer.cache._is_symlink",
            side_effect=lambda path: path == destination,
        ) as is_symlink:
            with self.assertRaisesRegex(ValueError, "symlink"):
                atomic_replace_tree(staging, destination)
            self.assertEqual(
                is_symlink.call_args_list,
                [mock.call(staging), mock.call(destination)],
            )

        self.assertEqual((staging / "new.bin").read_bytes(), b"new")
        self.assertEqual((destination / "old.bin").read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
