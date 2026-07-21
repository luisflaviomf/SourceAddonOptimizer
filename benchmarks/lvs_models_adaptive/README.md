# LVS Models Maximum adaptive benchmark

This harness freezes eight real model families from workshop addon `3027256228` and runs three isolated lanes: current Models Normal Safe (`0.75`), historical Maximum at exact commit `8812c7a`, and Maximum Adaptive v2. Every lane gets a copied model-only fixture and separate work/output/cache paths. The framework addon is a read-only reference resolver; no framework file is copied into a result.

Comparable model bytes always exclude `*.dx80.vtx` from both denominators. Removed DX80 bytes are reported separately. Every final MDL must retain a valid matching VVD and DX90 VTX, must contain no DX80 VTX, and must preserve the compiled inventory/material contract.

Run development first, calibrate and freeze the profile, then run holdout without changing code or thresholds. The exact commands are documented in `docs/superpowers/plans/2026-07-21-models-maximum-adaptive-v2.md`.
