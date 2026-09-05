"""Deterministic independent candidate export; never installs or edits inputs."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from .codecs import GENERAL_LIMIT, encode_options, verify_vendor
from .runtime import NAMESPACE, asset_header, contract_path, json_bytes, render_runtime, safe_target, support_alias
from _runtime_validation.high_impact_research.tacrp_compression_portability_v1 import source_gma
from _runtime_validation.high_impact_research.tacrp_compression_portability_v1.engine_overlap import scan_engine_overlaps

MARKER = f"data_static/{NAMESPACE}/manifest.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(a: Path, b: Path) -> bool:
    try:
        a.relative_to(b)
        return True
    except ValueError:
        return False


def _reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _checked_root(path: Path, *, existing: bool) -> Path:
    # Inspect spelling before normalization too: junction/../child must not hide it.
    lexical = Path(path).absolute()
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current /= part
        if _reparse(current):
            raise ValueError(f"reparse path refused: {current}")
    path = Path(os.path.abspath(lexical))
    for probe in (path, *path.parents):
        if _reparse(probe):
            raise ValueError(f"reparse path refused: {probe}")
    if existing and not path.is_dir():
        raise ValueError(f"directory missing: {path}")
    return path


def _inventory(source: Path) -> list[dict]:
    rows = []
    folded = {}
    def visit(folder):
        with os.scandir(folder) as children:
            paths = sorted((Path(entry.path) for entry in children), key=lambda p: p.name.encode("utf-8"))
        for path in paths:
            if _reparse(path):
                raise ValueError(f"reparse source refused: {path}")
            if path.is_dir():
                visit(path)
            elif path.is_file():
                relative = path.relative_to(source).as_posix()
                key = relative.casefold()
                if key in folded:
                    raise ValueError(f"case-fold collision: {folded[key]} and {relative}")
                folded[key] = relative
                raw = path.read_bytes()
                rows.append({"source_path": relative, "size": len(raw), "sha256": _sha(raw)})
            else:
                raise ValueError(f"non-regular source refused: {path}")
    visit(source)
    return sorted(rows, key=lambda row: row["source_path"].encode("utf-8"))


def _write(path: Path, raw: bytes) -> None:
    _checked_root(path.parent, existing=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if _reparse(path):
        raise ValueError(f"reparse output refused: {path}")
    with path.open("xb") as stream:
        stream.write(raw)


def _read_pinned(source: Path, row: dict) -> bytes:
    path = source / row["source_path"]
    _checked_root(path.parent, existing=True)
    if _reparse(path):
        raise ValueError("input drift: reparse file")
    raw = path.read_bytes()
    if (len(raw), _sha(raw)) != (row["size"], row["sha256"]):
        raise ValueError("input drift detected: " + row["source_path"])
    return raw


def _fingerprint(segments) -> dict:
    fingerprint = source_gma._ChunkFingerprint(source_gma.CHUNK_BYTES)
    for segment in segments:
        fingerprint.update(segment)
    size, digest, chunks = fingerprint.finish()
    return {"size": size, "sha256": digest, "chunk_size": source_gma.CHUNK_BYTES, "chunks": chunks}


def _family_artifacts(source, work, cid, folder, codec, selected):
    """Materialize only bounded support; reconstructed cache is fingerprinted."""
    family = f"{codec}_0000"
    entries = []
    mapping = {}
    expected = {}
    for row, target, option in selected:
        blob_path = f"data_static/{NAMESPACE}/{cid}/{option['blob_sha256']}.dat"
        physical = work / "blobs" / (option["blob_sha256"] + ".dat")
        if not physical.exists():
            _write(physical, option["blob"])
        entry = {key: value for key, value in option.items() if key not in ("blob", "family")}
        entry.update(source_path=row["source_path"], target_path=target, blob_path=blob_path)
        entries.append(entry)
        mapping[blob_path] = physical
        expected[blob_path] = {"size": option["blob_size"], "sha256": option["blob_sha256"]}
    entries.sort(key=lambda entry: entry["target_path"])
    sizes = {entry["target_path"]: entry["source_size"] for entry in entries}
    header = source_gma.gma_header(sizes) if codec in ("vvd", "vtf") else asset_header(sizes)
    ordered = sorted(selected, key=lambda item: item[1])
    def cache_segments():
        yield header
        for row, _, _ in ordered:
            yield _read_pinned(source, row)
        yield b"\0" * 4
    cache = _fingerprint(cache_segments())
    cache["header_hex"] = header.hex()
    contract = {"schema": "exact-distribution-runtime/v1", "family": family, "codec_family": codec,
                "shard": 0, "content_id": cid, "addon_folder": folder, "cache": cache,
                "entries": entries, "engine_preexisting": {}}
    relative = support_alias(contract)
    support = work / "artifacts" / relative
    support.parent.mkdir(parents=True, exist_ok=True)
    source_gma.build_gma_file(mapping, support)
    audit = source_gma.audit_gma_file(support, expected)
    with support.open("rb") as stream:
        identity = _fingerprint(iter(lambda: stream.read(source_gma.CHUNK_BYTES), b""))
    if identity["sha256"] != audit["sha256"]:
        raise ValueError("support fingerprint mismatch")
    contract.update(support=identity, support_path=f"addons/{folder}/{relative}")
    payload = json_bytes(contract)
    autorun = render_runtime(contract)
    artifacts = {relative: support,
                 contract_path(contract): payload,
                 f"lua/autorun/000000_exact_{cid}_{family}.lua": autorun}
    size = audit["size"] + len(payload) + len(autorun)
    return artifacts, size, contract


def build_candidate(source: Path, output: Path, work: Path, engine_root: Path, progress=None) -> dict:
    source = _checked_root(source, existing=True)
    engine_root = _checked_root(engine_root, existing=True)
    output = _checked_root(output, existing=False)
    work = _checked_root(work, existing=False)
    for a, b in ((output, source), (source, output), (work, source), (source, work), (output, work), (work, output)):
        if _inside(a, b) or _inside(a.resolve(), b.resolve()):
            raise ValueError("source/work/output overlap refused")
    for path in (output, work):
        if path.exists():
            raise FileExistsError(path)
        if _inside(path, engine_root) or _inside(engine_root, path):
            raise ValueError("engine/output/work overlap refused")
    if not re.fullmatch(r"[a-z0-9_-]+", output.name):
        raise ValueError("output addon folder must use lowercase ASCII letters/digits/underscore/hyphen")
    rows = _inventory(source)
    for row in rows:
        rel = row["source_path"].lower()
        if (rel.endswith((".gma", ".bin")) or rel.startswith((
                f"data_static/{NAMESPACE}/", "data_static/codex_tacrp_", "lua/codex_tacrp_"))
                or rel.startswith("lua/autorun/000000_exact_")):
            raise ValueError("prepacked runtime/archive refused: " + row["source_path"])
    pins = verify_vendor()
    excluded = [row for row in rows if row["source_path"].lower().endswith((".dx80.vtx", ".sw.vtx"))]
    excluded_paths = {row["source_path"] for row in excluded}
    decisions = {}
    candidates = []
    for index, row in enumerate(rows, 1):
        rel = row["source_path"]
        if progress:
            progress("inventory", index, len(rows), rel)
        target = rel.lower()
        if rel in excluded_paths:
            decisions[rel] = "excluded_distribution_policy"
        elif not safe_target(target):
            decisions[rel] = "native_unsafe_path"
        elif not 0 < row["size"] <= GENERAL_LIMIT:
            decisions[rel] = "native_size_cap"
        else:
            candidates.append({"target_path": target, "source_size": row["size"], "source_sha256": row["sha256"]})
    overlap = scan_engine_overlaps(engine_root, candidates)
    overlap_paths = {item["path"] for item in overlap["evidence"]}
    eligible = []
    for index, row in enumerate(rows, 1):
        rel = row["source_path"]
        if rel in decisions:
            continue
        target = rel.lower()
        if target in overlap_paths:
            decisions[rel] = "native_engine_overlap"
            continue
        if progress:
            progress("encode", index, len(rows), rel)
        raw = _read_pinned(source, row)
        options = encode_options(target, raw)  # Independent codec failures propagate.
        if not options:
            decisions[rel] = "native_unsupported_or_no_blob_gain"
            continue
        option = min(options, key=lambda item: (item["blob_size"], item["codec_domain"]))
        eligible.append((row, target, option))
        decisions[rel] = "native_family_overhead"
    producer = {path.name: _sha(path.read_bytes()) for path in (Path(__file__), Path(__file__).with_name("runtime.py"), Path(__file__).with_name("codecs.py"))}
    identity = {"schema": "exact-distribution-identity/v1", "source": rows, "addon_folder": output.name,
                "excluded": sorted(excluded_paths), "vendor": pins, "producer": producer,
                "options": [(target, option["codec_domain"], option["blob_sha256"]) for _, target, option in eligible]}
    cid = _sha(json_bytes(identity))
    work.mkdir(parents=True)
    artifacts = {}
    selected = []
    groups = {}
    for item in eligible:
        groups.setdefault(item[2]["family"], []).append(item)
    family_reports = []
    for codec, group in sorted(groups.items()):
        # A cap-bound remainder stays native; no arbitrary startup sharding.
        bounded = []
        total = 0
        for item in sorted(group, key=lambda item: item[1]):
            row, target, option = item
            if len(bounded) >= source_gma.MAX_ENTRIES or total + row["size"] > source_gma.MAX_PAYLOAD_BYTES:
                decisions[row["source_path"]] = "native_family_cap"
                continue
            bounded.append(item)
            total += row["size"]
        if not bounded:
            continue
        family_artifacts, cost, contract = _family_artifacts(source, work, cid, output.name, codec, bounded)
        accepted = cost < total
        family_reports.append({"family": contract["family"], "codec_family": codec, "native_bytes": total,
                               "transport_bytes": cost, "accepted": accepted})
        if accepted:
            artifacts.update(family_artifacts)
            selected.extend(bounded)
    source_bytes = sum(row["size"] for row in rows)
    excluded_bytes = sum(row["size"] for row in excluded)
    baseline = source_bytes - excluded_bytes
    manifest = json_bytes({"schema": "exact-distribution-candidate/v1", "content_id": cid,
                           "addon_folder": output.name, "encoded": len(selected)})
    artifact_size = sum(value.stat().st_size if isinstance(value, Path) else len(value) for value in artifacts.values())
    planned = baseline - sum(row["size"] for row, _, _ in selected) + artifact_size + (len(manifest) if selected else 0)
    fallback = None
    if selected and planned >= baseline:
        selected = []
        artifacts = {}
        planned = baseline
        fallback = "whole_candidate_no_net_gain"
        for family_report in family_reports:
            family_report["accepted"] = False
    if _inventory(source) != rows:
        raise ValueError("input drift detected before publication")
    _checked_root(output, existing=False)
    output.mkdir(parents=True, exist_ok=False)
    selected_paths = {row["source_path"] for row, _, _ in selected}
    for row in rows:
        rel = row["source_path"]
        if rel not in selected_paths and rel not in excluded_paths:
            _write(output / rel, _read_pinned(source, row))
    for relative, value in artifacts.items():
        _write(output / relative, value.read_bytes() if isinstance(value, Path) else value)
    if selected:
        _write(output / MARKER, manifest)
    actual = _inventory(output)
    actual_map = {row["source_path"]: row for row in actual}
    candidate_bytes = sum(row["size"] for row in actual)
    if candidate_bytes != planned:
        raise ValueError("candidate measured size mismatch")
    for row in rows:
        rel = row["source_path"]
        if rel in selected_paths or rel in excluded_paths:
            if rel in actual_map:
                raise ValueError("candidate retained excluded/encoded target")
        elif actual_map.get(rel) != row:
            raise ValueError("candidate native identity mismatch")
    expected_paths = {row["source_path"] for row in rows
                      if row["source_path"] not in selected_paths | excluded_paths}
    for relative, value in artifacts.items():
        raw = value.read_bytes() if isinstance(value, Path) else value
        if actual_map.get(relative) != {"source_path": relative, "size": len(raw), "sha256": _sha(raw)}:
            raise ValueError("candidate artifact identity mismatch: " + relative)
        expected_paths.add(relative)
    if selected:
        if actual_map.get(MARKER) != {"source_path": MARKER, "size": len(manifest), "sha256": _sha(manifest)}:
            raise ValueError("candidate artifact identity mismatch: manifest")
        expected_paths.add(MARKER)
    if set(actual_map) != expected_paths:
        raise ValueError("candidate closed set mismatch")
    if any(row["source_path"].lower().endswith((".dx80.vtx", ".sw.vtx")) for row in actual):
        raise ValueError("candidate legacy VTX gate")
    for path in output.rglob("*.gma"):
        audit = source_gma.audit_gma_file(path)
        if any(entry["path"].lower().endswith((".dx80.vtx", ".sw.vtx")) for entry in audit["entries"]):
            raise ValueError("GMA legacy VTX gate")
    if _inventory(source) != rows or verify_vendor() != pins or producer != {
            path.name: _sha(path.read_bytes()) for path in (Path(__file__), Path(__file__).with_name("runtime.py"), Path(__file__).with_name("codecs.py"))}:
        raise ValueError("input/producer drift detected at close")
    for rel in selected_paths:
        decisions[rel] = "encoded"
    report_path = work / "candidate-report.json"
    report = {"schema": "exact-distribution-report/v1", "status": "OFFLINE_PENDING_GMOD", "candidate": str(output),
              "report_path": str(report_path), "content_id": cid, "source_bytes": source_bytes,
              "candidate_bytes": candidate_bytes, "distribution_baseline_bytes": baseline,
              "codec_net_savings_bytes": baseline - candidate_bytes, "encoded_count": len(selected),
              "native_count": len(rows) - len(excluded) - len(selected),
              "unique_blob_count": len({option["blob_sha256"] for _, _, option in selected}),
              "excluded": {"count": len(excluded), "bytes": excluded_bytes, "paths": [row["source_path"] for row in excluded]},
              "fallback_reason": fallback, "families": family_reports, "byte_exact": True,
              "new_quality_loss": False, "installed": False,
              "dx90_coverage": {"source": sum(row["source_path"].lower().endswith(".dx90.vtx") for row in rows),
                                "candidate_native_or_reconstructed": sum(row["source_path"].lower().endswith(".dx90.vtx") for row in actual)
                                + sum(target.endswith(".dx90.vtx") for _, target, _ in selected)}}
    _write(work / "input-pins.json", json_bytes(identity))
    _write(work / "engine-overlap.json", json_bytes(overlap))
    _write(work / "decisions.json", json_bytes({"entries": [dict(
        row, target_path=row["source_path"].lower() if row["source_path"] in selected_paths else row["source_path"],
        decision=decisions[row["source_path"]]) for row in rows]}))
    _write(report_path, json_bytes(report))
    return report
