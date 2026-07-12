# Maximum State Fidelity and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Authorize Maximum candidates with state-exact region/animation evidence and recover marker-owned interrupted output transactions before any new run mutation.

**Architecture:** Add canonical region subsetting to `regions.py`, conservative Source SMD evidence helpers to the orchestrator, and a strict sibling transaction journal around atomic output promotion. Keep renderer completeness checks unchanged and invoke recovery from both the direct API and CLI bridge before logs/decompile.

**Tech Stack:** Python 3.11, unittest/pytest, Source QC/SMD parsing, SHA-256 canonical JSON, Windows-safe `os.replace` and directory `fsync`.

## Global Constraints

- Normal and Fidelity behavior must remain unchanged.
- Region assignment must use `require_complete=True` against a state-only canonical manifest.
- Staging is never promoted by recovery.
- Only a valid marker authorizes backup restore or cleanup.
- Marker creation is exclusive and fsynced before the first rename; marker updates/removal fsync the parent directory.
- Reparse points and paths outside the exact destination parent fail closed.
- Every behavior change follows a witnessed RED-GREEN cycle.

---

### Task 1: Canonical per-state region manifests

**Files:**
- Modify: `maximum_optimizer/regions.py`
- Modify: `maximum_optimizer/orchestrator.py`
- Test: `tests/maximum_optimizer/test_regions.py`
- Test: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- Produces: `filter_region_manifest(manifest: RegionManifest, source_identities: Sequence[str]) -> RegionManifest`
- Consumes: `build_region_manifest`, `resolve_region_assignments`, original graph state source paths.

- [ ] **Step 1: Write failing real-assignment tests**

Create a manifest containing base and LOD observations. Assert filtered base and LOD manifests each resolve their exact observation subset with `require_complete=True`, and each rejects an omitted descriptor from its own subset.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/maximum_optimizer/test_regions.py -k state_manifest -q`
Expected: FAIL because `filter_region_manifest` does not exist.

- [ ] **Step 3: Implement canonical filtering**

Normalize and deduplicate requested sources, select exact entries, reconstruct observations/occurrences, call `build_region_manifest`, and reject empty/unknown requests.

- [ ] **Step 4: Write and run orchestrator RED**

Assert every base/LOD render command receives a distinct manifest path whose loaded payload contains exactly that state's source identities.

- [ ] **Step 5: Wire filtered files and run GREEN**

Write canonical state payloads beside `maximum_region_manifest.json`, pass the matching path through `--region-manifest`, and keep renderer completeness enabled.

Run: `python -m pytest tests/maximum_optimizer/test_regions.py tests/maximum_optimizer/test_orchestrator.py -q`
Expected: PASS.

### Task 2: Conservative deformation/animation evidence

**Files:**
- Modify: `maximum_optimizer/orchestrator.py`
- Test: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- Produces: `_smd_deformation_evidence(path: Path) -> tuple[bool, str]`
- Produces: `_animation_requirement(graph: QcGraph) -> tuple[bool, str]`
- Consumes: canonical QC graph source identities and `_smd_animation_frames`.

- [ ] **Step 1: Write failing rigid and weighted SMD tests**

Use real minimal SMD text: one-bone/time-0 `$sequence idle` must classify `rigid-or-bind-only`; a two-bone mesh with positive influences across bones must require representative animation.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/maximum_optimizer/test_orchestrator.py -k 'deformation or rigid_sequence' -q`
Expected: FAIL because classification still keys off `$sequence` alone.

- [ ] **Step 3: Implement strict Source vertex parsing**

Parse node IDs and triangle vertex link lists. Treat malformed link counts/weights as unavailable evidence. Require at least two declared bones and at least two positively weighted controlling bone IDs.

- [ ] **Step 4: Preserve graph/provenance animation identity**

Pair animation references by directive plus normalized family-relative source identity, independent of `_OPT` QC/QCI graph filenames, and require identical positive frame sets.

- [ ] **Step 5: Persist classification and run GREEN**

Write `logs/render-animation-classification.json` containing schema, required flag, reason, and selected frame/source identities. Rigid/bind-only renders bind; deformable unavailable fails closed.

Run: `python -m pytest tests/maximum_optimizer/test_orchestrator.py tests/maximum_optimizer/test_render_animation.py -q`
Expected: PASS.

### Task 3: Marker-owned crash recovery

**Files:**
- Modify: `maximum_optimizer/orchestrator.py`
- Test: `tests/maximum_optimizer/test_orchestrator.py`
- Test: `tests/maximum_optimizer/test_cli.py`

**Interfaces:**
- Produces: `_recover_output_transaction(destination: Path) -> None`
- Extends: `_promote_verified_tree(..., crash_hook: Callable[[str], None] | None = None)`
- Consumes: strict tree manifests, reparse detection, lexical containment helpers.

- [ ] **Step 1: Write failing recovery tests**

Cover marker-owned backup restore after destination-to-backup crash, ambiguous backups fail closed, destination-present cleanup without overwrite, marker path/schema mismatch rejection, and markerless legacy orphan fail-closed without mutation.

- [ ] **Step 2: Run RED**

Run: `python -m pytest tests/maximum_optimizer/test_orchestrator.py -k 'transaction_recovery or crash_barrier' -q`
Expected: FAIL because no durable marker/recovery exists.

- [ ] **Step 3: Implement durable marker primitives**

Use exclusive `open(..., 'x')`, canonical JSON bytes, file flush+`os.fsync`, `os.replace` for updates, and directory-handle fsync where supported. Validate exact schema/types, nonce-bound names, direct-sibling containment, and absence of reparses.

- [ ] **Step 4: Add promotion phases and crash hooks**

Emit hooks after marker creation, backup rename/update, staging rename/update, final verification, and cleanup. Do not catch injected crash exceptions as cooperative cancellation.

- [ ] **Step 5: Implement pre-mutation recovery**

After lexical validation but before `create=True`, logs, dependency proof, or decompile: restore exactly one marker-owned safe backup only when destination is absent; never overwrite an existing destination; remove only valid marker-owned stale artifacts; reject legacy/ambiguous state.

- [ ] **Step 6: Run GREEN**

Run: `python -m pytest tests/maximum_optimizer/test_orchestrator.py tests/maximum_optimizer/test_cli.py -q`
Expected: PASS.

### Task 4: Pre-set cancellation and final verification

**Files:**
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `.superpowers/sdd/task-10-report.md`
- Test: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- Extends: `run_maximum_addon` terminal event semantics.

- [ ] **Step 1: Write pre-set cancellation RED test**

Invoke the direct API with an already-set event and assert exact `run_started`, `run_cancelled`, durable report, no inventory/build/output.

- [ ] **Step 2: Implement minimal early terminal path and run GREEN**

Perform profile/path validation and recovery, create only the work log journal, emit the two events, and return cancelled.

- [ ] **Step 3: Update report with current counts and runtime limitation**

Record the final focused/full counts and explicitly retain real Blender/Source Tools integration as residual manual validation.

- [ ] **Step 4: Verify and commit**

Run:

```powershell
python -m pytest tests/maximum_optimizer -q
python -m pytest -q
python -m py_compile build_optimized_addon.py maximum_optimizer/orchestrator.py maximum_optimizer/regions.py render_previews.py
git diff --check
```

Expected: all tests pass, compilation exits 0, diff check exits 0, worktree contains only intended changes.
