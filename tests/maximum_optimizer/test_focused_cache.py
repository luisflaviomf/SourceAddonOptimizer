from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from PIL import Image


HASHES = {letter: letter * 64 for letter in "abcdef1234567890"}


def _limits(value: float) -> dict[str, float]:
    from maximum_optimizer.visual_validation import REQUIRED_METRICS

    return {name: value for name in REQUIRED_METRICS}


def _validation_metrics(value: float) -> dict[str, float]:
    metrics = _limits(value)
    metrics["fidelity_score"] = max(0.0, 1.0 - value)
    return metrics


def _material_proof(*, cacheable: bool = True) -> dict:
    payload = {
        "schema": 1,
        "cacheable": cacheable,
        "reason": "ok" if cacheable else "file-limit",
        "roots": [{
            "root_index": 0,
            "root_identity": "materials-root-0",
            "inventory_sha256": HASHES["a"] if cacheable else None,
        }],
        "requests": [{
            "request_index": 0,
            "material_identity": "vehicles/body",
            "search_paths": ["vehicles"],
        }],
        "files": ([{
            "root_index": 0,
            "path": "vehicles/body.vmt",
            "kind": "vmt",
            "size": 10,
            "sha256": HASHES["b"],
        }, {
            "root_index": 0,
            "path": "vehicles/body.vtf",
            "kind": "vtf",
            "size": 20,
            "sha256": HASHES["c"],
        }] if cacheable else []),
        "resolutions": ([{
            "request_index": 0,
            "material_identity": "vehicles/body",
            "state": "resolved",
            "root_index": 0,
            "search_path_index": 0,
            "vmt_path": "vehicles/body.vmt",
            "vmt_sha256": HASHES["b"],
            "vtf_root_index": 0,
            "vtf_path": "vehicles/body.vtf",
            "vtf_sha256": HASHES["c"],
            "shader": "vertexlitgeneric",
            "texture_directive": "$basetexture",
            "duplicate_root_directives": [],
        }] if cacheable else []),
        "total_files": 2 if cacheable else 4096,
        "total_bytes": 30 if cacheable else 30,
    }
    from maximum_optimizer.reporting import canonical_json
    import hashlib

    if cacheable:
        payload["roots"][0]["inventory_sha256"] = hashlib.sha256(
            canonical_json({
                "root_index": 0,
                "root_identity": "materials-root-0",
                "files": payload["files"],
            }).encode("utf-8")
        ).hexdigest()
    payload["digest"] = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    return payload


def _cache_payload() -> dict:
    return {
        "schema": 1,
        "family_input_sha256": HASHES["a"],
        "candidate_cache_digest": HASHES["b"],
        "source_pairs": [["body.smd", HASHES["c"], HASHES["d"]]],
        "region_descriptor": {
            "descriptor_schema": "maximum-region-descriptor-v1",
            "local_ordinal": 0,
            "materials": ["paint"],
            "object_name": "body",
            "source_identity": "body.smd",
        },
        "target": {
            "rank": 0,
            "region_key": "r-" + HASHES["e"],
            "source_identity": "body.smd",
            "state_index": 0,
            "state_name": "engine-default",
            "bodygroups": [["000:body", 0]],
            "lod_index": 0,
            "anchor_pose": "bind",
            "surface_bidirectional_p95": 0.01,
            "surface_max": 0.02,
            "normalized_p95": 0.1,
            "normalized_max": 0.2,
            "selector_input_sha256": HASHES["f"],
        },
        "state": {
            "state_index": 0,
            "state_name": "engine-default",
            "bodygroups": [["000:body", 0]],
            "lod_index": 0,
            "poses": ["bind"],
            "selected_pose": "bind",
            "selected_frame": 0,
            "animation_state": "none",
            "animation_sha256": None,
        },
        "region_manifest_sha256": HASHES["1"],
        "configuration_manifest_sha256": HASHES["2"],
        "whole_profile": {
            "version": "whole-v1", "corpus_hash": HASHES["3"],
            "profile_file_sha256": HASHES["4"], "limits": _limits(0.1),
        },
        "focused_profile": {
            "version": "focused-v1", "corpus_hash": HASHES["3"],
            "profile_file_sha256": HASHES["4"], "limits": _limits(0.05),
        },
        "trusted_evidence_v3_sha256": (
            "2cc6b330ef97466f4d10986787f2ffd0d35f960c0bd47a32e0159f9559c6615c"
        ),
        "selector_version": "surface-risk-top-k-v1",
        "renderer_version": "focus-render-v1",
        "dependency_proof_sha256": HASHES["5"],
        "material_proof": _material_proof(),
        "expected": {
            "region_key": "r-" + HASHES["e"],
            "poses": ["bind"],
            "passes": ["textured", "clay"],
            "angles": ["front", "back", "left", "right", "top", "bottom", "iso1", "iso2"],
            "width": 64,
            "height": 64,
            "reference_count": 16,
            "candidate_count": 16,
        },
    }


class FocusCacheKeyTests(unittest.TestCase):
    def test_typed_context_round_trips_to_the_exact_key_payload_immutably(self):
        from maximum_optimizer.focused_cache import (
            FocusCacheContext, FocusCacheKey, FocusExpectedMatrix,
            FocusProfileProof, FocusStateProof,
        )
        from maximum_optimizer.regions import RegionDescriptor

        raw = _cache_payload()
        context = FocusCacheContext(
            1, raw["family_input_sha256"], raw["candidate_cache_digest"],
            tuple(tuple(item) for item in raw["source_pairs"]),
            RegionDescriptor("body.smd", "body", ("paint",), 0),
            _target(),
            FocusStateProof(
                0, "engine-default", (("000:body", 0),), 0, ("bind",),
                "bind", 0, "none", None,
            ),
            raw["region_manifest_sha256"], raw["configuration_manifest_sha256"],
            FocusProfileProof("whole-v1", HASHES["3"], HASHES["4"], _limits(0.1)),
            FocusProfileProof("focused-v1", HASHES["3"], HASHES["4"], _limits(0.05)),
            raw["trusted_evidence_v3_sha256"], raw["selector_version"],
            raw["renderer_version"], raw["dependency_proof_sha256"],
            raw["material_proof"],
            FocusExpectedMatrix(
                raw["expected"]["region_key"], ("bind",),
                tuple(raw["expected"]["passes"]), tuple(raw["expected"]["angles"]),
                64, 64, 16, 16,
            ),
        )
        payload = context.to_payload()
        self.assertEqual(FocusCacheKey.build(payload), FocusCacheKey.build(raw))
        with self.assertRaises(TypeError):
            payload["expected"]["width"] = 128

    def test_key_is_canonical_and_every_required_dimension_is_bound(self):
        from maximum_optimizer.focused_cache import FocusCacheKey

        base = _cache_payload()
        baseline = FocusCacheKey.build(base)
        reordered = {key: base[key] for key in reversed(tuple(base))}
        self.assertEqual(FocusCacheKey.build(reordered), baseline)

        mutations = (
            lambda item: item.update(family_input_sha256=HASHES["6"]),
            lambda item: item.update(candidate_cache_digest=HASHES["6"]),
            lambda item: item["source_pairs"][0].__setitem__(2, HASHES["6"]),
            lambda item: item["region_descriptor"].update(object_name="hood"),
            lambda item: item["target"].update(normalized_p95=0.11),
            lambda item: item["state"].update(selected_frame=1),
            lambda item: item.update(region_manifest_sha256=HASHES["6"]),
            lambda item: item.update(configuration_manifest_sha256=HASHES["6"]),
            lambda item: item["whole_profile"].update(version="whole-v2"),
            lambda item: item["focused_profile"]["limits"].update(edge_error=0.04),
            lambda item: item.update(selector_version="selector-v2"),
            lambda item: item.update(renderer_version="renderer-v2"),
            lambda item: item.update(dependency_proof_sha256=HASHES["6"]),
            self._change_material,
            lambda item: item["expected"].update(width=128),
        )
        for index, mutate in enumerate(mutations):
            changed = copy.deepcopy(base)
            mutate(changed)
            with self.subTest(index=index):
                self.assertNotEqual(FocusCacheKey.build(changed), baseline)

    @staticmethod
    def _change_material(item):
        import hashlib
        from maximum_optimizer.reporting import canonical_json

        proof = item["material_proof"]
        proof["files"][0]["sha256"] = HASHES["6"]
        proof["resolutions"][0]["vmt_sha256"] = HASHES["6"]
        proof["roots"][0]["inventory_sha256"] = hashlib.sha256(
            canonical_json({
                "root_index": 0,
                "root_identity": proof["roots"][0]["root_identity"],
                "files": proof["files"],
            }).encode("utf-8")
        ).hexdigest()
        unsealed = dict(proof)
        unsealed.pop("digest")
        proof["digest"] = hashlib.sha256(
            canonical_json(unsealed).encode("utf-8")
        ).hexdigest()

    def test_key_rejects_non_exact_noncanonical_or_unsafe_payloads(self):
        from maximum_optimizer.focused_cache import FocusCacheKey

        mutations = (
            lambda item: item.update(extra=True),
            lambda item: item.pop("renderer_version"),
            lambda item: item.update(trusted_evidence_v3_sha256=HASHES["6"]),
            lambda item: item["expected"].update(width=True),
            lambda item: item["focused_profile"]["limits"].update(edge_error=math.inf),
            lambda item: item["source_pairs"].append(item["source_pairs"][0]),
            lambda item: item["state"]["bodygroups"].insert(0, ["zzz", 0]),
            lambda item: item.update(material_proof=_material_proof(cacheable=False)),
            self._forge_material_resolution,
            lambda item: item.update(renderer_version=object()),
        )
        for index, mutate in enumerate(mutations):
            changed = copy.deepcopy(_cache_payload())
            mutate(changed)
            with self.subTest(index=index), self.assertRaises((TypeError, ValueError)):
                FocusCacheKey.build(changed)

    @staticmethod
    def _forge_material_resolution(item):
        import hashlib
        from maximum_optimizer.reporting import canonical_json

        proof = item["material_proof"]
        proof["resolutions"][0]["vtf_sha256"] = HASHES["6"]
        unsealed = dict(proof); unsealed.pop("digest")
        proof["digest"] = hashlib.sha256(
            canonical_json(unsealed).encode("utf-8")
        ).hexdigest()


class MaterialResolutionProofTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.first = self.base / "first"
        self.second = self.base / "second"
        (self.first / "vehicles").mkdir(parents=True)
        (self.second / "textures").mkdir(parents=True)
        (self.first / "vehicles/body.vmt").write_text(
            'VertexLitGeneric { "$basetexture" "textures/body" }',
            encoding="utf-8",
        )
        (self.second / "textures/body.vtf").write_bytes(b"texture-bytes")
        self.roots = (("first", self.first), ("second", self.second))
        self.requests = ({
            "material_identity": "body",
            "search_paths": ("vehicles",),
        },)

    def test_material_proof_matches_renderer_priority_and_is_deterministic(self):
        from maximum_optimizer.focused_cache import material_resolution_proof

        first = material_resolution_proof(self.roots, self.requests, threading.Event())
        second = material_resolution_proof(self.roots, self.requests, threading.Event())

        self.assertTrue(first.cacheable)
        self.assertEqual(first.reason, "ok")
        self.assertEqual(first, second)
        self.assertEqual(first.total_files, 2)
        expected_bytes = sum(
            path.stat().st_size
            for path in (self.first / "vehicles/body.vmt", self.second / "textures/body.vtf")
        )
        self.assertEqual(first.total_bytes, expected_bytes)
        resolution = first.resolutions[0]
        self.assertEqual(resolution.root_index, 0)
        self.assertEqual(resolution.search_path_index, 0)
        self.assertEqual(resolution.vtf_root_index, 1)
        self.assertEqual(resolution.vmt_path, "vehicles/body.vmt")
        self.assertEqual(resolution.vtf_path, "textures/body.vtf")

        (self.second / "vehicles").mkdir()
        (self.second / "vehicles/body.vmt").write_text(
            'VertexLitGeneric { "$basetexture" "textures/body" }', encoding="utf-8"
        )
        changed = material_resolution_proof(self.roots, self.requests, threading.Event())
        self.assertNotEqual(changed.digest, first.digest)
        self.assertEqual(changed.resolutions[0].root_index, 0)

    def test_resolution_is_derived_from_sealed_vmt_bytes_during_swap_and_restore(self):
        from maximum_optimizer import focused_cache
        import render_previews

        original_resolver = render_previews._source_material_files

        original_bytes = (self.first / "vehicles/body.vmt").read_bytes()

        def swap_resolve_restore(*args, **kwargs):
            (self.first / "vehicles/body.vmt").write_text(
                'UnlitGeneric { "$basetexture" "textures/body" }', encoding="utf-8"
            )
            try:
                return original_resolver(*args, **kwargs)
            finally:
                (self.first / "vehicles/body.vmt").write_bytes(original_bytes)

        with mock.patch.object(
            render_previews, "_source_material_files", side_effect=swap_resolve_restore
        ):
            proof = focused_cache.material_resolution_proof(
                self.roots, self.requests, threading.Event()
            )
        self.assertTrue(proof.cacheable)
        self.assertEqual(proof.resolutions[0].shader, "vertexlitgeneric")

    def test_selected_vmt_is_memory_bounded_and_cancellation_after_parse_propagates(self):
        from maximum_optimizer import focused_cache
        from maximum_optimizer.processes import ProcessCancelledError
        import render_previews

        vmt = self.first / "vehicles/body.vmt"
        with mock.patch.object(
            focused_cache, "_MAX_SELECTED_VMT_BYTES", vmt.stat().st_size - 1
        ):
            bounded = focused_cache.material_resolution_proof(
                self.roots, self.requests, threading.Event()
            )
        self.assertFalse(bounded.cacheable)
        self.assertEqual(bounded.reason, "byte-limit")

        second_vmt = self.first / "vehicles/wheel.vmt"
        second_vmt.write_text(
            'VertexLitGeneric { "$basetexture" "textures/body" }', encoding="utf-8"
        )
        requests = self.requests + ({
            "material_identity": "wheel", "search_paths": ("vehicles",),
        },)
        with mock.patch.object(
            focused_cache, "_MAX_CAPTURED_VMT_BYTES",
            vmt.stat().st_size + second_vmt.stat().st_size - 1,
        ):
            aggregate = focused_cache.material_resolution_proof(
                self.roots, requests, threading.Event()
            )
        self.assertFalse(aggregate.cacheable)
        self.assertEqual(aggregate.reason, "byte-limit")

        cancelled = threading.Event()
        original = render_previews._parse_vmt_root

        def parse_then_cancel(text):
            result = original(text)
            cancelled.set()
            return result

        with mock.patch.object(
            render_previews, "_parse_vmt_root", side_effect=parse_then_cancel
        ), self.assertRaises(ProcessCancelledError):
            focused_cache.material_resolution_proof(
                self.roots, self.requests, cancelled
            )

    def test_cancellation_between_material_files_closes_prior_vmt_capture(self):
        from maximum_optimizer import focused_cache
        from maximum_optimizer.processes import ProcessCancelledError

        cancelled = threading.Event()
        captures = []
        original_spool = tempfile.SpooledTemporaryFile
        original_proof = focused_cache._file_proof

        def record_spool(*args, **kwargs):
            stream = original_spool(*args, **kwargs)
            captures.append(stream)
            return stream

        def cancel_after_first_vmt(path, *args, **kwargs):
            result = original_proof(path, *args, **kwargs)
            if Path(path).suffix.casefold() == ".vmt":
                cancelled.set()
            return result

        with mock.patch.object(
            focused_cache.tempfile, "SpooledTemporaryFile", side_effect=record_spool
        ), mock.patch.object(
            focused_cache, "_file_proof", side_effect=cancel_after_first_vmt
        ), self.assertRaises(ProcessCancelledError):
            focused_cache.material_resolution_proof(
                self.roots, self.requests, cancelled
            )
        self.assertTrue(captures)
        self.assertTrue(all(stream.closed for stream in captures))

    def test_resolution_seek_failure_closes_all_vmt_captures(self):
        from maximum_optimizer import focused_cache

        captures = []
        original_spool = tempfile.SpooledTemporaryFile

        class FailingSecondSeek:
            def __init__(self, stream):
                self.stream = stream
                self.seek_count = 0
            def __getattr__(self, name):
                return getattr(self.stream, name)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()
            @property
            def closed(self):
                return self.stream.closed
            def seek(self, *args, **kwargs):
                self.seek_count += 1
                if self.seek_count == 2:
                    raise OSError("injected resolution seek")
                return self.stream.seek(*args, **kwargs)

        def failing_spool(*args, **kwargs):
            stream = FailingSecondSeek(original_spool(*args, **kwargs))
            captures.append(stream)
            return stream

        with mock.patch.object(
            focused_cache.tempfile, "SpooledTemporaryFile", side_effect=failing_spool
        ), self.assertRaisesRegex(OSError, "resolution seek"):
            focused_cache.material_resolution_proof(
                self.roots, self.requests, threading.Event()
            )
        self.assertTrue(captures)
        self.assertTrue(all(stream.closed for stream in captures))

    def test_material_proof_bounds_are_inclusive_and_stop_before_excess_hash(self):
        from maximum_optimizer import focused_cache

        with mock.patch.object(focused_cache, "_MAX_MATERIAL_FILES", 2), mock.patch.object(
            focused_cache, "_MAX_MATERIAL_BYTES", sum(
                path.stat().st_size
                for path in (self.first / "vehicles/body.vmt", self.second / "textures/body.vtf")
            )
        ):
            at_limit = focused_cache.material_resolution_proof(
                self.roots, self.requests, threading.Event()
            )
        self.assertTrue(at_limit.cacheable)

        with mock.patch.object(focused_cache, "_MAX_MATERIAL_FILES", 1), mock.patch.object(
            focused_cache, "_file_proof", wraps=focused_cache._file_proof
        ) as file_proof:
            over_files = focused_cache.material_resolution_proof(
                self.roots, self.requests, threading.Event()
            )
        self.assertFalse(over_files.cacheable)
        self.assertEqual(over_files.reason, "file-limit")
        file_proof.assert_not_called()

        with mock.patch.object(focused_cache, "_MAX_MATERIAL_BYTES", 1), mock.patch.object(
            focused_cache, "_file_proof", wraps=focused_cache._file_proof
        ) as file_proof:
            over_bytes = focused_cache.material_resolution_proof(
                self.roots, self.requests, threading.Event()
            )
        self.assertFalse(over_bytes.cacheable)
        self.assertEqual(over_bytes.reason, "byte-limit")
        file_proof.assert_not_called()

    def test_file_limit_stops_discovery_before_touching_file_4097(self):
        from maximum_optimizer import focused_cache

        first = self.first / "one.vmt"
        second = self.first / "two.vmt"
        first.write_text("one", encoding="utf-8")
        second.write_text("two", encoding="utf-8")
        real_lstat = Path.lstat

        def lstat(path):
            if Path(path) == second:
                raise AssertionError("file beyond discovery bound was touched")
            return real_lstat(path)

        with mock.patch.object(focused_cache, "_MAX_MATERIAL_FILES", 1), mock.patch.object(
            focused_cache, "_is_reparse", return_value=False
        ), mock.patch.object(Path, "lstat", lstat), mock.patch.object(
            focused_cache.os, "walk",
            return_value=iter([(str(self.first), [], ["one.vmt", "two.vmt"])]),
        ):
            proof = focused_cache.material_resolution_proof(
                (("first", self.first),), self.requests, threading.Event()
            )
        self.assertFalse(proof.cacheable)
        self.assertEqual(proof.reason, "file-limit")

    def test_file_hash_honors_remaining_byte_budget_without_excess_read(self):
        from maximum_optimizer import focused_cache

        path = self.first / "growing.vmt"
        path.write_bytes(b"0123456789")
        with self.assertRaisesRegex(ValueError, "byte limit"):
            focused_cache._file_proof(
                path, threading.Event(), max_bytes=9, contained_root=self.first
            )

    def test_material_proof_unsafe_tree_disables_cache_and_cancellation_propagates(self):
        from maximum_optimizer.focused_cache import material_resolution_proof
        from maximum_optimizer.processes import ProcessCancelledError

        with mock.patch("maximum_optimizer.focused_cache._is_reparse", return_value=True):
            unsafe = material_resolution_proof(
                self.roots, self.requests, threading.Event()
            )
        self.assertFalse(unsafe.cacheable)
        self.assertEqual(unsafe.reason, "unsafe-tree")

        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(ProcessCancelledError):
            material_resolution_proof(self.roots, self.requests, cancelled)

    def test_bounded_missing_material_is_a_cacheable_explicit_resolution_state(self):
        from maximum_optimizer.focused_cache import FocusCacheKey, material_resolution_proof
        from maximum_optimizer.reporting import canonical_payload

        (self.second / "textures/body.vtf").unlink()
        proof = material_resolution_proof(self.roots, self.requests, threading.Event())
        self.assertTrue(proof.cacheable)
        self.assertEqual(proof.resolutions[0].state, "missing")

        payload = _cache_payload()
        payload["material_proof"] = canonical_payload(proof)
        self.assertRegex(FocusCacheKey.build(payload).digest, r"^[0-9a-f]{64}$")

    def test_cache_key_rejects_duplicate_material_root_and_request_identities(self):
        import hashlib
        from maximum_optimizer.focused_cache import FocusCacheKey
        from maximum_optimizer.reporting import canonical_json

        def reseal(proof):
            unsealed = dict(proof)
            unsealed.pop("digest", None)
            proof["digest"] = hashlib.sha256(
                canonical_json(unsealed).encode("utf-8")
            ).hexdigest()

        duplicate_request = _cache_payload()
        proof = duplicate_request["material_proof"]
        request = copy.deepcopy(proof["requests"][0])
        request["request_index"] = 1
        resolution = copy.deepcopy(proof["resolutions"][0])
        resolution["request_index"] = 1
        proof["requests"].append(request)
        proof["resolutions"].append(resolution)
        reseal(proof)
        with self.assertRaisesRegex(ValueError, "identity"):
            FocusCacheKey.build(duplicate_request)

        duplicate_root = _cache_payload()
        proof = duplicate_root["material_proof"]
        root = copy.deepcopy(proof["roots"][0])
        root["root_index"] = 1
        root["inventory_sha256"] = hashlib.sha256(canonical_json({
            "root_index": 1,
            "root_identity": root["root_identity"],
            "files": [],
        }).encode("utf-8")).hexdigest()
        proof["roots"].append(root)
        reseal(proof)
        with self.assertRaisesRegex(ValueError, "identity"):
            FocusCacheKey.build(duplicate_root)


