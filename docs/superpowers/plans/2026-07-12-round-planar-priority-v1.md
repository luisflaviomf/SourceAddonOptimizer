# Round Planar Priority V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an opt-in, fail-closed round-component Blender prototype and prepare its locked-corpus experiment.

**Architecture:** A pure-Python classifier admits only rigid, manifold, nearly axial meshes and returns deterministic ring priorities. `batch_optimize_maximum.py` consumes that classification only for a new R&D strategy, applies a protected planar dissolve followed by the existing Blender collapse, and falls back through the existing exact source path. A benchmark command builder prepares wheel and steering-wheel arms without launching Blender.

**Tech Stack:** Python unittest, Blender Python API, existing SMD/QC Maximum pipeline.

## Global Constraints

- The strategy is opt-in and excluded from production candidate selection and profiles.
- Every rejection or processing error preserves the original SMD bytes.
- QC, materials, UV seams, split normals, weights, animations, and physics remain unchanged.
- Do not execute Blender while the shared Blender lock is held.

---

### Task 1: Pure round-component classifier

**Files:**
- Create: `maximum_optimizer/round_planar_priority.py`
- Create: `tests/maximum_optimizer/test_round_planar_priority.py`

**Interfaces:**
- Produces: `classify_round_component(...) -> RoundComponentDecision` with `eligible`, `reason`, `axis`, and `priority_vertices`.

- [ ] Write failing tests for a rigid wheel-like prism and rejection of skinned, open, non-round, and disconnected meshes.
- [ ] Run `python -m unittest tests.maximum_optimizer.test_round_planar_priority -v` and verify failure because the module is missing.
- [ ] Implement deterministic topology, rigidity, PCA/extents, radial-band, angular-coverage, and planar-interior checks.
- [ ] Run focused tests and verify all pass.
- [ ] Commit classifier and tests.

### Task 2: Blender modifier contract

**Files:**
- Modify: `batch_optimize_maximum.py`
- Modify: `tests/maximum_optimizer/test_maximum_blender_args.py`

**Interfaces:**
- Consumes: `RoundComponentDecision`.
- Produces: opt-in candidate strategy `round-planar-priority-v1` and metrics describing admission, planar prepass, and priority ring count.

- [ ] Write failing fake-Blender tests that require DISSOLVE before COLLAPSE, boundaries off, and exact delimiter set.
- [ ] Run the focused tests and verify the new strategy is rejected/missing.
- [ ] Add the minimal strategy branch, ring vertex group, planar modifier, and exact fallback behavior.
- [ ] Run focused and existing Maximum Blender tests.
- [ ] Commit integration and tests.

### Task 3: Locked experiment preparation

**Files:**
- Create: `benchmarks/lvs_models/run_round_planar_priority_v1.py`
- Create: `tests/maximum_optimizer/test_round_planar_priority_experiment.py`

**Interfaces:**
- Produces deterministic commands for `b050`, `planar-only`, and `round-planar-priority-v1` for one wheel and one steering wheel.

- [ ] Write failing tests for exact arms, source paths, output isolation, and no subprocess execution during command construction.
- [ ] Run the focused test and verify the runner is missing.
- [ ] Implement command construction only; require an explicit execute flag and Blender lock acquisition before subprocess work.
- [ ] Run focused tests without acquiring Blender or starting the corpus.
- [ ] Commit experiment preparation.

### Task 4: Verification and review handoff

**Files:**
- Test only; no new production files.

- [ ] Run all classifier, Blender contract, experiment, SMD contract, and QC graph tests.
- [ ] Confirm production candidate lists and profiles do not contain `round-planar-priority-v1`.
- [ ] Inspect the diff and request independent review before any winner claim.
