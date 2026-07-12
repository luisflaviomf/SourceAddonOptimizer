# Focused Region Gate and Composite Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an evidence-v3-gated isolated region validator, byte-exact donor recovery, and bounded Blender-adaptive/direct-position composite candidates.

**Architecture:** Preserve the existing whole-model visual gate, then select and render a deterministic top-K of isolated regions. Recovery composes proven SMD artifacts instead of regenerating unrelated sources, recompiles the full QC, reruns affected focuses, and performs one final whole visual authorization. Monaco composites use one global direct ratio per variant and actual compiled bytes after all gates.

**Tech Stack:** Python 3.11, `unittest`, Blender 5.0, Source StudioMDL, existing meshoptimizer bridge, canonical JSON/SHA-256 evidence, atomic filesystem cache.

## Global Constraints

- Do not begin any task until calibration evidence v3 is independently approved and the root agent explicitly authorizes implementation.
- Do not modify or regenerate evidence v3 as part of these tasks.
- Schema-1 and schema-2 profile behavior must remain unchanged.
- New behavior is enabled only by a trusted schema-3 profile.
- Focus bounds are fixed: top-K maximum 4, eight angles, two passes, two poses, sixteen whole states, and three recovery rounds.
- Monaco uses exactly four global direct ratios `(0.50, 0.45, 0.40, 0.35)` and at most eight fallback sources.
- Every candidate still requires complete QC compilation, structural validation, whole visual validation, and focused validation before promotion.
- No task changes the WPF or CLI surface.

## Locked interfaces

The implementation uses these names and field meanings across tasks. Immutable
mappings use the repository's existing `deep_freeze`/`MappingProxyType` patterns.

```python
@dataclass(frozen=True)
class FocusedRegionPolicy:
    schema: int
    selector: str
    top_k: int
    max_whole_states: int = 16
    max_recovery_rounds: int = 3

@dataclass(frozen=True)
class WholeStateEvidence:
    state_index: int
    state_name: str
    bodygroups: tuple[tuple[str, int], ...]
    lod_index: int
    poses: tuple[str, ...]
    source_pairs: tuple[tuple[str, str, str], ...]
    reference_manifest: str
    reference_manifest_sha256: str
    candidate_manifest: str
    candidate_manifest_sha256: str
    geometry_rows: tuple[Mapping[str, object], ...]

@dataclass(frozen=True)
class FocusTarget:
    rank: int
    region_key: str
    source_identity: str
    state_index: int
    state_name: str
    bodygroups: tuple[tuple[str, int], ...]
    lod_index: int
    anchor_pose: str
    surface_bidirectional_p95: float
    surface_max: float
    normalized_p95: float
    normalized_max: float
    selector_input_sha256: str

@dataclass(frozen=True)
class FocusRegionResult:
    target: FocusTarget
    validation: ValidationResult
    evidence_sha256: str
    cache_hit: bool

@dataclass(frozen=True)
class FocusedGateResult:
    validation: ValidationResult
    targets: tuple[FocusTarget, ...]
    regions: Mapping[str, FocusRegionResult]
    evidence_sha256: str

@dataclass(frozen=True)
class SourceOverlay:
    source_identity: str
    mode: Literal["donor", "exact-original", "direct-position"]
    donor_candidate_id: str | None
    effective_ratio: float | None
    input_sha256: str
    output_sha256: str
    focused_evidence_sha256: str | None

@dataclass(frozen=True)
class CompositeRecipe:
    schema: int
    kind: Literal["focused-recovery-v1", "adaptive-direct-fallback-v1"]
    base_candidate_id: str
    base_cache_digest: str
    round_index: int
    direct_ratio: float | None
    overlays: tuple[SourceOverlay, ...]
    selector_version: str
    prefilter_version: str | None

@dataclass(frozen=True)
class ComposedSourceTree:
    workspace: Path
    optimized_qc: Path
    source_hashes: Mapping[str, str]
    composition_evidence_sha256: str

@dataclass(frozen=True)
class FocusCacheKey:
    digest: str

    @classmethod
    def build(cls, payload: Mapping[str, object]) -> "FocusCacheKey":
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return cls(hashlib.sha256(raw.encode("utf-8")).hexdigest())
```

