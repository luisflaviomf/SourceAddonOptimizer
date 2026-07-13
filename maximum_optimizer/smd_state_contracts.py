from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path, PurePosixPath
import re
import threading

from .domain import SourceFileProof, require_canonical_relative
from .focused_cache import _read_regular_no_follow
from .reporting import canonical_json


_MAX_SMD_BYTES = 64 * 1024 * 1024
_MAX_NODES = 4096
_MAX_FRAMES = 4096
_NODE_RE = re.compile(r'^(-?\d+)\s+"([^"]*)"\s+(-?\d+)$')
_TIME_RE = re.compile(r"^time\s+(-?\d+)$", re.IGNORECASE)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _seal(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SmdNodeProof:
    bone_id: int
    name: str
    parent_id: int

    def __post_init__(self) -> None:
        if (
            type(self.bone_id) is not int or self.bone_id < 0
            or type(self.name) is not str or not self.name
            or type(self.parent_id) is not int or self.parent_id < -1
        ):
            raise ValueError("SMD node proof is invalid")


@dataclass(frozen=True)
class SmdTransformProof:
    bone_id: int
    values: tuple[float, float, float, float, float, float]

    def __post_init__(self) -> None:
        values = tuple(self.values)
        if (
            type(self.bone_id) is not int or self.bone_id < 0
            or len(values) != 6
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in values)
            or any(not math.isfinite(float(item)) for item in values)
        ):
            raise ValueError("SMD transform proof is invalid")
        object.__setattr__(
            self, "values", tuple(0.0 if float(item) == 0.0 else float(item) for item in values)
        )


@dataclass(frozen=True)
class SmdSkeletonContract:
    schema: int
    algorithm: str
    source_size: int
    source_sha256: str
    nodes: tuple[SmdNodeProof, ...]
    bind: tuple[SmdTransformProof, ...]
    skeleton_contract_sha256: str

    def __post_init__(self) -> None:
        nodes = tuple(self.nodes)
        bind = tuple(self.bind)
        if (
            type(self.schema) is not int or self.schema != 1
            or self.algorithm != "smd-nodes-bind-v1"
            or type(self.source_size) is not int or self.source_size < 0
            or _SHA256_RE.fullmatch(self.source_sha256) is None
            or not nodes or len(nodes) > _MAX_NODES
            or any(not isinstance(item, SmdNodeProof) for item in nodes)
            or tuple(item.bone_id for item in nodes) != tuple(sorted(item.bone_id for item in nodes))
            or len({item.bone_id for item in nodes}) != len(nodes)
            or any(not isinstance(item, SmdTransformProof) for item in bind)
            or tuple(item.bone_id for item in bind) != tuple(item.bone_id for item in nodes)
        ):
            raise ValueError("SMD skeleton contract is invalid")
        node_ids = {item.bone_id for item in nodes}
        if any(item.parent_id != -1 and item.parent_id not in node_ids for item in nodes):
            raise ValueError("SMD skeleton contract parent is invalid")
        by_id = {item.bone_id: item for item in nodes}
        for node in nodes:
            visited = set()
            current = node
            while current.parent_id != -1:
                if current.bone_id in visited:
                    raise ValueError("SMD skeleton contract has a parent cycle")
                visited.add(current.bone_id)
                current = by_id[current.parent_id]
        payload = {
            "schema": self.schema, "algorithm": self.algorithm,
            "source_size": self.source_size, "source_sha256": self.source_sha256,
            "nodes": [_node_payload(item) for item in nodes],
            "bind": [_transform_payload(item) for item in bind],
        }
        if _SHA256_RE.fullmatch(self.skeleton_contract_sha256) is None or _seal(payload) != self.skeleton_contract_sha256:
            raise ValueError("SMD skeleton contract seal mismatch")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "bind", bind)


