from __future__ import annotations

import hashlib
import json
import copy
from pathlib import Path

from maximum_optimizer.animation_pose_selector import AnimationPoseSelection
from maximum_optimizer.qc_graph import parse_qc_graph
from maximum_optimizer.regions import build_region_manifest


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _animation_smd(*, source_times: tuple[int, ...] = (0, 17)) -> str:
    lines = [
        "version 1", "nodes", '0 "root" -1', '1 "Hood" 0', "end",
        "skeleton",
    ]
    for ordinal, source_time in enumerate(source_times):
        lines.extend((
            f"time {source_time}", "0 0 0 0 0 0 0",
            f"1 0 0 {ordinal} 0 0 0",
        ))
    lines.extend(("end", "triangles", "end", ""))
    return "\n".join(lines)


def _geometry_smd() -> str:
    return (
        "version 1\nnodes\n0 \"root\" -1\n1 \"Hood\" 0\nend\n"
        "skeleton\ntime 0\n0 0 0 0 0 0 0\n1 0 0 0 0 0 0\nend\n"
        "triangles\nmaterial/body\n"
        "1 0 0 0 0 0 1 0 0 1 1 1\n"
        "1 1 0 0 0 0 1 1 0 1 1 1\n"
        "1 0 1 0 0 0 1 0 1 1 1 1\nend\n"
    )


def _write_family(root: Path) -> tuple[Path, object]:
    root.mkdir(parents=True)
    (root / "body.smd").write_text(_geometry_smd(), encoding="utf-8")
    (root / "hood.smd").write_text(_animation_smd(), encoding="utf-8")
    (root / "hood_ref.smd").write_text(
        _animation_smd(source_times=(0,)), encoding="utf-8",
    )
    qc = root / "main.qc"
    qc.write_text(
        '$body "body" "body.smd"\n'
        '$weightlist "weights_hood" {\n "root" 0\n "Hood" 1\n}\n'
        '$animation "hood_ref" "hood_ref.smd" {\n}\n'
        '$animation "hood" "hood.smd" {\n'
        ' subtract "hood_ref" 0\n weightlist "weights_hood"\n}\n',
        encoding="utf-8",
    )
    manifest = build_region_manifest((("body.smd", "Body", ("material/body",)),))
    return qc, manifest


def _write_many_family(root: Path, count: int = 89) -> tuple[Path, object]:
    root.mkdir(parents=True)
    (root / "body.smd").write_text(_geometry_smd(), encoding="utf-8")
    lines = [
        '$body "body" "body.smd"',
        '$weightlist "weights_hood" {', ' "root" 0', ' "Hood" 1', '}',
    ]
    for index in range(count):
        name = f"hood_{index:03d}"
        reference = f"{name}_ref"
        (root / f"{name}.smd").write_text(_animation_smd(), encoding="utf-8")
        (root / f"{reference}.smd").write_text(
            _animation_smd(source_times=(0,)), encoding="utf-8",
        )
        lines.extend((
            f'$animation "{reference}" "{reference}.smd" {{', '}',
            f'$animation "{name}" "{name}.smd" {{',
            f' subtract "{reference}" 0', ' weightlist "weights_hood"', '}',
        ))
    qc = root / "main.qc"
    qc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = build_region_manifest((("body.smd", "Body", ("material/body",)),))
    return qc, manifest


def _selection(animation_path: Path, reference_path: Path) -> AnimationPoseSelection:
    animation_sha = hashlib.sha256(animation_path.read_bytes()).hexdigest()
    reference_sha = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    unsigned = {
        "animation_relative_path": animation_path.name,
        "animation_sha256": animation_sha,
        "baseline": "geometry-rest",
        "bone_index": 1,
        "bone_name": "Hood",
        "candidate_inputs_consulted": False,
        "corrective_used_as_baseline": False,
        "displacement": 4.0,
        "displacement_squared": "16",
        "frame": 1,
        "geometry_inventory_sha256": _sha("geometry-inventory"),
        "pose_name": animation_path.stem.casefold(),
        "reference_relative_path": reference_path.name,
        "reference_sha256": reference_sha,
        "raw_render_max_displacement": 4.0,
        "raw_render_rms_displacement": 2.0,
        "rms_displacement": 2.0,
        "selector": "exact-raw-render-region-displacement-v3",
        "selector_input_sha256": _sha("selector-input"),
        "source_time": 17,
    }
    selection_sha = _canonical_hash(unsigned)
    return AnimationPoseSelection(
        selector="exact-raw-render-region-displacement-v3",
        animation_relative_path=animation_path.name,
        reference_relative_path=reference_path.name,
        animation_sha256=animation_sha,
        reference_sha256=reference_sha,
        frame=1,
        source_time=17,
        bone_index=1,
        bone_name="Hood",
        displacement=4.0,
        rms_displacement=2.0,
        displacement_squared="16",
        geometry_inventory_sha256=_sha("geometry-inventory"),
        selector_input_sha256=_sha("selector-input"),
        selection_sha256=selection_sha,
    )


