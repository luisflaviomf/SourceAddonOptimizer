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
- The real Models bridge validates every profile-set schema with
  `load_fidelity_profile_set`; no CLI field or argument changes.
- The `roundtrip-control` baseline never runs focused validation and never writes a
  whole-visual index.
- Schema-1 and schema-2 runs never call `focused_visual` and never write a
  whole-visual index.
- Focus bounds are fixed: top-K maximum 4, eight angles, two passes, two poses, sixteen whole states, and three recovery rounds.
- Recovery source manifests are limited to 4,096 regular files and 2 GiB; compiled
  composition manifests are limited to 64 regular artifacts and 2 GiB.
- Donor inspection is limited to eight ordered snapshots per changed source; invalid
  snapshots in the ordered prefix consume the bound and the ninth is never touched.
- A recovery composition changes at most four sources and consumes one recovery round
  and one `SearchBudget.max_candidates` slot before any mutation or process launch.
- Under trusted schema 3, byte-exact composite recovery disables the legacy
  `search._regional_recovery` path; schema-1/schema-2 search remains unchanged.
- Monaco uses exactly four terminal global direct ratios
  `(0.50, 0.45, 0.40, 0.35)`, at most eight fallback sources, no Cartesian product,
  bracket refinement, donor recovery, legacy regional recovery, filename trigger, or
  environment trigger.
- The Monaco base is the smallest compiled ordinary `blender-adaptive-v1`
  evaluation that already passed structural, initial whole, and every focused gate;
  candidate ID breaks ties.
- Every Monaco ratio is an independent schema-2 composite with one round at index 0,
  rerenders all top-K focuses, and is outside the three-round donor-recovery bound.
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
class FocusSelection:
    selector_input_sha256: str
    eligible_ranking: tuple[FocusTarget, ...]
    selected: tuple[FocusTarget, ...]

@dataclass(frozen=True)
class FocusExpectedMatrix:
    region_key: str
    poses: tuple[str, ...]
    passes: tuple[str, ...]
    angles: tuple[str, ...]
    width: int
    height: int
    reference_count: int
    candidate_count: int

@dataclass(frozen=True)
class FocusProfileProof:
    version: str
    corpus_hash: str
    profile_file_sha256: str
    limits: Mapping[str, float]

@dataclass(frozen=True)
class FocusStateProof:
    state_index: int
    state_name: str
    bodygroups: tuple[tuple[str, int], ...]
    lod_index: int
    poses: tuple[str, ...]
    selected_pose: str
    selected_frame: int
    animation_state: Literal["none", "source"]
    animation_sha256: str | None

@dataclass(frozen=True)
class DuplicateDirectiveProof:
    directive: str
    ignored_values: tuple[str, ...]

@dataclass(frozen=True)
class MaterialRootProof:
    root_index: int
    root_identity: str
    inventory_sha256: str | None

@dataclass(frozen=True)
class MaterialRequestProof:
    request_index: int
    material_identity: str
    search_paths: tuple[str, ...]

@dataclass(frozen=True)
class MaterialFileProof:
    root_index: int
    path: str
    kind: Literal["vmt", "vtf"]
    size: int
    sha256: str

@dataclass(frozen=True)
class MaterialRequestResolution:
    request_index: int
    material_identity: str
    state: Literal["resolved", "missing"]
    root_index: int | None
    search_path_index: int | None
    vmt_path: str | None
    vmt_sha256: str | None
    vtf_root_index: int | None
    vtf_path: str | None
    vtf_sha256: str | None
    shader: str | None
    texture_directive: str | None
    duplicate_root_directives: tuple[DuplicateDirectiveProof, ...]

@dataclass(frozen=True)
class MaterialResolutionProof:
    schema: int
    cacheable: bool
    reason: str
    roots: tuple[MaterialRootProof, ...]
    requests: tuple[MaterialRequestProof, ...]
    files: tuple[MaterialFileProof, ...]
    resolutions: tuple[MaterialRequestResolution, ...]
    total_files: int
    total_bytes: int
    digest: str

@dataclass(frozen=True)
class FocusCacheContext:
    schema: int
    family_input_sha256: str
    candidate_cache_digest: str
    source_pairs: tuple[tuple[str, str, str], ...]
    region_descriptor: RegionDescriptor
    target: FocusTarget
    state: FocusStateProof
    region_manifest_sha256: str
    configuration_manifest_sha256: str
    whole_profile: FocusProfileProof
    focused_profile: FocusProfileProof
    trusted_evidence_v3_sha256: str
    selector_version: str
    renderer_version: str
    dependency_proof_sha256: str
    material_proof: MaterialResolutionProof
    expected: FocusExpectedMatrix

    def to_payload(self) -> Mapping[str, object]:
        """Return the exact canonical schema-1 cache-key payload."""

@dataclass(frozen=True)
class FocusRenderDirectories:
    reference: Path
    candidate: Path

@dataclass(frozen=True)
class RenderFileProof:
    side: Literal["reference", "candidate"]
    kind: Literal["manifest", "image"]
    path: str
    size: int
    sha256: str
    width: int | None
    height: int | None

@dataclass(frozen=True)
class FocusCacheMetadata:
    schema: int
    context: FocusCacheContext
    target: FocusTarget
    expected: FocusExpectedMatrix

@dataclass(frozen=True)
class FocusedEvidenceContext:
    schema: int
    family_id: str
    candidate_id: str
    policy: FocusedRegionPolicy
    whole_profile: FocusProfileProof
    focused_profile: FocusProfileProof
    trusted_evidence_v3_sha256: str
    dependency_proof_sha256: str
    material_proofs: Mapping[str, MaterialResolutionProof]

@dataclass(frozen=True)
class FocusedRenderEvidence:
    target: FocusTarget
    terminal_status: Literal["passed", "failed", "cancelled"]
    expected: FocusExpectedMatrix
    reference_manifest: str
    reference_manifest_sha256: str
    candidate_manifest: str
    candidate_manifest_sha256: str
    files: tuple[RenderFileProof, ...]
    material_proof_sha256: str
    validation: ValidationResult
    cache_hit: bool
    evidence_sha256: str

@dataclass(frozen=True)
class FocusedRecoveryContext:
    schema: int
    base_context: FocusedEvidenceContext
    base_cache_digest: str
    initial_authorization_sha256: str

@dataclass(frozen=True)
class SourceFileProof:
    file_identity: str
    kind: Literal[
        "qc", "visual-source", "animation-source", "physics-source", "auxiliary",
    ]
    relative_path: str
    size: int
    sha256: str

@dataclass(frozen=True)
class SourceTreeManifest:
    schema: int
    root_identity: str
    files: tuple[SourceFileProof, ...]
    total_files: int
    total_bytes: int
    digest: str

@dataclass(frozen=True)
class FocusedEvidenceRef:
    region_key: str
    evidence_sha256: str

@dataclass(frozen=True)
class RecoverySourceSnapshot:
    schema: int
    kind: Literal["candidate", "original"]
    family_id: str
    family_input_sha256: str
    optimizer_contract_sha256: str
    whole_profile_sha256: str
    focused_profile_sha256: str
    dependency_proof_sha256: str
    candidate_id: str | None
    candidate_cache_digest: str | None
    source_root: Path
    source_manifest: SourceTreeManifest
    focused_evidence: tuple[FocusedEvidenceRef, ...]
    snapshot_sha256: str

@dataclass(frozen=True)
class SourceOverlay:
    source_identity: str
    mode: Literal["donor", "exact-original", "direct-position"]
    motivating_region_key: str | None
    base_source_sha256: str
    replacement_sha256: str
    replacement_size: int
    replacement_snapshot_sha256: str
    replacement_candidate_id: str | None
    replacement_cache_digest: str | None
    effective_ratio: float | None
    focused_evidence: tuple[FocusedEvidenceRef, ...]
    reason: str | None

