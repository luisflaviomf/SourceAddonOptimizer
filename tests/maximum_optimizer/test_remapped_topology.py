from __future__ import annotations

import hashlib
import json
import unittest
from unittest import mock

from maximum_optimizer.candidates import _validate_direct_smd_output
from maximum_optimizer import remapped_topology as remapped_module
from maximum_optimizer.remapped_topology import (
    remapped_topology_proof_from_payload,
    remapped_topology_proof_payload,
    validate_remapped_topology_smd,
)


POSITIONS = {
    "a": ("0", "0", "0"),
    "b": ("1", "0", "0"),
    "c": ("1", "1", "0"),
    "d": ("0", "1", "0"),
    "e": ("0.5", "0.5", "0"),
    "f": ("3", "0", "0"),
    "g": ("4", "0", "0"),
    "h": ("4", "1", "0"),
    "i": ("3", "1", "0"),
    "j": ("3.5", "0.5", "0"),
}


def _corner(name: str, *, normal: tuple[str, str, str] = ("0", "0", "1")) -> str:
    x, y, z = POSITIONS[name]
    return f"0 {x} {y} {z} {' '.join(normal)} {x} {y}"


def _smd(
    triangles: list[tuple[str, tuple[str, str, str]]],
    *,
    normal: tuple[str, str, str] = ("0", "0", "1"),
) -> str:
    prefix = (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\n'
    )
    records = []
    for material, corners in triangles:
        records.append(material + "\n")
        records.extend(_corner(name, normal=normal) + "\n" for name in corners)
    return prefix + "".join(records) + "end\n"


def _fan_source() -> str:
    return _smd([
        ("metal", ("a", "b", "e")),
        ("metal", ("b", "c", "e")),
        ("metal", ("c", "d", "e")),
        ("metal", ("d", "a", "e")),
    ])


def _diagonal_output() -> str:
    return _smd([
        ("metal", ("a", "b", "c")),
        ("metal", ("a", "c", "d")),
    ])


def _two_fans(*, materials: tuple[str, str] = ("metal", "metal")) -> str:
    return _smd([
        (materials[0], ("a", "b", "e")),
        (materials[0], ("b", "c", "e")),
        (materials[0], ("c", "d", "e")),
        (materials[0], ("d", "a", "e")),
        (materials[1], ("f", "g", "j")),
        (materials[1], ("g", "h", "j")),
        (materials[1], ("h", "i", "j")),
        (materials[1], ("i", "f", "j")),
    ])


def _two_diagonals(*, materials: tuple[str, str] = ("metal", "metal")) -> str:
    return _smd([
        (materials[0], ("a", "b", "c")),
        (materials[0], ("a", "c", "d")),
        (materials[1], ("f", "g", "h")),
        (materials[1], ("f", "h", "i")),
    ])


