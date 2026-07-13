from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Literal

_HASH = re.compile(r"^[0-9a-f]{64}$")
_REGION = re.compile(r"^r-[0-9a-f]{64}$")
_MAX_COMPILED_FILES = 64
_MAX_COMPILED_BYTES = 2 * 1024**3
MAX_FOCUS_REGIONS = 64
MAX_FOCUS_OCCURRENCES = 256
MAX_REGION_OPTION_PROOFS = MAX_FOCUS_REGIONS * 9 * 2

REGION_LADDER = (
    (0.15, 0.020),
    (0.20, 0.020),
    (0.25, 0.020),
    (0.30, 0.010),
    (0.35, 0.010),
    (0.40, 0.010),
    (0.50, 0.010),
    (0.60, 0.005),
)
REQUIRED_GATES = (
    "source_lineage",
    "structural",
    "compile",
    "whole_visual",
    "focused_all_changed",
    "source_union",
    "final_whole_visual",
)
BASE_REQUIRED_GATES = (
    "source_lineage",
    "structural",
    "compile",
    "whole_visual",
    "focused_all_changed",
    "source_union",
)
INTERMEDIATE_REQUIRED_GATES = (
    "source_lineage",
    "structural",
    "compile",
    "focused_all_changed",
    "source_union",
)


def required_gates_for_stage(stage: str) -> tuple[str, ...]:
    if stage == "base-terminal":
        return BASE_REQUIRED_GATES
    if stage == "region-intermediate":
        return INTERMEDIATE_REQUIRED_GATES
    if stage == "final-retained":
        return REQUIRED_GATES
    raise ValueError("holdout attempt stage is invalid")


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _sha(value: object, label: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _ratio(value: object, label: str) -> float:
    if type(value) is not float or not math.isfinite(value) or not 0.0 < value <= 1.0:
        raise ValueError(f"{label} is invalid")
    return value


def _relative(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} is invalid")
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute() or windows.is_absolute() or windows.drive
        or ".." in posix.parts
    ):
        raise ValueError(f"{label} must be a contained relative path")
    canonical = PurePosixPath(*(part for part in posix.parts if part not in ("", "."))).as_posix()
    if canonical != normalized or canonical in ("", "."):
        raise ValueError(f"{label} is not canonical")
    return canonical


@dataclass(frozen=True, order=True)
class BaseOption:
    strategy: Literal["blender-adaptive-v1"]
    ratio: float
    target_error: float
    transfer: Literal["blender-native-v1"]
    update_vertices: Literal[True]

    def __post_init__(self) -> None:
        if (
            self.strategy != "blender-adaptive-v1"
            or self.ratio not in (0.60, 0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15)
            or type(self.target_error) is not float
            or self.target_error != 0.0
            or self.transfer != "blender-native-v1"
            or self.update_vertices is not True
        ):
            raise ValueError("holdout base option is outside the frozen ladder")


BASE_LADDER = tuple(
    BaseOption("blender-adaptive-v1", ratio, 0.0, "blender-native-v1", True)
    for ratio in (0.60, 0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15)
)


def _base_payload(value: BaseOption) -> dict[str, object]:
    return {
        "strategy": value.strategy,
        "ratio": value.ratio,
        "target_error": value.target_error,
        "transfer": value.transfer,
        "update_vertices": value.update_vertices,
    }


@dataclass(frozen=True)
class HoldoutCalibrationApproval:
    schema: Literal[1]
    approval_id: Literal["visual-remap-calibration-approval-v1"]
    calibration_bundle_sha256: str
    whole_profile_sha256: str
    focused_profile_sha256: str
    profile_approval_sha256: str
    status: Literal["approved"]
    approval_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.schema) is not int or self.schema != 1
            or self.approval_id != "visual-remap-calibration-approval-v1"
            or self.status != "approved"
        ):
            raise ValueError("holdout calibration approval identity is invalid")
        for name in (
            "calibration_bundle_sha256", "whole_profile_sha256",
            "focused_profile_sha256", "profile_approval_sha256",
        ):
            _sha(getattr(self, name), f"holdout calibration approval {name}")
        if _sha(self.approval_sha256, "holdout calibration approval seal") != _canonical_digest(
            holdout_calibration_approval_payload(self, include_seal=False)
        ):
            raise ValueError("holdout calibration approval seal is invalid")


def holdout_calibration_approval_payload(
    value: HoldoutCalibrationApproval, *, include_seal: bool = True,
) -> dict[str, object]:
    if not isinstance(value, HoldoutCalibrationApproval):
        raise TypeError("holdout calibration approval is invalid")
    payload = {
        "schema": value.schema,
        "approval_id": value.approval_id,
        "calibration_bundle_sha256": value.calibration_bundle_sha256,
        "whole_profile_sha256": value.whole_profile_sha256,
        "focused_profile_sha256": value.focused_profile_sha256,
        "profile_approval_sha256": value.profile_approval_sha256,
        "status": value.status,
    }
    if include_seal:
        payload["approval_sha256"] = value.approval_sha256
    return payload


def build_holdout_calibration_approval(
    *,
    calibration_bundle_sha256: str,
    whole_profile_sha256: str,
    focused_profile_sha256: str,
    profile_approval_sha256: str,
) -> HoldoutCalibrationApproval:
    values = dict(
        schema=1,
        approval_id="visual-remap-calibration-approval-v1",
        calibration_bundle_sha256=calibration_bundle_sha256,
        whole_profile_sha256=whole_profile_sha256,
        focused_profile_sha256=focused_profile_sha256,
        profile_approval_sha256=profile_approval_sha256,
        status="approved",
        approval_sha256="0" * 64,
    )
    provisional = HoldoutCalibrationApproval.__new__(HoldoutCalibrationApproval)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["approval_sha256"] = _canonical_digest(
        holdout_calibration_approval_payload(provisional, include_seal=False)
    )
    return HoldoutCalibrationApproval(**values)


def holdout_calibration_approval_from_payload(value: object) -> HoldoutCalibrationApproval:
    fields = set(HoldoutCalibrationApproval.__dataclass_fields__)
    if type(value) is not dict or set(value) != fields:
        raise ValueError("holdout calibration approval fields are invalid")
    return HoldoutCalibrationApproval(**value)


@dataclass(frozen=True)
class HoldoutFreezeBindings:
    calibration_approval: HoldoutCalibrationApproval
    reviewed_calibration_approval_sha256: str
    optimizer_contract_sha256: str
    renderer_sha256: str
    compiler_sha256: str
    toolchain_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.calibration_approval, HoldoutCalibrationApproval):
            raise ValueError("holdout binding calibration approval is invalid")
        for name in self.__dataclass_fields__:
            if name == "calibration_approval":
                continue
            _sha(getattr(self, name), f"holdout binding {name}")
        if self.reviewed_calibration_approval_sha256 != self.calibration_approval.approval_sha256:
            raise ValueError("holdout binding calibration approval was not reviewed exactly")


def _bindings_payload(value: HoldoutFreezeBindings) -> dict[str, object]:
    return {
        "calibration_approval": holdout_calibration_approval_payload(
            value.calibration_approval
        ),
        "reviewed_calibration_approval_sha256": value.reviewed_calibration_approval_sha256,
        "optimizer_contract_sha256": value.optimizer_contract_sha256,
        "renderer_sha256": value.renderer_sha256,
        "compiler_sha256": value.compiler_sha256,
        "toolchain_sha256": value.toolchain_sha256,
    }