@dataclass(frozen=True)
class CompositeRecipe:
    schema: int
    kind: Literal["focused-recovery-v1", "adaptive-direct-fallback-v1"]
    family_id: str
    family_input_sha256: str
    base_candidate_id: str
    base_spec_sha256: str
    base_cache_digest: str
    base_source_manifest_sha256: str
    optimizer_contract_sha256: str
    whole_profile_sha256: str
    focused_profile_sha256: str
    dependency_proof_sha256: str
    round_index: int
    direct_ratio: float | None
    overlays: tuple[SourceOverlay, ...]
    selector_version: str
    prefilter_version: str | None
    recipe_sha256: str

@dataclass(frozen=True)
class ChangedSourceProof:
    source_identity: str
    relative_path: str
    before_size: int
    before_sha256: str
    after_size: int
    after_sha256: str
    overlay_sha256: str
    replacement_snapshot_sha256: str

@dataclass(frozen=True)
class CompileFileProof:
    relative_path: str
    kind: str
    size: int
    sha256: str

@dataclass(frozen=True)
class CompositionProof:
    schema: int
    recipe_sha256: str
    base_manifest_sha256: str
    composed_manifest_sha256: str
    changed_sources: tuple[ChangedSourceProof, ...]
    evidence_sha256: str

@dataclass(frozen=True)
class StructuralAuthorizationEvidence:
    candidate_cache_digest: str
    composition_evidence_sha256: str
    compile_manifest_sha256: str
    fingerprint_sha256: str
    validation: ValidationResult
    evidence_sha256: str

@dataclass(frozen=True)
class ComposedSourceTree:
    workspace: Path
    optimized_qc: Path
    source_manifest: SourceTreeManifest
    composition: CompositionProof

# These evidence types live in focused_cache.py beside FocusedRenderEvidence;
# they do not live in domain.py and therefore introduce no import cycle.
@dataclass(frozen=True)
class FinalWholeAuthorizationEvidence:
    candidate_id: str
    candidate_cache_digest: str
    recipe_sha256: str
    composition_evidence_sha256: str
    compile_manifest_sha256: str
    whole_index_path: str
    whole_index_sha256: str
    whole_render_evidence_sha256: str
    validation: ValidationResult
    evidence_sha256: str

@dataclass(frozen=True)
class FocusedRecoveryEvidence:
    round_index: int
    terminal_status: Literal[
        "composition_failed", "compile_failed", "structural_failed",
        "focused_failed", "final_whole_failed", "authorized",
    ]
    recipe: CompositeRecipe
    composition: CompositionProof | None
    changed_sources: tuple[ChangedSourceProof, ...]
    reused_region_evidence: tuple[FocusedEvidenceRef, ...]
    compile_files: tuple[CompileFileProof, ...]
    structural: StructuralAuthorizationEvidence | None
    rerun_records: tuple[FocusedRenderEvidence, ...]
    final_whole: FinalWholeAuthorizationEvidence | None
    evidence_sha256: str

@dataclass(frozen=True)
class FocusCacheKey:
    digest: str

    @classmethod
    def build(cls, payload: Mapping[str, object]) -> "FocusCacheKey":
        """Validate the exact FocusCacheContext schema, then hash canonical_json."""
