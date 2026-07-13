from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .reporting import canonical_json
from .smd_contract import (
    direct_smd_material_counts,
    match_direct_output_triangle_ordinals,
    parse_smd_triangles,
    prefilter_direct_degenerate_smd,
)


_HASH = re.compile(r"[0-9a-f]{64}")
_MAX_COMPONENTS = 256


@dataclass(frozen=True)
class SourceComponentProof:
    component_key: str
    triangle_ordinals: tuple[int, ...]

    def __post_init__(self) -> None:
        ordinals = tuple(self.triangle_ordinals)
        if (
            re.fullmatch(r"component-[0-9]{3}", self.component_key or "") is None
            or not ordinals
            or any(type(value) is not int or value < 0 for value in ordinals)
            or ordinals != tuple(sorted(ordinals))
            or len(set(ordinals)) != len(ordinals)
        ):
            raise ValueError("source component proof is invalid")
        object.__setattr__(self, "triangle_ordinals", ordinals)


def _manifest_unsigned(value: "SourceComponentManifest") -> dict[str, object]:
    return {
        "schema": value.schema,
        "algorithm": value.algorithm,
        "source_sha256": value.source_sha256,
        "prefilter_evidence_sha256": value.prefilter_evidence_sha256,
        "filtered_source_sha256": value.filtered_source_sha256,
        "triangle_count": value.triangle_count,
        "components": [{
            "component_key": item.component_key,
            "triangle_ordinals": list(item.triangle_ordinals),
        } for item in value.components],
    }


def source_component_manifest_payload(value: "SourceComponentManifest") -> dict[str, object]:
    if not isinstance(value, SourceComponentManifest):
        raise TypeError("source component manifest is invalid")
    return {**_manifest_unsigned(value), "component_manifest_sha256": value.component_manifest_sha256}


def source_component_manifest_from_payload(value: object) -> "SourceComponentManifest":
    fields = {
        "schema", "algorithm", "source_sha256", "prefilter_evidence_sha256",
        "filtered_source_sha256", "triangle_count", "components",
        "component_manifest_sha256",
    }
    if type(value) is not dict or set(value) != fields or type(value["components"]) is not list:
        raise ValueError("source component manifest payload fields are invalid")
    components = []
    for raw in value["components"]:
        if (
            type(raw) is not dict
            or set(raw) != {"component_key", "triangle_ordinals"}
            or type(raw["triangle_ordinals"]) is not list
        ):
            raise ValueError("source component manifest component fields are invalid")
        components.append(SourceComponentProof(
            raw["component_key"], tuple(raw["triangle_ordinals"]),
        ))
    copied = dict(value)
    copied["components"] = tuple(components)
    return SourceComponentManifest(**copied)


@dataclass(frozen=True)
class SourceComponentManifest:
    schema: int
    algorithm: str
    source_sha256: str
    prefilter_evidence_sha256: str
    filtered_source_sha256: str
    triangle_count: int
    components: tuple[SourceComponentProof, ...]
    component_manifest_sha256: str

    def __post_init__(self) -> None:
        components = tuple(self.components)
        ordinals = tuple(value for item in components for value in item.triangle_ordinals)
        if (
            self.schema != 1
            or self.algorithm != "post-prefilter-exact-position-components-v1"
            or any(_HASH.fullmatch(value or "") is None for value in (
                self.source_sha256, self.prefilter_evidence_sha256,
                self.filtered_source_sha256, self.component_manifest_sha256,
            ))
            or type(self.triangle_count) is not int
            or self.triangle_count < 1
            or not components
            or len(components) > _MAX_COMPONENTS
            or any(not isinstance(item, SourceComponentProof) for item in components)
            or tuple(item.component_key for item in components) != tuple(
                f"component-{index:03d}" for index in range(len(components))
            )
            or tuple(sorted(ordinals)) != tuple(range(self.triangle_count))
        ):
            message = "source component manifest partition is invalid"
            if len(components) > _MAX_COMPONENTS:
                message = "source component manifest component bound exceeded"
            raise ValueError(message)
        if hashlib.sha256(canonical_json(_manifest_unsigned(self)).encode()).hexdigest() != self.component_manifest_sha256:
            raise ValueError("source component manifest seal is invalid")
        object.__setattr__(self, "components", components)


@dataclass(frozen=True)
class SourceComponentTriangleTransfer:
    candidate_triangle_ordinal: int
    source_triangle_ordinal: int
    component_key: str

    def __post_init__(self) -> None:
        if (
            type(self.candidate_triangle_ordinal) is not int
            or self.candidate_triangle_ordinal < 0
            or type(self.source_triangle_ordinal) is not int
            or self.source_triangle_ordinal < 0
            or re.fullmatch(r"component-[0-9]{3}", self.component_key or "") is None
        ):
            raise ValueError("source component triangle transfer is invalid")


