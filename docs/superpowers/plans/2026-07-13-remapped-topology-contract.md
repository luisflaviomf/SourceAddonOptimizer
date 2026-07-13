# Remapped Topology R&D Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a sealed, fail-closed R&D contract that accepts only structurally conservative meshoptimizer remapped topology while leaving the retained-cycle direct-position contract unchanged.

**Architecture:** A focused pure-Python module parses and validates source/output SMD pairs and emits a sealed immutable proof with deterministic source-corner ordinal provenance. The batch optimizer receives a separate strategy/transfer identity that uses the existing no-update meshoptimizer generation mechanism but is never confused with direct-position authority. A local smoke tool validates real artifacts and only invokes StudioMDL after structural acceptance.

**Tech Stack:** Python 3.11, `unittest`, existing SMD parser and meshoptimizer/Blender research pipeline, StudioMDL local integration.

## Global Constraints

- Keep `meshopt-direct-position-v1` / `direct-position-v1` and `_validate_direct_smd_output` behavior unchanged.
- New identity is `meshopt-remapped-topology-v1` / `remapped-topology-v1`.
- Passing the contract never claims visual quality; status remains `unverified` until render gates pass.
- Fixed caps are 64 MiB per text, 1,000,000 triangles per text, 4,096 materials, 100,000 source components, and 64 tokens per corner.
- Do not modify `production_adapters.py`, `production_authority_bundle.py`, scheduler selection, WPF, or worker integration.

---

### Task 1: Sealed structural validator

**Files:**
- Create: `maximum_optimizer/remapped_topology.py`
- Create: `tests/maximum_optimizer/test_remapped_topology.py`

**Interfaces:**
- Consumes: `parse_smd_triangles(source_text: str)` from `maximum_optimizer.smd_contract`.
- Produces: `validate_remapped_topology_smd(source_text: str, output_text: str, requested_ratio: float) -> RemappedTopologyProof`, `remapped_topology_proof_payload(proof, include_seal=True) -> dict[str, object]`, and `remapped_topology_proof_from_payload(payload) -> RemappedTopologyProof`.

- [ ] **Step 1: Write failing happy-path and old-contract-isolation tests**

Construct a two-quad fixture where the output contains a valid diagonal-remapped triangle made entirely from exact source corner tuples. Assert the old `_validate_direct_smd_output` rejects it, while the new function is initially unavailable. Assert the desired proof identity, counts, and deterministic corner ordinal stream.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m unittest tests.maximum_optimizer.test_remapped_topology.RemappedTopologyContractTests.test_accepts_exact_remapped_cycle_and_seals_deterministic_provenance -v`

Expected: FAIL because `maximum_optimizer.remapped_topology` does not exist.

- [ ] **Step 3: Implement the minimal proof and valid remap path**

Implement immutable proof dataclasses, strict prefix/EOF/material/ratio checks, exact same-material full-token corner lookup, deterministic component-local ordinal selection, retained/new-cycle counts, and canonical SHA-256 payload sealing.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 5: Add adversarial RED tests**

Add one focused test per invariant: synthesized payload, material borrowing, component bridge/deletion, ambiguous duplicate provenance, boundary replacement, duplicate/reversed triangle, degenerate triangle, increased nonmanifold excess/max valence, increased directed manifold conflict, increased all-normal-opposite faces, ratio excess, each fixed cap, and tampered proof payload.

- [ ] **Step 6: Run adversarial tests and verify RED**

Run: `python -m unittest tests.maximum_optimizer.test_remapped_topology -v`

Expected: FAIL on the first not-yet-implemented invariant, with the failure naming that invariant.

- [ ] **Step 7: Implement topology analysis and fail-closed parser**

Build deterministic material/component indexes; exact boundary sets; per-component valence, nonmanifold excess, directed edge conflicts, and normal-hemisphere counts. Bind the canonical source duplicate-group multiset and complete output ordinal stream into the proof. Implement exact-field payload loading that recomputes the seal and enforces all relationships.

- [ ] **Step 8: Verify focused and neighboring tests**

Run: `python -m unittest tests.maximum_optimizer.test_remapped_topology tests.maximum_optimizer.test_task6_direct_builder tests.maximum_optimizer.test_smd_contract_prefilter -v`

Expected: all pass; existing direct-position winding/cycle rejection remains unchanged.

- [ ] **Step 9: Commit checkpoint**

Commit only the new validator and its tests with message `feat(maximum): add sealed remapped topology contract`.

### Task 2: Explicit batch R&D discriminator

**Files:**
- Modify: `batch_optimize_maximum.py`
- Modify: `tests/maximum_optimizer/test_maximum_blender_args.py`
- Modify: `tests/maximum_optimizer/test_meshopt_bridge.py`

**Interfaces:**
- Consumes: candidate JSON strategy `meshopt-remapped-topology-v1`, transfer `remapped-topology-v1`, `update_vertices=false`, and the existing direct-degenerate prefilter identity.
- Produces: direct no-update SMD output and metrics labeled only with the new strategy/transfer identity.

- [ ] **Step 1: Write RED discriminator tests**

Assert the new exact strategy tuple loads, an old strategy with the new transfer and a new strategy with `direct-v1` both reject, direct prefilter accepts the new strategy, and existing direct-position payloads retain their exact identity.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.maximum_optimizer.test_maximum_blender_args -v`

