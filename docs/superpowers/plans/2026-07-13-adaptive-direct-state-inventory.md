# Adaptive Direct Production State Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed production factory that enumerates every bounded active QC state and emits a sealed `AdaptiveDirectStateInventory` from current QC/SMD bytes and typed dependencies.

**Architecture:** `qc_states.py` parses state-affecting QC syntax and produces an exact, bounded active-occurrence product. `smd_state_contracts.py` produces canonical skeleton, bind, optional paired-animation, and equivalence digests from no-follow current bytes. `adaptive_state_inventory.py` binds those proofs to the existing metrics/snapshot domain and constructs sealed rows without scheduler/orchestrator integration.

**Tech Stack:** Python 3.11, frozen dataclasses, existing `QcGraph`, `RecoverySourceSnapshot`, `AdaptiveCandidateMetricsProof`, `SourceFileProof`, no-follow helpers, `unittest`.

## Global Constraints

- State product is every bodygroup combination x every LOD level x every skin row; never truncate.
- Reject zero or more than 16 states before reading any visual SMD.
- Emit rows only for occurrences active in a state.
- LOD syntax preserves exact `original -> replacement` pairs and applies them to every bodygroup combination.
- Unknown/malformed/multiple `$texturegroup` declarations fail closed.
- All SMD/QC bytes are current, bounded, and read no-follow; no absolute path enters canonical payloads.
- Component/material evidence is typed input; never synthesize its hashes.
- Do not modify scheduler, orchestrator, material resolution, source-union, or runners.

---

### Task 1: Exact QC state grammar and bounded enumeration

**Files:**
- Create: `maximum_optimizer/qc_states.py`
- Test: `tests/maximum_optimizer/test_qc_states.py`

**Interfaces:**
- Consumes: `enumerate_qc_states(graph: QcGraph, *, maximum_states: int = 16) -> tuple[QcActiveState, ...]`
- Produces: frozen `QcActiveOccurrence`, `QcActiveState`, canonical `bodygroup_key`, `lod_key`, `skin_key`, `state_key`, and exact source paths/provenance for Task 3.

- [ ] **Step 1: Write failing parser/product tests**

Create synthetic QCs with two bodygroups (`studio/studio` and `blank/studio`), two skins, and one `$lod` block. Assert eight states, active-only occurrences, stable keys across physical roots, and replacement occurrence provenance at LOD1. Assert 17 combinations reject before an injected SMD-read sentinel is called. Assert duplicate/unknown/malformed texturegroups and odd or duplicate LOD pairs reject.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.maximum_optimizer.test_qc_states -q`

Expected: import failure because `maximum_optimizer.qc_states` does not exist.

- [ ] **Step 3: Implement the grammar**

Implement frozen types with these minimum fields:

```python
@dataclass(frozen=True)
class QcActiveOccurrence:
    graph_relative_path: str
    directive: str
    line: int
    source_path: Path
    source_identity: str
    occurrence_ordinal: int

@dataclass(frozen=True)
class QcActiveState:
    state_key: str
    bodygroup_key: str
    lod_key: str
    skin_key: str
    active: tuple[QcActiveOccurrence, ...]
```

Use `QcGraph.files[*].text` plus the existing lexer to parse exactly one optional `$texturegroup`; rows must be nonempty and equal width. Build ordered bodygroup choice vectors with `itertools.product`. Parse every LOD group as ordered pairs of references and reject odd, duplicate-original, or noncanonical mappings. Calculate `bodygroup_product * lod_count * skin_count` before opening any SMD. Use SHA-256 of canonical JSON payloads for keys, prefixed `bodygroup-`, `lod-`, `skin-`, `state-`. Deduplicate exact active occurrence identities and sort final states by `state_key`.

- [ ] **Step 4: Run QC-state tests and verify GREEN**

Run: `python -m unittest tests.maximum_optimizer.test_qc_states -q`

Expected: all tests pass.

### Task 2: Current SMD skeleton, pose, and equivalence contracts

**Files:**
- Create: `maximum_optimizer/smd_state_contracts.py`
- Test: `tests/maximum_optimizer/test_smd_state_contracts.py`

**Interfaces:**
- Consumes: current SMD paths, their containing roots, exact `SourceFileProof`, optional original/candidate animation proof pair.
- Produces: `SmdSkeletonContract`, `SmdPoseContract`, `SmdEquivalenceContract`, payload functions, and no-follow builders for Task 3.

- [ ] **Step 1: Write failing contract tests**

Assert canonical node order and complete bind transforms; deterministic digests across roots; rejection of duplicate/missing nodes, invalid parents, missing/duplicate time0, nonfinite transforms, stale `SourceFileProof`, symlink/reparse input, and oversized bytes. Assert bind-only without an animation pair. Assert an exact original/candidate animation pair adds `animation` with identical frames and deterministic representative positive frame; supplied ambiguity/frame/node mismatch rejects.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.maximum_optimizer.test_smd_state_contracts -q`

