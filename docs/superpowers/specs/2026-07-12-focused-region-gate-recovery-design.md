# Focused Region Gate and Composite Recovery Design

## Status and execution gate

This design is approved for planning only. The committed LVS calibration evidence v3
is a sealed research input whose own status is `calibration-pending` and whose own
decision is `winner: false`; it cannot directly authorize schema 3. Task 8 may enable
only an explicit backend LVS research profile after a new independently reviewed,
committed approval artifact binds that exact input, the exact proposed profile, all
formerly external artifacts, and real fresh/resume validation. Product defaults,
the WPF surface, packaging, release, and broad production claims remain outside Task
8 and blocked on their later dedicated tasks.

The feature is opt-in only through the explicit existing `--maximum-profile` backend
argument pointing at the exact trusted research profile. The uncalibrated
`maximum-experimental-v1.json` sentinel remains the default. Schema-1 and schema-2
profiles retain their current behavior byte-for-byte: no focused renders, no
composite recovery, and no new candidate schedule.

## Objective

Add a second, isolated visual gate after the existing whole-model structural and
visual gates. The gate deterministically selects the visually riskiest model
regions, renders each selected region alone, and rejects detail loss that a full-car
view can hide. A rejected focus may recover from a proven, less-aggressive donor or
the exact original source without regenerating unrelated model sources.

For families such as the Dodge Monaco, add a typed composite path that starts from a
Blender-adaptive base and applies meshoptimizer direct-position compression only to
visual SMD files that the base preserved exactly. StudioMDL compiled bytes and both
visual gates decide the winner.

## Chosen architecture

The focused gate is a separate validation stage rather than an extension hidden
inside `ProductionAdapters.visual`. The existing whole visual call remains the
first visual authority and writes a sealed index of its state manifests. A new
focused adapter consumes that index, selects targets, performs isolated renders,
and returns a structured focused result.

Recovery is source-artifact composition. It never reruns an optimizer over a whole
candidate merely to raise one region. The system overlays byte-exact SMD outputs
from a proven donor, or the original SMD, on the already-authorized base candidate,
proves that every unrelated source hash is unchanged, and recompiles the complete
QC. This boundary is necessary to rerender only affected focuses honestly.

The alternatives rejected by this design are:

- Putting focus logic inside `ProductionAdapters.visual`. This is smaller but makes
  target selection, recovery provenance, and cache authorization opaque.
- Running the focused gate only after choosing the smallest whole-model winner.
  This is cheaper but can select the wrong search trail and delays recovery until
  the search evidence has already been discarded.

## Profile schema and activation

Schema 3 has the exact top-level fields:

```json
{
  "schema": 3,
  "version": "lvs-focused-v1",
  "calibrated": true,
  "corpus_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "selector": "audited-original-round-family-v1",
  "focused_evidence_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "focused_policy": {
    "schema": 1,
    "selector": "surface-risk-top-k-v1",
    "top_k": 3
  },
  "profiles": {
    "general-body-detail-v1": {
      "limits": {"silhouette_iou": 0.0, "rgb_mae": 0.0, "edge_error": 0.0, "surface_bidirectional_p95": 0.0, "surface_max": 0.0, "normal_angle_p95": 0.0, "uv_error_p95": 0.0, "skinning_error_p95": 0.0},
      "focused_limits": {"silhouette_iou": 0.0, "rgb_mae": 0.0, "edge_error": 0.0, "surface_bidirectional_p95": 0.0, "surface_max": 0.0, "normal_angle_p95": 0.0, "uv_error_p95": 0.0, "skinning_error_p95": 0.0}
    },
    "round-rigid-v1": {
      "limits": {"silhouette_iou": 0.0, "rgb_mae": 0.0, "edge_error": 0.0, "surface_bidirectional_p95": 0.0, "surface_max": 0.0, "normal_angle_p95": 0.0, "uv_error_p95": 0.0, "skinning_error_p95": 0.0},
      "focused_limits": {"silhouette_iou": 0.0, "rgb_mae": 0.0, "edge_error": 0.0, "surface_bidirectional_p95": 0.0, "surface_max": 0.0, "normal_angle_p95": 0.0, "uv_error_p95": 0.0, "skinning_error_p95": 0.0}
    }
  }
}
```

The pending v3 payload is not an approval and its raw distributions are not accepted
as thresholds. The real limits come only from a separately committed
`maximum-focused-lvs-v1.approval.json` whose exact status is
`approved-lvs-research-profile-v1`. Its exact schema binds:

- raw-file SHA-256 and canonical seal of
  `benchmarks/lvs_models/calibration_evidence_v3.json`, including its preserved
  `calibration-pending`/`winner: false` source decision;
- raw-file SHA-256 and canonical SHA-256 of
  `maximum_optimizer/profiles/maximum-focused-lvs-v1.json`;
- canonical contained repository paths, raw-file hashes, and canonical payload hashes
  where applicable for the focused summary, regional compile report, focused
  recovery evidence, Monaco composite evidence, and every other referenced input;
- independent reviewer identity, UTC review time, decision, and immutable review
  record hash;
- exact Python, Blender, Blender Source Tools, StudioMDL, VTFCmd, renderer,
  optimizer, dependency, and native-bridge identities;
- exact corpus, calibration-family, holdout-family, source-tree, original-model, and
  tool input manifests; and
- the real validation runner hash plus fresh/resume report and output-manifest hashes
  for every declared family.

The approval artifact has a canonical `approval_sha256` excluding only itself.
Trust constants distinguish raw-file hashes from canonical seals for the evidence,
profile, and approval artifact. `load_fidelity_profile_set` accepts schema 3 only
when the sibling approval artifact, exact profile bytes, canonical profile payload,
evidence raw bytes/seal, approval raw bytes/seal, and every cross-binding equal the
reviewed constants. Finite fields, valid ranges, a copied evidence seal, a copied
approval seal, or a self-resealed pending profile are insufficient. Any missing,
extra, stale, path-aliased, or mismatched field fails before decompile or output
mutation.

The real backend gate covers these five calibration families:
`pontiac_transam_wheel`, `dodge_charger`, `toyota_supra`,
`nissan_skyline_gtr32`, and `dodge_monaco_police`. The existing LVS corpus also
provides five disjoint holdouts: `ford_fairlane`, `vw_beetle`, `vw_touareg`,
`ferrari_365_fullrig`, and `caterham_620r`. All ten run with real Blender and
StudioMDL first from an empty work/cache and then with resume. Fresh and resume must
produce identical authorization, selected compiled bytes, final contained output
manifest, and terminal report semantics; cache diagnostics alone may differ. A
non-roundtrippable or failing holdout is preserved/rejected without promotion and is
still a valid safety result, but it cannot be reported as an optimized quality pass.

This evidence supports only the claim `LVS-calibrated backend research profile`.
It does not authorize a general addon, non-LVS, product-default, packaged-worker, or
release-quality claim. If any declared artifact is unavailable outside the original
workstation, it must be committed at a canonical bounded repository path or
reproduced byte-for-byte by a committed runner; otherwise Task 8 stops without trust
constant or profile activation changes.

`FidelityProfileSet.profile_for()` remains the whole-model interface.
`FidelityProfileSet.focused_profile_for()` returns the focused profile only in
schema-3 mode. Calling it in legacy or schema-2 mode is an error.

## Whole visual evidence index

Only a successful schema-3 search-candidate whole visual validation writes
`logs/whole-visual-index.json` inside the candidate workspace. Schema-1 and
schema-2 runs do not create this file. The `roundtrip-control` baseline never
writes it and never runs the focused gate; the control remains the existing
structural/whole compatibility denominator. Before a schema-3 candidate whole
render starts, any stale index restored with a compiled-candidate cache payload is
removed without following links. A failed or cancelled whole validation leaves no
authoritative index.

The exact schema-1 index binds:

- schema and selector versions;
- family ID, model-relative path, family input hash, candidate ID, candidate spec,
  and candidate cache digest;
- selected whole and focused profile versions;
- the full canonical family region-manifest relative path and SHA-256;
- ordered contiguous state identity, bodygroup indices, LOD index, and poses;
- each state's canonical filtered region/configuration-manifest relative paths and
  SHA-256 hashes;
