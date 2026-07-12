from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .benchmarking import canonical_json_bytes, compiled_kind


REQUIRED_KINDS = (".mdl", ".vvd", ".dx80.vtx", ".dx90.vtx", ".phy")


class SmoothingEvidenceError(ValueError):
    pass


def _artifact(raw: object, context: str) -> dict[str, object]:
    expected = {"path", "size_bytes", "sha256"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise SmoothingEvidenceError(f"{context} keys are invalid")
    path = raw["path"]
    if not isinstance(path, str) or not path or "\\" in path:
        raise SmoothingEvidenceError(f"{context} path is invalid")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise SmoothingEvidenceError(f"{context} path is not portable")
    size = raw["size_bytes"]
    digest = raw["sha256"]
    if type(size) is not int or size < 0:
        raise SmoothingEvidenceError(f"{context} size is invalid")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise SmoothingEvidenceError(f"{context} SHA-256 is invalid")
    return {"path": path, "size_bytes": size, "sha256": digest}


def _digest(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def build_artifact_comparison(
    control: Sequence[Mapping[str, object]], candidate: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    control_by_kind = {compiled_kind(str(item.get("path", ""))): _artifact(dict(item), "control artifact") for item in control}
    candidate_by_kind = {compiled_kind(str(item.get("path", ""))): _artifact(dict(item), "candidate artifact") for item in candidate}
    if set(control_by_kind) != set(REQUIRED_KINDS) or set(candidate_by_kind) != set(REQUIRED_KINDS):
        raise SmoothingEvidenceError("artifact comparison requires exact MDL/VVD/DX80/DX90/PHY kinds")
    artifacts = []
    for kind in REQUIRED_KINDS:
        before, after = control_by_kind[kind], candidate_by_kind[kind]
        artifacts.append({
            "kind": kind,
            "control": before,
            "candidate": after,
            "equal": before == after,
        })
    body = {"schema_version": 1, "artifacts": artifacts, "bundle_equal": all(item["equal"] for item in artifacts)}
    payload = {**body, "bundle_sha256": _digest(body)}
    parse_artifact_comparison(payload)
    return payload


def parse_artifact_comparison(raw: object) -> dict[str, object]:
    expected = {"schema_version", "artifacts", "bundle_equal", "bundle_sha256"}
    if not isinstance(raw, dict) or set(raw) != expected or raw["schema_version"] != 1:
        raise SmoothingEvidenceError("artifact comparison keys/schema are invalid")
    if type(raw["bundle_equal"]) is not bool or not isinstance(raw["artifacts"], list):
        raise SmoothingEvidenceError("artifact comparison fields are invalid")
    artifacts = []
    kinds = []
    for item in raw["artifacts"]:
        if not isinstance(item, dict) or set(item) != {"kind", "control", "candidate", "equal"}:
            raise SmoothingEvidenceError("artifact comparison entry keys are invalid")
        kind = item["kind"]
        if kind not in REQUIRED_KINDS or type(item["equal"]) is not bool:
            raise SmoothingEvidenceError("artifact comparison entry kind/equality is invalid")
        before = _artifact(item["control"], f"{kind} control")
        after = _artifact(item["candidate"], f"{kind} candidate")
        if compiled_kind(str(before["path"])) != kind or compiled_kind(str(after["path"])) != kind:
            raise SmoothingEvidenceError("artifact path kind disagrees with entry")
        equal = before == after
        if item["equal"] != equal:
            raise SmoothingEvidenceError("artifact equality claim disagrees with size/hash")
        kinds.append(kind)
        artifacts.append({"kind": kind, "control": before, "candidate": after, "equal": equal})
    if tuple(kinds) != REQUIRED_KINDS:
        raise SmoothingEvidenceError("artifact kinds are missing, duplicated or out of order")
    bundle_equal = all(item["equal"] for item in artifacts)
    if raw["bundle_equal"] != bundle_equal:
        raise SmoothingEvidenceError("bundle equality claim disagrees with artifacts")
    body = {"schema_version": 1, "artifacts": artifacts, "bundle_equal": bundle_equal}
    if raw["bundle_sha256"] != _digest(body):
        raise SmoothingEvidenceError("artifact comparison bundle digest mismatch")
    return {**body, "bundle_sha256": raw["bundle_sha256"]}