@dataclass(frozen=True)
class SmdAnimationPairInput:
    original_path: Path
    original_root: Path
    original_proof: SourceFileProof
    candidate_path: Path
    candidate_root: Path
    candidate_proof: SourceFileProof

    def __post_init__(self) -> None:
        for field, label in (
            ("original_path", "original animation"),
            ("original_root", "original animation root"),
            ("candidate_path", "candidate animation"),
            ("candidate_root", "candidate animation root"),
        ):
            path = Path(getattr(self, field))
            if not Path(path).is_absolute():
                raise ValueError(f"{label} path must be absolute")
            object.__setattr__(self, field, path)
        if (
            not isinstance(self.original_proof, SourceFileProof)
            or not isinstance(self.candidate_proof, SourceFileProof)
            or self.original_proof.kind != "animation-source"
            or self.candidate_proof.kind != "animation-source"
        ):
            raise ValueError("SMD animation pair proofs are invalid")


@dataclass(frozen=True)
class SmdPoseContract:
    schema: int
    algorithm: str
    skeleton_contract_sha256: str
    pose_keys: tuple[str, ...]
    animation_original_sha256: str | None
    animation_candidate_sha256: str | None
    animation_frames: tuple[int, ...]
    representative_frame: int | None
    pose_contract_sha256: str

    def __post_init__(self) -> None:
        poses = tuple(self.pose_keys)
        frames = tuple(self.animation_frames)
        common_valid = (
            type(self.schema) is int and self.schema == 1
            and self.algorithm == "smd-bind-animation-pair-v1"
            and _SHA256_RE.fullmatch(self.skeleton_contract_sha256) is not None
            and poses in {("bind",), ("bind", "animation")}
            and all(type(item) is int and item >= 0 for item in frames)
            and frames == tuple(sorted(frames)) and len(set(frames)) == len(frames)
        )
        if poses == ("bind",):
            variant_valid = (
                self.animation_original_sha256 is None
                and self.animation_candidate_sha256 is None
                and not frames and self.representative_frame is None
            )
        else:
            positives = tuple(item for item in frames if item > 0)
            variant_valid = (
                _SHA256_RE.fullmatch(self.animation_original_sha256 or "") is not None
                and self.animation_original_sha256 == self.animation_candidate_sha256
                and bool(positives) and self.representative_frame == max(positives)
            )
        payload = {
            "schema": self.schema, "algorithm": self.algorithm,
            "skeleton_contract_sha256": self.skeleton_contract_sha256,
            "pose_keys": list(poses),
            "animation_original_sha256": self.animation_original_sha256,
            "animation_candidate_sha256": self.animation_candidate_sha256,
            "animation_frames": list(frames),
            "representative_frame": self.representative_frame,
        }
        if (
            not common_valid or not variant_valid
            or _SHA256_RE.fullmatch(self.pose_contract_sha256) is None
            or _seal(payload) != self.pose_contract_sha256
        ):
            raise ValueError("SMD pose contract is invalid or its seal differs")
        object.__setattr__(self, "pose_keys", poses)
        object.__setattr__(self, "animation_frames", frames)


@dataclass(frozen=True)
class SmdEquivalenceContract:
    schema: int
    algorithm: str
    source_identity: str
    source_size: int
    source_sha256: str
    skeleton_contract_sha256: str
    pose_contract_sha256: str
    equivalence_class_sha256: str

    def __post_init__(self) -> None:
        identity = require_canonical_relative(self.source_identity, "SMD equivalence source")
        payload = {
            "schema": self.schema, "algorithm": self.algorithm,
            "source_identity": identity, "source_size": self.source_size,
            "source_sha256": self.source_sha256,
            "skeleton_contract_sha256": self.skeleton_contract_sha256,
            "pose_contract_sha256": self.pose_contract_sha256,
        }
        if (
            type(self.schema) is not int or self.schema != 1
            or self.algorithm != "adaptive-source-state-equivalence-v1"
            or type(self.source_size) is not int or self.source_size < 0
            or any(_SHA256_RE.fullmatch(value) is None for value in (
                self.source_sha256, self.skeleton_contract_sha256,
                self.pose_contract_sha256, self.equivalence_class_sha256,
            ))
            or _seal(payload) != self.equivalence_class_sha256
        ):
            raise ValueError("SMD equivalence contract is invalid or its seal differs")
        object.__setattr__(self, "source_identity", identity)