- relative original and candidate render-manifest paths plus SHA-256 hashes;
- canonical source-pair identities and hashes;
- animation classification, its relative path and hash, and the paired animation
  source proofs when representative animation is active;
- every geometry row needed for focus ranking;
- renderer/dependency identities needed to prove the current render contract;
- a canonical `evidence_sha256` over the complete index.

The focused gate does not glob render directories. It accepts only an exact,
sealed index whose files are canonical relative regular files contained by the
candidate workspace and whose hashes match current bytes. Absolute, UNC,
drive-qualified, backslash-aliased, dot/parent, case-colliding, symlink,
junction/reparse, special-file, missing, extra-field, reordered-state, and
same-size-mutated inputs fail before target selection. Recomputing the outer seal
cannot compensate for a changed current file, family/candidate identity, profile,
dependency, source pair, manifest, animation proof, or geometry row.

## Deterministic focus selection

The selector groups whole-render geometry rows by region key. It rejects non-finite
metrics, duplicate `(state, region, pose)` rows, missing canonical descriptors, and
rows whose source identity is not present in the paired configuration.

For each region, its anchor is the occurrence with the greatest tuple:

1. `max(surface_bidirectional_p95 / p95_limit, surface_max / max_limit)`;
2. normalized `surface_bidirectional_p95`;
3. normalized `surface_max`;
4. the earliest state index;
5. pose name in canonical lexical order.

When a limit is zero, a measured zero has normalized value zero and a positive
measurement is invalid because the whole gate could not have passed it. Regions are
ranked by the first three values descending, then region key ascending. The selector
returns exactly `min(top_k, eligible_unique_regions)` targets. A successful whole
gate with no eligible region is a focused-gate failure, not a silent pass.

Each target records rank, region key, canonical descriptor, source identity, state,
bodygroups, LOD, anchor pose, raw metrics, normalized metrics, and the hashes of all
selector inputs.

The implemented `select_focus_targets(...) -> tuple[FocusTarget, ...]` contract is
preserved. Task 3 adds a compatible `select_focus_targets_with_evidence(...) ->
FocusSelection` API. `FocusSelection.eligible_ranking` contains every unique region
anchor in canonical risk order, with ranks `0..n-1`; `FocusSelection.selected` is
exactly its first `min(policy.top_k, n)` entries. The existing function delegates to
the same ranking implementation and returns only `.selected`. This makes the full
ranking durable without changing any Task-1 caller.

## Isolated renderer contract

`render_previews.py --focus-region REGION_KEY` uses the existing eight camera
directions and `textured,clay` passes. It imports only the selected region's source
identity for the selected state. The full canonical manifest for that source is
used to resolve object assignments before non-selected mesh objects are hidden from
rendering and excluded from geometry capture.

Camera fit, bounding box, lights, geometry snapshots, and material application use
only the focused object. Unknown, duplicated, zero-triangle, or ambiguously assigned
regions fail closed. The output manifest contains exactly one region and preserves
the existing source-pair, material-resolution, animation, triangle-audit, and image
hash contracts.

For each target the expected image cardinality per side is:

`8 angles * 2 passes * pose_count`, where `pose_count` is one for bind-only and at
most two when the existing representative animation contract succeeds. Both
original and candidate sides must have the same exact matrix. No additional image,
pose, pass, angle, or region is accepted.

## Focused validation and aggregation

Each focused pair is compared with the selected class's schema-3 focused profile by
the existing `compare_render_sets`. Every selected focus is a hard gate. The
candidate passes only when structural, whole visual, and all focused validations
pass.

The aggregate result preserves the whole result and adds per-region results. Its
worst scope is `REGION_KEY/POSE`, allowing deterministic recovery. A focused
metric never replaces or weakens a worse whole-model metric in reporting.

## Orchestrator authorization contract

The real Models bridge validates schema-1, schema-2, and schema-3 profile sets with
`load_fidelity_profile_set`; it does not call the schema-1-only `load_profile` and
does not add or change any CLI field. Task 8 uses only the existing explicit
`--maximum-profile` argument; it does not change the default profile. Focus
activation is determined only by `FidelityProfileSet.focused_policy is not None`,
because schema-2 and schema-3 share the typed family selector mode.

For each schema-3 search candidate the authorization order is fixed:

1. build or privately restore compiled candidate bytes;
2. recompute structural validation;
3. recompute the existing whole visual validation and write a fresh authoritative
   whole index only on pass;
4. validate the index, select focuses, and process them in selector-rank order via
   the Task-3 `validate_focused_target` helper;
5. atomically rewrite diagnostic-only
   `logs/focused-region-gate.partial.json` after each terminal target; this progress
   schema has no authorization hash and is rejected by the Task-3 evidence parser;
6. after every selected target has exactly one terminal record, build and atomically
   publish authoritative schema-1 `logs/focused-region-gate.json` with
   `focused_gate_evidence_payload(..., recoveries=())`;
7. build the hard aggregate and only then publish a compiled-candidate cache entry
   on a cache miss.

Structural or whole failure performs no focus selection, focused-render-cache
restore, focus render, or focused evidence authorization. Cached structural, whole,
or focused diagnostics never authorize a candidate: a compiled-cache hit reruns every hard
gate, and a focused-render-cache hit is compared exactly once from its private
Task-3 snapshot. One failed focus rejects the candidate but does not omit later
selected focuses from the terminal diagnostic evidence unless cancellation stops
the run. A missing, duplicate, extra, mismatched, unsealed, or invalid focused
record is an explicit focused-gate failure, never an empty aggregate pass.

`CandidateEvaluation.whole_visual` retains the fresh whole result.
`CandidateEvaluation.focused_by_region` is an immutable exact mapping for the
selected targets. `CandidateEvaluation.visual` remains the search/report authority
and is the hard aggregate of whole plus every focused result. Whole metrics,
failures, and worst scope remain represented; focused failures use
`REGION_KEY/POSE`, and focused metrics cannot erase or make a worse whole result
appear better. Existing five-positional-argument construction and schema-1/2
behavior retain their current defaults.

Cancellation is checked before focused selection, between targets, during Task-3
cache lookup/private restore, before and during render, after comparison, before
each atomic evidence publication, after the focused adapter returns, and before
compiled-candidate cache storage. Cancellation emits one candidate terminal event,
does not emit `best_updated`, does not store/promote the candidate, and preserves
the original family through the existing cancellation path. Already published
partial progress remains complete and atomic but diagnostic only. The authoritative
file is absent until complete terminal cardinality is proven, so no partial file can
authorize an unattempted target.

## Recovery donors and exact fallback

Recovery operates on canonical QC-graph source identities because exact preservation
is a source-file operation. It never reconstructs a path from a candidate ID or a
filename token. Every ordinary candidate that completes source generation publishes
a bounded, sealed `SourceTreeManifest` and a runtime `RecoverySourceSnapshot`. The
snapshot binds family/input identity, optimizer contract, canonical whole and focused
profile proofs,
dependency proof, candidate/cache identity, the current contained source root, the
complete source-tree manifest, and the focused evidence hashes available for that
candidate. The source root is runtime-only; canonical recipe/evidence payloads seal
its logical manifest and snapshot hash, never an absolute path.

`SourceTreeManifest` inventories every regular file under the optimized source root
that can participate in the complete QC graph, plus required auxiliary files. Each
entry has one canonical logical file identity, typed kind (`qc`, `visual-source`,
`animation-source`, `physics-source`, or `auxiliary`), canonical contained relative
path, size, and SHA-256. File identities and case-folded paths are unique and sorted;
declared totals and the manifest seal are exact.

The orchestrator retains a bounded `candidate_id -> (CandidateBuild,
CandidateEvaluation, RecoverySourceSnapshot)` registry for every completed ordinary
candidate in the current family, including candidates rejected only by focused
validation. The registry contains at most `SearchBudget.max_candidates` candidate
entries plus the one original snapshot and is discarded at the family terminal.
A cache hit is eligible only after its source-tree manifest is reopened
and revalidated against current no-follow bytes. Structural failure, initial whole
failure, missing focused proof, stale source bytes, or a mismatched family, profile,
dependency, input, or optimizer contract makes a candidate ineligible as a donor.
The original graph has its own sealed snapshot built from the authoritative original
QC graph and per-source hashes; it is not inferred from the base candidate tree.
Candidate snapshots require candidate/cache identity and may carry canonical focused
refs. The original snapshot forbids candidate/cache identity and focused refs. Both
forms require the same family/input, optimizer/whole-profile/focused-profile/dependency
binding and current
manifest validation.