@dataclass(frozen=True)
class FrozenHoldoutProtocol:
    schema: Literal[1]
    protocol_id: Literal["lvs-holdout-region-tournament-v1"]
    no_retune_rule: Literal["frozen-before-input-v1"]
    bindings: HoldoutFreezeBindings
    base_ladder: tuple[BaseOption, ...]
    region_ladder: tuple[tuple[float, float], ...]
    focused_selector: Literal["exact-source-union-all-regions-v1"]
    max_focus_regions: Literal[64]
    max_focus_occurrences: Literal[256]
    max_region_option_proofs: Literal[1152]
    focused_evaluation_mode: Literal["isolated-once-per-region-option-v1"]
    focused_prefix_rerender: Literal[False]
    whole_visual_stages: tuple[
        Literal["base-terminal"], Literal["final-retained"]
    ]
    finalist_base_count: Literal[2]
    beam_width: Literal[4]
    camera_count: Literal[8]
    passes: tuple[Literal["textured", "clay"], Literal["textured", "clay"]]
    pose_limit: Literal[2]
    compiled_selector: Literal["actual-studiomdl-compiled-bytes-v1"]
    exact_fallback: Literal["exact-original-family-v1"]
    required_gates: tuple[str, ...]
    protocol_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.schema) is not int or self.schema != 1
            or self.protocol_id != "lvs-holdout-region-tournament-v1"
            or self.no_retune_rule != "frozen-before-input-v1"
            or not isinstance(self.bindings, HoldoutFreezeBindings)
            or tuple(self.base_ladder) != BASE_LADDER
            or tuple(self.region_ladder) != REGION_LADDER
            or self.focused_selector != "exact-source-union-all-regions-v1"
            or type(self.max_focus_regions) is not int
            or self.max_focus_regions != MAX_FOCUS_REGIONS
            or type(self.max_focus_occurrences) is not int
            or self.max_focus_occurrences != MAX_FOCUS_OCCURRENCES
            or type(self.max_region_option_proofs) is not int
            or self.max_region_option_proofs != MAX_REGION_OPTION_PROOFS
            or self.focused_evaluation_mode != "isolated-once-per-region-option-v1"
            or self.focused_prefix_rerender is not False
            or tuple(self.whole_visual_stages)
            != ("base-terminal", "final-retained")
            or type(self.finalist_base_count) is not int or self.finalist_base_count != 2
            or type(self.beam_width) is not int or self.beam_width != 4
            or type(self.camera_count) is not int or self.camera_count != 8
            or tuple(self.passes) != ("textured", "clay")
            or type(self.pose_limit) is not int or self.pose_limit != 2
            or self.compiled_selector != "actual-studiomdl-compiled-bytes-v1"
            or self.exact_fallback != "exact-original-family-v1"
            or tuple(self.required_gates) != REQUIRED_GATES
            or _sha(self.protocol_sha256, "holdout protocol seal")
            != _canonical_digest(holdout_protocol_payload(self, include_seal=False))
        ):
            raise ValueError("holdout protocol differs from the frozen algorithm")
        object.__setattr__(self, "base_ladder", tuple(self.base_ladder))
        object.__setattr__(self, "region_ladder", tuple(tuple(item) for item in self.region_ladder))
        object.__setattr__(self, "whole_visual_stages", tuple(self.whole_visual_stages))
        object.__setattr__(self, "passes", tuple(self.passes))
        object.__setattr__(self, "required_gates", tuple(self.required_gates))


def holdout_protocol_payload(
    value: FrozenHoldoutProtocol, *, include_seal: bool = True,
) -> dict[str, object]:
    if not isinstance(value, FrozenHoldoutProtocol):
        raise TypeError("holdout protocol is invalid")
    payload = {
        "schema": value.schema,
        "protocol_id": value.protocol_id,
        "no_retune_rule": value.no_retune_rule,
        "bindings": _bindings_payload(value.bindings),
        "base_ladder": [_base_payload(item) for item in value.base_ladder],
        "region_ladder": [list(item) for item in value.region_ladder],
        "focused_selector": value.focused_selector,
        "max_focus_regions": value.max_focus_regions,
        "max_focus_occurrences": value.max_focus_occurrences,
        "max_region_option_proofs": value.max_region_option_proofs,
        "focused_evaluation_mode": value.focused_evaluation_mode,
        "focused_prefix_rerender": value.focused_prefix_rerender,
        "whole_visual_stages": list(value.whole_visual_stages),
        "finalist_base_count": value.finalist_base_count,
        "beam_width": value.beam_width,
        "camera_count": value.camera_count,
        "passes": list(value.passes),
        "pose_limit": value.pose_limit,
        "compiled_selector": value.compiled_selector,
        "exact_fallback": value.exact_fallback,
        "required_gates": list(value.required_gates),
    }
    if include_seal:
        payload["protocol_sha256"] = value.protocol_sha256
    return payload


def _authenticate_calibration_approval(
    bindings: HoldoutFreezeBindings, expected_calibration_approval_sha256: str,
) -> None:
    expected = _sha(
        expected_calibration_approval_sha256,
        "expected reviewed holdout calibration approval seal",
    )
    if (
        bindings.reviewed_calibration_approval_sha256 != expected
        or bindings.calibration_approval.approval_sha256 != expected
    ):
        raise ValueError("holdout calibration approval does not match the reviewed freeze seal")


def frozen_holdout_protocol(
    bindings: HoldoutFreezeBindings, *, expected_calibration_approval_sha256: str,
) -> FrozenHoldoutProtocol:
    if not isinstance(bindings, HoldoutFreezeBindings):
        raise TypeError("holdout bindings are invalid")
    _authenticate_calibration_approval(bindings, expected_calibration_approval_sha256)
    values = dict(
        schema=1,
        protocol_id="lvs-holdout-region-tournament-v1",
        no_retune_rule="frozen-before-input-v1",
        bindings=bindings,
        base_ladder=BASE_LADDER,
        region_ladder=REGION_LADDER,
        focused_selector="exact-source-union-all-regions-v1",
        max_focus_regions=MAX_FOCUS_REGIONS,
        max_focus_occurrences=MAX_FOCUS_OCCURRENCES,
        max_region_option_proofs=MAX_REGION_OPTION_PROOFS,
        focused_evaluation_mode="isolated-once-per-region-option-v1",
        focused_prefix_rerender=False,
        whole_visual_stages=("base-terminal", "final-retained"),
        finalist_base_count=2,
        beam_width=4,
        camera_count=8,
        passes=("textured", "clay"),
        pose_limit=2,
        compiled_selector="actual-studiomdl-compiled-bytes-v1",
        exact_fallback="exact-original-family-v1",
        required_gates=REQUIRED_GATES,
        protocol_sha256="0" * 64,
    )
    provisional = FrozenHoldoutProtocol.__new__(FrozenHoldoutProtocol)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["protocol_sha256"] = _canonical_digest(
        holdout_protocol_payload(provisional, include_seal=False)
    )
    return FrozenHoldoutProtocol(**values)


def holdout_protocol_from_payload(
    value: object, *, expected_calibration_approval_sha256: str,
) -> FrozenHoldoutProtocol:
    fields = set(FrozenHoldoutProtocol.__dataclass_fields__)
    if type(value) is not dict or set(value) != fields:
        raise ValueError("holdout protocol fields are invalid")
    binding_fields = set(HoldoutFreezeBindings.__dataclass_fields__)
    if type(value["bindings"]) is not dict or set(value["bindings"]) != binding_fields:
        raise ValueError("holdout protocol binding fields are invalid")
    approval = value["bindings"]["calibration_approval"]
    parsed_approval = holdout_calibration_approval_from_payload(approval)
    if type(value["base_ladder"]) is not list:
        raise ValueError("holdout protocol base ladder is invalid")
    base_fields = set(BaseOption.__dataclass_fields__)
    bases = []
    for item in value["base_ladder"]:
        if type(item) is not dict or set(item) != base_fields:
            raise ValueError("holdout protocol base option fields are invalid")
        bases.append(BaseOption(**item))
    if (
        type(value["region_ladder"]) is not list
        or any(type(item) is not list or len(item) != 2 for item in value["region_ladder"])
        or type(value["whole_visual_stages"]) is not list
        or type(value["passes"]) is not list
        or type(value["required_gates"]) is not list
    ):
        raise ValueError("holdout protocol collection fields are invalid")
    copied = dict(value)
    copied_bindings = dict(value["bindings"])
    copied_bindings["calibration_approval"] = parsed_approval
    copied["bindings"] = HoldoutFreezeBindings(**copied_bindings)
    copied["base_ladder"] = tuple(bases)
    copied["region_ladder"] = tuple(tuple(item) for item in value["region_ladder"])
    copied["whole_visual_stages"] = tuple(value["whole_visual_stages"])
    copied["passes"] = tuple(value["passes"])
    copied["required_gates"] = tuple(value["required_gates"])
    protocol = FrozenHoldoutProtocol(**copied)
    _authenticate_calibration_approval(
        protocol.bindings, expected_calibration_approval_sha256,
    )
    return protocol