def _write_render_side(root: Path) -> None:
    import json

    entries = []
    for render_pass in ("textured", "clay"):
        for angle in ("front", "back", "left", "right", "top", "bottom", "iso1", "iso2"):
            relative = f"{render_pass}/bind/{angle}.png"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(path)
            entries.append({"pass": render_pass, "pose": "bind", "angle": angle, "image": relative})
    (root / "render_manifest.json").write_text(
        json.dumps({"schema": 1, "entries": entries}), encoding="utf-8"
    )


def _render_file_proofs(reference: Path, candidate: Path):
    import hashlib
    from maximum_optimizer.focused_cache import RenderFileProof

    result = []
    for side, root in (("reference", reference), ("candidate", candidate)):
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            kind = "manifest" if relative == "render_manifest.json" else "image"
            result.append(RenderFileProof(
                side, kind, relative, path.stat().st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
                None if kind == "manifest" else 64,
                None if kind == "manifest" else 64,
            ))
    return tuple(result)


class FocusedRenderCacheTests(unittest.TestCase):
    def setUp(self):
        from maximum_optimizer.focused_cache import (
            FocusCacheKey, FocusCacheMetadata, FocusRenderDirectories,
        )

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.reference = self.base / "fresh/reference"
        self.candidate = self.base / "fresh/candidate"
        _write_render_side(self.reference)
        _write_render_side(self.candidate)
        self.payload = _cache_payload()
        self.key = FocusCacheKey.build(self.payload)
        self.metadata = FocusCacheMetadata(
            1, self.payload, self.payload["target"], self.payload["expected"]
        )
        self.directories = FocusRenderDirectories(self.reference, self.candidate)
        self.files = _render_file_proofs(self.reference, self.candidate)

    def test_store_and_lookup_round_trip_exact_private_snapshot(self):
        import json
        from maximum_optimizer.focused_cache import FocusedRenderCache

        cache = FocusedRenderCache(self.base / "cache")
        stored = cache.store(
            self.key, self.directories, self.metadata, self.files, threading.Event()
        )
        self.assertEqual(stored.reference, self.base / "cache" / self.key.digest / "payload/reference")
        marker = json.loads(
            (self.base / "cache" / self.key.digest / "complete.json").read_text()
        )
        self.assertEqual(
            set(marker),
            {"schema", "key_digest", "metadata_sha256", "expected_file_count", "files"},
        )
        metadata = json.loads(
            (self.base / "cache" / self.key.digest / "metadata.json").read_text()
        )
        self.assertNotIn("passed", metadata)
        self.assertNotIn("validation", metadata)

        snapshot = cache.lookup(
            self.key, self.base / "snapshots/one", threading.Event()
        )
        self.assertIsNotNone(snapshot)
        self.assertNotEqual(snapshot.reference, stored.reference)
        self.assertEqual(
            (snapshot.reference / "textured/bind/front.png").read_bytes(),
            (self.reference / "textured/bind/front.png").read_bytes(),
        )

    def test_lookup_reads_control_files_through_no_follow_handles(self):
        from maximum_optimizer import focused_cache

        cache = focused_cache.FocusedRenderCache(self.base / "cache")
        cache.store(self.key, self.directories, self.metadata, self.files, threading.Event())
        original = focused_cache._open_regular_no_follow
        opened = []

        def recording_open(path, *args, **kwargs):
            opened.append(Path(path).name)
            return original(path, *args, **kwargs)

        with mock.patch.object(
            focused_cache, "_open_regular_no_follow", side_effect=recording_open
        ):
            snapshot = cache.lookup(
                self.key, self.base / "snapshots/control-handles", threading.Event()
            )
        self.assertIsNotNone(snapshot)
        self.assertIn("complete.json", opened)
        self.assertIn("metadata.json", opened)

    def test_render_dimensions_are_decoded_from_same_captured_bytes_as_hash(self):
        from maximum_optimizer import focused_cache
        from PIL import Image as PilImage

        original = PilImage.open
        opened = []

        def record_open(source, *args, **kwargs):
            opened.append(source)
            return original(source, *args, **kwargs)

        with mock.patch.object(PilImage, "open", side_effect=record_open):
            focused_cache._render_file_manifest(self.directories, threading.Event())
        self.assertTrue(opened)
        self.assertTrue(all(not isinstance(source, (str, Path)) for source in opened))

    def test_existing_same_key_lock_prevents_unserialized_publication(self):
        from maximum_optimizer import focused_cache

        cache = focused_cache.FocusedRenderCache(self.base / "cache")
        cache.root.mkdir(parents=True)
        lock = cache.root / f"{self.key.digest}.lock"
        original_validate = cache._validate_entry
        injected = False

        def inject_lock_immediately_after_revalidation(key, cancel_event):
            nonlocal injected
            result = original_validate(key, cancel_event)
            if not injected:
                lock.mkdir()
                injected = True
            return result

        with mock.patch.object(
            cache, "_validate_entry", side_effect=inject_lock_immediately_after_revalidation
        ), self.assertRaisesRegex(OSError, "locked"):
            cache.store(self.key, self.directories, self.metadata, self.files, threading.Event())
        self.assertFalse((cache.root / self.key.digest).exists())
        self.assertEqual(tuple(lock.iterdir()), ())

    def test_staging_creation_failure_never_leaks_owned_key_lock(self):
        from maximum_optimizer import focused_cache

        cache = focused_cache.FocusedRenderCache(self.base / "cache")
        original_mkdir = Path.mkdir

        def fail_staging(path, *args, **kwargs):
            if ".tmp-" in Path(path).name:
                raise OSError("injected staging mkdir failure")
            return original_mkdir(path, *args, **kwargs)

        with mock.patch.object(Path, "mkdir", fail_staging), self.assertRaisesRegex(
            OSError, "staging mkdir"
        ):
            cache.store(
                self.key, self.directories, self.metadata, self.files, threading.Event()
            )
        self.assertFalse(tuple((self.base / "cache").glob("*.lock")))

    def test_cleanup_bound_contains_maximum_valid_entry_plus_control_files(self):
        from maximum_optimizer import focused_cache

        root = self.base / "maximum-valid-cache-tree"
        root.mkdir()
        for name in ("reference.bin", "candidate.bin"):
            with (root / name).open("wb") as stream:
                stream.truncate(focused_cache._MAX_RENDER_BYTES_PER_SIDE)
        (root / "metadata.json").write_bytes(b"m")
        (root / "complete.json").write_bytes(b"c")
        files = focused_cache._assert_safe_tree(root)
        self.assertEqual(len(files), 4)

    def test_expected_layout_rejects_extra_before_hash_and_traversal_is_cancelable(self):
        from maximum_optimizer import focused_cache
        from maximum_optimizer.processes import ProcessCancelledError

        extra = self.reference / "hostile.bin"
        extra.write_bytes(b"do-not-hash")
        hashed = []
        original = focused_cache._file_proof

        def record_hash(path, *args, **kwargs):
            hashed.append(Path(path))
            return original(path, *args, **kwargs)

        with mock.patch.object(
            focused_cache, "_file_proof", side_effect=record_hash
        ), self.assertRaisesRegex(ValueError, "unexpected"):
            focused_cache._render_file_manifest(
                self.directories, threading.Event(), expected_files=self.files
            )
        self.assertNotIn(extra, hashed)

        extra.unlink()
        cancelled = threading.Event(); cancelled.set()
        with self.assertRaises(ProcessCancelledError):
            focused_cache._render_file_manifest(
                self.directories, cancelled, expected_files=self.files
            )

    def test_lookup_rejects_extra_missing_corrupt_metadata_and_reparse(self):
        import json
        from maximum_optimizer.focused_cache import FocusedRenderCache

        cache = FocusedRenderCache(self.base / "cache")
        cache.store(self.key, self.directories, self.metadata, self.files, threading.Event())
        final = self.base / "cache" / self.key.digest

        (final / "payload/candidate/extra.png").write_bytes(b"extra")
        self.assertIsNone(cache.lookup(self.key, self.base / "snapshots/extra", threading.Event()))
        (final / "payload/candidate/extra.png").unlink()

        image = final / "payload/candidate/textured/bind/front.png"
        original = image.read_bytes()
        image.write_bytes(b"corrupt")
        self.assertIsNone(cache.lookup(self.key, self.base / "snapshots/corrupt", threading.Event()))
        image.write_bytes(original)

        metadata_path = final / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["passed"] = True
        metadata_path.write_text(json.dumps(metadata))
        self.assertIsNone(cache.lookup(self.key, self.base / "snapshots/metadata", threading.Event()))

        with mock.patch(
            "maximum_optimizer.focused_cache._is_reparse",
            side_effect=lambda path: Path(path).name == "candidate",
        ):
            self.assertIsNone(cache.lookup(
                self.key, self.base / "snapshots/reparse", threading.Event()
            ))

    def test_lookup_treats_huge_sealed_png_header_as_cache_miss(self):
        import hashlib
        import json
        import struct
        import zlib
        from maximum_optimizer.focused_cache import FocusedRenderCache

        cache = FocusedRenderCache(self.base / "cache")
        cache.store(self.key, self.directories, self.metadata, self.files, threading.Event())
        final = self.base / "cache" / self.key.digest
        image = final / "payload/candidate/textured/bind/front.png"
        payload = bytearray(image.read_bytes())
        payload[16:20] = struct.pack(">I", 100_000)
        payload[20:24] = struct.pack(">I", 100_000)
        payload[29:33] = struct.pack(">I", zlib.crc32(payload[12:29]) & 0xFFFFFFFF)
        image.write_bytes(payload)
        marker_path = final / "complete.json"
        marker = json.loads(marker_path.read_text())
        sealed = next(
            item for item in marker["files"]
            if item["path"] == "candidate/textured/bind/front.png"
        )
        sealed["size"] = len(payload)
        sealed["sha256"] = hashlib.sha256(payload).hexdigest()
        marker_path.write_text(json.dumps(marker), encoding="utf-8")

        self.assertIsNone(cache.lookup(
            self.key, self.base / "snapshots/huge-ihdr", threading.Event()
        ))

    def test_store_rejects_unsafe_source_and_cancellation_leaves_no_complete_entry(self):
        from maximum_optimizer.focused_cache import FocusedRenderCache
        from maximum_optimizer.processes import ProcessCancelledError

        cache = FocusedRenderCache(self.base / "cache")
        with mock.patch(
            "maximum_optimizer.focused_cache._is_reparse",
            side_effect=lambda path: Path(path) == self.candidate,
        ), self.assertRaisesRegex(ValueError, "reparse"):
            cache.store(
                self.key, self.directories, self.metadata, self.files, threading.Event()
            )

        cancelled = threading.Event(); cancelled.set()
        with self.assertRaises(ProcessCancelledError):
            cache.store(self.key, self.directories, self.metadata, self.files, cancelled)
        final = self.base / "cache" / self.key.digest
        self.assertFalse((final / "complete.json").exists())

    def test_invalidate_refuses_reparse_and_removes_only_exact_entry(self):
        from maximum_optimizer.focused_cache import FocusedRenderCache

        cache = FocusedRenderCache(self.base / "cache")
        cache.store(self.key, self.directories, self.metadata, self.files, threading.Event())
        final = self.base / "cache" / self.key.digest
        with mock.patch(
            "maximum_optimizer.focused_cache._is_reparse",
            side_effect=lambda path: Path(path) == final,
        ), self.assertRaisesRegex(ValueError, "reparse"):
            cache.invalidate(self.key)
        self.assertTrue(final.exists())
        self.assertTrue(cache.invalidate(self.key))
        self.assertFalse(final.exists())

    def test_promotion_failure_restores_previous_entry_and_marker_is_written_last(self):
        from maximum_optimizer import focused_cache

        cache = focused_cache.FocusedRenderCache(self.base / "cache")
        cache.store(self.key, self.directories, self.metadata, self.files, threading.Event())
        final = self.base / "cache" / self.key.digest
        metadata_path = final / "metadata.json"
        metadata_path.write_bytes(metadata_path.read_bytes() + b"corrupt")
        old_bytes = (final / "complete.json").read_bytes()
        real_replace = focused_cache.os.replace
        events = []

        def replace(source, destination):
            source_path, destination_path = Path(source), Path(destination)
            events.append((source_path.name, destination_path.name))
            if ".tmp-" in source_path.name and destination_path == final:
                raise OSError("injected promotion failure")
            return real_replace(source, destination)

        with mock.patch.object(focused_cache.os, "replace", side_effect=replace):
            with self.assertRaisesRegex(OSError, "injected promotion"):
                cache.store(
                    self.key, self.directories, self.metadata, self.files, threading.Event()
                )
        self.assertEqual((final / "complete.json").read_bytes(), old_bytes)
        self.assertFalse(tuple((self.base / "cache").glob("*.quarantine-*")))
        self.assertTrue(any(".tmp-" in source for source, _destination in events))

    def test_lookup_rejects_entry_changed_during_private_snapshot_copy(self):
        from maximum_optimizer import focused_cache

        cache = focused_cache.FocusedRenderCache(self.base / "cache")
        cache.store(self.key, self.directories, self.metadata, self.files, threading.Event())
        shared = self.base / "cache" / self.key.digest / "payload/reference/clay/bind/back.png"
        real_copy = focused_cache._copy_file_no_follow
        changed = False

        def mutate_then_copy(source, destination, cancel_event, **kwargs):
            nonlocal changed
            if not changed:
                shared.write_bytes(b"changed-before-copy")
                changed = True
            return real_copy(source, destination, cancel_event, **kwargs)

        with mock.patch.object(
            focused_cache, "_copy_file_no_follow", side_effect=mutate_then_copy
        ):
            restored = cache.lookup(
                self.key, self.base / "snapshots/toctou", threading.Event()
            )
        self.assertIsNone(restored)
        self.assertFalse((self.base / "snapshots/toctou").exists())

    def test_store_keeps_valid_concurrent_same_key_winner(self):
        import hashlib
        import json
        import shutil
        from maximum_optimizer import focused_cache

        winner_cache = focused_cache.FocusedRenderCache(self.base / "winner-cache")
        winner_cache.store(
            self.key, self.directories, self.metadata, self.files, threading.Event()
        )
        winner = self.base / "winner-cache" / self.key.digest
        metadata_path = winner / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        marker_path = winner / "complete.json"
        marker = json.loads(marker_path.read_text())
        marker["metadata_sha256"] = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
        marker_path.write_text(json.dumps(marker, indent=2), encoding="utf-8")
        winner_metadata = metadata_path.read_bytes()

        cache = focused_cache.FocusedRenderCache(self.base / "cache")
        final = self.base / "cache" / self.key.digest
        real_write = focused_cache._write_json_fsync

        def publish_winner_after_marker(path, payload):
            real_write(path, payload)
            if Path(path).name == "complete.json" and ".tmp-" in Path(path).parent.name:
                shutil.copytree(winner, final)

        with mock.patch.object(
            focused_cache, "_write_json_fsync", side_effect=publish_winner_after_marker
        ):
            cache.store(
                self.key, self.directories, self.metadata, self.files, threading.Event()
            )
        self.assertEqual((final / "metadata.json").read_bytes(), winner_metadata)

    def test_cancellation_after_promotion_rolls_back_new_entry(self):
        from maximum_optimizer import focused_cache
        from maximum_optimizer.processes import ProcessCancelledError

        cache = focused_cache.FocusedRenderCache(self.base / "cache")
        final = self.base / "cache" / self.key.digest
        cancelled = threading.Event()
        real_replace = focused_cache.os.replace

        def cancel_after_replace(source, destination):
            result = real_replace(source, destination)
            if ".tmp-" in Path(source).name and Path(destination) == final:
                cancelled.set()
            return result

        with mock.patch.object(
            focused_cache.os, "replace", side_effect=cancel_after_replace
        ), self.assertRaises(ProcessCancelledError):
            cache.store(self.key, self.directories, self.metadata, self.files, cancelled)
        self.assertFalse(final.exists())

    def test_valid_winner_appearing_at_quarantine_is_restored(self):
        import hashlib
        import json
        import shutil
        from maximum_optimizer import focused_cache

        winner_cache = focused_cache.FocusedRenderCache(self.base / "winner-gap")
        winner_cache.store(
            self.key, self.directories, self.metadata, self.files, threading.Event()
        )
        winner = self.base / "winner-gap" / self.key.digest
        winner_metadata_path = winner / "metadata.json"
        winner_metadata = json.loads(winner_metadata_path.read_text())
        winner_metadata_path.write_text(json.dumps(winner_metadata, indent=2), encoding="utf-8")
        winner_marker_path = winner / "complete.json"
        winner_marker = json.loads(winner_marker_path.read_text())
        winner_marker["metadata_sha256"] = hashlib.sha256(winner_metadata_path.read_bytes()).hexdigest()
        winner_marker_path.write_text(json.dumps(winner_marker, indent=2), encoding="utf-8")
        expected_bytes = winner_metadata_path.read_bytes()

        cache = focused_cache.FocusedRenderCache(self.base / "cache")
        final = self.base / "cache" / self.key.digest
        real_lexists = focused_cache.os.path.lexists
        injected = False

        def inject_winner(path):
            nonlocal injected
            if Path(path) == final and not injected:
                shutil.copytree(winner, final)
                injected = True
                return True
            return real_lexists(path)

        with mock.patch.object(
            focused_cache.os.path, "lexists", side_effect=inject_winner
        ):
            cache.store(
                self.key, self.directories, self.metadata, self.files, threading.Event()
            )
        self.assertEqual((final / "metadata.json").read_bytes(), expected_bytes)

    def test_cancellation_never_deletes_concurrent_winner_after_promotion(self):
        import hashlib
        import json
        import shutil
        from maximum_optimizer import focused_cache
        from maximum_optimizer.processes import ProcessCancelledError

        winner_cache = focused_cache.FocusedRenderCache(self.base / "winner-after")
        winner_cache.store(
            self.key, self.directories, self.metadata, self.files, threading.Event()
        )
        winner = self.base / "winner-after" / self.key.digest
        winner_metadata_path = winner / "metadata.json"
        winner_metadata = json.loads(winner_metadata_path.read_text())
        winner_metadata_path.write_text(json.dumps(winner_metadata, indent=2), encoding="utf-8")
        winner_marker_path = winner / "complete.json"
        winner_marker = json.loads(winner_marker_path.read_text())
        winner_marker["metadata_sha256"] = hashlib.sha256(winner_metadata_path.read_bytes()).hexdigest()
        winner_marker_path.write_text(json.dumps(winner_marker, indent=2), encoding="utf-8")
        expected_bytes = winner_metadata_path.read_bytes()

        cache = focused_cache.FocusedRenderCache(self.base / "cache")
        final = self.base / "cache" / self.key.digest
        cancelled = threading.Event()
        real_replace = focused_cache.os.replace

        def replace_then_winner(source, destination):
            result = real_replace(source, destination)
            if ".tmp-" in Path(source).name and Path(destination) == final:
                shutil.rmtree(final)
                shutil.copytree(winner, final)
                cancelled.set()
            return result

        with mock.patch.object(
            focused_cache.os, "replace", side_effect=replace_then_winner
        ), self.assertRaises(ProcessCancelledError):
            cache.store(self.key, self.directories, self.metadata, self.files, cancelled)
        self.assertEqual((final / "metadata.json").read_bytes(), expected_bytes)

    def test_file_hash_rejects_symlink_even_if_path_precheck_is_bypassed(self):
        from maximum_optimizer import focused_cache

        target = self.base / "target.bin"; target.write_bytes(b"outside")
        link = self.base / "link.bin"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlink privilege unavailable: {exc}")
        with mock.patch.object(focused_cache, "_is_reparse", return_value=False):
            with self.assertRaises(ValueError):
                focused_cache._file_proof(
                    link, threading.Event(), contained_root=self.base
                )


