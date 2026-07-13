from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import threading

from .composite import (
    build_adaptive_candidate_metrics_proof,
    candidate_spec_sha256,
    optimizer_contract_sha256,
    revalidate_recovery_snapshot,
)
from .domain import (
    AdaptiveCandidateMetricsProof,
    AdaptiveGraphOccurrenceProof,
    CandidateSpec,
    EligibleAdaptiveSourceProof,
    FamilyManifest,
    IneligibleAdaptiveSourceProof,
    RecoverySourceSnapshot,
    SourceFileProof,
    SourceTreeManifest,
    require_canonical_relative,
)
from .focused_cache import _read_regular_no_follow
from .qc_graph import QcGraph, QcReference, _lex, parse_qc_graph
from .reporting import canonical_json


_QC_GRAPH_FILE_LIMIT = 4096
_QC_GRAPH_BYTE_LIMIT = 64 * 1024 * 1024
_QC_GRAPH_OCCURRENCE_LIMIT = 4096
_CANDIDATE_METRICS_BYTE_LIMIT = 64 * 1024 * 1024
_EXACT_PRESERVATION_FIELDS = {
    "schema", "source_identity", "kind", "preserved_exact", "reason",
}
_EXACT_PRESERVATION_MATRIX = {
    ("eligible-exact-v1", True, "ratio-preserved-exact-v1"),
    ("eligible-exact-v1", True, "approved-exact-source-fallback-v1"),
    ("ineligible-changed-v1", False, "adaptive-output-changed-v1"),
}


def _relative(root: Path, path: Path, label: str) -> str:
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    try:
        value = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} escapes QC graph root") from exc
    return require_canonical_relative(value, label)


def _logical_path(value: str, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} is invalid")
    normalized = value.replace("\\", "/").strip()
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(normalized)
    if not normalized or posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError(f"{label} is not relative")
    return posix.as_posix()


def _reference_payload(
    reference: QcReference, root: Path, occurrence_index: int,
) -> dict[str, object]:
    return {
        "occurrence_index": occurrence_index,
        "graph_path": _relative(root, reference.graph_file, "QC reference graph path"),
        "directive": reference.directive,
        "line": reference.line,
        "logical_path": _logical_path(reference.logical_path, "QC reference logical path"),
        "source_path": _relative(root, reference.source_path, "QC reference source path"),
        "role": reference.role,
        "group": reference.group,
        "token_start": reference.token_start,
        "token_end": reference.token_end,
    }


