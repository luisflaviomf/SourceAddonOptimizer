# Remapped Topology R&D Contract Design

## Status and scope

This design defines a new, non-authorizing R&D contract for meshoptimizer output whose vertices retain exact Source SMD corner payloads while connectivity changes through edge-collapse simplification. It does not modify, alias, or weaken `meshopt-direct-position-v1` / `direct-position-v1`, whose retained-cycle requirement remains intact.

The new identity is `meshopt-remapped-topology-v1` with transfer contract `remapped-topology-v1`. Passing this structural contract does not establish visual fidelity. Render, runtime, and calibrated profile gates remain mandatory before any production selection.

## Observed incompatibility

The meshoptimizer no-update simplifier returns an index buffer that references existing vertices. It can nevertheless create triangles that were not complete source triangle cycles because edge collapses change connectivity. The old direct-position contract intentionally accepts only retained source cycles, so it rejects these outputs.

Fresh real outputs demonstrate the distinction:

- 1,216 to 486 triangles: 340 new cycles, every corner payload exact, no cross-component triangle, no added nonmanifold excess, no orientation violation.
- 18,627 to 9,349 triangles: 4,242 new cycles and exact corner payloads, but nonmanifold excess grows from 5 to 37 and orientation violations grow from 6 to 54.
- 25,482 to 10,192 triangles: 5,822 new cycles and exact corner payloads, but 10 output faces oppose all three retained corner normals.

The contract must accept the first structural class and reject the latter structural regressions without claiming that accepted output is visually good.

## Contract boundary

The validator consumes the post-prefilter source SMD text, candidate SMD text, and requested ratio. It applies fixed resource caps before building topology indexes. It returns an immutable sealed proof, or raises without producing authority.

The proof binds:

- strategy and transfer identities;
- SHA-256 of exact source and output UTF-8 bytes;
- source/output triangle counts and reduction ratio;
- deterministic global triangle target, achieved ratio, explicit `target_reached`, and ordered per-material source/output counts;
- source connected-component count and complete output coverage;
- retained-cycle and remapped-cycle counts;
- exact boundary-edge counts;
- source/output nonmanifold excess and maximum valence;
- source/output orientation-conflict counts;
- the deterministic source-corner ordinal selected for every output corner;
- a canonical SHA-256 seal over all proof fields.

## Exact-payload provenance

Every output corner must equal a source corner's complete token tuple, including primary bone, position, normal, UV, link count, bone order, and weights. Matching is restricted to the same material.

For a token tuple that occurs once in a material, its ordinal is direct. For duplicates, the validator first partitions occurrences into edge-connected vertex fans, then derives a canonical semantic key containing material, exact token tuple, connected component, boundary membership, and incident source-triangle cycle multiset. A duplicate is accepted only when every fan has the same canonical semantic key. The lowest unused source ordinal in that equivalent group is selected deterministically; once every occurrence has been used, reuse cycles deterministically from the lowest ordinal because indexed mesh vertices may legitimately appear in multiple output triangles. If duplicate fans disagree semantically, validation fails as ambiguous; it must never round-robin between distinct shells that merely touch at one identical payload.

The complete output-corner ordinal stream is stored in the sealed proof, making repeated runs reproducible and allowing later tooling to reconstruct the exact accepted provenance.

## Topology invariants

Topology is evaluated per exact material and exact-position connected component.

The output must:

1. preserve the source prefix through the `triangles` marker modulo CRLF/LF encoding and contain no trailing data after `end`;
2. strictly reduce triangles, compute `max(material count, floor(total source triangles * requested ratio))` as the requested global target, and record whether the simplifier reached it;
3. preserve exact material spelling and first-occurrence order;
4. contain only nondegenerate triangles with three distinct exact corner payloads and position cross-product squared greater than `1e-30`;
5. contain no duplicate oriented or reverse-oriented triangle cycle;
6. map every triangle to exactly one source material/component and never connect separate source components;
7. cover every source material/component with at least one output triangle;
8. preserve the exact set of geometric boundary edges;
9. not increase maximum edge valence or aggregate nonmanifold excess (`sum(max(0, valence - 2))`) in any component;
10. not increase same-direction manifold-edge conflicts in any component;
11. not increase faces whose geometric normal is in the opposite hemisphere from all three retained exact corner normals.

Every material must retain at least one triangle and may never exceed its own source count. The target is deliberately global: a visually sensitive or heavily locked material may retain a larger fraction when another material can safely absorb more of the reduction. Failing to reach the requested target is not a structural failure: the proof seals `target_reached=false` and the achieved ratio so an aggregate scheduler can decide whether other regions compensate. The proof records this adaptive allocation instead of presenting a misleading uniform per-material target.

These checks limit connectivity changes to structurally conservative remapping. They do not estimate silhouette, shading quality, UV distortion across new edges, animation quality, or compiled size.

## Deterministic caps

The v1 validator uses fixed, non-configurable caps:

- source UTF-8 bytes: 64 MiB;
- output UTF-8 bytes: 64 MiB;
- source triangles: 1,000,000;
- output triangles: 1,000,000;
- materials: 4,096;
- connected components: 100,000;
- SMD corner tokens: 64 per corner.

Exceeding a cap fails closed. Material rows are indexed in one pass, position unions avoid per-position row lists, and the component cap is enforced incrementally before duplicate/topology indexes are allocated. Caller-specific looser values are not part of v1.

## Integration and isolation

The initial implementation lives in a focused module and is exposed only through an explicit R&D strategy discriminator in the batch optimizer/research harness. Existing direct-position builders, snapshots, scheduler rules, production authority bundle, and WPF remain unchanged.

Smoke validation uses existing real LVS outputs for wheel, Charger, and Monaco sources. Only outputs that pass the new structural contract may proceed. Source and candidate bytes are re-hashed immediately before paired StudioMDL compiles; success requires the exact coherent `.mdl`, `.vvd`, `.dx80.vtx`, and `.dx90.vtx` set. Eight-view renderer/script/image hashes and descriptive per-angle pixel metrics are sealed into local evidence. Compile reduction and render metrics remain structural/build observations only; quality remains `unverified`.

## Test strategy

Unit tests first establish that the old validator still rejects remapped cycles. New tests then require exact deterministic provenance, valid edge-collapse connectivity, component coverage, material isolation, exact boundaries, no degenerates, bounded nonmanifold topology, bounded orientation conflicts, caps, and proof tamper rejection.

Adversarial fixtures cover cross-material payload borrowing, cross-component bridges, semantically ambiguous duplicate tuples, component deletion, boundary replacement, new nonmanifold fans, reverse winding, all-normal-opposite faces, duplicate triangles, payload synthesis, and cap overflow.

Real smoke evidence is regenerated locally and not treated as portable or authorizing unless its exact artifacts and toolchain hashes are separately sealed.
