from __future__ import annotations

import hashlib
import json
import unittest
from unittest import mock

from maximum_optimizer import remapped_topology as remapped_module
from maximum_optimizer.remapped_topology import validate_remapped_topology_smd
from maximum_optimizer.visual_remapped_topology import (
    validate_visual_remapped_topology_smd,
    visual_remapped_topology_proof_from_payload,
    visual_remapped_topology_proof_payload,
)
from tests.maximum_optimizer.test_remapped_topology import (
    _corner,
    _diagonal_output,
    _fan_source,
    _smd,
    _two_diagonals,
    _two_fans,
)


def _boundary_change() -> str:
    return _smd([
        ("metal", ("a", "b", "e")),
        ("metal", ("a", "e", "d")),
    ])


def _reseal(payload: dict) -> None:
    copied = dict(payload)
    copied.pop("proof_sha256", None)
    payload["proof_sha256"] = hashlib.sha256(
        json.dumps(
            copied, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


class VisualRemappedTopologyContractTests(unittest.TestCase):
    def test_boundary_delta_passes_visual_contract_but_exact_contract_stays_closed(self) -> None:
        source = _fan_source()
        output = _boundary_change()

        with self.assertRaisesRegex(RuntimeError, "boundary edges"):
            validate_remapped_topology_smd(source, output, 0.5)
        proof = validate_visual_remapped_topology_smd(source, output, 0.5)

        self.assertEqual(proof.strategy, "meshopt-remapped-visual-v1")
        self.assertEqual(proof.transfer, "visual-remapped-topology-v1")
        self.assertEqual(proof.quality_status, "unverified")
        self.assertIsNone(proof.quality_claim)
        self.assertEqual((proof.triangles_before, proof.triangles_after), (4, 2))
        self.assertTrue(proof.target_reached)
        self.assertTrue(proof.boundary_changed)
        self.assertEqual(
            (
                proof.source_boundary_edges,
                proof.output_boundary_edges,
                proof.retained_boundary_edges,
                proof.removed_boundary_edges,
                proof.added_boundary_edges,
            ),
            (4, 4, 2, 2, 2),
        )
        self.assertEqual(len(proof.components), 1)
        component = proof.components[0]
        self.assertTrue(component.boundary_changed)
        self.assertEqual(
            (
                component.retained_boundary_edges,
                component.removed_boundary_edges,
                component.added_boundary_edges,
            ),
            (2, 2, 2),
        )
        self.assertEqual(
            visual_remapped_topology_proof_from_payload(
                visual_remapped_topology_proof_payload(proof)
            ),
            proof,
        )

    def test_exact_boundary_control_has_zero_visual_delta(self) -> None:
        proof = validate_visual_remapped_topology_smd(
            _fan_source(), _diagonal_output(), 0.5
        )

        self.assertFalse(proof.boundary_changed)
        self.assertEqual((proof.removed_boundary_edges, proof.added_boundary_edges), (0, 0))
        self.assertEqual(proof.source_boundary_sha256, proof.output_boundary_sha256)

    def test_non_boundary_structural_regressions_remain_rejected(self) -> None:
        source = _fan_source()
        variants = {
            "synthesized": _diagonal_output().replace(
                _corner("a"), _corner("a").replace(" 0 0 0 ", " 0.25 0 0 ", 1), 1
            ),
            "duplicate": _smd([
                ("metal", ("a", "b", "c")),
                ("metal", ("c", "a", "b")),
            ]),
            "reverse": _smd([
                ("metal", ("a", "b", "c")),
                ("metal", ("a", "c", "b")),
            ]),
            "degenerate": _smd([
                ("metal", ("a", "e", "c")),
                ("metal", ("a", "c", "d")),
            ]),
        }
        for name, output in variants.items():
            with self.subTest(name=name), self.assertRaises((RuntimeError, ValueError)):
                validate_visual_remapped_topology_smd(source, output, 0.5)

        deleted_component = _diagonal_output()
        with self.assertRaisesRegex(RuntimeError, "cover every source component"):
            validate_visual_remapped_topology_smd(
                _two_fans(), deleted_component, 0.5
            )
        bridged = _smd([
            ("metal", ("a", "b", "c")),
            ("metal", ("a", "c", "d")),
            ("metal", ("f", "g", "h")),
            ("metal", ("a", "h", "i")),
        ])
        with self.assertRaisesRegex(RuntimeError, "components"):
            validate_visual_remapped_topology_smd(_two_fans(), bridged, 0.5)

    def test_proof_loader_rejects_resealed_boundary_delta_lie(self) -> None:
        proof = validate_visual_remapped_topology_smd(
            _fan_source(), _boundary_change(), 0.5
        )
        payload = visual_remapped_topology_proof_payload(proof)
        payload["added_boundary_edges"] = 1
        _reseal(payload)

        with self.assertRaisesRegex(ValueError, "relationships"):
            visual_remapped_topology_proof_from_payload(payload)

    def test_proof_loader_rejects_resealed_boundary_hash_lie(self) -> None:
        proof = validate_visual_remapped_topology_smd(
            _fan_source(), _boundary_change(), 0.5
        )
        payload = visual_remapped_topology_proof_payload(proof)
        payload["components"][0]["added_boundary_sha256"] = "a" * 64
        _reseal(payload)

        with self.assertRaisesRegex(ValueError, "component proof"):
            visual_remapped_topology_proof_from_payload(payload)

        payload = visual_remapped_topology_proof_payload(proof)
        payload["added_boundary_sha256"] = "b" * 64
        _reseal(payload)

        with self.assertRaisesRegex(ValueError, "relationships"):
            visual_remapped_topology_proof_from_payload(payload)

    def test_proof_loader_rejects_resealed_duplicate_component_identity(self) -> None:
        proof = validate_visual_remapped_topology_smd(
            _two_fans(), _two_diagonals(), 0.5
        )
        payload = visual_remapped_topology_proof_payload(proof)
        duplicate = dict(payload["components"][0])
        duplicate["ordinal"] = 1
        payload["components"][1] = duplicate
        _reseal(payload)

        with self.assertRaisesRegex(ValueError, "relationships"):
            visual_remapped_topology_proof_from_payload(payload)

    def test_ambiguous_duplicate_direction_and_orientation_still_fail(self) -> None:
        shells = _smd([
            ("metal", ("a", "c", "b")),
            ("metal", ("a", "b", "l")),
            ("metal", ("a", "l", "c")),
            ("metal", ("b", "c", "l")),
            ("metal", ("a", "n", "m")),
            ("metal", ("a", "m", "o")),
            ("metal", ("a", "o", "n")),
            ("metal", ("m", "n", "o")),
        ], normal=("0", "0", "0"))
        second = _smd([
            ("metal", ("a", "n", "m")),
            ("metal", ("a", "m", "o")),
            ("metal", ("a", "o", "n")),
            ("metal", ("m", "n", "o")),
        ], normal=("0", "0", "0"))
        with self.assertRaisesRegex(RuntimeError, "ambiguous duplicate"):
            validate_visual_remapped_topology_smd(shells, second, 0.5)

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
        with self.assertRaisesRegex(RuntimeError, "direction conflicts"):
            validate_visual_remapped_topology_smd(
                zero_source, same_direction, 0.5
            )
        reversed_faces = _smd([
            ("metal", ("a", "c", "b")),
            ("metal", ("a", "d", "c")),
        ])
        with self.assertRaisesRegex(RuntimeError, "orientation conflicts"):
            validate_visual_remapped_topology_smd(
                _fan_source(), reversed_faces, 0.5
            )

    def test_shared_caps_and_strict_payload_fields_fail_closed(self) -> None:
        source, output = _fan_source(), _boundary_change()
        patches = {
            "byte cap": ("MAX_TEXT_BYTES", len(output.encode("utf-8")) - 1),
            "triangle cap": ("MAX_TRIANGLES", 3),
            "material cap": ("MAX_MATERIALS", 0),
            "component cap": ("MAX_COMPONENTS", 0),
        }
        for expected, (name, value) in patches.items():
            with (
                self.subTest(name=name),
                mock.patch.object(remapped_module, name, value),
                self.assertRaisesRegex(ValueError, expected),
            ):
                validate_visual_remapped_topology_smd(source, output, 0.5)

        proof = validate_visual_remapped_topology_smd(source, output, 0.5)
        payload = visual_remapped_topology_proof_payload(proof)
        payload["unknown"] = True
        with self.assertRaisesRegex(ValueError, "fields"):
            visual_remapped_topology_proof_from_payload(payload)


if __name__ == "__main__":
    unittest.main()
