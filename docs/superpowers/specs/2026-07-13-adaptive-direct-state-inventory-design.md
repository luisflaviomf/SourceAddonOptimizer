# Adaptive Direct Production State Inventory Design

## Goal

Build a production, fail-closed enumerator that turns the authoritative original QC graph, current source bytes, a sealed adaptive metrics proof, and typed component/material dependencies into `AdaptiveDirectStateInventory`. The inventory must describe only geometry occurrences active in each complete QC state and must stay independent from scheduling, rendering, material resolution, source-union execution, and orchestration.

## Inputs and binding

The production factory consumes:

- the original and base-candidate `RecoverySourceSnapshot` values and their current source roots;
- the sealed `AdaptiveCandidateMetricsProof` for the base adaptive candidate;
- the parsed authoritative original `QcGraph`;
- typed, per-source component/material dependency records supplied by callers;
- an optional, unambiguous original/candidate animation pair.

The factory validates that family, candidate, spec, cache, source-manifest, source-snapshot, and complete visual-source identities agree with the metrics proof. It revalidates the snapshot before and after production. All QC and SMD reads use no-follow/current-byte helpers and retain the existing 4,096-file, 2 GiB source-tree, 4,096-row, 256-component, 256-material, two-pose, and 16-state domain bounds.

No component or material hashes are synthesized. A dependency record must contain canonical `component_keys`, `material_region_keys`, `component_manifest_sha256`, and `material_contract_sha256`, and must bind to the same source identity, size, and SHA-256 as the source proof.

## QC state grammar

The enumerator parses all reachable QC/QCI files in the graph and supports the state-affecting directives already accepted by `QcGraph`:

- fixed `$body` and `$model` visual occurrences;
- every `$bodygroup` choice, including `blank`;
- every `$lod` block and each `replacemodel original replacement` pair;
- one complete `$texturegroup` skin-family declaration, including every nonempty row.

Malformed, duplicated, ambiguous, or partially understood state syntax fails closed. Texture rows must have equal positive width. Without `$texturegroup`, one canonical default skin exists. Includes are already flattened by `QcGraph`, but each occurrence retains its exact graph-relative path, directive, and line.

## Complete state product and early bound

A state is one member of the exact Cartesian product:

`all bodygroup choice combinations × (LOD0 plus every LOD block) × all skin rows`.

The enumerator computes that cardinality before reading or contracting any visual SMD. Zero states or more than 16 states is rejected. There is no truncation, sampling, default-only shortcut, or one-bodygroup-at-a-time approximation.

Each LOD is applied independently to every bodygroup combination. For a replaced active source, the row uses the exact replacement occurrence from that LOD pair. All other active occurrences retain their original fixed/bodygroup occurrence. A blank bodygroup contributes no active occurrence. Skin rows create distinct states even when active geometry is identical.

## Canonical identities

All identities are lowercase SHA-256-derived or canonical relative text; none contain absolute paths.

- `bodygroup_key` seals the ordered group index/name/selected-choice vector.
- `lod_key` seals LOD0 or the exact graph path/line/group and replacement mapping.
- `skin_key` seals default skin or the exact texturegroup path/line/name/row index/material row.
- `state_key` seals the three keys above.
- `occurrence_key` seals state key plus exact graph path, directive, line, source identity, and an occurrence ordinal where necessary to prevent collisions.

Rows are sorted by `(occurrence_key.casefold(), occurrence_key)` before constructing the typed inventory. Only active occurrence/state pairs produce rows. The union of row source identities must equal the metrics proof source union; otherwise production fails.

## Current SMD contracts

Each active SMD is read no-follow under the snapshot root and checked against its `SourceFileProof` size and SHA-256.

The skeleton contract parses and seals:

- every unique `nodes` record as bone id, exact name, and parent id;
- a valid parent graph;
- the complete `skeleton` bind frame at `time 0`, with one finite six-float transform for every node.

The pose contract always contains `bind`. It adds one `animation` pose only when an original/candidate animation pair is unambiguous, byte-current, has identical node contracts and frame indices, and exposes a deterministic positive representative frame. Ambiguity or mismatch fails closed when an animation pair was supplied; absence of an unambiguous pair preserves bind-only behavior.

The equivalence digest seals source identity and current source bytes together with the skeleton and pose contract digests. Therefore rows can share an equivalence class only when their geometry source and deformation contract are identical.

## Row construction

For each active occurrence/state pair, the factory creates a sealed `AdaptiveDirectStateInventoryRow` containing:

- exact QC occurrence provenance;
- canonical state/bodygroup/LOD/skin keys;
- current `SourceFileProof` size and SHA-256;
- injected canonical component/material dependencies;
- generated skeleton and pose contracts;
- the generated equivalence digest.

The final `AdaptiveDirectStateInventory` binds directly to the metrics proof family, candidate, spec, cache, source-manifest, and source-snapshot identities. It exposes the canonical complete source union from the metrics proof.

## Error handling and scope

Every unknown state-affecting construct, unsafe path, symlink/reparse point, stale byte, duplicate identity, incomplete source union, dependency mismatch, malformed SMD, state overflow, or noncanonical order raises before an inventory is returned. The factory never truncates or invents evidence.

This phase does not modify scheduler/orchestrator code and does not implement material discovery, source-union rendering, runner integration, or state-image scheduling.

## Tests

Synthetic tests cover:

- exact bodygroup Cartesian products with blanks;
- LOD replacement applied to every bodygroup combination;
- complete texturegroup skin rows;
- canonical/deterministic keys independent of absolute roots;
- rejection before SMD reads when state count is above 16;
- current no-follow skeleton/bind parsing and malformed/stale rejection;
- bind-only and unambiguous paired-animation pose contracts;
- typed dependency and metrics/snapshot binding failures;
- active-only row generation and typed inventory round trip.

Read-only real-corpus tests cover the Pontiac wheel as two states and the Dodge Charger as an early state-bound rejection. Tests skip explicitly only when the checked-in `.superpowers` source corpus is absent.
