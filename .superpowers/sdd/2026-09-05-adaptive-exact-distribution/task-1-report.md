# Task 1 report — immutable codec methods and exact selection adapter

## Result

Created the real-backend `exact_distribution` adapter and a minimal byte-identical frozen codec namespace. The adapter exposes `encode_options`, `decode_option`, and `verify_vendor`; native/unsupported selection returns an empty list, while corrupt blobs and reconstruction mismatches raise.

## TDD evidence

RED command:

`python -m pytest tests/test_exact_distribution_codecs.py -q`

RED output: collection failed with `ModuleNotFoundError: No module named 'exact_distribution'` (exit 1).

GREEN command:

`python -m pytest tests/test_exact_distribution_codecs.py -q`

GREEN output: `7 passed in 0.08s` (exit 0).

## Changed files

- `exact_distribution/__init__.py`
- `exact_distribution/codecs.py`
- `exact_distribution/vendor-pins.json`
- `tests/test_exact_distribution_codecs.py`
- `_runtime_validation/high_impact_research/vvd_scalar_tiled_fixup_delta_exact_p1/{encoder.py,decoder.py,format.py,runtime_bootstrap.lua.in}`
- `_runtime_validation/high_impact_research/tacrp_compression_portability_v1/{__init__.py,vtf_p2_encoder.py,vtf_p2_independent_decoder.py,vtf_p2_format.py,source_gma.py,engine_overlap.py}`
- `_runtime_validation/high_impact_research/tacrp_compression_portability_v1/vvd_portable/{__init__.py,encoder.py,decoder.py,format.py}`
- `_runtime_validation/high_impact_research/tacrp_compression_portability_v1/runtime_templates/{runtime.lua.in,vtf_decoder.lua,vvd_decoder.lua}`

## Pins and provenance

`vendor-pins.json` records the immutable research source root and SHA-256 for all 17 copied files. `verify_vendor()` recalculates every destination hash and raises on any mismatch. Mechanical `Copy-Item` vendoring checked each source/destination SHA immediately after copy; no frozen source byte was edited.

Codec domains: `vvd-scalar-tiled-fixup-delta-exact-p1/v1`, `vvd-scalar-tiled-fixup-delta-exact-portable/v1`, `vtf-bc-tiled-component-exact-p2/v1`, and `source-asset-lzma-known-size-exact/v1`.

## Self-review

- Tests exercise real codecs for P1 disjoint, portable padded active-LOD-zero and no-fixup layouts, VTF P2, and LZMA byte identity.
- Path selection is independent of original corpus filenames and constrained to supported GMod asset families.
- The 8 MiB VTF runtime cap and 64 MiB general cap are enforced during encode and decode.
- Blob size/hash and reconstructed source size/hash are checked before accepting output; corrupt and trailing LZMA data are rejected.
- P1 incompatibility is decided by its encoder before decode. Decoder/reconstruction failures are not converted into native fallback.
- No builders, corpora, baselines, WPF/GUI files, or existing user changes were modified.

## Concerns

- VTF inputs may produce both the frozen P2 option and the general LZMA option; downstream selection must compare these alternatives rather than assume one option per source.
- This task deliberately does not perform runtime activation or a broad product build, per the brief.

## Review fix round 1

Added strict per-option positive-gain filtering (`blob_size < source_size`) after independent reconstruction verification. Oversized-for-P2 VTF input now skips only P2 and continues through the general LZMA path. Tests additionally validate meaningful VTF runtime metadata fields and parameters.

Fix RED command:

`python -m pytest tests/test_exact_distribution_codecs.py -q`

Fix RED output: `2 failed, 6 passed` (exit 1): the large compressible VTF returned no LZMA option, and an incompressible WAV returned a larger LZMA option.

Fix GREEN command:

`python -m pytest tests/test_exact_distribution_codecs.py -q`

Fix GREEN output: `8 passed in 0.20s` (exit 0).

Fix self-review: gain is checked independently for every VVD, VTF, and LZMA option; decoding and identity verification still happen before filtering, so a reconstruction mismatch cannot be hidden as native fallback. No vendored file changed.