def _node_payload(value: SmdNodeProof) -> dict[str, object]:
    return {"bone_id": value.bone_id, "name": value.name, "parent_id": value.parent_id}


def _transform_payload(value: SmdTransformProof) -> dict[str, object]:
    return {"bone_id": value.bone_id, "values": list(value.values)}


def smd_skeleton_contract_payload(value: SmdSkeletonContract) -> dict[str, object]:
    return {
        "schema": value.schema, "algorithm": value.algorithm,
        "source_size": value.source_size, "source_sha256": value.source_sha256,
        "nodes": [_node_payload(item) for item in value.nodes],
        "bind": [_transform_payload(item) for item in value.bind],
        "skeleton_contract_sha256": value.skeleton_contract_sha256,
    }


def smd_pose_contract_payload(value: SmdPoseContract) -> dict[str, object]:
    return {
        "schema": value.schema, "algorithm": value.algorithm,
        "skeleton_contract_sha256": value.skeleton_contract_sha256,
        "pose_keys": list(value.pose_keys),
        "animation_original_sha256": value.animation_original_sha256,
        "animation_candidate_sha256": value.animation_candidate_sha256,
        "animation_frames": list(value.animation_frames),
        "representative_frame": value.representative_frame,
        "pose_contract_sha256": value.pose_contract_sha256,
    }


def smd_equivalence_contract_payload(value: SmdEquivalenceContract) -> dict[str, object]:
    return {
        "schema": value.schema, "algorithm": value.algorithm,
        "source_identity": value.source_identity,
        "source_size": value.source_size, "source_sha256": value.source_sha256,
        "skeleton_contract_sha256": value.skeleton_contract_sha256,
        "pose_contract_sha256": value.pose_contract_sha256,
        "equivalence_class_sha256": value.equivalence_class_sha256,
    }


def _unsigned(payload: dict[str, object], seal_name: str) -> dict[str, object]:
    result = dict(payload)
    result.pop(seal_name, None)
    return result


def _read_bound(
    path: Path, root: Path, proof: SourceFileProof,
    cancel_event: threading.Event | None,
    *, expected_kind: str,
) -> bytes:
    if not isinstance(proof, SourceFileProof) or proof.kind != expected_kind:
        raise ValueError("SMD SourceFileProof kind is invalid")
    root = Path(root)
    path = Path(path)
    try:
        relative = path.absolute().relative_to(root.absolute()).as_posix()
    except ValueError as exc:
        raise ValueError("SMD path escapes proof root") from exc
    relative = require_canonical_relative(relative, "SMD proof path")
    if relative != proof.relative_path:
        raise ValueError("SMD path differs from SourceFileProof")
    raw = _read_regular_no_follow(
        path, cancel_event, contained_root=root, max_bytes=_MAX_SMD_BYTES,
    )
    if (len(raw), hashlib.sha256(raw).hexdigest()) != (proof.size, proof.sha256):
        raise ValueError("current SMD bytes differ from SourceFileProof")
    return raw