@dataclass(frozen=True)
class ExactFocusRegion:
    region_key: str
    source_identity: str
    exact_source_sha256: str
    occurrence_commitment_sha256: str
    static_risk_rank: int
    occurrence_count: int
    renderability: Literal["renderable", "unrenderable", "unknown"]

    def __post_init__(self) -> None:
        if (
            type(self.region_key) is not str or _REGION.fullmatch(self.region_key) is None
            or type(self.static_risk_rank) is not int or self.static_risk_rank < 0
            or type(self.occurrence_count) is not int or self.occurrence_count < 1
            or self.renderability not in {"renderable", "unrenderable", "unknown"}
        ):
            raise ValueError("exact focus region is invalid")
        _relative(self.source_identity, "exact focus source identity")
        _sha(self.exact_source_sha256, "exact focus source hash")
        _sha(self.occurrence_commitment_sha256, "exact focus occurrence commitment")


def _focus_region_payload(value: ExactFocusRegion) -> dict[str, object]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__}


@dataclass(frozen=True)
class ExactFocusUniverse:
    schema: Literal[1]
    selector: Literal["exact-source-union-all-regions-v1"]
    family_commitment_sha256: str
    regions: tuple[ExactFocusRegion, ...]
    risk_order: tuple[str, ...]
    universe_sha256: str

    def __post_init__(self) -> None:
        regions = tuple(self.regions)
        expected_ranks = tuple(range(len(regions)))
        expected_risk = tuple(
            item.region_key for item in sorted(
                regions, key=lambda item: (item.static_risk_rank, item.region_key),
            )
        )
        if (
            type(self.schema) is not int or self.schema != 1
            or self.selector != "exact-source-union-all-regions-v1"
            or not regions or len(regions) > MAX_FOCUS_REGIONS
            or any(not isinstance(item, ExactFocusRegion) for item in regions)
            or regions != tuple(sorted(regions, key=lambda item: item.region_key))
            or len({item.region_key for item in regions}) != len(regions)
            or tuple(sorted(item.static_risk_rank for item in regions)) != expected_ranks
            or sum(item.occurrence_count for item in regions) > MAX_FOCUS_OCCURRENCES
            or tuple(self.risk_order) != expected_risk
            or _sha(self.universe_sha256, "exact focus universe seal")
            != _canonical_digest(_focus_universe_payload(self, include_seal=False))
        ):
            raise ValueError("exact focus universe is invalid")
        _sha(self.family_commitment_sha256, "exact focus family commitment")
        object.__setattr__(self, "regions", regions)
        object.__setattr__(self, "risk_order", tuple(self.risk_order))


def _focus_universe_payload(
    value: ExactFocusUniverse, *, include_seal: bool = True,
) -> dict[str, object]:
    payload = {
        "schema": value.schema,
        "selector": value.selector,
        "family_commitment_sha256": value.family_commitment_sha256,
        "regions": [_focus_region_payload(item) for item in value.regions],
        "risk_order": list(value.risk_order),
    }
    if include_seal:
        payload["universe_sha256"] = value.universe_sha256
    return payload


def focus_universe_payload(
    value: ExactFocusUniverse, *, include_seal: bool = True,
) -> dict[str, object]:
    if not isinstance(value, ExactFocusUniverse):
        raise TypeError("exact focus universe is invalid")
    return _focus_universe_payload(value, include_seal=include_seal)


def build_focus_universe(
    *, family_commitment_sha256: str, regions: tuple[ExactFocusRegion, ...],
) -> ExactFocusUniverse:
    if type(regions) is not tuple or any(
        not isinstance(item, ExactFocusRegion) for item in regions
    ):
        raise TypeError("exact focus regions must be a tuple of exact regions")
    ordered = tuple(sorted(regions, key=lambda item: item.region_key))
    risk_order = tuple(
        item.region_key for item in sorted(
            ordered, key=lambda item: (item.static_risk_rank, item.region_key),
        )
    )
    values = dict(
        schema=1,
        selector="exact-source-union-all-regions-v1",
        family_commitment_sha256=_sha(
            family_commitment_sha256, "exact focus family commitment",
        ),
        regions=ordered,
        risk_order=risk_order,
        universe_sha256="0" * 64,
    )
    provisional = ExactFocusUniverse.__new__(ExactFocusUniverse)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["universe_sha256"] = _canonical_digest(
        _focus_universe_payload(provisional, include_seal=False)
    )
    return ExactFocusUniverse(**values)


@dataclass(frozen=True)
class LocalRegionOptionProof:
    schema: Literal[1]
    protocol_sha256: str
    universe_sha256: str
    region_key: str
    occurrence_commitment_sha256: str
    renderability: Literal["renderable", "unrenderable", "unknown"]
    exact_source_sha256: str
    candidate_source_sha256: str | None
    option_identity_sha256: str
    terminal_status: Literal[
        "exact-unchanged", "focused-pass", "focused-reject",
        "unknown-reject", "unrenderable-reject",
    ]
    focused_evidence_sha256: str
    cache_identity_sha256: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.schema) is not int or self.schema != 1
            or type(self.region_key) is not str or _REGION.fullmatch(self.region_key) is None
            or self.renderability not in {"renderable", "unrenderable", "unknown"}
            or self.terminal_status not in {
                "exact-unchanged", "focused-pass", "focused-reject",
                "unknown-reject", "unrenderable-reject",
            }
        ):
            raise ValueError("local region option proof is invalid")
        for name in (
            "protocol_sha256", "universe_sha256", "occurrence_commitment_sha256",
            "exact_source_sha256", "option_identity_sha256",
            "focused_evidence_sha256", "cache_identity_sha256", "evidence_sha256",
        ):
            _sha(getattr(self, name), f"local region option {name}")
        if self.candidate_source_sha256 is not None:
            _sha(self.candidate_source_sha256, "local region option candidate source")
        unchanged = self.candidate_source_sha256 == self.exact_source_sha256
        if self.terminal_status == "exact-unchanged":
            valid_status = (
                unchanged
                and self.focused_evidence_sha256 == _exact_equality_digest(
                    region_key=self.region_key,
                    exact_source_sha256=self.exact_source_sha256,
                    candidate_source_sha256=self.candidate_source_sha256,
                    occurrence_commitment_sha256=(
                        self.occurrence_commitment_sha256
                    ),
                )
            )
        elif self.terminal_status in {"focused-pass", "focused-reject"}:
            valid_status = (
                self.renderability == "renderable"
                and self.candidate_source_sha256 is not None and not unchanged
            )
        elif self.terminal_status == "unknown-reject":
            valid_status = (
                self.candidate_source_sha256 is None
                or (
                    self.renderability == "unknown"
                    and self.candidate_source_sha256 != self.exact_source_sha256
                )
            )
        else:
            valid_status = (
                self.renderability == "unrenderable"
                and self.candidate_source_sha256 is not None and not unchanged
            )
        if (
            not valid_status
            or self.cache_identity_sha256 != _canonical_digest(
                _local_cache_payload(self)
            )
            or self.evidence_sha256 != _canonical_digest(
                _local_proof_payload(self, include_seal=False)
            )
        ):
            raise ValueError(
                f"local region option {self.terminal_status} proof is invalid"
            )


def _local_cache_payload(value: LocalRegionOptionProof) -> dict[str, object]:
    return {
        "protocol_sha256": value.protocol_sha256,
        "universe_sha256": value.universe_sha256,
        "region_key": value.region_key,
        "occurrence_commitment_sha256": value.occurrence_commitment_sha256,
        "renderability": value.renderability,
        "exact_source_sha256": value.exact_source_sha256,
        "candidate_source_sha256": value.candidate_source_sha256,
        "option_identity_sha256": value.option_identity_sha256,
    }


def _exact_equality_digest(
    *, region_key: str, exact_source_sha256: str,
    candidate_source_sha256: str | None, occurrence_commitment_sha256: str,
) -> str:
    return _canonical_digest({
        "kind": "exact-source-equality-v1",
        "region_key": region_key,
        "exact_source_sha256": exact_source_sha256,
        "candidate_source_sha256": candidate_source_sha256,
        "occurrence_commitment_sha256": occurrence_commitment_sha256,
    })


