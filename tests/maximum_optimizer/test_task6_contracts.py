from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
import hashlib
import tempfile
import unittest

from maximum_optimizer.composite import (
    adaptive_direct_source_union_record_from_payload,
    adaptive_direct_source_union_record_payload,
    build_adaptive_candidate_metrics_proof,
    build_adaptive_direct_coverage_manifest,
    build_adaptive_direct_state_inventory,
    build_adaptive_direct_state_inventory_row,
    build_adaptive_direct_source_union_record,
    build_adaptive_direct_source_union_target,
    build_direct_prefilter_proof,
    build_direct_source_request,
    build_direct_source_snapshot,
    adaptive_direct_recipe,
)
from maximum_optimizer.domain import (
    AdaptiveDirectCoverageOccurrenceProof,
    AdaptiveDirectCoverageSourceProof,
    AdaptiveGraphOccurrenceProof,
    CompositionProof,
    DirectDroppedTriangleProof,
    DirectInputMaterialProof,
    DirectMaterialTriangleProof,
    EligibleAdaptiveSourceProof,
    IneligibleAdaptiveSourceProof,
    GateFailure,
    SourceOverlay,
    ValidationResult,
    adaptive_candidate_metrics_from_payload,
    adaptive_candidate_metrics_payload,
    adaptive_direct_coverage_manifest_from_payload,
    adaptive_direct_coverage_manifest_payload,
    adaptive_direct_state_inventory_from_payload,
    adaptive_direct_state_inventory_row_from_payload,
    adaptive_direct_state_inventory_payload,
    direct_source_request_from_payload,
    direct_source_request_payload,
    direct_source_snapshot_from_payload,
    direct_source_snapshot_payload,
    composition_proof_from_payload,
    composition_proof_payload,
    composite_recipe_from_payload,
    composite_recipe_payload,
)
from maximum_optimizer.focused_cache import RenderFileProof
from tests.maximum_optimizer.test_task6_snapshot_runtime import (
    _input_smd as runtime_input_smd,
    _output_smd as runtime_output_smd,
    _request as runtime_request,
)


H = {c: c * 64 for c in "0123456789abcdef"}