def _parse_nodes_and_frames(
    raw: bytes,
) -> tuple[tuple[SmdNodeProof, ...], tuple[tuple[int, tuple[SmdTransformProof, ...]], ...]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("SMD is not UTF-8") from exc
    section = ""
    saw_nodes = False
    saw_skeleton = False
    nodes: dict[int, SmdNodeProof] = {}
    frames: list[tuple[int, dict[int, SmdTransformProof]]] = []
    current_frame: dict[int, SmdTransformProof] | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        folded = line.casefold()
        if not section:
            if folded == "nodes":
                if saw_nodes:
                    raise ValueError("SMD nodes section is duplicated")
                saw_nodes = True; section = "nodes"
            elif folded == "skeleton":
                if saw_skeleton:
                    raise ValueError("SMD skeleton section is duplicated")
                saw_skeleton = True; section = "skeleton"
            continue
        if folded == "end":
            section = ""
            current_frame = None
            continue
        if section == "nodes":
            match = _NODE_RE.fullmatch(line)
            if match is None:
                raise ValueError("SMD node record is invalid")
            bone_id, name, parent_id = int(match.group(1)), match.group(2), int(match.group(3))
            if bone_id < 0 or bone_id in nodes or not name:
                raise ValueError("SMD node identity is invalid or duplicated")
            nodes[bone_id] = SmdNodeProof(bone_id, name, parent_id)
        elif section == "skeleton":
            time_match = _TIME_RE.fullmatch(line)
            if time_match is not None:
                frame_id = int(time_match.group(1))
                if frame_id < 0 or any(item[0] == frame_id for item in frames):
                    raise ValueError("SMD skeleton frame is invalid or duplicated")
                current_frame = {}
                frames.append((frame_id, current_frame))
                continue
            if current_frame is None:
                raise ValueError("SMD skeleton transform has no frame")
            fields = line.split()
            if len(fields) != 7:
                raise ValueError("SMD skeleton transform is invalid")
            try:
                bone_id = int(fields[0])
                numeric = tuple(float(item) for item in fields[1:])
            except ValueError as exc:
                raise ValueError("SMD skeleton transform is nonnumeric") from exc
            if (
                bone_id in current_frame or len(numeric) != 6
                or any(not math.isfinite(item) for item in numeric)
            ):
                raise ValueError("SMD skeleton transform is duplicated or nonfinite")
            normalized = tuple(0.0 if item == 0.0 else item for item in numeric)
            current_frame[bone_id] = SmdTransformProof(bone_id, normalized)  # type: ignore[arg-type]
    if section or not saw_nodes or not saw_skeleton or not nodes or not frames:
        raise ValueError("SMD nodes/skeleton contract is incomplete")
    if len(nodes) > _MAX_NODES or len(frames) > _MAX_FRAMES:
        raise ValueError("SMD nodes/skeleton contract exceeds bound")
    node_ids = set(nodes)
    if any(item.parent_id != -1 and item.parent_id not in node_ids for item in nodes.values()):
        raise ValueError("SMD node parent is invalid")
    for node in nodes.values():
        visited = set()
        current = node
        while current.parent_id != -1:
            if current.bone_id in visited:
                raise ValueError("SMD node parent graph has a cycle")
            visited.add(current.bone_id)
            current = nodes[current.parent_id]
    frame_ids = tuple(item[0] for item in frames)
    if frame_ids != tuple(sorted(frame_ids)):
        raise ValueError("SMD skeleton frames are not canonical")
    canonical_frames = []
    for frame_id, transforms in frames:
        if set(transforms) != node_ids:
            raise ValueError("SMD skeleton frame does not cover every node")
        canonical_frames.append((frame_id, tuple(transforms[key] for key in sorted(transforms))))
    return tuple(nodes[key] for key in sorted(nodes)), tuple(canonical_frames)


def build_smd_skeleton_contract(
    path: Path, root: Path, proof: SourceFileProof,
    cancel_event: threading.Event | None,
) -> SmdSkeletonContract:
    raw = _read_bound(path, root, proof, cancel_event, expected_kind="visual-source")
    nodes, frames = _parse_nodes_and_frames(raw)
    bind_frames = [transforms for frame, transforms in frames if frame == 0]
    if len(bind_frames) != 1:
        raise ValueError("visual SMD has no unique bind frame at time 0")
    unsigned = {
        "schema": 1, "algorithm": "smd-nodes-bind-v1",
        "source_size": proof.size, "source_sha256": proof.sha256,
        "nodes": [_node_payload(item) for item in nodes],
        "bind": [_transform_payload(item) for item in bind_frames[0]],
    }
    return SmdSkeletonContract(
        1, "smd-nodes-bind-v1", proof.size, proof.sha256,
        nodes, bind_frames[0], _seal(unsigned),
    )


def build_smd_pose_contract(
    skeleton: SmdSkeletonContract, *, animation_pair: SmdAnimationPairInput | None,
    cancel_event: threading.Event | None,
) -> SmdPoseContract:
    if not isinstance(skeleton, SmdSkeletonContract):
        raise TypeError("pose contract requires an SMD skeleton contract")
    pose_keys = ("bind",)
    original_hash = candidate_hash = None
    frame_ids: tuple[int, ...] = ()
    representative = None
    if animation_pair is not None:
        if not isinstance(animation_pair, SmdAnimationPairInput):
            raise TypeError("animation pair input is invalid")
        original = _read_bound(
            animation_pair.original_path, animation_pair.original_root,
            animation_pair.original_proof, cancel_event, expected_kind="animation-source",
        )
        candidate = _read_bound(
            animation_pair.candidate_path, animation_pair.candidate_root,
            animation_pair.candidate_proof, cancel_event, expected_kind="animation-source",
        )
        original_nodes, original_frames = _parse_nodes_and_frames(original)
        candidate_nodes, candidate_frames = _parse_nodes_and_frames(candidate)
        original_frame_ids = tuple(item[0] for item in original_frames)
        candidate_frame_ids = tuple(item[0] for item in candidate_frames)
        if (
            original_nodes != skeleton.nodes or candidate_nodes != skeleton.nodes
            or original_frame_ids != candidate_frame_ids
            or animation_pair.original_proof.sha256 != animation_pair.candidate_proof.sha256
        ):
            raise ValueError("animation pair nodes, frames, or bytes differ")
        positives = tuple(frame for frame in original_frame_ids if frame > 0)
        if not positives:
            raise ValueError("animation pair has no positive representative frame")
        pose_keys = ("bind", "animation")
        original_hash = animation_pair.original_proof.sha256
        candidate_hash = animation_pair.candidate_proof.sha256
        frame_ids = original_frame_ids
        representative = max(positives)
    unsigned = {
        "schema": 1, "algorithm": "smd-bind-animation-pair-v1",
        "skeleton_contract_sha256": skeleton.skeleton_contract_sha256,
        "pose_keys": list(pose_keys),
        "animation_original_sha256": original_hash,
        "animation_candidate_sha256": candidate_hash,
        "animation_frames": list(frame_ids),
        "representative_frame": representative,
    }
    return SmdPoseContract(
        1, "smd-bind-animation-pair-v1", skeleton.skeleton_contract_sha256,
        pose_keys, original_hash, candidate_hash, frame_ids, representative, _seal(unsigned),
    )


def build_smd_equivalence_contract(
    source_identity: str, proof: SourceFileProof,
    skeleton: SmdSkeletonContract, pose: SmdPoseContract,
) -> SmdEquivalenceContract:
    identity = require_canonical_relative(source_identity, "SMD equivalence source")
    if (
        not isinstance(proof, SourceFileProof) or proof.kind != "visual-source"
        or not isinstance(skeleton, SmdSkeletonContract)
        or not isinstance(pose, SmdPoseContract)
        or (proof.size, proof.sha256) != (skeleton.source_size, skeleton.source_sha256)
        or pose.skeleton_contract_sha256 != skeleton.skeleton_contract_sha256
    ):
        raise ValueError("SMD equivalence inputs differ")
    unsigned = {
        "schema": 1, "algorithm": "adaptive-source-state-equivalence-v1",
        "source_identity": identity, "source_size": proof.size,
        "source_sha256": proof.sha256,
        "skeleton_contract_sha256": skeleton.skeleton_contract_sha256,
        "pose_contract_sha256": pose.pose_contract_sha256,
    }
    return SmdEquivalenceContract(
        1, "adaptive-source-state-equivalence-v1", identity,
        proof.size, proof.sha256, skeleton.skeleton_contract_sha256,
        pose.pose_contract_sha256, _seal(unsigned),
    )
