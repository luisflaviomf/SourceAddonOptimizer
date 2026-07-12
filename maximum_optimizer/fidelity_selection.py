from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
from types import MappingProxyType

from maximum_optimizer.qc_graph import QcGraph
from maximum_optimizer.round_planar_priority import classify_round_component
from maximum_optimizer.smd_contract import parse_smd_triangles
from maximum_optimizer.visual_validation import FidelityProfile, load_profile


ROUND_RIGID = "round-rigid-v1"
GENERAL_BODY_DETAIL = "general-body-detail-v1"
LEGACY_GLOBAL = "legacy-global-v1"
TYPED_SELECTOR = "audited-original-round-family-v1"


@dataclass(frozen=True)
class SourceFidelityAudit:
    source: str
    eligible: bool
    reason: str
    axis: int | None


@dataclass(frozen=True)
class FamilyFidelitySelection:
    profile_class: str
    reason: str
    sources: tuple[SourceFidelityAudit, ...]


@dataclass(frozen=True)
class FidelityProfileSet:
    mode: str
    version: str
    corpus_hash: str
    profiles: Mapping[str, FidelityProfile]

    def __post_init__(self) -> None:
        if self.mode not in {LEGACY_GLOBAL, TYPED_SELECTOR}:
            raise ValueError("invalid fidelity profile set mode")
        if type(self.version) is not str or not self.version:
            raise ValueError("fidelity profile set version is required")
        if type(self.corpus_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", self.corpus_hash) is None:
            raise ValueError("fidelity profile set corpus hash is invalid")
        copied = dict(self.profiles)
        if set(copied) != {GENERAL_BODY_DETAIL, ROUND_RIGID} or any(
            not isinstance(value, FidelityProfile) for value in copied.values()
        ):
            raise ValueError("fidelity profile set classes are invalid")
        object.__setattr__(self, "profiles", MappingProxyType(copied))

    def profile_for(self, profile_class: str) -> FidelityProfile:
        if profile_class not in self.profiles:
            raise ValueError(f"unknown fidelity profile class: {profile_class}")
        return self.profiles[profile_class]


def load_fidelity_profile_set(path: Path) -> FidelityProfileSet:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid fidelity profile set: {path}") from exc
    if type(payload) is not dict:
        raise ValueError("fidelity profile set must be an object")
    if payload.get("schema") == 1:
        profile = load_profile(path)
        return FidelityProfileSet(
            LEGACY_GLOBAL,
            profile.version,
            profile.corpus_hash,
            {GENERAL_BODY_DETAIL: profile, ROUND_RIGID: profile},
        )
    expected = {"schema", "version", "calibrated", "corpus_hash", "selector", "profiles"}
    if set(payload) != expected or payload.get("schema") != 2:
        raise ValueError("typed fidelity profile fields are invalid")
    if payload.get("calibrated") is not True:
        raise ValueError("typed fidelity profile must be calibrated")
    version = payload.get("version")
    corpus_hash = payload.get("corpus_hash")
    if type(version) is not str or not version.strip():
        raise ValueError("typed fidelity profile version is required")
    if type(corpus_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", corpus_hash) is None:
        raise ValueError("typed fidelity profile corpus hash is invalid")
    if payload.get("selector") != TYPED_SELECTOR:
        raise ValueError("typed fidelity selector is invalid")
    children = payload.get("profiles")
    if type(children) is not dict or set(children) != {GENERAL_BODY_DETAIL, ROUND_RIGID}:
        raise ValueError("typed fidelity profile classes are invalid")
    profiles = {}
    for profile_class in (GENERAL_BODY_DETAIL, ROUND_RIGID):
        child = children[profile_class]
        if type(child) is not dict or set(child) != {"limits"}:
            raise ValueError(f"typed fidelity profile {profile_class} fields are invalid")
        profiles[profile_class] = FidelityProfile(
            schema=1,
            version=f"{version}:{profile_class}",
            calibrated=True,
            corpus_hash=corpus_hash,
            limits=child["limits"],
        )
    return FidelityProfileSet(TYPED_SELECTOR, version, corpus_hash, profiles)


def _corner_influences(tokens: tuple[str, ...]) -> tuple[tuple[int, float], ...]:
    try:
        parent = int(tokens[0])
    except (IndexError, ValueError) as exc:
        raise ValueError("invalid parent bone") from exc
    if parent < 0:
        raise ValueError("invalid parent bone")
    if len(tokens) == 9:
        return ((parent, 1.0),)
    try:
        count = int(tokens[9])
    except (IndexError, ValueError) as exc:
        raise ValueError("invalid influence count") from exc
    if count < 0 or len(tokens) != 10 + count * 2:
        raise ValueError("invalid influence payload")
    if count == 0:
        return ((parent, 1.0),)
    result = []
    total = 0.0
    for index in range(count):
        try:
            bone = int(tokens[10 + index * 2])
            weight = float(tokens[11 + index * 2])
        except (IndexError, ValueError) as exc:
            raise ValueError("invalid influence payload") from exc
        if bone < 0 or not math.isfinite(weight) or weight < 0:
            raise ValueError("non-finite influence weight")
        result.append((bone, weight))
        total += weight
    if total > 1.0 + 1e-6:
        raise ValueError("invalid influence total")
    if total < 1.0:
        result.append((parent, 1.0 - total))
    return tuple(result)


def _round_input(path: Path):
    parsed = parse_smd_triangles(path.read_text(encoding="utf-8-sig"))
    positions = []
    influences = []
    triangles = []
    for triangle in parsed.triangles:
        indices = []
        for corner in triangle.corners:
            indices.append(len(positions))
            positions.append(corner.position)
            influences.append(_corner_influences(corner.tokens))
        triangles.append(tuple(indices))
    if not positions or not triangles:
        raise ValueError("SMD has no round-classification geometry")
    return tuple(positions), tuple(triangles), tuple(influences)


def classify_original_family(graph: QcGraph) -> FamilyFidelitySelection:
    if not isinstance(graph, QcGraph):
        raise TypeError("graph must be a QcGraph")
    root = graph.family_root.resolve(strict=True)
    unique: dict[str, Path] = {}
    for reference in graph.references:
        if reference.role != "visual":
            continue
        source = Path(os.path.abspath(os.fspath(reference.source_path)))
        unique.setdefault(os.path.normcase(os.fspath(source)), source)
    if not unique:
        return FamilyFidelitySelection(GENERAL_BODY_DETAIL, "no-visual-sources", ())

    identified = []
    for source in unique.values():
        try:
            relative = source.relative_to(root).as_posix()
        except ValueError:
            relative = source.name
        identified.append((relative, source))

    audits = []
    for relative, source in sorted(
        identified, key=lambda item: (item[0].casefold(), os.path.normcase(os.fspath(item[1])))
    ):
        try:
            resolved = source.resolve(strict=True)
        except OSError:
            audit = SourceFidelityAudit(relative, False, "missing-source", None)
        else:
            try:
                resolved.relative_to(root)
            except ValueError:
                audit = SourceFidelityAudit(relative, False, "source-outside-family", None)
            else:
                if resolved.suffix.casefold() != ".smd":
                    audit = SourceFidelityAudit(
                        relative, False,
                        f"unsupported-source-format:{resolved.suffix.casefold()}", None
                    )
                else:
                    try:
                        decision = classify_round_component(*_round_input(resolved))
                    except (OSError, UnicodeError, ValueError) as exc:
                        audit = SourceFidelityAudit(
                            relative, False, f"invalid-smd:{type(exc).__name__}", None
                        )
                    else:
                        audit = SourceFidelityAudit(
                            relative, decision.eligible, decision.reason, decision.axis
                        )
        audits.append(audit)

    rows = tuple(audits)
    failure = next((item for item in rows if not item.eligible), None)
    if failure is not None:
        return FamilyFidelitySelection(
            GENERAL_BODY_DETAIL, f"{failure.source}:{failure.reason}", rows
        )
    return FamilyFidelitySelection(ROUND_RIGID, "all-visual-sources-round-rigid", rows)