A failed focus is mapped through its canonical descriptor to one source. A changed
source affects every selected focus whose sealed Task-3 `source_pairs`, configuration,
or animation dependency closure names that source. That complete affected set is
rerendered. Equality with only `FocusTarget.source_identity` is never sufficient for
reuse. A focus may be reused only when every source pair and configuration/animation,
profile, selector, renderer, dependency, and material proof in its prior cache context
is byte-identical in the composed candidate.

The recovery order is:

1. A byte-exact output from an already built ordinary candidate with the same family,
   input, optimizer, profile, and dependency contract, a strictly less-aggressive
   effective ratio, and a passing focused result for the motivating failed region.
   The donor may fail another focused region globally, but its structural and initial
   whole gates must have passed. Recovery candidates are not donors in Task 5.
2. The exact original source bytes when no eligible donor remains.

For each source, candidate snapshots are ordered canonically by effective ratio
ascending and then candidate ID and snapshot hash. Only ratios strictly above the
current effective ratio are eligible; the first eligible entry wins. At most eight
ordered candidate snapshots are inspected per changed source, and the ninth snapshot
is not opened or hashed. Invalid entries encountered in that ordered prefix consume
the bound. A donor cannot donate to itself, repeat the current replacement bytes,
reduce or preserve the effective ratio, or cross any contract boundary.

`optimizer_contract_sha256` is the canonical hash of exactly `engine`,
`target_error`, `repair_profile`, `strategy`, `update_vertices`, and `transfer`.
Candidate ID, target ratio, region overrides, and composite recipe are excluded so
less-aggressive ordinary donors remain comparable; family/input, profile, dependency,
and tool bytes are bound separately and must still match. Effective ratio is computed
for the motivating region from its exact override or the candidate target ratio.

Each `SourceOverlay` records `base_source_sha256`, `replacement_sha256`, the sealed
replacement snapshot, and a canonical tuple of every `(region_key,
focused_evidence_sha256)` used to justify donor selection. Donor/original recovery
also names the canonical motivating region; direct-position has none. The mode matrix
is exact:

- `donor` requires donor candidate/cache identity, a finite strictly larger effective
  ratio for the motivating region, a donor snapshot hash, non-empty focused evidence
  hashes containing that region, and no exact reason;
- `exact-original` forbids donor identity, ratio, and focused evidence, requires the
  motivating region, authoritative original snapshot hash, and reason
  `donors-exhausted-v1`;
- `direct-position` is reserved for Task 6 and requires its direct candidate/cache
  identity, no motivating region, the global direct ratio, its source snapshot, no
  focused donor hashes, and reason `approved-direct-position-v1`.

If exact-original bytes already equal the base/replacement bytes, recovery is a no-op:
it emits no overlay, consumes no composition candidate, and the source is exhausted.

Every recovery round starts from the same immutable ordinary base candidate. Its
`CompositeRecipe` carries the entire cumulative overlay set, sorted by canonical
source identity; a later round may replace one prior overlay for a source but never
stack two overlays for it. It also seals family/input, base spec/cache/source manifest,
optimizer/whole-profile/focused-profile/dependency contract, selector version, round
index, and all donor
or original proofs. The complete recipe is part of `CandidateSpec.cache_payload`, the
candidate ID hash, and `CacheKey`; hashing only the candidate ID is insufficient.

The compositor receives the typed recipe plus the exact snapshot registry, copies the
ordinary base into a fresh non-overlapping private workspace, replaces only declared
source outputs through no-follow handles, rewrites no QC directive, and emits a
bounded source manifest proving:

- every base source and resulting source has a canonical identity and hash;
- a changed source has exactly one declared overlay;
- every undeclared source hash is identical to the base;
- every donor/exact hash and size equals the bytes copied from the sealed snapshot;
- the complete optimized QC graph still pairs with the original graph.

Canonical relative paths reject absolute, UNC, drive, backslash aliases, dot/parent,
case collisions, symlinks, junctions/reparse points, and special files. Discovery,
copy, hashing, manifest comparison, and cleanup have explicit file/byte bounds and
cancellation checkpoints. A source changed during a handle read, an undeclared
change, an extra/missing file, a QC rewrite, ambiguous graph pairing, or a stale
snapshot rejects only that composition and never publishes a complete tree.

The complete QC is recompiled after every composition and its exact contained
artifact manifest is sealed.
Structural evidence binds the composition hash, compile-manifest hash, current
fingerprint proof, and validation. Focused evidence schema 2 folds the initial records
with each round in order: rerun records replace prior records for the exact affected
dependency closure, while reused records name the immediately prior evidence hash.
The folded terminal set must contain exactly one current passing record per selected
target.

Intermediate rounds have `final_whole=None`. Exactly the last authorized round has a
non-null `FinalWholeAuthorizationEvidence`, created by one fresh post-composition
whole render. It binds the composite candidate/cache/recipe/composition and compile
digests, the fresh whole-index/render-manifest proof, current validation, and its own
seal. A bare `ValidationResult` cannot authorize structural or final whole state.
Exactly one terminal final-whole authorization is permitted across schema 2.

Budget/round availability is checked before donor inspection. Overlay proposal uses
the retained sealed manifests; an empty/no-op proposal stops without consuming a
slot. Once a non-empty proposal exists, both slots are reserved before the selected
snapshot is reopened, before any filesystem mutation, and before any process launch.
Every attempted
composition consumes both one recovery round and one `SearchBudget.max_candidates`
slot, including composition, compile, structural, focused, or final-whole failure.
Every attempted overlay/recipe hash is retained for the base candidate. A later
round skips it and advances the same failed source, in canonical source order, to the
next donor or exact-original fallback; an identical recipe is never retried.
The fourth round and an exhausted candidate budget reject before opening a snapshot or
starting a process. Failed stages remain typed, sealed round records with later-stage
fields absent according to the status matrix; they are diagnostic and cannot alter the
folded pass. Cancellation writes only atomic partial diagnostics and no authoritative
schema 2.

When trusted schema 3 is active, the byte-exact recovery coordinator exclusively
owns recovery scheduling. The legacy `search._regional_recovery` ratio/override path
is not called and cannot preempt or duplicate a composite round. Schema-1/schema-2
and non-focused search behavior remain unchanged.

## Monaco adaptive-direct composite

The Monaco path is typed, not filename-based and not environment-gated. It activates
only inside the research Maximum runner under trusted schema 3 after at least one
completed ordinary `blender-adaptive-v1` evaluation has passed structural, initial
whole, and every selected focused gate. Among those eligible ordinary evaluations,
the immutable base is the one with the smallest actual compiled byte count, then
candidate ID. Strategy, candidate metrics, source provenance, source snapshot, and
current bytes prove eligibility; model/family filenames, path tokens, environment
variables, and CLI switches never do. Task 6 does not enable the default product path:
production selection remains blocked until the later evidence/approval task changes
that gate explicitly.

The base publishes a sealed exact `AdaptiveCandidateMetricsProof` tied to its
candidate/cache identity, explicit base strategy identity, both QC graph digests, and
`RecoverySourceSnapshot` source-manifest/snapshot digests. Its `sources` field is an
exact-key discriminated union in canonical source-identity order:

- `eligible-exact-v1` requires `preserved_exact == true`, byte-identical original/base
  SMDs, and exactly one eligibility reason: `ratio-preserved-exact-v1` or
  `approved-exact-source-fallback-v1`;
- `ineligible-changed-v1` requires `preserved_exact == false`, no eligibility reason,
  and a fixed machine ineligibility reason. Free-form diagnostics are excluded from
  authorization seals.

