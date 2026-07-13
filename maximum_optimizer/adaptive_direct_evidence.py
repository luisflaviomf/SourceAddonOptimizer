from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Literal

from .composite import (
    AdaptiveDirectSourceUnionRecord,
    adaptive_direct_source_union_record_from_payload,
    adaptive_direct_source_union_record_payload,
)
from .domain import (
    ChangedSourceProof,
    CompileFileProof,
    CompositeRecipe,
    CompositionProof,
    FocusTarget,
    GateFailure,
    StructuralAuthorizationEvidence,
    ValidationResult,
    changed_source_proof_payload,
    compile_file_proof_payload,
    composite_recipe_from_payload,
    composite_recipe_payload,
    composition_proof_from_payload,
    composition_proof_payload,
    structural_authorization_evidence_payload,
    validate_compile_file_proofs,
    validation_result_payload,
)
from .focused_cache import (
    FinalWholeAuthorizationEvidence,
    FocusedRenderEvidence,
    RenderFileProof,
    _final_whole_payload,
    _record_payload,
    _validate_focused_render_record,
    compile_manifest_sha256,
)
from .reporting import canonical_json


_HASH = re.compile(r"[0-9a-f]{64}")
_STATUSES = {
    "composition_failed", "compile_failed", "structural_failed",
    "focused_failed", "final_whole_failed", "authorized",
}