```

`SourceFileProof` through `StructuralAuthorizationEvidence` live in
`maximum_optimizer.domain`. `FocusedRecoveryContext`,
`FinalWholeAuthorizationEvidence`, and `FocusedRecoveryEvidence` live in
`maximum_optimizer.focused_cache` beside `FocusedRenderEvidence`;
`focused_regions` delegates schema-2 construction to that module and never owns a
second authorization parser.

`CandidateSpec` gains one trailing field,
`composite_recipe: CompositeRecipe | None = None`. Ordinary candidates require it to
be `None`; composite candidates require the complete validated recipe. Its canonical
payload, not merely `candidate_id` or `recipe_sha256`, is included in
`CandidateSpec.cache_payload()`, candidate-ID derivation, and `CacheKey`.
`CandidateBuild` gains trailing
`source_snapshot: RecoverySourceSnapshot | None = None`; schema-3 completed builds
require it. Nested recipes/proofs are never serialized into the existing
`Mapping[str, str]` provenance field; provenance remains a string-only report summary
containing evidence IDs/hashes, while typed payloads use their dedicated fields.
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
- Extends selection compatibly with `select_focus_targets_with_evidence(states, manifest, profile, policy) -> FocusSelection`; existing `select_focus_targets(...)` still returns only `selection.selected`.
- Produces `FocusCacheKey.build(payload: Mapping[str, object])`, `FocusedRenderCache.lookup(key, snapshot_root, cancel_event) -> FocusRenderDirectories | None`, `FocusedRenderCache.store(key, source: FocusRenderDirectories, metadata: FocusCacheMetadata, expected_files: Sequence[RenderFileProof], cancel_event) -> FocusRenderDirectories`, `FocusedRenderCache.invalidate(key)`, and `material_resolution_proof(roots, requests, cancel_event) -> MaterialResolutionProof`.
- Produces `validate_focused_target(cache, key, target, profile, render_fresh, snapshot_root, metadata: FocusCacheMetadata, expected_files: Sequence[RenderFileProof], cancel_event, comparator=compare_render_sets) -> tuple[FocusRegionResult, FocusedRenderEvidence]`. This is the only Task-3 cache/fresh authorization helper and always calls the comparator exactly once.
- Produces `focused_gate_evidence_payload(context: FocusedEvidenceContext, selection: FocusSelection, records: Sequence[FocusedRenderEvidence], recoveries: Sequence[object] = ()) -> Mapping[str, object]`. Task 3 accepts only empty `recoveries` and emits schema 1.

- [ ] **Step 1: Write exact cache-key and selection-evidence RED tests**

  Assert `select_focus_targets_with_evidence` exposes every unique anchor in ranked
  order and that its selected prefix equals the existing selector result. Build one
  valid `FocusCacheContext`, then mutate family input, candidate digest, each SMD,
  descriptor, target, state/bodygroup/LOD, pose/frame/animation, region/configuration
  manifests, whole/focused profile payloads and file hash, trusted evidence v3,
  selector, renderer, dependency proof, material proof, pass, angle, cardinality,
  width, and height. Every mutation changes the digest. Missing/extra fields,
  shuffled non-canonical sequences, bool-as-int, NaN/infinity, non-JSON values, and a
  non-cacheable material proof are rejected before hashing.

  ```python
  selection = select_focus_targets_with_evidence(states, manifest, profile, policy)
  self.assertEqual(selection.selected, select_focus_targets(states, manifest, profile, policy))
  self.assertEqual(tuple(item.rank for item in selection.eligible_ranking), tuple(range(len(selection.eligible_ranking))))
  self.assertNotEqual(FocusCacheKey.build(base), FocusCacheKey.build(changed_smd))
  with self.assertRaises(ValueError):
      FocusCacheKey.build({**base, "unexpected": True})
  ```

- [ ] **Step 2: Write material-proof DoS and cancellation RED tests**

  Assert root/request priority, case folding, selected VMT/VTF hashes, duplicate
  directives, and earlier-root negative/shadow evidence are deterministic. Exactly
  4,096 files and 2 GiB are cacheable; file 4,097 or the first byte above 2 GiB
  returns `cacheable=False` and stops before hashing the excess file. Unsafe trees,
  permissions, reparse points, and special files disable caching without producing a
  visual pass/fail. Cancellation before traversal, between files, and during a hash
  chunk raises `ProcessCancelledError`.

  ```python
  self.assertTrue(proof_at_limits.cacheable)
  self.assertEqual(over_file_limit.reason, "file-limit")
  self.assertEqual(over_byte_limit.reason, "byte-limit")
  hasher.assert_not_called_with(file_4097)
  with self.assertRaises(ProcessCancelledError):
      material_resolution_proof(roots, requests, cancelled)
  ```

- [ ] **Step 3: Write cache integrity, path, and atomic RED tests**

  Require the exact root layout, metadata hash, sorted payload manifest, expected
  image matrix, dimensions, hashes, and cardinality. Reject extra, missing, corrupt,
  duplicate, case-colliding, absolute, UNC, drive, backslash-alias, dot/parent,
  special-file, symlink, broken-link, junction/reparse, non-contained, stale-material,
  and source-mutated-during-copy entries. Cover each reparse case with a real-platform
  test when available and a deterministic mocked `st_file_attributes & 0x400` test.

  Inject failures/cancellation during copy, post-copy verification, marker write,
  quarantine, promotion, and cleanup. Assert the marker is last, files/directories
  are flushed, the previous valid entry is restored, a concurrent valid same-key
  winner is retained, and cleanup/invalidation touches only owned direct children.
  Mutate a valid cache entry while it is copied to `snapshot_root`; lookup must
  remove the incomplete snapshot and return a miss rather than expose shared bytes.

- [ ] **Step 4: Run cache tests and verify RED**

  Run: `python -m unittest tests.maximum_optimizer.test_focused_cache -v`

  Expected: missing module/interfaces.

- [ ] **Step 5: Implement exact key and bounded material proof**

  Use `reporting.canonical_json` with exact key sets and finite values. Preserve
  resolver root/search priority and inventory every relevant regular VMT/VTF needed
  to prove selected and shadowed outcomes. Check cancellation at every traversal and
  hash boundary. Do not build or store a cache key when `cacheable` is false.

- [ ] **Step 6: Implement atomic focus cache**

  Do not subclass or directly reuse `CandidateCache.store`: its generic `copytree`
  and marker are insufficient for renders. Reuse the stronger no-follow walk,
  cancellable hash/copy, reparse-bit, exact file-manifest, staging/quarantine rollback,
  and contained-relative-path patterns already present in `orchestrator.py`. Store
  exactly `complete.json`, `metadata.json`, `payload/reference`, and
  `payload/candidate`; verify copied bytes against `expected_files` before writing the
  completion marker last. Fsync content and directories before atomic promotion.
  Lookup must recompute the key from metadata, copy a hit no-follow into an empty
  private snapshot, and verify the copied manifest before returning it.

- [ ] **Step 7: Implement the mandatory recompare helper**

  On a valid hit, use only the returned private snapshot directories. On a miss,
  render fresh and compare those fresh directories. If the proof is cacheable,
  publication may also occur, but it is an optimization side effect and never the
  source of authorization for that miss. Call `comparator` exactly once after either
  path. Cache metadata has no validation result or `passed` field; an entry
  containing either is invalid.

  ```python
  directories, cache_hit = cached_or_fresh(...)
  validation = comparator(directories.reference, directories.candidate, profile)
  return build_focus_result_and_record(target, validation, directories, cache_hit)
  ```

- [ ] **Step 8: Implement sealed no-recovery evidence payloads**

  Validate complete eligible ranking, exact selected prefix, ranks `0..n-1`, unique
  region keys, exact selected-target/record cardinality, record-target equality, one
  terminal record per selected target, trusted profile/evidence/dependency/material
  hashes, finite coherent validation payloads, relative contained manifest/image
  paths, per-record seals, and the canonical outer SHA-256. Require `recoveries == ()`
  and emit `"recoveries": []`; non-empty recovery is rejected until Task 5 schema 2.
  Cache status is serialized only as diagnostics and is never consulted when
  aggregating `passed`.

- [ ] **Step 9: Prove cache hits revalidate and evidence fails closed**

  Restore a valid cached render and inject a comparator that fails. Assert it is
  called once and the returned focused result fails. Assert the same helper calls the
  comparator once on a fresh render. Mutate every sealed context/selection/record
  field and reject missing/extra targets, rank gaps, target mismatch, duplicate or
  absent terminal records, non-empty recovery, non-finite validation, and untrusted
  v3/material/dependency hashes. Toggling `cache_hit` may alter the audit seal but
  leaves the hard aggregate result identical.

- [ ] **Step 10: Run focused cache/evidence tests and commit checkpoint 3**

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
- `ProductionAdapters.visual(..., whole_profile, *, focused_profile=None)` still
  returns the current whole `ValidationResult`; existing schema-1/2/control callers
  use the unchanged four positional arguments. For a schema-3 search candidate
  only, the orchestrator supplies `focused_profile=` and requires a fresh authoritative
  `logs/whole-visual-index.json`; schemas 1/2 and `roundtrip-control` write none.
- Adds `ProductionAdapters.focused_visual(manifest, control, candidate, whole_profile, focused_profile, policy) -> FocusedGateResult`.
- Consumes Task-3 `select_focus_targets_with_evidence`,
  `validate_focused_target`, and `focused_gate_evidence_payload(...,
  recoveries=())`; no alternate cache/compare/evidence authorization path is
  permitted.

- [ ] **Step 1: Write ordering and compatibility RED tests**

  Assert schema 1/2 never call `focused_visual`, never require that method on fake
  adapters, never write `whole-visual-index.json`, and preserve the current
  five-argument `CandidateEvaluation` defaults. Assert `roundtrip-control` performs
  only structural and whole compatibility gates. For a schema-3 search candidate,
  assert exact miss order `build -> structural -> whole -> focused -> candidate
  cache store`; a compiled-cache hit skips build/store but reruns all three hard
  gates. Structural or whole failure, focused exception, invalid focused return,
  focused cancellation, and a missing target result make zero candidate-cache
  stores and zero promotions. A smaller focus-failed candidate cannot win.

  Add a bridge regression that patches decompile/run dependencies and proves
  `run_maximum_from_existing_args` accepts trusted schema 3 through
  `load_fidelity_profile_set` without adding a CLI argument. Schema 1 and schema 2
  remain accepted; an untrusted schema 3 fails before decompile.

- [ ] **Step 2: Write whole-index RED tests**

  Require exact schema/root keys; family/model/input and candidate/spec/cache
  identity; selected whole/focused profile versions; dependency/renderer proofs;
  full canonical family region-manifest path/hash; ordered contiguous states up to
  16; canonical bodygroups/LOD/poses; exact source pairs; state filtered
  region/configuration manifests; reference/candidate render manifests; complete
  geometry rows; animation classification and paired source proofs; and the outer
  canonical seal.

  Mutate each field and current file independently, including same-size bytes after
  index publication. Reject self-resealed changes, stale cache-restored indexes,
  reordered/duplicate/missing states, incomplete region-pose rows, and absolute,
  UNC, drive, backslash-alias, dot/parent, case-colliding, special-file, symlink,
  junction/reparse, and non-contained paths before focused rendering. A failed or
  cancelled whole visual must remove/leave no authoritative index.

- [ ] **Step 3: Run orchestrator tests and verify RED**

  Run:
  `python -m unittest tests.maximum_optimizer.test_orchestrator -v`

  Expected: focused interfaces/stages are absent.

- [ ] **Step 4: Implement whole-index writing and focused adapter**

  Change the real bridge preflight from `load_profile` to
  `load_fidelity_profile_set` while preserving every existing CLI argument and
  return code. Activate focus only when `profile_set.focused_policy is not None` and
  obtain `focused_profile_for(profile_class)`; do not branch on the shared typed
  selector mode.

  Refactor only the evidence returned by the current render loop; do not change its
  state matrix, comparison, camera, material, or bodygroup behavior. Clear stale
  candidate whole/focused evidence safely before schema-3 validation. Build and
  atomically publish the whole index only after a fresh whole pass. The focused
  adapter reparses the sealed index against current contained bytes, uses the full
  family manifest for selection, selects the exact indexed state/source pair, and
  supplies Task 2 the complete selected-state source manifest before object
  isolation.

  Render/process targets in selector-rank order through Task-3
  `validate_focused_target`; cache hits still compare exactly once from their
  private snapshots. After every terminal target, atomically update diagnostic-only
  `logs/focused-region-gate.partial.json`; Task-3 evidence parsers reject this
  progress schema. Only after exact terminal cardinality is complete, call
  `focused_gate_evidence_payload` with the complete ranking, exact selected prefix,
  every terminal record, and `recoveries=()`, then atomically publish authoritative
  `logs/focused-region-gate.json` and remove the partial journal. Continue later
  focuses after a validation failure for complete diagnostics; stop only on
  cancellation/infrastructure failure.

- [ ] **Step 5: Aggregate focused hard gates**

  Extend `CandidateEvaluation` with trailing defaults so existing positional callers
  remain valid. Preserve `whole_visual` separately, expose an immutable exact
  `focused_by_region`, and set `visual` to the search-authoritative hard aggregate.
  Require exactly one matching `FocusRegionResult` per selected target. Missing,
  duplicate, extra, target-mismatched, or invalidly sealed results create an
  explicit focused gate failure; never rely on `all([])`.

  Preserve every whole failure, metric, and worst-scope contribution. Add focused
  failures with canonical `REGION_KEY/POSE` scope and combine metrics without
  erasing or making a worse whole result appear better. `AttemptReport.visual`,
  `CandidateEvaluation.passed`, `select_winner`, best events, and candidate cache
  diagnostics all consume the aggregate, not the whole-only result.

- [ ] **Step 6: Add cancellation tests**

  Cover cancellation before selection, between focuses, during cache restore,
  during render, after comparison, before every atomic evidence publication, after
  `focused_visual` returns, and before candidate-cache store. At every injection,
  assert exactly one candidate terminal event, no `best_updated`, no candidate-cache
  store, no selected-build/output promotion, original-family preservation, and the
  existing family/run cancellation outcome. Completed per-target evidence must be
  atomic and complete; a partial write or unattempted target cannot authorize pass.

- [ ] **Step 7: Run focused orchestrator gate and commit checkpoint 4**

  Run:
  `python -m unittest tests.maximum_optimizer.test_orchestrator tests.maximum_optimizer.test_focused_regions tests.maximum_optimizer.test_focused_cache tests.maximum_optimizer.test_cache -v`

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
- Modify: `maximum_optimizer/focused_cache.py`
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `tests/maximum_optimizer/test_search.py`
- Modify: `tests/maximum_optimizer/test_focused_cache.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- `maximum_optimizer.domain` produces the locked core types `SourceFileProof`,
  `SourceTreeManifest`, `FocusedEvidenceRef`, `RecoverySourceSnapshot`,
  `SourceOverlay`, `CompositeRecipe`, `ChangedSourceProof`, `CompileFileProof`,
  `CompositionProof`, `StructuralAuthorizationEvidence`, and `ComposedSourceTree`.