def payload_seal(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def assert_every_field_rejected(test: unittest.TestCase, parser, payload: dict) -> None:
    for field in payload:
        changed = dict(payload)
        changed[field] = "mutated" if payload[field] is None else None
        with test.subTest(field=field), test.assertRaises((TypeError, ValueError)):
            parser(changed)
    with test.assertRaises(ValueError):
        parser({**payload, "unexpected": None})
    missing = dict(payload); missing.pop(next(iter(payload)))
    with test.assertRaises(ValueError):
        parser(missing)


def occurrence(source: str = "body.smd", line: int = 1) -> AdaptiveGraphOccurrenceProof:
    return AdaptiveGraphOccurrenceProof("main.qc", "$body", line, source, "visual")


def source_metrics(
    source: str = "body.smd", *, eligible: bool = True,
    source_size: int = 100, source_sha256: str = H["1"],
):
    common = dict(
        source_identity=source,
        source_relative_path=source,
        source_size=source_size,
        source_sha256=source_sha256,
        output_relative_path=f"output/{source}",
        output_size=source_size if eligible else max(0, source_size - 1),
        output_sha256=source_sha256 if eligible else H["2"],
        occurrences=(occurrence(source),),
    )
    if eligible:
        return EligibleAdaptiveSourceProof.create(
            **common, eligibility_reason="ratio-preserved-exact-v1"
        )
    return IneligibleAdaptiveSourceProof.create(
        **common, ineligibility_reason="adaptive-output-changed-v1"
    )


def witness(
    source: str = "body.smd", *, ordinal: int = 0, state: str = "default",
    source_size: int = 100, source_sha256: str = H["1"],
    component_manifest_sha256: str = H["3"],
    material_contract_sha256: str = H["4"],
) -> AdaptiveDirectCoverageOccurrenceProof:
    return AdaptiveDirectCoverageOccurrenceProof.create(
        occurrence_key=f"occ-{hashlib.sha256(source.encode()).hexdigest()[:8]}-{ordinal:04d}", source_identity=source,
        graph_relative_path="main.qc", directive="$body", line=ordinal + 1,
        state_key=state, bodygroup_key="body", lod_key="lod0", skin_key="skin0",
        source_size=source_size, source_sha256=source_sha256, component_manifest_sha256=component_manifest_sha256,
        material_contract_sha256=material_contract_sha256,
        skeleton_contract_sha256=H["5"],
        pose_contract_sha256=H["6"], equivalence_class_sha256=H["7"],
    )


def coverage_source(
    source: str = "body.smd", *, states: tuple[str, ...] = ("default",),
    components: tuple[str, ...] = ("component-000",),
    poses: tuple[str, ...] = ("bind",),
    source_size: int = 100, source_sha256: str = H["1"],
    component_manifest_sha256: str = H["3"],
    material_contract_sha256: str = H["4"],
    material_region_keys: tuple[str, ...] = ("material-000",),
) -> AdaptiveDirectCoverageSourceProof:
    witnesses = tuple(witness(
        source, ordinal=i, state=state,
        source_size=source_size, source_sha256=source_sha256,
        component_manifest_sha256=component_manifest_sha256,
        material_contract_sha256=material_contract_sha256,
    ) for i, state in enumerate(states))
    return AdaptiveDirectCoverageSourceProof.create(
        source_identity=source, eligibility_kind="eligible-exact-v1",
        source_size=source_size, source_sha256=source_sha256,
        occurrence_keys=tuple(item.occurrence_key for item in witnesses),
        state_keys=states, component_keys=components,
        material_region_keys=material_region_keys,
        skeleton_contract_sha256=H["5"],
        pose_keys=poses, equivalence_class_sha256=H["7"], witnesses=witnesses,
        metrics_sha256=source_metrics(
            source, source_size=source_size, source_sha256=source_sha256,
        ).metrics_sha256,
        state_inventory_sha256=H["9"],
    )


def coverage_source_many(source: str, count: int) -> AdaptiveDirectCoverageSourceProof:
    witnesses = tuple(witness(source, ordinal=i, state="default") for i in range(count))
    return AdaptiveDirectCoverageSourceProof.create(
        source_identity=source, eligibility_kind="eligible-exact-v1",
        source_size=100, source_sha256=H["1"],
        occurrence_keys=tuple(item.occurrence_key for item in witnesses),
        state_keys=("default",), component_keys=("component-000",),
        material_region_keys=("material-000",), skeleton_contract_sha256=H["5"],
        pose_keys=("bind",), equivalence_class_sha256=H["7"], witnesses=witnesses,
        metrics_sha256=source_metrics(source).metrics_sha256,
        state_inventory_sha256=H["9"],
    )


def metrics_for_coverage(sources):
    metric_sources = []
    for source in sources:
        occurrences = tuple(AdaptiveGraphOccurrenceProof(
            item.graph_relative_path, item.directive, item.line, source.source_identity, "visual"
        ) for item in source.witnesses)
        metric_sources.append(EligibleAdaptiveSourceProof.create(
            source_identity=source.source_identity, source_relative_path=source.source_identity,
            source_size=source.source_size, source_sha256=source.source_sha256,
            output_relative_path=f"output/{source.source_identity}", output_size=source.source_size,
            output_sha256=source.source_sha256, eligibility_reason="ratio-preserved-exact-v1",
            occurrences=occurrences,
        ))
    return build_adaptive_candidate_metrics_proof(
        family_id=H["0"], family_input_sha256=H["1"], candidate_id="base",
        candidate_cache_digest=H["3"], base_spec_sha256=H["2"],
        source_manifest_sha256=H["4"], source_snapshot_sha256=H["5"],
        original_graph_sha256=H["6"], candidate_graph_sha256=H["7"],
        raw_metrics_sha256=H["8"], sources=tuple(metric_sources),
    )


def inventory_for_coverage(sources):
    rows = []
    for source in sources:
        for item in source.witnesses:
            rows.append(build_adaptive_direct_state_inventory_row(
                occurrence_key=item.occurrence_key, source_identity=item.source_identity,
                graph_relative_path=item.graph_relative_path, directive=item.directive,
                line=item.line, state_key=item.state_key, bodygroup_key=item.bodygroup_key,
                lod_key=item.lod_key, skin_key=item.skin_key, source_size=item.source_size,
                source_sha256=item.source_sha256, component_keys=source.component_keys,
                material_region_keys=source.material_region_keys,
                skeleton_contract_sha256=item.skeleton_contract_sha256,
                pose_keys=source.pose_keys, component_manifest_sha256=item.component_manifest_sha256,
                material_contract_sha256=item.material_contract_sha256,
                pose_contract_sha256=item.pose_contract_sha256,
                equivalence_class_sha256=item.equivalence_class_sha256,
            ))
    rows.sort(key=lambda item: (item.occurrence_key.casefold(), item.occurrence_key))
    return build_adaptive_direct_state_inventory(
        family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
        base_spec_sha256=H["2"], base_cache_digest=H["3"],
        base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
        complete_source_identities=tuple(item.source_identity for item in sources), rows=tuple(rows),
    )


class AdaptiveMetricsContracts(unittest.TestCase):
    def test_complete_metrics_union_round_trips_and_is_deeply_immutable(self) -> None:
        sources = (source_metrics(), source_metrics("wheel.smd", eligible=False))
        proof = build_adaptive_candidate_metrics_proof(
            family_id=H["0"], family_input_sha256=H["1"], candidate_id="base",
            candidate_cache_digest=H["2"], base_spec_sha256=H["3"],
            source_manifest_sha256=H["4"], source_snapshot_sha256=H["5"],
            original_graph_sha256=H["6"], candidate_graph_sha256=H["7"],
            raw_metrics_sha256=H["8"], sources=sources,
        )
        parsed = adaptive_candidate_metrics_from_payload(adaptive_candidate_metrics_payload(proof))
        self.assertEqual(parsed, proof)
        self.assertEqual(tuple(item.kind for item in proof.sources), ("eligible-exact-v1", "ineligible-changed-v1"))
        with self.assertRaises(FrozenInstanceError):
            proof.sources = ()
        with self.assertRaises(ValueError):
            replace(proof, family_input_sha256=H["f"])
        with self.assertRaises(ValueError):
            adaptive_candidate_metrics_from_payload({**adaptive_candidate_metrics_payload(proof), "extra": 1})
        assert_every_field_rejected(self, adaptive_candidate_metrics_from_payload, adaptive_candidate_metrics_payload(proof))
        for field in adaptive_candidate_metrics_payload(proof)["sources"][0]:
            payload = adaptive_candidate_metrics_payload(proof)
            payload["sources"][0][field] = None
            with self.subTest(source_field=field), self.assertRaises((TypeError, ValueError)):
                adaptive_candidate_metrics_from_payload(payload)
        for field in adaptive_candidate_metrics_payload(proof)["sources"][0]["occurrences"][0]:
            payload = adaptive_candidate_metrics_payload(proof)
            payload["sources"][0]["occurrences"][0][field] = None
            with self.subTest(occurrence_field=field), self.assertRaises((TypeError, ValueError)):
                adaptive_candidate_metrics_from_payload(payload)

    def test_metrics_reject_noncanonical_duplicate_or_incomplete_source_union(self) -> None:
        first = source_metrics("body.smd")
        second = source_metrics("wheel.smd")
        with self.assertRaises(ValueError):
            build_adaptive_candidate_metrics_proof(
                family_id=H["0"], family_input_sha256=H["1"], candidate_id="base",
                candidate_cache_digest=H["2"], base_spec_sha256=H["3"],
                source_manifest_sha256=H["4"], source_snapshot_sha256=H["5"],
                original_graph_sha256=H["6"], candidate_graph_sha256=H["7"],
                raw_metrics_sha256=H["8"], sources=(second, first),
            )


class CoverageContracts(unittest.TestCase):
    def manifest(self, sources=None):
        sources = (coverage_source(),) if sources is None else tuple(sources)
        metrics = metrics_for_coverage(sources)
        inventory = inventory_for_coverage(sources)
        return build_adaptive_direct_coverage_manifest(
            family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
            base_spec_sha256=H["2"], base_cache_digest=H["3"],
            base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
            metrics_proof=metrics, state_inventory=inventory,
        )

    def test_manifest_round_trips_and_seals_every_nested_field(self) -> None:
        manifest = self.manifest()
        parsed = adaptive_direct_coverage_manifest_from_payload(
            adaptive_direct_coverage_manifest_payload(manifest)
        )
        self.assertEqual(parsed, manifest)
        assert_every_field_rejected(
            self, adaptive_direct_coverage_manifest_from_payload,
            adaptive_direct_coverage_manifest_payload(manifest),
        )
        for level in ("sources", "witnesses"):
            original = adaptive_direct_coverage_manifest_payload(manifest)
            nested = original["sources"][0] if level == "sources" else original["sources"][0]["witnesses"][0]
            for field in tuple(nested):
                payload = adaptive_direct_coverage_manifest_payload(manifest)
                target = payload["sources"][0] if level == "sources" else payload["sources"][0]["witnesses"][0]
                target[field] = None
                with self.subTest(level=level, field=field), self.assertRaises((TypeError, ValueError)):
                    adaptive_direct_coverage_manifest_from_payload(payload)
        with self.assertRaises(ValueError):
            replace(manifest.sources[0].witnesses[0], line=99)
        with self.assertRaises(ValueError):
            replace(manifest, base_spec_sha256=H["f"])
        boolean_total = adaptive_direct_coverage_manifest_payload(manifest)
        boolean_total["occurrence_count"] = True
        unsealed = dict(boolean_total); unsealed.pop("coverage_manifest_sha256")
        boolean_total["coverage_manifest_sha256"] = payload_seal(unsealed)
        with self.assertRaises(ValueError):
            adaptive_direct_coverage_manifest_from_payload(boolean_total)
        forged = adaptive_direct_coverage_manifest_payload(manifest)
        forged_source = forged["sources"][0]
        forged_source["component_keys"] = ["forged-component"]
        source_unsealed = dict(forged_source); source_unsealed.pop("source_coverage_sha256")
        forged_source["source_coverage_sha256"] = payload_seal(source_unsealed)
        manifest_unsealed = dict(forged); manifest_unsealed.pop("coverage_manifest_sha256")
        forged["coverage_manifest_sha256"] = payload_seal(manifest_unsealed)
        with self.assertRaises(ValueError):
            adaptive_direct_coverage_manifest_from_payload(forged)

    def test_typed_state_inventory_round_trips_and_coverage_rejects_metrics_or_occurrence_mismatch(self) -> None:
        sources = (coverage_source(states=("a", "b")),)
        metrics = metrics_for_coverage(sources)
        inventory = inventory_for_coverage(sources)
        payload = adaptive_direct_state_inventory_payload(inventory)
        self.assertEqual(adaptive_direct_state_inventory_from_payload(payload), inventory)
        assert_every_field_rejected(self, adaptive_direct_state_inventory_from_payload, payload)
        mismatched_metrics = build_adaptive_candidate_metrics_proof(
            family_id=H["f"], family_input_sha256=H["1"], candidate_id="base",
            candidate_cache_digest=H["3"], base_spec_sha256=H["2"],
            source_manifest_sha256=H["4"], source_snapshot_sha256=H["5"],
            original_graph_sha256=H["6"], candidate_graph_sha256=H["7"],
            raw_metrics_sha256=H["8"], sources=metrics.sources,
        )
        with self.assertRaises(ValueError):
            build_adaptive_direct_coverage_manifest(
                family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
                base_spec_sha256=H["2"], base_cache_digest=H["3"],
                base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
                metrics_proof=mismatched_metrics, state_inventory=inventory,
            )
        incomplete = build_adaptive_direct_state_inventory(
            family_id=inventory.family_id, family_input_sha256=inventory.family_input_sha256,
            base_candidate_id=inventory.base_candidate_id, base_spec_sha256=inventory.base_spec_sha256,
            base_cache_digest=inventory.base_cache_digest,
            base_source_manifest_sha256=inventory.base_source_manifest_sha256,
            base_source_snapshot_sha256=inventory.base_source_snapshot_sha256,
            complete_source_identities=inventory.complete_source_identities,
            rows=inventory.rows[:-1],
        )
        with self.assertRaises(ValueError):
            build_adaptive_direct_coverage_manifest(
                family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
                base_spec_sha256=H["2"], base_cache_digest=H["3"],
                base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
                metrics_proof=metrics, state_inventory=incomplete,
            )
    def test_manifest_rejects_zero_or_more_than_eight_eligible_sources(self) -> None:
        with self.assertRaises(ValueError):
            self.manifest(())
        sources = tuple(coverage_source(f"source-{i:02d}.smd") for i in range(9))
        with self.assertRaises(ValueError):
            self.manifest(sources)

    def test_manifest_rejects_seventeen_states_and_257_components(self) -> None:
        with self.assertRaises(ValueError):
            self.manifest((coverage_source(states=tuple(f"state-{i:02d}" for i in range(17))),))
        with self.assertRaises(ValueError):
            self.manifest((coverage_source(components=tuple(f"component-{i:03d}" for i in range(257))),))
        with self.assertRaises(ValueError):
            coverage_source(poses=("bind", "Bind"))
        inventory = inventory_for_coverage((coverage_source(),))
        row = adaptive_direct_state_inventory_payload(inventory)["rows"][0]
        row["pose_keys"] = ["bind", "Bind"]
        row_unsealed = dict(row); row_unsealed.pop("row_sha256")
        row["row_sha256"] = payload_seal(row_unsealed)
        with self.assertRaises(ValueError):
            adaptive_direct_state_inventory_row_from_payload(row)

    def test_manifest_accepts_4096_and_rejects_4097_aggregate_witnesses(self) -> None:
        first = coverage_source_many("body.smd", 2048)
        second = coverage_source_many("wheel.smd", 2048)
        self.assertEqual(self.manifest((first, second)).occurrence_count, 4096)
        third = coverage_source_many("glass.smd", 1)
        with self.assertRaises(ValueError):
            self.manifest((first, third, second))

    def test_manifest_rejects_split_equivalence_and_noncanonical_order(self) -> None:
        valid = coverage_source(states=("a", "b"))
        item = valid.witnesses[1]
        split = AdaptiveDirectCoverageOccurrenceProof.create(
            occurrence_key=item.occurrence_key, source_identity=item.source_identity,
            graph_relative_path=item.graph_relative_path, directive=item.directive,
            line=item.line, state_key=item.state_key, bodygroup_key=item.bodygroup_key,
            lod_key=item.lod_key, skin_key=item.skin_key, source_size=item.source_size,
            source_sha256=item.source_sha256,
            component_manifest_sha256=item.component_manifest_sha256,
            material_contract_sha256=item.material_contract_sha256,
            skeleton_contract_sha256=item.skeleton_contract_sha256,
            pose_contract_sha256=item.pose_contract_sha256,
            equivalence_class_sha256=H["8"],
        )
        with self.assertRaises(ValueError):
            replace(valid, witnesses=(valid.witnesses[0], split))
        changed_contract = AdaptiveDirectCoverageOccurrenceProof.create(
            occurrence_key=item.occurrence_key, source_identity=item.source_identity,
            graph_relative_path=item.graph_relative_path, directive=item.directive,
            line=item.line, state_key=item.state_key, bodygroup_key=item.bodygroup_key,
            lod_key=item.lod_key, skin_key=item.skin_key, source_size=item.source_size,
            source_sha256=item.source_sha256,
            component_manifest_sha256=item.component_manifest_sha256,
            material_contract_sha256=H["8"],
            skeleton_contract_sha256=item.skeleton_contract_sha256,
            pose_contract_sha256=item.pose_contract_sha256,
            equivalence_class_sha256=item.equivalence_class_sha256,
        )
        with self.assertRaises(ValueError):
            AdaptiveDirectCoverageSourceProof.create(
                source_identity=valid.source_identity, eligibility_kind=valid.eligibility_kind,
                source_size=valid.source_size, source_sha256=valid.source_sha256,
                occurrence_keys=valid.occurrence_keys, state_keys=valid.state_keys,
                component_keys=valid.component_keys, material_region_keys=valid.material_region_keys,
                skeleton_contract_sha256=valid.skeleton_contract_sha256,
                pose_keys=valid.pose_keys, equivalence_class_sha256=valid.equivalence_class_sha256,
                metrics_sha256=valid.metrics_sha256,
                state_inventory_sha256=valid.state_inventory_sha256,
                witnesses=(valid.witnesses[0], changed_contract),
            )
        with self.assertRaises(ValueError):
            self.manifest((coverage_source("wheel.smd"), coverage_source("body.smd")))


class DirectContracts(unittest.TestCase):
    def prefilter(self):
        triangle = DirectDroppedTriangleProof(0, "paint", ("root",), "cross-squared-at-most-1e-30", H["1"])
        return build_direct_prefilter_proof(source_triangle_count=10, triangles=(triangle,))

    def request(self, coverage=H["a"], source_coverage=H["b"]):
        return build_direct_source_request(
            family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
            base_spec_sha256=H["2"], base_cache_digest=H["3"],
            base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
            coverage_manifest_sha256=coverage, source_coverage_sha256=source_coverage,
            optimizer_contract_sha256=H["6"], whole_profile_sha256=H["7"],
            focused_profile_sha256=H["8"], dependency_proof_sha256=H["9"],
            source_identity="body.smd", source_relative_path="body.smd", source_size=100,
            source_sha256=H["1"], direct_ratio=0.5, expected_prefilter=self.prefilter(),
            expected_materials=(DirectInputMaterialProof(0, "paint", 9, H["c"]),),
        )

    def test_prefilter_request_and_snapshot_round_trip_with_exact_bindings(self) -> None:
        request = self.request()
        self.assertEqual(direct_source_request_from_payload(direct_source_request_payload(request)), request)
        assert_every_field_rejected(self, direct_source_request_from_payload, direct_source_request_payload(request))
        for field in direct_source_request_payload(request)["expected_prefilter"]:
            payload = direct_source_request_payload(request)
            payload["expected_prefilter"][field] = None
            with self.subTest(prefilter_field=field), self.assertRaises((TypeError, ValueError)):
                direct_source_request_from_payload(payload)
        for field in direct_source_request_payload(request)["expected_materials"][0]:
            payload = direct_source_request_payload(request)
            payload["expected_materials"][0][field] = None
            with self.subTest(input_material_field=field), self.assertRaises((TypeError, ValueError)):
                direct_source_request_from_payload(payload)
        with tempfile.TemporaryDirectory() as root:
            base = Path(root).resolve()
            input_root = base / "input"; output_root = base / "output"
            input_root.mkdir(); output_root.mkdir()
            (input_root / "body.smd").write_bytes(runtime_input_smd())
            output = runtime_output_smd()
            (output_root / "output.smd").write_bytes(output)
            request = runtime_request()
            snapshot = build_direct_source_snapshot(
                request=request, input_source_root=input_root, source_root=output_root,
                output_relative_path="output.smd", output_size=len(output),
                output_sha256=hashlib.sha256(output).hexdigest(),
                triangles_before=9, triangles_after=4,
                material_triangles=(DirectMaterialTriangleProof(0, "paint", 9, 4, 4),),
                prefilter=request.expected_prefilter,
            )
            self.assertEqual(snapshot.triangles_before, 10 - request.expected_prefilter.dropped_count)
            self.assertTrue(snapshot.direct_candidate_id.startswith("direct-source-"))
            self.assertEqual(len(snapshot.direct_cache_digest), 64)
            payload = direct_source_snapshot_payload(snapshot)
            parser = lambda item: direct_source_snapshot_from_payload(
                item, source_root=output_root, input_source_root=input_root,
            )
            self.assertEqual(parser(payload), snapshot)
            for field in payload["material_triangles"][0]:
                changed = direct_source_snapshot_payload(snapshot)
                changed["material_triangles"][0][field] = None
                with self.subTest(material_triangle_field=field), self.assertRaises((TypeError, ValueError)):
                    parser(changed)
            assert_every_field_rejected(
                self, parser, payload,
            )
            for nested_name in ("request", "prefilter"):
                for field in payload[nested_name]:
                    changed = direct_source_snapshot_payload(snapshot)
                    changed[nested_name][field] = None
                    with self.subTest(snapshot_nested=nested_name, field=field), self.assertRaises((TypeError, ValueError)):
                        parser(changed)
            with self.assertRaises(ValueError):
                replace(snapshot, output_sha256=H["e"])
        with self.assertRaises(ValueError):
            replace(request, coverage_manifest_sha256=H["f"])
        with self.assertRaises(ValueError):
            replace(request, source_coverage_sha256=H["f"])

    def test_request_seals_independent_dropped_triangle_record_hash(self) -> None:
        triangle = DirectDroppedTriangleProof(0, "paint", ("root",), "cross-squared-at-most-1e-30", H["2"])
        mismatched = build_direct_prefilter_proof(source_triangle_count=10, triangles=(triangle,))
        request = build_direct_source_request(
            family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
            base_spec_sha256=H["2"], base_cache_digest=H["3"],
            base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
            coverage_manifest_sha256=H["a"], source_coverage_sha256=H["b"],
            optimizer_contract_sha256=H["6"], whole_profile_sha256=H["7"],
            focused_profile_sha256=H["8"], dependency_proof_sha256=H["9"],
            source_identity="body.smd", source_relative_path="body.smd", source_size=100,
            source_sha256=H["1"], direct_ratio=0.5, expected_prefilter=mismatched,
            expected_materials=(DirectInputMaterialProof(0, "paint", 9, H["c"]),),
        )
        self.assertEqual(request.expected_prefilter.triangles[0].source_sha256, H["2"])


class SourceUnionContracts(unittest.TestCase):
    def test_state_independent_record_has_exact_32_images_and_visibility_matrix(self) -> None:
        target = build_adaptive_direct_source_union_target(
            source_proof=coverage_source("body.smd"), coverage_manifest_sha256=H["a"],
        )
        files = []
        for side in ("candidate", "reference"):
            for pose in ("bind",):
                for render_pass in ("clay", "textured"):
                    for camera in tuple(f"camera-{i:02d}" for i in range(8)):
                        files.append(RenderFileProof(side, "image", f"source-union/{target.union_key}/{side}/{pose}/{render_pass}/{camera}.png", 1, H["1"], 1, 1))
        record = build_adaptive_direct_source_union_record(
            target=target, validation=ValidationResult(True), files=tuple(sorted(files, key=lambda item: item.path)),
            visibility=(("component-000", "bind", "camera-00", 1, 1),),
        )
        self.assertEqual(target.image_count, 32)
        self.assertEqual(len(record.files), 32)
        payload = adaptive_direct_source_union_record_payload(record)
        self.assertEqual(adaptive_direct_source_union_record_from_payload(payload), record)
        assert_every_field_rejected(self, adaptive_direct_source_union_record_from_payload, payload)
        for nested_name in ("target", "files", "visibility"):
            nested = payload[nested_name] if nested_name == "target" else payload[nested_name][0]
            for field in nested:
                changed = adaptive_direct_source_union_record_payload(record)
                target_payload = changed[nested_name] if nested_name == "target" else changed[nested_name][0]
                target_payload[field] = None
                with self.subTest(record_nested=nested_name, field=field), self.assertRaises((TypeError, ValueError)):
                    adaptive_direct_source_union_record_from_payload(changed)
        with self.assertRaises(ValueError):
            build_adaptive_direct_source_union_record(
                target=target, validation=ValidationResult(True), files=tuple(files + [files[0]]),
                visibility=(("component-000", "bind", "camera-00", 1, 1),),
            )
        with self.assertRaises(ValueError):
            replace(target, image_count=65)
        with self.assertRaises(ValueError):
            build_adaptive_direct_source_union_record(
                target=target, validation=ValidationResult(True),
                files=tuple(sorted(files, key=lambda item: item.path)),
                visibility=(("component-000", "bind", "camera-01", 1, 1),),
            )
        later = build_adaptive_direct_source_union_record(
            target=target, validation=ValidationResult(True),
            files=tuple(sorted(files, key=lambda item: item.path)),
            visibility=(("component-000", "bind", "camera-01", 1, 1, (("camera-00", 0, 3),)),),
        )
        self.assertEqual(later.visibility[0].camera_key, "camera-01")
        failed = build_adaptive_direct_source_union_record(
            target=target,
            validation=ValidationResult(False, (GateFailure("focused", "body", 2.0, 1.0, "too different"),)),
            files=tuple(sorted(files, key=lambda item: item.path)),
            visibility=(("component-000", "bind", "camera-00", 1, 1),),
        )
        self.assertEqual(
            adaptive_direct_source_union_record_from_payload(adaptive_direct_source_union_record_payload(failed)),
            failed,
        )

    def test_union_target_rejects_windows_and_noncanonical_relative_aliases(self) -> None:
        for identity in ("C:body.smd", "C:/body.smd", "//server/share/body.smd", "parts\\body.smd", "parts/./body.smd", "parts/../body.smd", "/body.smd"):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                build_adaptive_direct_source_union_target(
                    source_proof=coverage_source(identity), coverage_manifest_sha256=H["a"],
                )

    def test_two_pose_target_is_exactly_64_and_union_key_is_source_local(self) -> None:
        first = build_adaptive_direct_source_union_target(
            source_proof=coverage_source("body.smd", poses=("bind", "turn")), coverage_manifest_sha256=H["a"],
        )
        second = build_adaptive_direct_source_union_target(
            source_proof=coverage_source("wheel.smd", poses=("bind", "turn")), coverage_manifest_sha256=H["c"],
        )
        self.assertEqual(first.image_count, 64)
        self.assertEqual(first.union_key, f"source-union-{first.source_coverage_sha256[:32]}")
        self.assertNotEqual(first.union_key, second.union_key)


class CompositionDiscriminatorContracts(unittest.TestCase):
    def test_composition_proof_enforces_kind_specific_four_vs_eight_bound(self) -> None:
        from maximum_optimizer.domain import ChangedSourceProof
        def changed(i: int):
            return ChangedSourceProof(f"source-{i:02d}.smd", f"source-{i:02d}.smd", 2, H["1"], 1, H["2"], H["3"], H["4"])
        for kind, accepted, rejected in (("focused-recovery-v1", 4, 5), ("adaptive-direct-fallback-v1", 8, 9)):
            proof = CompositionProof.create(kind, H["5"], H["6"], H["7"], tuple(changed(i) for i in range(accepted)))
            payload = composition_proof_payload(proof)
            self.assertEqual(composition_proof_from_payload(payload), proof)
            assert_every_field_rejected(self, composition_proof_from_payload, payload)
            with self.assertRaises(ValueError):
                CompositionProof.create(kind, H["5"], H["6"], H["7"], tuple(changed(i) for i in range(rejected)))

    def test_adaptive_recipe_binds_complete_identity_and_round_zero_only(self) -> None:
        overlays = tuple(SourceOverlay(
            f"source-{i:02d}.smd", "direct-position", None, H["1"], H["2"], 10,
            H["3"], f"direct-{i:02d}", H["4"], 0.5, (), "approved-direct-position-v1",
        ) for i in range(8))
        recipe = adaptive_direct_recipe(
            family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
            base_spec_sha256=H["2"], base_cache_digest=H["3"],
            base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
            optimizer_contract_sha256=H["6"], whole_profile_sha256=H["7"],
            focused_profile_sha256=H["8"], dependency_proof_sha256=H["9"],
            coverage_manifest_sha256=H["a"], direct_request_set_sha256=H["b"],
            direct_snapshot_set_sha256=H["c"], direct_ratio=0.5, overlays=overlays,
        )
        payload = composite_recipe_payload(recipe)
        self.assertEqual(composite_recipe_from_payload(payload), recipe)
        self.assertEqual(len(recipe.overlays), 8)
        hostile = dict(payload); hostile["round_index"] = 1
        unsealed = dict(hostile); unsealed.pop("recipe_sha256")
        hostile["recipe_sha256"] = payload_seal(unsealed)
        with self.assertRaises(ValueError):
            composite_recipe_from_payload(hostile)
        for field in ("coverage_manifest_sha256", "base_source_snapshot_sha256", "direct_request_set_sha256", "direct_snapshot_set_sha256", "base_strategy", "direct_strategy", "direct_transfer"):
            missing = dict(payload); missing.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                composite_recipe_from_payload(missing)


if __name__ == "__main__":
    unittest.main()
