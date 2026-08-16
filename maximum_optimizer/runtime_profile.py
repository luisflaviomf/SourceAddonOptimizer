from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping
from uuid import UUID

from .companion_contract import ValidatedBundle, normalize_virtual_path, read_bundle
from .qc_graph import QcOccurrence
from .qc_runtime import QcRuntimeBinding, QcRuntimeBindings, parse_qc_runtime_bindings


EVIDENCE_IMPORTANCE: Mapping[str, float] = MappingProxyType(
    {
        "registered": 0.10,
        "instantiated": 0.25,
        "visible": 0.50,
        "repeated_visible": 0.75,
        "focused": 1.00,
    }
)


def importance_for_evidence(value: str) -> float:
    try:
        return EVIDENCE_IMPORTANCE[value]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"invalid runtime evidence: {value}") from exc


@dataclass(frozen=True)
class RegionRuntimeImportance:
    model_baseline: float
    observed_state_importance: float
    region_material_importance: float
    runtime_importance: float
    reasons: tuple[str, ...]
    model_path: str | None = None
    matched_materials: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeProfileDiagnostic:
    code: Literal["invalid_runtime_profile", "stale_runtime_profile"]
    message: str


@dataclass(frozen=True)
class RuntimeEvidenceLoadResult:
    evidence: RuntimeEvidenceMap | None
    diagnostic: RuntimeProfileDiagnostic | None


@dataclass(frozen=True)
class RuntimeEvidenceMap:
    bundle_sha256: str
    run_id: UUID
    bindings: QcRuntimeBindings
    model_importance: Mapping[str, float]
    bodygroup_importance: Mapping[tuple[str, str, int], float]
    skin_importance: Mapping[tuple[str, int], float]
    material_importance: Mapping[str, float]

    def for_region(self, occurrence: QcOccurrence, material: str) -> RegionRuntimeImportance:
        binding = self.bindings.by_occurrence.get(occurrence)
        if binding is None or binding.model_path is None:
            return RegionRuntimeImportance(0.0, 0.0, 0.0, 0.0, ())

        model_importance = self.model_importance.get(binding.model_path, 0.0)
        model_baseline = 0.40 * model_importance
        state_importance = 0.0
        material_signal = 0.0
        reasons: list[str] = []
        if model_baseline > 0.0:
            reasons.append(
                f"model {binding.model_path} importance {model_importance:.2f} -> baseline {model_baseline:.2f}"
            )

        if binding.bodygroup is not None:
            name, value = binding.bodygroup
            bodygroup_signal = self.bodygroup_importance.get(
                (binding.model_path, name.casefold(), value),
                0.0,
            )
            if bodygroup_signal > state_importance:
                state_importance = bodygroup_signal
            if bodygroup_signal > 0.0:
                reasons.append(
                    f"bodygroup {name}={value} importance {bodygroup_signal:.2f}"
                )

        material_tokens = [material]
        base_key = _material_token_key(material)
        if binding.skin_rows and binding.skin_rows[0]:
            columns = [
                index
                for index, value in enumerate(binding.skin_rows[0])
                if _material_token_key(value) == base_key
            ]
            if len(columns) == 1:
                column = columns[0]
                for (model_path, skin), skin_signal in sorted(self.skin_importance.items()):
                    if model_path != binding.model_path or skin >= len(binding.skin_rows):
                        continue
                    row = binding.skin_rows[skin]
                    if column >= len(row):
                        continue
                    replacement = row[column]
                    material_tokens.append(replacement)
                    if skin_signal > state_importance:
                        state_importance = skin_signal
                    reasons.append(
                        f"skin {skin} maps {binding.skin_rows[0][column]} -> {replacement} importance {skin_signal:.2f}"
                    )

        candidates: set[str] = set()
        for token in material_tokens:
            candidates.update(_material_candidates(binding, token))
        matched_materials: list[str] = []
        for candidate in sorted(candidates):
            signal = self.material_importance.get(candidate, 0.0)
            if signal <= 0.0:
                continue
            material_signal = max(material_signal, signal)
            matched_materials.append(candidate)
            reasons.append(f"material {candidate} importance {signal:.2f}")

        runtime_importance = max(model_baseline, state_importance, material_signal)
        return RegionRuntimeImportance(
            model_baseline,
            state_importance,
            material_signal,
            runtime_importance,
            tuple(reasons),
            binding.model_path,
            tuple(matched_materials),
        )