- `CandidateBuild` gains trailing
  `source_snapshot: RecoverySourceSnapshot | None = None`. It is required for every
  completed schema-3 ordinary/composite build and is revalidated after cache restore.
- `maximum_optimizer.search.choose_next` gains trailing keyword
  `recovery_mode: Literal["legacy-regional", "external-byte-exact"] =
  "legacy-regional"`. The external mode never calls `_regional_recovery`; only the
  trusted schema-3 orchestrator supplies it.
- `maximum_optimizer.composite` produces:
  `build_source_tree_manifest(root, graph, root_identity, cancel_event) -> SourceTreeManifest`,
  `revalidate_recovery_snapshot(snapshot, cancel_event) -> SourceTreeManifest`,
  `select_recovery_overlays(failed, selection, evaluations,
  snapshots_by_candidate: Mapping[str, RecoverySourceSnapshot],
  original_snapshot, current_recipe, attempted_overlay_sha256,
  round_index) -> tuple[SourceOverlay, ...]`,
  `recovery_candidate_spec(base_spec, recipe) -> CandidateSpec`,
  `compose_candidate_sources(base_build, recipe,
  snapshots_by_sha256: Mapping[str, RecoverySourceSnapshot], workspace,
  cancel_event) -> ComposedSourceTree`, and
  `validate_composition_proof(recipe, snapshots_by_sha256, base_root, composed_root,
  cancel_event) -> CompositionProof`.
- `maximum_optimizer.focused_cache` produces the locked
  `FocusedRecoveryContext`, `FinalWholeAuthorizationEvidence`,
  `FocusedRecoveryEvidence`, and
  `focused_recovery_evidence_payload(context: FocusedRecoveryContext, selection,
  initial_records,
  recoveries: Sequence[FocusedRecoveryEvidence]) -> Mapping[str, object]`.
  It emits schema 2; `focused_gate_evidence_payload` remains schema-1-only.
- The orchestrator retains at most `SearchBudget.max_candidates` entries in a
  per-family `candidate_id -> (CandidateBuild, CandidateEvaluation,
  RecoverySourceSnapshot)` registry plus exactly one original snapshot. The registry
  is discarded at the family terminal and never reconstructed from candidate IDs.
- `CandidateSpec.cache_payload()`, recovery candidate ID, `CacheKey`, cache record,
  and composition evidence include the entire canonical recipe and every donor or
  exact snapshot/focused evidence hash.

- [ ] **Step 1: Write typed-contract RED tests**

  In `test_composite.py`, construct every locked type and mutate each field. Require
  exact schemas, deep immutability, lowercase SHA-256, finite non-bool ratios,
  canonical relative source paths, sorted unique source identities and focus refs,
  exact totals/seals, and no case collisions. Enforce at most 4 overlays, 4,096/2-GiB
  source proofs, and 64/2-GiB compile proofs.

  Candidate snapshots require candidate/cache identity; original snapshots forbid
  both and forbid focused refs. `snapshot_sha256` seals every field except the runtime
  absolute `source_root`, which is revalidated against the sealed manifest whenever
  used.

  Enforce the overlay mode matrix exactly. `donor` requires donor ID/cache, strictly
  higher ratio, replacement snapshot, a motivating region contained in non-empty
  focused refs. `exact-original` requires a motivating region, forbids
  donor/ratio/focused refs, and requires `donors-exhausted-v1` plus the original
  snapshot. `direct-position` requires no motivating region, direct candidate/cache,
  the recipe's global ratio, its snapshot, no donor focus refs, and
  `approved-direct-position-v1`.

- [ ] **Step 2: Write durable source-snapshot RED tests**

  Build fresh and cache-restored candidate snapshots from complete optimized QC
  graphs. Assert canonical identity/path/hash/size equivalence and bind family input,
  optimizer, profile, dependency, candidate/cache, and focused evidence. Mutate a
  source after publication, swap equal-size bytes during a read, add/remove/alias a
  source, corrupt a cache record, or use a reparse/special/escaping path; fresh
  validation rejects and cache restore becomes a miss. Original snapshot bytes must
  come from the authoritative original QC graph and per-source proof, never from the
  base candidate clone.

- [ ] **Step 3: Write bounded donor-selection RED tests**

  Use a candidate rejected only because another focused region failed: structural,
  initial whole, and the motivating focus pass, so it is eligible. Assert the least
  strictly less-aggressive effective ratio wins, with candidate ID and snapshot hash
  tie-breaks. Reject structural/whole failure, missing/mismatched focused evidence,
  cross-family/input/profile/dependency/optimizer/strategy, self, equal/lower ratio,
  duplicate source/candidate, recursive recovery donor, stale source manifest, and
  repeated replacement bytes.

  Compute `optimizer_contract_sha256` from exactly engine, target error, repair
  profile, strategy, update-vertices, and transfer. Exclude ID, target ratio, region
  overrides, and recipe; bind family/input, profile, dependency, and tools separately.
  Compute effective ratio from the motivating region's override or target ratio.

  Feed prior attempted overlay hashes back into selection. After composition,
  compile, structural, focused, or final-whole failure, the next reserved round skips
  that exact replacement and deterministically advances the same failed source to the
  next donor or original fallback; it never retries an identical recipe.

  Present nine ordered snapshots and instrument manifest access. Invalid candidates
  in the first eight consume the bound and the ninth is never opened or hashed.
  Under schema 3, call `choose_next(...,
  recovery_mode="external-byte-exact")` and assert it never invokes legacy
  `_regional_recovery`; the default keeps all prior tests unchanged.