def canonical_qc_graph_payload(
    graph: QcGraph, cancel_event: threading.Event | None = None,
) -> dict[str, object]:
    """Capture one parsed QC graph using only canonical relative identities and current bytes."""
    if not isinstance(graph, QcGraph):
        raise TypeError("QC graph is invalid")
    root = Path(os.path.abspath(graph.family_root))
    if len(graph.files) == 0 or len(graph.files) > _QC_GRAPH_FILE_LIMIT:
        raise ValueError("QC graph file count is invalid or exceeds bound")
    if len(graph.references) > _QC_GRAPH_OCCURRENCE_LIMIT:
        raise ValueError("QC graph occurrence count exceeds bound")
    root_qc = _relative(root, graph.root, "QC graph root")
    occurrence_indexes = {id(item): index for index, item in enumerate(graph.references)}
    if len(occurrence_indexes) != len(graph.references):
        raise ValueError("QC graph occurrences are not unique objects")

    total_bytes = 0
    files: list[dict[str, object]] = []
    paths: list[str] = []
    for graph_file in graph.files:
        path = _relative(root, graph_file.path, "QC graph file")
        if Path(path).suffix.casefold() not in {".qc", ".qci"}:
            raise ValueError("QC graph contains an unsupported control file")
        remaining = _QC_GRAPH_BYTE_LIMIT - total_bytes
        if remaining < 0:
            raise ValueError("QC graph bytes exceed bound")
        raw = _read_regular_no_follow(
            graph_file.path, cancel_event, contained_root=root, max_bytes=remaining,
        )
        total_bytes += len(raw)
        try:
            current_text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("current QC graph bytes are not UTF-8") from exc
        if current_text != graph_file.text:
            raise ValueError("QC graph bytes changed after parsing")
        references = []
        for reference in graph_file.references:
            index = occurrence_indexes.get(id(reference))
            if index is None:
                raise ValueError("QC graph file reference is absent from occurrence union")
            references.append(_reference_payload(reference, root, index))
        includes = [{
            "line": include.line,
            "logical_path": _logical_path(include.logical_path, "QC include logical path"),
            "target_path": _relative(root, include.include_path, "QC include target path"),
            "token_start": include.token_start,
            "token_end": include.token_end,
        } for include in graph_file.includes]
        files.append({
            "path": path, "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "includes": includes, "references": references,
        })
        paths.append(path)
    canonical_keys = [(path.casefold(), path) for path in paths]
    if canonical_keys != sorted(canonical_keys) or len({key[0] for key in canonical_keys}) != len(paths):
        raise ValueError("QC graph file paths are not canonical and unique")

    occurrences = [
        _reference_payload(reference, root, index)
        for index, reference in enumerate(graph.references)
    ]
    bodygroups = []
    for bodygroup in graph.bodygroups:
        choices: list[int | None] = []
        for choice in bodygroup.choices:
            if choice is None:
                choices.append(None)
                continue
            index = occurrence_indexes.get(id(choice))
            if index is None:
                raise ValueError("QC bodygroup choice is absent from occurrence union")
            choices.append(index)
        bodygroups.append({
            "graph_path": _relative(root, bodygroup.graph_file, "QC bodygroup graph path"),
            "line": bodygroup.line, "name": bodygroup.name,
            "group": bodygroup.group, "choices": choices,
        })
    bodygroups.sort(key=lambda item: (
        str(item["graph_path"]).casefold(), str(item["graph_path"]),
        int(item["line"]), str(item["name"]).casefold(), str(item["name"]),
    ))
    return {
        "schema": 1, "root_qc": root_qc, "files": files,
        "occurrences": occurrences, "bodygroups": bodygroups,
        "total_files": len(files), "total_bytes": total_bytes,
    }


def qc_graph_sha256(
    graph: QcGraph, cancel_event: threading.Event | None = None,
) -> str:
    return hashlib.sha256(
        canonical_json(canonical_qc_graph_payload(graph, cancel_event)).encode("utf-8")
    ).hexdigest()


def load_candidate_metrics_json(
    path: Path, contained_root: Path, cancel_event: threading.Event | None,
) -> tuple[dict[str, object], int, str]:
    raw = _read_regular_no_follow(
        Path(path), cancel_event, contained_root=Path(contained_root),
        max_bytes=_CANDIDATE_METRICS_BYTE_LIMIT,
    )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("candidate metrics is not one UTF-8 JSON document") from exc
    if type(value) is not dict:
        raise ValueError("candidate metrics root must be an object")
    return value, len(raw), hashlib.sha256(raw).hexdigest()


def _source_files(
    snapshot: RecoverySourceSnapshot,
) -> tuple[dict[str, SourceFileProof], dict[str, SourceFileProof]]:
    visual: dict[str, SourceFileProof] = {}
    relative: dict[str, SourceFileProof] = {}
    for proof in snapshot.source_manifest.files:
        folded_path = proof.relative_path.casefold()
        if folded_path in relative:
            raise ValueError("source manifest path union is ambiguous")
        relative[folded_path] = proof
        if proof.kind == "visual-source":
            folded_identity = proof.file_identity.casefold()
            if folded_identity in visual:
                raise ValueError("visual source identity union is ambiguous")
            visual[folded_identity] = proof
    return visual, relative


def _source_for_reference(
    reference: QcReference, root: Path, by_relative: Mapping[str, SourceFileProof],
) -> SourceFileProof:
    relative = _relative(root, reference.source_path, "visual occurrence source")
    proof = by_relative.get(relative.casefold())
    if proof is None or proof.kind != "visual-source":
        raise ValueError("visual occurrence has no authoritative SourceFileProof")
    return proof


