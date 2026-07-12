# Focused Region Gate and Composite Recovery Design

## Status and execution gate

This design is approved for planning only. Implementation must not start until the
committed LVS calibration evidence v3 has passed independent review and the root
agent explicitly approves execution. Evidence v3, production profiles, and runtime
defaults are outside this design commit.

The feature is opt-in through a calibrated schema-3 fidelity profile. Schema-1 and
schema-2 profiles retain their current behavior byte-for-byte: no focused renders,
no composite recovery, and no new candidate schedule.

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

The real limits come only from approved evidence v3. The loader requires exact
fields, finite non-negative values for every required metric, `top_k` from 1 through
4, and an evidence seal equal to the independently trusted v3 seal. Merely writing
`schema: 3` or copying an unreviewed evidence hash cannot enable the feature.

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
does not add or change any CLI field. Focus activation is determined only by
`FidelityProfileSet.focused_policy is not None`, because schema-2 and schema-3 share
the typed family selector mode.

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
only under trusted schema 3 after at least one completed ordinary
`blender-adaptive-v1` evaluation has passed structural, initial whole, and every
selected focused gate. Among those eligible ordinary evaluations, the immutable base
is the one with the smallest actual compiled byte count, then candidate ID. Strategy,
candidate metrics, source provenance, source snapshot, and current bytes prove
eligibility; model/family filenames, path tokens, environment variables, and CLI
switches never do.

The base publishes a sealed exact `AdaptiveCandidateMetricsProof` tied to its
candidate/cache identity and `RecoverySourceSnapshot` source-manifest digest. It
inventories every paired visual SMD, not only eligible sources, and contains canonical
per-source input/output byte proofs plus grouped graph-occurrence proofs. The selector
reparses the original and candidate QC graphs
and groups repeated identical graph occurrences by canonical visual source identity.
It selects only paired visual `.smd` outputs whose exact per-source metrics have
`preserved_exact == true` and either fixed reason `ratio-preserved-exact-v1` or fixed
reason `approved-exact-source-fallback-v1`. Arbitrary exception text is diagnostic
only and never an authorization reason. Animation, physics/collision, DMX, missing or
stale outputs, conflicting repeated occurrences, duplicate/case-colliding identities,
unsealed metrics, and a ninth eligible source reject the Monaco proposal before a
mini-build, snapshot open, hash, or process starts. No eligible source produces no
Monaco candidates and consumes no budget.

Task 6 introduces a bounded `DirectSourceBuildRequest` and
`DirectSourceSnapshot`; an isolated mini-source is not misrepresented as a complete
`RecoverySourceSnapshot`. The request seals family/input, base candidate/cache/source
manifest, optimizer/profile/dependency contracts, canonical source identity and
current input proof, one global ratio, strategy
`meshopt-direct-position-v1`, prefilter
`direct-degenerate-prefilter-v1`, the exact expected prefilter proof recomputed from
the request input bytes, and its own digest. The snapshot carries the request,
direct candidate/cache identity, one contained regular `.smd` output proof, input and
output triangle counts, the exact recomputed prefilter proof, fixed reason
`approved-direct-position-v1`, and a seal over every canonical field except its
runtime absolute root. It contains exactly one output; across one ratio, the at-most
eight snapshots remain within the existing 4,096-file/2-GiB source bound.

The prefilter proof is authorization data, not a self-signed log. The builder
recomputes `direct-degenerate-prefilter-v1` from the exact no-follow input bytes and
requires exact canonical equality with the reported schema, threshold, dropped
triangle ordinals/records, counts, and digest. A usable direct snapshot requires
`applied == true`, `fallback_reason is None`, `preserved_exact == false`, finite
strictly decreasing triangle counts, changed output bytes, the exact strategy and
transfer contract, and current output bytes matching the sealed proof. A failure in
one mini-source makes that global-ratio candidate terminally failed without publishing
a partial recipe; later fixed ratios may still run if the outer candidate budget
permits.

`monaco_composite_specs` creates exactly the four terminal global ratios
`(0.50, 0.45, 0.40, 0.35)`. Every selected source inside one variant receives the
same ratio. Source order is canonical and cannot create a Cartesian product. Candidate
and recipe identity bind the base cache/source manifest plus every sorted direct
snapshot and prefilter proof. Adaptive-direct trails are terminal: `choose_next`
never applies bracket refinement, midpoint generation, donor recovery, or legacy
regional recovery, so there is no fifth ratio. The four scheduled variants still
obey `SearchBudget.max_candidates`; unavailable outer budget stops before opening a
snapshot or starting a mini-build.

Each ratio is an independent `adaptive-direct-fallback-v1` composite, not a donor
recovery continuation. It has its own focused-evidence schema-2 payload with exactly
one recovery record at `round_index == 0`; the four independent records do not consume
or extend the donor-recovery maximum of three rounds and are never cumulative across
ratios. Its direct `SourceOverlay` entries resolve only through the supplied
`DirectSourceSnapshot` registry and require fixed mode/reason, ratio, direct
candidate/cache identity, snapshot hash, and byte proof. The compositor overlays
those outputs on the immutable ordinary base, changes no other source or QC byte, and
recompiles the complete QC.