The proof inventories the complete union of visual SMD identities reparsed from the
original and base QC graphs, not merely the eligible subset, and includes canonical
grouped occurrences. Missing/unpaired identities, animation, physics/collision, DMX,
stale outputs, conflicting repeated occurrences, duplicate/case-colliding identities,
or unsealed metrics reject the proposal. Only after that in-memory inventory is
complete is the eligible set counted. Zero eligible sources returns no proposal; more
than eight rejects the complete proposal. Both happen before candidate reservation,
source/direct-cache open, copy/hash, or process launch. The set is never truncated.

During that same complete in-memory preflight, before ratio reservation, Task 6 builds
and seals one exact `AdaptiveDirectCoverageManifest` from the bounded current QC/state/
source inventory already opened and retained for ordinary-base selection. It binds
family/input and base candidate/spec/cache/source-manifest/source-snapshot identities,
the complete visual source identity sequence, and one canonical source proof per
identity. Each source proof inventories every occurrence, state, bodygroup, LOD, skin,
material, skeleton, pose dependency, and connected component. Every QC occurrence has
exactly one `covered-by-source-union-v1` witness binding current source bytes plus its
component/material/skeleton/pose contract. All witnesses for one source must resolve to
exactly one equivalence-class digest. Each source proof also repeats its exact
eligible/ineligible metrics variant; image/witness candidate totals cover the complete
eligible subset. Missing occurrences or a dependency difference
that splits the class rejects adaptive-direct entirely; state variation is never
silently ignored. Occurrence 4,097, component/material 257, state 17, or pose 3 rejects
before budget reservation or any new direct I/O/process. The preflight reads no path
beyond the retained base-selection inventory.

Task 6 introduces a bounded `DirectSourceBuildRequest` and `DirectSourceSnapshot`; an
isolated mini-source is not misrepresented as a complete `RecoverySourceSnapshot`.
The request seals family/input, immutable base candidate/spec/cache/source
manifest/snapshot, optimizer/profile/dependency contracts, explicit base strategy
`blender-adaptive-v1` with its complete cache payload, canonical source identity and
current input proof, one global ratio, explicit direct strategy/transfer
`meshopt-direct-position-v1`/`direct-position-v1`, prefilter
`direct-degenerate-prefilter-v1`, the exact expected prefilter proof recomputed from
the request input bytes, the exact coverage-manifest digest, and its own digest. The snapshot carries the request, derived
direct candidate/cache identity, one contained regular `.smd` output proof, input and
output triangle counts, the exact recomputed prefilter proof, fixed reason
`approved-direct-position-v1`, and a seal over every canonical field except its
runtime absolute root. Per-ratio request/snapshot set digests bind every sorted source;
composite identity binds both strategies, immutable base, coverage manifest, ratio,
recipe, and both set digests. The coverage digest is repeated exactly in each request,
the request-set digest, recipe, candidate spec/cache payload, cache record, and
adaptive evidence; mismatch or omission fails closed. Across one ratio the at-most eight one-output snapshots remain within the
4,096-file/2-GiB direct bound.

The prefilter proof is independent authorization data, not a self-signed log and not
part of Blender eligibility. The builder recomputes
`direct-degenerate-prefilter-v1` from exact no-follow input bytes and requires exact
canonical equality with schema, threshold, ordered dropped-triangle records, counts,
fraction, and digest. The threshold is exactly `1e-30`; dropped count cannot exceed
source triangle count, dropped fraction is the exact finite quotient, and the global
simplification ratio applies to the post-prefilter triangle set. Prefilter-only removal
cannot qualify as successful direct simplification or a separate saving claim. A
usable snapshot requires `applied == true`, `fallback_reason is None`,
`preserved_exact == false`, changed output bytes, a strict post-prefilter triangle
decrease, exact strategy/transfer, and current output bytes matching the seal. One
mini-source failure terminally fails that ratio without a partial recipe; later fixed
ratios may run only when already reserved and not cancelled.

`monaco_composite_specs` creates exactly the four terminal global ratios
`(0.50, 0.45, 0.40, 0.35)`. Every selected source in one variant receives the same
ratio; canonical order creates no Cartesian product. Adaptive-direct trails never use
bracket refinement, midpoint generation, donor recovery, or legacy regional recovery.
After complete zero/greater-than-eight preflight, the prefix allowed by the remaining
`SearchBudget.max_candidates` is reserved contiguously in that ratio order, including
report/event terminal capacity. No source snapshot or direct cache opens and no copy,
hash, or process begins before its ratio reservation. Task 6 has no pre-build hit; a
concurrent same-key incumbent can be adopted only after this attempt was built and
therefore consumes its reservation. Every reservation receives one terminal result.

Each ratio is an independent `adaptive-direct-fallback-v1` composite, not a donor
continuation. It uses a discriminated schema-2 `AdaptiveDirectEvidence` whose exact
kind is `adaptive-direct-fallback-v1` and whose only record has `round_index == 0`.
Focused-recovery evidence cannot parse as this type; direct keys are forbidden in
donor matrices and donor keys in direct matrices. These records do not consume the
donor maximum of three and never accumulate across ratios. Direct overlays resolve
only through `DirectSourceSnapshot`, use the fixed mode/reason/ratio/identity proofs,
and cover all eligible sources. The 1..8 selected identities are the exact changed
partition for adaptive-direct; focused recovery retains 1..4. `CompositeRecipe` and
`CompositionProof` discriminate kind so neither limit can be borrowed.

For every selected SMD, original and ordinary-base current bytes must have equal size,
SHA-256, and byte-for-byte content. The direct transform may change only triangle
membership/order and position-remapped topology. Nodes, skeleton frames, material
spelling/order, bone identities, weights, UVs, normals, retained-corner attributes,
and cyclic winding provenance remain exact under the typed SMD parser. Composition
copies the immutable base, replaces exactly the declared SMDs, proves every QC and
undeclared source byte unchanged, and rejects same-size mutation.

Every composed ratio runs structural authorization and two mandatory fresh focused
sets without reuse: every selected base top-K focus, plus exactly one state-independent
`AdaptiveDirectSourceUnionRecord` for every changed source identity. Its
`AdaptiveDirectSourceUnionTarget` is derived only from the sealed preflight source
proof, not from a bodygroup/LOD/state `FocusTarget`. The renderer loads the entire SMD
union in source-local space, so state visibility never selects or drops geometry;
typed equivalence witnesses cover repeated QC occurrences without duplicate renders.
Pose keys are exactly bind plus an optional single canonical anchor. Each source record
therefore contains exactly `2 sides * pose_count(1..2) * 8 cameras * 2 passes`, i.e.
32 or 64 current image proofs, with no additional record. A sealed visibility matrix
requires every canonical component to contribute nonzero isolated mask pixels in both
reference and candidate in at least one fixed camera for every pose. Files are exactly
the canonical Cartesian product
`source-union/<union-key>/<side>/<pose>/<pass>/<camera>.png`. Visibility contains one
witness per component/pose and chooses the lexicographically first camera with
nonzero pixels in both sides, making record↔pose↔camera cardinality deterministic. The record is required even
when the source overlaps a base target. If any disconnected, enclosed, or occluded
component cannot be represented and visibly proved within this fixed matrix, the
candidate fails closed; there is no object-only, partial, or unbounded per-component
fallback. The exact union key is
`source-union-<first-32-hex(sha256(canonical source-coverage proof))>`; the source
proof seals sorted occurrence/component/material/state-dependency/pose keys and
profile/dependency bindings. Shuffled graph or object discovery therefore produces the
same key. `AdaptiveDirectEvidence` seals both exact matrices, the complete coverage
manifest, visibility proofs, and current render-file manifests over the immutable base
schema-1 context. The base prefix and canonical direct-source set are immutable; fresh
ranking cannot remove either. Only after every record passes does exactly one fresh
final whole render run.
The final-whole evidence binds the composite recipe/composition, complete compile
manifest, current candidate/cache identity, and fresh whole index. Only terminal
`authorized` schema 2 can enter candidate cache, best update, winner selection, or
output promotion. Earlier composition/compile/structural/focused failure carries the
Task-5 typed terminal status and no final-whole authorization.

The Blender base remains eligible. Among candidates that pass every gate and have a
strictly positive compiled saving, the winner is the minimum actual StudioMDL bytes,
then the stronger fidelity score, then candidate ID. A composite never wins merely
because its intermediate SMD files are smaller.