def _normalized_graph_identity(value: str) -> str:
    path = PurePosixPath(value)
    stem = re.sub(r"_opt$", "", path.stem, flags=re.IGNORECASE)
    return path.with_name(stem + path.suffix.casefold()).as_posix()


def _graph_occurrence_union(
    graph: QcGraph, by_relative: Mapping[str, SourceFileProof], *, normalize_optimized: bool,
) -> tuple[tuple[str, str, int, str], ...]:
    root = Path(graph.family_root)
    rows = []
    for reference in graph.references:
        if reference.role != "visual":
            continue
        proof = _source_for_reference(reference, root, by_relative)
        graph_path = _relative(root, reference.graph_file, "visual occurrence graph")
        if normalize_optimized:
            graph_path = _normalized_graph_identity(graph_path)
        rows.append((graph_path, reference.directive, reference.line, proof.file_identity))
    rows.sort(key=lambda item: (item[0].casefold(), item[0], item[1], item[2], item[3].casefold(), item[3]))
    if not rows or len(rows) > _QC_GRAPH_OCCURRENCE_LIMIT or len(set(rows)) != len(rows):
        raise ValueError("visual occurrence union is empty, duplicated, or exceeds bound")
    return tuple(rows)


def _provenance_occurrence_union(
    payload: Mapping[str, object], original_by_relative: Mapping[str, SourceFileProof],
) -> tuple[tuple[str, str, int, str], ...]:
    raw_rows = payload.get("provenance")
    if type(raw_rows) is not list:
        raise ValueError("candidate metrics provenance is invalid")
    rows = []
    for raw in raw_rows:
        if type(raw) is not dict or raw.get("role") != "visual":
            continue
        graph_path = _logical_path(raw.get("graph_file"), "metrics provenance graph")
        graph_path = require_canonical_relative(graph_path, "metrics provenance graph")
        directive = raw.get("directive")
        line = raw.get("line")
        logical_path = _logical_path(raw.get("logical_path"), "metrics provenance logical path")
        if type(directive) is not str or not directive or type(line) is not int or line < 1:
            raise ValueError("candidate metrics visual provenance is invalid")
        graph_parent = PurePosixPath(graph_path).parent
        unresolved = graph_parent / PurePosixPath(logical_path)
        parts: list[str] = []
        for part in unresolved.parts:
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    raise ValueError("candidate metrics provenance escapes source root")
                parts.pop()
            else:
                parts.append(part)
        relative = PurePosixPath(*parts).as_posix()
        proof = original_by_relative.get(relative.casefold())
        if proof is None or proof.kind != "visual-source":
            raise ValueError("candidate metrics provenance has no SourceFileProof")
        rows.append((graph_path, directive, line, proof.file_identity))
    rows.sort(key=lambda item: (item[0].casefold(), item[0], item[1], item[2], item[3].casefold(), item[3]))
    if len(set(rows)) != len(rows):
        raise ValueError("candidate metrics visual provenance is duplicated")
    return tuple(rows)


def _closed_preservation(raw: object) -> tuple[str, str, bool, str]:
    if type(raw) is not dict or set(raw) != _EXACT_PRESERVATION_FIELDS:
        raise ValueError("adaptive exact preservation schema1 fields are invalid")
    source_identity = require_canonical_relative(
        raw["source_identity"], "adaptive exact source identity",
    )
    discriminator = (raw["kind"], raw["preserved_exact"], raw["reason"])
    if type(raw["schema"]) is not int or raw["schema"] != 1 or discriminator not in _EXACT_PRESERVATION_MATRIX:
        raise ValueError("adaptive exact preservation is unauthorized or inconsistent")
    return source_identity, discriminator[0], discriminator[1], discriminator[2]


def _normalized_model(value: str) -> str:
    return value.replace("\\", "/").strip().strip('"').casefold()