def _local_proof_payload(
    value: LocalRegionOptionProof, *, include_seal: bool = True,
) -> dict[str, object]:
    payload = {
        "schema": value.schema,
        **_local_cache_payload(value),
        "terminal_status": value.terminal_status,
        "focused_evidence_sha256": value.focused_evidence_sha256,
        "cache_identity_sha256": value.cache_identity_sha256,
    }
    if include_seal:
        payload["evidence_sha256"] = value.evidence_sha256
    return payload


def build_local_region_option_proof(
    *,
    protocol: FrozenHoldoutProtocol,
    universe: ExactFocusUniverse,
    region_key: str,
    candidate_source_sha256: str | None,
    option_identity_sha256: str,
    terminal_status: str,
    focused_evidence_sha256: str | None = None,
) -> LocalRegionOptionProof:
    if not isinstance(protocol, FrozenHoldoutProtocol):
        raise TypeError("holdout protocol is invalid")
    if not isinstance(universe, ExactFocusUniverse):
        raise TypeError("exact focus universe is invalid")
    matches = tuple(item for item in universe.regions if item.region_key == region_key)
    if len(matches) != 1:
        raise ValueError("local region option is outside the exact universe")
    region = matches[0]
    if terminal_status in {"focused-pass", "focused-reject"} and region.renderability != "renderable":
        raise ValueError(f"local region option is {region.renderability}")
    expected_equality = _exact_equality_digest(
        region_key=region.region_key,
        exact_source_sha256=region.exact_source_sha256,
        candidate_source_sha256=candidate_source_sha256,
        occurrence_commitment_sha256=region.occurrence_commitment_sha256,
    )
    if focused_evidence_sha256 is None:
        if terminal_status != "exact-unchanged":
            raise ValueError("local region option focused evidence is missing")
        focused_evidence_sha256 = expected_equality
    elif (
        terminal_status == "exact-unchanged"
        and focused_evidence_sha256 != expected_equality
    ):
        raise ValueError("local region exact equality proof differs")
    values = dict(
        schema=1,
        protocol_sha256=protocol.protocol_sha256,
        universe_sha256=universe.universe_sha256,
        region_key=region.region_key,
        occurrence_commitment_sha256=region.occurrence_commitment_sha256,
        renderability=region.renderability,
        exact_source_sha256=region.exact_source_sha256,
        candidate_source_sha256=candidate_source_sha256,
        option_identity_sha256=_sha(
            option_identity_sha256, "local region option identity",
        ),
        terminal_status=terminal_status,
        focused_evidence_sha256=_sha(
            focused_evidence_sha256, "local region focused evidence",
        ),
        cache_identity_sha256="0" * 64,
        evidence_sha256="0" * 64,
    )
    provisional = LocalRegionOptionProof.__new__(LocalRegionOptionProof)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["cache_identity_sha256"] = _canonical_digest(
        _local_cache_payload(provisional)
    )
    object.__setattr__(provisional, "cache_identity_sha256", values["cache_identity_sha256"])
    values["evidence_sha256"] = _canonical_digest(
        _local_proof_payload(provisional, include_seal=False)
    )
    return LocalRegionOptionProof(**values)


def build_local_region_option_proof_cache(
    proofs: tuple[LocalRegionOptionProof, ...],
) -> tuple[LocalRegionOptionProof, ...]:
    if type(proofs) is not tuple or any(
        not isinstance(item, LocalRegionOptionProof) for item in proofs
    ):
        raise TypeError("local region option proof cache inputs are invalid")
    if len(proofs) > MAX_REGION_OPTION_PROOFS:
        raise ValueError("local region option proof cache exceeds frozen bound")
    by_identity: dict[str, LocalRegionOptionProof] = {}
    for proof in proofs:
        previous = by_identity.get(proof.cache_identity_sha256)
        if previous is not None and previous.evidence_sha256 != proof.evidence_sha256:
            raise ValueError("local region option proof cache has conflicting evidence")
        by_identity[proof.cache_identity_sha256] = proof
    return tuple(by_identity[key] for key in sorted(by_identity))


@dataclass(frozen=True)
class FocusedCoverageEvidence:
    schema: Literal[1]
    protocol_sha256: str
    universe: ExactFocusUniverse
    recipe_sha256: str
    evaluation_mode: Literal["isolated-once-per-region-option-v1"]
    evaluation_order: tuple[str, ...]
    proofs: tuple[LocalRegionOptionProof, ...]
    changed_region_count: int
    terminal_changed_count: int
    authorized: bool
    evidence_sha256: str

    def __post_init__(self) -> None:
        proofs = tuple(self.proofs)
        changed = tuple(
            item for item in proofs
            if item.candidate_source_sha256 != item.exact_source_sha256
        )
        expected_authorized = all(
            item.terminal_status in {"exact-unchanged", "focused-pass"}
            for item in proofs
        )
        if (
            type(self.schema) is not int or self.schema != 1
            or not isinstance(self.universe, ExactFocusUniverse)
            or not proofs
            or self.protocol_sha256 != proofs[0].protocol_sha256
        ):
            raise ValueError("focused coverage identity is invalid")
        if (
            self.evaluation_mode != "isolated-once-per-region-option-v1"
            or tuple(self.evaluation_order) != self.universe.risk_order
            or len(proofs) != len(self.universe.regions)
            or tuple(item.region_key for item in proofs) != self.universe.risk_order
            or any(item.universe_sha256 != self.universe.universe_sha256 for item in proofs)
            or len({item.region_key for item in proofs}) != len(proofs)
            or len({item.cache_identity_sha256 for item in proofs}) != len(proofs)
            or type(self.changed_region_count) is not int
            or self.changed_region_count != len(changed)
            or type(self.terminal_changed_count) is not int
            or self.terminal_changed_count != len(changed)
            or type(self.authorized) is not bool or self.authorized != expected_authorized
            or _sha(self.recipe_sha256, "focused coverage recipe seal") is None
            or _sha(self.evidence_sha256, "focused coverage seal")
            != _canonical_digest(_focused_coverage_payload(self, include_seal=False))
        ):
            raise ValueError("focused coverage is incomplete or invalid")
        object.__setattr__(self, "evaluation_order", tuple(self.evaluation_order))
        object.__setattr__(self, "proofs", proofs)


def _focused_coverage_payload(
    value: FocusedCoverageEvidence, *, include_seal: bool = True,
) -> dict[str, object]:
    payload = {
        "schema": value.schema,
        "protocol_sha256": value.protocol_sha256,
        "universe": _focus_universe_payload(value.universe),
        "recipe_sha256": value.recipe_sha256,
        "evaluation_mode": value.evaluation_mode,
        "evaluation_order": list(value.evaluation_order),
        "proofs": [_local_proof_payload(item) for item in value.proofs],
        "changed_region_count": value.changed_region_count,
        "terminal_changed_count": value.terminal_changed_count,
        "authorized": value.authorized,
    }
    if include_seal:
        payload["evidence_sha256"] = value.evidence_sha256
    return payload


def focused_coverage_payload(
    value: FocusedCoverageEvidence, *, include_seal: bool = True,
) -> dict[str, object]:
    if not isinstance(value, FocusedCoverageEvidence):
        raise TypeError("focused coverage is invalid")
    return _focused_coverage_payload(value, include_seal=include_seal)


