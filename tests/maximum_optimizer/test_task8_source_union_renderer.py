import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import render_previews
from maximum_optimizer.reporting import canonical_json
from maximum_optimizer.source_components import (
    build_source_component_manifest,
    build_source_component_transfer,
    current_filtered_source_component_bytes,
    source_component_manifest_payload,
    source_component_transfer_payload,
)
from maximum_optimizer.source_materials import (
    build_source_union_material_contract,
    require_current_source_union_material_contract,
    source_union_material_contract_payload,
    source_union_material_render_evidence,
)
from maximum_optimizer.visual_validation import SourceUnionComparisonContract


def _triangle(material: str, offset: float) -> str:
    return "\n".join((
        material,
        f"0 {offset:g} 0 0 0 0 1 0 0",
        f"0 {offset + 1:g} 0 0 0 0 1 1 0",
        f"0 {offset:g} 1 0 0 0 1 0 1",
    ))


def _smd(*triangles: str) -> bytes:
    return ("\n".join((
        "version 1",
        "nodes",
        '0 "root" -1',
        "end",
        "skeleton",
        "time 0",
        "0 0 0 0 0 0 0",
        "end",
        "triangles",
        *triangles,
        "end",
        "",
    ))).encode("utf-8")


class SourceUnionRendererContractTests(unittest.TestCase):
    def _fixture(self, root: Path):
        first = _triangle("paint", 0.0)
        second = _triangle("paint", 10.0)
        source = _smd(first, second)
        candidate = _smd(second, first)
        component_manifest = build_source_component_manifest(source)
        filtered = current_filtered_source_component_bytes(component_manifest, source)
        transfer = build_source_component_transfer(
            component_manifest, source, candidate,
        )
        material_root = root / "materials"
        (material_root / "vehicles").mkdir(parents=True)
        (material_root / "textures").mkdir(parents=True)
        (material_root / "vehicles/paint.vmt").write_text(
            'VertexLitGeneric { "$basetexture" "textures/paint" }',
            encoding="utf-8",
        )
        (material_root / "textures/paint.vtf").write_bytes(b"vtf")
        material_contract = build_source_union_material_contract(
            source_identity="mesh.smd",
            filtered_source_bytes=filtered,
            requests=({
                "material_region_key": "material-000",
                "smd_material": "paint",
                "search_paths": ("vehicles",),
            },),
            roots=(material_root,),
            cancel_event=None,
        )
        authorization = require_current_source_union_material_contract(
            material_contract,
            filtered_source_bytes=filtered,
            roots=(material_root,),
            cancel_event=None,
        )
        evidence = source_union_material_render_evidence(authorization)
        runtime_root = root / "inputs" / "maximum_optimizer"
        runtime_root.mkdir(parents=True)
        runtime_files = []
        for name in (
            "__init__.py", "qc_graph.py", "regions.py", "reporting.py",
            "smd_contract.py", "source_components.py",
        ):
            source_path = Path(render_previews.__file__).parent / "maximum_optimizer" / name
            destination = runtime_root / name
            shutil.copy2(source_path, destination)
            runtime_payload = destination.read_bytes()
            runtime_files.append({
                "path": name, "size": len(runtime_payload),
                "sha256": hashlib.sha256(runtime_payload).hexdigest(),
            })
        runtime_unsigned = {
            "schema": 1,
            "kind": "adaptive-direct-source-union-python-runtime-v1",
            "files": runtime_files,
        }
        coverage_hash = "2" * 64
        comparison = SourceUnionComparisonContract.create(
            target_sha256="1" * 64,
            source_identity="mesh.smd",
            source_coverage_sha256=coverage_hash,
            reference_source_sha256=hashlib.sha256(filtered).hexdigest(),
            candidate_source_sha256=hashlib.sha256(candidate).hexdigest(),
            material_contract_sha256=material_contract.material_contract_sha256,
            pose_frames=(("bind", 0),),
            union_key="source-union-" + coverage_hash[:32],
        )
        payload = {
            "schema": 1,
            "kind": "adaptive-direct-source-union-render-v1",
            "target_sha256": comparison.target_sha256,
            "comparison_contract": {
                "contract_sha256": comparison.contract_sha256,
                "target_sha256": comparison.target_sha256,
                "source_identity": comparison.source_identity,
                "source_coverage_sha256": comparison.source_coverage_sha256,
                "reference_source_sha256": comparison.reference_source_sha256,
                "candidate_source_sha256": comparison.candidate_source_sha256,
                "material_contract_sha256": comparison.material_contract_sha256,
                "pose_frames": [["bind", 0]],
                "union_key": comparison.union_key,
            },
            "source_identity": comparison.source_identity,
            "source_coverage_sha256": comparison.source_coverage_sha256,
            "component_keys": [
                item.component_key for item in component_manifest.components
            ],
            "component_manifest": source_component_manifest_payload(component_manifest),
            "candidate_component_transfer": source_component_transfer_payload(transfer),
            "material_region_keys": ["material-000"],
            "material_contract_sha256": material_contract.material_contract_sha256,
            "material_contract": source_union_material_contract_payload(material_contract),
            "material_render_evidence": list(evidence),
            "pose_frames": {"bind": 0},
            "angles": list(render_previews.ANGLE_DIRS),
            "cameras": [f"camera-{index:02d}" for index in range(8)],
            "renderer_sha256": hashlib.sha256(
                Path(render_previews.__file__).read_bytes()
            ).hexdigest(),
            "python_runtime_contract_sha256": hashlib.sha256(
                canonical_json(runtime_unsigned).encode()
            ).hexdigest(),
            "python_runtime_files": runtime_files,
        }
        return (
            payload, filtered, candidate, (material_root,), component_manifest,
            transfer, runtime_root,
        )

    def test_strict_control_parser_binds_every_current_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            payload, reference, candidate, roots, manifest, transfer, runtime = fixture
            control = render_previews._parse_source_union_control(
                payload, reference, candidate, roots,
                private_python_runtime_root=runtime,
            )
            self.assertEqual(control.component_manifest, manifest)
            self.assertEqual(control.candidate_transfer, transfer)
            self.assertEqual(
                control.component_keys,
                tuple(item.component_key for item in manifest.components),
            )
            self.assertEqual(control.material_evidence, tuple(payload["material_render_evidence"]))
            self.assertEqual(
                control.python_runtime_contract_sha256,
                payload["python_runtime_contract_sha256"],
            )

            extra = runtime / "__pycache__"
            extra.mkdir()
            with self.assertRaisesRegex(ValueError, "sealed control"):
                render_previews._parse_source_union_control(
                    payload, reference, candidate, roots,
                    private_python_runtime_root=runtime,
                )
            extra.rmdir()

            forged = json.loads(json.dumps(payload))
            forged["extra"] = True
            with self.assertRaisesRegex(ValueError, "control.*fields"):
                render_previews._parse_source_union_control(
                    forged, reference, candidate, roots,
                    private_python_runtime_root=runtime,
                )
            forged = json.loads(json.dumps(payload))
            forged["schema"] = True
            with self.assertRaisesRegex(ValueError, "control.*identity"):
                render_previews._parse_source_union_control(
                    forged, reference, candidate, roots,
                    private_python_runtime_root=runtime,
                )

            forged = json.loads(json.dumps(payload))
            forged["material_contract"]["roots"][0]["root_index"] = False
            unsigned = dict(forged["material_contract"])
            unsigned.pop("material_contract_sha256")
            forged["material_contract"]["material_contract_sha256"] = hashlib.sha256(
                canonical_json(unsigned).encode()
            ).hexdigest()
            forged["material_contract_sha256"] = forged["material_contract"]["material_contract_sha256"]
            forged["comparison_contract"]["material_contract_sha256"] = forged["material_contract_sha256"]
            comparison_unsigned = dict(forged["comparison_contract"])
            comparison_unsigned.pop("contract_sha256")
            forged["comparison_contract"]["contract_sha256"] = hashlib.sha256(
                canonical_json(comparison_unsigned).encode()
            ).hexdigest()
            with self.assertRaisesRegex(ValueError, "sealed control"):
                render_previews._parse_source_union_control(
                    forged, reference, candidate, roots,
                    private_python_runtime_root=runtime,
                )

    def test_direct_smd_groups_follow_reference_and_reordered_candidate_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload, reference, candidate, roots, manifest, transfer, runtime = self._fixture(
                Path(temporary)
            )
            reference_groups = render_previews._source_union_component_groups(
                reference, manifest,
            )
            candidate_groups = render_previews._source_union_component_groups(
                candidate, manifest, transfer,
            )
            self.assertEqual(tuple(key for key, _ in reference_groups), (
                "component-000", "component-001",
            ))
            self.assertEqual(tuple(key for key, _ in candidate_groups), (
                "component-000", "component-001",
            ))
            self.assertEqual(
                reference_groups[0][1][0].corners[0].position,
                candidate_groups[0][1][0].corners[0].position,
            )
            self.assertEqual(
                reference_groups[1][1][0].corners[0].position,
                candidate_groups[1][1][0].corners[0].position,
            )

    def test_cryptomatte_counts_each_component_once_per_visible_pixel(self) -> None:
        def crypto_float(value: int) -> float:
            return struct.unpack("<f", struct.pack("<I", value))[0]

        first = 0x3F123456
        second = 0x40123456
        manifest = {
            "component-000": f"{first:08x}",
            "component-001": f"{second:08x}",
        }
        pixels = (
            ((crypto_float(first), 1.0), (0.0, 0.0)),
            ((crypto_float(second), 0.5), (0.0, 0.0)),
            ((crypto_float(first), 0.75), (crypto_float(second), 0.25)),
        )
        self.assertEqual(
            render_previews._count_cryptomatte_components(
                pixels, manifest, ("component-000", "component-001"),
            ),
            {"component-000": 2, "component-001": 2},
        )
        with self.assertRaisesRegex(ValueError, "manifest.*component"):
            render_previews._count_cryptomatte_components(
                pixels, {"component-000": f"{first:08x}"},
                ("component-000", "component-001"),
            )

    def test_visibility_payload_is_exact_sealed_and_canonical_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload, reference, candidate, roots, manifest, transfer, runtime = self._fixture(
                Path(temporary)
            )
            control = render_previews._parse_source_union_control(
                payload, reference, candidate, roots,
                private_python_runtime_root=runtime,
            )
            counts = {
                (side, component, f"camera-{camera:02d}"): camera + 1
                for side in ("candidate", "reference")
                for component in control.component_keys
                for camera in range(8)
            }
            visibility = render_previews._source_union_visibility_payload(
                control, counts,
            )
            self.assertEqual(set(visibility), {
                "schema", "kind", "target_sha256", "source_identity",
                "source_coverage_sha256", "cameras", "pose_keys",
                "observations", "evidence_sha256",
            })
            self.assertEqual(
                [
                    (item["side"], item["component_key"], item["camera_key"])
                    for item in visibility["observations"]
                ],
                [
                    (side, component, f"camera-{camera:02d}")
                    for side in ("candidate", "reference")
                    for component in control.component_keys
                    for camera in range(8)
                ],
            )
            unsigned = dict(visibility)
            seal = unsigned.pop("evidence_sha256")
            self.assertEqual(
                seal,
                hashlib.sha256(canonical_json(unsigned).encode()).hexdigest(),
            )

    def test_alpha_material_is_explicitly_dithered_and_blended_fails_closed(self) -> None:
        class Material:
            surface_render_method = "BLENDED"

        material = Material()
        render_previews._require_source_union_dithered_alpha(material, True)
        self.assertEqual(material.surface_render_method, "DITHERED")

        class RefusesDither:
            @property
            def surface_render_method(self):
                return "BLENDED"

            @surface_render_method.setter
            def surface_render_method(self, value):
                pass

        with self.assertRaisesRegex(ValueError, "DITHERED"):
            render_previews._require_source_union_dithered_alpha(
                RefusesDither(), True,
            )

    def test_private_source_union_cli_is_exact_without_heavy_runtime_imports(self) -> None:
        base = [
            "--before", "reference.smd", "--after", "candidate.smd",
            "--out", "raw", "--passes", "textured,clay",
            "--poses", "bind:0", "--source-union-contract", "contract.json",
            "--source-union-visibility-out", "visibility.json",
            "--source-union-control-sha256", "a" * 64,
        ]
        args = render_previews._parse_args(base)
        render_previews._validate_source_union_cli_args(args)
        for changed in (
            base + ["--aggregate-regions"],
            [
                "--before", "reference.smd", "--after", "candidate.smd",
                "--out", "raw", "--passes", "textured,clay",
                "--poses", "bind:0", "--source-union-contract", "contract.json",
                "--source-union-visibility-out", "",
                "--source-union-control-sha256", "a" * 64,
            ],
            ["--before", "reference.smd", "--after", "candidate.smd",
             "--out", "raw", "--passes", "clay,textured", "--poses", "bind:0",
             "--source-union-contract", "contract.json",
             "--source-union-visibility-out", "visibility.json",
             "--source-union-control-sha256", "a" * 64],
        ):
            with self.assertRaises(ValueError):
                render_previews._validate_source_union_cli_args(
                    render_previews._parse_args(changed)
                )

    def test_png_canonicalization_removes_only_volatile_blender_metadata(self) -> None:
        def chunk(kind: bytes, payload: bytes) -> bytes:
            body = kind + payload
            return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
        stable = chunk(b"tEXt", b"Software\0Blender")
        png = b"\x89PNG\r\n\x1a\n" + b"".join((
            chunk(b"IHDR", ihdr), chunk(b"tEXt", b"Date\x00" + b"12:34:56"),
            chunk(b"tEXt", b"RenderTime\x00" + b"1.23"), stable,
            chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00")),
            chunk(b"IEND", b""),
        ))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "render.png"
            path.write_bytes(png)
            render_previews._canonicalize_source_union_png(path)
            first = path.read_bytes()
            self.assertNotIn(b"Date\0", first)
            self.assertNotIn(b"RenderTime\0", first)
            self.assertIn(stable, first)
            render_previews._canonicalize_source_union_png(path)
            self.assertEqual(first, path.read_bytes())

            path.write_bytes(png[:-1])
            with self.assertRaisesRegex(ValueError, "PNG"):
                render_previews._canonicalize_source_union_png(path)
            corrupted = bytearray(png)
            corrupted[-8] ^= 1
            path.write_bytes(corrupted)
            with self.assertRaisesRegex(ValueError, "CRC"):
                render_previews._canonicalize_source_union_png(path)

    def test_invalid_private_runtime_cannot_execute_before_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload, _reference, _candidate, _roots, _manifest, _transfer, runtime = (
                self._fixture(root)
            )
            renderer = root / "inputs" / "render_previews.py"
            shutil.copy2(Path(render_previews.__file__), renderer)
            control_dir = root / "control"
            control_dir.mkdir()
            contract = control_dir / "source-union-contract.json"
            contract.write_text(json.dumps(payload), encoding="utf-8")
            parent_anchor = hashlib.sha256(contract.read_bytes()).hexdigest()
            marker = root / "HOSTILE-MODULE-EXECUTED"
            hostile = (
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
            )
            (runtime / "regions.py").write_text(hostile, encoding="utf-8")
            hostile_bytes = hostile.encode()
            region_proof = next(
                item for item in payload["python_runtime_files"]
                if item["path"] == "regions.py"
            )
            region_proof["size"] = len(hostile_bytes)
            region_proof["sha256"] = hashlib.sha256(hostile_bytes).hexdigest()
            runtime_unsigned = {
                "schema": 1,
                "kind": "adaptive-direct-source-union-python-runtime-v1",
                "files": payload["python_runtime_files"],
            }
            payload["python_runtime_contract_sha256"] = hashlib.sha256(
                canonical_json(runtime_unsigned).encode()
            ).hexdigest()
            contract.write_text(json.dumps(payload), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable, str(renderer),
                    "--source-union-contract", str(contract),
                    "--source-union-visibility-out",
                    str(control_dir / "source-union-visibility.json"),
                    "--source-union-control-sha256", parent_anchor,
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(
                marker.exists(),
                completed.stdout + "\nSTDERR\n" + completed.stderr,
            )

    def test_bootstrap_executes_captured_bytes_not_reopened_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "maximum_optimizer"
            runtime.mkdir()
            names = (
                "__init__.py", "qc_graph.py", "regions.py", "reporting.py",
                "smd_contract.py", "source_components.py",
            )
            for name in names:
                shutil.copy2(
                    Path(render_previews.__file__).parent / "maximum_optimizer" / name,
                    runtime / name,
                )
            marker = root / "REOPENED-HOSTILE-MODULE"
            script = "\n".join((
                "import pathlib, sys",
                f"sys.path.insert(0, {str(Path(render_previews.__file__).parent)!r})",
                "import render_previews",
                f"runtime = pathlib.Path({str(runtime)!r})",
                "captured = {path.name: path.read_bytes() for path in runtime.iterdir()}",
                f"marker = pathlib.Path({str(marker)!r})",
                "(runtime / 'regions.py').write_text("
                "'from pathlib import Path\\n' + "
                "f\"Path({str(marker)!r}).write_text('executed')\\n\", encoding='utf-8')",
                "render_previews._bootstrap_install_runtime(runtime, captured)",
                "import maximum_optimizer.regions",
                "raise SystemExit(91 if marker.exists() else 0)",
            ))
            completed = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True,
                timeout=30,
            )
            self.assertEqual(
                completed.returncode, 0,
                completed.stdout + "\nSTDERR\n" + completed.stderr,
            )
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