def _sealed_qc_bytes(
    root: Path, proof: SourceFileProof, cancel_event: threading.Event | None,
) -> bytes:
    path = Path(root).joinpath(*PurePosixPath(proof.relative_path).parts)
    raw = _read_regular_no_follow(
        path, cancel_event, contained_root=root, max_bytes=_QC_GRAPH_BYTE_LIMIT,
    )
    if (len(raw), hashlib.sha256(raw).hexdigest()) != (proof.size, proof.sha256):
        raise ValueError("current QC bytes differ from sealed SourceFileProof")
    return raw


def _qc_model_name(raw: bytes) -> str | None:
    try:
        tokens = _lex(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ValueError("sealed root QC is not UTF-8") from exc
    found = []
    for index, token in enumerate(tokens):
        if token.value.casefold() != "$modelname":
            continue
        values = []
        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor].kind not in {"newline", "brace"}:
            values.append(tokens[cursor].value)
            cursor += 1
        if len(values) != 1:
            raise ValueError("sealed root QC modelname is invalid")
        found.append(values[0])
    if len(found) > 1:
        raise ValueError("sealed root QC modelname is ambiguous")
    return found[0] if found else None


def _bind_graph_payload_to_manifest(
    payload: Mapping[str, object], source_manifest: SourceTreeManifest,
) -> None:
    expected = {
        item.relative_path: (item.size, item.sha256)
        for item in source_manifest.files if item.kind == "qc"
    }
    files = payload.get("files")
    if type(files) is not list:
        raise ValueError("QC graph payload files are invalid")
    actual = {}
    for item in files:
        if type(item) is not dict:
            raise ValueError("QC graph file payload is invalid")
        path = item.get("path")
        if type(path) is not str or path in actual:
            raise ValueError("QC graph file membership is not canonical")
        actual[path] = (item.get("size"), item.get("sha256"))
    if actual != expected or payload.get("root_qc") not in expected:
        raise ValueError("QC graph membership or bytes differ from sealed source manifest")


def _root_graph(
    root: Path, model_rel: str, *, optimized: bool,
    source_manifest: SourceTreeManifest,
    cancel_event: threading.Event | None,
) -> QcGraph:
    root = Path(root)
    candidates = []
    for proof in source_manifest.files:
        relative = PurePosixPath(proof.relative_path)
        if (
            proof.kind != "qc" or relative.suffix.casefold() != ".qc"
            or relative.stem.casefold().endswith("_opt") != optimized
        ):
            continue
        raw = _sealed_qc_bytes(root, proof, cancel_event)
        model_name = _qc_model_name(raw)
        if (
            model_name is not None
            and _normalized_model(model_name) == _normalized_model(model_rel)
        ):
            candidates.append(proof)
    if len(candidates) != 1:
        raise ValueError("authoritative sealed root QC is missing or ambiguous")
    selected = candidates[0]
    path = root.joinpath(*PurePosixPath(selected.relative_path).parts)
    graph = parse_qc_graph(path, root)
    payload = canonical_qc_graph_payload(graph, cancel_event)
    _bind_graph_payload_to_manifest(payload, source_manifest)
    if payload["root_qc"] != selected.relative_path:
        raise ValueError("parsed root QC differs from sealed root proof")
    return graph


def _bound_graph_digest(
    graph: QcGraph, source_manifest: SourceTreeManifest,
    cancel_event: threading.Event | None,
) -> str:
    before = canonical_qc_graph_payload(graph, cancel_event)
    _bind_graph_payload_to_manifest(before, source_manifest)
    digest = hashlib.sha256(canonical_json(before).encode("utf-8")).hexdigest()
    after = canonical_qc_graph_payload(graph, cancel_event)
    _bind_graph_payload_to_manifest(after, source_manifest)
    if after != before:
        raise ValueError("QC graph changed while binding its digest")
    return digest


