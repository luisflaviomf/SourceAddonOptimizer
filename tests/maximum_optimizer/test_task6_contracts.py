from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile
import unittest

from maximum_optimizer.composite import (
    adaptive_direct_source_union_record_from_payload,
    adaptive_direct_source_union_record_payload,
    build_adaptive_candidate_metrics_proof,
    build_adaptive_direct_coverage_manifest,
    build_adaptive_direct_source_union_record,
    build_adaptive_direct_source_union_target,
    build_direct_prefilter_proof,
    build_direct_source_request,
    build_direct_source_snapshot,
)
from maximum_optimizer.domain import (
    AdaptiveDirectCoverageOccurrenceProof,
    AdaptiveDirectCoverageSourceProof,
    AdaptiveGraphOccurrenceProof,
    CompositionProof,
    DirectDroppedTriangleProof,
    EligibleAdaptiveSourceProof,
    IneligibleAdaptiveSourceProof,
    ValidationResult,
    adaptive_candidate_metrics_from_payload,
    adaptive_candidate_metrics_payload,
    adaptive_direct_coverage_manifest_from_payload,
    adaptive_direct_coverage_manifest_payload,
    direct_source_request_from_payload,
    direct_source_request_payload,
    direct_source_snapshot_from_payload,
    direct_source_snapshot_payload,
    composition_proof_from_payload,
    composition_proof_payload,
)
from maximum_optimizer.focused_cache import RenderFileProof


H = {c: c * 64 for c in "0123456789abcdef"}


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


def source_metrics(source: str = "body.smd", *, eligible: bool = True):
    common = dict(
        source_identity=source,
        source_relative_path=source,
        source_size=100,
        source_sha256=H["1"],
        output_relative_path=f"output/{source}",
        output_size=100 if eligible else 80,
        output_sha256=H["1"] if eligible else H["2"],
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
    source: str = "body.smd", *, ordinal: int = 0, state: str = "default"
) -> AdaptiveDirectCoverageOccurrenceProof:
    return AdaptiveDirectCoverageOccurrenceProof.create(
        occurrence_key=f"occ-{ordinal:04d}", source_identity=source,
        graph_relative_path="main.qc", directive="$body", line=ordinal + 1,
        state_key=state, bodygroup_key="body", lod_key="lod0", skin_key="skin0",
        source_size=100, source_sha256=H["1"], component_manifest_sha256=H["3"],
        material_contract_sha256=H["4"], skeleton_contract_sha256=H["5"],
        pose_contract_sha256=H["6"], equivalence_class_sha256=H["7"],
    )


def coverage_source(
    source: str = "body.smd", *, states: tuple[str, ...] = ("default",),
    components: tuple[str, ...] = ("component-000",),
) -> AdaptiveDirectCoverageSourceProof:
    witnesses = tuple(witness(source, ordinal=i, state=state) for i, state in enumerate(states))
    return AdaptiveDirectCoverageSourceProof.create(
        source_identity=source, eligibility_kind="eligible-exact-v1",
        source_size=100, source_sha256=H["1"],
        occurrence_keys=tuple(item.occurrence_key for item in witnesses),
        state_keys=states, component_keys=components,
        material_region_keys=("material-000",), skeleton_contract_sha256=H["5"],
        pose_keys=("bind",), equivalence_class_sha256=H["7"], witnesses=witnesses,
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
        return build_adaptive_direct_coverage_manifest(
            family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
            base_spec_sha256=H["2"], base_cache_digest=H["3"],
            base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
            complete_source_identities=tuple(item.source_identity for item in sources),
            sources=sources,
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
        with tempfile.TemporaryDirectory() as root:
            snapshot = build_direct_source_snapshot(
                request=request, source_root=Path(root).resolve(),
                output_relative_path="output.smd", output_size=60, output_sha256=H["d"],
                triangles_before=9, triangles_after=5, prefilter=request.expected_prefilter,
            )
            self.assertEqual(snapshot.triangles_before, 10 - request.expected_prefilter.dropped_count)
            self.assertTrue(snapshot.direct_candidate_id.startswith("direct-source-"))
            self.assertEqual(len(snapshot.direct_cache_digest), 64)
            payload = direct_source_snapshot_payload(snapshot)
            self.assertEqual(direct_source_snapshot_from_payload(payload, source_root=Path(root).resolve()), snapshot)
            assert_every_field_rejected(
                self, lambda item: direct_source_snapshot_from_payload(item, source_root=Path(root).resolve()), payload,
            )
            for nested_name in ("request", "prefilter"):
                for field in payload[nested_name]:
                    changed = direct_source_snapshot_payload(snapshot)
                    changed[nested_name][field] = None
                    with self.subTest(snapshot_nested=nested_name, field=field), self.assertRaises((TypeError, ValueError)):
                        direct_source_snapshot_from_payload(changed, source_root=Path(root).resolve())
            with self.assertRaises(ValueError):
                replace(snapshot, output_sha256=H["e"])
        with self.assertRaises(ValueError):
            replace(request, coverage_manifest_sha256=H["f"])
        with self.assertRaises(ValueError):
            replace(request, source_coverage_sha256=H["f"])

    def test_request_rejects_dropped_triangle_from_different_source(self) -> None:
        triangle = DirectDroppedTriangleProof(0, "paint", ("root",), "cross-squared-at-most-1e-30", H["2"])
        mismatched = build_direct_prefilter_proof(source_triangle_count=10, triangles=(triangle,))
        with self.assertRaises(ValueError):
            build_direct_source_request(
                family_id=H["0"], family_input_sha256=H["1"], base_candidate_id="base",
                base_spec_sha256=H["2"], base_cache_digest=H["3"],
                base_source_manifest_sha256=H["4"], base_source_snapshot_sha256=H["5"],
                coverage_manifest_sha256=H["a"], source_coverage_sha256=H["b"],
                optimizer_contract_sha256=H["6"], whole_profile_sha256=H["7"],
                focused_profile_sha256=H["8"], dependency_proof_sha256=H["9"],
                source_identity="body.smd", source_relative_path="body.smd", source_size=100,
                source_sha256=H["1"], direct_ratio=0.5, expected_prefilter=mismatched,
            )


class SourceUnionContracts(unittest.TestCase):
    def test_state_independent_record_has_exact_32_images_and_visibility_matrix(self) -> None:
        target = build_adaptive_direct_source_union_target(
            source_identity="body.smd", coverage_manifest_sha256=H["a"],
            source_coverage_sha256=H["b"], component_keys=("component-000",),
            material_region_keys=("material-000",), pose_keys=("bind",),
        )
        files = []
        for side in ("candidate", "reference"):
            for pose in ("bind",):
                for render_pass in ("mask", "beauty"):
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

    def test_two_pose_target_is_exactly_64_and_union_key_is_source_local(self) -> None:
        first = build_adaptive_direct_source_union_target(
            source_identity="body.smd", coverage_manifest_sha256=H["a"],
            source_coverage_sha256=H["b"], component_keys=("component-000",),
            material_region_keys=("material-000",), pose_keys=("bind", "turn"),
        )
        second = build_adaptive_direct_source_union_target(
            source_identity="wheel.smd", coverage_manifest_sha256=H["c"],
            source_coverage_sha256=H["d"], component_keys=("component-000",),
            material_region_keys=("material-000",), pose_keys=("bind", "turn"),
        )
        self.assertEqual(first.image_count, 64)
        self.assertEqual(first.union_key, f"source-union-{H['b'][:32]}")
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


if __name__ == "__main__":
    unittest.main()