- [ ] **Step 4: Write exact-original, dependency-closure, and accounting RED tests**

  Exhaust donors and require one exact-original overlay. If original bytes already
  equal current replacement bytes, return no overlay and consume neither candidate
  nor round. When two focuses share a target source, or a changed source appears in
  another focus's sealed `source_pairs`/configuration/animation dependency closure,
  both are affected and rerun; equality of only `target.source_identity` never
  authorizes reuse.

  Reject a fifth changed source, fourth reserved round, or exhausted candidate budget
  before opening a snapshot, mutating a workspace, or launching a process. Reserve
  both counters before composition; composition, compile, structural, focus, and
  final-whole failures keep their reserved attempt index.

- [ ] **Step 5: Run source/search tests and verify RED**

  Run:
  `python -m unittest tests.maximum_optimizer.test_search tests.maximum_optimizer.test_composite -v`

  Expected: missing typed recovery APIs.

- [ ] **Step 6: Implement snapshots, canonical cumulative recipes, and cache identity**

  Implement source manifests with no-follow current-byte validation and the fixed
  bounds. Keep one immutable ordinary base. Every round recipe contains the complete
  cumulative overlay set sorted by source identity; replacing a source replaces its
  prior overlay and duplicate overlays are impossible. Hash family/input, base
  spec/cache/source manifest, optimizer/profile/dependency, selector, round, sorted
  overlays, replacement snapshots, and all focused refs.

  Derive the safe recovery candidate ID from the full recipe hash and store the full
  canonical recipe in `CandidateSpec.cache_payload()`. Any recipe/donor/evidence
  mutation changes both ID and `CacheKey`. Ordinary candidates require
  `composite_recipe=None`; composite candidates require exact base-contract equality
  and cannot repeat an attempted ID.

- [ ] **Step 7: Implement byte-exact source composition and proof**

  Require a fresh non-existing, non-overlapping private workspace. Resolve every
  replacement only through the supplied sealed snapshot registry. Traverse, hash,
  and copy through contained no-follow handles with cancellation and file/byte
  bounds. Compare complete base/result manifests: each changed source has exactly one
  overlay/proof, replacement bytes equal its snapshot, every undeclared source is
  byte-identical, and no extra/missing/case-alias path or QC rewrite exists. Reparse
  and pair the complete optimized and original QC graphs. A TOCTOU change, unsafe
  tree, ambiguity, cancellation, or proof mismatch leaves no complete composed tree.

- [ ] **Step 8: Write and implement schema-2 fold and byte-bound authorization**

  First write RED tests for exact top-level/round keys, non-empty contiguous reserved
  indices `0..n-1`, status/optional-field matrix, cumulative recipe continuity, one
  changed-source proof per overlay, complete compile files, structural binding,
  dependency-derived rerun/reuse partition, immediately-prior reused hashes, and
  per-round seals. Mutate/reseal every composition, compile, structural, rerun,
  reused, and final-whole binding independently. Recompute outer seals after forging
  pass/failure, metrics, fidelity score, profile limits, artifact hash, whole-index
  hash, or candidate digest; the parser must still reject semantic inconsistency.

  Fold from the exact initial records. Each rerun replaces exactly its affected
  target; reused targets preserve only the immediately prior evidence hash. Failed
  rounds cannot authorize. Intermediate rounds require `final_whole=None`.
  `final_whole_failed` may carry one fresh failed byte-bound whole record; exactly the
  last `authorized` round carries the only passing final-whole record. Bind it to the
  composite candidate/cache/recipe/composition/compile digests and fresh whole
  index/render proof. The terminal fold has exactly one current passing record per
  selected target. Keep schema 1 byte-for-byte no-recovery-only.

- [ ] **Step 9: Integrate retained builds, compile, structural, selective rerender, and final whole**

  Retain every completed ordinary candidate build/evaluation/snapshot until its
  family terminal, including focused-rejected donors. Revalidate snapshots before
  selection and copy. Recompile the complete QC, seal the exact artifact manifest,
  and create structural evidence bound to composition and current fingerprint.
  Rerender the full dependency-affected focus set; reuse only exact prior context.
  After the folded focused set passes, run final whole exactly once for that round and
  bind its fresh whole index/render evidence. Only an `authorized` schema-2 payload
  may enter winner selection/cache store/best update/output promotion.

  Compile/proof/gate failures reject only the recovery candidate, consume the
  reserved candidate/round, and may continue to the next bounded recipe. Cancellation
  emits one terminal event, leaves atomic partial diagnostics only, performs no cache
  store/best/output promotion, and preserves the original family.

- [ ] **Step 10: Test failure isolation and commit checkpoint 5**

  Run:
  `python -m unittest tests.maximum_optimizer.test_composite tests.maximum_optimizer.test_search tests.maximum_optimizer.test_focused_cache tests.maximum_optimizer.test_orchestrator -v`

  Expected: zero failures; compile/proof/gate errors reject only that recovery
  candidate, all hard bounds reject before excess work, and schema 1 is unchanged.

  Commit:
  `git commit -m "feat: recover focused failures with source donors"`

---

### Task 6: Bounded Monaco adaptive-direct composites

**Files:**
- Modify: `maximum_optimizer/domain.py`
- Modify: `maximum_optimizer/composite.py`
- Modify: `maximum_optimizer/candidates.py`
- Modify: `maximum_optimizer/search.py`
- Modify: `maximum_optimizer/focused_cache.py`
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `batch_optimize_maximum.py`
- Modify: `tests/maximum_optimizer/test_domain.py`
- Modify: `tests/maximum_optimizer/test_composite.py`
- Modify: `tests/maximum_optimizer/test_candidates.py`
- Modify: `tests/maximum_optimizer/test_search.py`
- Modify: `tests/maximum_optimizer/test_focused_cache.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`

**Interfaces:**
- `maximum_optimizer.domain` produces immutable `AdaptiveGraphOccurrenceProof`,
  `AdaptiveSourceMetricsProof`, `AdaptiveCandidateMetricsProof`,
  `DirectPrefilterProof`, `DirectSourceBuildRequest`, and `DirectSourceSnapshot`. A
  direct snapshot contains exactly one contained regular `.smd` output and is not a
  `RecoverySourceSnapshot`.
- `maximum_optimizer.composite` produces
  `select_monaco_base(evaluations, retained_builds) -> CandidateEvaluation | None`,
  `select_exact_fallback_sources(base_build, base_snapshot, original_graph,
  candidate_graph, cancel_event) -> tuple[str, ...]`,
  `direct_source_requests(base_build, base_snapshot, source_identities,
  ratio) -> tuple[DirectSourceBuildRequest, ...]`, and
  `monaco_composite_specs(base_spec, base_snapshot, snapshots_by_ratio) ->
  tuple[CandidateSpec, ...]`. Task 6 extends the Task-5 compositor's snapshot
  resolver to `Mapping[str, RecoverySourceSnapshot | DirectSourceSnapshot]` and
  dispatches strictly by `SourceOverlay.mode`; the other two overlay modes never
  accept a direct snapshot.
- `maximum_optimizer.candidates` produces
  `build_direct_source_snapshot(request, workspace, tools, cancel_event) ->
  DirectSourceSnapshot` and never routes a composite `CandidateSpec.cache_payload()`
  through the existing exact search-candidate JSON parser.
- Uses fixed strategy `meshopt-direct-position-v1`, prefilter
  `direct-degenerate-prefilter-v1`, eligibility reasons
  `ratio-preserved-exact-v1`/`approved-exact-source-fallback-v1`, and direct overlay
  reason `approved-direct-position-v1` exactly.
