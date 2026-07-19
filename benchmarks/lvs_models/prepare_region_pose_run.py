from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from maximum_optimizer.animation_pose_selector import select_animation_pose
from maximum_optimizer.qc_graph import parse_qc_graph
from maximum_optimizer.region_animation_catalog import (
    RegionAnimationCatalogCaps,
    build_region_animation_catalog,
)
from maximum_optimizer.region_pose_producer import (
    build_region_pose_caps,
    build_region_pose_contracts,
)
from maximum_optimizer.region_pose_render_request import (
    build_region_pose_render_request,
)
from maximum_optimizer.regions import (
    filter_region_manifest,
    load_region_manifest_payload,
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_proof(path: Path, root: Path) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    relative = resolved.relative_to(root.resolve(strict=True)).as_posix()
    payload = resolved.read_bytes()
    return {
        "path": relative,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_new(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(_canonical_bytes(payload))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--qc", type=Path, required=True)
    parser.add_argument("--region-manifest", type=Path, required=True)
    parser.add_argument("--source-identity", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--family-id", required=True)
    parser.add_argument("--toolchain-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    source_root = args.source_root.resolve(strict=True)
    qc = args.qc.resolve(strict=True)
    qc.relative_to(source_root)
    manifest_path = args.region_manifest.resolve(strict=True)
    full_manifest = load_region_manifest_payload(json.loads(
        manifest_path.read_text(encoding="utf-8")
    ))
    focused_manifest = filter_region_manifest(full_manifest, (args.source_identity,))
    if len(focused_manifest.entries) != 1:
        raise ValueError("region pose preparation requires exactly one source region")
    focused_payload = focused_manifest.to_payload()
    region_bytes = _canonical_bytes(focused_payload)
    region_manifest_sha256 = hashlib.sha256(region_bytes).hexdigest()
    graph = parse_qc_graph(qc, source_root)
    family_input_sha256 = hashlib.sha256(qc.read_bytes()).hexdigest()
    source_geometry = (source_root / args.source_identity).resolve(strict=True)
    source_geometry.relative_to(source_root)
    source_snapshot = {
        "kind": "source-only-focused-region-snapshot-v1",
        "family_id": args.family_id,
        "root_qc": _file_proof(qc, source_root),
        "qc_graph": [_file_proof(item.path, source_root) for item in graph.files],
        "region_manifest_sha256": region_manifest_sha256,
        "geometry": _file_proof(source_geometry, source_root),
    }
    source_snapshot_sha256 = _hash(source_snapshot)
    contracts = build_region_pose_contracts(args.toolchain_sha256)
    caps = build_region_pose_caps()
    catalog = build_region_animation_catalog(
        graph,
        focused_manifest,
        source_root=source_root,
        family_id=args.family_id,
        family_input_sha256=family_input_sha256,
        source_snapshot_sha256=source_snapshot_sha256,
        region_manifest_sha256=region_manifest_sha256,
        contracts_sha256=contracts["contracts_sha256"],
        selector=select_animation_pose,
        caps=RegionAnimationCatalogCaps(),
    ).to_payload()
    entry = catalog["regions"][0]
    if entry["source_identity"] != args.source_identity:
        raise ValueError("catalog source identity differs from focused manifest")
    if entry["mode"] != "bind-animation" or not isinstance(entry["selection_payload"], dict):
        raise ValueError("focused region has no authoritative animated pose")
    request = build_region_pose_render_request(
        family_id=args.family_id,
        family_input_sha256=family_input_sha256,
        source_snapshot_sha256=source_snapshot_sha256,
        catalog_sha256=catalog["catalog_sha256"],
        region_key=entry["region_key"],
        region_manifest_sha256=region_manifest_sha256,
        source_identity=args.source_identity,
        contracts_sha256=contracts["contracts_sha256"],
        caps_sha256=caps["caps_sha256"],
        selection_payload=entry["selection_payload"],
    ).to_payload()

    candidate = args.candidate.resolve(strict=True)
    configuration = {
        "schema": 1,
        "name": f"region-pose-calibration:{args.family_id}:{args.source_identity}",
        "bodygroups": {},
        "lod_index": 0,
        "source_pairs": [{
            "source_identity": args.source_identity,
            "reference_sha256": hashlib.sha256(source_geometry.read_bytes()).hexdigest(),
            "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        }],
    }
    out = args.out.absolute()
    if out.exists():
        raise ValueError("region pose preparation output already exists")
    out.mkdir(parents=True)
    _write_new(out / "region.json", focused_payload)
    _write_new(out / "source_snapshot.json", {
        **source_snapshot, "snapshot_sha256": source_snapshot_sha256,
    })
    _write_new(out / "contracts.json", contracts)
    _write_new(out / "caps.json", caps)
    _write_new(out / "catalog.json", catalog)
    _write_new(out / "request.json", request)
    _write_new(out / "configuration.json", configuration)
    _write_new(out / "summary.json", {
        "schema": 1,
        "kind": "prepared-source-only-region-pose-calibration-v1",
        "family_id": args.family_id,
        "source_root": str(source_root),
        "source": str(source_geometry),
        "candidate": str(candidate),
        "animation": str((source_root / entry["selection_payload"]["animation_relative_path"]).resolve(strict=True)),
        "frame": entry["selection_payload"]["frame"],
        "region_key": entry["region_key"],
        "request_sha256": request["request_sha256"],
        "catalog_sha256": catalog["catalog_sha256"],
        "contracts_sha256": contracts["contracts_sha256"],
        "caps_sha256": caps["caps_sha256"],
        "region_manifest_sha256": region_manifest_sha256,
    })
    print(json.dumps({
        "out": str(out),
        "region_key": entry["region_key"],
        "pose": entry["selection_payload"]["pose_name"],
        "frame": entry["selection_payload"]["frame"],
        "request_sha256": request["request_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