def _material_token_key(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/").casefold()
    if normalized.startswith("materials/"):
        normalized = normalized[len("materials/") :]
    if normalized.endswith(".vmt"):
        normalized = normalized[:-4]
    return normalized


def _material_candidates(binding: QcRuntimeBinding, value: str) -> tuple[str, ...]:
    token = _material_token_key(value)
    if not token or token.startswith("../") or "/../" in token:
        return ()
    raw = value.replace("\\", "/").strip("/").casefold()
    values: list[str] = []
    if raw.startswith("materials/"):
        values.append(raw if raw.endswith(".vmt") else raw + ".vmt")
    else:
        for directory in binding.material_directories:
            prefix = directory.as_posix().strip("/").casefold()
            if prefix.startswith("materials/"):
                prefix = prefix[len("materials/") :]
            values.append(f"materials/{prefix}/{token}.vmt")
        values.append(f"materials/{token}.vmt")
    normalized: set[str] = set()
    for candidate in values:
        try:
            path = normalize_virtual_path(candidate)
        except ValueError:
            continue
        if path.endswith(".vmt"):
            normalized.add(path)
    return tuple(sorted(normalized))


def _merge_max(values: dict[object, float], key: object, importance: float) -> None:
    values[key] = max(values.get(key, 0.0), importance)


def _build_runtime_evidence(bundle: ValidatedBundle, qc_root: Path) -> RuntimeEvidenceMap:
    model_importance: dict[str, float] = {}
    bodygroup_importance: dict[tuple[str, str, int], float] = {}
    skin_importance: dict[tuple[str, int], float] = {}
    material_importance: dict[str, float] = {}
    for payload in bundle.payloads:
        for raw_model in payload["models"]:  # type: ignore[index]
            model = raw_model  # validated by companion_contract.read_bundle
            path = normalize_virtual_path(model["path"])
            importance = importance_for_evidence(model["evidence"])
            _merge_max(model_importance, path, importance)
            for skin in model["skins"]:
                _merge_max(skin_importance, (path, skin), importance)
            for bodygroup in model["bodygroups"]:
                _merge_max(
                    bodygroup_importance,
                    (path, bodygroup["name"].casefold(), bodygroup["value"]),
                    importance_for_evidence(bodygroup["evidence"]),
                )
            for submaterial in model["submaterials"]:
                _merge_max(
                    material_importance,
                    normalize_virtual_path(submaterial["material"]),
                    importance_for_evidence(submaterial["evidence"]),
                )
        for raw_material in payload["materials"]:  # type: ignore[index]
            material = raw_material
            _merge_max(
                material_importance,
                normalize_virtual_path(material["path"]),
                importance_for_evidence(material["evidence"]),
            )
    return RuntimeEvidenceMap(
        bundle_sha256=bundle.index_sha256,
        run_id=bundle.run_id,
        bindings=parse_qc_runtime_bindings(qc_root),
        model_importance=MappingProxyType(dict(sorted(model_importance.items()))),
        bodygroup_importance=MappingProxyType(dict(sorted(bodygroup_importance.items()))),
        skin_importance=MappingProxyType(dict(sorted(skin_importance.items()))),
        material_importance=MappingProxyType(dict(sorted(material_importance.items()))),
    )


def load_runtime_evidence(
    profile_root: Path,
    qc_root: Path,
    *,
    now: datetime | None = None,
) -> RuntimeEvidenceLoadResult:
    try:
        bundle = read_bundle(Path(profile_root), expected_schema="sao-runtime-profile-v1")
        clock = now or datetime.now(timezone.utc)
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError("runtime profile clock must be timezone-aware")
        if clock.astimezone(timezone.utc) - bundle.created_utc > timedelta(days=180):
            return RuntimeEvidenceLoadResult(
                None,
                RuntimeProfileDiagnostic(
                    "stale_runtime_profile",
                    "runtime profile is stale (>180 days)",
                ),
            )
        evidence = _build_runtime_evidence(bundle, Path(qc_root))
    except (OSError, UnicodeError, ValueError) as exc:
        return RuntimeEvidenceLoadResult(
            None,
            RuntimeProfileDiagnostic("invalid_runtime_profile", str(exc)),
        )
    return RuntimeEvidenceLoadResult(evidence, None)
