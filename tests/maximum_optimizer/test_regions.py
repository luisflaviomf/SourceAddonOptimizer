from __future__ import annotations

from dataclasses import replace
import unittest

from maximum_optimizer.regions import (
    REGION_DESCRIPTOR_SCHEMA,
    REGION_HASH_ALGORITHM,
    REGION_MANIFEST_SCHEMA_VERSION,
    RegionManifest,
    build_region_manifest,
    filter_region_manifest,
    resolve_region_assignments,
)


class RegionStateManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base_observations = (
            ("models/car/base.smd", "Body", ("paint", "glass")),
            ("models/car/base.smd", "Antenna", ("metal",)),
        )
        self.lod_observations = (
            ("models/car/lod1.smd", "Body.001", ("paint", "glass")),
            ("models/car/lod1.smd", "Antenna_LOD", ("metal",)),
        )
        self.occurrences = {
            "models/car/base.smd": (
                {
                    "graph_file": "vehicle.qc",
                    "directive": "$body",
                    "line": 4,
                    "logical_path": "models/car/base.smd",
                },
            ),
            "models/car/lod1.smd": (
                {
                    "graph_file": "vehicle.qc",
                    "directive": "$lod/replacemodel",
                    "line": 12,
                    "logical_path": "models/car/lod1.smd",
                },
            ),
        }
        self.manifest = build_region_manifest(
            self.base_observations + self.lod_observations,
            occurrences=self.occurrences,
        )

    def test_state_manifest_filters_real_base_and_lod_assignments(self) -> None:
        base_manifest = filter_region_manifest(
            self.manifest, ("./MODELS\\CAR\\BASE.SMD",)
        )
        lod_manifest = filter_region_manifest(
            self.manifest, ("models/car/lod1.smd",)
        )

        self.assertEqual(
            {entry.descriptor.source_identity for entry in base_manifest.entries},
            {"models/car/base.smd"},
        )
        self.assertEqual(
            {entry.descriptor.source_identity for entry in lod_manifest.entries},
            {"models/car/lod1.smd"},
        )
        self.assertEqual(
            set(resolve_region_assignments(
                base_manifest, self.base_observations, require_complete=True
            )),
            set(self.base_observations),
        )
        self.assertEqual(
            set(resolve_region_assignments(
                lod_manifest, self.lod_observations, require_complete=True
            )),
            set(self.lod_observations),
        )
        with self.assertRaisesRegex(ValueError, "missing region descriptors"):
            resolve_region_assignments(
                base_manifest, self.base_observations[:1], require_complete=True
            )
        with self.assertRaisesRegex(ValueError, "missing region descriptors"):
            resolve_region_assignments(
                lod_manifest, self.lod_observations[:1], require_complete=True
            )

        expected_base_occurrences = self.manifest.entries[0].occurrences
        self.assertTrue(base_manifest.entries)
        self.assertTrue(all(
            entry.occurrences == expected_base_occurrences
            for entry in base_manifest.entries
        ))

    def test_state_manifest_rebuilds_canonical_schema_and_keys(self) -> None:
        stale_entry = replace(self.manifest.entries[0], key="legacy-key")
        stale_manifest = RegionManifest(
            (stale_entry,) + self.manifest.entries[1:],
            schema_version=999,
            descriptor_schema="legacy-descriptor",
            hash_algorithm="legacy-hash",
        )

        filtered = filter_region_manifest(stale_manifest, ("models/car/base.smd",))
        expected = filter_region_manifest(self.manifest, ("models/car/base.smd",))

        self.assertEqual(filtered.entries, expected.entries)
        self.assertEqual(filtered.schema_version, REGION_MANIFEST_SCHEMA_VERSION)
        self.assertEqual(filtered.descriptor_schema, REGION_DESCRIPTOR_SCHEMA)
        self.assertEqual(filtered.hash_algorithm, REGION_HASH_ALGORITHM)

    def test_state_manifest_rejects_empty_duplicate_and_unknown_sources(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            filter_region_manifest(self.manifest, ())
        with self.assertRaisesRegex(ValueError, "duplicate"):
            filter_region_manifest(
                self.manifest,
                ("MODELS\\CAR\\BASE.SMD", "models/car/base.smd"),
            )
        with self.assertRaisesRegex(ValueError, "unknown"):
            filter_region_manifest(self.manifest, ("models/car/missing.smd",))


if __name__ == "__main__":
    unittest.main()
