from __future__ import annotations

from dataclasses import replace
import hashlib
import unittest

from maximum_optimizer.reporting import canonical_json
from maximum_optimizer.source_components import (
    build_source_component_manifest,
    build_source_component_transfer,
    current_filtered_source_component_bytes,
    require_current_source_component_transfer,
    source_component_manifest_from_payload,
    source_component_manifest_payload,
    source_component_transfer_from_payload,
    source_component_transfer_payload,
    validate_source_component_transfer_against_manifest,
)
from maximum_optimizer.smd_contract import prefilter_direct_degenerate_smd


def _smd(records: tuple[tuple[str, tuple[tuple[float, float, float], ...]], ...]) -> bytes:
    triangles = []
    for material, corners in records:
        triangles.append(material + "\n")
        for x, y, z in corners:
            triangles.append(f"0 {x:g} {y:g} {z:g} 0 0 1 0 0\n")
    return (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\n' + "".join(triangles) + "end\n"
    ).encode("utf-8")


class SourceComponentContractTests(unittest.TestCase):
    def test_manifest_uses_only_post_prefilter_topology_and_binds_both_streams(self) -> None:
        source = _smd((
            ("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0))),
            # Degenerate bridge touches both otherwise-disconnected islands.
            ("paint", ((0, 0, 0), (10, 0, 0), (10, 0, 0))),
            ("paint", ((10, 0, 0), (11, 0, 0), (10, 1, 0))),
        ))
        prefilter = prefilter_direct_degenerate_smd(source.decode("utf-8"))

        manifest = build_source_component_manifest(source)

        self.assertEqual(manifest.source_sha256, hashlib.sha256(source).hexdigest())
        self.assertEqual(manifest.prefilter_evidence_sha256, prefilter.evidence["evidence_sha256"])
        self.assertEqual(
            manifest.filtered_source_sha256,
            hashlib.sha256(prefilter.filtered_text.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(manifest.triangle_count, 2)
        self.assertEqual(
            tuple(item.triangle_ordinals for item in manifest.components),
            ((0,), (1,)),
        )

    def test_manifest_rejects_non_partition_and_more_than_256_components(self) -> None:
        source = _smd((
            ("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0))),
            ("paint", ((5, 0, 0), (6, 0, 0), (5, 1, 0))),
        ))
        manifest = build_source_component_manifest(source)
        with self.assertRaisesRegex(ValueError, "partition"):
            replace(
                manifest,
                components=(replace(manifest.components[0], triangle_ordinals=(0, 2)),),
            )

        many = _smd(tuple(
            ("paint", ((i * 3, 0, 0), (i * 3 + 1, 0, 0), (i * 3, 1, 0)))
            for i in range(257)
        ))
        with self.assertRaisesRegex(ValueError, "component bound"):
            build_source_component_manifest(many)

    def test_manifest_requires_strict_utf8_and_exact_smd_eof(self) -> None:
        source = _smd((("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0))),))
        with self.assertRaises((UnicodeError, ValueError)):
            build_source_component_manifest(source + b"\xff")
        with self.assertRaisesRegex(ValueError, "trailing|malformed"):
            build_source_component_manifest(source + b"junk\n")

    def test_exact_position_connectivity_joins_seams_but_not_nearby_vertices(self) -> None:
        source = _smd((
            ("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0))),
            # Same position but different UV/normal rows would still be one geometric island.
            ("glass", ((0, 0, 0), (-1, 0, 0), (0, -1, 0))),
            ("paint", ((0.000001, 0, 0), (2, 0, 0), (2, 1, 0))),
        ))
        manifest = build_source_component_manifest(source)
        self.assertEqual(
            tuple(item.triangle_ordinals for item in manifest.components),
            ((0, 1), (2,)),
        )

    def test_transfer_maps_reordered_cyclic_candidate_to_original_components(self) -> None:
        first = ("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0)))
        bridge = ("paint", ((1, 0, 0), (1, 1, 0), (0, 1, 0)))
        second = ("paint", ((10, 0, 0), (11, 0, 0), (10, 1, 0)))
        source = _smd((first, bridge, second))
        # Reordered, and the retained triangle starts at another cyclic corner.
        candidate = _smd(((second[0], second[1][1:] + second[1][:1]), first))
        manifest = build_source_component_manifest(source)

        transfer = build_source_component_transfer(manifest, source, candidate)

        self.assertEqual(
            tuple((item.candidate_triangle_ordinal, item.source_triangle_ordinal, item.component_key)
                  for item in transfer.triangles),
            ((0, 2, "component-001"), (1, 0, "component-000")),
        )
        self.assertEqual(transfer.candidate_sha256, hashlib.sha256(candidate).hexdigest())

    def test_transfer_rejects_duplicate_identical_source_cycle_ambiguity(self) -> None:
        triangle = ("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0)))
        source = _smd((triangle, triangle))
        manifest = build_source_component_manifest(source)
        candidate = _smd((triangle,))
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            build_source_component_transfer(manifest, source, candidate)

    def test_transfer_rejects_changed_winding_or_corner_payload(self) -> None:
        first = ("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0)))
        second = ("paint", ((10, 0, 0), (11, 0, 0), (10, 1, 0)))
        source = _smd((first, second))
        manifest = build_source_component_manifest(source)
        reversed_candidate = _smd(((first[0], (first[1][0], first[1][2], first[1][1])),))
        with self.assertRaisesRegex(RuntimeError, "cycle|winding"):
            build_source_component_transfer(manifest, source, reversed_candidate)

    def test_exact_payload_parsers_round_trip_and_reject_extra_state(self) -> None:
        first = ("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0)))
        second = ("paint", ((10, 0, 0), (11, 0, 0), (10, 1, 0)))
        source = _smd((first, second))
        candidate = _smd((first,))
        manifest = build_source_component_manifest(source)
        transfer = build_source_component_transfer(manifest, source, candidate)
        manifest_payload = source_component_manifest_payload(manifest)
        transfer_payload = source_component_transfer_payload(transfer)
        self.assertEqual(source_component_manifest_from_payload(manifest_payload), manifest)
        self.assertEqual(source_component_transfer_from_payload(transfer_payload), transfer)
        with self.assertRaisesRegex(ValueError, "fields"):
            source_component_manifest_from_payload({**manifest_payload, "hidden": True})
        with self.assertRaisesRegex(ValueError, "fields"):
            source_component_transfer_from_payload({**transfer_payload, "hidden": True})

    def test_current_byte_helpers_return_prefiltered_reference_and_reject_mutation(self) -> None:
        first = ("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0)))
        degenerate = ("paint", ((0, 0, 0), (2, 0, 0), (2, 0, 0)))
        second = ("paint", ((10, 0, 0), (11, 0, 0), (10, 1, 0)))
        source = _smd((first, degenerate, second))
        candidate = _smd((first,))
        manifest = build_source_component_manifest(source)
        transfer = build_source_component_transfer(manifest, source, candidate)
        self.assertEqual(
            current_filtered_source_component_bytes(manifest, source),
            prefilter_direct_degenerate_smd(source.decode("utf-8")).filtered_text.encode("utf-8"),
        )
        require_current_source_component_transfer(transfer, manifest, source, candidate)
        with self.assertRaisesRegex(ValueError, "current source bytes"):
            current_filtered_source_component_bytes(manifest, source.replace(b"paint", b"metal", 1))
        with self.assertRaisesRegex(ValueError, "current candidate bytes"):
            require_current_source_component_transfer(
                transfer, manifest, source, candidate.replace(b"paint", b"metal", 1)
            )

    def test_cross_object_validator_rejects_forged_component_assignment_with_valid_seal(self) -> None:
        first = ("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0)))
        second = ("paint", ((10, 0, 0), (11, 0, 0), (10, 1, 0)))
        source = _smd((first, second))
        candidate = _smd((first,))
        manifest = build_source_component_manifest(source)
        transfer = build_source_component_transfer(manifest, source, candidate)
        payload = source_component_transfer_payload(transfer)
        payload["triangles"][0]["component_key"] = "component-001"
        unsigned = dict(payload)
        unsigned.pop("transfer_sha256")
        payload["transfer_sha256"] = hashlib.sha256(
            canonical_json(unsigned).encode()
        ).hexdigest()
        forged = source_component_transfer_from_payload(payload)
        with self.assertRaisesRegex(ValueError, "manifest"):
            validate_source_component_transfer_against_manifest(forged, manifest)

    def test_typed_contracts_reject_prefilter_hash_mutation_and_transfer_gaps(self) -> None:
        first = ("paint", ((0, 0, 0), (1, 0, 0), (0, 1, 0)))
        second = ("paint", ((10, 0, 0), (11, 0, 0), (10, 1, 0)))
        source = _smd((first, second))
        manifest = build_source_component_manifest(source)
        with self.assertRaisesRegex(ValueError, "seal"):
            replace(manifest, prefilter_evidence_sha256="0" * 64)

        transfer = build_source_component_transfer(manifest, source, _smd((first,)))
        payload = source_component_transfer_payload(transfer)
        payload["triangle_count"] = 2
        unsigned = dict(payload)
        unsigned.pop("transfer_sha256")
        payload["transfer_sha256"] = hashlib.sha256(
            canonical_json(unsigned).encode()
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "transfer"):
            source_component_transfer_from_payload(payload)


if __name__ == "__main__":
    unittest.main()