Expected: import failure because `maximum_optimizer.smd_state_contracts` does not exist.

- [ ] **Step 3: Implement sealed contracts**

Use frozen dataclasses with `schema=1`, exact algorithms, canonical tuple payloads, and SHA-256 seals. Read with `_read_regular_no_follow(..., max_bytes=64 * 1024 * 1024)` and compare size/hash to `SourceFileProof`. Parse one nodes section and one skeleton section; require all node ids unique, parents valid, and time0 transforms complete/finite. Optional animation pair must have identical canonical nodes and identical ordered frame ids; select the largest positive frame. Build equivalence from source identity/size/hash plus skeleton and pose digests.

- [ ] **Step 4: Run SMD contract tests and verify GREEN**

Run: `python -m unittest tests.maximum_optimizer.test_smd_state_contracts -q`

Expected: all tests pass.

### Task 3: Production typed inventory factory

**Files:**
- Create: `maximum_optimizer/adaptive_state_inventory.py`
- Test: `tests/maximum_optimizer/test_adaptive_state_inventory_factory.py`
- Modify: `maximum_optimizer/composite.py` only if the existing coverage builder must compare the unique base QC occurrence union instead of assuming one state row per metric occurrence.

**Interfaces:**
- Consumes: `FamilyManifest`, `CandidateSpec`, cache digest, original/candidate snapshots, typed `AdaptiveCandidateMetricsProof`, `Mapping[str, AdaptiveStateSourceDependencies]`, and optional typed animation pair.
- Produces: `build_production_adaptive_direct_state_inventory(...) -> AdaptiveDirectStateInventory`.

- [ ] **Step 1: Write failing factory tests**

Build current synthetic original/candidate snapshots and a real sealed metrics proof. Assert every active occurrence/state becomes one row, skin-only states remain distinct, inactive bodygroup choices produce no row, source identity/size/hash come from `SourceFileProof`, dependency fields come from exact typed dependency records, and all row keys/digests are canonical. Assert family/spec/cache/snapshot/metrics/dependency/source-union divergence fails. Assert a mutation during production is caught by final snapshot revalidation.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.maximum_optimizer.test_adaptive_state_inventory_factory -q`

Expected: import failure because `maximum_optimizer.adaptive_state_inventory` does not exist.

- [ ] **Step 3: Implement typed dependency and factory boundary**

Add:

```python
@dataclass(frozen=True)
class AdaptiveStateSourceDependencies:
    source_identity: str
    source_size: int
    source_sha256: str
    component_keys: tuple[str, ...]
    material_region_keys: tuple[str, ...]
    component_manifest_sha256: str
    material_contract_sha256: str
```

Validate metrics bindings against manifest/spec/cache/candidate snapshot, validate both snapshots current, parse the original authoritative graph, enumerate states before SMD work, require the dependency and visual source union to equal metrics sources, build per-source current SMD contracts once, and emit one row per active occurrence/state. Define `occurrence_key` from exact occurrence plus state. Sort rows and call existing `build_adaptive_direct_state_inventory`. Revalidate both snapshots after building.

- [ ] **Step 4: Run factory and existing domain tests**

Run: `python -m unittest tests.maximum_optimizer.test_adaptive_state_inventory_factory tests.maximum_optimizer.test_task6_contracts tests.maximum_optimizer.test_domain -q`

Expected: all tests pass.

### Task 4: Read-only real wheel/car evidence and verification

**Files:**
- Modify: `tests/maximum_optimizer/test_qc_states.py`

**Interfaces:**
- Consumes: checked-in `.superpowers/benchmark/task4_smoothing_fixed_v1` Pontiac wheel and Dodge Charger source trees.
- Produces: regression evidence that the real wheel has two states and the real Charger rejects above 16 before SMD reads.

- [ ] **Step 1: Add read-only corpus tests**

Load `wheel.qc` and `charger.qc` with `parse_qc_graph`. Record source-file mtimes/hashes before enumeration and assert they remain unchanged afterward. Assert wheel has exactly two states. Assert Charger raises the explicit 16-state bound before invoking the SMD contract builder.

- [ ] **Step 2: Run the complete focused suite**

Run:

```powershell
python -m unittest tests.maximum_optimizer.test_qc_states tests.maximum_optimizer.test_smd_state_contracts tests.maximum_optimizer.test_adaptive_state_inventory_factory tests.maximum_optimizer.test_adaptive_metrics_factory tests.maximum_optimizer.test_task6_contracts tests.maximum_optimizer.test_composite tests.maximum_optimizer.test_domain -q
```

Expected: all tests pass, with only explicit corpus-absence skips.

- [ ] **Step 3: Verify and commit only owned files**

Run `git diff --check`, inspect `git status --short`, exclude `.superpowers/sdd/progress.md` and parallel scheduler/orchestrator changes, then commit the six implementation/test files and any necessary `composite.py` adjustment as:

```powershell
git commit -m "feat(maximum): enumerate adaptive production states"
```