`CandidateSpec` gains one field, `composite_recipe: CompositeRecipe | None = None`.
`CandidateEvaluation` gains `whole_visual: ValidationResult | None = None` and
`focused_by_region: Mapping[str, FocusRegionResult]`. Existing callers that do not
enable schema 3 receive the current defaults and behavior.

---

### Task 1: Trusted schema-3 profiles and deterministic target selection

**Files:**
- Create: `maximum_optimizer/focused_regions.py`
- Create: `tests/maximum_optimizer/test_focused_regions.py`
- Modify: `maximum_optimizer/domain.py`
- Modify: `maximum_optimizer/fidelity_selection.py`
- Modify: `tests/maximum_optimizer/test_fidelity_selection.py`

**Interfaces:**
- `maximum_optimizer.domain` produces `FocusedRegionPolicy`, `WholeStateEvidence`, `FocusTarget`, `FocusRegionResult`, and `FocusedGateResult`.
- `maximum_optimizer.focused_regions` produces `select_focus_targets(states: Sequence[WholeStateEvidence], manifest: RegionManifest, profile: FidelityProfile, policy: FocusedRegionPolicy) -> tuple[FocusTarget, ...]`.
- `maximum_optimizer.fidelity_selection` produces `FidelityProfileSet.focused_profile_for(profile_class)`.
- Consumes the approved evidence-v3 seal from `maximum_optimizer.calibration_evidence` without modifying evidence files.

- [ ] **Step 1: Write schema-3 RED tests**

  Add fixtures with exact schema-3 fields from the design. Assert trusted evidence,
  exact child keys, complete finite limits, selector identity, and `1 <= top_k <= 4`.
  Assert schema 1/2 still map to their existing objects and reject
  `focused_profile_for`.

  ```python
  with self.assertRaisesRegex(ValueError, "trusted focused evidence"):
      load_fidelity_profile_set(untrusted_schema3)
  self.assertEqual(
      trusted.focused_profile_for(ROUND_RIGID).version,
      "lvs-focused-v1:round-rigid-v1:focused",
  )
  ```

- [ ] **Step 2: Run the profile tests and verify RED**

  Run: `python -m unittest tests.maximum_optimizer.test_fidelity_selection -v`

  Expected: failures because schema 3 and `focused_profile_for` do not exist.

- [ ] **Step 3: Implement the exact profile contract**

  Extend `FidelityProfileSet` with optional immutable `focused_policy` and
  `focused_profiles`. Keep the schema-1 and schema-2 branches unchanged. Accept
  schema 3 only when `focused_evidence_sha256` equals the reviewed v3 trust anchor.

  ```python
  def focused_profile_for(self, profile_class: str) -> FidelityProfile:
      if self.focused_policy is None or profile_class not in self.focused_profiles:
          raise ValueError("focused fidelity is not enabled")
      return self.focused_profiles[profile_class]
  ```

- [ ] **Step 4: Write selector RED tests**

  Build shuffled `WholeStateEvidence` rows and assert the same ordered three targets.
  Cover normalized p95/max ranking, stable anchor ties, zero limits, duplicate rows,
  non-finite metrics, unknown region keys, missing source pairs, empty eligible sets,
  and hard state/K bounds.

- [ ] **Step 5: Run selector tests and verify RED**

  Run: `python -m unittest tests.maximum_optimizer.test_focused_regions -v`

  Expected: import or behavior failures for the new selector.

- [ ] **Step 6: Implement pure selection and immutable result types**

  `select_focus_targets` must sort canonical rows rather than preserve input order.
  Return exactly `min(policy.top_k, unique_regions)` targets and include every hash
  used by the ranking in each target payload.

