# Frozen Holdout Region-Tournament Protocol

## Status and blind boundary

This document freezes an evaluation-only algorithm. It does not run a holdout,
authorize production, register a scheduler, add a worker command, or change WPF.

Absolute historical blindness is no longer representable in this repository: old
versioned corpus/control files already expose identifiers and compiled-size records.
During this design task an over-broad repository search also printed one such legacy
record. Those values are not inputs to this protocol. The binding blind rule is:

- do not open, enumerate, render, compile, or measure fresh holdout sources;
- do not read fresh holdout reports, images, manifests, or metrics;
- choose no algorithm parameter or threshold from historical or fresh holdout values;
- freeze the algorithm and every calibration/profile/tool hash before a separate
  executor computes the first holdout input commitment;
- after any holdout result becomes observable, v1 is immutable. A failure is a result,
  not permission to alter a ratio, error, gate, focus, beam, or threshold.

Only synthetic fixtures are used by the committed contract tests.

Calibration-only review invalidated the former fixed `top_k=3`: it covered only
9.09%--20% of changed car sources, while whole-family gates had already hidden severe
regional silhouette, RGB, and edge regressions. No holdout observation informed this
correction. The all-region rule below replaces that unsafe sampling boundary before
any holdout input commitment exists.

## Freeze prerequisite

`HoldoutFreezeBindings` requires an exact, self-sealed
`visual-remap-calibration-approval-v1` plus SHA-256 commitments for:

- the new full visual-remap calibration bundle;
- its whole and focused profiles;
- its independent profile approval;
- optimizer contract;
- renderer;
- compiler;
- complete toolchain.

The archived v3 calibration evidence is deliberately not accepted as this approval.
The new visual-remap calibration/profile bundle must first finish its remaining
negative controls and repeats, then receive independent approval. No real holdout
protocol can be created or reparsed until the caller supplies the exact reviewed
approval seal as an independent expected value. The approval payload, its seal, and
that expected seal must all agree.

The approved bundle does not exist yet, so the current implementation can be
instantiated only by synthetic tests with an explicitly synthetic approval. This is a
fail-closed prerequisite, not a runtime tuning knob. After approval, the separate
freeze step supplies every binding before input access and they become part of
`protocol_sha256`. Missing, changed, stale, or merely self-asserted approval blocks the
holdout. Environment variables and CLI overrides cannot change protocol fields.

## Frozen identities and search space

The exact protocol identity is `lvs-holdout-region-tournament-v1`. The base lane is
the existing `blender-adaptive-v1` / `blender-native-v1` ladder, with vertex updates:

| Base ratio | Target error |
|---:|---:|
| 0.60 | 0.0 |
| 0.50 | 0.0 |
| 0.45 | 0.0 |
| 0.40 | 0.0 |
| 0.35 | 0.0 |
| 0.30 | 0.0 |
| 0.25 | 0.0 |
| 0.20 | 0.0 |
| 0.15 | 0.0 |

The fixed order is conservative to aggressive and comes only from calibration, never
from holdout observations. It does not authorize an early accept: all nine bases must
receive a terminal attempt record before selection. Among base attempts that pass all
required gates, exactly the two smallest actual compiled families become tournament
finalists. Fewer than two may continue if the other bases fail; incomplete base-lane
terminal coverage invalidates the run.

Each selected region has nine ordered choices. Choice zero is exact source fallback.
The other choices use `meshopt-remapped-visual-v1`,
`visual-remapped-topology-v1`, and no vertex updates:

| Option | Ratio | Target error |
|---:|---:|---:|
| 1 | 0.15 | 0.020 |
| 2 | 0.20 | 0.020 |
| 3 | 0.25 | 0.020 |
| 4 | 0.30 | 0.010 |
| 5 | 0.35 | 0.010 |
| 6 | 0.40 | 0.010 |
| 7 | 0.50 | 0.010 |
| 8 | 0.60 | 0.005 |

