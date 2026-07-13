from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from maximum_optimizer.domain import SourceFileProof
from maximum_optimizer.smd_state_contracts import (
    SmdAnimationPairInput,
    build_smd_equivalence_contract,
    build_smd_pose_contract,
    build_smd_skeleton_pair_contract,
    build_smd_skeleton_contract,
)


def _smd(*, frames=(0,), malformed: str = "") -> bytes:
    nodes = '0 "root" -1\n1 "child" 0\n'
    transforms = []
    for frame in frames:
        transforms.extend((f"time {frame}", "0 0 0 0 0 0 0", f"1 {frame} 0 0 0 0 0"))
    text = (
        "version 1\nnodes\n" + nodes + "end\nskeleton\n"
        + "\n".join(transforms) + "\nend\ntriangles\nend\n"
    )
    if malformed == "duplicate-node":
        text = text.replace('1 "child" 0', '0 "child" 0')
    elif malformed == "invalid-parent":
        text = text.replace('1 "child" 0', '1 "child" 9')
    elif malformed == "missing-bind":
        text = text.replace("1 0 0 0 0 0 0\n", "")
    elif malformed == "nonfinite":
        text = text.replace("1 0 0 0 0 0 0", "1 nan 0 0 0 0 0")
    return text.encode()


def _proof(root: Path, path: Path, kind: str) -> SourceFileProof:
    raw = path.read_bytes()
    relative = path.relative_to(root).as_posix()
    return SourceFileProof(relative.casefold(), kind, relative, len(raw), hashlib.sha256(raw).hexdigest())