- [ ] **Step 7: Run focused tests and commit checkpoint 1**

  Run:
  `python -m unittest tests.maximum_optimizer.test_fidelity_selection tests.maximum_optimizer.test_focused_regions -v`

  Expected: zero failures.

  Commit:
  `git commit -m "feat: select trusted focused fidelity regions"`

---

### Task 2: Isolated region rendering

**Files:**
- Modify: `maximum_optimizer/regions.py`
- Modify: `render_previews.py`
- Modify: `tests/maximum_optimizer/test_regions.py`
- Modify: `tests/maximum_optimizer/test_visual_validation.py`

**Interfaces:**
- Produces `manifest_for_region(manifest, region_key)` and the renderer argument `--focus-region`.
- Consumes `FocusTarget.region_key`, source identity, pose list, and existing canonical region/configuration manifests.

- [ ] **Step 1: Write region extraction RED tests**

  Assert `manifest_for_region` returns exactly one entry, preserves its canonical
  descriptor and occurrences, and rejects unknown keys, malformed keys, or a
  manifest with duplicate assignments.

- [ ] **Step 2: Run region tests and verify RED**

  Run: `python -m unittest tests.maximum_optimizer.test_regions -v`

  Expected: missing `manifest_for_region`.

- [ ] **Step 3: Implement exact region extraction**

  Reuse `load_region_manifest_payload` invariants and never rebuild the descriptor
  from filenames or object-name tokens.

- [ ] **Step 4: Write renderer RED tests without starting Blender**

  Test argument parsing and a pure `_focused_region_objects` helper with fake objects.
  Assert only the object assigned to the selected key remains renderable and captured.
  Cover ambiguous assignments, non-selected objects sharing a source, zero-triangle
  targets, focus-only camera bounds, and exact expected matrix:

  ```python
  self.assertEqual(
      manifest["expected"],
      {"angles": list(ANGLES), "passes": ["textured", "clay"],
       "poses": ["bind"], "regions": [region_key]},
  )
  ```

- [ ] **Step 5: Run renderer tests and verify RED**

  Run:
  `python -m unittest tests.maximum_optimizer.test_visual_validation.RenderPreviewArgumentTests -v`

  Expected: missing focus argument/helper or incorrect object cardinality.

- [ ] **Step 6: Implement focused object isolation**

  Resolve all imported mesh objects against the canonical source manifest first.
  Hide non-target objects for viewport and render, exclude them from snapshots and
  camera fit, and emit exactly one region in both manifests. Keep the legacy path
  unchanged when `--focus-region` is absent.

- [ ] **Step 7: Run renderer compatibility tests and commit checkpoint 2**

  Run:
  `python -m unittest tests.maximum_optimizer.test_regions tests.maximum_optimizer.test_visual_validation -v`

  Expected: zero failures; platform privilege skips remain skips.

  Commit:
  `git commit -m "feat: render one model region in isolation"`

---

### Task 3: Focus evidence and render cache

**Files:**
- Create: `maximum_optimizer/focused_cache.py`
- Create: `tests/maximum_optimizer/test_focused_cache.py`
- Modify: `maximum_optimizer/focused_regions.py`
- Modify: `tests/maximum_optimizer/test_focused_regions.py`

**Interfaces:**
- Produces `FocusCacheKey.build(payload: Mapping[str, object])`, `FocusedRenderCache.lookup(key)`, `FocusedRenderCache.store(key, source, metadata)`, `FocusedRenderCache.invalidate(key)`, `material_resolution_proof(roots, requests, cancel_event)`, and `focused_gate_evidence_payload(policy, targets, results, recoveries)`.
- Cache hits return render directories only; callers must rerun `compare_render_sets`.

- [ ] **Step 1: Write cache-key RED tests**

  Mutate one input at a time: original SMD, candidate SMD, region descriptor, state,
  animation frame/hash, profile, evidence v3, renderer, tool dependency, material
  resolution, pass, angle, and size. Every mutation must change the digest.

