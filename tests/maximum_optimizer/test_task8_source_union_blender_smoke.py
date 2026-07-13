import hashlib
import io
import json
import os
import shutil
import subprocess
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image

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
from maximum_optimizer.visual_validation import (
    FidelityProfile,
    REQUIRED_METRICS,
    SourceUnionComparisonContract,
    compare_source_union_render_sets,
)


REPO = Path(__file__).resolve().parents[2]
BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe")
VTFCMD = REPO / ".superpowers/tools/VTFEdit/VTFEdit/VTFCmd.exe"


def _triangle(material, points):
    rows = [material]
    for index, (x, y, z) in enumerate(points):
        rows.append(f"0 {x:g} {y:g} {z:g} 0 0 1 {index == 1:d} {index == 2:d}")
    return "\n".join(rows)


def _smd(*triangles):
    return ("\n".join((
        "version 1", "nodes", '0 "root" -1', "end", "skeleton",
        "time 0", "0 0 0 0 0 0 0", "end", "triangles",
        *triangles, "end", "",
    ))).encode()


def _convert_vtf(source: Path, destination: Path) -> None:
    completed = subprocess.run(
        [str(VTFCMD), "-file", str(source), "-output", str(destination),
         "-format", "RGBA8888", "-silent"],
        cwd=VTFCMD.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)


class SourceUnionBlenderSmokeTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("MAXIMUM_RUN_BLENDER_SMOKE") == "1",
        "set MAXIMUM_RUN_BLENDER_SMOKE=1 for real Blender smoke",
    )
    def test_real_cli_opaque_occluded_alpha_reordered_and_deterministic(self):
        self.assertTrue(BLENDER.is_file())
        self.assertTrue(VTFCMD.is_file())
        front = _triangle("opaque", ((0, 0, 0), (2, 0, 0), (0, 2, 0)))
        back = _triangle("opaque", ((0, 0, -0.2), (2, 0, -0.2), (0, 2, -0.2)))
        alpha = _triangle("alpha", ((3, 0, 0), (5, 0, 0), (3, 2, 0)))
        source = _smd(front, back, alpha)
        candidate = _smd(alpha, back, front)

        outputs = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            texture_source = root / "texture-source"
            texture_source.mkdir()
            opaque_png = texture_source / "opaque.png"
            alpha_png = texture_source / "alpha.png"
            Image.new("RGBA", (16, 16), (180, 80, 30, 255)).save(opaque_png)
            alpha_image = Image.new("RGBA", (16, 16), (30, 180, 80, 128))
            alpha_image.save(alpha_png)
            _convert_vtf(opaque_png, texture_source)
            _convert_vtf(alpha_png, texture_source)

            for run in range(2):
                workspace = root / f"workspace-{run}"
                inputs = workspace / "inputs"
                control_dir = workspace / "control"
                material_root = workspace / "material-roots/root-000"
                texture_cache = workspace / "texture-cache"
                for directory in (
                    inputs, control_dir, material_root / "vehicles",
                    material_root / "textures", texture_cache,
                ):
                    directory.mkdir(parents=True, exist_ok=True)
                renderer = inputs / "render_previews.py"
                shutil.copy2(REPO / "render_previews.py", renderer)
                runtime = inputs / "maximum_optimizer"
                runtime.mkdir()
                for name in (
                    "__init__.py", "regions.py", "qc_graph.py",
                    "source_components.py", "smd_contract.py", "reporting.py",
                ):
                    shutil.copy2(REPO / "maximum_optimizer" / name, runtime / name)
                runtime_files = []
                for path in sorted(runtime.iterdir(), key=lambda item: item.name):
                    runtime_payload = path.read_bytes()
                    runtime_files.append({
                        "path": path.name, "size": len(runtime_payload),
                        "sha256": hashlib.sha256(runtime_payload).hexdigest(),
                    })
                runtime_unsigned = {
                    "schema": 1,
                    "kind": "adaptive-direct-source-union-python-runtime-v1",
                    "files": runtime_files,
                }
                runtime_seal = hashlib.sha256(json.dumps(
                    runtime_unsigned, ensure_ascii=False, allow_nan=False,
                    sort_keys=True, separators=(",", ":"),
                ).encode()).hexdigest()
                reference = inputs / "reference.smd"
                optimized = inputs / "candidate.smd"
                reference.write_bytes(source)
                optimized.write_bytes(candidate)
                (material_root / "vehicles/opaque.vmt").write_text(
                    'VertexLitGeneric { "$basetexture" "textures/opaque" }',
                    encoding="utf-8",
                )
                (material_root / "vehicles/alpha.vmt").write_text(
                    'VertexLitGeneric { "$basetexture" "textures/alpha" "$translucent" "1" }',
                    encoding="utf-8",
                )
                shutil.copy2(texture_source / "opaque.vtf", material_root / "textures/opaque.vtf")
                shutil.copy2(texture_source / "alpha.vtf", material_root / "textures/alpha.vtf")

                manifest = build_source_component_manifest(source)
                filtered = current_filtered_source_component_bytes(manifest, source)
                transfer = build_source_component_transfer(manifest, source, candidate)
                contract = build_source_union_material_contract(
                    source_identity="mesh.smd",
                    filtered_source_bytes=filtered,
                    requests=(
                        {"material_region_key": "material-000", "smd_material": "opaque", "search_paths": ("vehicles",)},
                        {"material_region_key": "material-001", "smd_material": "alpha", "search_paths": ("vehicles",)},
                    ),
                    roots=(material_root,), cancel_event=None,
                )
                authorization = require_current_source_union_material_contract(
                    contract, filtered_source_bytes=filtered,
                    roots=(material_root,), cancel_event=None,
                )
                evidence = source_union_material_render_evidence(authorization)
                coverage = "2" * 64
                comparison = SourceUnionComparisonContract.create(
                    target_sha256="1" * 64, source_identity="mesh.smd",
                    source_coverage_sha256=coverage,
                    reference_source_sha256=hashlib.sha256(filtered).hexdigest(),
                    candidate_source_sha256=hashlib.sha256(candidate).hexdigest(),
                    material_contract_sha256=contract.material_contract_sha256,
                    pose_frames=(("bind", 0),),
                    union_key="source-union-" + coverage[:32],
                )
                control = {
                    "schema": 1, "kind": "adaptive-direct-source-union-render-v1",
                    "target_sha256": comparison.target_sha256,
                    "comparison_contract": {
                        "contract_sha256": comparison.contract_sha256,
                        "target_sha256": comparison.target_sha256,
                        "source_identity": comparison.source_identity,
                        "source_coverage_sha256": comparison.source_coverage_sha256,
                        "reference_source_sha256": comparison.reference_source_sha256,
                        "candidate_source_sha256": comparison.candidate_source_sha256,
                        "material_contract_sha256": comparison.material_contract_sha256,
                        "pose_frames": [["bind", 0]], "union_key": comparison.union_key,
                    },
                    "source_identity": comparison.source_identity,
                    "source_coverage_sha256": comparison.source_coverage_sha256,
                    "component_keys": [item.component_key for item in manifest.components],
                    "component_manifest": source_component_manifest_payload(manifest),
                    "candidate_component_transfer": source_component_transfer_payload(transfer),
                    "material_region_keys": [item.material_region_key for item in contract.bindings],
                    "material_contract_sha256": contract.material_contract_sha256,
                    "material_contract": source_union_material_contract_payload(contract),
                    "material_render_evidence": list(evidence),
                    "pose_frames": {"bind": 0},
                    "angles": ["front", "back", "left", "right", "top", "bottom", "iso1", "iso2"],
                    "cameras": [f"camera-{index:02d}" for index in range(8)],
                    "renderer_sha256": hashlib.sha256(renderer.read_bytes()).hexdigest(),
                    "python_runtime_contract_sha256": runtime_seal,
                    "python_runtime_files": runtime_files,
                }
                control_path = control_dir / "source-union-contract.json"
                visibility_path = control_dir / "source-union-visibility.json"
                control_path.write_text(json.dumps(control), encoding="utf-8")
                control_anchor = hashlib.sha256(control_path.read_bytes()).hexdigest()
                raw = workspace / "raw"
                command = [
                    str(BLENDER), "--background", "--python", str(renderer), "--",
                    "--before", str(reference), "--after", str(optimized),
                    "--out", str(raw), "--size", "128",
                    "--angles", "front,back,left,right,top,bottom,iso1,iso2",
                    "--passes", "textured,clay", "--poses", "bind:0",
                    "--source-union-contract", str(control_path),
                    "--source-union-visibility-out", str(visibility_path),
                    "--source-union-control-sha256", control_anchor,
                    "--materials-root", str(material_root),
                    "--vtfcmd", str(VTFCMD), "--texture-cache", str(texture_cache),
                ]
                completed = subprocess.run(
                    command, cwd=REPO, capture_output=True, text=True, timeout=240,
                )
                self.assertEqual(
                    completed.returncode, 0,
                    completed.stdout + "\nSTDERR\n" + completed.stderr,
                )
                files = {path.relative_to(raw).as_posix(): path.read_bytes() for path in raw.rglob("*") if path.is_file()}
                self.assertEqual(len(files), 34)
                self.assertFalse(tuple(workspace.rglob("*.exr")))
                visibility = json.loads(visibility_path.read_text(encoding="utf-8"))
                by_side_component = {}
                for item in visibility["observations"]:
                    by_side_component.setdefault((item["side"], item["component_key"]), []).append(item["visible_mask_pixels"])
                for values in by_side_component.values():
                    self.assertGreater(max(values), 0)
                self.assertIn(0, by_side_component[("reference", "component-001")])
                for component in control["component_keys"]:
                    self.assertEqual(
                        by_side_component[("reference", component)],
                        by_side_component[("candidate", component)],
                    )
                result = compare_source_union_render_sets(
                    raw / "original", raw / "optimized",
                    FidelityProfile(1, "smoke", True, "f" * 64, {metric: 1e6 for metric in REQUIRED_METRICS}),
                    expected_contract=comparison,
                )
                self.assertTrue(result.passed, result.failures)
                outputs.append((files, visibility_path.read_bytes()))
            if outputs[0] != outputs[1]:
                first_files, first_visibility = outputs[0]
                second_files, second_visibility = outputs[1]
                changed = sorted(
                    name for name in first_files
                    if first_files[name] != second_files.get(name)
                )
                changed_png = [name for name in changed if name.endswith(".png")]
                pixel_equal = all(
                    Image.open(io.BytesIO(first_files[name])).tobytes()
                    == Image.open(io.BytesIO(second_files[name])).tobytes()
                    for name in changed_png
                )
                def chunks(payload):
                    offset = 8
                    values = []
                    while offset < len(payload):
                        size = struct.unpack(">I", payload[offset:offset + 4])[0]
                        name = payload[offset + 4:offset + 8].decode("ascii")
                        values.append((name, payload[offset + 8:offset + 8 + size]))
                        offset += 12 + size
                    return values
                first_chunks = chunks(first_files[changed_png[0]])
                second_chunks = chunks(second_files[changed_png[0]])
                chunk_diff = [
                    (index, left[0], right[0], len(left[1]), len(right[1]))
                    for index, (left, right) in enumerate(zip(first_chunks, second_chunks))
                    if left != right
                ]
                raise AssertionError(
                    "non-deterministic source-union outputs: "
                    f"changed={changed}; visibility_equal="
                    f"{first_visibility == second_visibility}; "
                    f"all_png_pixels_equal={pixel_equal}; chunk_diff={chunk_diff}"
                )


if __name__ == "__main__":
    unittest.main()