Every successfully composed ratio runs structural authorization, rerenders all
selected top-K focuses without reuse, folds those records over the immutable base
candidate's sealed schema-1 initial ranking/authorization through schema 2, and—only
after the structural and focused set passes—runs exactly one fresh final whole render.
The base selected prefix is fixed for that ratio; recomputing a ranking cannot remove
or replace an initially selected target.
The final-whole evidence binds the composite recipe/composition, complete compile
manifest, current candidate/cache identity, and fresh whole index. Only terminal
`authorized` schema 2 can enter candidate cache, best update, winner selection, or
output promotion. Earlier composition/compile/structural/focused failure carries the
Task-5 typed terminal status and no final-whole authorization.

The Blender base remains eligible. Among candidates that pass every gate and have a
strictly positive compiled saving, the winner is the minimum actual StudioMDL bytes,
then the stronger fidelity score, then candidate ID. A composite never wins merely
because its intermediate SMD files are smaller.

## Cache design

The existing candidate cache continues to cache compiled candidate workspaces, but
cached diagnostics never authorize structural, whole visual, or focused gates.
For schema-3 candidates it also records the complete canonical recipe, source-tree
manifest/snapshot seal, composition seal, and structural/final authorization evidence
hashes with exact keys. Cache restoration validates current contained source bytes
before the build enters the retained donor registry. A valid hit still reruns every
current hard gate; an old schema, mixed ordinary/composite fields, stale recipe,
same-size source mutation, or missing source manifest is a cache miss.

An adaptive-direct cache entry additionally binds every canonical
`DirectSourceBuildRequest`, `DirectSourceSnapshot`, recomputed prefilter proof, and
the exact independent ratio recipe. Direct snapshots never enter the donor registry
and cannot authorize donor/exact-original overlays. Restore reopens the one contained
`.smd` output no-follow, revalidates input and output bytes plus the base snapshot,
then reruns structural authorization, every selected focus, and the one final whole
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

For every round that reaches focus validation, selected targets partition exactly
into dependency-affected rerun records and reusable prior evidence hashes. A reused
focus names its immediately prior evidence hash. Folding starts with the exact initial
records and replaces only rerun targets in round order. The last record must be
`authorized`; its folded set contains exactly one passing current record per selected
target. No earlier record may authorize, no record after `authorized` is accepted,
and exactly one final-whole pass exists. Schema-2 parsing is introduced only in Task
5; schema 1 remains byte-for-byte no-recovery-only.

## Resource and denial-of-service bounds

The following are hard validation limits, not tunable environment variables:

- focused `top_k`: default 3, maximum 4;
- camera directions: exactly 8;
- passes: exactly `textured,clay`;
- poses: maximum 2;
- whole visual configurations: maximum 16 including LOD states;
- focused renders per candidate: maximum 4;
- recovery rounds per base candidate: maximum 3;
- changed sources per recovery composition: maximum 4;
- donor candidates inspected per changed source: maximum 8;
- source-tree manifest: maximum 4,096 regular files and 2 GiB total bytes;
- compiled composition manifest: maximum 64 regular artifacts and 2 GiB total bytes;
- retained recovery registry: `SearchBudget.max_candidates` candidate snapshots plus
  exactly one original snapshot;
- Monaco exact-fallback visual sources: maximum 8;
- Monaco direct ratios: exactly 4 and no Cartesian expansion;
- Monaco direct snapshots: exactly one contained regular `.smd` output each, with
  per-ratio aggregate discovery/copy/hash bounded by 4,096 files and 2 GiB;
- Monaco schema-2 records: exactly one independent record at round index 0 per
  global ratio, outside the three-round donor-recovery counter;
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

Modify:

- `maximum_optimizer/domain.py`
- `maximum_optimizer/fidelity_selection.py`
- `maximum_optimizer/regions.py`
- `render_previews.py`
- `maximum_optimizer/candidates.py`
- `maximum_optimizer/search.py`
- `maximum_optimizer/orchestrator.py`
- `batch_optimize_maximum.py`
- `tests/maximum_optimizer/test_fidelity_selection.py`
- `tests/maximum_optimizer/test_regions.py`
- `tests/maximum_optimizer/test_visual_validation.py`
- `tests/maximum_optimizer/test_search.py`
- `tests/maximum_optimizer/test_orchestrator.py`
- `tests/maximum_optimizer/test_cache.py`

No WPF or CLI field is required. The trusted schema-3 profile is the sole activation
mechanism.

## Acceptance criteria

- Schema 1 and 2 retain current behavior and test outputs.
- Schema 3 cannot load without independently approved evidence v3.
- Target selection is deterministic under shuffled input.
- Every selected focus is rendered alone with exact matrix cardinality.
- A candidate cannot pass with a missing, corrupt, or skipped focus.
- Recovery changes only declared source hashes and rerenders every affected focus.
- Donors may be region-passing/global-failing but cannot cross contracts.
- Exact fallback is explicit and auditable.
- Monaco creates no more than four direct composite variants and touches only
  approved exact-fallback visual SMDs.
- Monaco activation, base selection, and source eligibility use sealed typed strategy,
  metrics, provenance, and snapshot evidence; filenames and environment variables
  cannot activate or steer it.
- Every Monaco ratio has an independent one-round schema-2 authorization, rerenders
  all selected focuses, and runs at most one final whole gate before it can win.
- Winner selection uses compiled bytes only after structural, whole, and focused
  gates pass.
- Cache hits and misses produce the same authorization result.
- Cancellation and every hard resource bound fail safely without output promotion.