- [ ] **Step 2: Write integrity and DoS RED tests**

  Require exact file manifests and image cardinality. Reject extra, missing, corrupt,
  duplicate, symlink, junction, non-contained, and stale-material entries. Assert
  material proofs above 4,096 files or 2 GiB return `cacheable=False` without
  authorizing or failing validation.

- [ ] **Step 3: Run cache tests and verify RED**

  Run: `python -m unittest tests.maximum_optimizer.test_focused_cache -v`

  Expected: missing module/interfaces.

- [ ] **Step 4: Implement atomic focus cache**

  Follow `CandidateCache` staging/quarantine patterns. Store canonical marker,
  payload manifest, target payload, material proof, and expected matrix. Never
  deserialize an absolute payload path.

- [ ] **Step 5: Implement sealed focused evidence payloads**

  Validate exact selected-target cardinality, one terminal record per target,
  contiguous recovery indices, and canonical evidence SHA-256. Include cache status
  only as diagnostics.

- [ ] **Step 6: Prove cache hits revalidate**

  In a test, restore a valid cached render and inject a comparator that fails. Assert
  the returned focused result fails; cached prior `passed=True` must be ignored.

- [ ] **Step 7: Run focused cache/evidence tests and commit checkpoint 3**

  Run:
  `python -m unittest tests.maximum_optimizer.test_focused_cache tests.maximum_optimizer.test_focused_regions -v`

  Expected: zero failures.

  Commit:
  `git commit -m "feat: seal and cache focused render evidence"`

---

### Task 4: Orchestrator focused gate

**Files:**
- Modify: `maximum_optimizer/domain.py`
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- Adds immutable whole/per-region focused result fields to `CandidateEvaluation` using the Task 1 domain types.
- `ProductionAdapters.visual` still returns the current whole `ValidationResult` and writes `logs/whole-visual-index.json`.
- Adds `ProductionAdapters.focused_visual(manifest, control, candidate, whole_profile, focused_profile, policy) -> FocusedGateResult`.

- [ ] **Step 1: Write ordering and compatibility RED tests**

  Assert schema 1/2 never call `focused_visual`. For schema 3, assert the call order is
  build, structural, whole visual, focused selection/render, cache store. Structural
  or whole failure must make zero focused calls.

- [ ] **Step 2: Write whole-index RED tests**

  Require ordered states, relative contained paths, exact source pairs, manifest
  hashes, geometry rows, animation classification, profile versions, and an outer
  seal. Mutations and path escapes fail before focused rendering.

- [ ] **Step 3: Run orchestrator tests and verify RED**

  Run:
  `python -m unittest tests.maximum_optimizer.test_orchestrator -v`

  Expected: focused interfaces/stages are absent.

- [ ] **Step 4: Implement whole-index writing and focused adapter**

  Refactor only the evidence returned by the current render loop; do not change its
  state matrix or whole comparison. Render targets in selector order and atomically
  update `focused-region-gate.json` after each target.

- [ ] **Step 5: Aggregate focused hard gates**

  Preserve `whole_visual` separately, set `CandidateEvaluation.visual` to the hard
  aggregate, and expose immutable `focused_by_region`. A missing focused result is a
  candidate rejection with an explicit gate failure.

- [ ] **Step 6: Add cancellation tests**

  Cover cancellation before selection, between focuses, during cache restore,
  during render, after comparison, and before evidence publication. Assert no
  promotion and exact terminal events.

- [ ] **Step 7: Run focused orchestrator gate and commit checkpoint 4**

  Run:
  `python -m unittest tests.maximum_optimizer.test_orchestrator tests.maximum_optimizer.test_focused_regions tests.maximum_optimizer.test_focused_cache -v`

  Expected: zero failures.

  Commit:
  `git commit -m "feat: require focused region validation"`

---

### Task 5: Byte-exact donor and original-source recovery

