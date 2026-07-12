# Family-Level Typed Fidelity Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select either `round-rigid-v1` or `general-body-detail-v1` fidelity limits once per model family using an audited classifier over the original visual SMD sources.

**Architecture:** Add a pure-Python, fail-closed family classifier that parses the original QC graph, classifies every unique visual SMD with the existing `classify_round_component`, and selects `round-rigid-v1` only when every source is eligible. Add a typed profile bundle loader while preserving schema-1 profiles as an exact legacy-global mode. The orchestrator selects one immutable `FidelityProfile` before the control render and records the selection; `AdapterSet.visual` and `compare_render_sets` remain unchanged.

**Tech Stack:** Python 3.11, `unittest`, existing QC/SMD parsers, existing round classifier, atomic JSON reporting.

## Global Constraints

- Do not modify calibration evidence, its builder, its parser, or committed calibration JSON until the final-evidence commit is present.
- Do not invoke Blender; all classification and tests are pure Python.
- Never classify from filename, material name, candidate strategy, or manually curated family ID.
- Any unsupported format, malformed source, non-rigid vertex, or significant non-round component selects `general-body-detail-v1`.
- A legacy schema-1 profile must preserve current behavior and must not trigger source classification.
- Profile selection happens once per family and the same profile is used for control and every candidate.

---

### Task 1: Original-source family classifier

**Files:**
- Create: `maximum_optimizer/fidelity_selection.py`
- Create: `tests/maximum_optimizer/test_fidelity_selection.py`

**Interfaces:**
- Produces: `ROUND_RIGID = "round-rigid-v1"` and `GENERAL_BODY_DETAIL = "general-body-detail-v1"`.
- Produces: `SourceFidelityAudit(source: str, eligible: bool, reason: str, axis: int | None)`.
- Produces: `FamilyFidelitySelection(profile_class: str, reason: str, sources: tuple[SourceFidelityAudit, ...])`.
- Produces: `classify_original_family(graph: QcGraph) -> FamilyFidelitySelection`.

- [ ] **Step 1: Write failing tests for real round and non-round SMD data**

  In `tests/maximum_optimizer/test_fidelity_selection.py`, construct SMD text from indexed triangles. Emit each corner as `bone x y z nx ny nz u v`; for the rigid case use a triangulated 16-segment thin cylinder with one bone. Assert that two unique eligible visual references select `round-rigid-v1`, duplicate references are audited once in normalized path order, and every audit reports `eligible=True`.

  Add a box SMD as a second visual source and assert the whole family selects `general-body-detail-v1`. Add a cylinder whose first corner has two links (`2 0 0.5 1 0.5`) and assert non-rigid geometry selects general. Add a `.dmx` visual source and malformed SMD and assert both select general without raising.

- [ ] **Step 2: Run the classifier tests and verify RED**

  Run: `python -m unittest tests.maximum_optimizer.test_fidelity_selection -v`

  Expected: import failure because `maximum_optimizer.fidelity_selection` does not exist.

- [ ] **Step 3: Implement SMD-to-round input conversion**

  Implement `_round_input(path: Path)` with `parse_smd_triangles(path.read_text(encoding="utf-8-sig"))`. Flatten corners in triangle order; triangle indices are `(0,1,2)`, `(3,4,5)`, and so on. For nine-token corners, emit `((int(tokens[0]), 1.0),)`. When link data is present, emit the declared `(bone, weight)` pairs; a zero-link row falls back to the parent bone. Reject invalid bone IDs, non-finite weights, truncated pairs, and empty triangles with `ValueError`.

- [ ] **Step 4: Implement fail-closed family classification**

  Deduplicate `reference.source_path` for `reference.role == "visual"`, sort by normalized path relative to `graph.family_root`, and reject an empty visual set. For each `.smd`, call `classify_round_component(positions, triangles, influences)`. Record its `eligible`, `reason`, and `axis`. For unsupported suffixes and caught `OSError`, `UnicodeError`, or `ValueError`, record `eligible=False` with a stable typed reason. Return round only if every audit is eligible; otherwise return general with the first failing source/reason. Do not catch `KeyboardInterrupt`, `SystemExit`, or cancellation exceptions.