def build_focused_coverage(
    *, protocol: FrozenHoldoutProtocol, universe: ExactFocusUniverse,
    recipe: "CandidateRecipe", proofs: tuple[LocalRegionOptionProof, ...],
) -> FocusedCoverageEvidence:
    if not isinstance(protocol, FrozenHoldoutProtocol):
        raise TypeError("holdout protocol is invalid")
    if not isinstance(universe, ExactFocusUniverse):
        raise TypeError("exact focus universe is invalid")
    if (
        not isinstance(recipe, CandidateRecipe)
        or type(proofs) is not tuple
        or any(not isinstance(item, LocalRegionOptionProof) for item in proofs)
    ):
        raise TypeError("focused coverage inputs are invalid")
    by_key = {item.region_key: item for item in proofs}
    if (
        len(by_key) != len(proofs)
        or set(by_key) != {item.region_key for item in universe.regions}
    ):
        raise ValueError("focused coverage is not complete for exact universe")
    ordered = tuple(by_key[key] for key in universe.risk_order)
    changed = tuple(
        item for item in ordered
        if item.candidate_source_sha256 != item.exact_source_sha256
    )
    authorized = all(
        item.terminal_status in {"exact-unchanged", "focused-pass"}
        for item in ordered
    )
    values = dict(
        schema=1,
        protocol_sha256=protocol.protocol_sha256,
        universe=universe,
        recipe_sha256=recipe.recipe_sha256,
        evaluation_mode="isolated-once-per-region-option-v1",
        evaluation_order=universe.risk_order,
        proofs=ordered,
        changed_region_count=len(changed),
        terminal_changed_count=len(changed),
        authorized=authorized,
        evidence_sha256="0" * 64,
    )
    provisional = FocusedCoverageEvidence.__new__(FocusedCoverageEvidence)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["evidence_sha256"] = _canonical_digest(
        _focused_coverage_payload(provisional, include_seal=False)
    )
    return FocusedCoverageEvidence(**values)


@dataclass(frozen=True, order=True)
class RegionOption:
    region_rank: int
    region_key: str
    option_index: int
    strategy: Literal["exact-source-v1", "meshopt-remapped-visual-v1"]
    ratio: float | None
    target_error: float | None
    transfer: Literal["exact-source-v1", "visual-remapped-topology-v1"]
    update_vertices: Literal[False]

    def __post_init__(self) -> None:
        if (
            type(self.region_rank) is not int or self.region_rank < 0
            or type(self.region_key) is not str or _REGION.fullmatch(self.region_key) is None
            or type(self.option_index) is not int or not 0 <= self.option_index <= len(REGION_LADDER)
            or self.update_vertices is not False
        ):
            raise ValueError("holdout region option identity is invalid")
        if self.option_index == 0:
            if (
                self.strategy != "exact-source-v1" or self.ratio is not None
                or self.target_error is not None or self.transfer != "exact-source-v1"
            ):
                raise ValueError("holdout exact region option is invalid")
        else:
            expected = REGION_LADDER[self.option_index - 1]
            if (
                self.strategy != "meshopt-remapped-visual-v1"
                or (self.ratio, self.target_error) != expected
                or self.transfer != "visual-remapped-topology-v1"
            ):
                raise ValueError("holdout visual region option is outside the frozen ladder")


def _region_payload(value: RegionOption) -> dict[str, object]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__}


def region_tournament_options(
    protocol: FrozenHoldoutProtocol,
    focuses: tuple[tuple[int, str], ...],
) -> tuple[RegionOption, ...]:
    if not isinstance(protocol, FrozenHoldoutProtocol):
        raise TypeError("holdout protocol is invalid")
    if type(focuses) is not tuple or any(type(item) is not tuple or len(item) != 2 for item in focuses):
        raise ValueError("holdout focus identities are invalid")
    ordered = tuple(sorted(focuses))
    if (
        len(ordered) > protocol.max_focus_regions
        or tuple(item[0] for item in ordered) != tuple(range(len(ordered)))
        or len({item[1] for item in ordered}) != len(ordered)
    ):
        raise ValueError("holdout focus ranks are not canonical and complete")
    options = []
    for rank, key in ordered:
        options.append(RegionOption(
            rank, key, 0, "exact-source-v1", None, None, "exact-source-v1", False,
        ))
        for index, (ratio, error) in enumerate(protocol.region_ladder, 1):
            options.append(RegionOption(
                rank, key, index, "meshopt-remapped-visual-v1", ratio, error,
                "visual-remapped-topology-v1", False,
            ))
    return tuple(options)


@dataclass(frozen=True)
class CandidateRecipe:
    schema: Literal[1]
    kind: Literal["exact-original", "base", "region-composition"]
    base: BaseOption | None
    regions: tuple[RegionOption, ...]
    recipe_sha256: str

    def __post_init__(self) -> None:
        regions = tuple(self.regions)
        if (
            type(self.schema) is not int or self.schema != 1
            or self.kind not in {"exact-original", "base", "region-composition"}
            or any(not isinstance(item, RegionOption) for item in regions)
            or tuple(sorted(regions)) != regions
            or len({item.region_rank for item in regions}) != len(regions)
            or len({item.region_key for item in regions}) != len(regions)
            or _sha(self.recipe_sha256, "holdout recipe seal")
            != _canonical_digest(_recipe_payload(self, include_seal=False))
        ):
            raise ValueError("holdout candidate recipe is invalid")
        if self.kind == "exact-original":
            if self.base is not None or regions:
                raise ValueError("holdout exact recipe is not exact")
        elif self.kind == "base":
            if self.base not in BASE_LADDER or regions:
                raise ValueError("holdout base recipe is outside the frozen ladder")
        elif (
            self.base not in BASE_LADDER or not 1 <= len(regions) <= MAX_FOCUS_REGIONS
            or tuple(item.region_rank for item in regions) != tuple(range(len(regions)))
        ):
            raise ValueError("holdout composition recipe is incomplete")
        object.__setattr__(self, "regions", regions)


def _recipe_payload(value: CandidateRecipe, *, include_seal: bool = True) -> dict[str, object]:
    payload = {
        "schema": value.schema,
        "kind": value.kind,
        "base": None if value.base is None else _base_payload(value.base),
        "regions": [_region_payload(item) for item in value.regions],
    }
    if include_seal:
        payload["recipe_sha256"] = value.recipe_sha256
    return payload


def _build_recipe(kind: str, base, regions) -> CandidateRecipe:
    values = dict(
        schema=1, kind=kind, base=base, regions=tuple(regions), recipe_sha256="0" * 64,
    )
    provisional = CandidateRecipe.__new__(CandidateRecipe)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["recipe_sha256"] = _canonical_digest(
        _recipe_payload(provisional, include_seal=False)
    )
    return CandidateRecipe(**values)


def exact_recipe(protocol: FrozenHoldoutProtocol) -> CandidateRecipe:
    if not isinstance(protocol, FrozenHoldoutProtocol):
        raise TypeError("holdout protocol is invalid")
    return _build_recipe("exact-original", None, ())


def base_recipe(
    protocol: FrozenHoldoutProtocol, base: BaseOption,
) -> CandidateRecipe:
    if not isinstance(protocol, FrozenHoldoutProtocol) or base not in protocol.base_ladder:
        raise ValueError("holdout base recipe is outside the frozen ladder")
    return _build_recipe("base", base, ())


def expected_base_recipes(
    protocol: FrozenHoldoutProtocol,
) -> tuple[CandidateRecipe, ...]:
    if not isinstance(protocol, FrozenHoldoutProtocol):
        raise TypeError("holdout protocol is invalid")
    return tuple(base_recipe(protocol, item) for item in protocol.base_ladder)


def composition_recipe(
    protocol: FrozenHoldoutProtocol,
    base: BaseOption,
    regions: tuple[RegionOption, ...],
) -> CandidateRecipe:
    if not isinstance(protocol, FrozenHoldoutProtocol) or base not in protocol.base_ladder:
        raise ValueError("holdout composition base is outside the frozen ladder")
    ordered = tuple(sorted(regions))
    if (
        not ordered or len(ordered) > protocol.max_focus_regions
        or tuple(item.region_rank for item in ordered) != tuple(range(len(ordered)))
    ):
        raise ValueError("holdout composition regions are outside the focused prefix")
    for item in ordered:
        expected = region_tournament_options(
            protocol, tuple((value.region_rank, value.region_key) for value in ordered),
        )
        if item not in expected:
            raise ValueError("holdout composition option is outside the frozen tournament")
    return _build_recipe("region-composition", base, ordered)


def candidate_recipe_id(recipe: CandidateRecipe) -> str:
    if not isinstance(recipe, CandidateRecipe):
        raise TypeError("holdout recipe is invalid")
    prefix = {
        "exact-original": "exact", "base": "base", "region-composition": "tournament",
    }[recipe.kind]
    return f"{prefix}-{recipe.recipe_sha256[:32]}"


