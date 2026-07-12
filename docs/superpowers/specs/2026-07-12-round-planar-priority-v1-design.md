# Round Planar Priority V1 Design

## Goal

Prototype an opt-in R&D strategy for rigid, nearly round SMD components. The
strategy must remove planar interior triangulation before the existing Blender
collapse while preserving circular silhouette rings and every Source material,
UV, normal, and skin boundary. It is not a production candidate or profile.

## Admission and fallback

Admission is deterministic and fail-closed. A mesh is eligible only when it has
one effective rigid influence signature, one connected manifold component, a
stable thin principal axis, enough angular coverage around that axis, low radial
variation on its outer band, and at least one planar interior edge. Otherwise
the caller returns the original SMD bytes and records the rejection reason.

## Blender pipeline

The first modifier is Decimate/DISSOLVE with boundaries disabled and delimiters
`UV`, `SHARP`, `NORMAL`, `MATERIAL`, and `SEAM`. Its angle is deliberately small
and supplied by the R&D experiment. The second modifier reuses the current
adaptive COLLAPSE path. A vertex group marks the outer radial band and axial end
rings. The group is inverted so Blender decimates the interior more aggressively
while retaining the visible rings. This prototype calls the behavior
`round-planar-priority-v1`; it does not claim native meshoptimizer Priority.

## Preservation contract

The existing importer/exporter, QC graph, material slots, vertex groups, custom
loop-normal reconstruction, fixed topology checks, and exact fallback remain in
charge. The prototype does not edit QC, animations, physics, or materials. Any
modifier/export/validation failure restores the untouched source payload.

## Experiment

Prepare an R&D runner for one wheel SMD and one steering-wheel SMD. It emits a
baseline `b050`, a planar-only ablation, and the full planar-plus-ring-priority
candidate. Execution waits for the Blender lock. No result is called a winner
before compiled byte accounting, structural validation, eight-view textured and
clay gates, and independent review.