Expected: FAIL because the new tuple is unknown.

- [ ] **Step 3: Add the minimal isolated strategy routing**

Route the new strategy through exact-float32 wedge construction, position-topology flags, no-update meshoptimizer simplification, exact source-corner serialization, direct prefilter, and direct-object checks. Emit `remapped-topology-v1`; do not change the strings or routing of either existing direct strategy.

- [ ] **Step 4: Verify batch and bridge suites**

Run: `python -m unittest tests.maximum_optimizer.test_maximum_blender_args tests.maximum_optimizer.test_meshopt_bridge tests.maximum_optimizer.test_remapped_topology -v`

Expected: all pass.

- [ ] **Step 5: Commit checkpoint**

Commit only batch/test changes with message `feat(maximum): discriminate remapped topology R&D`.

### Task 3: Real LVS smoke and compile gate

**Files:**
- Create: `benchmarks/lvs_models/smoke_remapped_topology_v1.py`
- Create: `tests/maximum_optimizer/test_remapped_topology_smoke.py`
- Create only if reproducible source artifacts are available: `benchmarks/lvs_models/remapped_topology_v1_observations.json`

**Interfaces:**
- Consumes: explicit `--case NAME=SOURCE=OUTPUT` arguments and optional `--compile-command` template after validation.
- Produces: JSON observations labeled `local_experiment`, `quality_status=unverified`, contract pass/failure reason, source/output bytes and triangles, proof seal, and optional StudioMDL return code/artifact sizes.

- [ ] **Step 1: Write RED smoke-gating tests**

Use a fake compile callback to assert it runs for an accepted proof and is never called for a structurally rejected pair. Assert output JSON cannot encode a visual winner or quality claim.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.maximum_optimizer.test_remapped_topology_smoke -v`

Expected: FAIL because the smoke module does not exist.

- [ ] **Step 3: Implement deterministic smoke reporting**

Validate before compile, capture exact hashes/counts/proof seal, run compile only after acceptance, and keep visual quality fields fixed to `unverified`/`null`.

- [ ] **Step 4: Verify smoke tests**

Run: `python -m unittest tests.maximum_optimizer.test_remapped_topology_smoke tests.maximum_optimizer.test_remapped_topology -v`

Expected: all pass.

- [ ] **Step 5: Run real wheel/Charger/Monaco observations**

Use current local LVS source/output pairs. Record every structural rejection honestly. For accepted cases, invoke the existing local StudioMDL compile flow, report triangle/byte reduction and compilation only, and do not report visual quality.

- [ ] **Step 6: Run regression verification**

Run: `python -m unittest discover -s tests/maximum_optimizer -p "test_*.py" -v`

Expected: all non-environment tests pass; environment-only skips remain explicit.

- [ ] **Step 7: Commit checkpoint**

Commit smoke code/tests and any reproducible observation record with message `test(maximum): smoke remapped topology on LVS`.