### Task-6 cache checkpoint

Task 6 uses the validated private candidate transaction already present at commit
`a4e9ad9`; it does not implement Task 7 early. After a ratio has been fully built, its
private workspace is copied into private candidate-cache staging. Staging integrity is
sealed first, current source/compile/whole/base-focus/source-union bytes are then
semantically reauthorized, integrity is verified, `complete.json` is written last, and
one durable atomic rename publishes the entry. The runner reopens `final/payload`,
reauthorizes it again, retains that final payload as the selected immutable build, and
promotes output only from its authorized compile proofs. A concurrent same-key winner
is retained only after the same semantic validation. Stale legacy/corrupt entries are
replaced transactionally; cancellation and control-flow exceptions preserve the
incumbent.

Task 6 performs no pre-build cache lookup and offers no cross-run recovery resume. An
adopted concurrent incumbent is therefore not a free attempt: the ratio was already
reserved and built. The direct workspace uses the future-compatible
`direct/<ratio-token>/<source-ordinal>-<identity-digest>/output.smd` shape, but Task 7
alone introduces the exact whitelist, outer/record schemas, report schema, pre-build
lookup, and private resume restore described below. Task-7 restore copies whitelisted
bytes into a new private root and reruns current structural, base top-K, every
source-union proof, and final whole exactly once.

## Task-7 cache design and resume contract

The remainder of this section is implemented by Task 7, not by the Task-6 checkpoint
transaction above.

The candidate cache uses outer schema 2 and maximum cache-record schema 3. An entry
root has exactly `payload/`, `payload-manifest.json`, `metadata.json`, and
marker-last `complete.json`. Publication never copies a whole candidate workspace and
never seals after promotion. It whitelists typed source, compiled, composition, and
direct-snapshot artifacts into same-volume staging through bounded no-follow handles;
writes and fsyncs the exact payload manifest, record, and metadata; writes and fsyncs
the completion marker last; reparses the complete staging entry against current copied
bytes; then performs one atomic `os.replace`. A crash before that replacement exposes
no hit. A valid concurrent same-key winner is retained only after the same full
schema-2/3 validation.

Outer metadata contains exactly `schema`, `key_digest`, `record_schema`,
`candidate_kind`, `payload_manifest_sha256`, `payload_file_count`, and
`payload_total_bytes`. The payload manifest contains exactly schema, a canonical
sorted sequence of `{path,size,sha256,kind}`, exact totals, and its digest.
`complete.json` contains exactly `schema`, `key_digest`, metadata/record/payload
manifest hashes, payload file/byte totals, and `entry_sha256`; the entry seal excludes
only itself. Each control or manifest JSON is at most 16 MiB and is captured through a
handle-verified bounded read before parsing.

Payload paths are an exact whitelist: `maximum_cache_record.json`, typed files below
`manifests/`, complete bounded `src/` and `compiled/` trees, and—only for
adaptive-direct—typed bounded
`direct/<ratio-token>/<source-ordinal>-<identity-digest>/output.smd` snapshots plus
their request/snapshot manifests. Every fresh build or cache restore first copies this
whitelist through no-follow handles into a new same-volume, non-overlapping private
candidate root. A `DirectSourceSnapshot` is rooted only in that private copy, never in
shared cache storage or another ratio workspace. Logs, renders, focused
snapshots, texture caches, temporary/quarantine files, extra images, absolute/UNC/
drive/backslash/dot/parent aliases, case collisions, symlink/junction/reparse leaves
or ancestors, special files, and unmanifested content are forbidden before copy.
Source content retains the 4,096-file/2-GiB bound, compiled content the
64-artifact/2-GiB bound, and direct content the per-ratio 4,096-file/2-GiB bound. The
combined entry cannot exceed those three class bounds plus six 16-MiB control files.

Record schema 3 has exact common keys `schema`, `key_digest`, `candidate_kind`,
`family_id`, `model_rel`, `family_input_sha256`, `candidate_spec`,
`dependency_proof_sha256`, `compiled_models_dir`, `optimized_qc`, `compile_record`,
`provenance`, `source_manifest_sha256`, `source_snapshot_sha256`,
`compile_manifest_sha256`, `kind_proofs`, and `prior_diagnostics`. Its one exact
candidate kind and corresponding nested matrices are:

| candidate kind | `kind_proofs` exact keys | `prior_diagnostics` exact keys |
| --- | --- | --- |
| `legacy-ordinary-v1` | `schema`, `kind` | `schema`, `whole_visual_sha256` |
| `schema3-ordinary-v1` | `schema`, `kind`, `source_manifest_sha256`, `source_snapshot_sha256` | `schema`, `whole_index_sha256`, `focused_authorization_sha256` |
| `focused-recovery-v1` | `schema`, `kind`, `source_manifest_sha256`, `source_snapshot_sha256`, `recipe_sha256`, `composition_evidence_sha256`, `compile_manifest_sha256` | `schema`, `initial_focused_authorization_sha256`, `recovery_schema2_evidence_sha256`, `final_whole_evidence_sha256` |
| `adaptive-direct-fallback-v1` | `schema`, `kind`, `source_manifest_sha256`, `source_snapshot_sha256`, `coverage_manifest_sha256`, `recipe_sha256`, `composition_evidence_sha256`, `compile_manifest_sha256`, `direct_request_set_sha256`, `direct_snapshot_set_sha256` | `schema`, `initial_focused_authorization_sha256`, `recovery_schema2_evidence_sha256`, `final_whole_evidence_sha256` |

All common keys are present for every kind. Only legacy ordinary sets the two common
source hashes to JSON null; all kinds require `compile_manifest_sha256`. Wherever a
schema-3 common source or compile hash is duplicated in `kind_proofs`, the two values
are exactly equal.
Composite proof keys are forbidden in ordinary matrices, donor keys are forbidden in
adaptive-direct matrices, and direct keys are forbidden in focused-recovery matrices.
Missing, extra, wrong-nullability, cross-copied, ordinary/composite mixed, and donor/
direct mixed fields make the entry a miss.

Prior whole/focused/schema-2/final evidence hashes live only in the record's bounded
`prior_diagnostics` block. Cached `ValidationResult`, `passed`, terminal status, or
old authorization payloads never authorize. Cache restoration validates current
contained source/direct snapshots, explicit strategy identities, complete adaptive
inventory, recipe, composition, and complete compiled bytes
before a build enters any retained registry. Legacy/ordinary hits rerun their current
structural/whole/focused gates. A focused-recovery hit reruns structural validation and
its selected top-K. An adaptive-direct hit reruns structural validation, the complete
base top-K, and one complete source-union proof for every changed direct source. Each rebuilds its
correct discriminated schema 2 from the sealed ordinary-base context and then runs
exactly one fresh final whole gate after every required focus passes. Fresh and resume have identical authorization semantics;
only cache-hit diagnostics may differ. Old schema, stale proof, same-size mutation,
or corrupt content is a read-only miss followed only by safe direct-child no-follow
invalidation.

An adaptive-direct cache entry additionally binds both explicit strategy identities,
the complete `AdaptiveDirectCoverageManifest` and its digest, every canonical
`DirectSourceBuildRequest`, `DirectSourceSnapshot`, recomputed prefilter proof, and
the exact independent ratio recipe, request/snapshot set digests, and discriminated
round-0 evidence shape. Direct snapshots never enter the donor registry
and cannot authorize donor/exact-original overlays. Restore reopens the one contained
`.smd` output no-follow, revalidates input and output bytes plus the base snapshot,
then reruns structural authorization, every base focus, every changed-source isolated
focus, and the one final whole
gate. A stale mini-source snapshot is a miss for that ratio, not permission to reuse
schema-1 diagnostics.

A separate focused-render cache may reuse expensive Blender image generation. It
stores render bytes only and never stores a `ValidationResult`, `passed` flag, or
other authorization decision. `FocusCacheKey.build` accepts only the canonical
payload produced by a typed `FocusCacheContext`. Its exact schema-1 fields are:

- `schema`, fixed to integer `1`;
- `family_input_sha256` and `candidate_cache_digest`;
- canonical sorted `source_pairs`, each with source identity and original/candidate
  SMD SHA-256;
