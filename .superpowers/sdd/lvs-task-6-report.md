# LVS R&D Task 6 report

## Outcome

`meshopt-direct-position-v1` removes false wedge-border saturation on the Pontiac wheel while
preserving the Task 5 direct/no-update tuple contract. It is not a winner and has no quality claim.
The wheel compiles substantially smaller than `meshopt-direct-v1`, but remains larger than Blender.
The first full-car probe fails closed during explicit imported-corner provenance reconstruction, so
no Charger candidate or compiled result is claimed and the pressure set was not run.

## Topology and ownership

Exact float32 positions form canonical IDs with the same first-representative behavior as
`meshopt_generatePositionRemap`, including normalization of negative zero to positive zero.
Canonical edge ownership locks true open edges and all incident positions of nonmanifold edges. UV,
normal, material and skin transitions are counted separately and use `Protect`; skin transitions use
the complete normalized float32 `(bone, weight)` influence signature instead of a dominant-bone
shortcut. The old dominant-skin-signature `Lock` rule is bypassed. Flags are propagated to every
wedge at a canonical position before meshoptimizer v1.2 permissive simplification.

The native bridge ABI is 3. The new mode simplifies one global index buffer instead of per-material
subsets. Output triangle material is accepted only when the intersection of the three source-index
material provenance sets contains exactly one value; empty or ambiguous ownership returns native
error `-7`. A synthetic unsplit shared-material mesh proves that fail-closed behavior. The legacy
`meshopt-direct-v1` execution mode remains available and its historical evidence builder reads the
attested Task 5 DLL rather than silently rebinding that evidence to the current bridge. The
production/default candidate schedule remains `meshopt-direct-v1`; the position-remap strategy is
available only through the explicit opt-in candidate schedule.

## Wheel benchmark

Both requested ratios passed source/material/bone/animation/collision audits and StudioMDL compile.

| requested | achieved | locks | triangles | output vertices | compiled vertices | bytes |
| --- | --- | --- | --- | --- | --- | --- |
| 0.25 | 0.599135 | 421/12,090 (3.48%) | 9,694 | 8,121 | 8,060 | 784,269 |
| 0.40 | 0.598764 | 421/12,090 (3.48%) | 9,688 | 8,125 | 8,064 | 784,525 |

For comparison: strict control is 1,187,559 bytes, Task 5 best is 1,034,283 bytes and Blender best
is 633,089 bytes. Thus r0.25 is 34.0% below control and 24.2% below Task 5, but 23.9% above Blender.
Requested r0.25 saturates at the configured error bound, not at false locks.

## Provenance, car probe and decision

Imported Blender loop order is no longer assumed to match source SMD corner order. Before direct
simplification, each imported corner is mapped explicitly to one original SMD corner by material,
normalized exact-float32 position, exact UV and canonical skin. A unique semantic match copies the
original tuple regardless of harmless imported-normal drift. Multiple matches are disambiguated by
the closest normalized source normal only within a fixed 15-degree ceiling and a `1e-4` separation
margin; ambiguity or excess drift fails closed. Per-object exclusions ensure mappings are disjoint,
and multiple direct payloads are merged deterministically.

Under that hardened contract, both Pontiac wheel ratios still generate direct SMDs and compile with
the measurements above. The Dodge Charger reaches an ambiguous semantic bucket at triangle 2,105;
its nearest source-normal distance is about 1.2526 (roughly 77.6 degrees), beyond the fixed ceiling.
It is therefore rejected before candidate generation. No hardened Charger metrics file, direct SMD,
compiled artifact or byte comparison exists, and the earlier permissive compile is deliberately not
claimed. With no full-car candidate and the wheel still larger than Blender, the five-family pressure
set was not plausible.

Textures remain unavailable, so visual quality is unverified. Portable measured evidence is in
`benchmarks/lvs_models/meshopt_direct_position_v1.json`. Its builder rehashes every available source,
tool, SMD and compiled artifact and records the unavailable Blender artifact honestly (only the
attested 633,089-byte baseline is available, so no invented hash is present). Two clean `/Brepro`
builds matched SHA-256
`173e1b86dad4e553d702ab3586ac8d4f77e167090f4f5abada2f09f3f3594c3c`.

## Verification

The TDD REDs covered the absent canonical topology API, absent immutable strategy and absent native
position-remap option. Focused tests cover signed zero, complete skin-weight transitions, duplicated
UV/normal/material wedges, real borders, nonmanifold fail-closed behavior, imported-corner
permutations, hard edges, normal near-ties/ceiling, exact material ownership, evidence mutation and
ambiguous ownership. Final discovery: 385 tests passed with 12 existing environment skips.
