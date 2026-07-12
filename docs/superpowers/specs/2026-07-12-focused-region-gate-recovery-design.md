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

Recovery operates on source identities because exact preservation is a source-file
operation. A failed region is mapped through its canonical descriptor to one source.
If several selected focuses share that source, all of them are affected and must be
rerun.

The recovery order is:

1. A byte-exact output from an already built candidate with the same optimizer
   contract, a less aggressive effective ratio, and a passing focused result for the
   failed region. The donor may fail another focused region globally.
2. The exact original source bytes when no eligible donor remains.

Donor output hashes, candidate ID, optimizer contract, effective ratio, and focused
evidence hash are part of the recipe. Exact fallback records the original source
hash and reason. A donor cannot donate to itself, repeat an existing overlay, reduce
the effective ratio, or cross family/strategy/profile boundaries.

The compositor copies the base candidate source tree, replaces only declared source
outputs, rewrites no unrelated QC directive, and emits a source manifest proving:

- every base source and resulting source has a canonical identity and hash;
- a changed source has exactly one declared overlay;
- every undeclared source hash is identical to the base;
- every donor/exact hash equals the bytes copied;
- the complete optimized QC graph still pairs with the original graph.

The complete QC is recompiled and structurally validated after every composition.
Only focuses whose source hash changed are rerendered during recovery; unchanged
focus evidence is reused after its source, profile, selector, and material proofs are
revalidated. After all focused regions pass, one final whole visual validation is
mandatory because the composed candidate differs from the initially authorized
whole render.

## Monaco adaptive-direct composite

The Monaco path is typed, not filename-based:

1. Build a normal `blender-adaptive-v1` base.
2. Read its sealed candidate metrics and QC graph.
3. Select only visual `.smd` sources explicitly marked `preserved_exact` or carrying
   the approved exact-fallback reason.
4. Reject ambiguous provenance, unsupported formats, missing outputs, duplicate
   identities, or more than eight selected sources.
5. For the global direct ratios `0.50`, `0.45`, `0.40`, and `0.35`, optimize every
   selected source in isolation with `meshopt-direct-position-v1` and exactly
   `direct-degenerate-prefilter-v1`.
6. Assemble one complete candidate per global ratio. Ratios are not combined per
   source, so the search creates at most four variants rather than a Cartesian
   product.
7. Compile the full QC and run structural, whole, and focused gates.

The Blender base remains eligible. Among candidates that pass every gate and have a
strictly positive compiled saving, the winner is the minimum actual StudioMDL bytes,
then the stronger fidelity score, then candidate ID. A composite never wins merely
because its intermediate SMD files are smaller.

## Cache design

The existing candidate cache continues to cache compiled candidate workspaces, but
cached diagnostics never authorize structural, whole visual, or focused gates.

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

Schema 2 keeps the same exact top-level keys and requires a non-empty `recoveries`
list. Each recovery record has only `round_index`, `recipe`, `changed_sources`,
`reused_region_evidence`, `compile_files`, `structural`, `rerun_records`,
`final_whole`, and `evidence_sha256`. Each changed source corresponds to exactly one
recipe overlay; selected regions partition exactly into rerun and reused records;
compile files are exact relative contained artifact proofs; and the final whole
result must be a fresh reauthorization of the composed bytes. Schema-2 parsing is
introduced only in Task 5.

There must be one terminal record for every selected focus. Recovery rounds have an
exact contiguous index starting at zero. Every changed source appears exactly once
per round and every reused focus names the prior evidence hash it depends on.

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
- Monaco exact-fallback visual sources: maximum 8;
- Monaco direct ratios: exactly 4 and no Cartesian expansion;
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
  selection, focused aggregation, and evidence payloads.
- `maximum_optimizer/focused_cache.py`: atomic focused-render cache and material
  proof validation.
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
- Winner selection uses compiled bytes only after structural, whole, and focused
  gates pass.
- Cache hits and misses produce the same authorization result.
- Cancellation and every hard resource bound fail safely without output promotion.