class SmdStateContractTests(unittest.TestCase):
    def test_skeleton_pair_seals_exact_nodes_hierarchy_and_bind_equivalence(self) -> None:
        with tempfile.TemporaryDirectory() as original_raw, tempfile.TemporaryDirectory() as candidate_raw:
            original_root = Path(original_raw); candidate_root = Path(candidate_raw)
            original_path = original_root / "body.smd"
            candidate_path = candidate_root / "body.smd"
            original_path.write_bytes(_smd())
            candidate_path.write_bytes(_smd())
            original = build_smd_skeleton_contract(
                original_path, original_root,
                _proof(original_root, original_path, "visual-source"), None,
            )
            candidate = build_smd_skeleton_contract(
                candidate_path, candidate_root,
                _proof(candidate_root, candidate_path, "visual-source"), None,
            )
            comparison = build_smd_skeleton_pair_contract(original, candidate)
            self.assertEqual(comparison.original_skeleton_sha256, original.skeleton_contract_sha256)
            self.assertEqual(comparison.candidate_skeleton_sha256, candidate.skeleton_contract_sha256)
            with self.assertRaisesRegex(ValueError, "nodes|hierarchy|bind|equivalent"):
                changed_path = candidate_root / "changed.smd"
                changed_path.write_bytes(_smd().replace(b'1 "child" 0', b'1 "other" 0'))
                changed = build_smd_skeleton_contract(
                    changed_path, candidate_root,
                    _proof(candidate_root, changed_path, "visual-source"), None,
                )
                build_smd_skeleton_pair_contract(original, changed)

    def test_public_contracts_reject_forged_seals(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); path = root / "body.smd"; path.write_bytes(_smd())
            proof = _proof(root, path, "visual-source")
            skeleton = build_smd_skeleton_contract(path, root, proof, None)
            pose = build_smd_pose_contract(skeleton, animation_pair=None, cancel_event=None)
            equivalence = build_smd_equivalence_contract("body.smd", proof, skeleton, pose)
            for value, field in (
                (skeleton, "skeleton_contract_sha256"),
                (pose, "pose_contract_sha256"),
                (equivalence, "equivalence_class_sha256"),
            ):
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, "seal"):
                    replace(value, **{field: "f" * 64})

    def test_skeleton_and_equivalence_are_canonical_and_root_independent(self) -> None:
        values = []
        for _ in range(2):
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name)
            path = root / "body.smd"; path.write_bytes(_smd())
            proof = _proof(root, path, "visual-source")
            skeleton = build_smd_skeleton_contract(path, root, proof, None)
            pose = build_smd_pose_contract(skeleton, animation_pair=None, cancel_event=None)
            equivalence = build_smd_equivalence_contract("body.smd", proof, skeleton, pose)
            values.append((skeleton, pose, equivalence))
        first, second = values
        self.assertEqual(first[0].skeleton_contract_sha256, second[0].skeleton_contract_sha256)
        self.assertEqual(first[0].nodes[1].parent_id, 0)
        self.assertEqual(tuple(item.bone_id for item in first[0].bind), (0, 1))
        self.assertEqual(first[1].pose_keys, ("bind",))
        self.assertEqual(first[2].equivalence_class_sha256, second[2].equivalence_class_sha256)

    def test_rejects_malformed_stale_or_symlinked_current_smd(self) -> None:
        for malformed in ("duplicate-node", "invalid-parent", "missing-bind", "nonfinite"):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as raw:
                root = Path(raw); path = root / "body.smd"; path.write_bytes(_smd(malformed=malformed))
                with self.assertRaises(ValueError):
                    build_smd_skeleton_contract(path, root, _proof(root, path, "visual-source"), None)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); path = root / "body.smd"; path.write_bytes(_smd())
            proof = _proof(root, path, "visual-source")
            path.write_bytes(_smd() + b"// stale\n")
            with self.assertRaisesRegex(ValueError, "proof|bytes|current"):
                build_smd_skeleton_contract(path, root, proof, None)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); target = root / "target.smd"; target.write_bytes(_smd())
            link = root / "body.smd"
            try:
                os.symlink(target, link)
            except OSError:
                return
            proof = _proof(root, target, "visual-source")
            proof = SourceFileProof("body.smd", "visual-source", "body.smd", proof.size, proof.sha256)
            with self.assertRaises(ValueError):
                build_smd_skeleton_contract(link, root, proof, None)

    def test_unambiguous_animation_pair_adds_one_deterministic_pose(self) -> None:
        with tempfile.TemporaryDirectory() as original_raw, tempfile.TemporaryDirectory() as candidate_raw:
            original_root = Path(original_raw); candidate_root = Path(candidate_raw)
            body = original_root / "body.smd"; body.write_bytes(_smd())
            skeleton = build_smd_skeleton_contract(
                body, original_root, _proof(original_root, body, "visual-source"), None,
            )
            original_anim = original_root / "anim.smd"; original_anim.write_bytes(_smd(frames=(0, 5, 12)))
            candidate_anim = candidate_root / "anim.smd"; candidate_anim.write_bytes(_smd(frames=(0, 5, 12)))
            pair = SmdAnimationPairInput(
                original_anim, original_root, _proof(original_root, original_anim, "animation-source"),
                candidate_anim, candidate_root, _proof(candidate_root, candidate_anim, "animation-source"),
            )
            pose = build_smd_pose_contract(skeleton, animation_pair=pair, cancel_event=None)
            self.assertEqual(pose.pose_keys, ("bind", "animation"))
            self.assertEqual(pose.animation_frames, (0, 5, 12))
            self.assertEqual(pose.representative_frame, 12)

    def test_supplied_animation_pair_fails_closed_on_frame_node_or_finite_mismatch(self) -> None:
        mutations = {
            "frames": _smd(frames=(0, 6)),
            "nodes": _smd(frames=(0, 5)).replace(b'1 "child" 0', b'1 "other" 0'),
            "finite": _smd(frames=(0, 5)).replace(b"1 5 0 0 0 0 0", b"1 nan 0 0 0 0 0"),
        }
        for label, candidate_bytes in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as original_raw, tempfile.TemporaryDirectory() as candidate_raw:
                original_root = Path(original_raw); candidate_root = Path(candidate_raw)
                body = original_root / "body.smd"; body.write_bytes(_smd())
                skeleton = build_smd_skeleton_contract(
                    body, original_root, _proof(original_root, body, "visual-source"), None,
                )
                original_anim = original_root / "anim.smd"; original_anim.write_bytes(_smd(frames=(0, 5)))
                candidate_anim = candidate_root / "anim.smd"; candidate_anim.write_bytes(candidate_bytes)
                pair = SmdAnimationPairInput(
                    original_anim, original_root, _proof(original_root, original_anim, "animation-source"),
                    candidate_anim, candidate_root, _proof(candidate_root, candidate_anim, "animation-source"),
                )
                with self.assertRaises(ValueError):
                    build_smd_pose_contract(skeleton, animation_pair=pair, cancel_event=None)


if __name__ == "__main__":
    unittest.main()