- Each global ratio is an independent schema-2 composite containing one recovery
  record at `round_index == 0`; it is outside the Task-5 donor round counter of three.

  ```python
  @dataclass(frozen=True)
  class AdaptiveGraphOccurrenceProof:
      graph_relative_path: str
      directive: str
      line: int
      logical_path: str
      role: Literal["visual"]

  @dataclass(frozen=True)
  class AdaptiveSourceMetricsProof:
      source_identity: str
      source_relative_path: str
      source_size: int
      source_sha256: str
      output_relative_path: str
      output_size: int
      output_sha256: str
      preserved_exact: bool
      eligibility_reason: Literal[
          "ratio-preserved-exact-v1",
          "approved-exact-source-fallback-v1",
      ] | None
      occurrences: tuple[AdaptiveGraphOccurrenceProof, ...]
      metrics_sha256: str

  @dataclass(frozen=True)
  class AdaptiveCandidateMetricsProof:
      schema: Literal[1]
      family_id: str
      family_input_sha256: str
      candidate_id: str
      candidate_cache_digest: str
      strategy: Literal["blender-adaptive-v1"]
      source_manifest_sha256: str
      raw_metrics_sha256: str
      sources: tuple[AdaptiveSourceMetricsProof, ...]
      evidence_sha256: str

  @dataclass(frozen=True)
  class DirectDroppedTriangleProof:
      ordinal: int
      material: str
      primary_bones: tuple[str, ...]
      reason: Literal["cross-squared-at-most-1e-30"]
      source_sha256: str

  @dataclass(frozen=True)
  class DirectPrefilterProof:
      schema: Literal[1]
      strategy: Literal["direct-degenerate-prefilter-v1"]
      cross_squared_threshold: float
      source_triangle_count: int
      dropped_count: int
      dropped_fraction: float
      triangles: tuple[DirectDroppedTriangleProof, ...]
      applied: Literal[True]
      evidence_sha256: str

  @dataclass(frozen=True)
  class DirectSourceBuildRequest:
      schema: Literal[1]
      family_id: str
      family_input_sha256: str
      base_candidate_id: str
      base_cache_digest: str
      base_source_manifest_sha256: str
      optimizer_contract_sha256: str
      whole_profile_sha256: str
      focused_profile_sha256: str
      dependency_proof_sha256: str
      source_identity: str
      source_relative_path: str
      source_size: int
      source_sha256: str
      direct_ratio: float
      strategy: Literal["meshopt-direct-position-v1"]
      prefilter_version: Literal["direct-degenerate-prefilter-v1"]
      expected_prefilter: DirectPrefilterProof
      request_sha256: str

  @dataclass(frozen=True)
  class DirectSourceSnapshot:
      schema: Literal[1]
      request: DirectSourceBuildRequest
      direct_candidate_id: str
      direct_cache_digest: str
      source_root: Path
      output_relative_path: str
      output_size: int
      output_sha256: str
      triangles_before: int
      triangles_after: int
      prefilter: DirectPrefilterProof
      fallback_reason: None
      preserved_exact: Literal[False]
      reason: Literal["approved-direct-position-v1"]
      snapshot_sha256: str
  ```

  `request_sha256` seals every request field except itself.
  `metrics_sha256` seals the normalized per-source metrics and canonical grouped
  occurrences; `AdaptiveCandidateMetricsProof.evidence_sha256` seals every field
  except itself and must bind the same candidate/cache/source manifest as the selected
  base snapshot. It inventories every paired visual SMD, not only eligible sources.
  `snapshot_sha256` seals every snapshot field except itself and runtime-only
  `source_root`. `SourceOverlay.replacement_snapshot_sha256` may resolve to a
  `DirectSourceSnapshot` only when `mode == "direct-position"`; donor and
  exact-original modes continue to require `RecoverySourceSnapshot`.

- [ ] **Step 1: Write typed adaptive-metrics, direct-request, and snapshot RED tests**

  In `test_domain.py` and `test_composite.py`, construct each new type and mutate
  every field. Require exact schema/keys, deep immutability, lowercase hashes,
  finite non-bool ratio, exact threshold `1e-30`, coherent dropped counts/fraction,
  canonical source identity/path, fixed strategy/prefilter/
  reason enums, direct candidate/cache identity, one `.smd` output, changed bytes,
  decreasing triangle counts, and a seal over every canonical field except the
  runtime absolute root. Reject a snapshot presented as a complete recovery source
  tree, a second output, case aliases, reparse/special/escaping paths, stale current
  bytes, and per-ratio aggregate discovery/copy/hash beyond 4,096 files or 2 GiB.
  Require the adaptive metrics proof to inventory every paired visual SMD in canonical
  order, group only byte-identical occurrences, bind raw metrics plus base
  candidate/cache/source manifest, and authorize eligibility only through the two
  fixed reason enums. Reject an eligible-only/incomplete inventory and arbitrary
  diagnostic exception text.

- [ ] **Step 2: Write deterministic base and fallback-selector RED tests**

  Build shuffled ordinary evaluations and select only a `blender-adaptive-v1`
  evaluation that already passed structural, initial whole, and all focused gates;
  choose smallest actual compiled bytes then candidate ID. Reject composite bases,
  schema-1/2 activation, environment-only activation, filename tokens, failed gates,
  missing retained build/snapshot, and stale base evidence.

  Parse the sealed base candidate-metrics proof and both QC graphs. Select only paired
  visual `.smd` identities with `preserved_exact == true` and fixed reason
  `ratio-preserved-exact-v1` or `approved-exact-source-fallback-v1`. Group repeated
  byte-identical graph occurrences, but reject conflicting duplicate provenance,
  animation/physics roles, DMX, ambiguous/case-colliding identities, arbitrary
  exception strings, missing/stale outputs, and a ninth eligible source. Instrument
  snapshot access and assert the ninth is rejected before any mini-build, hash, or
  process. Assert zero eligible sources returns no proposal and consumes no budget.

- [ ] **Step 3: Write fixed-ratio terminal schedule RED tests**

  Assert exact global ratios `(0.50, 0.45, 0.40, 0.35)`, exactly four scheduled
  candidates (subject only to the existing outer attempt budget),
  the same ratio for every source within one candidate, no Cartesian product, and
  stable candidate IDs under shuffled metrics, graph occurrences, and snapshot-map
  insertion order. Candidate/recipe hashes must change with any base cache/source
  manifest, direct snapshot, prefilter proof, ratio, or source mutation.

  Feed pass/fail results for all four adaptive-direct specs back to `choose_next` and
  instrument `_narrowest_bracket`, donor recovery, and legacy `_regional_recovery`.
  Assert none is called for this terminal strategy and no midpoint/fifth ratio is
  returned. Assert outer `SearchBudget.max_candidates` is reserved before opening a
  direct snapshot or starting a mini-build; unavailable budget leaves all inputs
  unopened.

- [ ] **Step 4: Run domain/composite/search tests and verify RED**

  Run: `python -m unittest tests.maximum_optimizer.test_domain tests.maximum_optimizer.test_composite tests.maximum_optimizer.test_search -v`

  Expected: direct request/snapshot and Monaco coordinator contracts are absent.

- [ ] **Step 5: Implement isolated typed direct-position mini-builds**

  First make `batch_optimize_maximum.py` emit a separate normalized
  `eligibility_reason` using only `ratio-preserved-exact-v1`,
  `approved-exact-source-fallback-v1`, or null; retain raw exception text only in a
  diagnostic field excluded from eligibility. Emit the approved-fallback enum only
  after the existing typed exact-fallback predicate accepts the failure class/reason.
  Build and seal the complete
  `AdaptiveCandidateMetricsProof` against current no-follow bytes and the base
  snapshot.

  Implement exact direct parsers/builders rather than adding optional fields to the generic
  search JSON. Each request binds family/input, immutable base candidate/cache/source
  manifest, optimizer/profile/dependency contracts, canonical input proof, one
  global ratio, fixed strategy/prefilter, and request digest. Build each selected
  source in a fresh non-overlapping canonical mini-QC workspace through no-follow
  handles and cancellation barriers.

  Recompute `direct-degenerate-prefilter-v1` from the exact input bytes and require
  exact equality with the complete reported proof: schema, strategy, threshold,
  source/dropped counts, dropped fraction, ordered triangle records, and digest.
  Require `applied == true`, `fallback_reason is None`, `preserved_exact == false`,
  exact direct strategy/transfer, changed output hash, decreasing triangle count, and
  a current one-file output proof. On any source failure, publish no partial recipe
  for that ratio, record its reserved terminal failure, clean owned staging no-follow,
  and allow the next fixed ratio only if budget/cancellation permits.