**Files:**
- Create: `maximum_optimizer/composite.py`
- Create: `tests/maximum_optimizer/test_composite.py`
- Modify: `maximum_optimizer/domain.py`
- Modify: `maximum_optimizer/search.py`
- Modify: `maximum_optimizer/candidates.py`
- Modify: `tests/maximum_optimizer/test_search.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- `maximum_optimizer.domain` produces immutable `SourceOverlay`, `CompositeRecipe`, and `ComposedSourceTree`.
- `maximum_optimizer.composite` produces `select_recovery_overlays(failed, evaluations, manifests, round_index) -> tuple[SourceOverlay, ...]`, `compose_candidate_sources(base_build, recipe, workspace, cancel_event) -> ComposedSourceTree`, and `validate_composition_proof(payload, base_root, composed_root) -> Mapping[str, str]`.
- Candidate cache payloads include the entire canonical recipe and donor evidence hashes.

- [ ] **Step 1: Write donor-selection RED tests**

  Use a globally failed candidate whose target region passed as a donor. Assert the
  least less-aggressive same-contract donor wins. Reject cross-family, cross-profile,
  cross-strategy, equal/lower ratio, self, duplicated, missing-proof, and more than
  eight inspected donors.

- [ ] **Step 2: Write exact-fallback RED tests**

  Exhaust donors and assert one explicit original-source overlay. When two focuses
  share the source, both become affected. Reject more than four changed sources and
  a fourth recovery round.

- [ ] **Step 3: Run search/composite tests and verify RED**

  Run:
  `python -m unittest tests.maximum_optimizer.test_search tests.maximum_optimizer.test_composite -v`

  Expected: missing typed recovery APIs.

- [ ] **Step 4: Implement canonical recipes and candidate IDs**

  Hash family, base candidate, optimizer contract, sorted overlays, donor/exact
  hashes, focused evidence hashes, and round index. Composite and recovery candidates
  consume `SearchBudget.max_candidates` and cannot repeat an attempted ID.

- [ ] **Step 5: Implement source composition proof**

  Copy into a fresh workspace. Compare canonical source manifests before and after;
  every changed hash must have one overlay and every undeclared hash must remain
  equal. Parse and pair the full optimized QC graph before compilation.

- [ ] **Step 6: Integrate compile, structural, and selective rerender**

  Recompile complete QC. Rerender every focus whose source changed, reuse sealed
  evidence for unchanged sources, then perform one final whole visual validation
  after all focuses pass.

- [ ] **Step 7: Test failure isolation and commit checkpoint 5**

  Run:
  `python -m unittest tests.maximum_optimizer.test_composite tests.maximum_optimizer.test_search tests.maximum_optimizer.test_orchestrator -v`

  Expected: zero failures; compile or proof errors reject only that candidate.

  Commit:
  `git commit -m "feat: recover focused failures with source donors"`

---

### Task 6: Bounded Monaco adaptive-direct composites

**Files:**
- Modify: `maximum_optimizer/composite.py`
- Modify: `maximum_optimizer/candidates.py`
- Modify: `maximum_optimizer/search.py`
- Modify: `batch_optimize_maximum.py`
- Modify: `tests/maximum_optimizer/test_composite.py`
- Modify: `tests/maximum_optimizer/test_search.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- Produces `select_exact_fallback_sources(base_build, original_graph, candidate_graph) -> tuple[str, ...]` and `monaco_composite_specs(base_spec, source_identities) -> tuple[CandidateSpec, ...]`.
- Uses `meshopt-direct-position-v1` and `direct-degenerate-prefilter-v1` exactly.

- [ ] **Step 1: Write fallback-selector RED tests**

  Select only paired visual SMDs whose sealed base metrics say `preserved_exact` or
  the approved fallback reason. Reject filename heuristics, animation/physics roles,
  DMX, ambiguous identities, missing outputs, duplicate provenance, malformed
  prefilter evidence, and a ninth source.

- [ ] **Step 2: Write schedule RED tests**

  Assert exact global ratios `(0.50, 0.45, 0.40, 0.35)`, at most four candidates,
  the same ratio for every source within one candidate, no Cartesian product, and
  stable candidate IDs under shuffled metrics.

