# LVS R&D Task 6 report

## Outcome

`meshopt-direct-position-v1` removes false wedge-border saturation on the Pontiac wheel while
preserving the Task 5 direct/no-update tuple contract. It is not a winner and has no quality claim.
In the recorded local experiment, the wheel compiled substantially smaller than
`meshopt-direct-v1`, but remained larger than Blender. Those external binaries are not committed.
The recorded local full-car probe failed closed during imported-corner provenance reconstruction, so
no Charger candidate or compiled result is claimed; the current stricter mapper has not been rerun.

## Topology and ownership

Exact float32 positions form canonical IDs with the same first-representative behavior as
`meshopt_generatePositionRemap`, including normalization of negative zero to positive zero.
Canonical edge ownership locks true open edges and all incident positions of nonmanifold edges. UV,
normal, material and skin transitions are counted separately and use `Protect`; skin transitions use
the complete normalized float32 `(bone, weight)` influence signature instead of a dominant-bone
shortcut. The old dominant-skin-signature `Lock` rule is bypassed. Flags are propagated to every
wedge at a canonical position before meshoptimizer v1.2 permissive simplification.

SMD skin identity preserves exact bone/group name case, and a corner with `link_count=0` uses its
primary bone at weight 1.0 as required by the SMD grammar. Imported triangles map only through the
three cyclic source-corner rotations; odd permutations fail closed, so reversed winding cannot pass
unchanged into the direct serializer.

The native bridge ABI is 3. The new mode simplifies one global index buffer instead of per-material
subsets. Output triangle material is accepted only when the intersection of the three source-index
material provenance sets contains exactly one value; empty or ambiguous ownership returns native
error `-7`. A synthetic unsplit shared-material mesh proves that fail-closed behavior. The legacy
`meshopt-direct-v1` execution mode remains available and its historical evidence builder reads the
attested Task 5 DLL rather than silently rebinding that evidence to the current bridge. The
production/default candidate schedule remains `meshopt-direct-v1`; the position-remap strategy is
available only through the explicit opt-in candidate schedule.

## Wheel benchmark

The following are measurements from the local experiment. The checked-in JSON contains the actual
aggregate and per-source simplifier metrics. StudioMDL/SMD binaries and their hashes are explicitly
labeled `local_external_evidence`; this checkout does not independently reproduce their contents or
prove source/material/bone/animation/collision audits.

| requested | achieved | locks | triangles | output vertices | compiled vertices | bytes |
| --- | --- | --- | --- | --- | --- | --- |
| 0.25 | 0.599135 | 421/12,090 (3.48%) | 9,694 | 8,121 | 8,060 | 784,269 |
| 0.40 | 0.598764 | 421/12,090 (3.48%) | 9,688 | 8,125 | 8,064 | 784,525 |

For local comparison, strict control was observed at 1,187,559 bytes, Task 5 best at 1,034,283 bytes
and Blender best at 633,089 bytes. Thus the recorded r0.25 result was 34.0% below control and 24.2%
below Task 5, but 23.9% above Blender. Its checked-in simplifier metrics show saturation at the
configured error bound, not at false locks.

## Provenance, car probe and decision

Imported Blender loop order is no longer assumed to match source SMD corner order. Before direct
simplification, each imported corner is mapped explicitly to one original SMD corner by material,
normalized exact-float32 position, exact UV and canonical skin. Only cyclic corner permutations are
eligible, preserving source winding. A unique semantic match copies the
original tuple regardless of harmless imported-normal drift. Multiple matches are disambiguated by
the closest normalized source normal only within a fixed 15-degree ceiling and a `1e-4` separation
margin; ambiguity or excess drift fails closed. Per-object exclusions ensure mappings are disjoint,
and multiple direct payloads are merged deterministically.

The numerical wheel table predates the final odd-permutation rejection and has not been rerun; it is
retained only as an honest local experiment, not as proof of the current corrected mapper. In the
recorded local Charger probe, mapping reached an ambiguous semantic bucket at triangle 2,105 and its
nearest source-normal distance was about 1.2526 (roughly 77.6 degrees), beyond the fixed ceiling. No
Charger metrics file, direct SMD, compiled artifact or byte comparison exists. The current stricter
winding contract has not been rerun on Charger. With no current full-car candidate, the five-family
pressure set was not run.

Textures remain unavailable, so visual quality is unverified. The sealed local experiment record is
`benchmarks/lvs_models/meshopt_direct_position_v1.json`; it commits actual simplifier counts but marks
all uncommitted SMD/compiled hashes and byte observations as `local_external_evidence`. Rehashing is
an explicit integration command and requires `LVS_TASK6_EVIDENCE_ROOT` to point to the local
`.superpowers` experiment root. Default unit tests instead use the small checked-in hermetic fixture
`benchmarks/lvs_models/fixtures/meshopt_direct_position_v1_unit.json`. The committed clean `/Brepro`
DLL records match SHA-256
`173e1b86dad4e553d702ab3586ac8d4f77e167090f4f5abada2f09f3f3594c3c`, but do not make the external
candidate artifacts portable.

## Verification

The TDD REDs covered the absent canonical topology API, absent immutable strategy and absent native
position-remap option. Focused tests cover signed zero, complete skin-weight transitions, duplicated
UV/normal/material wedges, real borders, nonmanifold fail-closed behavior, imported-corner
cyclic permutations, reversed-winding rejection, hard edges, zero-link primary-bone fallback, exact
bone-name case, normal near-ties/ceiling, exact material ownership, hermetic evidence mutation and
ambiguous ownership. Normal discovery and a second discovery with `.superpowers` hidden both passed:
388 tests with 12 existing environment skips.