- [ ] **Step 6: Assemble byte-exact independent composites**

  Resolve every direct overlay only through the `DirectSourceSnapshot` registry.
  For one ratio, overlay all selected outputs on the same immutable ordinary base,
  set mode `direct-position`, no motivating region or donor focus refs, fixed reason
  `approved-direct-position-v1`, and the identical global ratio on every overlay.
  Require exact unchanged bytes for every undeclared source and every QC file.

  Emit one complete `adaptive-direct-fallback-v1` recipe/spec per ratio. Each recipe
  is independent, has `round_index == 0`, and seals the base plus all sorted direct
  requests/snapshots/prefilter proofs. Never carry an overlay from one ratio into
  another and never consume the three-round donor-recovery counter.

- [ ] **Step 7: Require complete compile and schema-2 gates**

  Compile the complete composed QC and seal every current contained StudioMDL
  artifact, including required `.mdl/.vvd/.vtx/.ani/.phy` sidecars. Run structural
  authorization, rerender all selected top-K focuses without reuse, and fold exactly
  those records over the selected base candidate's sealed schema-1 initial
  authorization in an independent `FocusedRecoveryContext`/schema-2 payload with one
  record at round 0. The selected target/ranking prefix is immutable across that
  ratio; no fresh ranking can silently drop a base target.
  Only after structural and every focus pass, run exactly one fresh final whole gate
  and bind its whole index/render evidence to recipe, composition, compile manifest,
  and candidate/cache identity. Earlier terminal failure has no final-whole evidence.

  Only terminal `authorized` enters candidate cache, best update, winner selection,
  or promotion. Keep the passing Blender base in evaluations. Cache restore must
  revalidate direct snapshots/current source bytes and rerun structural, all focuses,
  and final whole; cache diagnostics never authorize.

- [ ] **Step 8: Add compiled-byte, failure, and bound regression tests**

  Make the smallest intermediate-SMD tree compile to larger complete artifacts and
  assert it loses to lower actual StudioMDL bytes. Make every composite fail one
  focus and assert the passing Blender base wins without a composite cache store or
  final whole. Make every passing candidate, including the base, at least as large as
  original and assert original preservation.

  Cover one direct source failure, prefilter self-reseal, same-size input/output
  mutation, source alias, incomplete compile sidecars, structural failure, focused
  failure, final-whole failure, cache hit, and cancellation before/within/after each
  mini-build and gate. Assert exactly four full-QC compile attempts maximum, no fifth
  ratio/bracket, no partial recipe/schema-2 authorization, no best update/promotion,
  and schema-1/schema-2 behavior unchanged.

- [ ] **Step 9: Run composite integration and commit checkpoint 6**

  Run:
  `python -m unittest tests.maximum_optimizer.test_domain tests.maximum_optimizer.test_composite tests.maximum_optimizer.test_candidates tests.maximum_optimizer.test_search tests.maximum_optimizer.test_focused_cache tests.maximum_optimizer.test_orchestrator -v`

  Expected: zero failures; four terminal ratios only, independent schema-2 round-0
  authorization, all top-K rerendered, and winner chosen from complete compiled bytes.

  Commit:
  `git commit -m "feat: build bounded adaptive direct composites"`

---

### Task 7: Report, cache, cancellation, and resource-bound integration

**Files:**
- Modify: `maximum_optimizer/orchestrator.py`
- Modify: `maximum_optimizer/cache.py`
- Modify: `maximum_optimizer/reporting.py`
- Modify: `tests/maximum_optimizer/test_orchestrator.py`
- Modify: `tests/maximum_optimizer/test_cache.py`
- Modify: `tests/maximum_optimizer/test_focused_cache.py`
- Create: `tests/maximum_optimizer/test_reporting.py`

**Interfaces:**
- `CandidateCache` publishes exact outer schema 2. Its root contains only
  `payload/`, `payload-manifest.json`, `metadata.json`, and marker-last
  `complete.json`.
- `payload/maximum_cache_record.json` uses exact schema 3 and one mutually
  exclusive `candidate_kind`: `legacy-ordinary-v1`, `schema3-ordinary-v1`,
  `focused-recovery-v1`, or `adaptive-direct-fallback-v1`.
- Legacy profile-schema-1/2 `MaximumRunReport` serialization remains byte-for-byte
  schema 1. Trusted profile schema 3 uses an exact conditional report schema 2 and a
  distinct exact schema-2 progress envelope.
- Cache records retain prior whole/focused/composition/schema-2/final hashes only as
  bounded diagnostics. Restore always rebuilds current hard authorization.
- Control JSON and terminal/progress report files are each limited to 16 MiB.

  The outer schema-2 metadata has only `schema`, `key_digest`, `record_schema`,
  `candidate_kind`, `payload_manifest_sha256`, `payload_file_count`, and
  `payload_total_bytes`. The payload manifest has only `schema`, sorted exact
  `{path,size,sha256,kind}` files, declared file/byte totals, and `digest`.
  `complete.json` has only `schema`, `key_digest`, `metadata_sha256`,
  `record_sha256`, `payload_manifest_sha256`, `payload_file_count`,
  `payload_total_bytes`, and `entry_sha256`; its seal covers every field except
  itself.

  Cache-record schema 3 has exact common keys `schema`, `key_digest`,
  `candidate_kind`, `family_id`, `model_rel`, `family_input_sha256`,
  `candidate_spec`, `dependency_proof_sha256`, `compiled_models_dir`,
  `optimized_qc`, `compile_record`, `provenance`, `source_manifest_sha256`,
  `source_snapshot_sha256`, `compile_manifest_sha256`, `kind_proofs`, and
  `prior_diagnostics`. `kind_proofs` and `prior_diagnostics` use these exact matrices:

  | candidate kind | `kind_proofs` exact keys | `prior_diagnostics` exact keys |
  | --- | --- | --- |
  | `legacy-ordinary-v1` | `schema`, `kind` | `schema`, `whole_visual_sha256` |
  | `schema3-ordinary-v1` | `schema`, `kind`, `source_manifest_sha256`, `source_snapshot_sha256` | `schema`, `whole_index_sha256`, `focused_authorization_sha256` |
  | `focused-recovery-v1` | `schema`, `kind`, `source_manifest_sha256`, `source_snapshot_sha256`, `recipe_sha256`, `composition_evidence_sha256`, `compile_manifest_sha256` | `schema`, `initial_focused_authorization_sha256`, `recovery_schema2_evidence_sha256`, `final_whole_evidence_sha256` |
  | `adaptive-direct-fallback-v1` | `schema`, `kind`, `source_manifest_sha256`, `source_snapshot_sha256`, `recipe_sha256`, `composition_evidence_sha256`, `compile_manifest_sha256`, `direct_request_set_sha256`, `direct_snapshot_set_sha256` | `schema`, `initial_focused_authorization_sha256`, `recovery_schema2_evidence_sha256`, `final_whole_evidence_sha256` |

  All common keys are present for every kind. Only legacy ordinary sets
  `source_manifest_sha256` and `source_snapshot_sha256` to JSON null; every kind
  requires a non-null `compile_manifest_sha256`. Wherever a schema-3 common source or
  compile hash is duplicated in `kind_proofs`, the two values must be exactly equal.
  Composite fields are forbidden in ordinary matrices, donor fields are forbidden in
  adaptive-direct matrices, and absent fields are rejected rather than ignored.