- [ ] **Step 5: Run focused tests and commit**

  Run: `python -m unittest tests.maximum_optimizer.test_fidelity_selection tests.maximum_optimizer.test_round_planar_priority -v`

  Expected: all tests pass without Blender.

  Commit: `git commit -m "feat: classify family fidelity from original geometry"`

---

### Task 2: Typed profile bundle with legacy compatibility

**Files:**
- Modify: `maximum_optimizer/fidelity_selection.py`
- Modify: `tests/maximum_optimizer/test_fidelity_selection.py`
- Test fixture only: temporary JSON created by the tests

**Interfaces:**
- Produces: `FidelityProfileSet(mode: str, version: str, corpus_hash: str, profiles: Mapping[str, FidelityProfile])`.
- Produces: `load_fidelity_profile_set(path: Path) -> FidelityProfileSet`.
- Produces: `FidelityProfileSet.profile_for(profile_class: str) -> FidelityProfile`.

- [ ] **Step 1: Write failing schema and compatibility tests**

  Verify an existing calibrated schema-1 payload loads with `mode == "legacy-global-v1"`, maps both typed classes to the same `FidelityProfile`, and requires no classifier. Verify this typed payload loads with distinct limits:

  ```json
  {
    "schema": 2,
    "version": "lvs-family-typed-v1",
    "calibrated": true,
    "corpus_hash": "<64 lowercase hex>",
    "selector": "audited-original-round-family-v1",
    "profiles": {
      "general-body-detail-v1": {"limits": {"<all eight metrics>": 0.0}},
      "round-rigid-v1": {"limits": {"<all eight metrics>": 0.0}}
    }
  }
  ```

  Add rejection tests for an extra/missing profile class, unknown selector, uncalibrated bundle, non-finite limit, absent metric, extra top-level field, and invalid corpus hash.

- [ ] **Step 2: Run tests and verify RED**

  Run: `python -m unittest tests.maximum_optimizer.test_fidelity_selection -v`

  Expected: failure because `load_fidelity_profile_set` is absent.

- [ ] **Step 3: Implement exact typed loading**

  Read JSON once. If `schema == 1`, delegate to existing `load_profile(path)` and create a legacy set whose two keys reference that immutable profile. If `schema == 2`, require the exact top-level and profile keys above, construct one `FidelityProfile(schema=1, version=f"{version}:{profile_class}", calibrated=True, corpus_hash=corpus_hash, limits=...)` per class, and expose profiles through `MappingProxyType`. Reject every other schema.

- [ ] **Step 4: Run focused tests and commit**

  Run: `python -m unittest tests.maximum_optimizer.test_fidelity_selection tests.maximum_optimizer.test_visual_validation -v`

  Expected: all pass; all pre-existing schema-1 loader tests remain green.

  Commit: `git commit -m "feat: load typed family fidelity profiles"`

---

### Task 3: Orchestrator selection and durable audit

**Files:**
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- Consumes: `load_fidelity_profile_set`, `classify_original_family`, and `FamilyFidelitySelection`.
- Extends: `run_maximum_addon(..., profile_selector: Callable[[FamilyManifest], FamilyFidelitySelection] | None = None)` for deterministic tests.
- Produces: `work/logs/fidelity-profile-selection.json` with schema 1 and one record per inventoried family.

- [ ] **Step 1: Write failing legacy compatibility test**

  Use the existing schema-1 `_profile` fixture and a selector stub that raises if called. Run the optimizer and assert the stub is never invoked, all `FakeAdapters.visual` calls receive the legacy profile, and current selected candidates/statuses remain unchanged.