- exact canonical region descriptor and `FocusTarget` payload;
- a state payload containing state index/name, canonically sorted bodygroups, LOD,
  ordered poses, selected pose/frame, and animation source SHA-256 or explicit
  `none` state;
- region- and configuration-manifest SHA-256 values;
- canonical whole and focused profile proofs, each containing version, corpus hash,
  profile-file SHA-256, and all finite limits;
- trusted evidence-v3 SHA-256, selector version, renderer version, and
  dependency-proof SHA-256;
- the complete canonical cacheable schema-1 material-resolution proof, including its
  verified digest;
- an exact expected matrix: one region, ordered poses, the mandatory two passes and
  eight angles, positive image width/height, and equal derived reference/candidate
  cardinalities.

The builder rejects missing or extra fields, booleans used as integers, non-finite
numbers, non-canonical ordering, unsupported values, and non-JSON types before
hashing with `canonical_json(..., allow_nan=False)`. A non-cacheable material proof
cannot be used to build a key.

`material_resolution_proof(roots, requests, cancel_event)` returns a typed
`MaterialResolutionProof`. A cacheable proof has exact fields `schema`, `cacheable`,
`reason`, `roots`, `requests`, `files`, `resolutions`, `total_files`, `total_bytes`,
and `digest`. Root and request order is preserved because it controls Source lookup;
paths within roots are canonical relative POSIX paths. The sorted file inventory
contains every regular VMT/VTF that can affect the requests, including files in
higher-priority roots that prove a selected result was not shadowed. Each resolution
records request identity, selected root/search indices, relative VMT/VTF paths and
hashes, shader/directive choice, and duplicate-directive audit. Exactly 4,096 files
and exactly 2 GiB are cacheable. Discovery stops before hashing file 4,097 or bytes
above 2 GiB and returns `cacheable=False` with one of the fixed reasons
`file-limit`, `byte-limit`, `unsafe-tree`, or `io-error`. A non-cacheable proof
disables this optimization only; it neither passes nor fails visual validation.
Cancellation is checked before traversal, between directory entries, and between
hash chunks and propagates `ProcessCancelledError` rather than becoming a
non-cacheable result.

The nested material-proof schemas are exact. A root has only `root_index`,
`root_identity`, and `inventory_sha256`. A request has only `request_index`,
`material_identity`, and ordered `search_paths`. A file has only `root_index`,
`path`, `kind` (`vmt` or `vtf`), `size`, and `sha256`. A resolution has only
`request_index`, `material_identity`, `state` (`resolved` or `missing`), nullable
selected `root_index`/`search_path_index`, nullable VMT/VTF relative paths and
hashes, nullable `vtf_root_index`, shader, texture directive, and the canonical
duplicate-root-directive audit. Cacheable proofs use reason `ok`, have canonical
unique root/request/file identities, and their counts equal the inventory. A
non-cacheable proof preserves roots and requests but has empty files/resolutions,
uses null root inventory hashes and exactly one fixed failure reason, and cannot
enter a cache context.

The entry layout is exactly `complete.json`, `metadata.json`,
`payload/reference/...`, and `payload/candidate/...`. Metadata contains the complete
canonical key context, target, material proof, and expected matrix. The completion
marker contains only `schema`, `key_digest`, `metadata_sha256`,
`expected_file_count`, and a sorted exact `{path,size,sha256}` payload manifest.
The marker is written last and is not part of its own manifest. No absolute, UNC,
drive-qualified, backslash-aliased, empty, dot, or parent path is ever deserialized.
Each expected render file proof has only `side`, `kind`, `path`, `size`, `sha256`,
`width`, and `height`. Manifests use null dimensions; images use positive dimensions
equal to the expected matrix. The exact payload set is two manifests plus the
derived image cardinality for both sides; unreferenced files are forbidden.

Publication uses a same-volume direct-child staging directory, cancellable no-follow
copy, post-copy comparison with the expected render manifest, file and directory
`fsync`, and staging/quarantine `os.replace` with rollback. It rejects symlinks,
Windows junctions/reparse points, special files, reparse ancestors, case-colliding
paths, and files whose identity/size/hash changes while read. A prepared writer
rechecks an existing same-key entry before promotion; a valid concurrent winner is
kept and the staging tree is discarded. Cancellation before promotion removes only
the owned staging tree; cancellation or failure during replacement restores the old
entry before propagating. Cleanup and invalidation operate only on verified direct
children and never follow links.

Lookup is read-only with respect to the shared cache. It recomputes the key from the
stored canonical context, validates the marker, then no-follow copies a hit into a
fresh caller-owned snapshot and revalidates the exact manifest after copying. A
source identity/size/hash change during either read makes the operation a miss and
the incomplete snapshot is removed. The comparator receives only this private
snapshot, never the mutable shared entry; this closes the validate-then-swap window.
Missing, extra, corrupt, duplicate, stale-material,
non-contained, wrong-cardinality, wrong-dimension, or unsafe entries are misses and
cannot authorize anything. `validate_focused_target(...)` is the sole Task-3 helper
that converts cached or fresh renders into a `FocusRegionResult`. It obtains a
validated cache hit or invokes `render_fresh`, then calls the injected comparator
(default `compare_render_sets`) exactly once on the selected directories. A cache
hit therefore always recomputes current metrics; any legacy/corrupt cached
`passed=True` field is rejected or ignored. Cache unavailability falls back to the
fresh directories and never changes the comparison result.

## Durable evidence

Each schema-3 candidate that reaches complete focused terminal cardinality writes
`logs/focused-region-gate.json` atomically. The no-recovery Task-3 schema 1 contains:

- policy, profile, evidence-v3, dependency, and material proof hashes;
- the complete ordered eligible ranking and the exact selected prefix;
- every focused target and expected image cardinality;
- original/candidate manifest and image hashes;
- per-focus validation results;
- cache hit/miss status that never changes authorization semantics;
- `recoveries`, which is exactly an empty list in schema 1;
- a canonical evidence hash.

During Task-4 orchestration, completed targets may also be journaled atomically in
`logs/focused-region-gate.partial.json`. That file is a diagnostic progress envelope,
not Task-3 schema 1: it has no `authorization_sha256`, is rejected by authorization
parsers, and is removed only after the complete authoritative file is published.
Cancellation may leave the partial journal for diagnosis but never an incomplete
authoritative file.

Its exact top-level keys are `schema`, `family_id`, `candidate_id`, `context`,
`selection`, `records`, `recoveries`, `authorization_sha256`, and
`evidence_sha256`. `authorization_sha256` seals policy, ranking/selection, trusted
proofs, targets, manifests/images, and current validations but excludes diagnostic
`cache_hit`. `evidence_sha256` seals every field except itself, including cache
diagnostics. Thus a diagnostic change is auditable while being incapable of changing
authorization.

The builder receives a typed `FocusedEvidenceContext`, a `FocusSelection`, and one
typed `FocusedRenderEvidence` per selected target. Context supplies family/candidate
identity, policy, whole/focused profile proofs, the trusted evidence-v3 seal,
dependency proof, and per-target material proofs. Selection supplies the complete
eligible ranking and selected prefix. Each render record supplies its exact target,
terminal status, expected matrix, relative manifest/image proofs, current
`ValidationResult`, per-record evidence hash, and diagnostic `cache_hit` value.

Schema 1 requires `recoveries` to be exactly an empty list. It rejects missing or
extra targets, non-contiguous ranks, target/result mismatches, duplicate region keys,
more than one or fewer than one terminal record per target, incoherent/non-finite
validation payloads, untrusted seals, and non-canonical relative paths. Cache status
is diagnostic: changing it may change the outer audit hash but never the aggregate
`passed` decision. Task 5 introduces focused evidence schema 2 for non-empty,
typed recovery records with contiguous round indices `0..n-1`; it never changes the
meaning accepted for schema 1.

Schema 2 stays in the same authorization module as schema 1 and keeps the same exact
top-level keys. It requires a non-empty `recoveries` list. Core source/composition
types live in `maximum_optimizer.domain`; recovery evidence types that reference
`FocusedRenderEvidence` live beside that existing type in
`maximum_optimizer.focused_cache`. `focused_regions` never implements a second
schema parser and may only delegate to the shared builder. This placement avoids a
`domain <-> focused_cache` import cycle and prevents schema-1/2 validation drift.