def _digest(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _exact(value: object, fields: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} fields are invalid")
    return value


def _validation_from_payload(value: object) -> ValidationResult:
    raw = _exact(value, {"passed", "failures", "metrics", "worst_scope"}, "validation")
    if type(raw["failures"]) is not list or type(raw["metrics"]) is not dict:
        raise ValueError("validation nested fields are invalid")
    failures = tuple(GateFailure(**_exact(item, {
        "gate", "scope", "measured", "limit", "message",
    }, "validation failure")) for item in raw["failures"])
    result = ValidationResult(raw["passed"], failures, raw["metrics"], raw["worst_scope"])
    validation_result_payload(result)
    return result


def _target_from_payload(value: object) -> FocusTarget:
    raw = _exact(value, {
        "rank", "region_key", "source_identity", "state_index", "state_name",
        "bodygroups", "lod_index", "anchor_pose", "surface_bidirectional_p95",
        "surface_max", "normalized_p95", "normalized_max", "selector_input_sha256",
    }, "base focus target")
    if type(raw["bodygroups"]) is not list:
        raise ValueError("base focus bodygroups are invalid")
    return FocusTarget(
        raw["rank"], raw["region_key"], raw["source_identity"], raw["state_index"],
        raw["state_name"], tuple(tuple(item) for item in raw["bodygroups"]),
        raw["lod_index"], raw["anchor_pose"], raw["surface_bidirectional_p95"],
        raw["surface_max"], raw["normalized_p95"], raw["normalized_max"],
        raw["selector_input_sha256"],
    )


def _render_file_from_payload(value: object) -> RenderFileProof:
    raw = _exact(value, {
        "side", "kind", "path", "size", "sha256", "width", "height",
    }, "base focus render file")
    return RenderFileProof(**raw)


def _focused_record_from_payload(value: object) -> FocusedRenderEvidence:
    raw = _exact(value, {
        "target", "terminal_status", "expected", "reference_manifest",
        "reference_manifest_sha256", "candidate_manifest",
        "candidate_manifest_sha256", "files", "material_proof_sha256",
        "validation", "cache_hit", "evidence_sha256",
    }, "base focus record")
    if type(raw["files"]) is not list or type(raw["expected"]) is not dict:
        raise ValueError("base focus record nested fields are invalid")
    result = FocusedRenderEvidence(
        _target_from_payload(raw["target"]), raw["terminal_status"], raw["expected"],
        raw["reference_manifest"], raw["reference_manifest_sha256"],
        raw["candidate_manifest"], raw["candidate_manifest_sha256"],
        tuple(_render_file_from_payload(item) for item in raw["files"]),
        raw["material_proof_sha256"], _validation_from_payload(raw["validation"]),
        raw["cache_hit"], raw["evidence_sha256"],
    )
    _validate_focused_render_record(result, verify_seal=True)
    return result


def _changed_from_payload(value: object) -> ChangedSourceProof:
    return ChangedSourceProof(**_exact(value, {
        "source_identity", "relative_path", "before_size", "before_sha256",
        "after_size", "after_sha256", "overlay_sha256",
        "replacement_snapshot_sha256",
    }, "adaptive changed source"))


def _compile_from_payload(value: object) -> CompileFileProof:
    return CompileFileProof(**_exact(value, {
        "relative_path", "kind", "size", "sha256",
    }, "adaptive compile file"))


def _structural_from_payload(value: object) -> StructuralAuthorizationEvidence:
    raw = _exact(value, {
        "candidate_cache_digest", "composition_evidence_sha256",
        "compile_manifest_sha256", "fingerprint_sha256", "validation",
        "evidence_sha256",
    }, "adaptive structural evidence")
    return StructuralAuthorizationEvidence(
        raw["candidate_cache_digest"], raw["composition_evidence_sha256"],
        raw["compile_manifest_sha256"], raw["fingerprint_sha256"],
        _validation_from_payload(raw["validation"]), raw["evidence_sha256"],
    )


def _final_from_payload(value: object) -> FinalWholeAuthorizationEvidence:
    raw = _exact(value, {
        "candidate_id", "candidate_cache_digest", "recipe_sha256",
        "composition_evidence_sha256", "compile_manifest_sha256",
        "whole_index_path", "whole_index_sha256", "whole_render_evidence_sha256",
        "validation", "evidence_sha256",
    }, "adaptive final whole evidence")
    return FinalWholeAuthorizationEvidence(
        raw["candidate_id"], raw["candidate_cache_digest"], raw["recipe_sha256"],
        raw["composition_evidence_sha256"], raw["compile_manifest_sha256"],
        raw["whole_index_path"], raw["whole_index_sha256"],
        raw["whole_render_evidence_sha256"],
        _validation_from_payload(raw["validation"]), raw["evidence_sha256"],
    )


@dataclass(frozen=True)
class AdaptiveDirectEvidence:
    schema: Literal[2]
    kind: Literal["adaptive-direct-fallback-v1"]
    round_index: Literal[0]
    terminal_status: str
    recipe: CompositeRecipe
    coverage_manifest_sha256: str
    composition: CompositionProof | None
    changed_sources: tuple[ChangedSourceProof, ...]
    compile_files: tuple[CompileFileProof, ...]
    structural: StructuralAuthorizationEvidence | None
    base_focus_records: tuple[FocusedRenderEvidence, ...]
    direct_focus_records: tuple[AdaptiveDirectSourceUnionRecord, ...]
    final_whole: FinalWholeAuthorizationEvidence | None
    evidence_sha256: str

    def __post_init__(self) -> None:
        if type(self.schema) is not int or self.schema != 2:
            raise ValueError("adaptive-direct evidence schema must be integer 2")
        if (
            type(self.kind) is not str
            or self.kind != "adaptive-direct-fallback-v1"
            or type(self.round_index) is not int
            or self.round_index != 0
        ):
            raise ValueError("adaptive-direct evidence kind or round is invalid")
        if self.terminal_status not in _STATUSES:
            raise ValueError("adaptive-direct terminal status is invalid")
        if (
            not isinstance(self.recipe, CompositeRecipe)
            or self.recipe.kind != "adaptive-direct-fallback-v1"
            or self.recipe.round_index != 0
        ):
            raise ValueError("adaptive-direct evidence recipe is invalid")
        recipe_coverage = getattr(self.recipe, "coverage_manifest_sha256", None)
        if (
            type(self.coverage_manifest_sha256) is not str
            or _HASH.fullmatch(self.coverage_manifest_sha256) is None
            or self.coverage_manifest_sha256 != recipe_coverage
        ):
            raise ValueError("adaptive-direct evidence coverage binding differs")

        changed = tuple(self.changed_sources)
        compiled = validate_compile_file_proofs(tuple(self.compile_files))
        base = tuple(self.base_focus_records)
        direct = tuple(self.direct_focus_records)
        if any(not isinstance(item, ChangedSourceProof) for item in changed):
            raise ValueError("adaptive-direct changed source is invalid")
        changed_keys = tuple(item.source_identity for item in changed)
        if (
            changed_keys != tuple(sorted(changed_keys, key=lambda item: (item.casefold(), item)))
            or len({item.casefold() for item in changed_keys}) != len(changed_keys)
        ):
            raise ValueError("adaptive-direct changed sources are not canonical")
        if any(not isinstance(item, FocusedRenderEvidence) for item in base):
            raise ValueError("adaptive-direct base focus record is invalid")
        if tuple(item.target.rank for item in base) != tuple(range(len(base))):
            raise ValueError("adaptive-direct base focus records are not canonical")
        if len({item.target.region_key.casefold() for item in base}) != len(base):
            raise ValueError("adaptive-direct base focus records are duplicated")
        for item in base:
            _validate_focused_render_record(item, verify_seal=True)
        if any(not isinstance(item, AdaptiveDirectSourceUnionRecord) for item in direct):
            raise ValueError("adaptive-direct source-union record is invalid")
        direct_keys = tuple(item.target.source_identity for item in direct)
        if direct and direct_keys != changed_keys:
            raise ValueError("adaptive-direct source-union records differ from changed sources")
        if any(item.target.coverage_manifest_sha256 != self.coverage_manifest_sha256 for item in direct):
            raise ValueError("adaptive-direct source-union coverage binding differs")

        if self.composition is not None:
            if (
                not isinstance(self.composition, CompositionProof)
                or self.composition.kind != "adaptive-direct-fallback-v1"
                or self.composition.recipe_sha256 != self.recipe.recipe_sha256
                or self.composition.changed_sources != changed
            ):
                raise ValueError("adaptive-direct composition binding differs")
        present = (
            self.composition is not None, bool(changed), bool(compiled),
            self.structural is not None, bool(base), bool(direct),
            self.final_whole is not None,
        )
        expected = {
            "composition_failed": (False, False, False, False, False, False, False),
            "compile_failed": (True, True, False, False, False, False, False),
            "structural_failed": (True, True, True, True, False, False, False),
            "focused_failed": (True, True, True, True, True, True, False),
            "final_whole_failed": (True, True, True, True, True, True, True),
            "authorized": (True, True, True, True, True, True, True),
        }[self.terminal_status]
        if present != expected:
            raise ValueError("adaptive-direct optional-field matrix is invalid")

        if self.structural is not None:
            if (
                not isinstance(self.structural, StructuralAuthorizationEvidence)
                or self.structural.composition_evidence_sha256 != self.composition.evidence_sha256
                or self.structural.compile_manifest_sha256 != compile_manifest_sha256(compiled)
            ):
                raise ValueError("adaptive-direct structural binding differs")
            should_pass = self.terminal_status != "structural_failed"
            if self.structural.validation.passed != should_pass:
                raise ValueError("adaptive-direct structural status differs")
        if base or direct:
            focus_passed = all(item.validation.passed for item in (*base, *direct))
            if focus_passed != (self.terminal_status != "focused_failed"):
                raise ValueError("adaptive-direct focused status differs")
        if self.final_whole is not None:
            if (
                not isinstance(self.final_whole, FinalWholeAuthorizationEvidence)
                or self.final_whole.candidate_id != "recovery-" + self.recipe.recipe_sha256
                or self.final_whole.recipe_sha256 != self.recipe.recipe_sha256
                or self.final_whole.composition_evidence_sha256 != self.composition.evidence_sha256
                or self.final_whole.compile_manifest_sha256 != self.structural.compile_manifest_sha256
                or self.final_whole.candidate_cache_digest != self.structural.candidate_cache_digest
                or self.final_whole.validation.passed != (self.terminal_status == "authorized")
            ):
                raise ValueError("adaptive-direct final-whole binding differs")
        if type(self.evidence_sha256) is not str or _HASH.fullmatch(self.evidence_sha256) is None:
            raise ValueError("adaptive-direct evidence seal is invalid")
        if self.evidence_sha256 != _digest(adaptive_direct_evidence_payload(self, include_seal=False)):
            raise ValueError("adaptive-direct evidence seal mismatch")
        object.__setattr__(self, "changed_sources", changed)
        object.__setattr__(self, "compile_files", compiled)
        object.__setattr__(self, "base_focus_records", base)
        object.__setattr__(self, "direct_focus_records", direct)


def adaptive_direct_evidence_payload(
    value: AdaptiveDirectEvidence, *, include_seal: bool = True,
) -> dict[str, object]:
    if not isinstance(value, AdaptiveDirectEvidence):
        raise TypeError("adaptive-direct evidence is invalid")
    payload = {
        "schema": value.schema,
        "kind": value.kind,
        "round_index": value.round_index,
        "terminal_status": value.terminal_status,
        "recipe": composite_recipe_payload(value.recipe),
        "coverage_manifest_sha256": value.coverage_manifest_sha256,
        "composition": None if value.composition is None else composition_proof_payload(value.composition),
        "changed_sources": [changed_source_proof_payload(item) for item in value.changed_sources],
        "compile_files": [compile_file_proof_payload(item) for item in value.compile_files],
        "structural": None if value.structural is None else structural_authorization_evidence_payload(value.structural),
        "base_focus_records": [
            _record_payload(item, include_cache=True, include_seal=True)
            for item in value.base_focus_records
        ],
        "direct_focus_records": [
            adaptive_direct_source_union_record_payload(item)
            for item in value.direct_focus_records
        ],
        "final_whole": None if value.final_whole is None else _final_whole_payload(value.final_whole, include_seal=True),
    }
    if include_seal:
        payload["evidence_sha256"] = value.evidence_sha256
    return payload


def adaptive_direct_evidence_from_payload(value: object) -> AdaptiveDirectEvidence:
    raw = _exact(value, {
        "schema", "kind", "round_index", "terminal_status", "recipe",
        "coverage_manifest_sha256", "composition", "changed_sources",
        "compile_files", "structural", "base_focus_records",
        "direct_focus_records", "final_whole", "evidence_sha256",
    }, "adaptive-direct evidence")
    for name in ("changed_sources", "compile_files", "base_focus_records", "direct_focus_records"):
        if type(raw[name]) is not list:
            raise ValueError(f"adaptive-direct {name} must be a list")
    return AdaptiveDirectEvidence(
        raw["schema"], raw["kind"], raw["round_index"], raw["terminal_status"],
        composite_recipe_from_payload(raw["recipe"]), raw["coverage_manifest_sha256"],
        None if raw["composition"] is None else composition_proof_from_payload(raw["composition"]),
        tuple(_changed_from_payload(item) for item in raw["changed_sources"]),
        tuple(_compile_from_payload(item) for item in raw["compile_files"]),
        None if raw["structural"] is None else _structural_from_payload(raw["structural"]),
        tuple(_focused_record_from_payload(item) for item in raw["base_focus_records"]),
        tuple(adaptive_direct_source_union_record_from_payload(item) for item in raw["direct_focus_records"]),
        None if raw["final_whole"] is None else _final_from_payload(raw["final_whole"]),
        raw["evidence_sha256"],
    )


def build_adaptive_direct_evidence(
    *, terminal_status: str, recipe: CompositeRecipe,
    composition: CompositionProof | None, changed_sources, compile_files,
    structural: StructuralAuthorizationEvidence | None, base_focus_records,
    direct_focus_records, final_whole: FinalWholeAuthorizationEvidence | None,
) -> AdaptiveDirectEvidence:
    values = {
        "schema": 2,
        "kind": "adaptive-direct-fallback-v1",
        "round_index": 0,
        "terminal_status": terminal_status,
        "recipe": recipe,
        "coverage_manifest_sha256": getattr(recipe, "coverage_manifest_sha256", None),
        "composition": composition,
        "changed_sources": tuple(changed_sources),
        "compile_files": tuple(compile_files),
        "structural": structural,
        "base_focus_records": tuple(base_focus_records),
        "direct_focus_records": tuple(direct_focus_records),
        "final_whole": final_whole,
        "evidence_sha256": "0" * 64,
    }
    provisional = object.__new__(AdaptiveDirectEvidence)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["evidence_sha256"] = _digest(
        adaptive_direct_evidence_payload(provisional, include_seal=False)
    )
    return AdaptiveDirectEvidence(**values)
