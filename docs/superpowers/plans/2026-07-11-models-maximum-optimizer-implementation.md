# Models Maximum Optimizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adicionar ao produto WPF um modo `Maximum` experimental que procura o menor `models/` Source compilado que passe gates estruturais e visuais de pior caso, com reparo seletivo, cache, retomada e relatório transparente.

**Architecture:** O WPF apenas seleciona o modo e apresenta progresso/relatório; o worker Python chama um novo pacote `maximum_optimizer`. O pacote inventaria famílias, gera candidatos isolados com Blender/meshoptimizer, compila cada candidato com StudioMDL, mede os bytes compilados, valida estrutura e fidelidade, mantém uma fronteira de Pareto e promove atomicamente o menor candidato aprovado. `Normal` e `Fidelity` continuam passando pelo fluxo atual sem alteração de comportamento.

**Tech Stack:** Python 3.11+, `unittest`, Blender headless + Blender Source Tools, StudioMDL/Crowbar, Pillow 12.3.0, meshoptimizer v1.2 (`9d9890c73011d75920af614485296d1e03e95448`), CMake/MSVC, WPF C#/.NET 6, MSTest 4.3.0.

## Global Constraints

- UI nova somente em `GmodAddonCompressor-master/GmodAddonCompressor/`; `gui/` permanece legado e intocado.
- Processamento pesado permanece no worker Python e é ativado por `--optimizer-mode maximum`.
- `Normal` e `Fidelity` devem manter argumentos, saída e comportamento atuais.
- A entrada original é somente leitura; candidatos usam workspaces isolados e a saída final é promovida atomicamente.
- O objetivo é minimizar bytes reais do `models/` compilado; razão de faces é apenas parâmetro de geração.
- 50% não é teto nem promessa universal; a busca continua até os gates, orçamento ou ganho marginal encerrarem a exploração.
- Hard gates estruturais nunca são relaxados por perfil visual ou orçamento.
- Preservar uma família original é permitido apenas com status e relatório explícitos; fallback oculto reprova o candidato.
- Ganhos por remoção opcional de `.dx80.vtx` são separados de ganhos geométricos.
- meshoptimizer é somente offline; o resultado final continua sendo MDL/VVD/VTX/ANI/PHY Source normal.
- Open3D não entra no primeiro release; só será reconsiderado por benchmark separado.
- Release oficial: `powershell -ExecutionPolicy Bypass -File .\build_release_wpf.ps1`.
- Antes da execução, criar worktree isolada com `superpowers:using-git-worktrees`; não misturar as alterações locais existentes em `gui/` e logs.

---

## Mapa de arquivos

### Novos módulos Python

- `maximum_optimizer/domain.py`: enums e dataclasses imutáveis compartilhados.
- `maximum_optimizer/compiled_size.py`: inventário de bytes Source e parsing seguro de VVD.
- `maximum_optimizer/qc_inventory.py`: agrupamento de famílias e fingerprint estrutural de QC/SMD.
- `maximum_optimizer/structural_validation.py`: hard gates e proveniência sem fallback oculto.
- `maximum_optimizer/search.py`: fronteira de Pareto, agenda adaptativa e decisão de parada.
- `maximum_optimizer/cache.py`: cache endereçado por conteúdo e escrita atômica.
- `maximum_optimizer/reporting.py`: relatório JSON agregado e linhas de progresso JSONL.
- `maximum_optimizer/processes.py`: execução cancelável de Blender/StudioMDL e captura de logs.
- `maximum_optimizer/candidates.py`: adapters Fidelity/Blender/meshoptimizer.
- `maximum_optimizer/visual_validation.py`: comparação multiângulo e agregação por pior caso.
- `maximum_optimizer/orchestrator.py`: fluxo completo por família e promoção do resultado.
- `maximum_optimizer/profiles/maximum-experimental-v1.json`: thresholds calibrados e versionados.
- `maximum_optimizer/native/meshopt_bridge.cpp`: ABI C mínima para simplificação attribute-aware.
- `maximum_optimizer/native/CMakeLists.txt`: build reproduzível do bridge x64.
- `maximum_optimizer/native/build.ps1`: build/rebuild atômico da DLL x64 usada pelo worker.
- `batch_optimize_maximum.py`: script executado dentro do Blender para simplificar/reparar/exportar.
- `calibrate_maximum_profiles.py`: gera o perfil a partir do corpus de calibração.

### Novos testes e fixtures

- `tests/maximum_optimizer/test_compiled_size.py`
- `tests/maximum_optimizer/test_qc_inventory.py`
- `tests/maximum_optimizer/test_structural_validation.py`
- `tests/maximum_optimizer/test_search.py`
- `tests/maximum_optimizer/test_cache.py`
- `tests/maximum_optimizer/test_visual_validation.py`
- `tests/maximum_optimizer/test_orchestrator.py`
- `tests/maximum_optimizer/test_cli.py`
- `tests/fixtures/maximum/`: QC/SMD/VVD sintéticos e manifests determinísticos.
- `GmodAddonCompressor-master/GmodAddonCompressor.Tests/`: testes MSTest do protocolo e view-model.

### Arquivos existentes modificados

- `build_optimized_addon.py`: aceita `maximum` e delega somente esse modo ao orchestrator novo.
- `worker/worker_main.py`: preserva roteamento e propaga o novo modo.
- `requirements.txt`: adiciona Pillow pinado.
- `pyinstaller/worker.spec`: inclui perfil, bridge, licença e scripts novos.
- `pyinstaller/package_wpf_tools.ps1`: detecta fontes/dll/perfil stale e valida entradas novas no ZIP.
- `build_release_wpf.ps1`: roda testes antes de empacotar/publicar.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerRunner.cs`: argumento e eventos Maximum.
- `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerProgressParser.cs`: protocolo `MAXIMUM_EVENT`.
- `GmodAddonCompressor-master/GmodAddonCompressor/DataContexts/MainWindowContext.cs`: terceiro modo e campos de observabilidade.
- `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml`: seleção `Maximum (experimental)` e status.
- `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml.cs`: roteamento, relatório e integração com Pipeline.
- `GmodAddonCompressor-master/GmodAddonCompressor.sln`: inclui o projeto de testes.

## Cobertura da especificação

| Requisito aprovado | Implementação |
|---|---|
| Medir bytes compilados e vértices, separando `.dx80.vtx` | Tasks 2 e 10 |
| Inventário por família sem depender de filename | Task 3 |
| Hard gates e zero fallback oculto | Task 4 |
| Busca além de 50%, Pareto e recuperação regional | Tasks 5 e 10 |
| Cache, Resume, cancelamento e promoção atômica | Tasks 6, 7 e 10 |
| Fidelity como baseline obrigatório | Tasks 5 e 7 |
| Multiângulo, textured/clay, superfície, normal, UV e poses | Tasks 8 e 9 |
| meshoptimizer offline attribute-aware | Task 9 |
| Relatório auditável e progresso detalhado | Tasks 10 a 12 |
| WPF principal, worker Python e modos antigos preservados | Tasks 10 a 12 |
| Worker/ZIP/release oficial reproduzível | Task 13 |
| Calibração separada de validação e teste in-game | Task 14 |

---

### Task 1: Contratos de domínio e harness de testes Python

**Files:**
- Create: `maximum_optimizer/__init__.py`
- Create: `maximum_optimizer/domain.py`
- Create: `tests/__init__.py`
- Create: `tests/maximum_optimizer/__init__.py`
- Create: `tests/maximum_optimizer/test_domain.py`

**Interfaces:**
- Produces: `ArtifactStat`, `CompiledSizeSnapshot`, `StructuralFingerprint`, `CandidateSpec`, `GateFailure`, `ValidationResult`, `CandidateEvaluation`, `CandidateAttempt`, `SearchBudget`, `FamilyManifest`, `FamilyOutcome`.
- Consumes: somente stdlib; nenhuma dependência do pipeline atual.

- [ ] **Step 1: Escrever o teste que fixa serialização, imutabilidade e validação dos contratos**

```python
# tests/maximum_optimizer/test_domain.py
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from maximum_optimizer.domain import CandidateSpec, CompiledSizeSnapshot, SearchBudget