- [ ] **Step 1: Write cache-schema RED tests**

  Build every valid outer-schema-2/record-schema-3 candidate-kind fixture, then add,
  remove, null, swap, or cross-copy each common and kind-specific field. Reject old
  outer/record schemas, ordinary/composite and donor/direct mixtures, wrong seals,
  arbitrary `passed`/validation fields, stale trusted profile/evidence/selector/
  renderer/dependency bindings, and candidate/cache/recipe mismatch as read-only
  misses.

  Prove one atomic publication: payload and typed manifests are copied no-follow into
  same-volume staging, `payload-manifest.json` and metadata bind current copied bytes,
  `complete.json` is fsynced last, the complete staging entry is fully reparsed, and
  only then one `os.replace` promotes it. Crash before marker or promotion exposes no
  hit. A valid concurrent same-key winner is retained only after full schema-2/3
  revalidation. There is no post-promotion sealing step.

  Enforce the physical whitelist: exact record and typed source/compile/composition/
  direct manifest files; contained `src/`, `compiled/`, and, for adaptive-direct only,
  typed `direct/` snapshot outputs. Reject `logs/`, renders, focused snapshots,
  texture caches, temporary/quarantine names, extra images, and any unmanifested
  file before copying it. Exercise same-size tampering, source/material/direct proof
  changes, case aliases, absolute/UNC/drive/backslash/dot/parent paths, special files,
  symlink/junction/reparse leaves and ancestors, source identity changes during read,
  and unsafe invalidation/cleanup.

- [ ] **Step 2: Write report/cardinality RED tests**

  Preserve golden canonical bytes for every existing profile-schema-1/2 report and
  progress fixture. For trusted schema 3, require report schema 2 exact top-level
  keys: `schema`, `report_kind`, `status`, `original_size`, `control_size`,
  `selected_size`, `final_size`, `tool_versions`, `families`, `events`,
  `event_count`, `event_bound`, `cancelled`, `report_path`, and `report_sha256`.
  A progress envelope has exactly `schema`, `report_kind="progress"`, `status`,
  `original_size`, `control_size`, `selected_size`, `tool_versions`, `families`,
  `events`, `event_count`, `event_bound`, `cancelled`, `report_path`, and
  `progress_sha256`; it omits terminal-only `final_size`/`report_sha256` and can never
  parse as a terminal report. Both paths are canonical contained relative paths and
  each seal excludes only itself.

  Every schema-3 attempt uses a bounded exact summary with candidate ID/kind/engine,
  status, compiled bytes, cache diagnostic, base candidate, nullable reserved round
  and direct ratio, selected-focus states, changed source identities, reused region
  keys, and nullable composition/compile/structural/schema-2/final-whole hashes.
  Require one focus state per selected target. Report-only state is exactly
  `passed`, `failed`, `cancelled`, or `unattempted`; only passed/failed attempted
  states carry an evidence hash. Recovery rounds are contiguous reserved `0..n-1`;
  adaptive-direct has exactly `[0]`. Changed/reused sets exactly partition selected
  dependencies, compile summaries cover every required artifact, and only an
  authorized attempt may carry a passing final-whole hash or become selected.

  Reports embed hashes and bounded identity/status summaries, never complete focused
  evidence, material inventories, source manifests, raw commands, or absolute runtime
  paths. Candidate/error strings are bounded to 128/4,096 UTF-8 bytes and list
  cardinalities reuse the top-K, changed-source, direct-source, round, artifact, and
  candidate-budget limits.

- [ ] **Step 3: Write hard-bound RED tests**

  Exercise top-K 5, 17 whole states, 3 poses, a fifth changed source, ninth Monaco
  fallback, fifth direct ratio, fourth recovery round, and candidate-budget
  exhaustion. Each must reject before opening/hashing an excess snapshot, allocating
  staging, or starting a process. Add cache source file 4,097, compiled artifact 65,
  aggregate direct snapshot over its existing 4,096-file/2-GiB bound, sparse content
  above any 2-GiB class bound, control/report JSON byte 16 MiB + 1, deep/oversized
  arrays, and event-count exhaustion. Instrument open/hash/copy/process/cache store/
  best update/promotion and require zero excess calls.

  Event history uses the derived hard bound
  `2 + family_count * (2 + (budget.max_candidates + 1) * candidate_event_bound)`,
  where `candidate_event_bound = 2 + 13 + 2 * focused_top_k + 3 * 8`:
  candidate start/finish, thirteen singleton typed stages, at most two per-focus
  render/compare stages for four focuses, and three direct-source stages for eight
  sources. The terminal/progress JSON 16-MiB bound remains authoritative even when
  the derived count is larger. Events and summaries reserve room for terminal
  records; history is never silently truncated into a false-success report.

- [ ] **Step 4: Run integration tests and verify RED**

  Run:
  `python -m unittest tests.maximum_optimizer.test_cache tests.maximum_optimizer.test_focused_cache tests.maximum_optimizer.test_orchestrator tests.maximum_optimizer.test_reporting -v`

  Expected: missing schema/report/bound enforcement.

- [ ] **Step 5: Implement exact cache and report schemas**

  Implement outer CandidateCache schema 2 and maximum cache-record schema 3 exactly
  once. Replace whole-workspace `copytree` and post-promotion `_seal_cache_entry` with
  the whitelist, bounded no-follow staged publication above. Control JSON is read
  through a 16-MiB handle-verified capture before parsing. Enforce existing source
  4,096/2-GiB, compile 64/2-GiB, and direct per-ratio 4,096/2-GiB bounds independently;
  the combined cache maximum is their sum plus at most six 16-MiB control/manifests.
  Lookup is read-only; corrupt/old/mixed entries are misses, and later invalidation
  removes only a verified direct child without following links.

  On resume, dispatch by exact candidate kind. Legacy/ordinary rerun current
  structural, whole, and focused gates as applicable. Focused-recovery and
  adaptive-direct entries first revalidate source/direct snapshots, recipe,
  composition, and complete compile bytes, then rerun structural, **all selected
  top-K focuses**, rebuild schema 2 from the sealed base initial context, and run
  exactly one fresh final whole after focus pass. Stored prior hashes remain
  diagnostics and cannot enter the new authorization payload. Fresh and resume must
  return the same pass/fail and authorization semantics; cache-hit diagnostics alone
  may differ.

  Serialize legacy reports/progress with the existing schema-1 path unchanged.
  Serialize trusted-schema-3 report/progress through separate exact schema-2 typed
  builders, canonical seals, bounded summaries, and safe contained atomic writes.
  Reject a `logs` reparse ancestor and never follow or replace an external target.
  After inventory but before the first family process, compute the derived event bound
  and the minimum terminal-summary capacity. If a valid terminal report cannot fit in
  16 MiB, fail preflight before candidate work; later serializers reserve terminal
  capacity before accepting another diagnostic event.

- [ ] **Step 6: Add cancellation barriers and event stages**

  The fixed stage vocabulary is exactly `generate_compile`, `cache_restore`,
  `cache_revalidate`, `compiled_size`, `structural`, `whole_visual`,
  `focused_select`, `focused_render`, `focused_compare`,
  `focused_evidence_publish`, `recovery_select`, `recovery_compose`,
  `recovery_compile`, `direct_source_prepare`, `direct_source_build`,
  `direct_source_validate`, `final_whole_visual`, and `cache_store`. Every stage event
  has family/candidate identity, candidate kind, ordinal/total, and nullable exact
  `round_index`/`region_key`/`source_identity` according to its stage. Unknown stages,
  fields, order regressions, and count excess are rejected; events are diagnostic and
  never authorize.

  Add cancellation barriers before slot reservation; snapshot open; each bounded
  copy/hash/process; composition/compile; every focus select/render/compare; partial
  and authoritative evidence publication; final whole; cache publication; best
  update; and output replacement. Cancellation before cache promotion removes only
  owned staging. If cancellation becomes visible only after one fully validated
  atomic cache entry has been promoted, keep that entry reusable, record the current
  attempt/focus remainder as cancelled/unattempted, and perform no best update or
  output promotion. Cancellation never creates authoritative schema 2 from report-only
  unattempted states and produces exactly one candidate, family, and run terminal.

- [ ] **Step 7: Run integration gate and commit checkpoint 7**

  Run:
  `python -m unittest tests.maximum_optimizer.test_cache tests.maximum_optimizer.test_focused_cache tests.maximum_optimizer.test_orchestrator tests.maximum_optimizer.test_reporting -v`

  Expected: zero failures; cache/report control files remain within 16 MiB, event
  cardinality is bounded, resume rebuilds every current authorization, and privilege
  skips remain skips.

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