Schema 2 receives a typed `FocusedRecoveryContext` with exact fields `schema=2`, the
complete initial `FocusedEvidenceContext`, ordinary-base cache digest, and initial
schema-1 `authorization_sha256`. Thus initial records remain bound to the ordinary
base that produced them. The schema-2 top-level `candidate_id` is derived from the
last authorized recipe/final-whole record and names the terminal composite; it is
never accepted as a caller-supplied alias. Every round recipe must chain back to the
same base context/cache and initial authorization.

Each recovery record has only `round_index`, `terminal_status`, `recipe`,
`composition`, `changed_sources`, `reused_region_evidence`, `compile_files`,
`structural`, `rerun_records`, `final_whole`, and `evidence_sha256`. Round indices are
the reserved attempt indices and are exactly contiguous `0..n-1`; failed attempts are
not removed or renumbered. An absent optional object is exactly `null`/`None`; an
absent sequence is exactly an empty list/tuple, never a missing key. The exact status
matrix is:

- `composition_failed`: composition/changed sources, compile files, structural,
  reruns, and final whole are absent;
- `compile_failed`: composition and changed sources are present; compile files,
  structural, reruns, and final whole are absent;
- `structural_failed`: composition, changed sources, complete compile files, and
  structural evidence are present; reruns and final whole are absent;
- `focused_failed`: composition, changed sources, complete compile files, structural
  pass, rerun records, and reused hashes are present; final whole is absent;
- `final_whole_failed`: the same fields as `focused_failed` fold to all-focused pass,
  and one failed fresh final-whole evidence is present but cannot authorize;
- `authorized`: all prior fields are complete, the folded focused set passes, and one
  fresh passing final-whole evidence is present.

`ChangedSourceProof` and `CompileFileProof` are typed exact-key records, not arbitrary
mappings. Each changed source corresponds to exactly one cumulative recipe overlay
and proves canonical path, before/after size and hash, and replacement snapshot.
Compile files are the complete required contained artifact set and seal kind, size,
and current hash. Structural and final-whole records bind those manifests and the
composition/candidate/cache digests.

The record matrix above is exact for `FocusedRecoveryEvidence`. Adaptive-direct never
adds optional direct fields to that record. It uses exact `AdaptiveDirectEvidence`
with `schema == 2`, `kind == "adaptive-direct-fallback-v1"`, `round_index == 0`,
`coverage_manifest_sha256`, `base_focus_records`, and `direct_focus_records`. Its
direct records are in canonical changed-source order, contain exactly one
state-independent source-union record per changed identity, and have neither missing
nor duplicate identities. Every target repeats the manifest/source-proof digest and
has exactly one record whose file count equals its 32/64 image formula; extra state- or
object-specific records are forbidden. Its terminal-status presence matrix is
otherwise the same fail-closed progression through composition, compile, structural,
focused, final whole, and authorized.

For every round that reaches focus validation, selected targets partition exactly
into dependency-affected rerun records and reusable prior evidence hashes. A reused
focus names its immediately prior evidence hash. Folding starts with the exact initial
records and replaces only rerun targets in round order. The last record must be
`authorized`; its folded set contains exactly one passing current record per selected
target. No earlier record may authorize, no record after `authorized` is accepted,
and exactly one final-whole pass exists. Schema-2 parsing is introduced only in Task
5; schema 1 remains byte-for-byte no-recovery-only.

## Report and lifecycle protocol

Profile-schema-1/2 runs preserve the existing canonical Maximum report and progress
schema 1 byte-for-byte. Trusted profile schema 3 uses a conditional exact report
schema 2; adding recovery fields to the legacy dataclass serializer is forbidden.
This schema bump applies only to the durable terminal/progress JSON files. Every
stdout line prefixed by `MAXIMUM_EVENT ` remains the existing schema-1 event envelope
and field types so the current WPF parser remains compatible. A schema-2 durable
report is never emitted as a schema-2 stdout event, and Task 8 makes no WPF change.
The terminal schema-2 report contains exactly `schema`, `report_kind="terminal"`,
status, original/control/selected/final sizes, tool versions, family summaries,
bounded events, declared event count/bound, cancellation flag, and a canonical report
path/seal. Its exact keys are `schema`, `report_kind`, `status`, `original_size`,
`control_size`, `selected_size`, `final_size`, `tool_versions`, `families`, `events`,
`event_count`, `event_bound`, `cancelled`, `report_path`, and `report_sha256`.
Schema-3 progress has exactly the same non-terminal summary keys but uses
`report_kind="progress"` and `progress_sha256`, and omits `final_size` and
`report_sha256`; it cannot parse as a terminal report. Both report paths are canonical
contained relative paths and each seal excludes only itself. Every
terminal/progress JSON file is at most 16 MiB and is published through a safe
contained atomic helper that rejects a reparse `logs` leaf or ancestor.

Schema-3 attempt summaries are exact bounded diagnostics, not copies of evidence.
They name candidate ID/kind/engine/status, actual compiled bytes, cache-hit status,
base candidate, nullable reserved round and direct ratio, nullable coverage-manifest
digest, base-focus states,
changed source identities, adaptive-direct isolated source-focus states, reused region
keys, and nullable composition, compile,
structural, schema-2, and final-whole hashes. They never embed full material/source/
render evidence, raw commands, absolute runtime paths, or caller-sized error text.
Candidate IDs are at most 128 UTF-8 bytes, errors 4,096, and every list reuses its
existing top-K/source/round/artifact/candidate-budget cardinality.

Each selected base target has exactly one report-only state, and adaptive-direct has
exactly one additional state for every changed source identity: `passed`, `failed`,
`cancelled`, or `unattempted`. Only attempted passed/failed states carry a focused
evidence hash. Cancelled/unattempted summaries cannot be converted into
`FocusedRenderEvidence` and never enter authoritative schema 2. Donor recovery report
rounds match reserved contiguous indices `0..n-1`; each independent adaptive-direct
attempt has only round `[0]`. Changed and reused identities exactly partition the
selected dependency set. Only an attempt whose current schema-2 terminal status is
`authorized` may carry a passing final-whole hash, become selected, trigger
`best_updated`, or reach promotion.

The fixed stage vocabulary is `generate_compile`, `cache_restore`,
`cache_revalidate`, `compiled_size`, `structural`, `whole_visual`,
`focused_select`, `focused_render`, `focused_compare`,
`focused_evidence_publish`, `recovery_select`, `recovery_compose`,
`recovery_compile`, `direct_source_prepare`, `direct_source_build`,
`direct_source_validate`, `final_whole_visual`, and `cache_store`. Stage records have
exact family/candidate identity and candidate kind, ordinal/total, plus stage-valid
nullable round, region, and source identity. Unknown stages, fields, order, or excess
cardinality fail closed. Events remain diagnostic.

The event-count ceiling is derived before work as
`2 + family_count * (2 + (SearchBudget.max_candidates + 1) * candidate_event_bound)`,
where `candidate_event_bound = 2 + 13 + 2 * focused_top_k + 5 * 8`. This covers
candidate start/finish, thirteen singleton stages, two render/compare stages for each
of at most four base focuses, and prepare/build/validate plus isolated render/compare
for each of at most eight direct sources.
The 16-MiB report bound is authoritative even when the derived ceiling is larger.
Space for candidate/family/run terminal records is reserved; history is never silently
truncated into a successful report or rewritten without bounds. After inventory and
before candidate work, the runner rejects a family/event projection whose minimum
valid terminal summaries cannot fit in 16 MiB.

Cancellation is checked before reservation, snapshot open, every bounded copy/hash/
process, composition/compile, each focus stage, partial/authoritative publication,
final whole, cache publication, best update, and output replacement. Before cache
promotion it removes only owned staging. If cancellation becomes visible only after a
fully validated marker-last cache entry has atomically replaced its destination, that
entry remains reusable; the current attempt records cancelled/unattempted remainder
states and performs no best update or output promotion. Every reserved attempt,
family, and run receives exactly one terminal record. Cancellation publishes no
authoritative schema 2 containing report-only unattempted targets.