There is no midpoint insertion, adaptive error change, early ratio invention, or
family-specific ladder. An engine returning fewer triangles than requested is allowed
only when the structural contract records the actual result and all gates pass.
Choice order reserves deterministic work only: every choice is expanded and reaches a
terminal record at its beam stage, so an aggressive choice can never stop evaluation
before a later conservative fallback is tested.

## Deterministic all-region focus selection

The selector is `exact-source-union-all-regions-v1`. Before any optimized candidate
exists, the exact source union defines the complete canonical region universe. The
universe seals every region key, exact source hash, source identity, complete
occurrence/configuration commitment, renderability classification, occurrence count,
and a static risk rank. It is bounded to 64 regions and 256 occurrences. Exceeding a
bound fails the family closed; it never truncates the universe.

Every exact-union region is selected. Static risk changes execution order only and can
enable early rejection; it cannot remove a region, change a gate, or authorize a
sample. The authoritative order is the complete permutation of risk ranks with region
key as the deterministic tie identity. Candidate image or geometry metrics never
influence the universe or order.

For each candidate, source lineage compares the candidate source SHA-256 to the exact
source SHA-256 after the sealed prefilter:

- an identical hash receives an `exact-source-equality-v1` proof and needs no render;
- every changed hash receives exactly one terminal isolated focused proof;
- an unknown source or a changed region classified unknown/unrenderable receives a
  terminal rejection;
- authorization requires complete terminal coverage of 100% of changed regions and
  exact equality proofs for every unchanged region.

Thus the selected set is absolutely candidate-independent while work is proportional
to changed regions through equality-proof reuse. There is no `top_k`, risk threshold,
or unchecked tail.

Each changed focused comparison renders the region alone with exactly eight cameras, exactly
the `textured` and `clay` passes, bind pose plus at most one sealed animation pose, and
the same material roots/configuration on reference and candidate. Missing images,
states, poses, manifests, or material proofs reject the candidate.

## Region tournament

For each of the at most two base finalists:

1. Start a beam containing the exact finalist composition.
2. Process every region in the sealed static-risk order. A base that changes no source
   uses only equality proofs.
3. Expand every retained composition with all nine ordered choices for the current
   region. Previously selected regions remain fixed and later regions remain exact.
4. Snapshot/validate every changed source and reuse its isolated region-option proof
   by the exact cache identity `(protocol, universe, region, occurrence, renderability,
   exact source, candidate source, option)`. Conflicting results under one identity
   fail closed.
5. Intermediate region compositions run lineage, structural, compile,
   `focused_all_changed`, and source-union gates. They are `eligible` or `rejected`,
   never authorized, and never run a whole visual gate.
6. Retain at most four passing compositions by `(actual compiled total bytes,
   candidate_id)`. Cache state cannot affect this ordering.
7. After the last region, run one fresh final whole gate on every retained composition.

The beam width is exactly four. It is a fixed bound, not a score threshold. A smaller
beam caused by failures remains smaller; rejected candidates are never backfilled by
relaxing a gate. Exact source choice at every region ensures a base can survive a
region that has no acceptable remap. Recipes may contain up to the complete 64-region
universe; the old three-region recipe limit is removed.

Focused evidence is linear in unique region-option identities. A previous prefix is
never rerendered when adding the next region. The protocol caps the cache namespace at
1,152 identities (64 regions times nine exact/remap choices times two base finalists;
576 per finalist). Naive prefix evidence with
quadratic render semantics is outside the protocol. Whole visual gates run only for
base-terminal attempts and final-retained compositions.

## Required gates

An authorized attempt has exactly these ordered seals:

1. `source_lineage`: raw source, deterministic prefilter, exact filtered input,
   request, toolchain, and output source hashes agree;
2. `structural`: topology/material/component/boundary/animation and provenance
   contracts pass for every changed source;
3. `compile`: StudioMDL succeeds without unapproved autofix and produces one bounded,
   canonical artifact inventory;