- [ ] **Step 2: Write failing typed selection tests**

  Change the test profile to schema 2 with visibly different `edge_error` limits. Inject a selector returning round for one family and general for another. Make `FakeAdapters.visual` record `profile.version`; assert control and every candidate in a family receive exactly the selected child version and that selection occurs before the first build for that family.

  Add a selector result with general fallback reason `unsupported-source-format:body.dmx`; assert the run continues using general. Add a selector exception test and require the run to fail closed before building that family, rather than silently selecting round.

- [ ] **Step 3: Run orchestrator tests and verify RED**

  Run: `python -m unittest tests.maximum_optimizer.test_orchestrator -v`

  Expected: failures because the orchestrator still loads one profile and has no selector injection/audit.

- [ ] **Step 4: Integrate profile-set loading before mutations**

  Replace the first `load_profile(config.profile_path)` call with `load_fidelity_profile_set(config.profile_path)`. Preserve the current first-operation fail-closed guarantee. Keep `versions["profile_sha256"]` as the SHA-256 of the entire profile file and add `versions["fidelity_selector"] = "audited-original-round-family-v1"` for typed mode or `"legacy-global-v1"` for legacy mode.

- [ ] **Step 5: Select once per family**

  Before building `roundtrip-control`, choose the legacy profile directly when `mode == "legacy-global-v1"`. In typed mode, locate the unique original QC with existing `_matching_qcs`, parse it with `parse_qc_graph`, call the injected selector or `classify_original_family`, and call `profile_set.profile_for(selection.profile_class)`. Reuse that local `profile` for control and all candidates. Never classify candidate output.

- [ ] **Step 6: Write the atomic selection audit**

  Accumulate records containing `family_id`, `model_rel`, `profile_class`, `reason`, profile `version`, profile `corpus_hash`, and source audit objects. Write `{"schema":1,"selector":...,"families":[...]}` through `atomic_write_json` whenever the partial report is journaled and at terminal completion. Sort by inventory order and keep relative source identities only; do not store absolute paths.

- [ ] **Step 7: Run focused tests and commit**

  Run: `python -m unittest tests.maximum_optimizer.test_fidelity_selection tests.maximum_optimizer.test_orchestrator tests.maximum_optimizer.test_visual_validation -v`

  Expected: all pass.

  Commit: `git commit -m "feat: select fidelity profile per model family"`

---

### Task 4: Backward-compatibility and full verification gate

**Files:**
- Modify only if a failing regression requires it: `tests/maximum_optimizer/test_cli.py`
- Do not modify calibration evidence/schema files in this task.

- [ ] **Step 1: Verify production schema-1 behavior**

  Run the existing configuration/CLI tests and assert `profile_path` remains the only configuration field, schema-1 calibrated fixtures remain accepted, uncalibrated `maximum-experimental-v1.json` still fails before writes, and cache invalidation still keys on the whole profile file hash.

  Run: `python -m unittest tests.maximum_optimizer.test_cli tests.maximum_optimizer.test_cache tests.maximum_optimizer.test_orchestrator -v`

- [ ] **Step 2: Run the full suite**

  Run: `python -m unittest discover -s tests\\maximum_optimizer`

  Expected: zero failures; platform privilege skips remain skips.

- [ ] **Step 3: Inspect scope and request review**

  Run: `git diff --check` and `git status --short`.

  Confirm no Blender process was started and no calibration evidence, evidence builder, evidence parser, committed calibration JSON, renderer, or batch optimizer file changed. Request independent review focused on fail-closed classification, schema-1 compatibility, and one-profile-per-family reuse.

- [ ] **Step 4: Commit any test-only compatibility adjustment**

  If Task 4 changed a test, commit only that test with `git commit -m "test: preserve legacy fidelity profile behavior"`. If no file changed, do not create an empty commit.

## Self-Review

- Spec coverage: family-level only; audited original geometry; general fallback; no Blender; typed limits; legacy behavior; durable audit; cache/profile provenance.
- No region-level routing is introduced. Mixed or uncertain families conservatively use general.
- No candidate strategy or filename heuristic participates in selection.
- The final-evidence commit is an explicit prerequisite before executing this plan.
- Function names and profile-class constants are consistent across all tasks.