- [ ] **Step 3: Run composite/search tests and verify RED**

  Run:
  `python -m unittest tests.maximum_optimizer.test_composite tests.maximum_optimizer.test_search -v`

  Expected: Monaco composite contracts are absent.

- [ ] **Step 4: Implement isolated direct-position fallback builds**

  Build the Blender-adaptive base once. For each global ratio, process selected
  sources in isolated canonical mini-QCs, require stable prefilter schema/hash and
  `fallback_reason is None`, then overlay output bytes on the base.

- [ ] **Step 5: Require complete compile and gates**

  Every variant compiles the full QC and receives structural, whole, and focused
  validation. Keep the Blender base in evaluations. `select_winner` continues to use
  compiled bytes only among fully passing candidates.

- [ ] **Step 6: Add compiled-byte regression tests**

  Make the smallest intermediate-SMD candidate compile larger and assert it loses.
  Make all composites fail one focus and assert the passing Blender base wins. Make
  every variant larger than original and assert original preservation.

- [ ] **Step 7: Run composite integration and commit checkpoint 6**

  Run:
  `python -m unittest tests.maximum_optimizer.test_composite tests.maximum_optimizer.test_search tests.maximum_optimizer.test_orchestrator -v`

  Expected: zero failures.

  Commit:
  `git commit -m "feat: build bounded adaptive direct composites"`

---

### Task 7: Report, cache, cancellation, and resource-bound integration

**Files:**
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `maximum_optimizer/cache.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`
- Modify: `tests/maximum_optimizer/test_cache.py`
- Modify: `tests/maximum_optimizer/test_focused_cache.py`

**Interfaces:**
- Candidate cache schema records whole, focused, composition, and final authorization evidence hashes, but all gates rerun on resume.
- Maximum report attempts expose focused status, recovery round, changed sources, and final whole reauthorization.

- [ ] **Step 1: Write cache-schema RED tests**

  Reject old/new mixed fields, forged focused pass, wrong composition hash, stale
  profile/evidence version, same-size tampering, reparse content, extra images, and
  source/material proof changes. Confirm a valid hit still invokes structural,
  whole, focused comparison, and final authorization.

- [ ] **Step 2: Write report/cardinality RED tests**

  Require one selected focus record per target, contiguous recovery rounds, exact
  changed/reused focus partition, complete compile artifacts, and terminal states
  for cancelled unattempted focuses.

- [ ] **Step 3: Write hard-bound RED tests**

  Exercise top-K 5, 17 whole states, 3 poses, a fifth changed source, ninth Monaco
  fallback, fifth direct ratio, fourth recovery round, and candidate-budget
  exhaustion. Each must reject before starting the excess process.

- [ ] **Step 4: Run integration tests and verify RED**

  Run:
  `python -m unittest tests.maximum_optimizer.test_cache tests.maximum_optimizer.test_focused_cache tests.maximum_optimizer.test_orchestrator -v`

  Expected: missing schema/report/bound enforcement.

- [ ] **Step 5: Implement exact cache and report schemas**

  Bump the candidate cache record schema once. Keep relative contained paths, exact
  key sets, canonical hashes, atomic writes, and current corrupt-entry-as-miss
  behavior.

- [ ] **Step 6: Add cancellation barriers and event stages**

  Add stages `focused_select`, `focused_render`, `focused_compare`,
  `recovery_compose`, `recovery_compile`, and `final_whole_visual`. Cancellation
  between stages writes partial evidence and never promotes output.

- [ ] **Step 7: Run integration gate and commit checkpoint 7**

  Run:
  `python -m unittest tests.maximum_optimizer.test_cache tests.maximum_optimizer.test_focused_cache tests.maximum_optimizer.test_orchestrator -v`

  Expected: zero failures; privilege skips remain skips.

  Commit:
  `git commit -m "feat: audit focused recovery lifecycle"`

---

### Task 8: Evidence-v3 activation and full compatibility gate

