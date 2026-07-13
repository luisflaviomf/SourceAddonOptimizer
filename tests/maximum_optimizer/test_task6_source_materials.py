from __future__ import annotations

import tempfile
import threading
import unittest
import hashlib
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest import mock

from maximum_optimizer.reporting import canonical_json
from maximum_optimizer.processes import ProcessCancelledError
from maximum_optimizer.visual_validation import MATERIAL_EVIDENCE_FIELDS
from maximum_optimizer.source_materials import (
    build_source_union_material_contract,
    materialize_private_source_union_material_roots,
    require_current_source_union_material_contract,
    source_union_material_contract_from_payload,
    source_union_material_contract_payload,
    source_union_material_render_evidence,
)
from tests.maximum_optimizer.test_task6_direct_compositor import _smd


def _multi_material_smd(materials: tuple[str, ...]) -> bytes:
    rows = []
    for triangle, material in enumerate(materials):
        x = triangle * 2
        rows.append(
            f"{material}\n"
            f"0 {x} 0 0 0 0 1 0 0\n"
            f"0 {x + 1} 0 0 0 0 1 1 0\n"
            f"0 {x} 1 0 0 0 1 0 1\n"
        )
    return (
        'version 1\nnodes\n0 "root" -1\nend\nskeleton\ntime 0\n'
        '0 0 0 0 0 0 0\nend\ntriangles\n' + "".join(rows) + "end\n"
    ).encode("utf-8")


class SourceUnionMaterialContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.first = self.root / "first"; self.second = self.root / "second"
        (self.first / "vehicles").mkdir(parents=True)
        (self.second / "textures").mkdir(parents=True)
        (self.first / "vehicles/paint.vmt").write_text(
            'VertexLitGeneric { "$basetexture" "textures/paint" }',
            encoding="utf-8",
        )
        (self.second / "textures/paint.vtf").write_bytes(b"paint texture")
        self.roots = (self.first, self.second)
        self.requests = ({
            "material_region_key": "paint-region",
            "smd_material": "paint",
            "search_paths": ("vehicles",),
        },)
        self.filtered = _smd(2, "paint")

    def build(self):
        return build_source_union_material_contract(
            source_identity="meshes/body.smd",
            filtered_source_bytes=self.filtered,
            requests=self.requests,
            roots=self.roots,
            cancel_event=threading.Event(),
        )

    def test_targeted_contract_is_deterministic_exact_and_round_trips(self) -> None:
        first = self.build(); second = self.build()
        self.assertEqual(first, second)
        self.assertEqual(first.bindings[0].root_index, 0)
        self.assertEqual(first.bindings[0].search_path_index, 0)
        self.assertEqual(first.bindings[0].vtf_root_index, 1)
        self.assertEqual(len(first.files), 2)
        payload = source_union_material_contract_payload(first)
        self.assertEqual(source_union_material_contract_from_payload(payload), first)
        with self.assertRaisesRegex(ValueError, "fields"):
            source_union_material_contract_from_payload({**payload, "hidden": True})

    def test_unrelated_file_does_not_change_contract_but_new_precedence_shadow_does(self) -> None:
        contract = self.build()
        (self.first / "unrelated.vmt").write_text("ignored", encoding="utf-8")
        self.assertEqual(self.build(), contract)
        (self.first / "textures").mkdir()
        (self.first / "textures/paint.vtf").write_bytes(b"higher priority")
        with self.assertRaisesRegex(ValueError, "current material"):
            require_current_source_union_material_contract(
                contract, filtered_source_bytes=self.filtered,
                roots=self.roots, cancel_event=threading.Event(),
            )

    def test_same_size_vmt_or_vtf_mutation_is_rejected(self) -> None:
        for relative in ("vehicles/paint.vmt", "textures/paint.vtf"):
            with self.subTest(relative=relative):
                contract = self.build()
                base = self.first if relative.endswith(".vmt") else self.second
                path = base / relative; data = bytearray(path.read_bytes())
                data[len(data) // 2] ^= 1; path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "current material"):
                    require_current_source_union_material_contract(
                        contract, filtered_source_bytes=self.filtered,
                        roots=self.roots, cancel_event=threading.Event(),
                    )
                if relative.endswith(".vmt"):
                    path.write_text(
                        'VertexLitGeneric { "$basetexture" "textures/paint" }',
                        encoding="utf-8",
                    )
                else:
                    path.write_bytes(b"paint texture")

    def test_private_roots_contain_only_selected_bytes_and_revalidate(self) -> None:
        contract = self.build(); (self.first / "ignored.vtf").write_bytes(b"ignored")
        destination = self.root / "private"
        private = materialize_private_source_union_material_roots(
            contract, self.roots, destination, threading.Event(),
            filtered_source_bytes=self.filtered,
        )
        self.assertEqual(len(private), 2)
        self.assertEqual(
            tuple(sorted(path.relative_to(destination).as_posix()
                         for path in destination.rglob("*") if path.is_file())),
            ("root-000/vehicles/paint.vmt", "root-001/textures/paint.vtf"),
        )
        require_current_source_union_material_contract(
            contract, filtered_source_bytes=self.filtered,
            roots=private, cancel_event=threading.Event(),
        )

    def test_requests_must_cover_exact_smd_material_order(self) -> None:
        for requests in (
            (), ({**self.requests[0], "smd_material": "wrong"},),
            ({**self.requests[0], "search_paths": "vehicles"},),
        ):
            with self.subTest(requests=requests), self.assertRaises(ValueError):
                build_source_union_material_contract(
                    source_identity="meshes/body.smd",
                    filtered_source_bytes=self.filtered, requests=requests,
                    roots=self.roots, cancel_event=threading.Event(),
                )

    def test_render_evidence_preserves_alpha_and_duplicate_directives(self) -> None:
        (self.first / "vehicles/paint.vmt").write_text(
            'Refract { "$refracttinttexture" "textures/paint" '
            '"$translucent" "1" "$translucent" "0" }',
            encoding="utf-8",
        )
        contract = self.build()
        authorization = require_current_source_union_material_contract(
            contract, filtered_source_bytes=self.filtered,
            roots=self.roots, cancel_event=threading.Event(),
        )
        self.assertIsNotNone(authorization)
        with self.assertRaises(TypeError):
            source_union_material_render_evidence(contract)
        evidence = source_union_material_render_evidence(authorization)
        self.assertEqual(evidence[0]["shader"], "refract")
        self.assertTrue(evidence[0]["uses_texture_alpha"])
        self.assertEqual(
            evidence[0]["duplicate_root_directives"],
            [{"directive": "$translucent", "ignored_values": ["0"]}],
        )

    def test_exact_backslash_smd_spelling_round_trips_and_materializes(self) -> None:
        filtered = _smd(1, "vehicles\\paint")
        requests = ({
            "material_region_key": "paint-region",
            "smd_material": "vehicles\\paint",
            "search_paths": (),
        },)
        contract = build_source_union_material_contract(
            source_identity="meshes/body.smd", filtered_source_bytes=filtered,
            requests=requests, roots=self.roots, cancel_event=threading.Event(),
        )
        self.assertEqual(contract.bindings[0].smd_material, "vehicles\\paint")
        self.assertEqual(
            source_union_material_contract_from_payload(
                source_union_material_contract_payload(contract)
            ), contract,
        )
        authorization = require_current_source_union_material_contract(
            contract, filtered_source_bytes=filtered,
            roots=self.roots, cancel_event=threading.Event(),
        )
        private = materialize_private_source_union_material_roots(
            contract, self.roots, self.root / "backslash-private",
            threading.Event(), filtered_source_bytes=filtered,
        )
        require_current_source_union_material_contract(
            contract, filtered_source_bytes=filtered,
            roots=private, cancel_event=threading.Event(),
        )
        evidence = source_union_material_render_evidence(authorization)
        self.assertEqual(set(evidence[0]), MATERIAL_EVIDENCE_FIELDS)
        self.assertEqual(evidence[0]["material_identity"], "paint-region")
        self.assertEqual(
            evidence[0]["vmt_sha256"],
            next(item.sha256 for item in contract.files if item.kind == "vmt"),
        )

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_nested_junction_is_never_followed_even_when_target_stays_in_root(self) -> None:
        junction_root = self.root / "junction-root"
        actual = junction_root / "actual"
        actual.mkdir(parents=True)
        (actual / "paint.vmt").write_text(
            'VertexLitGeneric { "$basetexture" "textures/paint" }',
            encoding="utf-8",
        )
        junction = junction_root / "vehicles"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(actual)],
            capture_output=True, text=True,
        )
        if created.returncode != 0:
            self.skipTest(f"cannot create junction: {created.stderr}")
        try:
            with self.assertRaises(ValueError):
                build_source_union_material_contract(
                    source_identity="meshes/body.smd",
                    filtered_source_bytes=self.filtered,
                    requests=self.requests,
                    roots=(junction_root, self.second),
                    cancel_event=threading.Event(),
                )
        finally:
            if os.path.lexists(junction):
                os.rmdir(junction)

    def test_actual_aggregate_budget_stops_growth_before_second_file_is_read(self) -> None:
        from maximum_optimizer import source_materials as module
        materials = ("first", "second")
        for material in materials:
            (self.first / f"vehicles/{material}.vmt").write_text(
                f'VertexLitGeneric {{ "$basetexture" "textures/{material}" }}',
                encoding="utf-8",
            )
            (self.second / f"textures/{material}.vtf").write_bytes(b"x")
        filtered = _multi_material_smd(materials)
        requests = tuple({
            "material_region_key": f"{material}-region",
            "smd_material": material,
            "search_paths": ("vehicles",),
        } for material in materials)
        completed: list[str] = []
        real_read = module._read_regular_no_follow

        def grow_vtf_before_open(path, *args, **kwargs):
            path = Path(path)
            if path.suffix.casefold() == ".vtf":
                path.write_bytes(path.stem.encode("ascii")[:1] * 600)
            result = real_read(path, *args, **kwargs)
            completed.append(path.relative_to(self.root).as_posix())
            return result

        with mock.patch.object(module, "_MAX_BYTES", 1000), mock.patch.object(
            module, "_read_regular_no_follow", side_effect=grow_vtf_before_open,
        ), self.assertRaises(ValueError):
            build_source_union_material_contract(
                source_identity="meshes/body.smd",
                filtered_source_bytes=filtered, requests=requests,
                roots=self.roots, cancel_event=threading.Event(),
            )
        self.assertIn("second/textures/first.vtf", completed)
        self.assertNotIn("second/textures/second.vtf", completed)

    def test_resealed_binding_cannot_swap_targeted_file_indices(self) -> None:
        materials = ("first", "second")
        for material in materials:
            (self.first / f"vehicles/{material}.vmt").write_text(
                f'VertexLitGeneric {{ "$basetexture" "textures/{material}" }}',
                encoding="utf-8",
            )
            (self.second / f"textures/{material}.vtf").write_bytes(material.encode())
        contract = build_source_union_material_contract(
            source_identity="meshes/body.smd",
            filtered_source_bytes=_multi_material_smd(materials),
            requests=tuple({
                "material_region_key": f"{material}-region",
                "smd_material": material,
                "search_paths": ("vehicles",),
            } for material in materials),
            roots=self.roots, cancel_event=threading.Event(),
        )
        payload = source_union_material_contract_payload(contract)
        first, second = payload["bindings"]
        first["vmt_file_index"], second["vmt_file_index"] = (
            second["vmt_file_index"], first["vmt_file_index"],
        )
        first["vtf_file_index"], second["vtf_file_index"] = (
            second["vtf_file_index"], first["vtf_file_index"],
        )
        unsigned = dict(payload); unsigned.pop("material_contract_sha256")
        payload["material_contract_sha256"] = hashlib.sha256(
            canonical_json(unsigned).encode()
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "relation"):
            source_union_material_contract_from_payload(payload)

    def test_render_evidence_rejects_resealed_vmt_to_vtf_semantic_swap(self) -> None:
        materials = ("first", "second")
        for material in materials:
            (self.first / f"vehicles/{material}.vmt").write_text(
                f'VertexLitGeneric {{ "$basetexture" "textures/{material}" }}',
                encoding="utf-8",
            )
            (self.second / f"textures/{material}.vtf").write_bytes(material.encode())
        filtered = _multi_material_smd(materials)
        contract = build_source_union_material_contract(
            source_identity="meshes/body.smd", filtered_source_bytes=filtered,
            requests=tuple({
                "material_region_key": f"{material}-region",
                "smd_material": material, "search_paths": ("vehicles",),
            } for material in materials),
            roots=self.roots, cancel_event=threading.Event(),
        )
        payload = source_union_material_contract_payload(contract)
        first, second = payload["bindings"]
        for field in ("texture_identity", "vtf_file_index", "vtf_path"):
            first[field], second[field] = second[field], first[field]
        unsigned = dict(payload); unsigned.pop("material_contract_sha256")
        payload["material_contract_sha256"] = hashlib.sha256(
            canonical_json(unsigned).encode()
        ).hexdigest()
        forged = source_union_material_contract_from_payload(payload)
        with self.assertRaisesRegex(ValueError, "current material"):
            require_current_source_union_material_contract(
                forged, filtered_source_bytes=filtered,
                roots=self.roots, cancel_event=threading.Event(),
            )
        with self.assertRaises(TypeError):
            source_union_material_render_evidence(forged)

    def test_invalid_request_control_fails_before_any_selected_file_read(self) -> None:
        from maximum_optimizer import source_materials as module
        for searches in (
            ("vehicles", "../escape"),
            tuple(f"search-{index:05d}" for index in range(10_001)),
        ):
            requests = ({**self.requests[0], "search_paths": searches},)
            with self.subTest(count=len(searches)), mock.patch(
                "maximum_optimizer.source_materials._read_regular_no_follow",
                wraps=module._read_regular_no_follow,
            ) as reader, self.assertRaises(ValueError):
                build_source_union_material_contract(
                    source_identity="meshes/body.smd",
                    filtered_source_bytes=self.filtered, requests=requests,
                    roots=self.roots, cancel_event=threading.Event(),
                )
            reader.assert_not_called()

    def test_nested_payload_collections_and_strings_are_bounded(self) -> None:
        contract = self.build()
        mutations = []
        too_many_searches = source_union_material_contract_payload(contract)
        too_many_searches["bindings"][0]["search_paths"] = [
            f"search-{index:03d}" for index in range(65)
        ]
        mutations.append(too_many_searches)
        too_many_directives = source_union_material_contract_payload(contract)
        too_many_directives["bindings"][0]["duplicate_root_directives"] = [{
            "directive": f"$duplicate-{index:03d}", "ignored_values": ["x"],
        } for index in range(65)]
        mutations.append(too_many_directives)
        too_many_values = source_union_material_contract_payload(contract)
        too_many_values["bindings"][0]["duplicate_root_directives"] = [{
            "directive": "$duplicate", "ignored_values": [
                f"value-{index:03d}" for index in range(65)
            ],
        }]
        mutations.append(too_many_values)
        long_value = source_union_material_contract_payload(contract)
        long_value["bindings"][0]["duplicate_root_directives"] = [{
            "directive": "$duplicate", "ignored_values": ["x" * 4097],
        }]
        mutations.append(long_value)
        for forged in mutations:
            unsigned = dict(forged); unsigned.pop("material_contract_sha256")
            forged["material_contract_sha256"] = hashlib.sha256(
                canonical_json(unsigned).encode()
            ).hexdigest()
            with self.subTest(forged=forged["bindings"][0]), self.assertRaises(ValueError):
                source_union_material_contract_from_payload(forged)

    def test_material_region_bound_allows_65_and_rejects_257(self) -> None:
        materials = tuple(f"material-{index:03d}" for index in range(65))
        for material in materials:
            (self.first / f"vehicles/{material}.vmt").write_text(
                'VertexLitGeneric { "$basetexture" "textures/shared" }',
                encoding="utf-8",
            )
        (self.second / "textures/shared.vtf").write_bytes(b"shared")
        requests = tuple({
            "material_region_key": f"region-{index:03d}",
            "smd_material": material,
            "search_paths": ("vehicles",),
        } for index, material in enumerate(materials))
        contract = build_source_union_material_contract(
            source_identity="meshes/body.smd",
            filtered_source_bytes=_multi_material_smd(materials),
            requests=requests, roots=self.roots, cancel_event=threading.Event(),
        )
        self.assertEqual(len(contract.bindings), 65)
        too_many = tuple(f"too-many-{index:03d}" for index in range(257))
        with self.assertRaisesRegex(ValueError, "cardinality"):
            build_source_union_material_contract(
                source_identity="meshes/body.smd",
                filtered_source_bytes=_multi_material_smd(too_many),
                requests=tuple({
                    "material_region_key": f"region-{index:03d}",
                    "smd_material": material,
                    "search_paths": ("vehicles",),
                } for index, material in enumerate(too_many)),
                roots=self.roots, cancel_event=threading.Event(),
            )

    def test_payload_rejects_boolean_schema_and_nested_hidden_fields_even_resealed(self) -> None:
        contract = self.build(); payload = source_union_material_contract_payload(contract)
        mutations = []
        boolean = {**payload, "schema": True}; mutations.append(boolean)
        root_extra = source_union_material_contract_payload(contract)
        root_extra["roots"][0]["hidden"] = True; mutations.append(root_extra)
        file_extra = source_union_material_contract_payload(contract)
        file_extra["files"][0]["hidden"] = True; mutations.append(file_extra)
        binding_extra = source_union_material_contract_payload(contract)
        binding_extra["bindings"][0]["hidden"] = True; mutations.append(binding_extra)
        for forged in mutations:
            unsigned = dict(forged); unsigned.pop("material_contract_sha256")
            forged["material_contract_sha256"] = hashlib.sha256(
                canonical_json(unsigned).encode()
            ).hexdigest()
            with self.assertRaises(ValueError):
                source_union_material_contract_from_payload(forged)

    def test_root_order_reparse_missing_unsupported_and_traversal_fail_closed(self) -> None:
        contract = self.build()
        with self.assertRaisesRegex(ValueError, "current material"):
            require_current_source_union_material_contract(
                contract, filtered_source_bytes=self.filtered,
                roots=tuple(reversed(self.roots)), cancel_event=threading.Event(),
            )
        with mock.patch(
            "maximum_optimizer.source_materials._has_reparse_ancestor",
            side_effect=lambda path: Path(path) == self.first,
        ), self.assertRaisesRegex(ValueError, "unsafe"):
            self.build()
        lexical = self.root / "junction"
        if os.name == "nt":
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(lexical), str(self.first)],
                capture_output=True, text=True,
            )
            if created.returncode == 0:
                try:
                    with self.assertRaisesRegex(ValueError, "unsafe"):
                        build_source_union_material_contract(
                            source_identity="meshes/body.smd",
                            filtered_source_bytes=self.filtered, requests=self.requests,
                            roots=(lexical, self.second), cancel_event=threading.Event(),
                        )
                finally:
                    if os.path.lexists(lexical): os.rmdir(lexical)
        (self.first / "vehicles/paint.vmt").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            self.build()
        (self.first / "vehicles/paint.vmt").write_text(
            'Unsupported { "$basetexture" "textures/paint" }', encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.build()
        unsafe = ({
            "material_region_key": "paint-region", "smd_material": "paint",
            "search_paths": ("../escape",),
        },)
        with self.assertRaises(ValueError):
            build_source_union_material_contract(
                source_identity="meshes/body.smd", filtered_source_bytes=self.filtered,
                requests=unsafe, roots=self.roots, cancel_event=threading.Event(),
            )

    def test_private_destination_preexisting_or_unsafe_parent_is_never_touched(self) -> None:
        contract = self.build(); destination = self.root / "private"
        destination.mkdir(); marker = destination / "marker"; marker.write_bytes(b"preserve")
        with self.assertRaisesRegex(ValueError, "destination"):
            materialize_private_source_union_material_roots(
                contract, self.roots, destination, threading.Event(),
                filtered_source_bytes=self.filtered,
            )

    def test_private_destination_publication_race_preserves_external_winner(self) -> None:
        contract = self.build(); destination = self.root / "private"
        marker = destination / "external.marker"
        original_rename = os.rename
        def raced_publish(source, target):
            if Path(target) == destination:
                destination.mkdir(); marker.write_bytes(b"preserve")
            return original_rename(source, target)
        with mock.patch(
            "maximum_optimizer.source_materials.os.rename", side_effect=raced_publish,
        ), self.assertRaises((FileExistsError, OSError)):
            materialize_private_source_union_material_roots(
                contract, self.roots, destination, threading.Event(),
                filtered_source_bytes=self.filtered,
            )
        self.assertEqual(marker.read_bytes(), b"preserve")
        self.assertEqual(marker.read_bytes(), b"preserve")
        destination = self.root / "unsafe-parent" / "private"
        destination.parent.mkdir()
        with mock.patch(
            "maximum_optimizer.source_materials._has_reparse_ancestor",
            side_effect=lambda path: Path(path) == destination.parent,
        ), self.assertRaisesRegex(ValueError, "destination"):
            materialize_private_source_union_material_roots(
                contract, self.roots, destination, threading.Event(),
                filtered_source_bytes=self.filtered,
            )

    def test_cancellation_and_mid_copy_external_mutation_fail_and_cleanup(self) -> None:
        contract = self.build(); cancelled = threading.Event(); cancelled.set()
        with self.assertRaises(ProcessCancelledError):
            build_source_union_material_contract(
                source_identity="meshes/body.smd", filtered_source_bytes=self.filtered,
                requests=self.requests, roots=self.roots, cancel_event=cancelled,
            )
        from maximum_optimizer import source_materials as module
        real_copy = module._copy_file_no_follow
        mutated = {"done": False}
        def mutate_after_copy(source, *args, **kwargs):
            result = real_copy(source, *args, **kwargs)
            if not mutated["done"]:
                data = bytearray(Path(source).read_bytes()); data[len(data) // 2] ^= 1
                Path(source).write_bytes(data); mutated["done"] = True
            return result
        destination = self.root / "private"
        with mock.patch(
            "maximum_optimizer.source_materials._copy_file_no_follow",
            side_effect=mutate_after_copy,
        ), self.assertRaisesRegex(ValueError, "current material"):
            materialize_private_source_union_material_roots(
                contract, self.roots, destination, threading.Event(),
                filtered_source_bytes=self.filtered,
            )
        self.assertFalse(destination.exists())

    def test_forged_duplicate_directive_shape_is_rejected_even_when_resealed(self) -> None:
        for label, mutate in (
            ("duplicates", lambda item: item.update(duplicate_root_directives=[{
                "directive": "UPPERCASE", "ignored_values": [],
            }])),
            ("shader", lambda item: item.update(shader="forged")),
            ("directive", lambda item: item.update(texture_directive="$refracttinttexture")),
            ("refract-alpha", lambda item: item.update(
                shader="refract", texture_directive="$refracttinttexture",
                uses_texture_alpha=False,
            )),
            ("unsafe-region", lambda item: item.update(material_region_key="../escape")),
            ("unsafe-smd", lambda item: item.update(smd_material="C:/escape")),
            ("empty-smd-segment", lambda item: item.update(smd_material="vehicles//paint")),
            ("dot-smd-segment", lambda item: item.update(smd_material="vehicles/./paint")),
            ("parent-smd-segment", lambda item: item.update(smd_material="vehicles/../paint")),
            ("newline-region", lambda item: item.update(material_region_key="paint\nregion")),
        ):
            with self.subTest(label=label):
                payload = source_union_material_contract_payload(self.build())
                mutate(payload["bindings"][0])
                unsigned = dict(payload); unsigned.pop("material_contract_sha256")
                payload["material_contract_sha256"] = hashlib.sha256(
                    canonical_json(unsigned).encode()
                ).hexdigest()
                with self.assertRaises(ValueError):
                    source_union_material_contract_from_payload(payload)

    def test_private_destination_replacement_during_copy_is_preserved(self) -> None:
        contract = self.build(); destination = self.root / "private"
        marker = destination / "external.marker"
        from maximum_optimizer import source_materials as module
        real_copy = module._copy_file_no_follow; replaced = {"done": False}
        def replace_after_copy(*args, **kwargs):
            result = real_copy(*args, **kwargs)
            if not replaced["done"]:
                destination.mkdir(); marker.write_bytes(b"preserve")
                replaced["done"] = True
            return result
        with mock.patch(
            "maximum_optimizer.source_materials._copy_file_no_follow",
            side_effect=replace_after_copy,
        ), self.assertRaises((ValueError, OSError)):
            materialize_private_source_union_material_roots(
                contract, self.roots, destination, threading.Event(),
                filtered_source_bytes=self.filtered,
            )
        self.assertEqual(marker.read_bytes(), b"preserve")
        self.assertEqual(
            tuple(path.name for path in destination.iterdir()),
            ("external.marker",),
        )

    def test_file_bounds_fail_before_selected_bytes_are_read(self) -> None:
        from maximum_optimizer import source_materials as module
        with mock.patch.object(
            module, "_MAX_VMT_BYTES",
            (self.first / "vehicles/paint.vmt").stat().st_size - 1,
        ), mock.patch.object(
            module, "_read_regular_no_follow", wraps=module._read_regular_no_follow,
        ) as reader, self.assertRaisesRegex(ValueError, "VMT bound"):
            self.build()
        reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