def _transfer_unsigned(value: "SourceComponentTransferProof") -> dict[str, object]:
    return {
        "schema": value.schema,
        "algorithm": value.algorithm,
        "component_manifest_sha256": value.component_manifest_sha256,
        "candidate_sha256": value.candidate_sha256,
        "triangle_count": value.triangle_count,
        "triangles": [{
            "candidate_triangle_ordinal": item.candidate_triangle_ordinal,
            "source_triangle_ordinal": item.source_triangle_ordinal,
            "component_key": item.component_key,
        } for item in value.triangles],
    }


def source_component_transfer_payload(value: "SourceComponentTransferProof") -> dict[str, object]:
    if not isinstance(value, SourceComponentTransferProof):
        raise TypeError("source component transfer is invalid")
    return {**_transfer_unsigned(value), "transfer_sha256": value.transfer_sha256}


def source_component_transfer_from_payload(value: object) -> "SourceComponentTransferProof":
    fields = {
        "schema", "algorithm", "component_manifest_sha256", "candidate_sha256",
        "triangle_count", "triangles", "transfer_sha256",
    }
    if type(value) is not dict or set(value) != fields or type(value["triangles"]) is not list:
        raise ValueError("source component transfer payload fields are invalid")
    triangles = []
    triangle_fields = {
        "candidate_triangle_ordinal", "source_triangle_ordinal", "component_key",
    }
    for raw in value["triangles"]:
        if type(raw) is not dict or set(raw) != triangle_fields:
            raise ValueError("source component transfer triangle fields are invalid")
        triangles.append(SourceComponentTriangleTransfer(**raw))
    copied = dict(value)
    copied["triangles"] = tuple(triangles)
    return SourceComponentTransferProof(**copied)


@dataclass(frozen=True)
class SourceComponentTransferProof:
    schema: int
    algorithm: str
    component_manifest_sha256: str
    candidate_sha256: str
    triangle_count: int
    triangles: tuple[SourceComponentTriangleTransfer, ...]
    transfer_sha256: str

    def __post_init__(self) -> None:
        triangles = tuple(self.triangles)
        if (
            self.schema != 1
            or self.algorithm != "exact-cyclic-source-component-transfer-v1"
            or any(_HASH.fullmatch(value or "") is None for value in (
                self.component_manifest_sha256, self.candidate_sha256,
                self.transfer_sha256,
            ))
            or type(self.triangle_count) is not int
            or self.triangle_count < 1
            or not triangles
            or any(not isinstance(item, SourceComponentTriangleTransfer) for item in triangles)
            or tuple(item.candidate_triangle_ordinal for item in triangles)
            != tuple(range(self.triangle_count))
            or len({item.source_triangle_ordinal for item in triangles}) != len(triangles)
        ):
            raise ValueError("source component transfer is invalid")
        if hashlib.sha256(canonical_json(_transfer_unsigned(self)).encode()).hexdigest() != self.transfer_sha256:
            raise ValueError("source component transfer seal is invalid")
        object.__setattr__(self, "triangles", triangles)


def validate_source_component_transfer_against_manifest(
    transfer: SourceComponentTransferProof, manifest: SourceComponentManifest,
) -> None:
    if not isinstance(transfer, SourceComponentTransferProof) or not isinstance(
        manifest, SourceComponentManifest
    ):
        raise TypeError("source component transfer/manifest is invalid")
    by_ordinal = {
        ordinal: component.component_key
        for component in manifest.components
        for ordinal in component.triangle_ordinals
    }
    if (
        transfer.component_manifest_sha256 != manifest.component_manifest_sha256
        or any(
            item.source_triangle_ordinal not in by_ordinal
            or by_ordinal[item.source_triangle_ordinal] != item.component_key
            for item in transfer.triangles
        )
    ):
        raise ValueError("source component transfer differs from manifest")