def _target():
    from maximum_optimizer.domain import FocusTarget

    raw = _cache_payload()["target"]
    return FocusTarget(
        raw["rank"], raw["region_key"], raw["source_identity"],
        raw["state_index"], raw["state_name"],
        tuple(tuple(item) for item in raw["bodygroups"]), raw["lod_index"],
        raw["anchor_pose"], raw["surface_bidirectional_p95"], raw["surface_max"],
        raw["normalized_p95"], raw["normalized_max"], raw["selector_input_sha256"],
    )


def _profile():
    from maximum_optimizer.visual_validation import FidelityProfile

    return FidelityProfile(1, "focused-v1", True, HASHES["3"], _limits(0.05))


class FocusedValidationAndEvidenceTests(unittest.TestCase):
    def setUp(self):
        from maximum_optimizer.domain import FocusedRegionPolicy
        from maximum_optimizer.focused_cache import (
            FocusCacheKey, FocusCacheMetadata, FocusRenderDirectories,
            FocusedEvidenceContext,
        )
        from maximum_optimizer.focused_regions import FocusSelection

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.reference = self.base / "fresh/reference"
        self.candidate = self.base / "fresh/candidate"
        _write_render_side(self.reference)
        _write_render_side(self.candidate)
        self.payload = _cache_payload()
        self.key = FocusCacheKey.build(self.payload)
        self.metadata = FocusCacheMetadata(
            1, self.payload, self.payload["target"], self.payload["expected"]
        )
        self.files = _render_file_proofs(self.reference, self.candidate)
        self.directories = FocusRenderDirectories(self.reference, self.candidate)
        self.target = _target()
        self.selection = FocusSelection(
            self.target.selector_input_sha256, (self.target,), (self.target,)
        )
        self.context = FocusedEvidenceContext(
            1, "family", "candidate", FocusedRegionPolicy(1, "surface-risk-top-k-v1", 1),
            self.payload["whole_profile"], self.payload["focused_profile"],
            self.payload["trusted_evidence_v3_sha256"],
            self.payload["dependency_proof_sha256"],
            {self.target.region_key: self.payload["material_proof"]},
        )

    def test_cache_hit_always_recompares_and_cached_diagnostics_cannot_authorize(self):
        from maximum_optimizer.domain import GateFailure, ValidationResult
        from maximum_optimizer.focused_cache import (
            FocusedRenderCache, validate_focused_target,
        )

        cache = FocusedRenderCache(self.base / "cache")
        cache.store(self.key, self.directories, self.metadata, self.files, threading.Event())
        failed_metrics = _validation_metrics(0.0)
        failed_metrics["edge_error"] = 0.2
        failed_metrics["fidelity_score"] = 0.0
        failed = ValidationResult(
            False, (GateFailure("edge_error", "scope", 0.2, 0.05, "failed"),),
            failed_metrics, "scope",
        )
        comparator = mock.Mock(return_value=failed)
        render_fresh = mock.Mock(side_effect=AssertionError("hit must not rerender"))

        result, record = validate_focused_target(
            cache, self.key, self.target, _profile(), render_fresh,
            self.base / "snapshots/hit", self.metadata, (),
            threading.Event(), comparator=comparator,
        )

        self.assertFalse(result.validation.passed)
        self.assertTrue(result.cache_hit)
        self.assertTrue(record.cache_hit)
        comparator.assert_called_once()
        render_fresh.assert_not_called()

    def test_fresh_render_recompares_once_and_cache_store_is_not_authorization(self):
        from maximum_optimizer.domain import FocusRegionResult, ValidationResult
        from maximum_optimizer.focused_cache import (
            FocusedRenderCache, validate_focused_target,
        )

        cache = FocusedRenderCache(self.base / "cache")
        comparator = mock.Mock(return_value=ValidationResult(
            True, metrics=_validation_metrics(0.0)
        ))
        render_fresh = mock.Mock(return_value=self.directories)

        result, record = validate_focused_target(
            cache, self.key, self.target, _profile(), render_fresh,
            self.base / "snapshots/miss", self.metadata, self.files,
            threading.Event(), comparator=comparator,
        )

        self.assertTrue(result.validation.passed)
        self.assertFalse(result.cache_hit)
        self.assertFalse(record.cache_hit)
        comparator.assert_called_once_with(self.reference, self.candidate, _profile())
        render_fresh.assert_called_once_with()
        self.assertTrue((self.base / "cache" / self.key.digest / "complete.json").is_file())

    def test_fresh_render_rejects_resolved_material_hashes_outside_bound_proof(self):
        from maximum_optimizer.domain import ValidationResult
        from maximum_optimizer.focused_cache import (
            FocusedRenderCache, validate_focused_target,
        )

        manifest_path = self.candidate / "render_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["entries"][0]["resolved_materials"] = [{
            "vmt_sha256": "9" * 64,
            "vtf_sha256": self.payload["material_proof"]["resolutions"][0]["vtf_sha256"],
        }]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        current_files = _render_file_proofs(self.reference, self.candidate)

        with self.assertRaisesRegex(ValueError, "material proof|resolved material"):
            validate_focused_target(
                FocusedRenderCache(self.base / "cache"), self.key, self.target,
                _profile(), mock.Mock(return_value=self.directories),
                self.base / "snapshots/material-mutation", self.metadata,
                current_files, threading.Event(),
                comparator=mock.Mock(return_value=ValidationResult(
                    True, metrics=_validation_metrics(0.0)
                )),
            )

    def test_noncacheable_material_proof_renders_fresh_once_without_cache_and_is_auditable(self):
        import hashlib
        from maximum_optimizer.domain import FocusedRegionPolicy, ValidationResult
        from maximum_optimizer.focused_cache import (
            FocusedEvidenceContext, UncachedFocusMetadata,
            focused_gate_evidence_payload, validate_focused_target,
        )
        from maximum_optimizer.reporting import canonical_json

        for reason in ("file-limit", "unsafe-tree"):
            with self.subTest(reason=reason):
                proof = _material_proof(cacheable=False)
                proof["reason"] = reason
                unsealed = dict(proof)
                del unsealed["digest"]
                proof["digest"] = hashlib.sha256(
                    canonical_json(unsealed).encode("utf-8")
                ).hexdigest()
                metadata = UncachedFocusMetadata(
                    self.payload["target"], self.payload["expected"], proof,
                )
                comparator = mock.Mock(return_value=ValidationResult(
                    True, metrics=_validation_metrics(0.0)
                ))
                render_fresh = mock.Mock(return_value=self.directories)

                result, record = validate_focused_target(
                    None, None, self.target, _profile(), render_fresh,
                    self.base / f"snapshots/{reason}", metadata, self.files,
                    threading.Event(), comparator=comparator,
                )

                self.assertTrue(result.validation.passed)
                self.assertFalse(result.cache_hit)
                self.assertFalse(record.cache_hit)
                render_fresh.assert_called_once_with()
                comparator.assert_called_once_with(
                    self.reference, self.candidate, _profile()
                )
                self.assertFalse((self.base / "cache").exists())

                context = FocusedEvidenceContext(
                    1, "family", "candidate", FocusedRegionPolicy(
                        1, "surface-risk-top-k-v1", 1
                    ),
                    self.payload["whole_profile"], self.payload["focused_profile"],
                    self.payload["trusted_evidence_v3_sha256"],
                    self.payload["dependency_proof_sha256"],
                    {self.target.region_key: proof},
                )
                evidence = focused_gate_evidence_payload(
                    context, self.selection, (record,)
                )
                self.assertEqual(
                    evidence["context"]["material_proofs"][self.target.region_key]["reason"],
                    reason,
                )

    def test_validation_rejects_target_not_bound_to_cache_context(self):
        from dataclasses import replace
        from maximum_optimizer.domain import ValidationResult
        from maximum_optimizer.focused_cache import (
            FocusedRenderCache, validate_focused_target,
        )

        forged = replace(self.target, state_index=1, state_name="forged-state")
        comparator = mock.Mock(return_value=ValidationResult(
            True, metrics=_validation_metrics(0.0)
        ))
        with self.assertRaisesRegex(ValueError, "target"):
            validate_focused_target(
                FocusedRenderCache(self.base / "cache"), self.key, forged, _profile(),
                mock.Mock(return_value=self.directories), self.base / "snapshots/forged",
                self.metadata, self.files, threading.Event(), comparator=comparator,
            )
        comparator.assert_not_called()

    def test_validation_rejects_missing_required_metrics(self):
        from maximum_optimizer.domain import ValidationResult
        from maximum_optimizer.focused_cache import build_focused_render_evidence

        with self.assertRaisesRegex(ValueError, "metrics"):
            build_focused_render_evidence(
                self.target, ValidationResult(True, metrics={}),
                self.metadata.expected, self.files,
                self.payload["material_proof"]["digest"], False,
            )

    def test_schema1_evidence_seals_complete_selection_and_rejects_recovery(self):
        from maximum_optimizer.domain import FocusRegionResult, ValidationResult
        from maximum_optimizer.focused_cache import (
            build_focused_render_evidence, focused_gate_evidence_payload,
            validate_focused_gate_evidence_payload,
        )
        from maximum_optimizer.reporting import canonical_json

        validation = ValidationResult(True, metrics=_validation_metrics(0.0))
        record = build_focused_render_evidence(
            self.target, validation, self.metadata.expected, self.files,
            self.payload["material_proof"]["digest"], False,
        )
        evidence = focused_gate_evidence_payload(
            self.context, self.selection, (record,)
        )
        self.assertEqual(
            set(evidence),
            {"schema", "family_id", "candidate_id", "context", "selection", "records",
             "recoveries", "authorization_sha256", "evidence_sha256"},
        )
        self.assertEqual(evidence["recoveries"], ())
        validated = validate_focused_gate_evidence_payload(
            evidence, family_id="family", candidate_id="candidate",
            eligible_targets=self.selection.eligible_ranking,
            targets=(self.target,), regions={
                self.target.region_key: FocusRegionResult(
                    self.target, validation, record.evidence_sha256, False
                )
            },
        )
        self.assertEqual(validated["evidence_sha256"], evidence["evidence_sha256"])

        forged = json.loads(canonical_json(evidence))
        forged["records"][0]["validation"]["worst_scope"] = "forged"
        forged_unsealed = dict(forged)
        del forged_unsealed["evidence_sha256"]
        forged["evidence_sha256"] = hashlib.sha256(
            canonical_json(forged_unsealed).encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "record|authorization"):
            validate_focused_gate_evidence_payload(
                forged, family_id="family", candidate_id="candidate",
                eligible_targets=self.selection.eligible_ranking,
                targets=(self.target,), regions={
                    self.target.region_key: FocusRegionResult(
                        self.target, validation, record.evidence_sha256, False
                    )
                },
            )

        semantic_forgery = json.loads(canonical_json(evidence))
        semantic_forgery["context"]["trusted_evidence_v3_sha256"] = "0" * 64
        authorization_records = []
        for item in semantic_forgery["records"]:
            authorization_record = dict(item)
            del authorization_record["cache_hit"]
            del authorization_record["evidence_sha256"]
            authorization_records.append(authorization_record)
        authorization = {
            "schema": 1, "family_id": "family", "candidate_id": "candidate",
            "context": semantic_forgery["context"],
            "selection": semantic_forgery["selection"],
            "records": authorization_records, "recoveries": [],
        }
        semantic_forgery["authorization_sha256"] = hashlib.sha256(
            canonical_json(authorization).encode("utf-8")
        ).hexdigest()
        semantic_unsealed = dict(semantic_forgery)
        del semantic_unsealed["evidence_sha256"]
        semantic_forgery["evidence_sha256"] = hashlib.sha256(
            canonical_json(semantic_unsealed).encode("utf-8")
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "trusted|evidence"):
            validate_focused_gate_evidence_payload(
                semantic_forgery, family_id="family", candidate_id="candidate",
                eligible_targets=self.selection.eligible_ranking,
                targets=(self.target,), regions={
                    self.target.region_key: FocusRegionResult(
                        self.target, validation, record.evidence_sha256, False
                    )
                },
            )

        cached_record = build_focused_render_evidence(
            self.target, validation, self.metadata.expected, self.files,
            self.payload["material_proof"]["digest"], True,
        )
        cached = focused_gate_evidence_payload(
            self.context, self.selection, (cached_record,)
        )
        self.assertEqual(cached["authorization_sha256"], evidence["authorization_sha256"])
        self.assertNotEqual(cached["evidence_sha256"], evidence["evidence_sha256"])

        with self.assertRaisesRegex(ValueError, "recovery"):
            focused_gate_evidence_payload(
                self.context, self.selection, (record,), recoveries=({"round_index": 0},)
            )
        with self.assertRaisesRegex(ValueError, "cardinality"):
            focused_gate_evidence_payload(self.context, self.selection, ())

    def test_schema2_recovery_context_binds_initial_base_authorization(self):
        from dataclasses import replace
        from maximum_optimizer.focused_cache import FocusedRecoveryContext

        context = FocusedRecoveryContext(2, self.context, HASHES["1"], HASHES["2"])
        self.assertEqual(context.base_context.candidate_id, "candidate")
        with self.assertRaises(ValueError):
            replace(context, schema=1)
        with self.assertRaises(ValueError):
            replace(context, initial_authorization_sha256="A" * 64)

    def test_schema2_requires_nonempty_typed_recovery_sequence(self):
        from maximum_optimizer.focused_cache import (
            FocusedRecoveryContext,
            focused_gate_evidence_payload,
            focused_recovery_evidence_payload,
        )
        from maximum_optimizer.domain import ValidationResult
        from maximum_optimizer.focused_cache import build_focused_render_evidence

        validation = ValidationResult(True, metrics=_validation_metrics(0.0))
        record = build_focused_render_evidence(
            self.target, validation, self.metadata.expected, self.files,
            self.payload["material_proof"]["digest"], False,
        )
        initial = focused_gate_evidence_payload(self.context, self.selection, (record,))
        recovery_context = FocusedRecoveryContext(
            2, self.context, HASHES["1"], initial["authorization_sha256"]
        )
        with self.assertRaisesRegex(ValueError, "non-empty"):
            focused_recovery_evidence_payload(
                recovery_context, self.selection, (record,), ()
            )

    def test_schema2_authorized_round_folds_rerun_and_derives_terminal_candidate(self):
        from maximum_optimizer.domain import (
            ChangedSourceProof, CompileFileProof, CompositeRecipe, CompositionProof,
            FocusRegionResult, FocusedRegionPolicy, SourceOverlay, StructuralAuthorizationEvidence,
            ValidationResult, changed_source_proof_payload, composition_proof_payload,
            source_overlay_payload, structural_authorization_evidence_payload,
        )
        from maximum_optimizer.focused_cache import (
            FocusedEvidenceContext, FocusedRecoveryContext,
            build_final_whole_authorization_evidence,
            build_focused_recovery_evidence, build_focused_render_evidence,
            compile_manifest_sha256, focused_gate_evidence_payload,
            focused_recovery_evidence_payload, validate_focused_gate_evidence_payload,
        )
        from maximum_optimizer.reporting import canonical_json

        def digest(value):
            return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

        validation = ValidationResult(True, metrics=_validation_metrics(0.0))
        record = build_focused_render_evidence(
            self.target, validation, self.metadata.expected, self.files,
            self.payload["material_proof"]["digest"], False,
        )
        base_context = FocusedEvidenceContext(
            1, HASHES["3"], "base", FocusedRegionPolicy(1, "surface-risk-top-k-v1", 1),
            self.payload["whole_profile"], self.payload["focused_profile"],
            self.payload["trusted_evidence_v3_sha256"],
            self.payload["dependency_proof_sha256"],
            {self.target.region_key: self.payload["material_proof"]},
        )
        initial = focused_gate_evidence_payload(base_context, self.selection, (record,))
        overlay = SourceOverlay(
            "body.smd", "exact-original", self.target.region_key,
            HASHES["1"], HASHES["2"], 12, HASHES["4"],
            None, None, None, (), "donors-exhausted-v1",
        )
        recipe_raw = {
            "schema": 1, "kind": "focused-recovery-v1", "family_id": HASHES["3"],
            "family_input_sha256": HASHES["4"], "base_candidate_id": "base",
            "base_spec_sha256": HASHES["5"], "base_cache_digest": HASHES["1"],
            "base_source_manifest_sha256": HASHES["2"],
            "optimizer_contract_sha256": HASHES["6"],
            "whole_profile_sha256": digest(self.payload["whole_profile"]),
            "focused_profile_sha256": digest(self.payload["focused_profile"]),
            "dependency_proof_sha256": self.payload["dependency_proof_sha256"],
            "round_index": 0, "direct_ratio": None,
            "overlays": [source_overlay_payload(overlay)],
            "selector_version": "surface-risk-top-k-v1", "prefilter_version": None,
        }
        recipe_value = CompositeRecipe(
            1, "focused-recovery-v1", HASHES["3"], HASHES["4"], "base",
            HASHES["5"], HASHES["1"], HASHES["2"], HASHES["6"],
            digest(self.payload["whole_profile"]), digest(self.payload["focused_profile"]),
            self.payload["dependency_proof_sha256"], 0, None, (overlay,),
            "surface-risk-top-k-v1", None, digest(recipe_raw),
        )
        changed = ChangedSourceProof(
            "body.smd", "body.smd", 10, HASHES["1"], 12, HASHES["2"],
            digest(source_overlay_payload(overlay)), HASHES["4"],
        )
        composition_raw = {
            "schema": 1, "kind": "focused-recovery-v1", "recipe_sha256": recipe_value.recipe_sha256,
            "base_manifest_sha256": HASHES["2"],
            "composed_manifest_sha256": HASHES["7"],
            "changed_sources": [changed_source_proof_payload(changed)],
        }
        composition = CompositionProof(
            1, recipe_value.recipe_sha256, HASHES["2"], HASHES["7"],
            (changed,), digest(composition_raw),
        )
        compile_files = (CompileFileProof("models/car.mdl", ".mdl", 100, HASHES["8"]),)
        compile_hash = compile_manifest_sha256(compile_files)
        structural_raw = {
            "candidate_cache_digest": HASHES["9"],
            "composition_evidence_sha256": composition.evidence_sha256,
            "compile_manifest_sha256": compile_hash, "fingerprint_sha256": HASHES["a"],
            "validation": {"passed": True, "failures": [], "metrics": {}, "worst_scope": ""},
        }
        structural = StructuralAuthorizationEvidence(
            HASHES["9"], composition.evidence_sha256, compile_hash, HASHES["a"],
            ValidationResult(True), digest(structural_raw),
        )
        terminal_id = "recovery-" + recipe_value.recipe_sha256
        final = build_final_whole_authorization_evidence(
            terminal_id, HASHES["9"], recipe_value.recipe_sha256,
            composition.evidence_sha256, compile_hash, "logs/whole-index.json",
            HASHES["b"], HASHES["c"], validation,
        )
        recovery = build_focused_recovery_evidence(
            0, "authorized", recipe_value, composition, (changed,), (),
            compile_files, structural, (record,), final,
        )
        evidence = focused_recovery_evidence_payload(
            FocusedRecoveryContext(2, base_context, HASHES["1"], initial["authorization_sha256"]),
            self.selection, (record,), (recovery,),
        )
        self.assertEqual(evidence["schema"], 2)
        self.assertEqual(evidence["candidate_id"], terminal_id)
        self.assertEqual(evidence["recoveries"][0]["terminal_status"], "authorized")
        validated = validate_focused_gate_evidence_payload(
            evidence, family_id=HASHES["3"], candidate_id=terminal_id,
            eligible_targets=self.selection.eligible_ranking,
            targets=self.selection.selected,
            regions={self.target.region_key: FocusRegionResult(
                self.target, validation, record.evidence_sha256, False
            )},
            recovery_context=FocusedRecoveryContext(
                2, base_context, HASHES["1"], initial["authorization_sha256"]
            ),
            initial_records=(record,), recoveries=(recovery,),
        )
        self.assertEqual(validated["evidence_sha256"], evidence["evidence_sha256"])

    def test_schema1_evidence_rejects_rank_target_terminal_and_seal_mutations(self):
        from dataclasses import replace
        from maximum_optimizer.domain import ValidationResult
        from maximum_optimizer.focused_cache import (
            build_focused_render_evidence, focused_gate_evidence_payload,
        )
        from maximum_optimizer.focused_regions import FocusSelection

        validation = ValidationResult(True, metrics=_validation_metrics(0.0))
        record = build_focused_render_evidence(
            self.target, validation, self.metadata.expected, self.files,
            self.payload["material_proof"]["digest"], False,
        )
        bad_rank = replace(self.target, rank=1)
        invalid_cases = (
            (FocusSelection(self.target.selector_input_sha256, (bad_rank,), (bad_rank,)), (record,)),
            (self.selection, (replace(record, terminal_status="failed"),)),
            (self.selection, (replace(record, evidence_sha256=HASHES["0"]),)),
        )
        for selection, records in invalid_cases:
            with self.subTest(selection=selection, records=records), self.assertRaises(ValueError):
                focused_gate_evidence_payload(self.context, selection, records)

    def test_schema1_revalidates_self_resealed_record_internal_consistency(self):
        from dataclasses import replace
        from maximum_optimizer.domain import ValidationResult
        from maximum_optimizer import focused_cache

        validation = ValidationResult(True, metrics=_validation_metrics(0.0))
        record = focused_cache.build_focused_render_evidence(
            self.target, validation, self.metadata.expected, self.files,
            self.payload["material_proof"]["digest"], False,
        )
        forged = replace(record, reference_manifest_sha256=HASHES["9"])
        forged = replace(forged, evidence_sha256=focused_cache._record_digest(forged))
        with self.assertRaisesRegex(ValueError, "manifest"):
            focused_cache.focused_gate_evidence_payload(
                self.context, self.selection, (forged,)
            )

    def test_schema1_rejects_self_resealed_pass_above_focused_profile_limit(self):
        from dataclasses import replace
        from maximum_optimizer.domain import ValidationResult
        from maximum_optimizer import focused_cache

        record = focused_cache.build_focused_render_evidence(
            self.target, ValidationResult(True, metrics=_validation_metrics(0.0)),
            self.metadata.expected, self.files,
            self.payload["material_proof"]["digest"], False,
        )
        forged_metrics = _validation_metrics(0.0)
        forged_metrics["edge_error"] = 999.0
        forged = replace(
            record, validation=ValidationResult(True, metrics=forged_metrics)
        )
        forged = replace(forged, evidence_sha256=focused_cache._record_digest(forged))
        with self.assertRaisesRegex(ValueError, "profile"):
            focused_cache.focused_gate_evidence_payload(
                self.context, self.selection, (forged,)
            )


if __name__ == "__main__":
    unittest.main()