def _scored_selection(animation_path: Path, reference_path: Path) -> dict[str, object]:
    payload = _selection(animation_path, reference_path).to_payload()
    score = float(int(animation_path.stem.rsplit("_", 1)[1]) + 1)
    payload["displacement"] = payload["raw_render_max_displacement"] = score
    payload["rms_displacement"] = payload["raw_render_rms_displacement"] = score / 2
    payload["displacement_squared"] = str(score * score)
    payload["selection_sha256"] = _canonical_hash({
        key: value for key, value in payload.items() if key != "selection_sha256"
    })
    return payload


def test_catalog_resolves_exact_source_action_corrective_and_weightlist(tmp_path):
    from maximum_optimizer.region_animation_catalog import (
        RegionAnimationCatalogCaps,
        build_region_animation_catalog,
    )

    source = tmp_path / "original"
    qc, manifest = _write_family(source)
    graph = parse_qc_graph(qc, source)
    calls = []

    def selector(animation_root, *, source_root, animation_pairs, geometry_paths):
        calls.append((Path(animation_root), Path(source_root), animation_pairs, geometry_paths))
        return _selection(Path(animation_pairs[0][0]), Path(animation_pairs[0][1]))

    catalog = build_region_animation_catalog(
        graph,
        manifest,
        source_root=source,
        family_id="charger",
        family_input_sha256=_sha("family"),
        source_snapshot_sha256=_sha("source-snapshot"),
        region_manifest_sha256=_sha("region-manifest"),
        contracts_sha256=_sha("contracts"),
        selector=selector,
        caps=RegionAnimationCatalogCaps(),
    )

    payload = catalog.to_payload()
    assert payload["candidate_inputs_consulted"] is False
    assert len(calls) == 2  # one source shard, then its authoritative winner
    assert calls[0][0] == source.resolve()
    assert calls[0][1] == source.resolve()
    assert tuple(Path(item).name for item in calls[0][2][0]) == ("hood.smd", "hood_ref.smd")
    assert calls[0][3] == (source / "body.smd",)
    definition = payload["definitions"][0]
    assert definition["action_name"] == "hood"
    assert definition["corrective_name"] == "hood_ref"
    assert definition["subtract_frame"] == 0
    assert definition["weightlist_name"] == "weights_hood"
    assert definition["action_sha256"] == hashlib.sha256((source / "hood.smd").read_bytes()).hexdigest()
    assert definition["corrective_sha256"] == hashlib.sha256((source / "hood_ref.smd").read_bytes()).hexdigest()
    region = payload["regions"][0]
    assert region["mode"] == "bind-animation"
    assert region["selection_payload"]["frame"] == 1
    assert region["selection_payload"]["source_time"] == 17
    assert region["related_pair_count"] == 1
    assert region["shards"][0]["pair_count"] == 1


def test_catalog_rejects_action_corrective_or_geometry_lineage_mismatch(tmp_path):
    from maximum_optimizer.region_animation_catalog import (
        RegionAnimationCatalogCaps,
        build_region_animation_catalog,
    )

    source = tmp_path / "original"
    qc, manifest = _write_family(source)
    graph = parse_qc_graph(qc, source)
    mismatched = _animation_smd(source_times=(0,)).replace('"Hood"', '"Door"')
    (source / "hood_ref.smd").write_text(mismatched, encoding="utf-8")

    def selector(_animation_root, *, animation_pairs, **_kwargs):
        return _selection(Path(animation_pairs[0][0]), Path(animation_pairs[0][1]))

    try:
        build_region_animation_catalog(
            graph,
            manifest,
            source_root=source,
            family_id="charger",
            family_input_sha256=_sha("family"),
            source_snapshot_sha256=_sha("source-snapshot"),
            region_manifest_sha256=_sha("region-manifest"),
            contracts_sha256=_sha("contracts"),
            selector=selector,
            caps=RegionAnimationCatalogCaps(),
        )
    except ValueError as exc:
        assert "lineage" in str(exc).casefold()
    else:
        raise AssertionError("mismatched action/corrective lineage was accepted")


