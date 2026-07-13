from __future__ import annotations

from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from PIL import Image

from maximum_optimizer.remapped_topology import validate_remapped_topology_smd
from benchmarks.lvs_models.smoke_remapped_topology_v1 import (
    CompileArtifact,
    CompileResult,
    SmokeCase,
    StudioMdlCompiler,
    compile_artifact_manifest_sha256,
    pair_compile_results,
    run_smoke_cases,
)
from tests.maximum_optimizer.test_remapped_topology import (
    _diagonal_output,
    _fan_source,
    _smd,
)


class RemappedTopologySmokeTests(unittest.TestCase):
    def _fake_compile_script(
        self, path: Path, *, missing: str | None = None, corrupt_mdl: bool = False
    ) -> None:
        suffixes = (".mdl", ".vvd", ".dx80.vtx", ".dx90.vtx")
        statements = [
            "from pathlib import Path",
            "import sys",
            "out = Path(sys.argv[sys.argv.index('--out') + 1])",
            "model = out / 'models/maximum/remapped/accepted'",
            "model.parent.mkdir(parents=True)",
            "checksum = bytes.fromhex('01020304')",
        ]
        for suffix in suffixes:
            if suffix != missing:
                if suffix == ".mdl":
                    magic = "b'NOPE'" if corrupt_mdl else "b'IDST'"
                    payload = f"{magic} + (48).to_bytes(4, 'little') + checksum + b'\\0' * 500"
                elif suffix == ".vvd":
                    payload = "b'IDSV' + (4).to_bytes(4, 'little') + checksum + b'\\0' * 64"
                else:
                    payload = "(7).to_bytes(4, 'little') + b'\\0' * 12 + checksum + b'\\0' * 32"
                statements.append(f"Path(str(model) + {suffix!r}).write_bytes({payload})")
        statements.append("(out / 'compile_summary.json').write_bytes(b'x' * 99)")
        path.write_text("\n".join(statements) + "\n", encoding="utf-8")

    def test_studiomdl_compiler_requires_real_model_artifact_and_counts_only_models(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            work = root / "work"
            repo.mkdir()
            work.mkdir()
            studiomdl = root / "studiomdl.exe"
            studiomdl.write_bytes(b"placeholder")
            source = root / "source.smd"
            output = root / "output.smd"
            source.write_text(_fan_source(), encoding="utf-8")
            output.write_text(_diagonal_output(), encoding="utf-8")
            case = SmokeCase("accepted", 0.5, source, output)
            proof = validate_remapped_topology_smd(
                source.read_bytes().decode("utf-8"),
                output.read_bytes().decode("utf-8"),
                0.5,
            )
            script = repo / "batch_compile_opt_qc.py"
            self._fake_compile_script(script, missing=".dx80.vtx")
            compiler = StudioMdlCompiler(studiomdl=studiomdl, work_root=work, repo_root=repo)

            failed = compiler(case, proof)

            self.assertEqual(failed.status, "failed")
            self.assertEqual((failed.source.status, failed.candidate.status), ("failed", "failed"))
            self.assertEqual((failed.source.returncode, failed.candidate.returncode), (-1, -1))
            self._fake_compile_script(script, corrupt_mdl=True)
            corrupt = compiler(case, proof)
            self.assertEqual(corrupt.status, "failed")
            self._fake_compile_script(script)

            compiled = compiler(case, proof)

            self.assertEqual(compiled.status, "compiled")
            self.assertGreater(compiled.source.compiled_bytes, 0)
            self.assertEqual(compiled.candidate.compiled_bytes, compiled.source.compiled_bytes)
            self.assertEqual(compiled.delta_bytes, 0)
            self.assertEqual(len(compiled.source.artifacts), 4)
            self.assertTrue(all(isinstance(item, CompileArtifact) for item in compiled.source.artifacts))
            with self.assertRaisesRegex(ValueError, "compile result"):
                CompileResult(
                    status="compiled",
                    returncode=0,
                    compiled_bytes=compiled.source.compiled_bytes,
                    artifacts=compiled.source.artifacts,
                    artifact_sha256="f" * 64,
                    log_sha256=compiled.source.log_sha256,
                    input_sha256=compiled.source.input_sha256,
                )
            newest = max(work.iterdir(), key=lambda item: item.stat().st_mtime_ns)
            self.assertIn(
                '$sequence "idle" "idle.smd" fps 1',
                (newest / "model_OPT.qc").read_text(),
            )

            output.write_text(_fan_source(), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "changed after structural validation"):
                compiler(case, proof)

    def test_injected_compiler_cannot_omit_expected_sidecars(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.smd"
            output = root / "output.smd"
            source.write_text(_fan_source(), encoding="utf-8")
            output.write_text(_diagonal_output(), encoding="utf-8")
            case = SmokeCase("accepted", 0.5, source, output)

            def incomplete(case, proof):
                artifacts = (
                    CompileArtifact("maximum/remapped/accepted.mdl", 4, "a" * 64),
                )
                side = CompileResult(
                    status="compiled", returncode=0, compiled_bytes=4,
                    artifacts=artifacts,
                    artifact_sha256=compile_artifact_manifest_sha256(artifacts),
                    log_sha256="b" * 64, input_sha256=proof.source_sha256,
                )
                candidate = CompileResult(
                    status="compiled", returncode=0, compiled_bytes=4,
                    artifacts=artifacts,
                    artifact_sha256=compile_artifact_manifest_sha256(artifacts),
                    log_sha256="b" * 64, input_sha256=proof.output_sha256,
                )
                return pair_compile_results(side, candidate)

            with self.assertRaisesRegex(RuntimeError, "artifact set"):
                run_smoke_cases((case,), compiler=incomplete)

    def test_render_evidence_is_hashed_measured_and_stays_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.smd"
            output = root / "output.smd"
            source.write_text(_fan_source(), encoding="utf-8")
            output.write_text(_diagonal_output(), encoding="utf-8")
            render_case = root / "renders" / "accepted"
            original = Image.new("RGBA", (2, 2), (10, 20, 30, 255))
            optimized = original.copy()
            optimized.putpixel((0, 0), (12, 20, 30, 255))
            angles = ("front", "back", "left", "right", "top", "bottom", "iso1", "iso2")
            for angle in angles:
                (render_case / "original").mkdir(parents=True, exist_ok=True)
                (render_case / "optimized").mkdir(parents=True, exist_ok=True)
                original.save(render_case / "original" / f"{angle}.png")
                optimized.save(render_case / "optimized" / f"{angle}.png")
            summary = {
                "angles": list(angles),
                "size": 2,
                "before": {"file": str(source), "files": [str(source)],
                           "sha256s": [hashlib.sha256(source.read_bytes()).hexdigest()], "tris": 4,
                           "images": {angle: f"original/{angle}.png" for angle in angles}},
                "after": {"file": str(output), "files": [str(output)],
                          "sha256s": [hashlib.sha256(output.read_bytes()).hexdigest()], "tris": 2,
                          "images": {angle: f"optimized/{angle}.png" for angle in angles}},
            }
            (render_case / "preview_summary.json").write_text(json.dumps(summary), encoding="utf-8")
            renderer = root / "blender.exe"
            render_script = root / "render_previews.py"
            renderer.write_bytes(b"renderer")
            render_script.write_bytes(b"script")

            payload = run_smoke_cases(
                (SmokeCase("accepted", 0.5, source, output),),
                render_root=root / "renders",
                renderer=renderer,
                render_script=render_script,
            )

        evidence = payload["cases"][0]["render"]
        self.assertEqual(evidence["status"], "rendered")
        self.assertEqual(evidence["quality_status"], "unverified")
        self.assertIsNone(evidence["quality_claim"])
        self.assertEqual(len(evidence["views"]), 8)
        self.assertEqual(evidence["views"][0]["changed_pixels"], 1)
        self.assertEqual(evidence["views"][0]["max_channel_delta"], 2)
        self.assertEqual(len(evidence["evidence_sha256"]), 64)

    def test_compile_runs_only_after_structural_acceptance_and_quality_stays_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.smd"
            accepted = root / "accepted.smd"
            rejected = root / "rejected.smd"
            source.write_text(_fan_source(), encoding="utf-8")
            accepted.write_text(_diagonal_output(), encoding="utf-8")
            rejected.write_text(_smd([
                ("metal", ("a", "b", "e")),
                ("metal", ("a", "e", "d")),
            ]), encoding="utf-8")
            calls = []

            def compiler(case, proof):
                calls.append((case.name, proof.proof_sha256))
                artifacts = tuple(
                    CompileArtifact(f"maximum/remapped/accepted{suffix}", size, "c" * 64)
                    for suffix, size in (
                        (".mdl", 334), (".vvd", 300),
                        (".dx80.vtx", 300), (".dx90.vtx", 300),
                    )
                )
                side = CompileResult(
                    status="compiled",
                    returncode=0,
                    compiled_bytes=1234,
                    artifacts=artifacts,
                    artifact_sha256=compile_artifact_manifest_sha256(artifacts),
                    log_sha256="b" * 64,
                    input_sha256=proof.source_sha256,
                )
                candidate = CompileResult(
                    status="compiled",
                    returncode=0,
                    compiled_bytes=1234,
                    artifacts=artifacts,
                    artifact_sha256=compile_artifact_manifest_sha256(artifacts),
                    log_sha256="b" * 64,
                    input_sha256=proof.output_sha256,
                )
                return pair_compile_results(side, candidate)

            payload = run_smoke_cases((
                SmokeCase("accepted", 0.5, source, accepted),
                SmokeCase("rejected", 0.5, source, rejected),
            ), compiler=compiler)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "accepted")
        self.assertEqual(payload["record_kind"], "local_experiment")
        self.assertEqual(payload["strategy"], "meshopt-remapped-topology-v1")
        self.assertEqual(payload["quality_status"], "unverified")
        self.assertIsNone(payload["quality_claim"])
        self.assertFalse(payload["winner"])
        by_name = {item["name"]: item for item in payload["cases"]}
        self.assertEqual(by_name["accepted"]["structural_status"], "passed")
        self.assertEqual(by_name["accepted"]["compile"]["status"], "compiled")
        self.assertEqual(by_name["accepted"]["compile"]["candidate"]["compiled_bytes"], 1234)
        self.assertEqual(by_name["rejected"]["structural_status"], "rejected")
        self.assertEqual(by_name["rejected"]["compile"]["status"], "not-run")
        self.assertIn("boundary", by_name["rejected"]["rejection_reason"])


if __name__ == "__main__":
    unittest.main()
