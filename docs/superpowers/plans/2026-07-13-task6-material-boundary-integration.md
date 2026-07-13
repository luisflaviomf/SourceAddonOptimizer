# Task 6 Material Boundary Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind the approved current-material contract to every source-union render artifact while Blender receives only exact private material roots acquired inside the E1 workspace.

**Architecture:** `AdaptiveDirectProductionBoundary` accepts one untrusted `SourceUnionMaterialContract`, checks it against the filtered SMD and every coverage witness, and repeatedly obtains current authorizations for the original and private roots. The existing E1 workspace lease remains the sole workspace owner; material staging publishes only `workspace/material-roots`, and control/raw/comparison evidence all carry the same `material_contract_sha256`.

**Tech Stack:** Python 3.11, unittest/pytest, immutable SHA-256 contracts, no-follow filesystem helpers, Blender CLI boundary fixtures.

## Global Constraints

- Do not implement a real source-union renderer in `render_previews.py` in this checkpoint.
- Use one `material_contract_sha256`; it must equal every coverage witness and every control/raw/comparison binding.
- Never pass original material roots, shared texture cache paths, or material staging paths to Blender.
- Preserve the original `SourceUnionWorkspaceLease` identity and cleanup semantics.
- Follow RED/GREEN TDD; exclude `.superpowers/sdd/progress.md` and local probe files from commits.

---

### Task 1: Real material fixture and pre-workspace binding

**Files:**
- Modify: `tests/maximum_optimizer/test_task6_contracts.py`
- Modify: `tests/maximum_optimizer/test_task6_direct_compositor.py`
- Modify: `tests/maximum_optimizer/test_task6_production_adapters.py`
- Modify: `maximum_optimizer/production_adapters.py`

**Interfaces:**
- Consumes: `SourceUnionMaterialContract` from `maximum_optimizer.source_materials`.
- Produces: `render_adaptive_direct_source_union(..., material_contract: SourceUnionMaterialContract)`.

- [x] Add optional `material_contract_sha256` and `material_region_keys` arguments to the test coverage helpers and fixture so all sealed witnesses carry the runtime material hash.
- [x] Build real `vehicles/paint.vmt` and `textures/paint.vtf` roots in `_render_case`, then build a contract from the filtered component bytes.
- [x] Add parameterized failing tests for wrong source identity, filtered hash, ordered region keys, any witness hash, and root count/order; assert the runner is not called and the workspace does not exist.
- [x] Run `python -m pytest tests/maximum_optimizer/test_task6_production_adapters.py -q` and confirm the new positive case fails because the boundary has no `material_contract` argument.
- [x] Add the typed argument and perform all structural/current checks before E1 workspace acquisition.
- [x] Rerun the focused tests green.

### Task 2: Private roots, exact control, and Blender command

**Files:**
- Modify: `tests/maximum_optimizer/test_task6_production_adapters.py`
- Modify: `maximum_optimizer/production_adapters.py`

**Interfaces:**
- Consumes: `materialize_private_source_union_material_roots(...)` and `require_current_source_union_material_contract(...)`.
- Produces: `workspace/material-roots/root-NNN`, exact material control payload, and CLI `--materials-root` entries containing only published private roots.

- [x] Add failing assertions that the command contains only private roots and no original/shared/staging paths.
- [x] Add failing assertions that `source-union-contract.json` contains the exact runtime contract payload, current render evidence, and the same single material hash as `comparison_contract`.
- [x] Extend `_assert_source_union_workspace_root` to require `material-roots`; materialize after E1 acquisition and validate the exact selected-file tree with the approved 512-file/512-MiB contract bounds.
- [x] Authorize original roots immediately before materialization, authorize original and private roots after publication, and pass only private roots to the command.
- [x] Make `SourceUnionRunner` write textured `resolved_materials` equal to authorized current evidence and clay `resolved_materials=[]`.
- [x] Rerun the focused tests green.

### Task 3: Reauthorization and hostile mutation coverage

**Files:**
- Modify: `tests/maximum_optimizer/test_task6_production_adapters.py`
- Modify: `maximum_optimizer/production_adapters.py`

**Interfaces:**
- Consumes: issued `CurrentSourceUnionMaterialAuthorization` values.
- Produces: fail-closed pre/post Blender, comparator, and final-return checks for both original and private bytes.

- [x] Add RED subtests for same-size original/private VMT and VTF mutation before, during, after Blender, during comparison, and at final validation.
- [x] Add RED tests for higher-priority VMT/VTF shadows, private extra files, nested junctions, root replacement, cancellation, and workspace replacement ownership.
- [x] Reauthorize original and private roots immediately before Blender, after Blender, before/after comparison, and in final validation; require emitted evidence and hashes to remain exact.
- [x] Revalidate exact private tree and control/raw manifests at each boundary.
- [x] Verify failures clean only owned trees and preserve every external winner/target; when a foreign descendant replaces a material root, retain its workspace container rather than deleting or moving that descendant.
- [x] Rerun the focused tests green.

### Task 4: Full verification and review

**Files:**
- Verify: `maximum_optimizer/production_adapters.py`
- Verify: `tests/maximum_optimizer/test_task6_production_adapters.py`
- Verify: fixture helper files changed above.

**Interfaces:**
- Consumes: completed Task 1-3 implementation.
- Produces: isolated commit and exact core/visual review verdicts.

- [x] Run `python -m py_compile maximum_optimizer/production_adapters.py`.
- [x] Run `python -m pytest tests/maximum_optimizer/test_task6_production_adapters.py tests/maximum_optimizer/test_task6_source_materials.py tests/maximum_optimizer/test_task6_source_union_comparator.py -q`.
- [x] Run `python -m pytest -q` and require exit 0.
- [x] Run `git diff --check`, stage only checkpoint files, and commit without progress/probe artifacts.
- [ ] Request exact-hash core and visual reviews; address every concrete blocker through a new RED/GREEN follow-up.
