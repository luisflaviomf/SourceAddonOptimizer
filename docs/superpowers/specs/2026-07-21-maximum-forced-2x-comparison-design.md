# Maximum Forced 2x Comparison Design

## Objective

Measure, on a deterministic set of exactly 100 representative VTF textures, whether the Maximum pipeline produces materially different size and decoded visual quality from the production Magick pipeline when both are required to reduce dimensions by at least 2x. Maximum may use 4x only when the existing quality gates accept it.

This is an isolated experiment. It must not change the published Maximum behavior, the WPF interface, the legacy Python frontend, or the source addon.

## Source and isolation

- Source addon: `C:\WorkshopDL\steamcmd\steamapps\workshop\content\4000\3027256228\lvs_cars_pack`.
- The source tree is read-only for the experiment and is hash-checked after processing.
- The experiment creates separate `original`, `magick-2x`, and `maximum-forced` trees under a new results directory.
- Only the selected VTFs and their related VMTs are copied.

## Deterministic sample

The unit of sampling is the VTF because that is the file being compressed. VMT relationships supply semantic evidence.

- Select exactly 100 VTFs using a fixed seed and stable path hashing.
- Reuse the existing coverage-first selector and inventory tags.
- Limit the selection pool to textures that both pipelines can rewrite as a fair single-image comparison: known alpha classification, one frame, one face, and depth one.
- Preserve representation of opaque textures, gradual alpha, cutouts, glass, normal maps, phong and environment masks, emissive textures, decals, effects, large and small dimensions, DXT1/DXT5, mipmapped and non-mipmapped inputs, where available in the eligible pool.
- Record excluded structural categories and the sample selection SHA-256 so the comparison is reproducible.

## Compared pipelines

### Magick 2x

Run the existing production Magick Compress path with resolution divisor 2, minimum width 8, minimum height 8, aspect ratio preservation, and the current VTF planner protections. No special rule is added for this experiment.

### Maximum forced minimum 2x

Reuse the existing Maximum preprocessing, semantic format decisions, encoders, VTF builder, validation, and decoded-image metrics. The experiment changes only the candidate scales and fallback selection:

- Do not consider original-resolution candidates.
- Generate candidates at 2x and 4x, with each dimension clamped to at least 8 pixels.
- Reject corrupt, structurally incompatible, version-incompatible, semantically invalid, or non-decodable candidates.
- A 4x candidate is eligible only if it passes all existing absolute and semantic quality gates.
- At 2x, choose the smallest candidate that passes the existing gates.
- If no 2x candidate passes image-quality thresholds, choose the structurally and semantically valid 2x candidate with the lowest composite visual error. Mark it `forced_below_quality_gate` and retain all rejection reasons.
- If no structurally and semantically valid 2x candidate exists, mark the texture failed rather than emitting a corrupt or incompatible VTF.

This forced fallback is deliberately experiment-only. It satisfies the requested minimum reduction while making any quality cost explicit.

## Measurements

All quality measurements compare decoded final VTF pixels against the decoded original, not intermediate PNG or DDS data.

Collect per texture and aggregate:

- Actual VTF byte size and full materials-tree byte size.
- Elapsed wall-clock time.
- Final format, dimensions, mip count, version, flags, frames, faces, and depth.
- RGB SSIM, RGB PSNR, FLIP mean, and FLIP P95.
- Alpha SSIM, MAE, and P99 when alpha is semantically used.
- Top-level and mip-chain alpha coverage plus IoU for cutouts.
- Mean, P95, and maximum angular error for normal maps.
- Winner scale and encoder, accepted/rejected candidate counts, quality-gate failures, and structural failures.
- Counts of Maximum 2x, Maximum 4x, forced-below-gate, and failed textures.

The aggregate report must show Magick versus Maximum byte totals, relative reduction, metric means, and how often Maximum is better, equal, or worse per metric/composite error. Size wins never override compatibility or reported visual quality.

## Visual inspection

Generate side-by-side contact sheets containing original, Magick 2x, and Maximum output for a deterministic review subset. Include at least the best, median, and worst Maximum-versus-Magick composite differences, plus alpha/cutout and normal-map cases when present. Label every preview with path, scale, dimensions, byte size, and key metrics.

## Deliverables and acceptance

The experiment produces:

- The three isolated sample trees and a sample manifest.
- Machine-readable per-texture and aggregate reports.
- A concise human-readable summary.
- Labeled visual contact sheets.
- Full absolute paths to all results.

The run is valid only if all 100 selected textures have an original and Magick result, every emitted Maximum VTF decodes and passes structural checks, related VMTs remain byte-identical, and the source addon hash is unchanged. Failures and forced quality exceptions are reported rather than hidden.

No production behavior, UI, release, commit, or push beyond experiment-support code is implied by the result.