## Resource and denial-of-service bounds

The following are hard validation limits, not tunable environment variables:

- focused `top_k`: default 3, maximum 4;
- camera directions: exactly 8;
- passes: exactly `textured,clay`;
- poses: maximum 2;
- whole visual configurations: maximum 16 including LOD states;
- base focused renders per candidate: maximum 4;
- adaptive-direct isolated source focuses: exactly one per changed direct source,
  maximum 8, in addition to the base focused prefix;
- source-union proof per changed source: maximum 4,096 canonical occurrence records,
  256 connected components, 256 material-region keys, 16 state/dependency keys, and
  2 poses; excess rejects the ratio before render;
- source-union render images: maximum 64 per changed source and 512 per candidate
  (`2 sides * 2 poses * 8 cameras * 2 passes`); visibility witnesses: maximum 512 per
  source and 4,096 per candidate (`256 components * 2 poses`). Exact totals are
  computed and sealed during coverage preflight before ratio reservation/direct I/O;
- recovery rounds per base candidate: maximum 3;
- changed sources per focused-recovery composition: maximum 4;
- donor candidates inspected per changed source: maximum 8;
- source-tree manifest: maximum 4,096 regular files and 2 GiB total bytes;
- compiled composition manifest: maximum 64 regular artifacts and 2 GiB total bytes;
- retained recovery registry: `SearchBudget.max_candidates` candidate snapshots plus
  exactly one original snapshot;
- Monaco exact-fallback visual sources: maximum 8;
- Monaco adaptive-direct changed sources: minimum 1, maximum 8; the complete eligible
  set is used or the proposal is rejected, never truncated;
- Monaco direct ratios: exactly 4 and no Cartesian expansion;
- Monaco direct snapshots: exactly one contained regular `.smd` output each, with
  per-ratio aggregate discovery/copy/hash bounded by 4,096 files and 2 GiB;
- Monaco schema-2 records: exact discriminated adaptive-direct kind with one
  independent record at round index 0 per global ratio, containing the complete base
  top-K and one mandatory isolated focus per changed source, outside the three-round
  donor-recovery counter;
- candidate-cache combined payload: the sum of the existing source, compile, and
  direct class bounds plus at most six 16-MiB control/manifest files; transient
  workspace content is never included;
- every cache control, terminal report, and progress JSON: maximum 16 MiB;
- event history: the derived family/candidate/stage formula in the report protocol,
  additionally constrained by the 16-MiB report bound;
- focus-cache material proof: maximum 4,096 files and 2 GiB of hashed content; over
  the bound disables the cache and renders fresh;
- all copy, hash, render, compile, cache, and round boundaries observe cancellation.

The existing `SearchBudget.max_candidates` remains the outer bound. Composite and
recovery candidates consume it exactly like ordinary candidates.

## Failure behavior

Any ambiguity in selector inputs, source/region mapping, pose pairing, provenance,
composition, cache integrity, focused cardinality, or trusted evidence fails the
candidate closed. It does not select a more aggressive profile, skip a focus, or
promote partial output. Exhausted recovery preserves the original family through the
existing outcome path.

Cancellation never promotes output. Partial reports and focused evidence remain
atomic and explicitly mark unattempted focuses and rounds cancelled.

## Planned files

Create:

- `maximum_optimizer/focused_regions.py`: policy, whole-evidence parsing, target
  selection, focused aggregation, and delegation to the shared evidence builder.
- `maximum_optimizer/focused_cache.py`: atomic focused-render cache and material
  proof validation plus the single schema-1/schema-2 authorization parser.
- `maximum_optimizer/composite.py`: donor selection, overlay recipes, composition
  proofs, and Monaco adaptive-direct assembly.
- `tests/maximum_optimizer/test_focused_regions.py`
- `tests/maximum_optimizer/test_focused_cache.py`
- `tests/maximum_optimizer/test_composite.py`
- `tests/maximum_optimizer/test_reporting.py`
- `maximum_optimizer/profiles/maximum-focused-lvs-v1.json`: exact research-only
  schema-3 thresholds.
- `maximum_optimizer/profiles/maximum-focused-lvs-v1.approval.json`: independently
  reviewed evidence/profile/toolchain/input/validation cross-binding.
- `benchmarks/lvs_models/focused_summary_v1.json`: byte-exact import of the formerly
  ignored focused summary.
- `benchmarks/lvs_models/regional_compile_report_v1.json`: byte-exact import of the
  formerly workstation-local compile report.
- `benchmarks/lvs_models/monaco_accepted_composite_v1.json`: byte-exact import of the
  formerly workstation-local accepted Monaco research composite.
- `benchmarks/lvs_models/run_focused_profile_gate_v1.py`: independently reviewed,
  approval-bound real ten-family calibration/holdout fresh-plus-resume runner.
- `tests/maximum_optimizer/test_task8_activation.py`

Modify:

- `maximum_optimizer/domain.py`
- `maximum_optimizer/calibration_evidence.py`
- `maximum_optimizer/fidelity_selection.py`
- `maximum_optimizer/regions.py`
- `render_previews.py`
- `maximum_optimizer/candidates.py`
- `maximum_optimizer/search.py`
- `maximum_optimizer/orchestrator.py`
- `maximum_optimizer/cache.py`
- `maximum_optimizer/reporting.py`
- `batch_optimize_maximum.py`
- `tests/maximum_optimizer/test_fidelity_selection.py`
- `tests/maximum_optimizer/test_regions.py`
- `tests/maximum_optimizer/test_visual_validation.py`
- `tests/maximum_optimizer/test_search.py`
- `tests/maximum_optimizer/test_orchestrator.py`
- `tests/maximum_optimizer/test_cache.py`
- `tests/maximum_optimizer/test_calibration_evidence.py`

No WPF or CLI field changes in this design. The existing explicit backend profile
argument plus the exact trusted profile/approval pair is the sole Task-8 activation
mechanism. The default sentinel remains uncalibrated.

## Acceptance criteria

- Schema 1 and 2 retain current behavior and test outputs.
- Schema 3 cannot load from the pending evidence-v3 seal alone; it requires exact
  independently approved evidence/profile/approval raw and canonical hashes.
- Every formerly external Task-8 artifact is committed at its canonical path or
  reproduced byte-for-byte by the committed runner; absence blocks activation.
- The five calibration and five disjoint LVS holdout families pass the real bounded
  fresh/resume backend gate, with failures preserved rather than promoted.
- Task-8 scope is explicitly `LVS-calibrated backend research profile`; the default,
  WPF, packaged worker, release, and broader quality claims remain unchanged.
- Target selection is deterministic under shuffled input.
- Every selected focus is rendered alone with exact matrix cardinality.
- A candidate cannot pass with a missing, corrupt, or skipped focus.
- Recovery changes only declared source hashes and rerenders every affected focus.
- Donors may be region-passing/global-failing but cannot cross contracts.
- Exact fallback is explicit and auditable.
- Monaco creates no more than four direct composite variants, uses the complete
  approved set of 1..8 exact-fallback visual SMDs, and rejects zero or more than eight
  before reservation or I/O.
- Monaco activation, base selection, and source eligibility use sealed typed strategy,
  metrics, provenance, and snapshot evidence; filenames and environment variables
  cannot activate or steer it.
- Every Monaco ratio has independent discriminated round-0 schema-2 authorization,
  rerenders all base top-K focuses plus one complete source-union proof for every changed direct
  source, and runs at most one final whole gate before it can win.
- Winner selection uses compiled bytes only after structural, whole, and focused
  gates pass.
- Cache hits and misses produce the same authorization result.
- Candidate cache entries use marker-last outer schema 2 and exact record schema 3,
  contain only whitelisted bounded typed artifacts, and never require post-promotion
  sealing.
- Legacy reports remain byte-identical schema 1; trusted schema-3 reports use exact
  bounded schema 2 with hashes/summaries rather than embedded evidence.
- `MAXIMUM_EVENT` stdout remains schema 1 for WPF compatibility; schema 2 is durable
  report/progress JSON only.
- Cancellation observed after a fully valid atomic cache promotion may retain that
  reusable cache entry, but cannot produce a best update or output promotion.
- Cancellation and every hard resource bound fail safely without output promotion.
