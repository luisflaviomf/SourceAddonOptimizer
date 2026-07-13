# Visual-Remapped Source Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated, non-authorizing runner and sealed evidence boundary for production-grade generation of visual-remapped R&D candidates.

**Architecture:** Add a new module whose request composes the existing sealed source request but has distinct visual identity, candidate/cache derivation, execution, evidence, and result types. Reuse hardened no-follow filesystem/process primitives without changing `direct_source_runner.py`; validate output through the visual-remapped structural contract and keep all scheduler/adapter/UI activation absent.

**Tech Stack:** Python 3.11 dataclasses, unittest, Blender batch CLI, meshoptimizer bridge, canonical JSON SHA-256 evidence.

## Global Constraints

- Reference and preserve the R&D structural contract from commit `6238897`.
- Accept only `meshopt-remapped-visual-v1` / `visual-remapped-topology-v1`.
- Fix quality to `unverified` and authority to `false`.
- Do not modify or relax `BlenderDirectSourceRunner`.
- Do not register scheduler, production adapter, worker, or WPF integration.
- Do not stage `.superpowers/sdd/progress.md`.

---

### Task 1: Discriminated request and evidence types

**Files:**
- Create: `maximum_optimizer/visual_remapped_source_runner.py`
- Create: `tests/maximum_optimizer/test_visual_remapped_source_runner.py`

**Interfaces:**
- Consumes: `DirectSourceBuildRequest`, `VisualRemappedTopologyProof`, and `SourceFileProof`.
- Produces: `VisualRemappedSourceRequest`, `VisualRemappedToolProof`, `VisualRemappedSourceEvidence`, exact payload loaders, `visual_remapped_candidate_id`, and `visual_remapped_cache_digest`.

- [ ] **Step 1: Write failing identity tests**

Specify a sealed request wrapping a direct source request plus exact post-prefilter byte proof. Assert strict literals, ratio equality, candidate/cache derivation, exact fields, round-trip payload loading, and rejection of unknown fields, mismatched strategy/transfer, stale cache digest, changed source/output hashes, authorizing state, and tampered seal.

- [ ] **Step 2: Run RED**

Run `python -m unittest tests.maximum_optimizer.test_visual_remapped_source_runner -v` and confirm the new module import fails.

- [ ] **Step 3: Implement minimal immutable types**

Use frozen dataclasses and canonical JSON hashing. Evidence must relationship-check the embedded request, topology proof, source/output sizes and hashes, canonical artifact/tool rows, engine version, candidate/cache derivation, and fixed unverified/non-authorizing state.

- [ ] **Step 4: Run GREEN and commit**

Run the focused module, then commit only the new module/test and design/plan files.

### Task 2: Safe one-process runner

**Files:**
- Modify: `maximum_optimizer/visual_remapped_source_runner.py`
- Modify: `tests/maximum_optimizer/test_visual_remapped_source_runner.py`

**Interfaces:**
- Produces: `VisualRemappedRunnerTools`, `VisualRemappedRunResult`, and `BlenderVisualRemappedSourceRunner.__call__(input_path, output_path, request, cancel_event)`.

- [ ] **Step 1: Write failing happy-path execution test**

Use a fake process that parses the emitted candidate JSON and writes a boundary-changing SMD, typed metrics, provenance, manifest, and log. Assert the exact batch identity, structural proof/deltas, evidence round trip, fresh run artifacts, tool proofs, and atomic published output.

- [ ] **Step 2: Run RED**

Confirm failure because the runner API is absent.

- [ ] **Step 3: Implement minimal safe execution**

Reuse the hardened direct-runner filesystem primitives. Pin roots and tool bytes, acquire a fresh owned root, write the QC/candidate exclusively, enforce timeout/cancellation, read output bytes once, invoke `validate_visual_remapped_topology_smd`, validate metrics/provenance, inventory artifacts twice, build sealed evidence, and publish with no replacement.

- [ ] **Step 4: Run GREEN**

Run the focused module and existing direct runner tests to prove isolation.

### Task 3: Adversarial boundaries

**Files:**
- Modify: `tests/maximum_optimizer/test_visual_remapped_source_runner.py`
- Modify: `maximum_optimizer/visual_remapped_source_runner.py` only in response to a witnessed RED test.

**Interfaces:**
- Validates the Task 2 public API under hostile filesystem/process behavior.

- [ ] **Step 1: Add and witness RED cases individually**

Cover pre-cancel, nonzero exit, timeout, tool mutation, source mutation/mismatch, output collision, strategy/transfer metrics mismatch, provenance/hash mismatch, malformed JSON, structural rejection, artifact mutation between inventories, symlink/reparse artifact, artifact budgets, replaced run root, and foreign/stale evidence/cache payloads.

- [ ] **Step 2: Implement each minimal fix and rerun GREEN**

Every failure must preserve external bytes, publish nothing, and retain only an identity-owned quarantine. Root replacement must never quarantine or delete the foreign replacement by pathname.

- [ ] **Step 3: Run isolation regression and commit**

Run visual runner, visual topology, direct runner, remapped topology, batch discriminator, process, and cache tests. Commit without the shared progress file.

### Task 4: Review and verification

**Files:**
- Modify only files required by concrete Critical/Important review findings.

- [ ] **Step 1: Request independent review**

Review request/evidence discrimination, canonical loaders, tool/root pinning, TOCTOU barriers, artifact budgets, quarantine ownership, and absence of scheduler/WPF/worker authority.

- [ ] **Step 2: Fix findings through RED/GREEN**

Add a reproducing test before each implementation fix and request follow-up review.

- [ ] **Step 3: Run targeted and full verification**

Run focused regression modules, `git diff --check`, and `python -m unittest discover`. Record exact counts/skips and confirm `direct_source_runner.py`, scheduler, adapters, WPF, and worker are untouched.

- [ ] **Step 4: Propose but do not activate the regional adapter**

Document the minimal future callable that maps a sealed regional source request and ratio to this runner evidence, with compile and calibrated whole/focused gates required downstream.
