from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import tempfile
import unittest

from maximum_optimizer.adaptive_direct_evidence import (
    AdaptiveDirectEvidence,
    adaptive_direct_evidence_from_payload,
    adaptive_direct_evidence_payload,
    build_adaptive_direct_evidence,
)
from maximum_optimizer.composite import (
    build_adaptive_direct_source_union_record,
    build_adaptive_direct_source_union_target,
)
from maximum_optimizer.domain import (
    ChangedSourceProof,
    CompileFileProof,
    CompositeRecipe,
    CompositionProof,
    GateFailure,
    StructuralAuthorizationEvidence,
    ValidationResult,
    composite_recipe_payload,
    structural_authorization_evidence_payload,
)
from maximum_optimizer.focused_cache import (
    RenderFileProof,
    build_final_whole_authorization_evidence,
    build_focused_render_evidence,
    compile_manifest_sha256,
)
from maximum_optimizer.reporting import canonical_json
from tests.maximum_optimizer.test_task6_contracts import coverage_source
from tests.maximum_optimizer.test_focused_cache import (
    _cache_payload,
    _material_proof,
    _render_file_proofs,
    _target,
    _validation_metrics,
    _write_render_side,
)


H = {char: char * 64 for char in "0123456789abcdef"}


def digest(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def recipe(*, coverage: str = H["9"], round_index: int = 0) -> CompositeRecipe:
    values = {
        "schema": 1,
        "kind": "adaptive-direct-fallback-v1",
        "family_id": H["0"],
        "family_input_sha256": H["1"],
        "base_candidate_id": "base",
        "base_spec_sha256": H["2"],
        "base_cache_digest": H["3"],
        "base_source_manifest_sha256": H["4"],
        "base_source_snapshot_sha256": H["5"],
        "base_strategy": "blender-adaptive-v1",
        "direct_strategy": "meshopt-direct-position-v1",
        "direct_transfer": "direct-position-v1",
        "coverage_manifest_sha256": coverage,
        "direct_request_set_sha256": H["6"],
        "direct_snapshot_set_sha256": H["7"],
        "optimizer_contract_sha256": H["8"],
        "whole_profile_sha256": H["a"],
        "focused_profile_sha256": H["b"],
        "dependency_proof_sha256": H["c"],
        "round_index": round_index,
        "direct_ratio": 0.5,
        "overlays": (),
        "selector_version": "surface-risk-top-k-v1",
        "prefilter_version": "direct-degenerate-prefilter-v1",
        "recipe_sha256": H["f"],
    }
    # Recipe requires a non-empty direct overlay in production. Tests replace the
    # temporary tuple below after the domain checkpoint exposes its exact factory.
    from maximum_optimizer.domain import SourceOverlay

    values["overlays"] = (SourceOverlay(
        "body.smd", "direct-position", None, H["1"], H["2"], 1,
        H["3"], "direct", H["4"], 0.5, (), "approved-direct-position-v1",
    ),)
    provisional = object.__new__(CompositeRecipe)
    for name, value in values.items():
        object.__setattr__(provisional, name, value)
    values["recipe_sha256"] = digest(
        composite_recipe_payload(provisional, include_seal=False)
    )
    return CompositeRecipe(**values)


def changed(source_identity: str = "body.smd", *, after: str = H["2"]) -> ChangedSourceProof:
    return ChangedSourceProof(
        source_identity, source_identity, 100, H["1"], 60, after, H["3"], H["4"]
    )


def composition(
    value: CompositeRecipe, sources: tuple[ChangedSourceProof, ...] | None = None,
) -> CompositionProof:
    return CompositionProof.create(
        "adaptive-direct-fallback-v1", value.recipe_sha256,
        H["5"], H["6"], (changed(),) if sources is None else sources,
    )


def compile_files() -> tuple[CompileFileProof, ...]:
    return (CompileFileProof("test.mdl", ".mdl", 10, H["7"]),)


def structural(
    comp: CompositionProof, files: tuple[CompileFileProof, ...], *, passed: bool = True,
) -> StructuralAuthorizationEvidence:
    failures = () if passed else (
        GateFailure("structural", "candidate", 1.0, 0.0, "failed"),
    )
    validation = ValidationResult(passed, failures, {})
    raw = {
        "candidate_cache_digest": H["8"],
        "composition_evidence_sha256": comp.evidence_sha256,
        "compile_manifest_sha256": compile_manifest_sha256(files),
        "fingerprint_sha256": H["9"],
        "validation": validation,
        "evidence_sha256": H["f"],
    }
    provisional = object.__new__(StructuralAuthorizationEvidence)
    for name, item in raw.items():
        object.__setattr__(provisional, name, item)
    raw["evidence_sha256"] = digest(
        structural_authorization_evidence_payload(provisional, include_seal=False)
    )
    return StructuralAuthorizationEvidence(**raw)


def direct_record(
    *, source_identity: str = "body.smd", coverage: str = H["9"],
    passed: bool = True,
):
    target = build_adaptive_direct_source_union_target(
        source_proof=coverage_source(source_identity),
        coverage_manifest_sha256=coverage,
    )
    files = tuple(sorted((
        RenderFileProof(
            side, "image",
            f"source-union/{target.union_key}/{side}/bind/{render_pass}/camera-{camera:02d}.png",
            1, H["1"], 1, 1,
        )
        for side in ("candidate", "reference")
        for render_pass in ("clay", "textured")
        for camera in range(8)
    ), key=lambda item: item.path))
    failures = () if passed else (
        GateFailure("focused", source_identity, 1.0, 0.0, "failed"),
    )
    return build_adaptive_direct_source_union_record(
        target=target, validation=ValidationResult(passed, failures), files=files,
        visibility=(("component-000", "bind", "camera-00", 1, 1),),
    )


def base_record(root: Path, *, passed: bool = True):
    target = _target()
    reference = root / "reference"
    candidate = root / "candidate"
    _write_render_side(reference)
    _write_render_side(candidate)
    payload = _cache_payload()
    validation = ValidationResult(passed, metrics=_validation_metrics(0.0))
    return build_focused_render_evidence(
        target, validation, payload["expected"],
        _render_file_proofs(reference, candidate), _material_proof()["digest"], False,
    )


class AdaptiveDirectEvidenceTests(unittest.TestCase):
    def test_authorized_round_trip_uses_exact_keys_and_seals_nested_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rec = recipe()
            comp = composition(rec)
            files = compile_files()
            struct = structural(comp, files)
            base = (base_record(Path(temporary)),)
            direct = (direct_record(),)
            final = build_final_whole_authorization_evidence(
                "recovery-" + rec.recipe_sha256, struct.candidate_cache_digest,
                rec.recipe_sha256, comp.evidence_sha256,
                struct.compile_manifest_sha256, "logs/whole-visual-index.json",
                H["a"], H["b"], ValidationResult(True),
            )
            evidence = build_adaptive_direct_evidence(
                terminal_status="authorized", recipe=rec, composition=comp,
                changed_sources=(changed(),), compile_files=files, structural=struct,
                base_focus_records=base, direct_focus_records=direct,
                final_whole=final,
            )
            payload = adaptive_direct_evidence_payload(evidence)
            self.assertEqual(adaptive_direct_evidence_from_payload(payload), evidence)
            self.assertEqual(set(payload), {
                "schema", "kind", "round_index", "terminal_status", "recipe",
                "coverage_manifest_sha256", "composition", "changed_sources",
                "compile_files", "structural", "base_focus_records",
                "direct_focus_records", "final_whole", "evidence_sha256",
            })
            for field in tuple(payload):
                hostile = adaptive_direct_evidence_payload(evidence)
                hostile[field] = None
                with self.subTest(field=field), self.assertRaises((TypeError, ValueError)):
                    adaptive_direct_evidence_from_payload(hostile)
            with self.assertRaises(ValueError):
                adaptive_direct_evidence_from_payload({**payload, "extra": None})

    def test_exact_round_kind_coverage_and_changed_source_bindings(self) -> None:
        rec = recipe()
        comp = composition(rec)
        files = compile_files()
        struct = structural(comp, files)
        evidence = build_adaptive_direct_evidence(
            terminal_status="structural_failed", recipe=rec, composition=comp,
            changed_sources=(changed(),), compile_files=files,
            structural=structural(comp, files, passed=False),
            base_focus_records=(), direct_focus_records=(), final_whole=None,
        )
        with self.assertRaises(ValueError):
            replace(evidence, schema=True)
        with self.assertRaises(ValueError):
            replace(evidence, kind="focused-recovery-v1")
        with self.assertRaises(ValueError):
            replace(evidence, round_index=1)
        with self.assertRaises(ValueError):
            replace(evidence, coverage_manifest_sha256=H["e"])
        with self.assertRaises(ValueError):
            build_adaptive_direct_evidence(
                terminal_status="structural_failed", recipe=rec,
                composition=comp, changed_sources=(), compile_files=files,
                structural=struct, base_focus_records=(), direct_focus_records=(),
                final_whole=None,
            )

    def test_terminal_optional_matrix_fails_closed(self) -> None:
        rec = recipe()
        comp = composition(rec)
        files = compile_files()
        struct = structural(comp, files)
        valid = build_adaptive_direct_evidence(
            terminal_status="compile_failed", recipe=rec, composition=comp,
            changed_sources=(changed(),), compile_files=(), structural=None,
            base_focus_records=(), direct_focus_records=(), final_whole=None,
        )
        with self.assertRaises(ValueError):
            replace(valid, compile_files=files)
        with self.assertRaises(ValueError):
            build_adaptive_direct_evidence(
                terminal_status="focused_failed", recipe=rec, composition=comp,
                changed_sources=(changed(),), compile_files=files, structural=struct,
                base_focus_records=(), direct_focus_records=(), final_whole=None,
            )

    def test_nested_seals_and_direct_source_order_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rec = recipe()
            sources = (
                changed("body.smd"),
                changed("wheel.smd", after=H["d"]),
            )
            comp = composition(rec, sources)
            files = compile_files()
            struct = structural(comp, files)
            base = (base_record(Path(temporary)),)
            direct = (
                direct_record(source_identity="body.smd"),
                direct_record(source_identity="wheel.smd", passed=False),
            )
            evidence = build_adaptive_direct_evidence(
                terminal_status="focused_failed", recipe=rec, composition=comp,
                changed_sources=sources, compile_files=files, structural=struct,
                base_focus_records=base, direct_focus_records=direct,
                final_whole=None,
            )
            with self.assertRaises(ValueError):
                replace(evidence, direct_focus_records=tuple(reversed(direct)))

            payload = adaptive_direct_evidence_payload(evidence)
            payload["direct_focus_records"][0]["evidence_sha256"] = H["e"]
            with self.assertRaises(ValueError):
                adaptive_direct_evidence_from_payload(payload)

            payload = adaptive_direct_evidence_payload(evidence)
            payload["base_focus_records"][0]["validation"]["extra"] = None
            with self.assertRaises(ValueError):
                adaptive_direct_evidence_from_payload(payload)

    def test_direct_coverage_and_final_status_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rec = recipe()
            comp = composition(rec)
            files = compile_files()
            struct = structural(comp, files)
            base = (base_record(Path(temporary)),)
            wrong_coverage = (direct_record(coverage=H["e"]),)
            with self.assertRaises(ValueError):
                build_adaptive_direct_evidence(
                    terminal_status="focused_failed", recipe=rec, composition=comp,
                    changed_sources=(changed(),), compile_files=files, structural=struct,
                    base_focus_records=base, direct_focus_records=wrong_coverage,
                    final_whole=None,
                )

            direct = (direct_record(),)
            failure = ValidationResult(False, (
                GateFailure("whole", "candidate", 1.0, 0.0, "failed"),
            ))
            final = build_final_whole_authorization_evidence(
                "recovery-" + rec.recipe_sha256, struct.candidate_cache_digest,
                rec.recipe_sha256, comp.evidence_sha256,
                struct.compile_manifest_sha256, "logs/whole-visual-index.json",
                H["a"], H["b"], failure,
            )
            failed = build_adaptive_direct_evidence(
                terminal_status="final_whole_failed", recipe=rec, composition=comp,
                changed_sources=(changed(),), compile_files=files, structural=struct,
                base_focus_records=base, direct_focus_records=direct,
                final_whole=final,
            )
            with self.assertRaises(ValueError):
                replace(failed, terminal_status="authorized")


if __name__ == "__main__":
    unittest.main()