@dataclass(frozen=True, order=True)
class CompiledArtifact:
    relative_path: str
    size: int
    sha256: str

    def __post_init__(self) -> None:
        path = _relative(self.relative_path, "compiled artifact path")
        if (
            type(self.size) is not int or self.size < 1
            or PurePosixPath(path).suffix.casefold()
            not in {".mdl", ".vvd", ".vtx", ".phy", ".ani"}
        ):
            raise ValueError("compiled artifact is invalid")
        _sha(self.sha256, "compiled artifact hash")


def _artifact_payload(value: CompiledArtifact) -> dict[str, object]:
    return {
        "relative_path": value.relative_path,
        "size": value.size,
        "sha256": value.sha256,
    }


@dataclass(frozen=True)
class CompiledFamilyProof:
    schema: Literal[1]
    artifacts: tuple[CompiledArtifact, ...]
    total_bytes: int
    manifest_sha256: str

    def __post_init__(self) -> None:
        artifacts = tuple(self.artifacts)
        suffixes = {PurePosixPath(item.relative_path).suffix.casefold() for item in artifacts}
        if (
            type(self.schema) is not int or self.schema != 1
            or not artifacts or len(artifacts) > _MAX_COMPILED_FILES
            or any(not isinstance(item, CompiledArtifact) for item in artifacts)
            or artifacts != tuple(sorted(artifacts))
            or len({item.relative_path.casefold() for item in artifacts}) != len(artifacts)
            or type(self.total_bytes) is not int
            or self.total_bytes != sum(item.size for item in artifacts)
            or self.total_bytes > _MAX_COMPILED_BYTES
            or not {".mdl", ".vvd"}.issubset(suffixes)
            or _sha(self.manifest_sha256, "compiled manifest seal")
            != _canonical_digest({
                "schema": 1,
                "artifacts": [_artifact_payload(item) for item in artifacts],
                "total_bytes": self.total_bytes,
            })
        ):
            raise ValueError("compiled family proof is invalid")
        object.__setattr__(self, "artifacts", artifacts)


def build_compiled_proof(
    artifacts: tuple[CompiledArtifact, ...],
) -> CompiledFamilyProof:
    ordered = tuple(sorted(artifacts))
    payload = {
        "schema": 1,
        "artifacts": [_artifact_payload(item) for item in ordered],
        "total_bytes": sum(item.size for item in ordered),
    }
    return CompiledFamilyProof(
        1, ordered, payload["total_bytes"], _canonical_digest(payload),
    )


@dataclass(frozen=True)
class HoldoutAttemptEvidence:
    schema: Literal[1]
    protocol_sha256: str
    input_commitment_sha256: str
    family_commitment_sha256: str
    candidate_id: str
    recipe: CandidateRecipe
    compiled: CompiledFamilyProof
    attempt_stage: Literal["base-terminal", "region-intermediate", "final-retained"]
    focused_coverage: FocusedCoverageEvidence | None
    terminal_status: Literal["authorized", "eligible", "rejected"]
    gate_seals: tuple[tuple[str, str], ...]
    failed_gate: str | None
    replay_compiled_manifest_sha256: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "protocol_sha256", "input_commitment_sha256", "family_commitment_sha256",
            "replay_compiled_manifest_sha256", "evidence_sha256",
        ):
            _sha(getattr(self, name), f"holdout attempt {name}")
        gates = tuple(tuple(item) for item in self.gate_seals)
        try:
            required = required_gates_for_stage(self.attempt_stage)
        except ValueError as exc:
            raise ValueError("holdout attempt stage is invalid") from exc
        if (
            type(self.schema) is not int or self.schema != 1
            or not isinstance(self.recipe, CandidateRecipe)
            or self.candidate_id != candidate_recipe_id(self.recipe)
            or not isinstance(self.compiled, CompiledFamilyProof)
            or self.terminal_status not in {"authorized", "eligible", "rejected"}
            or any(
                type(item) is not tuple or len(item) != 2
                or item[0] not in required or _HASH.fullmatch(item[1]) is None
                for item in gates
            )
            or tuple(item[0] for item in gates) != required[:len(gates)]
        ):
            raise ValueError("holdout attempt identity or gate evidence is invalid")
        if self.attempt_stage == "region-intermediate":
            if self.recipe.kind != "region-composition" or self.terminal_status == "authorized":
                raise ValueError("holdout intermediate attempt cannot authorize")
        elif self.attempt_stage == "base-terminal":
            if self.recipe.kind != "base":
                raise ValueError("holdout base-terminal attempt recipe is invalid")
        elif self.recipe.kind == "base":
            raise ValueError("holdout base attempt cannot be final-retained")
        if (
            self.attempt_stage != "region-intermediate"
            and self.terminal_status == "eligible"
        ):
            raise ValueError("holdout non-intermediate attempt cannot be eligible")
        if self.recipe.kind == "base" and self.attempt_stage != "base-terminal":
            raise ValueError("holdout base attempt stage is invalid")
        if self.recipe.kind == "exact-original" and self.attempt_stage != "final-retained":
            raise ValueError("holdout exact fallback stage is invalid")
        if self.terminal_status in {"authorized", "eligible"}:
            if tuple(item[0] for item in gates) != required or self.failed_gate is not None:
                raise ValueError("holdout authorized attempt is missing a gate")
        elif (
            self.failed_gate not in required
            or len(gates) != required.index(self.failed_gate)
        ):
            raise ValueError("holdout rejected attempt failure gate is invalid")
        focus_index = required.index("focused_all_changed")
        focus_reached = len(gates) >= focus_index
        coverage = self.focused_coverage
        if coverage is not None:
            if (
                not isinstance(coverage, FocusedCoverageEvidence)
                or coverage.protocol_sha256 != self.protocol_sha256
                or coverage.universe.family_commitment_sha256
                != self.family_commitment_sha256
                or coverage.recipe_sha256 != self.recipe.recipe_sha256
            ):
                raise ValueError(
                    "holdout attempt protocol/focused coverage identity is invalid"
                )
        if self.terminal_status in {"authorized", "eligible"}:
            if coverage is None or not coverage.authorized:
                raise ValueError("holdout attempt focused coverage is missing or rejected")
        elif self.failed_gate == "focused_all_changed":
            if coverage is None or coverage.authorized:
                raise ValueError("holdout focused rejection lacks rejected coverage")
        elif focus_reached and self.failed_gate != "focused_all_changed":
            if coverage is None or not coverage.authorized:
                raise ValueError("holdout post-focus rejection lacks authorized coverage")
        elif coverage is not None:
            raise ValueError("holdout pre-focus rejection has unexpected focused coverage")
        if self.replay_compiled_manifest_sha256 != self.compiled.manifest_sha256:
            raise ValueError("holdout attempt replay compiled manifest differs")
        if self.evidence_sha256 != _canonical_digest(
            _attempt_payload(self, include_seal=False)
        ):
            raise ValueError("holdout attempt protocol/evidence seal differs")
        object.__setattr__(self, "gate_seals", gates)


def _attempt_payload(
    value: HoldoutAttemptEvidence, *, include_seal: bool = True,
) -> dict[str, object]:
    payload = {
        "schema": value.schema,
        "protocol_sha256": value.protocol_sha256,
        "input_commitment_sha256": value.input_commitment_sha256,
        "family_commitment_sha256": value.family_commitment_sha256,
        "candidate_id": value.candidate_id,
        "recipe": _recipe_payload(value.recipe),
        "compiled": {
            "schema": value.compiled.schema,
            "artifacts": [_artifact_payload(item) for item in value.compiled.artifacts],
            "total_bytes": value.compiled.total_bytes,
            "manifest_sha256": value.compiled.manifest_sha256,
        },
        "attempt_stage": value.attempt_stage,
        "focused_coverage": (
            None if value.focused_coverage is None
            else _focused_coverage_payload(value.focused_coverage)
        ),
        "terminal_status": value.terminal_status,
        "gate_seals": [list(item) for item in value.gate_seals],
        "failed_gate": value.failed_gate,
        "replay_compiled_manifest_sha256": value.replay_compiled_manifest_sha256,
    }
    if include_seal:
        payload["evidence_sha256"] = value.evidence_sha256
    return payload