def _reseal(payload: dict) -> dict:
    copied = dict(payload)
    copied.pop("proof_sha256", None)
    payload["proof_sha256"] = hashlib.sha256(
        json.dumps(copied, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return payload


class RemappedTopologyContractTests(unittest.TestCase):
    def test_accepts_exact_remapped_cycle_and_seals_deterministic_provenance(self) -> None:
        source = _fan_source()
        output = _diagonal_output()

        with self.assertRaisesRegex(RuntimeError, "cycle or winding"):
            _validate_direct_smd_output(source, output, 0.5)

        first = validate_remapped_topology_smd(source, output, 0.5)
        second = validate_remapped_topology_smd(source, output, 0.5)

        self.assertEqual(first, second)
        self.assertEqual(first.strategy, "meshopt-remapped-topology-v1")
        self.assertEqual(first.transfer, "remapped-topology-v1")
        self.assertEqual(first.quality_status, "unverified")
        self.assertIsNone(first.quality_claim)
        self.assertEqual((first.triangles_before, first.triangles_after), (4, 2))
        self.assertEqual((first.retained_cycles, first.remapped_cycles), (0, 2))
        self.assertEqual(first.source_component_count, 1)
        self.assertEqual(first.covered_component_count, 1)
        self.assertEqual(first.source_boundary_edges, 4)
        self.assertEqual(first.output_boundary_edges, 4)
        self.assertEqual(first.source_boundary_sha256, first.output_boundary_sha256)
        self.assertEqual(len(first.components), 1)
        component = first.components[0]
        self.assertEqual(component.material, "metal")
        self.assertEqual((component.source_triangles, component.output_triangles), (4, 2))
        self.assertEqual(component.source_boundary_sha256, component.output_boundary_sha256)
        self.assertTrue(component.covered)
        self.assertEqual(len(first.output_source_corner_ordinals), 6)
        self.assertEqual(first.output_source_corner_ordinals, (0, 1, 4, 10, 6, 7))
        self.assertEqual(
            remapped_topology_proof_from_payload(remapped_topology_proof_payload(first)),
            first,
        )

    def test_rejects_resealed_non_deterministic_material_target(self) -> None:
        proof = validate_remapped_topology_smd(_fan_source(), _diagonal_output(), 0.5)
        payload = remapped_topology_proof_payload(proof)
        payload["materials"][0]["target_triangles"] = 3
        _reseal(payload)

        with self.assertRaisesRegex(ValueError, "relationships"):
            remapped_topology_proof_from_payload(payload)

    def test_rejects_synthesized_or_cross_material_corner_payload(self) -> None:
        source = _two_fans(materials=("metal", "glass"))
        valid = _two_diagonals(materials=("metal", "glass"))
        variants = {
            "synthesized": valid.replace(_corner("f"), _corner("f").replace(" 3 0", " 3.25 0", 1), 1),
            "cross-material": valid.replace(_corner("f"), _corner("a"), 1),
        }
        for name, output in variants.items():
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "same-material source payload"):
                validate_remapped_topology_smd(source, output, 0.5)

    def test_rejects_cross_component_bridge_and_component_deletion(self) -> None:
        source = _two_fans()
        deleted = _diagonal_output()
        bridged = _smd([
            ("metal", ("a", "b", "c")),
            ("metal", ("a", "c", "d")),
            ("metal", ("f", "g", "h")),
            ("metal", ("a", "h", "i")),
        ])
        with self.assertRaisesRegex(RuntimeError, "cover every source component"):
            validate_remapped_topology_smd(source, deleted, 0.5)
        with self.assertRaisesRegex(RuntimeError, "crosses or ambiguously selects components"):
            validate_remapped_topology_smd(source, bridged, 0.5)

    def test_rejects_boundary_replacement_duplicate_reverse_and_degenerate(self) -> None:
        source = _fan_source()
        variants = {
            "boundary": _smd([
                ("metal", ("a", "b", "e")),
                ("metal", ("a", "e", "d")),
            ]),
            "duplicate": _smd([
                ("metal", ("a", "b", "c")),
                ("metal", ("c", "a", "b")),
            ]),
            "reverse-duplicate": _smd([
                ("metal", ("a", "b", "c")),
                ("metal", ("a", "c", "b")),
            ]),
            "degenerate": _smd([
                ("metal", ("a", "e", "c")),
                ("metal", ("a", "c", "d")),
            ]),
        }
        expected = {
            "boundary": "boundary edges",
            "duplicate": "duplicates an oriented or reversed triangle",
            "reverse-duplicate": "duplicates an oriented or reversed triangle",
            "degenerate": "degenerate triangle",
        }
        for name, output in variants.items():
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, expected[name]):
                validate_remapped_topology_smd(source, output, 0.5)

    def test_rejects_new_direction_and_normal_hemisphere_conflicts(self) -> None:
        zero_source = _smd([
            ("metal", ("a", "b", "e")),
            ("metal", ("b", "c", "e")),
            ("metal", ("c", "d", "e")),
            ("metal", ("d", "a", "e")),
        ], normal=("0", "0", "0"))
        same_direction = _smd([
            ("metal", ("a", "b", "c")),
            ("metal", ("a", "d", "c")),
        ], normal=("0", "0", "0"))
        reversed_faces = _smd([
            ("metal", ("a", "c", "b")),
            ("metal", ("a", "d", "c")),
        ])
        with self.assertRaisesRegex(RuntimeError, "direction conflicts"):
            validate_remapped_topology_smd(zero_source, same_direction, 0.5)
        with self.assertRaisesRegex(RuntimeError, "orientation conflicts"):
            validate_remapped_topology_smd(_fan_source(), reversed_faces, 0.5)

    def test_fixed_caps_fail_closed(self) -> None:
        source, output = _fan_source(), _diagonal_output()
        patches = {
            "byte cap": ("MAX_TEXT_BYTES", len(output.encode("utf-8")) - 1),
            "triangle cap": ("MAX_TRIANGLES", 3),
            "material cap": ("MAX_MATERIALS", 0),
            "component cap": ("MAX_COMPONENTS", 0),
        }
        for expected, (name, value) in patches.items():
            with self.subTest(name=name), mock.patch.object(remapped_module, name, value), self.assertRaisesRegex(ValueError, expected):
                validate_remapped_topology_smd(source, output, 0.5)

        wide = _corner("a") + " 28" + "".join(f" {index} 0.0357142857" for index in range(28))
        wide_source = source.replace(_corner("a"), wide, 1)
        with self.assertRaisesRegex(ValueError, "token cap"):
            validate_remapped_topology_smd(wide_source, output, 0.5)

    def test_proof_parser_rejects_unknown_fields_tampering_and_out_of_range_ordinals(self) -> None:
        proof = validate_remapped_topology_smd(_fan_source(), _diagonal_output(), 0.5)
        variants = []
        unknown = remapped_topology_proof_payload(proof); unknown["extra"] = 1; variants.append(unknown)
        stale = remapped_topology_proof_payload(proof); stale["triangles_after"] = 1; variants.append(stale)
        ordinal = remapped_topology_proof_payload(proof)
        ordinal["output_source_corner_ordinals"][0] = proof.triangles_before * 3
        _reseal(ordinal); variants.append(ordinal)
        for payload in variants:
            with self.subTest(keys=tuple(payload)), self.assertRaises(ValueError):
                remapped_topology_proof_from_payload(payload)


if __name__ == "__main__":
    unittest.main()