def build_source_component_manifest(smd_bytes: bytes) -> SourceComponentManifest:
    if type(smd_bytes) is not bytes:
        raise TypeError("source component input must be bytes")
    source_hash = hashlib.sha256(smd_bytes).hexdigest()
    text = smd_bytes.decode("utf-8", errors="strict")
    prefilter = prefilter_direct_degenerate_smd(text)
    filtered = prefilter.filtered_text
    direct_smd_material_counts(filtered)
    parsed = parse_smd_triangles(filtered)
    positions = [frozenset(corner.position for corner in triangle.corners) for triangle in parsed.triangles]
    parents = list(range(len(positions)))

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parents[max(left, right)] = min(left, right)

    owners: dict[tuple[float, float, float], int] = {}
    for ordinal, values in enumerate(positions):
        for position in values:
            if position in owners:
                union(ordinal, owners[position])
            else:
                owners[position] = ordinal
    groups: dict[int, list[int]] = {}
    for ordinal in range(len(positions)):
        groups.setdefault(find(ordinal), []).append(ordinal)
    ordered = sorted(groups.values(), key=lambda values: values[0])
    if len(ordered) > _MAX_COMPONENTS:
        raise ValueError("source component manifest component bound exceeded")
    components = tuple(
        SourceComponentProof(f"component-{index:03d}", tuple(values))
        for index, values in enumerate(ordered)
    )
    values = {
        "schema": 1,
        "algorithm": "post-prefilter-exact-position-components-v1",
        "source_sha256": source_hash,
        "prefilter_evidence_sha256": str(prefilter.evidence["evidence_sha256"]),
        "filtered_source_sha256": hashlib.sha256(filtered.encode("utf-8")).hexdigest(),
        "triangle_count": len(parsed.triangles),
        "components": components,
    }
    unsigned = {
        **{key: value for key, value in values.items() if key != "components"},
        "components": [{
            "component_key": item.component_key,
            "triangle_ordinals": list(item.triangle_ordinals),
        } for item in components],
    }
    return SourceComponentManifest(
        **values,
        component_manifest_sha256=hashlib.sha256(canonical_json(unsigned).encode()).hexdigest(),
    )


def build_source_component_transfer(
    manifest: SourceComponentManifest, source_smd_bytes: bytes,
    candidate_smd_bytes: bytes,
) -> SourceComponentTransferProof:
    if build_source_component_manifest(source_smd_bytes) != manifest:
        raise ValueError("source component manifest differs from current source bytes")
    candidate_text = candidate_smd_bytes.decode("utf-8", errors="strict")
    prefilter = prefilter_direct_degenerate_smd(source_smd_bytes.decode("utf-8", errors="strict"))
    source_ordinals = match_direct_output_triangle_ordinals(prefilter.filtered_text, candidate_text)
    by_ordinal = {
        ordinal: component.component_key
        for component in manifest.components
        for ordinal in component.triangle_ordinals
    }
    triangles = tuple(
        SourceComponentTriangleTransfer(index, source, by_ordinal[source])
        for index, source in enumerate(source_ordinals)
    )
    values = {
        "schema": 1,
        "algorithm": "exact-cyclic-source-component-transfer-v1",
        "component_manifest_sha256": manifest.component_manifest_sha256,
        "candidate_sha256": hashlib.sha256(candidate_smd_bytes).hexdigest(),
        "triangle_count": len(triangles),
        "triangles": triangles,
    }
    unsigned = {
        **{key: value for key, value in values.items() if key != "triangles"},
        "triangles": [{
            "candidate_triangle_ordinal": item.candidate_triangle_ordinal,
            "source_triangle_ordinal": item.source_triangle_ordinal,
            "component_key": item.component_key,
        } for item in triangles],
    }
    result = SourceComponentTransferProof(
        **values,
        transfer_sha256=hashlib.sha256(canonical_json(unsigned).encode()).hexdigest(),
    )
    validate_source_component_transfer_against_manifest(result, manifest)
    return result


def current_filtered_source_component_bytes(
    manifest: SourceComponentManifest, source_smd_bytes: bytes,
) -> bytes:
    if build_source_component_manifest(source_smd_bytes) != manifest:
        raise ValueError("source component manifest differs from current source bytes")
    return prefilter_direct_degenerate_smd(
        source_smd_bytes.decode("utf-8", errors="strict")
    ).filtered_text.encode("utf-8")


def require_current_source_component_transfer(
    transfer: SourceComponentTransferProof, manifest: SourceComponentManifest,
    source_smd_bytes: bytes, candidate_smd_bytes: bytes,
) -> None:
    validate_source_component_transfer_against_manifest(transfer, manifest)
    try:
        current = build_source_component_transfer(
            manifest, source_smd_bytes, candidate_smd_bytes,
        )
    except (TypeError, ValueError, RuntimeError, UnicodeError) as exc:
        raise ValueError(
            "source component transfer differs from current candidate bytes"
        ) from exc
    if current != transfer:
        raise ValueError("source component transfer differs from current candidate bytes")