def _validate_recipe(protocol: FrozenHoldoutProtocol, recipe: CandidateRecipe) -> None:
    if recipe.kind == "exact-original":
        if recipe != exact_recipe(protocol):
            raise ValueError("holdout exact recipe differs")
        return
    if recipe.kind == "base":
        if recipe not in expected_base_recipes(protocol):
            raise ValueError("holdout base recipe differs from frozen protocol")
        return
    if recipe.base not in protocol.base_ladder:
        raise ValueError("holdout recipe base differs from frozen protocol")
    expected = region_tournament_options(
        protocol, tuple((item.region_rank, item.region_key) for item in recipe.regions),
    )
    if any(item not in expected for item in recipe.regions):
        raise ValueError("holdout recipe region option differs from frozen protocol")


def select_base_finalists(
    protocol: FrozenHoldoutProtocol,
    attempts: tuple["HoldoutAttemptEvidence", ...],
) -> tuple["HoldoutAttemptEvidence", ...]:
    if not isinstance(protocol, FrozenHoldoutProtocol) or type(attempts) is not tuple:
        raise TypeError("holdout base finalist inputs are invalid")
    expected = expected_base_recipes(protocol)
    by_recipe = {item.recipe.recipe_sha256: item for item in attempts}
    if (
        len(by_recipe) != len(attempts)
        or set(by_recipe) != {item.recipe_sha256 for item in expected}
        or any(item.protocol_sha256 != protocol.protocol_sha256 for item in attempts)
        or len({item.input_commitment_sha256 for item in attempts}) != 1
        or len({item.family_commitment_sha256 for item in attempts}) != 1
    ):
        raise ValueError("holdout base ladder terminal coverage is incomplete")
    passing = tuple(
        item for item in attempts if item.terminal_status == "authorized"
    )
    return tuple(sorted(passing, key=lambda item: (
        item.compiled.total_bytes, item.candidate_id,
    ))[:protocol.finalist_base_count])


def build_attempt(
    *,
    protocol: FrozenHoldoutProtocol,
    input_commitment_sha256: str,
    family_commitment_sha256: str,
    recipe: CandidateRecipe,
    compiled: CompiledFamilyProof,
    attempt_stage: str,
    terminal_status: str,
    gate_seals: tuple[tuple[str, str], ...],
    failed_gate: str | None,
    focused_coverage: FocusedCoverageEvidence | None,
) -> HoldoutAttemptEvidence:
    if not isinstance(protocol, FrozenHoldoutProtocol):
        raise TypeError("holdout protocol is invalid")
    _validate_recipe(protocol, recipe)
    values = dict(
        schema=1,
        protocol_sha256=protocol.protocol_sha256,
        input_commitment_sha256=_sha(input_commitment_sha256, "holdout input commitment"),
        family_commitment_sha256=_sha(family_commitment_sha256, "holdout family commitment"),
        candidate_id=candidate_recipe_id(recipe),
        recipe=recipe,
        compiled=compiled,
        attempt_stage=attempt_stage,
        focused_coverage=focused_coverage,
        terminal_status=terminal_status,
        gate_seals=tuple(gate_seals),
        failed_gate=failed_gate,
        replay_compiled_manifest_sha256=compiled.manifest_sha256,
        evidence_sha256="0" * 64,
    )
    provisional = HoldoutAttemptEvidence.__new__(HoldoutAttemptEvidence)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["evidence_sha256"] = _canonical_digest(
        _attempt_payload(provisional, include_seal=False)
    )
    return HoldoutAttemptEvidence(**values)


def select_holdout_winner(
    protocol: FrozenHoldoutProtocol,
    attempts: tuple[HoldoutAttemptEvidence, ...],
) -> HoldoutAttemptEvidence:
    if not isinstance(protocol, FrozenHoldoutProtocol) or type(attempts) is not tuple:
        raise TypeError("holdout selection inputs are invalid")
    if not attempts or any(not isinstance(item, HoldoutAttemptEvidence) for item in attempts):
        raise ValueError("holdout attempts are invalid")
    input_hashes = {item.input_commitment_sha256 for item in attempts}
    family_hashes = {item.family_commitment_sha256 for item in attempts}
    if (
        any(item.protocol_sha256 != protocol.protocol_sha256 for item in attempts)
        or len(input_hashes) != 1 or len(family_hashes) != 1
        or len({item.candidate_id for item in attempts}) != len(attempts)
    ):
        raise ValueError("holdout attempt set differs from protocol/input identity")
    for item in attempts:
        _validate_recipe(protocol, item.recipe)
    exact_id = candidate_recipe_id(exact_recipe(protocol))
    originals = tuple(
        item for item in attempts
        if item.candidate_id == exact_id and item.terminal_status == "authorized"
    )
    if len(originals) != 1:
        raise ValueError("holdout exact original fallback is missing or ambiguous")
    original = originals[0]
    passing_savings = tuple(
        item for item in attempts
        if item.terminal_status == "authorized"
        and item.attempt_stage == "final-retained"
        and item.candidate_id != exact_id
        and item.compiled.total_bytes < original.compiled.total_bytes
    )
    if not passing_savings:
        return original
    return min(passing_savings, key=lambda item: (
        item.compiled.total_bytes, item.candidate_id,
    ))


@dataclass(frozen=True)
class HoldoutRunEvidence:
    schema: Literal[1]
    protocol_sha256: str
    input_commitment_sha256: str
    family_commitment_sha256: str
    attempts: tuple[HoldoutAttemptEvidence, ...]
    tournament_trace_sha256: str
    fresh_run_sha256: str
    replay_run_sha256: str
    winner_candidate_id: str
    no_retune_rule: Literal["frozen-before-input-v1"]
    quality_scope: Literal["holdout-evaluation-only"]
    authorizing_production: Literal[False]
    evidence_sha256: str

    def __post_init__(self) -> None:
        attempts = tuple(self.attempts)
        if any(not isinstance(item, HoldoutAttemptEvidence) for item in attempts):
            raise ValueError("holdout run attempts are invalid")
        for name in (
            "protocol_sha256", "input_commitment_sha256", "family_commitment_sha256",
            "tournament_trace_sha256", "fresh_run_sha256", "replay_run_sha256",
            "evidence_sha256",
        ):
            _sha(getattr(self, name), f"holdout run {name}")
        originals = tuple(
            item for item in attempts
            if item.recipe.kind == "exact-original"
            and item.terminal_status == "authorized"
        )
        expected_winner = None
        if len(originals) == 1:
            original = originals[0]
            savings = tuple(
                item for item in attempts
                if item.terminal_status == "authorized"
                and item.attempt_stage == "final-retained"
                and item.recipe.kind != "exact-original"
                and item.compiled.total_bytes < original.compiled.total_bytes
            )
            expected_winner = (
                original if not savings else min(savings, key=lambda item: (
                    item.compiled.total_bytes, item.candidate_id,
                ))
            ).candidate_id
        if (
            type(self.schema) is not int or self.schema != 1
            or not attempts or attempts != tuple(sorted(attempts, key=lambda item: item.candidate_id))
            or any(item.protocol_sha256 != self.protocol_sha256 for item in attempts)
            or any(item.input_commitment_sha256 != self.input_commitment_sha256 for item in attempts)
            or any(item.family_commitment_sha256 != self.family_commitment_sha256 for item in attempts)
            or expected_winner is None or self.winner_candidate_id != expected_winner
            or self.fresh_run_sha256 != self.replay_run_sha256
            or self.no_retune_rule != "frozen-before-input-v1"
            or self.quality_scope != "holdout-evaluation-only"
            or self.authorizing_production is not False
            or self.evidence_sha256 != _canonical_digest(
                _run_payload(self, include_seal=False)
            )
        ):
            raise ValueError("holdout run evidence is invalid")
        object.__setattr__(self, "attempts", attempts)