**Files:**
- Modify only if approved evidence requires its trust anchor: `maximum_optimizer/calibration_evidence.py`
- Modify only for trusted profile activation: `maximum_optimizer/profiles/maximum-focused-lvs-v1.json`
- Modify tests only for approved immutable hashes: `tests/maximum_optimizer/test_calibration_evidence.py`, `tests/maximum_optimizer/test_fidelity_selection.py`
- Do not modify the evidence-v3 builder or evidence payload in this implementation task.

**Interfaces:**
- Activates schema 3 only after independently reviewed evidence and profile hashes are known.
- Leaves `maximum-experimental-v1.json` uncalibrated unless the user separately approves replacing the production sentinel.

- [ ] **Step 1: Verify the approved evidence externally**

  Recompute the committed evidence-v3 file SHA-256, canonical evidence seal, trusted
  focused lane bindings, family/state/source/provenance/compiled bindings, and
  mutation regressions. Record exact reviewed hashes in the trust-anchor test.

- [ ] **Step 2: Add the approved profile and run RED trust tests**

  The profile must fail before the trust anchor is updated. This proves a copied
  schema-3 file cannot self-authorize.

- [ ] **Step 3: Update only reviewed trust constants**

  Change no thresholds, policy values, evidence content, renderer, or optimizer in
  this step. Recompute profile and evidence hashes after the edit.

- [ ] **Step 4: Run focused compatibility suites**

  Run:

  ```powershell
  python -m unittest `
    tests.maximum_optimizer.test_calibration_evidence `
    tests.maximum_optimizer.test_fidelity_selection `
    tests.maximum_optimizer.test_focused_regions `
    tests.maximum_optimizer.test_focused_cache `
    tests.maximum_optimizer.test_regions `
    tests.maximum_optimizer.test_visual_validation `
    tests.maximum_optimizer.test_composite `
    tests.maximum_optimizer.test_search `
    tests.maximum_optimizer.test_cache `
    tests.maximum_optimizer.test_orchestrator -v
  ```

  Expected: zero failures; only established platform privilege skips.

- [ ] **Step 5: Run the complete backend suite**

  Run: `python -m unittest discover -s tests\maximum_optimizer`

  Expected: zero failures.

- [ ] **Step 6: Inspect process and repository scope**

  Run:

  ```powershell
  Get-Process | Where-Object { $_.ProcessName -like '*blender*' -or $_.ProcessName -like '*studiomdl*' }
  git diff --check
  git status --short
  ```

  Confirm no process remains, no unreviewed evidence changed, no WPF/CLI file changed,
  and only the approved schema-3 profile/trust files plus implementation files are in
  scope.

- [ ] **Step 7: Request independent review**

  Require separate reviewers for: trusted evidence/profile activation; isolated
  renderer correctness; donor/composition provenance; cache/path safety; schema-1/2
  compatibility; cancellation and DoS bounds.

- [ ] **Step 8: Commit activation checkpoint only after approval**

  Commit:
  `git commit -m "feat: activate trusted focused fidelity profile"`

## Checkpoint review order

1. Trusted schema and pure selector.
2. Renderer isolation.
3. Evidence/cache safety.
4. Orchestrator focused authorization.
5. Donor/exact recovery composition.
6. Monaco bounded composite.
7. Lifecycle/report/DoS integration.
8. Independent evidence-v3 activation and full verification.

No checkpoint may be folded into evidence activation. A rejection at any checkpoint
is fixed and re-reviewed before the next task begins.

## Self-review

- Spec coverage: profile gate, ranking, isolation, recovery, Monaco composition,
  cache, evidence, cardinality, cancellation, compiled-byte winner, and every hard
  resource bound each have an implementation task and test command.
- Scope: no UI, CLI, unrelated refactor, evidence regeneration, or production enable
  is included before Task 8.
- Type consistency: `FocusedRegionPolicy`, `FocusTarget`, `FocusedGateResult`,
  `SourceOverlay`, and `CompositeRecipe` are introduced before downstream use.
- Test discipline: every behavior task begins with RED tests, names the exact command,
  ends with a focused green gate, and has an isolated commit.
