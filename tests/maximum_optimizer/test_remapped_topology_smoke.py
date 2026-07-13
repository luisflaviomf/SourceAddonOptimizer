from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from maximum_optimizer.remapped_topology import validate_remapped_topology_smd
from benchmarks.lvs_models.smoke_remapped_topology_v1 import (
    CompileResult,
    SmokeCase,
    StudioMdlCompiler,
    run_smoke_cases,
)
from tests.maximum_optimizer.test_remapped_topology import (
    _diagonal_output,
    _fan_source,
    _smd,
)


class RemappedTopologySmokeTests(unittest.TestCase):
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
            proof = validate_remapped_topology_smd(source.read_text(), output.read_text(), 0.5)
            script = repo / "batch_compile_opt_qc.py"
            script.write_text("raise SystemExit(0)\n", encoding="utf-8")
            compiler = StudioMdlCompiler(studiomdl=studiomdl, work_root=work, repo_root=repo)

            failed = compiler(case, proof)

            self.assertEqual((failed.status, failed.returncode, failed.compiled_bytes), ("failed", -1, 0))
            script.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "out = Path(sys.argv[sys.argv.index('--out') + 1])\n"
                "model = out / 'models/maximum/remapped/accepted.mdl'\n"
                "model.parent.mkdir(parents=True)\n"
                "model.write_bytes(b'mdl')\n"
                "(out / 'compile_summary.json').write_bytes(b'x' * 99)\n",
                encoding="utf-8",
            )

            compiled = compiler(case, proof)

            self.assertEqual((compiled.status, compiled.returncode, compiled.compiled_bytes), ("compiled", 0, 3))
            newest = max(work.iterdir(), key=lambda item: item.stat().st_mtime_ns)
            self.assertIn('$sequence "idle" "idle.smd" fps 1', (newest / "model_OPT.qc").read_text())

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
                return CompileResult(
                    status="compiled",
                    returncode=0,
                    compiled_bytes=1234,
                    artifact_sha256="a" * 64,
                    log_sha256="b" * 64,
                )

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
        self.assertEqual(by_name["accepted"]["compile"]["compiled_bytes"], 1234)
        self.assertEqual(by_name["rejected"]["structural_status"], "rejected")
        self.assertEqual(by_name["rejected"]["compile"]["status"], "not-run")
        self.assertIn("boundary", by_name["rejected"]["rejection_reason"])


if __name__ == "__main__":
    unittest.main()
