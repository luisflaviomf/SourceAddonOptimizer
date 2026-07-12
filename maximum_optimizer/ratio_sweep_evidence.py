from __future__ import annotations

import hashlib
import json
import re


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_FAMILIES = ("dodge_charger", "toyota_supra", "nissan_skyline_gtr32")
_RATIOS = (0.35, 0.30, 0.25)
_RUN_BASE_COMMIT = "b9bf711c5706d057d895defb4d967cfdd91d53e2"
_EXECUTION_SNAPSHOT_COMMIT = "47548fd255d43027ec8ddd73c9bc72ddc777b23f"
_RUNTIME_SCRIPTS = {
    "batch_compile_opt_qc.py": {"git_blob_sha1": "39bb95630a1fbb6754687ee05636bc77d3fe7626", "checkout_sha256": "9ae7a78878fd281de99cd3476ce8d1fc8e073b20e8f4897a9df36dc97c3215cc", "checkout_eol": "crlf"},
    "batch_optimize_maximum.py": {"git_blob_sha1": "7989d38ef77900f6742b3f01df70f98753712aff", "checkout_sha256": "76cd2f9ca769d92e49fd66b1f8e383adb4492208a50a231147cf9a5fbb5e8098", "checkout_eol": "lf"},
    "maximum_optimizer/compiler_aware.py": {"git_blob_sha1": "0a88926c804c8244b7722b3a5c8024e74e32577a", "checkout_sha256": "186c4d7cd49f8085dd3741c9d4221cc5daa7e8b80ca7c83d7d4d0ee0d6c45b13", "checkout_eol": "lf"},
    "maximum_optimizer/qc_graph.py": {"git_blob_sha1": "342df930c3fa42b710597998bc099b18b6f3df97", "checkout_sha256": "625d64ef8b78f49ce1ab42a979b9faa781b2feefcc9722c8665d074a094b8260", "checkout_eol": "lf"},
    "maximum_optimizer/qc_inventory.py": {"git_blob_sha1": "2022c336dfaf71e8f1ab7198f56f228c40d26158", "checkout_sha256": "ca4cb526add762bb7b6b56a308f5aee37b06ec045b9a2e5b1be028d32952b057", "checkout_eol": "lf"},
}
_INPUTS = {
    "benchmarks/lvs_models/blender_adaptive_v1.json": "d246a382db92c6f19e49e170f6cf56eddfb72c93834bd73511eb6c365a3779ec"
}
_TOOLING_PATHS = {
    "benchmarks/lvs_models/build_adaptive_ratio_sweep_v1.py",
    "benchmarks/lvs_models/run_adaptive_ratio_sweep.py",
    "maximum_optimizer/ratio_sweep_evidence.py",
}
_FINGERPRINT_FIELDS = {
    "model_name", "bodygroups", "materials", "skin_families", "bones",
    "bone_parents", "attachments", "hitboxes", "sequences", "mesh_files",
    "lod_mesh_files", "physics_mesh",
}


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _keys(value: object, expected: set[str], context: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{context} fields are invalid")
    return value


def _portable_path(value: object) -> bool:
    return (
        type(value) is str and bool(value) and "\\" not in value
        and not value.startswith("/") and ":" not in value and ".." not in value.split("/")
    )


def _artifacts(value: object, context: str, *, require_source_sidecars: bool = False) -> tuple[dict, ...]:
    if type(value) is not list or not value:
        raise ValueError(f"{context} artifacts are invalid")
    result = []
    seen = set()
    for raw in value:
        item = _keys(raw, {"path", "size_bytes", "sha256"}, context)
        path = item["path"]
        if not _portable_path(path) or path in seen:
            raise ValueError(f"{context} artifact path is not portable")
        if type(item["size_bytes"]) is not int or item["size_bytes"] <= 0:
            raise ValueError(f"{context} artifact size is invalid")
        if type(item["sha256"]) is not str or _SHA256.fullmatch(item["sha256"]) is None:
            raise ValueError(f"{context} artifact hash is invalid")
        seen.add(path)
        result.append(item)
    if [item["path"] for item in result] != sorted(seen):
        raise ValueError(f"{context} artifacts must be sorted")
    if require_source_sidecars:
        mdl = [path for path in seen if path.endswith(".mdl")]
        if len(mdl) != 1:
            raise ValueError("candidate must contain exactly one MDL family")
        stem = mdl[0][:-4]
        expected = {
            f"{stem}.mdl", f"{stem}.vvd", f"{stem}.dx80.vtx",
            f"{stem}.dx90.vtx", f"{stem}.phy",
        }
        if seen != expected:
            raise ValueError("candidate Source sidecars are incomplete")
    return tuple(result)


def _lane(value: object, context: str, *, require_source_sidecars: bool = False) -> tuple[dict, ...]:
    lane = _keys(value, {"total_bytes", "artifacts"}, context)
    artifacts = _artifacts(lane["artifacts"], context, require_source_sidecars=require_source_sidecars)
    if lane["total_bytes"] != sum(item["size_bytes"] for item in artifacts):
        raise ValueError(f"{context} total does not match artifacts")
    return artifacts


def _validate_provenance(value: object) -> None:
    provenance = _keys(
        value,
        {"run_base_commit", "execution_snapshot", "evidence_tooling", "inputs"},
        "provenance",
    )
    if provenance["run_base_commit"] != _RUN_BASE_COMMIT:
        raise ValueError("run base commit is invalid")
    snapshot = _keys(
        provenance["execution_snapshot"], {"snapshot_commit", "runtime_scripts"},
        "execution snapshot",
    )
    if snapshot["snapshot_commit"] != _EXECUTION_SNAPSHOT_COMMIT:
        raise ValueError("execution snapshot commit is invalid")
    scripts = snapshot["runtime_scripts"]
    if type(scripts) is not dict or not scripts:
        raise ValueError("runtime script provenance is invalid")
    for path, raw in scripts.items():
        item = _keys(raw, {"git_blob_sha1", "checkout_sha256", "checkout_eol"}, "runtime script")
        if (
            not _portable_path(path)
            or _SHA1.fullmatch(str(item["git_blob_sha1"])) is None
            or _SHA256.fullmatch(str(item["checkout_sha256"])) is None
            or item["checkout_eol"] not in {"lf", "crlf"}
        ):
            raise ValueError("runtime script provenance is invalid")
    if scripts != _RUNTIME_SCRIPTS:
        raise ValueError("runtime script snapshot does not match the frozen execution")
    for name in ("evidence_tooling", "inputs"):
        values = provenance[name]
        if type(values) is not dict or not values or any(
            not _portable_path(path) or _SHA256.fullmatch(str(digest)) is None
            for path, digest in values.items()
        ):
            raise ValueError(f"{name} provenance is invalid")
    if set(provenance["evidence_tooling"]) != _TOOLING_PATHS:
        raise ValueError("evidence tooling is incomplete")
    if provenance["inputs"] != _INPUTS:
        raise ValueError("frozen evidence inputs changed")


def _validate_qc(value: object) -> None:
    qc = _keys(value, {"status", "differing_fields", "source", "optimized"}, "qc")
    if qc["status"] != "pass" or qc["differing_fields"] != ["mesh_files"]:
        raise ValueError("QC fingerprint did not pass")
    fingerprints = {}
    for label in ("source", "optimized"):
        item = _keys(qc[label], {"path", "sha256", "fingerprint", "fingerprint_sha256"}, f"qc {label}")
        if (
            not _portable_path(item["path"])
            or _SHA256.fullmatch(str(item["sha256"])) is None
            or _SHA256.fullmatch(str(item["fingerprint_sha256"])) is None
            or type(item["fingerprint"]) is not dict
            or set(item["fingerprint"]) != _FINGERPRINT_FIELDS
            or hashlib.sha256(_canonical(item["fingerprint"])).hexdigest() != item["fingerprint_sha256"]
        ):
            raise ValueError("QC fingerprint evidence is invalid")
        fingerprints[label] = item["fingerprint"]
    differing = [
        field for field in sorted(_FINGERPRINT_FIELDS)
        if fingerprints["source"][field] != fingerprints["optimized"][field]
    ]
    if differing != ["mesh_files"]:
        raise ValueError("QC fingerprints do not match the disclosed delta")


def _validate_external_files(value: object) -> None:
    files = _keys(
        value,
        {"candidate_metrics", "candidate_payload", "compile_log", "compile_summary"},
        "attempt provenance files",
    )
    for item_raw in files.values():
        item = _keys(item_raw, {"sha256", "availability"}, "attempt provenance file")
        if (
            _SHA256.fullmatch(str(item["sha256"])) is None
            or item["availability"] != "external-not-committed"
        ):
            raise ValueError("attempt provenance file is invalid")


def _pareto_ratios(attempts: list[dict]) -> list[float]:
    result = []
    for candidate in attempts:
        axes = (
            candidate["candidate"]["total_bytes"],
            candidate["fallback_summary"]["count"],
            candidate["fallback_summary"]["bytes"],
        )
        dominated = False
        for other in attempts:
            if other is candidate:
                continue
            other_axes = (
                other["candidate"]["total_bytes"],
                other["fallback_summary"]["count"],
                other["fallback_summary"]["bytes"],
            )
            if all(a <= b for a, b in zip(other_axes, axes)) and any(
                a < b for a, b in zip(other_axes, axes)
            ):
                dominated = True
                break
        if not dominated:
            result.append(float(candidate["ratio"]))
    return result


def parse_ratio_sweep_evidence(raw: object) -> dict:
    root = _keys(
        raw,
        {
            "schema_version", "strategy", "provenance", "toolchain", "excluded_runs",
            "families", "quality", "evidence_sha256",
        },
        "evidence",
    )
    if root["schema_version"] != 2 or root["strategy"] != "blender-adaptive-ratio-sweep-v1":
        raise ValueError("unsupported ratio sweep evidence")
    _validate_provenance(root["provenance"])
    tools = _keys(root["toolchain"], {"blender", "studiomdl"}, "toolchain")
    for raw_tool in tools.values():
        tool = _keys(raw_tool, {"version", "sha256"}, "tool")
        if type(tool["version"]) is not str or not tool["version"] or _SHA256.fullmatch(str(tool["sha256"])) is None:
            raise ValueError("tool provenance is invalid")
    if type(root["excluded_runs"]) is not list or len(root["excluded_runs"]) != 1:
        raise ValueError("excluded run ledger is invalid")
    excluded = _keys(
        root["excluded_runs"][0],
        {
            "run_id", "path", "reason", "availability", "available_hashes",
            "path_reused_after_clean", "replacement_run_id", "contamination_proof",
        },
        "excluded run",
    )
    if (
        not _portable_path(excluded["run_id"]) or not _portable_path(excluded["path"])
        or type(excluded["reason"]) is not str or not excluded["reason"]
        or excluded["availability"] != "deleted-before-valid-rerun-no-artifacts-retained"
        or excluded["available_hashes"] != {}
        or excluded["path_reused_after_clean"] is not True
        or not _portable_path(excluded["replacement_run_id"])
        or excluded["contamination_proof"] != {
            "candidate_metrics_required": True,
            "exactly_one_optimized_qc_required": True,
            "one_successful_compile_record_required": True,
            "workspace_recreated_after_recursive_delete": True,
        }
    ):
        raise ValueError("excluded run ledger is not auditable")
    if type(root["families"]) is not list or len(root["families"]) != len(_FAMILIES):
        raise ValueError("ratio sweep families are invalid")
    family_ids = []
    valid_run_ids = []
    for family_raw in root["families"]:
        family = _keys(family_raw, {"family_id", "baselines", "attempts", "decision"}, "family")
        family_ids.append(family["family_id"])
        baselines = _keys(family["baselines"], {"original", "b050", "r040"}, "baselines")
        for name, lane in baselines.items():
            _lane(lane, f"baseline {name}")
        if type(family["attempts"]) is not list or len(family["attempts"]) != 3:
            raise ValueError("each family must contain the three planned attempts")
        totals = {}
        for expected_ratio, attempt_raw in zip(_RATIOS, family["attempts"]):
            attempt = _keys(
                attempt_raw,
                {
                    "run_id", "ratio", "candidate", "triangles", "fallback_summary",
                    "fallbacks", "qc", "provenance_files",
                },
                "attempt",
            )
            if not _portable_path(attempt["run_id"]) or attempt["run_id"] in valid_run_ids:
                raise ValueError("attempt run id is invalid")
            valid_run_ids.append(attempt["run_id"])
            if type(attempt["ratio"]) not in (int, float) or attempt["ratio"] != expected_ratio:
                raise ValueError("attempt ratios/order changed")
            _lane(attempt["candidate"], "candidate", require_source_sidecars=True)
            totals[float(attempt["ratio"])] = attempt["candidate"]["total_bytes"]
            triangles = _keys(attempt["triangles"], {"before", "after"}, "triangles")
            if (
                type(triangles["before"]) is not int or type(triangles["after"]) is not int
                or not 0 < triangles["after"] < triangles["before"]
            ):
                raise ValueError("triangle evidence is invalid")
            _validate_external_files(attempt["provenance_files"])
            if type(attempt["fallbacks"]) is not list:
                raise ValueError("fallbacks must be an array")
            fallback_bytes = 0
            fallback_paths = []
            for fallback_raw in attempt["fallbacks"]:
                fallback = _keys(
                    fallback_raw,
                    {"source", "reason", "size_bytes", "source_sha256", "output_sha256"},
                    "fallback",
                )
                path = fallback["source"]
                if (
                    not _portable_path(path) or type(fallback["reason"]) is not str
                    or not fallback["reason"] or type(fallback["size_bytes"]) is not int
                    or fallback["size_bytes"] <= 0
                    or fallback["source_sha256"] != fallback["output_sha256"]
                    or _SHA256.fullmatch(str(fallback["source_sha256"])) is None
                ):
                    raise ValueError("fallback is not byte-exact portable evidence")
                fallback_paths.append(path)
                fallback_bytes += fallback["size_bytes"]
            if fallback_paths != sorted(fallback_paths) or len(set(fallback_paths)) != len(fallback_paths):
                raise ValueError("fallbacks must be uniquely sorted")
            summary = _keys(attempt["fallback_summary"], {"count", "bytes"}, "fallback summary")
            if summary != {"count": len(attempt["fallbacks"]), "bytes": fallback_bytes}:
                raise ValueError("fallback summary is inconsistent")
            _validate_qc(attempt["qc"])
        decision = _keys(
            family["decision"],
            {"minimum_bytes_ratio", "minimum_bytes", "sampled_pareto_ratios", "reason"},
            "decision",
        )
        best_ratio, best_bytes = min(totals.items(), key=lambda item: (item[1], item[0]))
        if (
            decision["minimum_bytes_ratio"] != best_ratio
            or decision["minimum_bytes"] != best_bytes
            or decision["sampled_pareto_ratios"] != _pareto_ratios(family["attempts"])
            or type(decision["reason"]) is not str or not decision["reason"]
        ):
            raise ValueError("decision does not match sampled byte/fallback evidence")
    if tuple(family_ids) != _FAMILIES:
        raise ValueError("ratio sweep family order changed")
    if excluded["run_id"] in valid_run_ids or excluded["replacement_run_id"] not in valid_run_ids:
        raise ValueError("excluded run contaminated the valid attempt ledger")
    quality = _keys(root["quality"], {"status", "visual_gate", "reason"}, "quality")
    if (
        quality["status"] != "unverified" or quality["visual_gate"] != "separate-pending"
        or type(quality["reason"]) is not str or not quality["reason"]
    ):
        raise ValueError("ratio sweep cannot claim visual quality")
    digest = root["evidence_sha256"]
    if type(digest) is not str or _SHA256.fullmatch(digest) is None:
        raise ValueError("evidence digest is invalid")
    unsigned = dict(root)
    unsigned.pop("evidence_sha256")
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != digest:
        raise ValueError("evidence digest mismatch")
    return root