def _run_payload(value: HoldoutRunEvidence, *, include_seal: bool = True) -> dict[str, object]:
    payload = {
        "schema": value.schema,
        "protocol_sha256": value.protocol_sha256,
        "input_commitment_sha256": value.input_commitment_sha256,
        "family_commitment_sha256": value.family_commitment_sha256,
        "attempts": [_attempt_payload(item) for item in value.attempts],
        "tournament_trace_sha256": value.tournament_trace_sha256,
        "fresh_run_sha256": value.fresh_run_sha256,
        "replay_run_sha256": value.replay_run_sha256,
        "winner_candidate_id": value.winner_candidate_id,
        "no_retune_rule": value.no_retune_rule,
        "quality_scope": value.quality_scope,
        "authorizing_production": value.authorizing_production,
    }
    if include_seal:
        payload["evidence_sha256"] = value.evidence_sha256
    return payload


def holdout_run_evidence_payload(
    value: HoldoutRunEvidence, *, include_seal: bool = True,
) -> dict[str, object]:
    if not isinstance(value, HoldoutRunEvidence):
        raise TypeError("holdout run evidence is invalid")
    return _run_payload(value, include_seal=include_seal)


def _recipe_from_payload(value: object) -> CandidateRecipe:
    fields = set(CandidateRecipe.__dataclass_fields__)
    if type(value) is not dict or set(value) != fields:
        raise ValueError("holdout recipe payload fields are invalid")
    base = value["base"]
    if base is not None:
        if type(base) is not dict or set(base) != set(BaseOption.__dataclass_fields__):
            raise ValueError("holdout recipe base payload is invalid")
        base = BaseOption(**base)
    if type(value["regions"]) is not list:
        raise ValueError("holdout recipe region payload is invalid")
    regions = []
    fields_region = set(RegionOption.__dataclass_fields__)
    for item in value["regions"]:
        if type(item) is not dict or set(item) != fields_region:
            raise ValueError("holdout recipe region fields are invalid")
        regions.append(RegionOption(**item))
    copied = dict(value)
    copied["base"] = base
    copied["regions"] = tuple(regions)
    return CandidateRecipe(**copied)


def _compiled_from_payload(value: object) -> CompiledFamilyProof:
    fields = set(CompiledFamilyProof.__dataclass_fields__)
    if type(value) is not dict or set(value) != fields or type(value["artifacts"]) is not list:
        raise ValueError("holdout compiled proof payload fields are invalid")
    artifact_fields = set(CompiledArtifact.__dataclass_fields__)
    artifacts = []
    for item in value["artifacts"]:
        if type(item) is not dict or set(item) != artifact_fields:
            raise ValueError("holdout compiled artifact payload fields are invalid")
        artifacts.append(CompiledArtifact(**item))
    copied = dict(value)
    copied["artifacts"] = tuple(artifacts)
    return CompiledFamilyProof(**copied)


def _focus_universe_from_payload(value: object) -> ExactFocusUniverse:
    fields = set(ExactFocusUniverse.__dataclass_fields__)
    if (
        type(value) is not dict or set(value) != fields
        or type(value["regions"]) is not list
        or type(value["risk_order"]) is not list
    ):
        raise ValueError("exact focus universe payload fields are invalid")
    region_fields = set(ExactFocusRegion.__dataclass_fields__)
    regions = []
    for item in value["regions"]:
        if type(item) is not dict or set(item) != region_fields:
            raise ValueError("exact focus region payload fields are invalid")
        regions.append(ExactFocusRegion(**item))
    copied = dict(value)
    copied["regions"] = tuple(regions)
    copied["risk_order"] = tuple(value["risk_order"])
    return ExactFocusUniverse(**copied)


def _local_proof_from_payload(value: object) -> LocalRegionOptionProof:
    fields = set(LocalRegionOptionProof.__dataclass_fields__)
    if type(value) is not dict or set(value) != fields:
        raise ValueError("local region option payload fields are invalid")
    return LocalRegionOptionProof(**value)


def _focused_coverage_from_payload(value: object) -> FocusedCoverageEvidence:
    fields = set(FocusedCoverageEvidence.__dataclass_fields__)
    if (
        type(value) is not dict or set(value) != fields
        or type(value["proofs"]) is not list
        or type(value["evaluation_order"]) is not list
    ):
        raise ValueError("focused coverage payload fields are invalid")
    copied = dict(value)
    copied["universe"] = _focus_universe_from_payload(value["universe"])
    copied["proofs"] = tuple(_local_proof_from_payload(item) for item in value["proofs"])
    copied["evaluation_order"] = tuple(value["evaluation_order"])
    return FocusedCoverageEvidence(**copied)


def focus_universe_from_payload(value: object) -> ExactFocusUniverse:
    return _focus_universe_from_payload(value)


def focused_coverage_from_payload(value: object) -> FocusedCoverageEvidence:
    return _focused_coverage_from_payload(value)


def _attempt_from_payload(value: object) -> HoldoutAttemptEvidence:
    fields = set(HoldoutAttemptEvidence.__dataclass_fields__)
    if (
        type(value) is not dict or set(value) != fields
        or type(value["gate_seals"]) is not list
        or any(type(item) is not list or len(item) != 2 for item in value["gate_seals"])
    ):
        raise ValueError("holdout attempt payload fields are invalid")
    copied = dict(value)
    copied["recipe"] = _recipe_from_payload(value["recipe"])
    copied["compiled"] = _compiled_from_payload(value["compiled"])
    copied["focused_coverage"] = (
        None if value["focused_coverage"] is None
        else _focused_coverage_from_payload(value["focused_coverage"])
    )
    copied["gate_seals"] = tuple(tuple(item) for item in value["gate_seals"])
    return HoldoutAttemptEvidence(**copied)


def holdout_run_evidence_from_payload(value: object) -> HoldoutRunEvidence:
    fields = set(HoldoutRunEvidence.__dataclass_fields__)
    if type(value) is not dict or set(value) != fields or type(value["attempts"]) is not list:
        raise ValueError("holdout run evidence payload fields are invalid")
    copied = dict(value)
    copied["attempts"] = tuple(_attempt_from_payload(item) for item in value["attempts"])
    return HoldoutRunEvidence(**copied)


def build_holdout_run_evidence(
    *,
    protocol: FrozenHoldoutProtocol,
    input_commitment_sha256: str,
    family_commitment_sha256: str,
    attempts: tuple[HoldoutAttemptEvidence, ...],
    tournament_trace_sha256: str,
    fresh_run_sha256: str,
    replay_run_sha256: str,
) -> HoldoutRunEvidence:
    if fresh_run_sha256 != replay_run_sha256:
        raise ValueError("holdout fresh/replay run seals differ")
    base_attempts = tuple(item for item in attempts if item.recipe.kind == "base")
    finalists = select_base_finalists(protocol, base_attempts)
    for finalist in finalists:
        if not any(
            item.recipe.kind == "region-composition"
            and item.recipe.base == finalist.recipe.base
            for item in attempts
        ):
            raise ValueError("holdout tournament trace lacks a base finalist")
    winner = select_holdout_winner(protocol, attempts)
    ordered = tuple(sorted(attempts, key=lambda item: item.candidate_id))
    if (
        any(item.input_commitment_sha256 != input_commitment_sha256 for item in ordered)
        or any(item.family_commitment_sha256 != family_commitment_sha256 for item in ordered)
    ):
        raise ValueError("holdout run commitment differs from attempts")
    values = dict(
        schema=1,
        protocol_sha256=protocol.protocol_sha256,
        input_commitment_sha256=_sha(input_commitment_sha256, "holdout input commitment"),
        family_commitment_sha256=_sha(family_commitment_sha256, "holdout family commitment"),
        attempts=ordered,
        tournament_trace_sha256=_sha(tournament_trace_sha256, "holdout trace seal"),
        fresh_run_sha256=_sha(fresh_run_sha256, "holdout fresh run seal"),
        replay_run_sha256=_sha(replay_run_sha256, "holdout replay run seal"),
        winner_candidate_id=winner.candidate_id,
        no_retune_rule="frozen-before-input-v1",
        quality_scope="holdout-evaluation-only",
        authorizing_production=False,
        evidence_sha256="0" * 64,
    )
    provisional = HoldoutRunEvidence.__new__(HoldoutRunEvidence)
    for name, item in values.items():
        object.__setattr__(provisional, name, item)
    values["evidence_sha256"] = _canonical_digest(
        _run_payload(provisional, include_seal=False)
    )
    return HoldoutRunEvidence(**values)
