from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from maximum_optimizer.domain import FocusedRegionPolicy, FocusTarget, WholeStateEvidence
from maximum_optimizer.regions import (
    RegionManifest,
    load_region_manifest_payload,
    normalized_source_identity,
)
from maximum_optimizer.visual_validation import FidelityProfile


_HASH = re.compile(r"^[0-9a-f]{64}$")
_POSE = re.compile(r"^[A-Za-z0-9_.-]+$")
_GEOMETRY_FIELDS = {
    "scope", "pose", "surface_bidirectional_p95", "surface_max",
}


@dataclass(frozen=True)
class FocusSelection:
    selector_input_sha256: str
    eligible_ranking: tuple[FocusTarget, ...]
    selected: tuple[FocusTarget, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "eligible_ranking", tuple(self.eligible_ranking))
        object.__setattr__(self, "selected", tuple(self.selected))


def _relative_path(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} is invalid")
    normalized = value.strip().replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise ValueError(f"{label} must be a contained relative path")
    result = PurePosixPath(*(part for part in posix.parts if part not in ("", "."))).as_posix()
    if not result or result == ".":
        raise ValueError(f"{label} is invalid")
    return result


def _hash(value: object, label: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _metric(value: object, label: str) -> float:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return number


def _normalized_metric(value: float, limit: float, label: str) -> float:
    if limit == 0.0:
        if value != 0.0:
            raise ValueError(f"{label} is positive under a zero focused limit")
        return 0.0
    return value / limit


def _canonical_manifest(manifest: RegionManifest) -> RegionManifest:
    if not isinstance(manifest, RegionManifest):
        raise TypeError("manifest must be a RegionManifest")
    loaded = load_region_manifest_payload(manifest.to_payload())
    if (
        loaded.to_payload() != manifest.to_payload()
        or tuple(manifest.entries) != tuple(sorted(manifest.entries, key=lambda item: item.descriptor))
    ):
        raise ValueError("region manifest is not canonical")
    return loaded


def _canonical_states(
    states: Sequence[WholeStateEvidence],
    manifest: RegionManifest,
    policy: FocusedRegionPolicy,
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    if isinstance(states, (str, bytes)) or not isinstance(states, Sequence):
        raise TypeError("states must be a sequence")
    if not states or len(states) > policy.max_whole_states:
        raise ValueError("whole-state evidence count is outside the focused bound")
    if any(not isinstance(state, WholeStateEvidence) for state in states):
        raise TypeError("whole-state evidence contains an invalid item")
    ordered = tuple(sorted(states, key=lambda item: item.state_index))
    if tuple(item.state_index for item in ordered) != tuple(range(len(ordered))):
        raise ValueError("whole-state indexes must be unique and contiguous")
    if len({item.state_name for item in ordered}) != len(ordered):
        raise ValueError("whole-state names are duplicated")

    canonical_states: list[dict[str, object]] = []
    ranking_rows: list[dict[str, object]] = []
    seen_rows: set[tuple[int, str, str]] = set()
    for state in ordered:
        if type(state.state_name) is not str or not state.state_name:
            raise ValueError("whole-state name is invalid")
        if type(state.lod_index) is not int or state.lod_index < 0:
            raise ValueError("whole-state LOD index is invalid")
        bodygroups = []
        bodygroup_names: set[str] = set()
        for item in state.bodygroups:
            if (
                type(item) is not tuple or len(item) != 2
                or type(item[0]) is not str or not item[0]
                or type(item[1]) is not int or item[1] < 0
                or item[0] in bodygroup_names
            ):
                raise ValueError("whole-state bodygroups are invalid")
            bodygroup_names.add(item[0])
            bodygroups.append([item[0], item[1]])
        bodygroups.sort(key=lambda item: (item[0].casefold(), item[0]))
        if (
            not state.poses or len(state.poses) > 2
            or state.poses[0] != "bind"
            or state.poses.count("bind") != 1
            or len(set(state.poses)) != len(state.poses)
            or any(type(pose) is not str or _POSE.fullmatch(pose) is None for pose in state.poses)
        ):
            raise ValueError("whole-state poses are invalid")

        pairs = []
        sources: set[str] = set()
        for pair in state.source_pairs:
            if type(pair) is not tuple or len(pair) != 3:
                raise ValueError("whole-state source pair is invalid")
            source = normalized_source_identity(pair[0])
            if source in sources:
                raise ValueError("duplicate source identity after canonical normalization")
            sources.add(source)
            pairs.append([
                source,
                _hash(pair[1], "reference source hash"),
                _hash(pair[2], "candidate source hash"),
            ])
        if not pairs:
            raise ValueError("whole-state source pairs cannot be empty")
        pairs.sort(key=lambda item: item[0])

        rows = []
        state_row_identities: set[tuple[str, str]] = set()
        for raw in state.geometry_rows:
            if not isinstance(raw, Mapping):
                raise ValueError("focused geometry row must be an object")
            row = dict(raw)
            if set(row) != _GEOMETRY_FIELDS:
                raise ValueError("focused geometry row fields are invalid")
            region_key = row["scope"]
            pose = row["pose"]
            if type(region_key) is not str or region_key not in manifest.by_key:
                raise ValueError("focused geometry row references an unknown region")
            if type(pose) is not str or pose not in state.poses:
                raise ValueError("focused geometry row pose is invalid")
            descriptor = manifest.by_key[region_key].descriptor
            if descriptor.source_identity not in sources:
                raise ValueError("focused region source is absent from state source pairs")
            identity = (state.state_index, region_key, pose)
            if identity in seen_rows:
                raise ValueError("duplicate focused geometry row")
            seen_rows.add(identity)
            state_row_identities.add((region_key, pose))
            canonical_row = {
                "scope": region_key,
                "pose": pose,
                "surface_bidirectional_p95": _metric(
                    row["surface_bidirectional_p95"], "surface_bidirectional_p95"
                ),
                "surface_max": _metric(row["surface_max"], "surface_max"),
            }
            rows.append(canonical_row)
            ranking_rows.append({
                **canonical_row,
                "state_index": state.state_index,
                "state_name": state.state_name,
                "bodygroups": tuple((item[0], item[1]) for item in bodygroups),
                "lod_index": state.lod_index,
                "source_identity": descriptor.source_identity,
            })
        eligible_region_keys = {
            entry.key for entry in manifest.entries
            if entry.descriptor.source_identity in sources
        }
        expected_row_identities = {
            (region_key, pose)
            for region_key in eligible_region_keys
            for pose in state.poses
        }
        if state_row_identities != expected_row_identities:
            raise ValueError(
                "focused geometry coverage must contain every eligible region and pose"
            )
        rows.sort(key=lambda item: (item["scope"], item["pose"]))
        canonical_states.append({
            "state_index": state.state_index,
            "state_name": state.state_name,
            "bodygroups": bodygroups,
            "lod_index": state.lod_index,
            "poses": list(state.poses),
            "source_pairs": pairs,
            "reference_manifest": _relative_path(
                state.reference_manifest, "reference manifest path"
            ),
            "reference_manifest_sha256": _hash(
                state.reference_manifest_sha256, "reference manifest hash"
            ),
            "candidate_manifest": _relative_path(
                state.candidate_manifest, "candidate manifest path"
            ),
            "candidate_manifest_sha256": _hash(
                state.candidate_manifest_sha256, "candidate manifest hash"
            ),
            "geometry_rows": rows,
        })
    if not ranking_rows:
        raise ValueError("whole-state evidence has no eligible focused regions")
    return tuple(canonical_states), tuple(ranking_rows)


def select_focus_targets_with_evidence(
    states: Sequence[WholeStateEvidence],
    manifest: RegionManifest,
    profile: FidelityProfile,
    policy: FocusedRegionPolicy,
) -> FocusSelection:
    if not isinstance(policy, FocusedRegionPolicy):
        raise TypeError("policy must be a FocusedRegionPolicy")
    if not isinstance(profile, FidelityProfile):
        raise TypeError("profile must be a FidelityProfile")
    canonical_manifest = _canonical_manifest(manifest)
    canonical_states, rows = _canonical_states(states, canonical_manifest, policy)
    p95_limit = float(profile.limits["surface_bidirectional_p95"])
    max_limit = float(profile.limits["surface_max"])

    ranked_rows = []
    for row in rows:
        p95 = float(row["surface_bidirectional_p95"])
        maximum = float(row["surface_max"])
        normalized_p95 = _normalized_metric(p95, p95_limit, "surface_bidirectional_p95")
        normalized_max = _normalized_metric(maximum, max_limit, "surface_max")
        ranked_rows.append({
            **row,
            "normalized_p95": normalized_p95,
            "normalized_max": normalized_max,
            "risk": max(normalized_p95, normalized_max),
        })

    selector_payload = {
        "schema": 1,
        "policy": {
            "schema": policy.schema,
            "selector": policy.selector,
            "top_k": policy.top_k,
            "max_whole_states": policy.max_whole_states,
            "max_recovery_rounds": policy.max_recovery_rounds,
        },
        "profile": {
            "version": profile.version,
            "corpus_hash": profile.corpus_hash,
            "limits": dict(profile.limits),
        },
        "region_manifest": canonical_manifest.to_payload(),
        "states": canonical_states,
    }
    encoded = json.dumps(
        selector_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    selector_hash = hashlib.sha256(encoded).hexdigest()

    by_region: dict[str, list[dict[str, object]]] = {}
    for row in ranked_rows:
        by_region.setdefault(str(row["scope"]), []).append(row)
    anchors = []
    for region_key, region_rows in by_region.items():
        anchor = min(region_rows, key=lambda item: (
            -float(item["risk"]),
            -float(item["normalized_p95"]),
            -float(item["normalized_max"]),
            int(item["state_index"]),
            str(item["pose"]),
        ))
        anchors.append((region_key, anchor))
    anchors.sort(key=lambda item: (
        -float(item[1]["risk"]),
        -float(item[1]["normalized_p95"]),
        -float(item[1]["normalized_max"]),
        item[0],
    ))

    eligible_ranking = tuple(
        FocusTarget(
            rank,
            region_key,
            str(row["source_identity"]),
            int(row["state_index"]),
            str(row["state_name"]),
            tuple(row["bodygroups"]),
            int(row["lod_index"]),
            str(row["pose"]),
            float(row["surface_bidirectional_p95"]),
            float(row["surface_max"]),
            float(row["normalized_p95"]),
            float(row["normalized_max"]),
            selector_hash,
        )
        for rank, (region_key, row) in enumerate(anchors)
    )
    return FocusSelection(
        selector_hash,
        eligible_ranking,
        eligible_ranking[:policy.top_k],
    )


def select_focus_targets(
    states: Sequence[WholeStateEvidence],
    manifest: RegionManifest,
    profile: FidelityProfile,
    policy: FocusedRegionPolicy,
) -> tuple[FocusTarget, ...]:
    return select_focus_targets_with_evidence(states, manifest, profile, policy).selected
