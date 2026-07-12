from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Sequence
import unicodedata


REGION_MANIFEST_SCHEMA_VERSION = 1
REGION_DESCRIPTOR_SCHEMA = "maximum-region-descriptor-v1"
REGION_HASH_ALGORITHM = "sha256"
_REGION_KEY_RE = re.compile(r"^r-[0-9a-f]{64}$")
_POSE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

RegionObservation = tuple[str, str, tuple[str, ...]]
RegionHasher = Callable[[bytes], str]


def normalized_region_name(name: str) -> str:
    if type(name) is not str:
        raise ValueError("region object name must be a string")
    normalized = unicodedata.normalize("NFC", name).strip()
    normalized = re.sub(r"\.\d{3}$", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"_OPT$", "", normalized, flags=re.IGNORECASE)
    if not normalized:
        raise ValueError("region object name cannot be empty")
    return normalized.casefold()


def normalized_region_material(name: str) -> str:
    if type(name) is not str:
        raise ValueError("region material name must be a string")
    normalized = unicodedata.normalize("NFC", name).strip().replace("\\", "/")
    normalized = re.sub(r"/+", "/", normalized).strip("/")
    return normalized.casefold() or "none"


def normalized_source_identity(value: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError("region source identity must be a non-empty string")
    normalized = unicodedata.normalize("NFC", value).strip().replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise ValueError("region source identity must be a relative POSIX path")
    parts = tuple(part for part in posix.parts if part not in ("", "."))
    if not parts:
        raise ValueError("region source identity must identify a file")
    return PurePosixPath(*(unicodedata.normalize("NFC", part).casefold() for part in parts)).as_posix()


def source_material_slot_identities(
    source_identity: str,
    source_materials: Sequence[str],
    blender_datablock_names: Sequence[str],
) -> tuple[str, ...]:
    normalized_source_identity(source_identity)
    canonical_source = tuple(normalized_region_material(name) for name in source_materials)
    datablock_names = tuple(normalized_region_material(name) for name in blender_datablock_names)
    if not canonical_source:
        if datablock_names:
            raise ValueError("ambiguous material slots without Source material evidence")
        return ()
    if len(canonical_source) == len(datablock_names):
        slot_indices = tuple(range(len(canonical_source)))
    else:
        slot_indices_list: list[int] = []
        used: set[int] = set()
        for datablock_name in datablock_names:
            matches = tuple(
                index for index, source_name in enumerate(canonical_source)
                if source_name == datablock_name and index not in used
            )
            if len(matches) != 1:
                raise ValueError("ambiguous material slot mapping for Source occurrence")
            used.add(matches[0])
            slot_indices_list.append(matches[0])
        slot_indices = tuple(slot_indices_list)
    return tuple(
        f"slot:{index}:{canonical_source[index]}" for index in slot_indices
    )


def blender_suffix_number(name: str) -> int:
    match = re.search(r"\.(\d{3})$", unicodedata.normalize("NFC", str(name)).strip())
    return int(match.group(1)) if match else 0


@dataclass(frozen=True, order=True)
class RegionDescriptor:
    source_identity: str
    object_name: str
    materials: tuple[str, ...]
    local_ordinal: int

    def canonical_payload(self) -> dict[str, object]:
        return {
            "descriptor_schema": REGION_DESCRIPTOR_SCHEMA,
            "local_ordinal": self.local_ordinal,
            "materials": list(self.materials),
            "object_name": self.object_name,
            "source_identity": self.source_identity,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.canonical_payload(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")


@dataclass(frozen=True, order=True)
class RegionOccurrence:
    graph_file: str
    directive: str
    line: int
    logical_path: str

    def to_payload(self) -> dict[str, object]:
        return {
            "directive": self.directive,
            "graph_file": self.graph_file,
            "line": self.line,
            "logical_path": self.logical_path,
        }


@dataclass(frozen=True, order=True)
class RegionEntry:
    key: str
    descriptor: RegionDescriptor
    occurrences: tuple[RegionOccurrence, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "descriptor": self.descriptor.canonical_payload(),
            "key": self.key,
            "occurrences": [occurrence.to_payload() for occurrence in self.occurrences],
        }


@dataclass(frozen=True)
class RegionManifest:
    entries: tuple[RegionEntry, ...]
    schema_version: int = REGION_MANIFEST_SCHEMA_VERSION
    descriptor_schema: str = REGION_DESCRIPTOR_SCHEMA
    hash_algorithm: str = REGION_HASH_ALGORITHM
    by_key: Mapping[str, RegionEntry] = field(init=False, repr=False, compare=False)
    by_descriptor: Mapping[RegionDescriptor, RegionEntry] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_key = {entry.key: entry for entry in self.entries}
        by_descriptor = {entry.descriptor: entry for entry in self.entries}
        if len(by_key) != len(self.entries):
            raise ValueError("duplicate region key in manifest")
        if len(by_descriptor) != len(self.entries):
            raise ValueError("duplicate region descriptor in manifest")
        object.__setattr__(self, "by_key", MappingProxyType(by_key))
        object.__setattr__(self, "by_descriptor", MappingProxyType(by_descriptor))

    def to_payload(self) -> dict[str, object]:
        return {
            "descriptor_schema": self.descriptor_schema,
            "hash_algorithm": self.hash_algorithm,
            "regions": [entry.to_payload() for entry in self.entries],
            "schema_version": self.schema_version,
        }


def _default_hasher(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _region_key(descriptor: RegionDescriptor, hasher: RegionHasher) -> str:
    digest = hasher(descriptor.canonical_bytes())
    if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("region hasher must return a lowercase SHA-256 hex digest")
    return f"r-{digest}"


def _coerce_observations(
    observations: Iterable[tuple[str, str, Sequence[str]]],
) -> tuple[RegionObservation, ...]:
    result: list[RegionObservation] = []
    raw_seen: set[RegionObservation] = set()
    for source, object_name, materials in observations:
        raw = (str(source), str(object_name), tuple(str(material) for material in materials))
        if raw in raw_seen:
            raise ValueError("duplicate region observation")
        raw_seen.add(raw)
        result.append(raw)
    if not result:
        raise ValueError("region observations cannot be empty")
    return tuple(result)


def _descriptors_for_observations(
    observations: Iterable[tuple[str, str, Sequence[str]]],
) -> tuple[tuple[RegionObservation, RegionDescriptor], ...]:
    raw = _coerce_observations(observations)
    grouped: dict[tuple[str, str, tuple[str, ...]], list[RegionObservation]] = {}
    for observation in raw:
        source, object_name, materials = observation
        normalized_materials = tuple(normalized_region_material(value) for value in materials) or ("none",)
        group = (
            normalized_source_identity(source),
            normalized_region_name(object_name),
            normalized_materials,
        )
        grouped.setdefault(group, []).append(observation)

    result: list[tuple[RegionObservation, RegionDescriptor]] = []
    for group, members in sorted(grouped.items()):
        ordered = sorted(
            members,
            key=lambda item: (
                blender_suffix_number(item[1]),
                unicodedata.normalize("NFC", item[1]).casefold(),
                unicodedata.normalize("NFC", item[1]),
                tuple(unicodedata.normalize("NFC", value) for value in item[2]),
            ),
        )
        source_identity, object_name, materials = group
        for ordinal, observation in enumerate(ordered):
            result.append(
                (
                    observation,
                    RegionDescriptor(source_identity, object_name, materials, ordinal),
                )
            )
    return tuple(result)


def _coerce_occurrences(
    source_identity: str, values: Sequence[Mapping[str, object]]
) -> tuple[RegionOccurrence, ...]:
    result: list[RegionOccurrence] = []
    seen: set[RegionOccurrence] = set()
    for payload in values:
        if type(payload) is not dict or set(payload) != {"graph_file", "directive", "line", "logical_path"}:
            raise ValueError("region source occurrence has invalid fields")
        graph_file = normalized_source_identity(payload["graph_file"])  # type: ignore[arg-type]
        directive = payload["directive"]
        line = payload["line"]
        logical_path = normalized_source_identity(payload["logical_path"])  # type: ignore[arg-type]
        if type(directive) is not str or not directive.startswith("$") or type(line) is not int or line < 1:
            raise ValueError("region source occurrence is invalid")
        if logical_path != source_identity:
            raise ValueError("region occurrence logical path does not match descriptor source")
        occurrence = RegionOccurrence(graph_file, directive.casefold(), line, logical_path)
        if occurrence in seen:
            raise ValueError("duplicate region source occurrence")
        seen.add(occurrence)
        result.append(occurrence)
    return tuple(sorted(result))


def build_region_manifest(
    observations: Iterable[tuple[str, str, Sequence[str]]],
    *,
    occurrences: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    hasher: RegionHasher = _default_hasher,
) -> RegionManifest:
    descriptors = _descriptors_for_observations(observations)
    occurrence_map = {
        normalized_source_identity(source): tuple(values)
        for source, values in (occurrences or {}).items()
    }
    described_sources = {descriptor.source_identity for _observation, descriptor in descriptors}
    unknown_occurrences = set(occurrence_map) - described_sources
    if unknown_occurrences:
        raise ValueError(f"unknown region occurrence sources: {sorted(unknown_occurrences)}")
    entries: list[RegionEntry] = []
    keys: dict[str, RegionDescriptor] = {}
    for _observation, descriptor in descriptors:
        key = _region_key(descriptor, hasher)
        existing = keys.get(key)
        if existing is not None and existing != descriptor:
            raise ValueError("region hash collision")
        keys[key] = descriptor
        entries.append(
            RegionEntry(
                key,
                descriptor,
                _coerce_occurrences(descriptor.source_identity, occurrence_map.get(descriptor.source_identity, ())),
            )
        )
    return RegionManifest(tuple(sorted(entries, key=lambda entry: entry.descriptor)))


def load_region_manifest_payload(
    payload: object, *, hasher: RegionHasher = _default_hasher
) -> RegionManifest:
    expected_top = {"schema_version", "descriptor_schema", "hash_algorithm", "regions"}
    if type(payload) is not dict or set(payload) != expected_top:
        raise ValueError("region manifest schema fields are invalid")
    if (
        payload["schema_version"] != REGION_MANIFEST_SCHEMA_VERSION
        or payload["descriptor_schema"] != REGION_DESCRIPTOR_SCHEMA
        or payload["hash_algorithm"] != REGION_HASH_ALGORITHM
        or type(payload["regions"]) is not list
    ):
        raise ValueError("region manifest schema/version is unsupported")
    entries: list[RegionEntry] = []
    for region in payload["regions"]:
        if type(region) is not dict or set(region) != {"key", "descriptor", "occurrences"}:
            raise ValueError("region manifest entry schema is invalid")
        descriptor_payload = region["descriptor"]
        if type(descriptor_payload) is not dict or set(descriptor_payload) != {
            "descriptor_schema", "source_identity", "object_name", "materials", "local_ordinal"
        }:
            raise ValueError("region descriptor schema is invalid")
        if descriptor_payload["descriptor_schema"] != REGION_DESCRIPTOR_SCHEMA:
            raise ValueError("region descriptor schema/version is unsupported")
        materials = descriptor_payload["materials"]
        ordinal = descriptor_payload["local_ordinal"]
        if type(materials) is not list or not materials or type(ordinal) is not int or ordinal < 0:
            raise ValueError("region descriptor values are invalid")
        canonical_source = normalized_source_identity(descriptor_payload["source_identity"])  # type: ignore[arg-type]
        canonical_object = normalized_region_name(descriptor_payload["object_name"])  # type: ignore[arg-type]
        canonical_materials = tuple(normalized_region_material(value) for value in materials)
        if (
            descriptor_payload["source_identity"] != canonical_source
            or descriptor_payload["object_name"] != canonical_object
            or tuple(materials) != canonical_materials
        ):
            raise ValueError("region descriptor is not in canonical normalized form")
        descriptor = RegionDescriptor(
            canonical_source, canonical_object, canonical_materials, ordinal
        )
        key = region["key"]
        if type(key) is not str or not is_region_key(key) or key != _region_key(descriptor, hasher):
            raise ValueError("region manifest hash/key mismatch")
        occurrences_payload = region["occurrences"]
        if type(occurrences_payload) is not list:
            raise ValueError("region occurrence list is invalid")
        occurrences = _coerce_occurrences(descriptor.source_identity, occurrences_payload)
        if [occurrence.to_payload() for occurrence in occurrences] != occurrences_payload:
            raise ValueError("region occurrences are not in canonical normalized order")
        entries.append(RegionEntry(key, descriptor, occurrences))
    if not entries:
        raise ValueError("region manifest cannot be empty")
    return RegionManifest(tuple(entries))


def resolve_region_assignments(
    manifest: RegionManifest,
    observations: Iterable[tuple[str, str, Sequence[str]]],
    *,
    require_complete: bool = True,
) -> Mapping[RegionObservation, str]:
    described = _descriptors_for_observations(observations)
    result: dict[RegionObservation, str] = {}
    matched: set[str] = set()
    for observation, descriptor in described:
        entry = manifest.by_descriptor.get(descriptor)
        if entry is None:
            raise ValueError(f"unknown region descriptor: {descriptor}")
        if entry.key in matched:
            raise ValueError(f"ambiguous duplicate region match: {entry.key}")
        matched.add(entry.key)
        result[observation] = entry.key
    if require_complete:
        missing = sorted(set(manifest.by_key) - matched)
        if missing:
            raise ValueError(f"missing region descriptors: {missing}")
    return MappingProxyType(result)


def manifest_for_source(manifest: RegionManifest, source_identity: str) -> RegionManifest:
    normalized = normalized_source_identity(source_identity)
    entries = tuple(entry for entry in manifest.entries if entry.descriptor.source_identity == normalized)
    if not entries:
        raise ValueError(f"unknown region source identity: {normalized}")
    return RegionManifest(entries)


def parse_region_scope(scope: str) -> tuple[str, str] | None:
    if type(scope) is not str or scope.count("/") != 1:
        return None
    region_key, pose = scope.rsplit("/", 1)
    if not is_region_key(region_key) or not _POSE_RE.fullmatch(pose):
        return None
    return region_key, pose


def is_region_key(value: str) -> bool:
    return type(value) is str and _REGION_KEY_RE.fullmatch(value) is not None
