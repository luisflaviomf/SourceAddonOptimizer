from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath

from .domain import FamilyManifest, GateFailure, ValidationResult
from .qc_inventory import parse_qc_fingerprint


_SEMANTIC_FIELDS = (
    "model_name",
    "bodygroups",
    "materials",
    "skin_families",
    "bones",
    "bone_parents",
    "attachments",
    "hitboxes",
    "sequences",
)
_MESH_ROLE_FIELDS = ("mesh_files", "lod_mesh_files")
_ARTIFACT_KINDS = {
    ".mdl",
    ".vvd",
    ".ani",
    ".phy",
    ".dx90.vtx",
    ".dx80.vtx",
    ".vtx",
}


def _casefold_nested(value):
    if isinstance(value, str):
        return value.casefold()
    return tuple(_casefold_nested(item) for item in value)


def _require_equal(
    name: str,
    expected,
    actual,
    failures: list[GateFailure],
) -> None:
    if _casefold_nested(expected) != _casefold_nested(actual):
        failures.append(
            GateFailure(
                name,
                "family",
                repr(actual),
                repr(expected),
                f"{name} changed",
            )
        )


def _nonempty_count(values: tuple[str, ...]) -> int:
    return sum(bool(value.strip()) for value in values)


def _validated_model_path(model_rel: str) -> PurePosixPath:
    normalized = model_rel.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(model_rel)
    if (
        not normalized
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
        or posix_path.suffix.casefold() != ".mdl"
    ):
        raise ValueError(f"invalid model_rel: {model_rel}")
    return posix_path


def _artifact_relative_path(model_path: PurePosixPath, kind: str) -> str:
    normalized_kind = kind.casefold()
    if normalized_kind not in _ARTIFACT_KINDS:
        raise ValueError(f"unsupported artifact kind: {kind}")
    if normalized_kind == ".mdl":
        return model_path.as_posix()
    model_stem = model_path.as_posix()[: -len(model_path.suffix)]
    return f"{model_stem}{normalized_kind}"


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _compile_gates(
    manifest: FamilyManifest,
    compile_record: Mapping[str, object] | None,
    failures: list[GateFailure],
) -> None:
    record = compile_record if isinstance(compile_record, Mapping) else {}
    status = record.get("status")
    if status != "ok":
        failures.append(
            GateFailure(
                "compile_status",
                "family",
                status,
                "ok",
                "candidate compile did not complete successfully",
            )
        )

    compiled_model = record.get("model_rel")
    model_matches = (
        isinstance(compiled_model, str)
        and compiled_model.casefold() == manifest.model_rel.casefold()
    )
    if not model_matches:
        failures.append(
            GateFailure(
                "compile_model",
                "family",
                compiled_model if isinstance(compiled_model, str) else None,
                manifest.model_rel,
                "compile record belongs to a different model",
            )
        )


def _structural_gates(
    manifest: FamilyManifest,
    candidate_qc: Path,
    failures: list[GateFailure],
) -> None:
    expected = manifest.fingerprint
    actual = parse_qc_fingerprint(candidate_qc)
    for field_name in _SEMANTIC_FIELDS:
        _require_equal(
            field_name,
            getattr(expected, field_name),
            getattr(actual, field_name),
            failures,
        )

    for field_name in _MESH_ROLE_FIELDS:
        expected_count = _nonempty_count(getattr(expected, field_name))
        actual_count = _nonempty_count(getattr(actual, field_name))
        if expected_count != actual_count:
            failures.append(
                GateFailure(
                    field_name,
                    "family",
                    float(actual_count),
                    float(expected_count),
                    f"{field_name} non-empty role count changed",
                )
            )

    expected_physics = expected.physics_mesh is not None
    actual_physics = actual.physics_mesh is not None
    if expected_physics != actual_physics:
        failures.append(
            GateFailure(
                "physics_mesh",
                "family",
                str(actual_physics).lower(),
                str(expected_physics).lower(),
                "physics mesh presence changed",
            )
        )


def _artifact_gates(
    manifest: FamilyManifest,
    model_path: PurePosixPath,
    candidate_models_dir: Path,
    provenance: Mapping[str, object],
    failures: list[GateFailure],
) -> None:
    models_root = candidate_models_dir.resolve()
    for kind in manifest.required_artifact_kinds:
        relative_path = _artifact_relative_path(model_path, kind)
        artifact_path = models_root.joinpath(*PurePosixPath(relative_path).parts)
        resolved_artifact = artifact_path.resolve()
        if not _is_within(resolved_artifact, models_root) or not artifact_path.is_file():
            failures.append(
                GateFailure(
                    "missing_artifact",
                    relative_path,
                    None,
                    kind.casefold(),
                    f"required candidate artifact is missing: {relative_path}",
                )
            )
            continue

        if relative_path not in provenance:
            failures.append(
                GateFailure(
                    "missing_provenance",
                    relative_path,
                    None,
                    "candidate-compile",
                    f"candidate artifact has no provenance: {relative_path}",
                )
            )
        elif provenance[relative_path] != "candidate-compile":
            failures.append(
                GateFailure(
                    "hidden_fallback",
                    relative_path,
                    str(provenance[relative_path]),
                    "candidate-compile",
                    f"candidate artifact was not produced by candidate compile: {relative_path}",
                )
            )


def validate_structure(
    manifest: FamilyManifest,
    candidate_qc: Path,
    candidate_models_dir: Path,
    compile_record: Mapping[str, object] | None,
    provenance: Mapping[str, object],
) -> ValidationResult:
    model_path = _validated_model_path(manifest.model_rel)
    failures: list[GateFailure] = []
    _compile_gates(manifest, compile_record, failures)
    _structural_gates(manifest, candidate_qc, failures)
    _artifact_gates(
        manifest,
        model_path,
        candidate_models_dir,
        provenance,
        failures,
    )
    return ValidationResult(
        passed=not failures,
        failures=tuple(failures),
        metrics={"failure_count": float(len(failures))},
        worst_scope=failures[0].scope if failures else "",
    )
