# Visual-Remapped Topology R&D Contract Design

## Status and isolation

This design adds a second, non-authorizing R&D contract for meshoptimizer edge-collapse output: strategy `meshopt-remapped-visual-v1`, transfer `visual-remapped-topology-v1`.

It does not modify or alias either existing contract:

- `meshopt-direct-position-v1` / `direct-position-v1` still requires retained source cycles;
- `meshopt-remapped-topology-v1` / `remapped-topology-v1` still requires exact component boundary-edge sets.

The visual-remapped contract exists for otherwise conservative outputs that change boundary connectivity. A structural pass is never a visual-quality claim and never authorizes production selection by itself. `quality_status` is fixed to `unverified`, `quality_claim` is `null`, and `winner` is false in local observations.

## Shared structural invariants

The implementation must reuse the exact contract's parser and topology analysis rather than copy or weaken it. It preserves:

1. source prefix, nodes, skeleton frames, exact material spelling/order, and strict EOF;
2. strict triangle reduction, requested target, achieved ratio, and explicit `target_reached`;
3. complete exact same-material source corner payloads, including bone, position, normal, UV, links, bone order, and weights;
4. deterministic duplicate provenance by edge-connected semantic fan, rejecting ambiguous fans;
5. unique source component assignment for every output triangle and coverage of every source material/component;
6. no degenerate triangle, repeated corner payload, duplicate cycle, or reversed duplicate cycle;
7. no per-component increase in maximum edge valence, nonmanifold excess, same-direction edge conflict, or all-normal-opposite face count;
8. the same fixed byte, triangle, material, token, and incrementally enforced component caps;
9. immutable exact-field proof parsing and canonical SHA-256 sealing.

The only structural difference is boundary admission. Exact boundary equality remains mandatory in `remapped-topology-v1`; `visual-remapped-topology-v1` may change boundary edges but must describe the complete delta.

## Boundary delta proof

For every source material/component and for the aggregate proof, seal:

- source and output boundary-edge counts and canonical set hashes;
- retained boundary-edge count and hash;
- removed boundary-edge count and hash (`source - output`);
- added boundary-edge count and hash (`output - source`);
- `boundary_changed`, exactly equivalent to either delta set being nonempty.

Edges use the existing exact float-position canonicalization. Counts, hashes, and booleans are relationship-checked when a proof payload is loaded. Added/removed aggregates equal the sums of the component rows. The proof cannot describe a boundary delta different from the validator's observed sets.

Allowing a boundary delta does not permit a cross-component bridge, a new vertex/corner payload, a deleted component, or worse nonmanifold/orientation metrics.

## Requested target and aggregate policy

As in the exact remapped contract, a simplifier that safely stops above its requested triangle target remains structurally reportable. The proof seals `global_target_triangles`, `achieved_ratio`, and `target_reached=false`. Aggregate reduction policy belongs to the scheduler, not the structural validator.

## Batch discriminator

The batch optimizer accepts the new identity only as the exact tuple:

`("meshoptimizer", "meshopt-remapped-visual-v1", false, "visual-remapped-topology-v1")`

Generation reuses the existing exact-float32 wedge, position topology, no-update meshoptimizer, exact source-corner serializer, and degenerate prefilter. Existing discriminator strings and routing remain unchanged.

## Authority boundary

The new strategy is R&D-only. It is not added to WPF, worker production selection, scheduler preference, production adapters, or authority bundles in this task.

A future scheduler may select a visual-remapped candidate only when all of the following are independently bound to the same source/candidate bytes:

- structural visual-remapped proof;
- coherent paired StudioMDL compile proof;
- calibrated focused and whole-state visual/geometry gates appropriate to the changed component;
- aggregate size/reduction policy.

Basic eight-view descriptive metrics are useful diagnostics but are not calibrated authority.

## Tests and real observations

TDD fixtures first prove that a boundary replacement fails the exact contract and passes the visual-remapped contract with the precise added/removed delta. Existing synthesized payload, component bridge/deletion, duplicate/reverse, degenerate, nonmanifold, valence, direction, orientation, ambiguity, cap, and tamper fixtures must continue to fail.

Real R&D observations include the Skyline trunk boundary-changing candidate, exact-contract passing glass/wheel controls, and Monaco/body failures. Only structurally accepted cases may compile or render; all results remain local and unverified.