def test_catalog_shards_eighty_nine_pairs_as_sixty_four_plus_twenty_five(tmp_path):
    from maximum_optimizer.region_animation_catalog import (
        RegionAnimationCatalogCaps,
        build_region_animation_catalog,
        parse_region_animation_catalog,
    )

    source = tmp_path / "original"
    qc, manifest = _write_many_family(source)
    graph = parse_qc_graph(qc, source)
    calls: list[int] = []

    def selector(_animation_root, *, animation_pairs, **_kwargs):
        calls.append(len(animation_pairs))
        return max(
            (_scored_selection(Path(action), Path(reference))
             for action, reference in animation_pairs),
            key=lambda payload: float(payload["displacement"]),
        )

    payload = build_region_animation_catalog(
        graph, manifest, source_root=source, family_id="skyline",
        family_input_sha256=_sha("family"),
        source_snapshot_sha256=_sha("source-snapshot"),
        region_manifest_sha256=_sha("region-manifest"),
        contracts_sha256=_sha("contracts"), selector=selector,
        caps=RegionAnimationCatalogCaps(),
    ).to_payload()

    assert calls == [64, 25, 2]
    region = payload["regions"][0]
    assert [item["pair_count"] for item in region["shards"]] == [64, 25]
    assert region["related_pair_count"] == 89
    assert region["selection_payload"]["animation_relative_path"] == "hood_088.smd"

    tampered = copy.deepcopy(payload)
    changed_region = tampered["regions"][0]
    first_winner = copy.deepcopy(changed_region["shards"][0]["selection_payload"])
    changed_region["shards"][1]["selection_payload"] = first_winner
    changed_region["selection_payload"] = first_winner
    second_shard = changed_region["shards"][1]
    second_shard["proof_sha256"] = _canonical_hash({
        key: value for key, value in second_shard.items() if key != "proof_sha256"
    })
    changed_region["proof_sha256"] = _canonical_hash({
        key: value for key, value in changed_region.items() if key != "proof_sha256"
    })
    tampered["catalog_sha256"] = _canonical_hash({
        key: value for key, value in tampered.items() if key != "catalog_sha256"
    })
    try:
        parse_region_animation_catalog(
            tampered,
            expected_catalog_sha256=tampered["catalog_sha256"],
            expected_family_id="skyline",
            expected_family_input_sha256=_sha("family"),
            expected_source_snapshot_sha256=_sha("source-snapshot"),
            expected_region_manifest_sha256=_sha("region-manifest"),
            expected_contracts_sha256=_sha("contracts"),
            expected_caps_sha256=tampered["caps"]["caps_sha256"],
        )
    except ValueError:
        pass
    else:
        raise AssertionError("cross-shard winner substitution was accepted")


def test_catalog_external_parser_rejects_nested_or_binding_tamper(tmp_path):
    from maximum_optimizer.region_animation_catalog import (
        RegionAnimationCatalogCaps,
        build_region_animation_catalog,
        parse_region_animation_catalog,
    )

    source = tmp_path / "original"
    qc, manifest = _write_family(source)
    graph = parse_qc_graph(qc, source)

    def selector(_animation_root, *, animation_pairs, **_kwargs):
        return _selection(Path(animation_pairs[0][0]), Path(animation_pairs[0][1]))

    payload = build_region_animation_catalog(
        graph, manifest, source_root=source, family_id="charger",
        family_input_sha256=_sha("family"),
        source_snapshot_sha256=_sha("source-snapshot"),
        region_manifest_sha256=_sha("region-manifest"),
        contracts_sha256=_sha("contracts"), selector=selector,
        caps=RegionAnimationCatalogCaps(),
    ).to_payload()
    expected = dict(
        expected_catalog_sha256=payload["catalog_sha256"],
        expected_family_id="charger",
        expected_family_input_sha256=_sha("family"),
        expected_source_snapshot_sha256=_sha("source-snapshot"),
        expected_region_manifest_sha256=_sha("region-manifest"),
        expected_contracts_sha256=_sha("contracts"),
        expected_caps_sha256=payload["caps"]["caps_sha256"],
    )
    assert parse_region_animation_catalog(payload, **expected).to_payload() == payload

    nested = copy.deepcopy(payload)
    nested["regions"][0]["selection_payload"]["displacement"] = 99.0
    nested["catalog_sha256"] = _canonical_hash({
        key: value for key, value in nested.items() if key != "catalog_sha256"
    })
    try:
        parse_region_animation_catalog(
            nested,
            **{**expected, "expected_catalog_sha256": nested["catalog_sha256"]},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("nested selector tamper was accepted")

    binding = copy.deepcopy(payload)
    binding["family_id"] = "other"
    binding["catalog_sha256"] = _canonical_hash({
        key: value for key, value in binding.items() if key != "catalog_sha256"
    })
    try:
        parse_region_animation_catalog(
            binding, **{**expected, "expected_catalog_sha256": binding["catalog_sha256"]},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("external family binding tamper was accepted")
