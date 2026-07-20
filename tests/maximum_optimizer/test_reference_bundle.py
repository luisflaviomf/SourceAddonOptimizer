from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from maximum_optimizer.reference_bundle import (
    ReferenceBundleIdentity,
    ReferenceBundleStore,
)


class ReferenceBundleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = ReferenceBundleStore(self.base / "bundles")

    def identity(self, **changes: str) -> ReferenceBundleIdentity:
        values = {
            "family_id": "family-001",
            "family_hash": "a" * 64,
            "renderer_sha256": "b" * 64,
            "profile_sha256": "c" * 64,
            "dependency_digest": "d" * 64,
            "material_roots_sha256": "e" * 64,
            "vtfcmd_sha256": "f" * 64,
        }
        values.update(changes)
        return ReferenceBundleIdentity(**values)

    def make_complete_source(self) -> Path:
        source = self.base / "source"
        image = source / "engine-default" / "original" / "textured" / "bind" / "front.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"png-evidence")
        manifest = source / "engine-default" / "original" / "render_manifest.json"
        manifest.write_text(json.dumps({
            "schema": 1,
            "entries": [{
                "image": "textured/bind/front.png",
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            }],
        }), encoding="utf-8")
        return source

    def test_bundle_reopens_only_for_exact_identity(self) -> None:
        identity = self.identity()
        published = self.store.publish(identity, self.make_complete_source())

        reopened = self.store.lookup(identity)

        self.assertIsNotNone(reopened)
        self.assertEqual(reopened.root, published.root)
        self.assertIsNone(
            self.store.lookup(replace(identity, family_hash="0" * 64))
        )
        self.assertFalse(tuple(self.store.root.rglob(".pending-*")))

    def test_corrupt_bundle_is_not_reused(self) -> None:
        identity = self.identity()
        bundle = self.store.publish(identity, self.make_complete_source())
        manifest = bundle.root / "engine-default/original/render_manifest.json"
        manifest.write_bytes(b"corrupt")

        self.assertIsNone(self.store.lookup(identity))

    def test_publish_is_idempotent_and_does_not_replace_valid_evidence(self) -> None:
        identity = self.identity()
        first = self.store.publish(identity, self.make_complete_source())
        expected = (first.root / "engine-default/original/render_manifest.json").read_bytes()
        other = self.base / "other"
        (other / "engine-default/original").mkdir(parents=True)
        (other / "engine-default/original/render_manifest.json").write_bytes(b"other")

        second = self.store.publish(identity, other)

        self.assertEqual(second.root, first.root)
        self.assertEqual(
            (second.root / "engine-default/original/render_manifest.json").read_bytes(),
            expected,
        )

    def test_identity_rejects_unsafe_family_and_invalid_hashes(self) -> None:
        with self.assertRaises(ValueError):
            self.identity(family_id="../escape")
        with self.assertRaises(ValueError):
            self.identity(renderer_sha256="short")


if __name__ == "__main__":
    unittest.main()
