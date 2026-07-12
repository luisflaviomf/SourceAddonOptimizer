# LVS Task 6 Review Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make direct SMD provenance orientation-safe and make Task 6 unit evidence hermetic and honest about external local artifacts.

**Architecture:** The SMD mapper will index only the three cyclic permutations of every source triangle, so every returned corner tuple preserves source orientation. The checked-in evidence JSON becomes a small self-contained experiment summary validated by the schema; rehashing large local inputs and binaries moves to an explicit environment-gated integration builder that is absent from default unit discovery.

**Tech Stack:** Python 3, `unittest`, SMD text parsing, SHA-256 sealed JSON.

## Global Constraints

- Follow RED/GREEN TDD for each behavioral change.
- Default tests must pass when `.superpowers` is absent.
- Compiled and direct-SMD hashes derived from non-committed artifacts must be labeled `local_external_evidence`.
- Keep only measured wheel claims and the honest local Charger fail-closed observation; make no portable audit claim unsupported by checked-in contents.

---

### Task 1: Orientation-safe SMD provenance

**Files:**
- Modify: `maximum_optimizer/smd_contract.py`
- Test: `tests/maximum_optimizer/test_smoothing.py`

**Interfaces:**
- Consumes: `map_imported_corners_to_smd(...)` imported triangle tuples.
- Produces: source-corner ordinals using only cyclic permutations `(0,1,2)`, `(1,2,0)`, `(2,0,1)`.

- [ ] Add tests proving all cyclic rotations map, an odd permutation rejects, and serialized normals/winding retain source orientation.
- [ ] Run the focused tests and observe the odd-permutation test fail because all six permutations are currently accepted.
- [ ] Restrict lookup/diagnostics to cyclic permutations and rerun focused tests green.
- [ ] Add RED tests for SMD `link_count=0` primary-bone fallback and case-distinct bone names.
- [ ] Implement exact-case influence identity and zero-link fallback; rerun focused tests green.

### Task 2: Hermetic checked-in evidence

**Files:**
- Create: `benchmarks/lvs_models/fixtures/meshopt_direct_position_v1_unit.json`
- Modify: `tests/maximum_optimizer/test_position_evidence.py`
- Modify: `maximum_optimizer/position_evidence.py`

**Interfaces:**
- Consumes: a small sealed JSON fixture committed to Git.
- Produces: schema validation and hostile-mutation tests with no filesystem dependency outside tracked files.

- [ ] Change the unit test to load only the fixture and add a subprocess test that runs with `.superpowers` hidden/unavailable.
- [ ] Run it RED against the old schema/builder coupling.
- [ ] Define schema v3 fields separating `checked_in_metrics` from `local_external_evidence`; validate metric relationships and unavailable artifacts.
- [ ] Generate/seal the fixture and run focused tests green.

### Task 3: Explicit local integration evidence

**Files:**
- Modify: `benchmarks/lvs_models/build_meshopt_direct_position_v1.py`
- Modify: `benchmarks/lvs_models/meshopt_direct_position_v1.json`
- Create: `benchmarks/lvs_models/run_meshopt_direct_position_v1_integration.py`

**Interfaces:**
- Consumes: `LVS_TASK6_EVIDENCE_ROOT`, which must name the local experiment root.
- Produces: an explicit integration-only rehash command and a sealed local experiment JSON labeled `local_external_evidence`.

- [ ] Add a non-discovery integration script that errors when the required environment variable/root is absent.
- [ ] Update the builder to require the explicit root and emit checked-in metrics/audit JSON plus external artifact hashes under the honest label.
- [ ] Regenerate the committed evidence and verify its digest/schema.

### Task 4: Documentation and clean-checkout verification

**Files:**
- Modify: `.superpowers/sdd/lvs-task-6-report.md`
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: final test and integration results.
- Produces: an honest local experiment report without portable artifact/audit overclaims.

- [ ] Remove claims that the portable package proves source/material/bone/animation/collision audits.
- [ ] Label SMD/compiled hashes as local external evidence and retain only the wheel byte/count experiment result.
- [ ] Temporarily rename `.superpowers`, run focused tests, and restore it in `finally`-equivalent PowerShell handling.
- [ ] Run full discovery, `git diff --check`, verify clean status after commit, and report exact results.
