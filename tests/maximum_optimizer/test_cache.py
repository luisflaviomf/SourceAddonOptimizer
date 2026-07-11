from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maximum_optimizer.cache import CacheKey, CandidateCache, atomic_replace_tree


VALID_DIGEST = "a" * 64


def write_tree(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for relative_path, content in files.items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


class CacheKeyTests(unittest.TestCase):
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

    def test_lookup_rejects_missing_corrupt_and_mismatched_marker_without_deleting(self):
        cases = (
            None,
            "not-json",
            {"digest": "b" * 64},
        )

        for marker in cases:
            with self.subTest(marker=marker):
                if self.root.exists():
                    import shutil

                    shutil.rmtree(self.root)
                final = self.create_final_entry(marker)

                self.assertIsNone(self.cache.lookup(self.key))
                self.assertTrue(final.is_dir())

    def test_lookup_rejects_missing_payload_and_non_directory_final(self):
        final = self.create_final_entry({"digest": self.key.digest})
        import shutil

        shutil.rmtree(final / "payload")
        self.assertIsNone(self.cache.lookup(self.key))

        shutil.rmtree(final)
        final.write_text("not a directory", encoding="utf-8")
        self.assertIsNone(self.cache.lookup(self.key))
        self.assertTrue(final.is_file())

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


if __name__ == "__main__":
    unittest.main()