def build_production_adaptive_candidate_metrics_proof(
    *,
    manifest: FamilyManifest,
    spec: CandidateSpec,
    candidate_cache_digest: str,
    original_snapshot: RecoverySourceSnapshot,
    candidate_snapshot: RecoverySourceSnapshot,
    candidate_metrics_path: Path,
    cancel_event: threading.Event | None = None,
) -> AdaptiveCandidateMetricsProof:
    """Fail-closed factory from current production artifacts into the sealed domain proof."""
    if not isinstance(manifest, FamilyManifest) or not isinstance(spec, CandidateSpec):
        raise TypeError("adaptive metrics production identity is invalid")
    if spec.strategy != "blender-adaptive-v1":
        raise ValueError("adaptive metrics factory requires blender-adaptive-v1")
    if not isinstance(original_snapshot, RecoverySourceSnapshot) or not isinstance(candidate_snapshot, RecoverySourceSnapshot):
        raise TypeError("adaptive metrics factory requires typed recovery snapshots")
    if original_snapshot.kind != "original" or candidate_snapshot.kind != "candidate":
        raise ValueError("adaptive metrics snapshot roles are invalid")
    if Path(os.path.abspath(manifest.source_dir)) != Path(os.path.abspath(original_snapshot.source_root)):
        raise ValueError("original snapshot root differs from family manifest")
    expected_contract = optimizer_contract_sha256(spec)
    for snapshot in (original_snapshot, candidate_snapshot):
        if (
            snapshot.family_id != manifest.family_id
            or snapshot.family_input_sha256 != manifest.input_hash
            or snapshot.optimizer_contract_sha256 != expected_contract
        ):
            raise ValueError("adaptive metrics snapshot family/spec binding differs")
    if (
        original_snapshot.whole_profile_sha256 != candidate_snapshot.whole_profile_sha256
        or original_snapshot.focused_profile_sha256 != candidate_snapshot.focused_profile_sha256
        or original_snapshot.dependency_proof_sha256 != candidate_snapshot.dependency_proof_sha256
        or candidate_snapshot.candidate_id != spec.candidate_id
        or candidate_snapshot.candidate_cache_digest != candidate_cache_digest
    ):
        raise ValueError("adaptive metrics snapshot provenance binding differs")

    revalidate_recovery_snapshot(original_snapshot, cancel_event)
    revalidate_recovery_snapshot(candidate_snapshot, cancel_event)
    original_graph = _root_graph(
        Path(original_snapshot.source_root), manifest.model_rel, optimized=False,
        source_manifest=original_snapshot.source_manifest, cancel_event=cancel_event,
    )
    candidate_graph = _root_graph(
        Path(candidate_snapshot.source_root), manifest.model_rel, optimized=True,
        source_manifest=candidate_snapshot.source_manifest, cancel_event=cancel_event,
    )
    original_graph_digest = _bound_graph_digest(
        original_graph, original_snapshot.source_manifest, cancel_event,
    )
    candidate_graph_digest = _bound_graph_digest(
        candidate_graph, candidate_snapshot.source_manifest, cancel_event,
    )

    original_visual, original_relative = _source_files(original_snapshot)
    candidate_visual, candidate_relative = _source_files(candidate_snapshot)
    if not original_visual or set(original_visual) != set(candidate_visual):
        raise ValueError("original/candidate visual SourceFileProof union differs")
    original_occurrences = _graph_occurrence_union(
        original_graph, original_relative, normalize_optimized=False,
    )
    candidate_occurrences = _graph_occurrence_union(
        candidate_graph, candidate_relative, normalize_optimized=True,
    )
    if original_occurrences != candidate_occurrences:
        raise ValueError("original/candidate complete visual occurrence union differs")

    metrics_relative = _relative(
        Path(candidate_snapshot.source_root), Path(candidate_metrics_path),
        "candidate metrics path",
    )
    if metrics_relative != "candidate_metrics.json":
        raise ValueError("candidate metrics path is not canonical candidate_metrics.json")
    metrics_file = candidate_relative.get(metrics_relative.casefold())
    if (
        metrics_file is None
        or metrics_file.kind != "auxiliary"
        or metrics_file.file_identity != "auxiliary/candidate_metrics.json"
        or metrics_file.relative_path != "candidate_metrics.json"
    ):
        raise ValueError("candidate metrics has no authoritative SourceFileProof")
    payload, metrics_size, metrics_digest = load_candidate_metrics_json(
        candidate_metrics_path, candidate_snapshot.source_root, cancel_event,
    )
    if (metrics_size, metrics_digest) != (metrics_file.size, metrics_file.sha256):
        raise ValueError("candidate metrics current bytes differ from SourceFileProof")
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
        or payload.get("candidate_id") != spec.candidate_id
        or payload.get("engine") != "blender"
        or payload.get("strategy") != spec.strategy
    ):
        raise ValueError("candidate metrics document identity differs")
    if _provenance_occurrence_union(payload, original_relative) != original_occurrences:
        raise ValueError("candidate metrics provenance is not the complete visual occurrence union")

    raw_files = payload.get("files")
    if type(raw_files) is not list:
        raise ValueError("candidate metrics file records are invalid")
    preservation_by_identity: dict[str, tuple[str, bool, str]] = {}
    for raw in raw_files:
        if type(raw) is not dict:
            raise ValueError("candidate metrics file record is invalid")
        identity, kind, preserved, reason = _closed_preservation(
            raw.get("adaptive_exact_preservation")
        )
        folded = identity.casefold()
        if folded in preservation_by_identity:
            raise ValueError("candidate metrics preservation source is duplicated")
        preservation_by_identity[folded] = (kind, preserved, reason)
    if set(preservation_by_identity) != set(original_visual):
        raise ValueError("candidate metrics preservation union is incomplete or divergent")

    occurrence_objects: dict[str, list[AdaptiveGraphOccurrenceProof]] = {
        identity: [] for identity in original_visual
    }
    for graph_path, directive, line, identity in original_occurrences:
        occurrence_objects[identity.casefold()].append(AdaptiveGraphOccurrenceProof(
            graph_path, directive, line, identity, "visual",
        ))
    sources = []
    for folded_identity in sorted(original_visual):
        source = original_visual[folded_identity]
        output = candidate_visual[folded_identity]
        kind, _preserved, reason = preservation_by_identity[folded_identity]
        common = dict(
            source_identity=source.file_identity,
            source_relative_path=source.relative_path,
            source_size=source.size, source_sha256=source.sha256,
            output_relative_path=output.relative_path,
            output_size=output.size, output_sha256=output.sha256,
            occurrences=tuple(occurrence_objects[folded_identity]),
        )
        if kind == "eligible-exact-v1":
            sources.append(EligibleAdaptiveSourceProof.create(
                **common, eligibility_reason=reason,
            ))
        elif kind == "ineligible-changed-v1":
            sources.append(IneligibleAdaptiveSourceProof.create(
                **common, ineligibility_reason=reason,
            ))
        else:  # The closed parser makes this unreachable; keep the boundary fail-closed.
            raise ValueError("adaptive exact preservation kind is unauthorized")

    proof = build_adaptive_candidate_metrics_proof(
        family_id=manifest.family_id,
        family_input_sha256=manifest.input_hash,
        candidate_id=spec.candidate_id,
        candidate_cache_digest=candidate_cache_digest,
        base_spec_sha256=candidate_spec_sha256(spec),
        source_manifest_sha256=candidate_snapshot.source_manifest.digest,
        source_snapshot_sha256=candidate_snapshot.snapshot_sha256,
        original_graph_sha256=original_graph_digest,
        candidate_graph_sha256=candidate_graph_digest,
        raw_metrics_sha256=metrics_file.sha256,
        sources=tuple(sources),
    )
    # Close the audit window: every path/hash above came from these manifests,
    # so a concurrent mutation anywhere in either source tree invalidates production.
    revalidate_recovery_snapshot(original_snapshot, cancel_event)
    revalidate_recovery_snapshot(candidate_snapshot, cancel_event)
    return proof