class DomainTests(unittest.TestCase):
    def test_candidate_is_immutable_and_has_stable_key(self):
        candidate = CandidateSpec("meshopt-r035", "meshoptimizer", 0.35, 0.01, "transfer-v1")
        self.assertEqual(candidate.cache_payload()["target_ratio"], 0.35)
        with self.assertRaises(FrozenInstanceError):
            candidate.target_ratio = 0.5

    def test_snapshot_rejects_inconsistent_total(self):
        with self.assertRaisesRegex(ValueError, "total_bytes"):
            CompiledSizeSnapshot(Path("models"), 7, {".mdl": 3}, {}, ())

    def test_budget_defaults_are_bounded(self):
        budget = SearchBudget.experimental_default()
        self.assertEqual(budget.max_candidates, 18)
        self.assertGreater(budget.min_ratio_step, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar a falha por módulo ausente**

Run: `python -m unittest tests.maximum_optimizer.test_domain -v`

Expected: `ModuleNotFoundError: No module named 'maximum_optimizer'`.

- [ ] **Step 3: Criar os contratos mínimos, incluindo validações em `__post_init__` e `to_dict()` sem caminhos implícitos**

```python
# maximum_optimizer/domain.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

EngineName = Literal["fidelity", "blender", "meshoptimizer"]
FamilyStatus = Literal["optimized", "preserved", "failed", "cancelled"]


@dataclass(frozen=True)
class ArtifactStat:
    relative_path: str
    kind: str
    size_bytes: int
    lod_vertices: tuple[int, ...] = ()


@dataclass(frozen=True)
class CompiledSizeSnapshot:
    root: Path
    total_bytes: int
    bytes_by_kind: dict[str, int]
    vertices_by_lod: dict[int, int]
    artifacts: tuple[ArtifactStat, ...]

    def __post_init__(self) -> None:
        if self.total_bytes != sum(item.size_bytes for item in self.artifacts):
            raise ValueError("total_bytes must equal artifact bytes")

    def to_dict(self) -> dict:
        return {**asdict(self), "root": str(self.root)}


@dataclass(frozen=True)
class StructuralFingerprint:
    model_name: str
    bodygroups: tuple[str, ...]
    materials: tuple[str, ...]
    skin_families: tuple[tuple[str, ...], ...]
    bones: tuple[str, ...]
    bone_parents: tuple[tuple[str, str], ...]
    attachments: tuple[str, ...]
    hitboxes: tuple[str, ...]
    sequences: tuple[str, ...]
    mesh_files: tuple[str, ...]
    lod_mesh_files: tuple[str, ...]
    physics_mesh: str | None


@dataclass(frozen=True)
class FamilyManifest:
    family_id: str
    model_rel: str
    source_dir: Path
    original_models_dir: Path
    fingerprint: StructuralFingerprint
    input_hash: str
    required_artifact_kinds: tuple[str, ...]


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    engine: EngineName
    target_ratio: float
    target_error: float
    repair_profile: str
    region_overrides: tuple[tuple[str, float], ...] = ()

    def cache_payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GateFailure:
    gate: str
    scope: str
    measured: float | str | None
    limit: float | str | None
    message: str


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    failures: tuple[GateFailure, ...] = ()
    metrics: dict[str, float] = field(default_factory=dict)
    worst_scope: str = ""


@dataclass(frozen=True)
class CandidateEvaluation:
    spec: CandidateSpec
    size: CompiledSizeSnapshot
    structural: ValidationResult
    visual: ValidationResult
    compiled_models_dir: Path

    @property
    def passed(self) -> bool:
        return self.structural.passed and self.visual.passed


@dataclass(frozen=True)
class CandidateAttempt:
    spec: CandidateSpec
    status: Literal["generated", "compile_failed", "evaluated", "cancelled"]
    evaluation: CandidateEvaluation | None
    error: str = ""


@dataclass(frozen=True)
class SearchBudget:
    max_candidates: int
    min_ratio_step: float
    min_marginal_saving: float

    @classmethod
    def experimental_default(cls) -> "SearchBudget":
        return cls(max_candidates=18, min_ratio_step=0.025, min_marginal_saving=0.005)


@dataclass(frozen=True)
class FamilyOutcome:
    manifest: FamilyManifest
    status: FamilyStatus
    selected: CandidateEvaluation | None
    attempted: tuple[CandidateAttempt, ...]
    reason: str
```

- [ ] **Step 4: Rodar o teste e todo o discovery Python**

Run: `python -m unittest discover -s tests -t . -v`

Expected: `OK`, com 3 testes novos aprovados.

- [ ] **Step 5: Commit**

```powershell
git add maximum_optimizer tests
git commit -m "test: define Maximum optimizer domain contracts"
```

---

### Task 2: Medição dos bytes Source compilados e vértices VVD

**Files:**
- Create: `maximum_optimizer/compiled_size.py`
- Create: `tests/maximum_optimizer/test_compiled_size.py`

**Interfaces:**
- Consumes: `ArtifactStat`, `CompiledSizeSnapshot` da Task 1.
- Produces: `read_vvd_lod_vertices(path: Path) -> tuple[int, ...]`; `scan_compiled_models(models_dir: Path) -> CompiledSizeSnapshot`; `compare_snapshots(original, control, selected, optional_removed) -> dict`.

- [ ] **Step 1: Escrever fixtures binárias em memória e testes para VVD válido, truncado e soma por variante**

```python
# tests/maximum_optimizer/test_compiled_size.py
import struct
import tempfile
import unittest
from pathlib import Path

from maximum_optimizer.compiled_size import read_vvd_lod_vertices, scan_compiled_models


def vvd_bytes(lods=(100, 50, 25)):
    values = list(lods) + [0] * (8 - len(lods))
    return struct.pack("<4siii8i", b"IDSV", 4, 1234, len(lods), *values) + b"x" * 64


class CompiledSizeTests(unittest.TestCase):
    def test_reads_all_declared_lods(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "a.vvd")
            path.write_bytes(vvd_bytes())
            self.assertEqual(read_vvd_lod_vertices(path), (100, 50, 25))

    def test_truncated_vvd_has_no_vertex_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "a.vvd")
            path.write_bytes(b"IDSV")
            self.assertEqual(read_vvd_lod_vertices(path), ())

    def test_scan_counts_real_vtx_variants_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.mdl").write_bytes(b"m" * 10)
            (root / "a.vvd").write_bytes(vvd_bytes((10,)))
            (root / "a.dx90.vtx").write_bytes(b"v" * 7)
            (root / "a.dx80.vtx").write_bytes(b"v" * 5)
            snap = scan_compiled_models(root)
            self.assertEqual(snap.total_bytes, sum(p.stat().st_size for p in root.iterdir()))
            self.assertEqual(snap.bytes_by_kind[".dx90.vtx"], 7)
            self.assertEqual(snap.bytes_by_kind[".dx80.vtx"], 5)
            self.assertEqual(snap.vertices_by_lod[0], 10)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Confirmar a falha pelo módulo ausente**

Run: `python -m unittest tests.maximum_optimizer.test_compiled_size -v`

Expected: import de `maximum_optimizer.compiled_size` falha.

- [ ] **Step 3: Implementar parsing defensivo e classificação pela terminação completa do nome**

```python
# maximum_optimizer/compiled_size.py
from __future__ import annotations

import struct
from collections import defaultdict
from pathlib import Path

from .domain import ArtifactStat, CompiledSizeSnapshot

SOURCE_SUFFIXES = (".mdl", ".vvd", ".vtx", ".ani", ".phy")
VVD_HEADER = struct.Struct("<4siii8i")


def _kind(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".vtx"):
        stem = name[:-4]
        variant = stem.rsplit(".", 1)[-1] if "." in stem else "plain"
        return f".{variant}.vtx" if variant != "plain" else ".vtx"
    return path.suffix.lower()


def read_vvd_lod_vertices(path: Path) -> tuple[int, ...]:
    try:
        data = path.read_bytes()[:VVD_HEADER.size]
        ident, version, _checksum, lod_count, *lods = VVD_HEADER.unpack(data)
    except (OSError, struct.error):
        return ()
    if ident != b"IDSV" or version <= 0 or not 1 <= lod_count <= 8:
        return ()
    return tuple(max(0, int(value)) for value in lods[:lod_count])


def scan_compiled_models(models_dir: Path) -> CompiledSizeSnapshot:
    root = models_dir.resolve()
    artifacts = []
    bytes_by_kind = defaultdict(int)
    vertices_by_lod = defaultdict(int)
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        kind = _kind(path)
        lods = read_vvd_lod_vertices(path) if kind == ".vvd" else ()
        size = path.stat().st_size
        artifacts.append(ArtifactStat(path.relative_to(root).as_posix(), kind, size, lods))
        bytes_by_kind[kind] += size
        for index, count in enumerate(lods):
            vertices_by_lod[index] += count
    return CompiledSizeSnapshot(
        root=root,
        total_bytes=sum(item.size_bytes for item in artifacts),
        bytes_by_kind=dict(sorted(bytes_by_kind.items())),
        vertices_by_lod=dict(sorted(vertices_by_lod.items())),
        artifacts=tuple(artifacts),
    )
```

- [ ] **Step 4: Adicionar `compare_snapshots` que retorna `roundtrip_delta`, `geometric_delta`, `optional_removed_bytes` e percentuais com denominador zero protegido; testar os quatro campos**

Run: `python -m unittest tests.maximum_optimizer.test_compiled_size -v`

Expected: 4 testes aprovados, incluindo comparação sem atribuir `.dx80.vtx` à geometria.

- [ ] **Step 5: Commit**

```powershell
git add maximum_optimizer/compiled_size.py tests/maximum_optimizer/test_compiled_size.py
git commit -m "feat: measure compiled Source model bytes"
```

---

### Task 3: Inventário de famílias e fingerprint estrutural

**Files:**
- Create: `maximum_optimizer/qc_inventory.py`
- Create: `tests/maximum_optimizer/test_qc_inventory.py`
- Create: `tests/fixtures/maximum/family.qc`
- Create: `tests/fixtures/maximum/reference.smd`

**Interfaces:**
- Consumes: `FamilyManifest`, `StructuralFingerprint`.
- Produces: `parse_qc_fingerprint(qc_path: Path) -> StructuralFingerprint`; `build_family_manifests(addon_models_dir, decompile_manifest_path, src_root) -> tuple[FamilyManifest, ...]`.

- [ ] **Step 1: Criar um QC fixture com `$modelname`, `$bodygroup`, `$texturegroup`, `$attachment`, `$hbox`, `$sequence` e dois SMDs; testar ordem estável e deduplicação case-insensitive**

```python
def test_fingerprint_keeps_source_order_without_case_duplicates(self):
    fp = parse_qc_fingerprint(FIXTURES / "family.qc")
    self.assertEqual(fp.model_name, "vehicles/test.mdl")
    self.assertEqual(fp.bodygroups, ("body", "wheels"))
    self.assertEqual(fp.attachments, ("eyes",))
    self.assertEqual(fp.mesh_files, ("reference.smd", "wheel.smd"))
```

- [ ] **Step 2: Confirmar a falha por função ausente**

Run: `python -m unittest tests.maximum_optimizer.test_qc_inventory -v`

Expected: import/função ausente.

- [ ] **Step 3: Implementar tokenizer de QC que remove `//` apenas fora de aspas, acompanha blocos e normaliza barras sem usar tokens de filename para classificar a peça**

```python
def _unique(values: list[str]) -> tuple[str, ...]:
    seen, result = set(), []
    for value in values:
        normalized = value.replace("\\", "/").strip().strip('"')
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(result)
```

O parser deve reconhecer as diretivas listadas no teste, seguir includes locais dentro da mesma família e rejeitar includes que escapem de `source_dir` após `resolve()`.

- [ ] **Step 4: Implementar `build_family_manifests` a partir dos registros `status == "ok"` do `decompile_manifest.json`; o `family_id` é SHA-256 de `model_rel.casefold()` e o `input_hash` cobre QC, SMD/DMX referenciados e artefatos originais de mesmo stem**

Run: `python -m unittest tests.maximum_optimizer.test_qc_inventory -v`

Expected: testes de diretivas, include inseguro, hash alterado e ordem determinística aprovados.

- [ ] **Step 5: Commit**

```powershell
git add maximum_optimizer/qc_inventory.py tests/maximum_optimizer/test_qc_inventory.py tests/fixtures/maximum
git commit -m "feat: inventory Source model families"
```

---

### Task 4: Hard gates estruturais e proveniência

**Files:**
- Create: `maximum_optimizer/structural_validation.py`
- Create: `tests/maximum_optimizer/test_structural_validation.py`

**Interfaces:**
- Consumes: `FamilyManifest`, `CompiledSizeSnapshot`, `StructuralFingerprint`, `ValidationResult`.
- Produces: `validate_structure(manifest, candidate_qc, candidate_models_dir, compile_record, provenance) -> ValidationResult`.

- [ ] **Step 1: Escrever testes parametrizados que reprovam individualmente MDL ausente, compile failure, skin/bodygroup/material/bone/attachment/hitbox/sequence removido, PHY/ANI obrigatório ausente e arquivo copiado fora do workspace do candidato**

```python
def test_hidden_original_fallback_is_a_hard_failure(self):
    result = validate_structure(
        manifest=self.manifest,
        candidate_qc=self.candidate_qc,
        candidate_models_dir=self.models,
        compile_record={"status": "ok", "model_rel": self.manifest.model_rel},
        provenance={"vehicles/test.mdl": "original-copy"},
    )
    self.assertFalse(result.passed)
    self.assertIn("hidden_fallback", {failure.gate for failure in result.failures})
```

- [ ] **Step 2: Rodar e confirmar falha pelo módulo ausente**

Run: `python -m unittest tests.maximum_optimizer.test_structural_validation -v`

Expected: import falha.

- [ ] **Step 3: Implementar gates como funções puras que acumulam todos os erros em vez de parar no primeiro**

```python
def _require_equal(name: str, expected: tuple[str, ...], actual: tuple[str, ...], failures: list[GateFailure]) -> None:
    if tuple(x.casefold() for x in expected) != tuple(x.casefold() for x in actual):
        failures.append(GateFailure(name, "family", repr(actual), repr(expected), f"{name} changed"))
```

`validate_structure` deve comparar exatamente, case-insensitive e preservando ordem, `model_name`, `bodygroups`, `materials`, rows de `skin_families`, `bones`, `bone_parents`, `attachments`, `hitboxes` e `sequences`. Como `mesh_files` e `lod_mesh_files` são nomes intermediários que podem receber `_OPT`, o gate exige a mesma contagem não vazia por papel, não igualdade de filename; `physics_mesh` exige somente a mesma presença/ausência. A validação também deve exigir a família compilada e variantes obrigatórias observadas no baseline, validar `compile_record["status"] == "ok"` e aceitar `provenance == "candidate-compile"` apenas.

- [ ] **Step 4: Rodar o módulo e o discovery completo**

Run:

```powershell
python -m unittest tests.maximum_optimizer.test_structural_validation -v
python -m unittest discover -s tests -t . -v
```

Expected: todos aprovados.

- [ ] **Step 5: Commit**

```powershell
git add maximum_optimizer/structural_validation.py tests/maximum_optimizer/test_structural_validation.py
git commit -m "feat: enforce Maximum structural gates"
```

---

### Task 5: Fronteira de Pareto e busca adaptativa

**Files:**
- Create: `maximum_optimizer/search.py`
- Create: `tests/maximum_optimizer/test_search.py`

**Interfaces:**
- Consumes: `CandidateSpec`, `CandidateEvaluation`, `SearchBudget`.
- Produces: `initial_candidates()`, `pareto_frontier(evaluations)`, `choose_next(evaluations, budget)`, `select_winner(evaluations)`.

- [ ] **Step 1: Fixar por testes a agenda inicial e as regras essenciais**

```python
class SearchTests(unittest.TestCase):
    def test_initial_set_contains_fidelity_and_goes_beyond_fifty_percent(self):
        candidates = initial_candidates()
        self.assertEqual(candidates[0].engine, "fidelity")
        self.assertTrue(any(c.target_ratio <= 0.10 for c in candidates))

    def test_smallest_passing_candidate_wins(self):
        winner = select_winner([evaluation(100, True, 0.01), evaluation(60, True, 0.02), evaluation(40, False, 0.20)])
        self.assertEqual(winner.size.total_bytes, 60)

    def test_quality_break_creates_midpoint_candidate(self):
        nxt = choose_next([evaluation_at(0.50, True), evaluation_at(0.25, False)], SearchBudget.experimental_default())
        self.assertAlmostEqual(nxt.target_ratio, 0.375)
```

- [ ] **Step 2: Confirmar falha por módulo ausente**

Run: `python -m unittest tests.maximum_optimizer.test_search -v`

Expected: import falha.

- [ ] **Step 3: Implementar agenda `Fidelity, 0.75, 0.50, 0.35, 0.25, 0.15, 0.10, 0.05`, poda de dominados e bisseção entre último aprovado/primeiro reprovado**

```python
def select_winner(evaluations: list[CandidateEvaluation]) -> CandidateEvaluation | None:
    passing = [item for item in evaluations if item.passed]
    if not passing:
        return None
    return min(passing, key=lambda item: (item.size.total_bytes, -item.visual.metrics.get("fidelity_score", 0.0)))
```

A busca não gera ratio abaixo de `0.01`, não repete `candidate_id`, respeita `max_candidates`, encerra quando o intervalo fica menor que `min_ratio_step` ou quando a economia compilada marginal é menor que `min_marginal_saving`.

- [ ] **Step 4: Adicionar teste de mapa de erro que cria `region_overrides=((scope, recovered_ratio),)` somente para a pior região reprovada**

Run: `python -m unittest tests.maximum_optimizer.test_search -v`

Expected: agenda, Pareto, desempate, bisseção, parada e recuperação regional aprovados.

- [ ] **Step 5: Commit**

```powershell
git add maximum_optimizer/search.py tests/maximum_optimizer/test_search.py
git commit -m "feat: add adaptive compiled-size search"
```

---

### Task 6: Cache consistente, cancelamento e promoção atômica

**Files:**
- Create: `maximum_optimizer/cache.py`
- Create: `tests/maximum_optimizer/test_cache.py`

**Interfaces:**
- Produces: `CacheKey.build(...)`; `CandidateCache.lookup/store`; `atomic_replace_tree(staging, destination)`.
- Consumes: payloads serializáveis de manifest, candidato, tools e perfil.

- [ ] **Step 1: Testar invalidação por conteúdo/tool/profile, recusa de entrada sem `complete.json` e preservação da saída quando a promoção falha**

```python
def test_cache_key_changes_with_profile_version(self):
    a = CacheKey.build("input", {"id": "c"}, {"blender": "4.3"}, "profile-a")
    b = CacheKey.build("input", {"id": "c"}, {"blender": "4.3"}, "profile-b")
    self.assertNotEqual(a.digest, b.digest)
```

- [ ] **Step 2: Confirmar falha por módulo ausente**

Run: `python -m unittest tests.maximum_optimizer.test_cache -v`

Expected: import falha.

- [ ] **Step 3: Implementar escrita em `{digest}.tmp-{pid}`, fsync de JSON, rename para `{digest}` e marker `complete.json` escrito por último**

```python
@dataclass(frozen=True)
class CacheKey:
    digest: str

    @classmethod
    def build(cls, input_hash: str, candidate: dict, tools: dict, profile_version: str) -> "CacheKey":
        raw = json.dumps({"input": input_hash, "candidate": candidate, "tools": tools, "profile": profile_version}, sort_keys=True, separators=(",", ":"))
        return cls(hashlib.sha256(raw.encode("utf-8")).hexdigest())
```

- [ ] **Step 4: Implementar promoção no mesmo volume com backup temporário e rollback testado; cancelamento remove apenas `.tmp-*` incompletos**

Run: `python -m unittest tests.maximum_optimizer.test_cache -v`

Expected: todos aprovados, inclusive falha injetada entre backup e rename.

- [ ] **Step 5: Commit**

```powershell
git add maximum_optimizer/cache.py tests/maximum_optimizer/test_cache.py
git commit -m "feat: add resumable Maximum candidate cache"
```

---

### Task 7: Execução cancelável e adapters Blender/Fidelity

**Files:**
- Create: `maximum_optimizer/processes.py`
- Create: `maximum_optimizer/candidates.py`
- Create: `tests/maximum_optimizer/test_candidates.py`

**Interfaces:**
- Produces: `run_process(command, cwd, log_path, cancel_event) -> ProcessResult`; protocolo `CandidateAdapter.generate(manifest, spec, workspace, tools) -> CandidateBuild`.
- Consumes: scripts atuais `batch_optimize_round_parts_policy.py`, `batch_compile_opt_qc.py` e `vehicle_steer_turn_basis_fix.py` sem alterar seu comportamento.

- [ ] **Step 1: Criar fake executables Python que aprovam, falham e aguardam; testar stdout/stderr, exit code e encerramento da árvore no cancelamento**

Run: `python -m unittest tests.maximum_optimizer.test_candidates.ProcessTests -v`

Expected: falha por módulos ausentes.

- [ ] **Step 2: Implementar `run_process` com `CREATE_NEW_PROCESS_GROUP` no Windows, polling de 100 ms e `taskkill /T /F` somente para o PID criado**

```python
@dataclass(frozen=True)
class ProcessResult:
    command: tuple[str, ...]
    returncode: int
    elapsed_seconds: float
    log_path: Path


@dataclass(frozen=True)
class CandidateBuild:
    spec: CandidateSpec
    workspace: Path
    optimized_qc: Path
    compiled_models_dir: Path
    compile_record: dict
    provenance: dict[str, str]
    commands: tuple[tuple[str, ...], ...]
```

O comando completo é registrado no log com quoting; a função nunca usa shell string.

- [ ] **Step 3: Testar que `FidelityAdapter` clona somente a família para o workspace e monta os mesmos parâmetros do modo Fidelity atual**

```python
def test_fidelity_adapter_preserves_validated_stack(self):
    build = self.adapter.generate(self.manifest, self.spec, self.workspace, self.tools)
    joined = " ".join(build.commands[0])
    self.assertIn("batch_optimize_round_parts_policy.py", joined)
    self.assertIn("silhouette_floor_20", joined)
    self.assertIn("floor_24", joined)
```

- [ ] **Step 4: Implementar `FidelityAdapter` e `BlenderAdapter`; ambos geram `_OPT.qc`, chamam StudioMDL no workspace isolado e retornam proveniência `candidate-compile` por artefato**

Run: `python -m unittest tests.maximum_optimizer.test_candidates -v`

Expected: montagem de comandos, isolamento, falha e cancelamento aprovados usando fakes.

- [ ] **Step 5: Commit**

```powershell
git add maximum_optimizer/processes.py maximum_optimizer/candidates.py tests/maximum_optimizer/test_candidates.py
git commit -m "feat: generate isolated Maximum candidates"
```

---

### Task 8: Validação visual multiângulo e calibrador (sem calibração real nesta etapa)

**Files:**
- Modify: `render_previews.py`
- Create: `maximum_optimizer/visual_validation.py`
- Create: `calibrate_maximum_profiles.py`
- Create: `maximum_optimizer/profiles/schema-v1.json`
- Create: `maximum_optimizer/profiles/maximum-experimental-v1.json`
- Create: `tests/corpus/maximum/corpus.json`
- Create: `tests/maximum_optimizer/test_visual_validation.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `load_profile(path) -> FidelityProfile`; `compare_render_sets(reference_dir, candidate_dir, profile) -> ValidationResult`; CLI de calibração.
- Consumes: renders `textured` e `clay` nos ângulos `front,back,left,right,top,bottom,iso1,iso2`, mais poses definidas no manifest.

- [ ] **Step 1: Adicionar `Pillow==12.3.0` e escrever testes com imagens sintéticas em que a média passa, mas um único ângulo ruim reprova**

```python
def test_worst_angle_fails_even_when_average_is_small(self):
    result = compare_render_sets(self.reference, self.candidate, self.profile)
    self.assertFalse(result.passed)
    self.assertEqual(result.worst_scope, "clay/right")
    self.assertIn("silhouette_iou", {failure.gate for failure in result.failures})
```

- [ ] **Step 2: Confirmar falha e instalar a dependência pinada no ambiente de desenvolvimento**

Run:

```powershell
python -m pip install Pillow==12.3.0
python -m unittest tests.maximum_optimizer.test_visual_validation -v
```

Expected: teste falha porque o módulo ainda não existe.

- [ ] **Step 3: Estender `render_previews.py` com `--passes textured,clay`, `--poses`, `--materials-root`, câmera determinística e `render_manifest.json` contendo SHA-256, ângulo, pose, bounding box e métricas geométricas por região**

O passe `textured` resolve VMT/VTF pelo material root e registra explicitamente `texture_missing`; o passe `clay` mantém material controlado. Para cada região e pose, o script usa `BVHTree` para amostrar distância candidato->original e original->candidato, calcula p95/máximo de distância normalizada, p95 do ângulo de normais, p95 do deslocamento UV e erro de posição após skinning. O modo antigo sem os novos argumentos deve continuar gerando `preview_summary.json` exatamente como hoje; adicionar um teste de parsing de argumentos para essa compatibilidade.

- [ ] **Step 4: Implementar métricas de imagem com Pillow e gates geométricos: alpha/silhouette IoU, MAE RGB linearizado, erro de borda por dilatação de 1 pixel, distância bidirecional, normal, UV e skinning; agregar `max` por ângulo/pose/passe/região**

```python
def _gate(metric: str, scope: str, value: float, limit: float, failures: list[GateFailure]) -> None:
    if value > limit:
        failures.append(GateFailure(metric, scope, value, limit, f"{metric} exceeded at {scope}"))
```

- [ ] **Step 5: Registrar 20 famílias reais e paths de métricas a gerar, testar o calibrador com corpus temporário e gravar somente o sentinel não calibrado**

O `tests/corpus/maximum/corpus.json` registra IDs/`model_rel` reais sob `${MAXIMUM_CALIBRATION_ROOT}` e paths de `roundtrip`, `known_good` e `known_bad`, mas a geração dessas métricas reais pertence à Task 14, depois de engines/orchestrator. Nesta etapa, os testes do calibrador usam um corpus temporário de 20 famílias.

O calibrador implementa `max(hard_floor, roundtrip_p99 + 3 * MAD)`, hash determinístico do conteúdo lógico/bytes das métricas, expansão estrita de ambiente e escrita atômica. Ele recusa menos de 20 famílias, qualquer métrica ausente/não finita, `known_good` acima do limite ou qualquer `known_bad` que não exceda ao menos um limite. Nenhum addon de terceiros é commitado.

`maximum_optimizer/profiles/maximum-experimental-v1.json` permanece um sentinel honesto com `calibrated: false` e `limits: {}`. `load_profile` deve recusá-lo; nenhum threshold real é inventado na Task 8.

Run: `python -m unittest tests.maximum_optimizer.test_visual_validation -v`

Expected: perfil inválido, pior caso, imagem ausente, calibração insuficiente/aprovada testados; JSON gerado sem campo nulo.

- [ ] **Step 6: Commit**

```powershell
git add requirements.txt render_previews.py maximum_optimizer/visual_validation.py maximum_optimizer/profiles calibrate_maximum_profiles.py tests/maximum_optimizer/test_visual_validation.py tests/corpus/maximum/corpus.json
git commit -m "feat: validate Maximum visual fidelity"
```

---

### Task 9: Bridge meshoptimizer v1.2 e reparo de aparência no Blender

**Files:**
- Create: `third_party/meshoptimizer/` from tag `v1.2`
- Create: `maximum_optimizer/native/CMakeLists.txt`
- Create: `maximum_optimizer/native/meshopt_bridge.cpp`
- Create: `maximum_optimizer/native/build.ps1`
- Create: `maximum_optimizer/meshopt_bridge.py`
- Create: `batch_optimize_maximum.py`
- Create: `tests/maximum_optimizer/test_meshopt_bridge.py`
- Create: `tests/maximum_optimizer/test_maximum_blender_args.py`

**Interfaces:**
- Produces C ABI: `maximum_meshopt_simplify(const MaximumMeshInput*, const MaximumMeshOptions*, MaximumMeshOutput*)`; Python `simplify_mesh(mesh, options) -> SimplifiedMesh`.
- Consumes meshoptimizer `meshopt_simplifyWithAttributes`/`meshopt_simplifyWithUpdate`; Blender meshes importados pelo Source Tools.

- [ ] **Step 1: Vendorizar somente `LICENSE.md`, `src/*.cpp` e `src/meshoptimizer.h` do commit pinado e registrar `third_party/meshoptimizer/VERSION` com `v1.2` e o hash completo**

Run:

```powershell
git clone --branch v1.2 --depth 1 https://github.com/zeux/meshoptimizer.git build/meshoptimizer-vendor
git -C build/meshoptimizer-vendor rev-parse HEAD
New-Item -ItemType Directory -Force third_party/meshoptimizer/src | Out-Null
Copy-Item build/meshoptimizer-vendor/LICENSE.md third_party/meshoptimizer/LICENSE.md
Copy-Item build/meshoptimizer-vendor/src/*.cpp third_party/meshoptimizer/src/
Copy-Item build/meshoptimizer-vendor/src/meshoptimizer.h third_party/meshoptimizer/src/meshoptimizer.h
```

Criar `third_party/meshoptimizer/VERSION` via `apply_patch` com uma única linha: `v1.2 9d9890c73011d75920af614485296d1e03e95448`.

Expected: `9d9890c73011d75920af614485296d1e03e95448`.

- [ ] **Step 2: Escrever um teste ABI que simplifica um grid, preserva quatro borders bloqueadas, mantém índices válidos e reduz a quantidade de triângulos**

Run:

```powershell
$env:MAXIMUM_MESHOPT_DLL='maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll'
python -m unittest tests.maximum_optimizer.test_meshopt_bridge -v
```

Expected antes do build: FAIL explícito `meshopt_bridge.dll not built`; o teste não usa skip.

- [ ] **Step 3: Criar `CMakeLists.txt` x64 e bridge que valida ponteiros/counts, usa atributos intercalados `normal.xyz, uv.xy, weights.xyzw`, flags de lock/protect/priority e retorna código de erro sem lançar exceção através da ABI**

```cpp
extern "C" __declspec(dllexport) int maximum_meshopt_version() { return 10200; }
```

O bridge usa `meshopt_simplifyWithAttributes` para o primeiro candidato e `meshopt_simplifyWithUpdate` quando `options.update_vertices != 0`; UVs e pesos são renormalizados após update, e materiais são processados por subset com borders protegidas.

- [ ] **Step 4: Criar `maximum_optimizer/native/build.ps1`, fazer build x64 em staging e copiar atomicamente a DLL para `maximum_optimizer/native/bin/win-x64`; então rodar o teste ABI real**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File maximum_optimizer/native/build.ps1
$env:MAXIMUM_MESHOPT_DLL=(Resolve-Path maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll)
python -m unittest tests.maximum_optimizer.test_meshopt_bridge -v
```

Expected: grid reduzido, borders preservadas e zero acesso inválido.

- [ ] **Step 5: Implementar `batch_optimize_maximum.py` com funções puras testáveis para argumentos e profiles; dentro do Blender, classificar por geometria, aplicar locks de silhueta/border/material/peça fina, simplificar, projetar na superfície original, transferir normal/UV/pesos e exportar `_OPT.qc`**

Regras exatas: não usar nome do modelo para decidir classe; material boundaries usam `Protect`; skinned usa `SimplifyRegularizeLight`; região reprovada recebe `region_overrides`; pesos são clampados em `[0,1]`, renormalizados e rejeitados se a soma ficar zero.

- [ ] **Step 6: Executar fixture real no Blender em dois ratios e confirmar que o SMD exportado mantém materiais/bones e reduz triângulos**

Run:

```powershell
if ([string]::IsNullOrWhiteSpace($env:BLENDER_EXE) -or !(Test-Path $env:BLENDER_EXE)) { throw 'Set BLENDER_EXE to the validated blender.exe.' }
& $env:BLENDER_EXE --background --python batch_optimize_maximum.py -- tests/fixtures/maximum/source --candidate-json tests/fixtures/maximum/candidate-r035.json --meshopt-dll maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll
```

Expected: exit 0, `_OPT.qc` criado, `candidate_metrics.json` com `engine=meshoptimizer`, redução positiva e zero `attribute_repair_failures`.

- [ ] **Step 7: Commit**

```powershell
git add third_party/meshoptimizer maximum_optimizer/native maximum_optimizer/meshopt_bridge.py batch_optimize_maximum.py tests/maximum_optimizer/test_meshopt_bridge.py tests/maximum_optimizer/test_maximum_blender_args.py
git commit -m "feat: add attribute-aware meshoptimizer candidates"
```

---

### Task 10: Orchestrator Maximum, relatório e integração CLI

**Files:**
- Create: `maximum_optimizer/reporting.py`
- Create: `maximum_optimizer/orchestrator.py`
- Create: `tests/maximum_optimizer/test_orchestrator.py`
- Create: `tests/maximum_optimizer/test_cli.py`
- Modify: `build_optimized_addon.py`
- Modify: `worker/worker_main.py`

**Interfaces:**
- Produces: `run_maximum_addon(config: MaximumRunConfig, cancel_event=None) -> MaximumRunReport`; `run_maximum_from_existing_args(args, *, repo_root, addon_path, out_addon_dir, work_dir) -> int`; stdout `MAXIMUM_EVENT {json}`; `logs/maximum_report.json`.
- Consumes: todas as interfaces Python anteriores e tools já resolvidas por `build_optimized_addon.py`.

`MaximumRunConfig` contém `addon_dir`, `output_dir`, `work_dir`, `blender_path`, `studiomdl_path`, `repo_root`, `budget`, `profile_path`, `resume` e `overwrite`; `MaximumRunReport` contém snapshots original/control/final, versões, outcomes e `report_path`. Ambos são dataclasses frozen definidos em `orchestrator.py` e serializados por `reporting.py`.

- [ ] **Step 1: Criar adapters fake e teste end-to-end de uma família com candidatos 100/60/40 bytes, onde 40 reprova e 60 é promovido**

```python
def test_orchestrator_promotes_smallest_passing_candidate(self):
    report = run_maximum_addon(self.config, adapters=self.adapters, validator=self.validator)
    self.assertEqual(report.families[0].selected_candidate, "candidate-60")
    self.assertEqual(report.final_size.total_bytes, 60)
    self.assertTrue((self.config.output_dir / "models" / "test.mdl").exists())
```

- [ ] **Step 2: Adicionar testes de nenhuma variante aprovada, cancelamento, cache hit, compile failure isolado e batch com uma família preservada**

Run: `python -m unittest tests.maximum_optimizer.test_orchestrator -v`

Expected: falha por orchestrator ausente.

- [ ] **Step 3: Implementar o loop `generate -> compile -> size -> structural -> visual -> search`, emitindo evento após cada transição e escrevendo relatório parcial atomicamente**

```python
def emit_event(kind: str, **payload: object) -> None:
    print("MAXIMUM_EVENT " + json.dumps({"schema": 1, "kind": kind, **payload}, ensure_ascii=False, separators=(",", ":")), flush=True)
```

Eventos obrigatórios: `run_started`, `family_started`, `candidate_started`, `stage`, `candidate_finished`, `best_updated`, `family_finished`, `run_finished`, `run_cancelled`.

- [ ] **Step 4: Implementar relatório com tamanhos original/control/selected por extensão, vértices por LOD, savings geométrico/opcional, métricas piores, famílias `optimized/preserved/failed/cancelled` e versões das tools**

Run: `python -m unittest tests.maximum_optimizer.test_orchestrator -v`

Expected: schema, promoção, preservação explícita e contabilidade aprovados.

- [ ] **Step 5: Adicionar `OPTIMIZER_MODE_MAXIMUM = "maximum"`, choice do argparse e branch no início de `_run_single_addon` após a resolução das entradas; Normal/Fidelity permanecem no corpo atual**

```python
if args.optimizer_mode == OPTIMIZER_MODE_MAXIMUM:
    return run_maximum_from_existing_args(args, repo_root=repo_root, addon_path=addon_path, out_addon_dir=out_addon_dir, work_dir=work_dir)
```

- [ ] **Step 6: Testar CLI sem tools reais usando mocks e confirmar que `worker_main.main()` propaga `--optimizer-mode maximum` sem consumir o argumento**

Run: `python -m unittest tests.maximum_optimizer.test_cli -v`

Expected: choices `normal/fidelity/maximum`, exit codes 0/1/2 e roteamento aprovados.

- [ ] **Step 7: Commit**

```powershell
git add maximum_optimizer/reporting.py maximum_optimizer/orchestrator.py build_optimized_addon.py worker/worker_main.py tests/maximum_optimizer/test_orchestrator.py tests/maximum_optimizer/test_cli.py
git commit -m "feat: orchestrate Maximum model optimization"
```

---

### Task 11: Protocolo e testes WPF

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor.Tests/GmodAddonCompressor.Tests.csproj`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor.Tests/SourceAddonOptimizerProgressParserTests.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Properties/AssemblyInfo.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor.sln`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerProgressParser.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer/SourceAddonOptimizerRunner.cs`

**Interfaces:**
- Produces em C#: campos `MaximumKind`, `FamilyId`, `FamilyIndex`, `FamilyTotal`, `CandidateId`, `CandidateIndex`, `CandidateTotal`, `Stage`, `BestBytes`, `ReductionPercent`, `GateStatus`, `ReportPath` em `SourceAddonOptimizerProgressUpdate`.
- Consumes: prefixo JSONL `MAXIMUM_EVENT ` schema 1.

- [ ] **Step 1: Criar projeto MSTest net6.0-windows com `Microsoft.NET.Test.Sdk 18.7.0`, `MSTest.TestAdapter 4.3.0`, `MSTest.TestFramework 4.3.0` e referência ao WPF**

Adicionar `[assembly: InternalsVisibleTo("GmodAddonCompressor.Tests")]` em `Properties/AssemblyInfo.cs`.

- [ ] **Step 2: Escrever testes para evento válido, JSON inválido, schema desconhecido e compatibilidade das regex antigas**

```csharp
[TestMethod]
public void ParsesMaximumBestUpdated()
{
    var update = new SourceAddonOptimizerProgressParser().Parse(
        "MAXIMUM_EVENT {\"schema\":1,\"kind\":\"best_updated\",\"family_id\":\"abc\",\"candidate_id\":\"r035\",\"best_bytes\":600,\"reduction_percent\":40.0,\"gate_status\":\"PASS\"}");
    Assert.AreEqual("best_updated", update!.MaximumKind);
    Assert.AreEqual(600L, update.BestBytes);
    Assert.AreEqual(40.0, update.ReductionPercent);
}
```

- [ ] **Step 3: Confirmar falha**

Run: `dotnet test GmodAddonCompressor-master/GmodAddonCompressor.Tests/GmodAddonCompressor.Tests.csproj -c Release`

Expected: propriedades/parser ainda ausentes.

- [ ] **Step 4: Implementar DTO JSON interno e parsing com `System.Text.Json`; em erro/schema desconhecido retornar `null` e deixar a linha somente no log**

O parser testa `MAXIMUM_EVENT ` antes das regex atuais. Não remover nem alterar a semântica de `_step`, `_item`, `_packaging`, `_finalize`, `_batchAddon`, `_output` e `_workDir`.

- [ ] **Step 5: Rodar testes C#**

Run: `dotnet test GmodAddonCompressor-master/GmodAddonCompressor.Tests/GmodAddonCompressor.Tests.csproj -c Release`

Expected: todos aprovados.

- [ ] **Step 6: Commit**

```powershell
git add GmodAddonCompressor-master/GmodAddonCompressor.Tests GmodAddonCompressor-master/GmodAddonCompressor.sln GmodAddonCompressor-master/GmodAddonCompressor/Properties/AssemblyInfo.cs GmodAddonCompressor-master/GmodAddonCompressor/Systems/Optimizer
git commit -m "feat: parse Maximum worker progress"
```

---

### Task 12: Opção Maximum, progresso e relatório na UI WPF

**Files:**
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/DataContexts/MainWindowContext.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor.Tests/MainWindowContextOptimizerModeTests.cs`

**Interfaces:**
- Consumes: propriedades novas de `SourceAddonOptimizerProgressUpdate`.
- Produces: `OptimizerModeMaximumChecked`, `OptimizerModeIsMaximum`, `MaximumProgressText`, `MaximumBestText`, e argumento `maximum`.

- [ ] **Step 1: Escrever testes do view-model para índices 0/1/2, notificações e descrição experimental**

```csharp
[TestMethod]
public void MaximumModeMapsToIndexTwo()
{
    var context = new MainWindowContext { OptimizerModeMaximumChecked = true };
    Assert.AreEqual(2, context.OptimizerModeIndex);
    Assert.IsTrue(context.OptimizerModeIsMaximum);
    StringAssert.Contains(context.OptimizerModeDescriptionText, "compiled Source bytes");
}
```

- [ ] **Step 2: Confirmar falha**

Run: `dotnet test GmodAddonCompressor-master/GmodAddonCompressor.Tests/GmodAddonCompressor.Tests.csproj -c Release`

Expected: propriedades Maximum ausentes.

- [ ] **Step 3: Adicionar `Maximum (experimental)` à lista, checkbox exclusivo e descrição; manter índices antigos 0 e 1 para settings já salvos**

```csharp
private const int OptimizerModeFidelityIndex = 1;
private const int OptimizerModeMaximumIndex = 2;

private string GetOptimizerModeArgument() => _context.OptimizerModeIndex switch
{
    OptimizerModeFidelityIndex => "fidelity",
    OptimizerModeMaximumIndex => "maximum",
    _ => "normal",
};
```

- [ ] **Step 4: No XAML, adicionar o terceiro checkbox e um painel somente visível em Maximum com família/candidato, melhor tamanho/redução, gate e estágio**

Criar `OptimizerManualTuningEnabled => !OptimizerModeIsMaximum`, notificar essa propriedade em `NotifyOptimizerModePropertiesChanged()` e vinculá-la ao painel de ratio/merge/autosmooth. Assim os controles continuam disponíveis para Normal/Fidelity e ficam desabilitados no Maximum, cuja agenda é automática.

- [ ] **Step 5: Atualizar `OptimizerProgressUpdate` para preencher as propriedades Maximum e carregar `maximum_report.json` no relatório final; Pipeline reutiliza o mesmo modo salvo**

Run:

```powershell
dotnet test GmodAddonCompressor-master/GmodAddonCompressor.Tests/GmodAddonCompressor.Tests.csproj -c Release
dotnet build GmodAddonCompressor-master/GmodAddonCompressor/GmodAddonCompressor.csproj -c Release
```

Expected: testes e build aprovados, sem erro de binding em output de debug.

- [ ] **Step 6: Commit**

```powershell
git add GmodAddonCompressor-master/GmodAddonCompressor/DataContexts/MainWindowContext.cs GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml.cs GmodAddonCompressor-master/GmodAddonCompressor.Tests/MainWindowContextOptimizerModeTests.cs
git commit -m "feat: expose Maximum mode in WPF"
```

---

### Task 13: Empacotamento reproduzível e proteção contra worker stale

**Files:**
- Modify: `pyinstaller/worker.spec`
- Modify: `pyinstaller/package_wpf_tools.ps1`
- Modify: `build_release_wpf.ps1`
- Create: `tests/test_package_wpf_tools.ps1`

**Interfaces:**
- Produces ZIP contendo worker, Crowbar, `meshopt_bridge.dll`, perfil, licença e dependências Pillow.
- Consumes: módulos/scripts criados nas Tasks 1-10.

- [ ] **Step 1: Escrever teste PowerShell que cria worker fake antigo, toca um arquivo em `maximum_optimizer/` e exige `Test-WorkerRebuildNeeded == $true`; testar também entradas ZIP obrigatórias**

Run: `powershell -ExecutionPolicy Bypass -File tests/test_package_wpf_tools.ps1`

Expected: falha porque o package script ainda não monitora o diretório novo.

- [ ] **Step 2: Incluir `maximum_optimizer`, `batch_optimize_maximum.py`, `calibrate_maximum_profiles.py`, perfil JSON, DLL e `third_party/meshoptimizer/LICENSE.md` no spec; adicionar hidden imports Pillow necessários detectados pelo build real**

- [ ] **Step 3: Fazer `Get-WorkerSourceFiles` enumerar recursivamente os módulos novos e native DLL; `Assert-PackageZip` exige `meshopt_bridge.dll`, perfil e licença, além das entradas atuais**

- [ ] **Step 4: Alterar release oficial para construir o bridge, executar Python tests e `dotnet test` antes de empacotar; manter clean/publish atual depois dos testes**

```powershell
Invoke-Step -Name "Build meshoptimizer bridge" -Action {
    & (Join-Path $repoRoot "maximum_optimizer\native\build.ps1")
    if ($LASTEXITCODE -ne 0) { throw "meshoptimizer bridge build failed." }
}
Invoke-Step -Name "Run Python tests" -Action {
    & python -m unittest discover -s tests -t . -p "test_*.py" -v
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed." }
}
Invoke-Step -Name "Run WPF tests" -Action {
    Invoke-Dotnet @("test", (Join-Path $repoRoot "GmodAddonCompressor-master\GmodAddonCompressor.Tests\GmodAddonCompressor.Tests.csproj"), "-c", $configuration)
}
```

- [ ] **Step 5: Rebuild limpo do worker e teste do ZIP**

Run:

```powershell
pyinstaller --noconfirm --clean pyinstaller/worker.spec
powershell -ExecutionPolicy Bypass -File pyinstaller/package_wpf_tools.ps1
```

Expected: worker exit 0 em `--help`; ZIP contém todas as entradas atuais e novas.

- [ ] **Step 6: Commit**

```powershell
git add pyinstaller/worker.spec pyinstaller/package_wpf_tools.ps1 build_release_wpf.ps1 tests/test_package_wpf_tools.ps1
git commit -m "build: package Maximum optimizer dependencies"
```

---

### Task 14: Calibração, corpus real e aceitação end-to-end

**Files:**
- Modify: `tests/corpus/maximum/corpus.json`
- Create: `docs/maximum-optimizer-validation.md`
- Modify: `maximum_optimizer/profiles/maximum-experimental-v1.json` to replace the Task 8 sentinel with the deterministic `calibrated: true` profile generated from real metrics.
- Modify: `docs/superpowers/specs/2026-07-11-models-maximum-optimizer-design.md` only to record implemented profile/version and measured results.

**Interfaces:**
- Consumes: executável worker empacotado, addons reais separados em `calibration` e `validation`.
- Produces: perfil numérico versionado, relatório consolidado e evidência de validação in-game.

- [ ] **Step 1: Gerar roundtrip/known-good/known-bad reais para as 20 famílias de calibração registradas na Task 8 e acrescentar ao menos 20 famílias de validação diferentes**

As engines e o orchestrator completos geram os manifests/métricas apontados pelo corpus. A cobertura inclui carros/rodas, veículos, aeronaves, armas, attachments, props, multi-material/bodygroup/skin e skinned/animated. O JSON usa caminhos relativos ou variáveis de ambiente; nenhum addon de terceiros é commitado. Cada item de validação inclui `category`, `addon_root`, `model_rel`, `poses` e `manual_views`.

- [ ] **Step 2: Gerar/substituir o sentinel por perfil `calibrated: true` e exigir saída byte a byte idêntica quando a partição calibration não mudou**

Run: `python calibrate_maximum_profiles.py --corpus tests/corpus/maximum/corpus.json --partition calibration --out maximum_optimizer/profiles/maximum-experimental-v1.json`

Expected: exit 0, `family_count >= 20`, hash da partição e limite numérico para cada gate; nenhum campo nulo e nenhuma diferença inesperada no perfil commitado.

Este é o primeiro ponto do plano em que limites reais são produzidos e aceitos pelo runtime.

- [ ] **Step 3: Rodar validação cega e gerar relatório agregado**

Run:

```powershell
if ([string]::IsNullOrWhiteSpace($env:MAXIMUM_VALIDATION_ROOT) -or !(Test-Path $env:MAXIMUM_VALIDATION_ROOT)) { throw 'Set MAXIMUM_VALIDATION_ROOT to the validation addon root.' }
if ([string]::IsNullOrWhiteSpace($env:MAXIMUM_VALIDATION_WORK)) { throw 'Set MAXIMUM_VALIDATION_WORK to an isolated work directory.' }
& dist/GModAddonOptimizerWorker/GModAddonOptimizerWorker.exe $env:MAXIMUM_VALIDATION_ROOT --optimizer-mode maximum --work $env:MAXIMUM_VALIDATION_WORK --single-addon-only --overwrite-work --strict
```

Expected: hard gates 100% aprovados nas famílias promovidas; preservadas aparecem explicitamente; relatório de disco bate byte a byte; nenhuma família reprovada é promovida.

- [ ] **Step 4: Comparar Maximum contra Fidelity com o mesmo perfil; exigir que, com orçamento suficiente, Maximum selecione resultado de tamanho menor ou igual em cada família onde Fidelity passa**

Registrar por família: bytes original/control/fidelity/maximum, redução geométrica, remoções opcionais, candidatos, tempo, pior métrica e motivo de preservação.

- [ ] **Step 5: Validar manualmente no Garry's Mod as vistas/poses marcadas e registrar data, build, modelo, resultado e screenshot path local no documento**

Expected: zero skin/bodygroup/animation/attachment/hitbox ausente, zero crack visível novo e nenhuma falha local acima do perfil. Casos reprovados permanecem preservados.

- [ ] **Step 6: Reempacotar worker/tools com o perfil calibrado, gerar release oficial e validar as abas**

Run: `powershell -ExecutionPolicy Bypass -File .\build_release_wpf.ps1`

Expected: exit 0 e `GmodAddonCompressor-master/GmodAddonCompressor/bin/Release/net6.0-windows/win-x64/publish/GmodAddonOptimizer.exe` existente.

Abrir o executável publicado e confirmar: Models abre; `Maximum (experimental)` persiste; progresso mostra família/candidato/gate/melhor redução; cancelar preserva cache; Resume reutiliza candidato; Normal/Fidelity ainda executam; Pipeline encaminha o modo selecionado.

- [ ] **Step 7: Rodar verificação final e commit de evidências**

Run:

```powershell
python -m unittest discover -s tests -t . -p "test_*.py" -v
dotnet test GmodAddonCompressor-master/GmodAddonCompressor.Tests/GmodAddonCompressor.Tests.csproj -c Release
git diff --check
```

Expected: todos os testes aprovados e `git diff --check` sem saída.

```powershell
git add tests/corpus/maximum/corpus.json maximum_optimizer/profiles/maximum-experimental-v1.json docs/maximum-optimizer-validation.md docs/superpowers/specs/2026-07-11-models-maximum-optimizer-design.md
git commit -m "test: calibrate and validate Maximum optimizer"
```

---

## Checkpoints de revisão

1. Após Task 4: revisar contratos, medição e hard gates antes de qualquer simplificação nova.
2. Após Task 7: executar uma família com adapters Blender/Fidelity e confirmar isolamento/cache.
3. Após Task 9: comparar meshoptimizer versus Blender em pelo menos cinco famílias antes de torná-lo engine principal; se não vencer em bytes compilados com qualidade aprovada, mantê-lo disponível mas não preferido.
4. Após Task 10: revisar relatório/proveniência e testar que nenhuma cópia original é contada como otimização.
5. Após Task 12: validar o fluxo WPF manualmente em build local antes de reconstruir o worker.
6. Após Task 14: somente então decidir se o rótulo experimental pode ser removido; a primeira entrega permanece experimental.

## Ordem e comandos de verificação final

```powershell
python -m unittest discover -s tests -t . -p "test_*.py" -v
dotnet test GmodAddonCompressor-master/GmodAddonCompressor.Tests/GmodAddonCompressor.Tests.csproj -c Release
cmake --build build/meshopt --config Release
pyinstaller --noconfirm --clean pyinstaller/worker.spec
powershell -ExecutionPolicy Bypass -File pyinstaller/package_wpf_tools.ps1
powershell -ExecutionPolicy Bypass -File .\build_release_wpf.ps1
git diff --check
```

Não declarar conclusão apenas porque esses comandos passam: a aceitação também exige o corpus real, a inspeção dentro do Garry's Mod e a conferência de que o relatório corresponde aos bytes finais no disco.