4. `whole_visual`: all frozen whole states/configurations pass the whole profile;
5. `focused_all_changed`: every changed exact-union region has one passing terminal
   isolated proof and every unchanged region has an exact equality proof;
6. `source_union`: every changed source has complete occurrence/component/material/
   state/pose visibility and isolated comparison evidence;
7. `final_whole_visual`: the exact compiled composition receives one fresh final whole
   pass after all changes.

A base-terminal attempt uses gates 1 through 6. An intermediate region composition
omits both whole gates and cannot authorize. A final-retained attempt uses all seven
gates. A rejected attempt contains the canonical passing prefix and names the first
failed gate. Unknown, unrenderable, missing, reordered, duplicated, or post-hoc gates
fail the evidence closed.

## Actual compiled-byte selection and fallback

Compiled size is never estimated from SMD size, triangle count, source bytes, or
individual region savings. Each candidate carries a canonical inventory of at most 64
regular StudioMDL artifacts and at most 2 GiB. `total_bytes` must equal the sum of the
sealed `.mdl`, `.vvd`, `.vtx`, `.phy`, and `.ani` files; `.mdl` and `.vvd` are
mandatory.

The exact original family is compiled, structurally checked, rendered, and retained as
the mandatory authorized fallback. Final selection considers only attempts with all
seven gates and an identical replay compile-manifest hash. A non-original wins only
when its actual compiled total is strictly smaller than the exact original. Selection
is `(total_bytes, candidate_id)`. Equal or larger size selects the original.

This is intentionally independent of a promised percentage. A large reduction may
win only through the same gates; a visually safe small reduction may win when no more
aggressive candidate passes; no saving returns the original exactly.

## Determinism and fresh/replay rule

All collections are canonicalized before hashing. Region keys, focus ranks, recipes,
attempts, artifacts, and ties have explicit order. Random seeds, timestamps, absolute
paths, cache-hit flags, wall time, and filesystem enumeration order are excluded from
authority payloads.

The complete run is executed fresh and replayed under the identical protocol/input/
toolchain commitments. The replay must reproduce:

- base terminal matrix;
- exact source-union universe hash, complete risk order, equality proofs, and terminal
  changed-region coverage;
- unique local region-option cache identities and linear evaluation trace;
- tournament reservation/terminal trace;
- candidate recipe IDs;
- compiled artifact manifests;
- gate seals;
- winner and complete run seal.

Any difference invalidates the evaluation. Resume/cache may be tested separately, but
must reproduce the fresh authorization result and cannot substitute for fresh/replay.

## No-retune rule

The executor first publishes the protocol seal and freeze bindings, then computes the
holdout input commitment. Once that commitment exists:

- no ratio, error, strategy, beam width, universe, region, state, pose, pass, camera, profile,
  threshold, gate, retry, fallback, or tie rule may change;
- failures, timeouts, unsupported sources, and no-saving outcomes remain in evidence;
- rerunning with modified code or tools requires a different protocol seal and cannot
  be reported as v1;
- observed v1 holdout results may not be used to create a replacement v1 or recalibrate
  the profiles. Further tuning requires new calibration data and a genuinely untouched
  evaluation partition.

## Evidence and activation boundary

`maximum_optimizer.holdout_protocol` provides only immutable contracts and pure
selection helpers. Each attempt seals protocol/input/family commitments, exact recipe,
compiled inventory, attempt stage, complete focused coverage/equality proofs, ordered
gate seals, failure gate, replay manifest, and evidence hash. The family run
additionally seals the complete sorted attempts, tournament trace, fresh/replay seal,
selected candidate, no-retune declaration, and scope.

The fixed scope is `holdout-evaluation-only`; `authorizing_production` is always false.
No executor, filesystem discovery, renderer, compiler invocation, scheduler adapter,
worker route, WPF control, cache registration, or output promotion is added here.
Production activation requires a separate reviewed task after the still-unexecuted
holdout protocol completes successfully and its evidence is independently audited.
