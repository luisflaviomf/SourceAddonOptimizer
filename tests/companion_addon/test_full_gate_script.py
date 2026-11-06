from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_full_gate_script_names_every_required_lane_and_fails_closed() -> None:
    script = (ROOT / "scripts" / "test_companion.ps1").read_text(encoding="utf-8")
    for required in (
        r"tests\companion_contract",
        r"tests\companion_addon",
        r"tests\maximum_optimizer",
        "test_batch_compile_paths.py",
        "test_batch_optimize_parallel.py",
        "test_companion_lua.ps1",
        "GmodAddonOptimizer.ContractTests.csproj",
        "GmodAddonOptimizer.CompanionContractTests.csproj",
        "GmodAddonCompressor.csproj",
    ):
        assert required in script
    assert "Write-Host" in script
    assert "$LASTEXITCODE" in script
    assert "throw" in script
