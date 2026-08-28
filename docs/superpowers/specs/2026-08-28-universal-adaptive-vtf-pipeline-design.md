# Universal adaptive VTF pipeline design

Date: 2026-08-28

Status: written specification approved by the user on 2026-08-28.

## Objective

Replace the current narrow adaptive-resolution experiment with a universal VTF
pipeline. Every spatially reducible texture must be reduced by at least one
resolution level: each axis becomes `max(1, floor(axis / 2))` (`2x` in the
optimizer's terminology), so an axis already at one pixel stays at one. The
classifier then attempts additional consecutive levels and chooses the largest
reduction that satisfies metrics appropriate to the texture's actual semantics.

Uncertainty changes the metric and the maximum accepted level. It must not make
an otherwise reducible texture remain at its original resolution. Level zero is
permitted only when a texture is already at the physical/dimensional floor; it
is never a semantic fallback.

The immediate corpus is the raw `lvs_cars_pack` tree of 1,441,918,651 bytes and
1,152 VTFs. The existing safe baseline, its manifests, the raw source and every
previous candidate remain immutable. The frozen authorities are:

| Authority | Relative location | Files | Bytes | Tree SHA-256 |
|---|---|---:|---:|---|
| Raw addon | `lvs_cars_pack` | 3,827 | 1,441,918,651 | `5f7b31c77e34608c4a40c6b945a7ad24b151be4ad067761c24aa1f93ed908758` |
| Safe optimized baseline | `_runtime_validation/source/addons/lvs_cars_pack_CORPUS_BC3_TO_BC1_SAFE_FINAL_V1` | 3,664 | 438,230,044 | `8e60b8b46744a192fd5ab1ab77848ab9fd4123f1dd042c31d365e16505a698af` |

The raw tree contains 835,331,232 VTF bytes. The safe baseline contains
166,151,944 VTF bytes and 272,078,100 non-VTF bytes. These independent totals
are accounting anchors, not estimates.

## Correction to the previous design

The previous classifier conflated two decisions:

1. whether a texture was safe to touch at all;
2. how far beyond the existing global `2x` reduction it could go.

It allowed only well-understood opaque `$basetexture` and `$detail` consumers to
enter adaptive selection. On the raw addon this changed only 221 VTFs in the
conservative profile and left 931 untouched. That is not the requested
optimizer.

The corrected rule is:

- every reducible VTF receives level 1;
- semantic classification selects the evaluator, not eligibility for level 1;
- levels 2 and above are attempted in order;
- selection stops permanently at the first failed level;
- conservative, balanced and aggressive profiles share the same mandatory
  floor and differ only in their beyond-floor quality thresholds.

For a square BC-compressed texture, halving both axes normally removes about
75% of the top-level pixel payload. `2x` therefore refers to dimensions, not a
promise that the file will be exactly half its original byte size.

## Approaches considered

### A. One generic RGB metric for all VTFs

This is simple and would achieve universal coverage, but it misinterprets normal
vectors, masks, alpha-tested edges, transmission, emissive energy and animated
or cubemap relationships. It is rejected as the production design.

### B. Mandatory `2x` floor plus semantic evaluators

Every reducible texture receives level 1. A semantic resolver assigns one or
more channel-aware evaluators, and those evaluators determine the maximum safe
level above the floor. Unknown or conflicting semantics use a composite
conservative evaluator but still receive level 1. This is the selected design.

### C. Runtime-only visual search

Garry's Mod could render every candidate level and select from screenshots. This
can observe final shader behavior, but it is slow, camera-dependent and hard to
make exhaustive for 1,152 textures. Runtime comparison remains a validation
gate rather than the primary selector.

## Terminology and invariants

- Source level 0: original mip 0 and original dimensions.
- Selected level 1: `max(1, floor(width / 2)) x
  max(1, floor(height / 2))`, using authored mip 1 when valid.
- Selected level N: dimensions
  `max(1, floor(original_width / 2^N)) x
  max(1, floor(original_height / 2^N))`. Existing power-of-two dimensions
  therefore use ordinary right shifts; a one-pixel axis remains one while the
  other axis continues shrinking.
- Spatially reducible: at least one axis is greater than 1 and can be halved
  while preserving a valid VTF dimension and container. An axis already equal
  to 1 remains 1 while the other axis is reduced.
- Dimensionally irreducible: both axes are already 1. This is the only physical
  condition that permits level zero.
- Byte-floor case: dimensions can still be reduced but BC block storage remains
  the same size, such as reducing an already single-block image. The mandatory
  spatial reduction still occurs and is reported with zero payload saving; the
  byte floor does not permit level zero.
- Semantic ambiguity: conflicting, missing or dynamic consumers. This selects
  the conservative composite evaluator. It still attempts every consecutive
  higher level and stops at the first failure; it does not authorize level zero.
- No profile may select a lower level than a stricter profile for the same VTF:
  `conservative <= balanced <= aggressive`.
- A failed level prevents considering every later level, even if a later metric
  happens to improve.

## Source authorities and candidate composition

Two immutable authorities are used for the LVS experiment, and each output path
has exactly one declared source authority:

1. The raw addon supplies every VTF path, original VTF pixels, dimensions, mip
   chains, quality references and every newly materialized VTF payload.
2. `CORPUS_BC3_TO_BC1_SAFE_FINAL_V1` supplies every non-VTF output path and the
   runtime-confirmed model/audio/VTX/VVD fixes.

The raw and safe trees must first be anchored by the hashes above. The 1,152 raw
VTF paths must map one-to-one to the candidate namespace, and the 1,525 raw VMT
paths must be reconciled against the VMT/Lua consumers that will actually ship
from the safe tree. The semantic resolver follows VMT includes, patches,
fallbacks, proxies and Lua-computed names until the consumer graph is closed.
Any unresolved mounted-framework dependency is recorded as an unknown consumer
and receives the composite evaluator; a matching path count alone is not proof
of semantic closure.

When the selected level already exists in the raw authored mip chain, its
payload is promoted directly from the raw VTF; the old globally resized VTF is
not used as a pixel authority. This avoids mistaking matching dimensions for
matching pixels or encoder output.

Any raw VTF without the required authored level is decoded, resized and encoded
by the new transformer. Approved BC3-to-BC1 rules are then reapplied exactly once
to the materialized resolution as a separate audited step. The candidate is not
publishable until the transformer supports the source format, version, frames,
faces, depth and resource layout. Unsupported structure is an implementation
blocker, not permission to copy the original texture silently.

All non-VTF transformations are byte-identical across conservative, balanced
and aggressive candidates. This makes their total sizes directly comparable.
The raw-only candidates around 1.2 GB remain research controls and are never
used as final optimized addons.

## Implementation boundary

The production implementation belongs in
`C:\Users\luisf\Music\teste\GmodAddonCompressor-master\GmodAddonCompressor`.
It extends the existing `MaximumVTFEdit`, VTF planner/parser and Maximum VTF
tooling rather than creating a second optimizer with different rules. Standard
and Magick modes remain unchanged. The current fixed
`VtfResizePolicy(ResolutionFactor: 2, MinimumWidth: 8, MinimumHeight: 8, ...)`
is not the selector for Maximum: Maximum invokes the universal inventory,
semantic evaluators and consecutive-level selector described here.

An isolated corpus driver may live under `_runtime_validation` to build and
audit the three physical trees, but it must call the same production library and
consume the same serialized policy. A Python-only research classifier cannot be
declared finished if the WPF product would make different decisions. Candidate
pixels, manifests and decisions must be reproducible from the product code.

## Components

### 1. Complete VTF inventory

The frozen raw census that the implementation must reproduce before doing any
work is:

- 1,152 VTFs and 835,331,232 bytes;
- 200 DXT1 and 952 DXT5 high-resolution payloads;
- 1,145 version 7.2, four version 7.1, one version 7.4 and two version 7.5;
- 1,145 files with authored mip chains and seven `mip_count = 1`/`NOMIP`
  files that require generated candidates;
- all 1,152 files have at least one axis greater than one, so the expected
  dimensionally irreducible/level-zero count for this corpus is exactly zero;
- all 1,152 files have one frame and depth one; 1,151 have one face and the
  remaining file is the seven-face cubemap;
- one version-7.4 seven-face cubemap;
- three resource-directory files: two version-7.5 files with offset LRS and HRS
  resources, and one version-7.4 file with an offset HRS resource plus an inline
  CRC resource.

Any mismatch blocks the build as corpus drift. These facts do not grant generic
support: every frame, face, depth slice and resource is still parsed and audited
per file.

Parse every VTF from the raw tree and record:

- path, size and SHA-256;
- version, header size and flags;
- width, height, depth and mip count;
- high- and low-resolution formats;
- frame, face and depth-slice counts;
- resource-directory entries, offsets and trailing payloads;
- byte ranges and hashes for every mip/frame/face/depth slice;
- all VMT and Lua consumers from the effective candidate namespace, including
  included/patch materials, dynamic references and unresolved consumers.

The inventory fails on path collisions, malformed ranges, overlapping resources,
truncation, consumer-graph parse errors or a source-tree hash change during
classification.

### 2. Semantic resolver

Resolve every use into one or more semantic classes:

- diffuse/paint/interior;
- detail/tiled surface;
- tangent-space or object-space normal/bump;
- scalar or packed mask;
- alpha-tested/cutout;
- translucent/glass/refract;
- emissive/light;
- decal/logo/text/gauge/plate;
- reflection/environment/cubemap;
- animated/multi-frame;
- unknown or conflicting composite.

Resolution uses VMT roles, shader, flags, alpha semantics, proxies, includes,
Lua references, filenames and channel statistics. A texture used in multiple
ways must satisfy the union of all applicable evaluators. Missing consumers
select the unknown composite evaluator. The resolver emits both a semantic
classification and a proof record: all consumers, how each reference resolved,
which channels each consumer reads and every uncertainty that caused composite
evaluation. It may not infer that alpha is unused merely because a local VMT
does not mention it when the consumer graph is incomplete.

### 3. Universal level-1 transformer

Prefer promotion of an existing authored mip because it preserves the creator's
own filtered data byte-for-byte. When authored mip 1 does not exist, generate it
with a deterministic, semantic-aware downsampler and encoder:

- color data is filtered in the domain declared by its VTF/VMT semantics;
- normal vectors are decoded, averaged and renormalized;
- binary alpha uses coverage-preserving threshold adjustment;
- continuous alpha and masks are filtered independently from RGB;
- emissive data uses the color filter and is accepted only when its separate
  energy gate passes;
- every frame, face and depth slice is transformed consistently;
- low-resolution thumbnails and resource entries are retained or relocated with
  independently audited offsets;
- source format is preserved unless a separate already-approved conversion such
  as BC3-to-opaque-BC1 applies.

For ordinary color/scalar generation, each requested level is generated directly
from decoded source mip 0, never recursively from the preceding candidate.
Magick.NET Q16 AnyCPU 14.10.4 with `LanczosSharp` is the frozen V1 color/scalar
filter. Its netstandard20 managed assembly is 1,126,696 bytes with SHA-256
`5e2ff85094dfd8fa9e15c3f884c3e3ef483bc074538b8c4fc8e1e0aaa643dfd6`;
all loaded native Magick binaries are also hashed into the run policy. RGB marked
as sRGB is linearized before filtering and converted back afterward; channels
classified as linear data remain linear. Continuous transparency is
premultiplied before filtering and returned to straight alpha afterward. Normal
maps instead average decoded vectors over the exact source footprint and
renormalize each result.
Cutouts use the alpha-aware path and adjust the output threshold scale to retain
source coverage at the resolved `$alphatestreference` (0.5 when absent). Encoder
binary SHA-256, version and full arguments are part of the policy fingerprint;
changing a binary, filter or argument creates a different policy/candidate.

Coverage scaling is fixed rather than encoder-dependent guesswork. Compute source
coverage at the resolved threshold, filter unscaled alpha, seed candidates
`{0,1,8}`, then perform 32 bisection steps over `[0,8]`: add the midpoint and set
the low endpoint to it when midpoint coverage is below target, otherwise set the
high endpoint to it. Coverage uses
`round-to-nearest-even(clamp(alpha * scale, 0, 1) * 255)`. Choose the scale with
smallest absolute coverage error; ties choose the smallest `abs(log2(scale))`,
then the smaller scale. Zero is handled as negative infinity for the tie only.
Encode once with the winning scale and measure the decoded BC result. A
beyond-floor level that then misses its cutout gates fails normally; mandatory
level 1 records the deficit but remains the floor.

Filtering-domain resolution is deterministic and ordered:

1. `$ssbump`/`TEXTUREFLAGS_SSBUMP` uses three independent linear scalar channels,
   never the unit-vector route;
2. normal/object-space vector semantics use the vector route regardless of the
   sRGB flag. DXT1 and DXT5 whose alpha is explicitly consumed as a material mask
   use RGB normals: `xyz = 2 * (rgb/255) - 1`. DXT5 with varying alpha, no alpha
   consumer and a normal role/flag uses Source DXT5nm: `x = 2*A/255-1`,
   `y = 2*G/255-1`, `z = sqrt(max(0,1-x*x-y*y))`. Every other proven normal uses
   RGB mapping. Conflicting evidence selects the unknown packed-data evaluator
   and records both RGB and AG diagnostics rather than guessing an angular gate;
3. consumed scalar/packed-mask channels use numeric linear filtering;
4. known diffuse, paint, interior, emissive, glass-tint, decal, logo, text,
   gauge and color-detail roles use linear-light filtering with an sRGB transfer;
5. otherwise, `TEXTUREFLAGS_SRGB` selects the sRGB color route and an absent flag
   selects numeric linear filtering;
6. if consumer roles demand both color and data interpretations, both routes are
   generated. The route with the greatest worst normalized gate margin across
   the union of consumers wins; if neither passes at mandatory level 1, the route
   with the smaller worst normalized deficit wins, with the VTF flag as the final
   tie-break. The forced-floor decision is recorded.

Generated normals are encoded back into the same proven layout. RGB normals
write normalized XYZ to RGB and filter a separately consumed alpha mask as
linear data. DXT5nm writes normalized X to A and Y to G while filtering R and B
independently as linear auxiliary channels so no unproven channel is discarded.

For a minimum gate with threshold `t`, normalized margin is
`(value-t)/max(abs(t),1e-12)`; for a maximum gate it is
`(t-value)/max(abs(t),1e-12)`. The route score is the minimum margin across every
assigned gate and plane. Positive infinity remains positive infinity, NaN always
fails, and exact score ties prefer the route implied by `TEXTUREFLAGS_SRGB`, then
numeric-linear. This same formula defines “deficit”; no implementation-specific
weighting is allowed.

Authored mip promotion performs no encoding. Generated `NOMIP` levels and any
regenerated thumbnail use the embedded `VTFCmd.exe` version 1.0.10, 102,400
bytes, SHA-256
`30d185a6df81d0c5d09c7ecda91a3f49ff183c14a2db12aaa845dc88059ee11e`,
from `VTFEdit.zip` SHA-256
`87d0280223f60be947723cb67076ed47db55221ee69ca60a7f773e4926f11c3c`.
The canonical invocation is
`-file <input.png> -output <isolated-dir> -format <DXT1|DXT5> -alphaformat <same> -version <source-version-clamped-to-7.4>`.
Only the required compressed image block is extracted from the temporary output;
the dedicated writer, not VTFCmd, supplies the authoritative header, flags,
version, resources and `NOMIP` state. Locale is invariant, paths are absolute,
and stdout/stderr, exit code and tool hashes are captured in the manifest.

Mip-chain materialization is explicit:

- for a source with authored mips, selecting level N removes only authored
  levels `0..N-1`, makes authored level N the new top level and copies every
  coarser authored level byte-for-byte, including all frames/faces/depth slices;
- for each of the seven source `NOMIP`/single-mip files, every candidate level is
  generated independently from source level 0, the output remains `NOMIP` with
  `mip_count = 1`, and no useless tail is invented;
- output depth at an authored level is `max(1, source_depth >> N)` and all slices
  belonging to that level are retained; a volume may not be flattened;
- an incomplete or dimensionally inconsistent authored chain is a structural
  blocker, never an excuse to omit an expected coarser level.

Container metadata is rebuilt by a dedicated writer rather than by assuming a
fixed header layout. Low-resolution image handling is tag-aware:

- an absent thumbnail remains absent;
- when its width and height do not exceed the selected high-resolution width and
  height, its dimensions, format and payload are preserved byte-for-byte;
- when either thumbnail axis would exceed the selected high-resolution axis, it
  is regenerated from the selected top level at
  `min(old_low_width, final_width) x min(old_low_height, final_height)`, with a
  one-pixel lower bound and the original low-resolution format. Failure to encode,
  parse or decode it blocks publication; the thumbnail is never silently removed.

Offset LRS/HRS entries are rewritten to their new locations. Inline resources
(resource flag `RSRCF_HAS_NO_DATA_CHUNK`) keep their four-byte values and are
never interpreted as offsets, except that each known tag has its own policy. The
current inline `CRC` value `0x3C66AA52` in
`materials/models/diggercars/mb_w123/w123_int_envmap.vtf` is preserved as opaque
source-provenance metadata because the original authoring source needed to
recompute it is unavailable; it is not claimed to be a checksum of the rewritten
HRS range. This exact tag/value/path policy must pass parser round-trip and a
focused GMod load/render test. Any other inline CRC or inline tag is an
implementation blocker pending a tag-specific rule, not generically copied.

Every other offset resource is copied byte-for-byte as an independently bounded
chunk and receives a relocated offset. Entry order, flags, unknown-resource
payloads, header version, reflectivity, bump scale, frame count, first frame and
texture flags are preserved. The one seven-face cubemap retains all seven faces
at every retained mip. A pre/post table proves whether LRS was preserved or
regenerated, that approved inline values and unknown-resource payload hashes are
identical, and that only declared offsets and HRS/LRS image ranges changed.

The transformer must be deterministic, build to a temporary path, parse and
decode the completed file, and replace only the isolated candidate path. It must
never partially overwrite a VTF or modify either authority tree.

### 4. Beyond-floor evaluators

Each candidate authored or generated top level is decoded and reconstructed to
source-level-0 dimensions with the frozen comparison filter. It is compared to
decoded raw source mip 0, not to a previously optimized file. Metrics are
computed independently for every frame, face and depth slice. A level passes
only when the worst plane passes every required gate; global averaging cannot
hide one broken animation frame or cubemap face.

Common color evaluation gates RGB PSNR, RGB SSIM and Rec.709 edge-energy
retention. Color drift and high-frequency retention remain recorded diagnostics
in V1 but do not participate in acceptance until a later frozen policy gives
them explicit thresholds. Additional evaluators are mandatory by class:

- normals: mean, 95th-percentile and maximum angular error after decoding and
  renormalizing; alpha evaluated separately when used;
- masks/continuous alpha: SSIM, MAE and P99 absolute error per consumed channel;
- alpha-test: thresholded intersection-over-union, edge displacement, connected
  component preservation and coverage;
- emissive: luminance-energy drift and hotspot intersection-over-union; peak loss
  and chroma drift remain recorded diagnostics;
- glass/translucency: transmission/alpha error, tint error and silhouette edge
  preservation;
- decal/text/gauge: stricter RGB structural and edge thresholds;
- detail/tiled data: frequency-energy and seam/tiling consistency;
- cubemap: every face plus cross-face seam consistency;
- animation: every frame plus temporal-difference consistency;
- unknown composite: common RGB plus continuous-alpha gates for every nonopaque
  channel, cutout gates when the alpha histogram is bimodal, and the stricter
  decal/text RGB gates. No channel is treated as disposable.

The initial frozen policy is `universal-vtf-v1`. All inequalities are inclusive.
Rows marked `pp` are absolute percentage-point drift; rows marked `%` are
absolute relative error `abs(candidate/source - 1) * 100`. An evaluator applies
only when its semantic class is present.

For reproducibility, V1 reconstruction is bilinear from the decoded candidate
top level to source dimensions, performed in the same channel domain used by the
evaluator. PSNR uses 8-bit decoded samples and a peak of 255. RGB SSIM is the
minimum of per-channel SSIM values using an 11x11 Gaussian window, sigma 1.5,
K1=0.01 and K2=0.03, a unit-sum kernel, population covariance and reflect-101
border extension. Edge energy is the sum of the mean absolute horizontal and
vertical first differences of Rec.709 luma; retention is candidate/source, with
two zero-energy images defined as 1.0. Alpha SSIM uses the same SSIM constants on
the scalar channel. Normal angular statistics use unit vectors reconstructed
from decoded channels; a zero-length vector fails the level. P95 and P99 use the
nearest-rank convention on ascending values, index `ceil(p*n)-1`, with no
interpolation. Delta-E converts decoded sRGB through XYZ to CIELAB and uses
CIEDE2000, D65 and the 2-degree standard observer. Metric formulas and
comparison-filter identity are serialized into the policy fingerprint.

An alpha plane is treated as cutout-like when at least 95% of samples lie in
`[0, 0.1]` or `[0.9, 1]` and each pole contains at least 1% of samples. Emissive
hotspots use the source threshold `max(0.8 * source_peak, source_P95)` and that
same absolute threshold on the reconstructed candidate. Detail frequency energy
is squared 2D-FFT magnitude outside the central half-width/half-height band,
without an apodization window and excluding DC; wrap-seam error is mean absolute
RGB difference in `[0,1]` between opposite borders,
measured as candidate increase over source. Cubemap seams use the Source VTF
face-orientation table and the same border error. Animation temporal PSNR compares
successive signed RGB difference fields with peak 510. Cutout boundary
displacement is the symmetric Hausdorff distance between thresholded boundaries.
For every retention ratio, source and candidate both zero means 1.0; source zero
with a nonzero candidate fails. For IoU, two empty masks equal 1.0 and only one
empty mask equals 0.0. An emissive plane with zero source peak has zero drift only
when the candidate is also zero and hotspot IoU 1.0. These definitions are part
of `universal-vtf-v1`, not implementation choices.

| Gate | Conservative | Balanced | Aggressive |
|---|---:|---:|---:|
| Common RGB PSNR minimum | 26 dB | 24 dB | 22 dB |
| Common RGB SSIM minimum | 0.950 | 0.925 | 0.900 |
| Common edge-energy retention minimum | 0.50 | 0.40 | 0.30 |
| Normal mean angular error maximum | 3 degrees | 5 degrees | 8 degrees |
| Normal P95 angular error maximum | 8 degrees | 14 degrees | 22 degrees |
| Normal maximum angular error | 25 degrees | 40 degrees | 60 degrees |
| Continuous alpha/mask SSIM minimum | 0.980 | 0.960 | 0.930 |
| Continuous alpha/mask MAE maximum | 0.020 | 0.040 | 0.070 |
| Continuous alpha/mask P99 maximum | 0.080 | 0.140 | 0.240 |
| Cutout top-level coverage drift maximum | 1 pp | 2 pp | 4 pp |
| Cutout retained-level coverage drift maximum | 2 pp | 4 pp | 7 pp |
| Cutout top-level IoU minimum | 0.980 | 0.950 | 0.900 |
| Emissive luminance-energy drift maximum | 5% | 10% | 20% |
| Emissive hotspot IoU minimum | 0.950 | 0.900 | 0.800 |
| Glass mean tint Delta-E 2000 maximum | 2.0 | 4.0 | 6.0 |
| Glass silhouette edge retention minimum | 0.65 | 0.55 | 0.45 |
| Text/decal/gauge RGB PSNR minimum | 30 dB | 27 dB | 24 dB |
| Text/decal/gauge RGB SSIM minimum | 0.970 | 0.950 | 0.920 |
| Text/decal/gauge edge retention minimum | 0.70 | 0.55 | 0.40 |
| Detail frequency-energy retention minimum | 0.70 | 0.55 | 0.40 |
| Detail wrap-seam error increase maximum | 1/255 | 2/255 | 4/255 |
| Cubemap cross-face seam error increase maximum | 2/255 | 4/255 | 8/255 |
| Animation temporal-difference PSNR minimum | 26 dB | 24 dB | 22 dB |

Glass also must pass common RGB and continuous-alpha gates. Emissive, detail,
cubemap and animation evaluators add their rows to common RGB rather than replace
it. Cutout connected components whose source area is at least 16 source pixels
must be retained at 100%, 99% and 97% respectively, and their maximum boundary
displacement after reconstruction is two, four and eight source pixels. These
are part of the frozen policy even though they do not fit compactly in the table.

The profile columns are nested by construction: any level passing conservative
also passes balanced and aggressive. Threshold changes create a new policy
version and new candidates; they never mutate an existing manifest or get
relaxed after seeing which candidate is smallest.

Before the first authoritative corpus selection, the implementation must freeze
`universal-vtf-v1-fixtures.json` and include its SHA-256 in the policy. At a
minimum it contains raw arrays and expected double-precision results for:

- identical 16x16 RGBA `(64,128,192,255)`: PSNR infinity, every SSIM 1.0 and
  zero-edge retention 1.0;
- a 16x16 half-black/half-white source versus constant RGB 128: RGB PSNR
  `6.020533124945522` dB and edge retention 0.0;
- equal binary-alpha half planes: coverage drift 0 pp, IoU 1.0 and Hausdorff 0;
- the alpha sequences `[0,255]` repeated and `[0,128,255]` repeated, which must
  classify respectively as cutout-like and not cutout-like;
- identical, orthogonal and opposite unit-normal pairs: angular errors 0, 90 and
  180 degrees;
- RGB-normal, DXT5nm AG-normal, normal-alpha-mask and SSBump fixtures that force
  each channel-layout branch;
- arrays that lock nearest-rank P95/P99, reflect-101 SSIM borders, source-zero/
  candidate-nonzero retention failure and exact normalized route-score ties;
- cutout alpha input with the complete 32-step coverage-scale candidate list and
  expected winning scale before and after 8-bit quantization;
- a two-frame signed-difference fixture and adjacent cubemap-face border fixture
  with hand-calculated expected values.

The C# production evaluator and one independent audit implementation must agree
with every fixture within `1e-12` for scalar metrics and exactly for counts and
classifications. The fixture file, policy serialization, ImageMagick assembly,
VTFCmd binary and encoder argument template together form the policy fingerprint.
No corpus decision is authoritative before that fingerprint is recorded.

### 5. Selector

For each spatially reducible VTF:

1. Set selected level to 1 unconditionally after structural transformation
   succeeds. Record its quality metrics for visibility, but do not use them as a
   semantic escape back to level zero.
2. Evaluate level 2 with every assigned semantic evaluator.
3. Accept it only if every evaluator passes the active profile.
4. Continue consecutively until the first failure or physical floor.
5. Record every attempted metric and the exact stopping reason.

Profiles do not contain quotas. The system does not force a majority beyond
level 1 merely to improve a statistic; it must nevertheless attempt every valid
higher level for every texture. The resulting level distribution demonstrates
how many genuinely tolerate `4x`, `8x`, `16x` or more.

The selector computes all three profiles from the same attempt matrix. Because
the V1 thresholds are nested, it asserts per path that
`conservative_level <= balanced_level <= aggressive_level`; a violation is an
implementation error, not something to repair by rewriting the report. A manual
runtime downgrade creates a versioned per-path maximum-level cap. The cap is
applied to the failing profile and every looser profile that selected that level
or higher, then all three selections are recomputed and retested. If a regression
requires going below level 1, publication stops for an explicit user decision
because that contradicts the approved universal-floor contract.

### 6. Full optimized candidate builder

Build three new physical trees from immutable authorities:

- universal conservative;
- universal balanced;
- universal aggressive.

All three reuse the same approved model, VTX/VVD, audio, Lua and material-text
pipeline. Their only differences are the VTF levels selected by the versioned
adaptive policy. The frozen 193-path BC3-to-BC1 receipt is reused as a
path/consumer-semantic constraint, not as proof that newly generated raw-derived
pixels equal the previously approved pixels. Format handling is audited after
the selected resolution is materialized.

BC3-to-BC1 is composition, not a second resize pass. Format eligibility is
profile-independent and frozen from the complete raw chain plus the detailed
approved consumer receipt, before any spatial level is selected. The existing
193-path receipt contains three raw-source cases:

- 155 BC3 VTFs whose alpha is physically opaque in every raw mip from level 1
  through the coarsest mip and whose frozen consumer receipt contains no alpha
  read; these may be converted after level selection;
- 32 VTFs that are already ordinary DXT1 in the raw addon; these retain format
  13 and undergo only the selected spatial transformation;
- six exact-path, constant-nonopaque `$detail` RGB-only
  exceptions already approved manually in GMod:
  `lambo_countach/int_seat_occ.vtf`, `lambo_countach/skin.vtf`,
  `mazda_miata/skin.vtf`, and Toyota Supra `skin.vtf`, `skin2.vtf`, `skin3.vtf`
  under `materials/models/diggercars/`; these may be converted only under their
  exact frozen consumer receipts.

The detailed approved manifest's `source_sha256` fields belong to
`CORPUS_STATIC_FLEX_CACHE_SAFE_FINAL_V1`, not to the raw VTF authority, and must
never be compared directly to raw files. Before selection, a new immutable
`bc3-raw-to-approved-v1` mapping is generated and independently audited. Each of
its 193 rows contains the raw full-file hash/size, hashes and alpha statistics for
every raw mip from level 1 onward, the prior safe-source hash/size, the canonical
consumer-receipt hash, raw format, eligibility mode and approved output path. Its
path set must equal the frozen 193-path cohort; its case counts must be exactly
155 raw BC3 physically opaque, 32 raw DXT1 and six raw BC3 semantic exceptions;
the raw hashes must reconcile to the raw tree anchor; and its own SHA-256 becomes
a required policy input. No authoritative candidate may be built until that
mapping and hash are frozen.

An independent bridge audit has established that the old safe pixels are not a
byte authority for this new build: although all 155 physical-opaque BC3 files
have raw mip `n+1` dimensions corresponding to prior-safe mip `n`, zero of the
155 have byte-identical BC3 color blocks across those corresponding chains. The
old 193-path GMod approval therefore proves only that the frozen consumers do not
need discarded alpha and that ordinary opaque BC1 is accepted on those paths. It
does not approve the newly selected or filtered RGB values.

The raw-derived build consequently requires a new independent approval basis:

1. the new mapping proves exact raw hashes, raw formats, raw mip geometry, full
   level-1-and-coarser alpha statistics and consumer-receipt closure;
2. the converter is exhaustively tested per retained BC3 block and proves decoded
   RGB equality before/after BC3-color-to-opaque-BC1 conversion, including endpoint
   remapping when BC1 ordering requires it;
3. the spatial evaluator separately proves the selected raw-to-output RGB quality;
4. every output parses as format 13, preserves all frames/faces/depth slices and
   passes the container/resource audit; and
5. the resulting candidates require focused manual GMod validation before any
   promotion. Prior runtime approval must not be reported as runtime approval of
   these new raw-derived VTFs.

The six exceptions require the exact new raw mapping and exact frozen consumer
receipt from
`_runtime_validation/vtf_novel_v2/corpus_bc3_to_bc1_v1.manifest.json`
(SHA-256 `6eda827ee1fb1bddb85a1aabeea1a0181722947139bc4d58776a529c15d14b15`).
They never generalize to a new nonopaque asset. Any of the 193 paths whose source
mapping or consumer receipt differs retains BC3 and blocks baseline-equivalence
accounting until reviewed. Every other BC3 remains BC3 in this hypothesis even
if a new scan suggests opaque alpha; generalization is separate work.

For one of the 161 eligible raw BC3 files, the builder first materializes the
selected spatial level in BC3, then transfers each retained BC3 color block into
ordinary opaque BC1. The eight color bytes are copied directly when their endpoint
ordering is already legal for opaque BC1; otherwise endpoints and the corresponding
two-bit indices are remapped without changing decoded RGB. The parser must report
DXT1 format 13, never DXT1 one-bit-alpha format 20, and every decoded RGB texel
must be pixel-exact across this format-only step. Because eligibility is frozen
from the full raw chain, the three profiles differ only in selected resolution,
not in the final format decision for these 193 paths. The 32 raw DXT1 files bypass
format conversion. The manifest records resolution and format transformations
separately, so their savings cannot be counted twice.

Publication is atomic per candidate. Destinations must not exist, the source
trees are inventoried before and after, no hardlinks are shared, and only
manifest-declared paths may differ.

### 7. Manifest and accounting

The manifest contains one row for every VTF, not just changed files:

- source semantic class and consumers;
- original and final dimensions;
- original and final formats;
- mandatory-floor method (`authored_mip` or `generated`);
- selected level per profile;
- every attempted metric and stop reason;
- source/output hashes and bytes;
- dimensional-floor, byte-floor or blocker reason, if applicable;
- interaction with BC3-to-BC1;
- savings by file, family, semantic class and level.

Corpus totals must report three non-overlapping views:

1. raw addon (1,441,918,651 bytes) to complete candidate;
2. safe baseline (438,230,044 bytes) to complete candidate, which is the
   incremental gain delivered by this hypothesis;
3. raw VTF total (835,331,232 bytes), safe-baseline VTF total (166,151,944
   bytes), and final VTF total side by side.

Each view includes before, after and savings in bytes, decimal MB, binary MiB and
percent. Reports also include resolution-only saving, format-only BC3-to-BC1
saving and their composed total without double counting, plus distributions for
levels 0, 1, 2, 3 and higher. Level-zero count must equal the independently
proven dimensionally irreducible count (zero in this frozen corpus); there may be
no silent semantic skips.

## Failure handling

- Semantic uncertainty selects the composite evaluator. If a higher level fails
  it, that texture falls back to level 1, not the original dimensions.
- A beyond-floor metric failure stops that texture at the previous accepted
  level without affecting other textures.
- A malformed or unsupported reducible VTF blocks publication until its
  transformer is implemented or corrected.
- A deterministic rebuild mismatch blocks publication.
- A manual GMod regression causes only the responsible texture to step back one
  level at a time, never below level 1 unless the user explicitly revokes the
  universal-floor requirement for a demonstrated engine incompatibility.
- Every rejected higher level and runtime downgrade remains preserved in the
  report.

## Test strategy

Implementation follows red-green-refactor.

Unit tests cover:

- the universal level-1 invariant and prohibition on semantic level-zero skips;
- monotonic profile selection and stop-first-failure;
- DXT1/DXT5 alpha palettes across all mips;
- sRGB/linear color filtering;
- normal-vector decoding, averaging and renormalization;
- continuous and binary alpha coverage;
- mask channels, emissive energy and feature-edge metrics;
- authored-chain promotion without re-encoding retained payloads;
- independent direct-from-source generation for all seven `NOMIP` files while
  preserving `NOMIP` and `mip_count = 1`;
- multiple frames, all seven cubemap faces, depth slices and resource directories;
- offset LRS/HRS relocation, byte-identical thumbnail preservation, inline CRC
  path-specific preservation, thumbnail regeneration below 16x16 and
  unknown-resource chunk relocation;
- exact agreement between production and independent implementations on the
  frozen policy fixtures;
- raw-to-prior-safe BC3 receipt mapping, including the exact 155/32/6 raw-format
  partition, the observed zero-of-155 corresponding color-block equivalence and
  a regression test that rejects a prior safe-source hash when it is incorrectly
  presented as a raw hash or pixel authority;
- exhaustive BC3-color-to-opaque-BC1 block fixtures covering both endpoint-order
  branches and proving decoded RGB equality for every texel;
- deterministic encoding, range relocation and parse round trips;
- dimensionally irreducible images and BC byte-floor cases;
- stale source hashes, traversal, collisions and transaction failures.

Corpus tests require:

- exactly 1,152 manifest rows and complete raw path coverage;
- exact reproduction of the format/version/mip/resource census and both frozen
  authority hashes before processing;
- a selected level of at least 1 for every reducible VTF;
- no unsupported reducible file at publication time;
- profile nesting for every path;
- exact changed-path, size and hash accounting;
- separate reconciliation of raw-to-final, safe-to-final and VTF-only totals;
- format-13 opaque DXT1 proof and no format-20 output for every reapplied
  BC3-to-BC1 conversion;
- exact 193-path receipt closure (155 raw BC3 physically opaque, 32 raw DXT1 and
  six frozen nonopaque raw BC3 exceptions), a frozen raw-to-approved mapping hash
  and identical final-format decisions across profiles;
- identical non-VTF inventories across all three candidates;
- deterministic second builds;
- preservation of the approved VTX `0x07` and static-flex/cache model rules by
  byte identity.

Manual GMod validation starts with a stratified sample covering every semantic
class, every selected level, generated-mip cases, the largest savings and shared
textures. It compares close, medium and far views, skins, interiors, glass,
lights, gauges, wheels and LOD transitions. The sample explicitly includes every
regenerated-thumbnail path and the W123 seven-face cubemap carrying the frozen
inline CRC. Only after the sample passes is the complete candidate presented for
broader manual validation.

## Rollback and preservation

- Do not modify or delete the raw addon.
- Do not modify `CORPUS_BC3_TO_BC1_SAFE_FINAL_V1` or any approved manifest.
- Do not promote over an approved baseline automatically.
- Keep raw-only candidates as isolated controls.
- Every new tree has a source anchor, tree hash, manifest hash and rollback path.

## Non-goals

- No new VVD/VTX/model/audio hypothesis is introduced by this work.
- No mip-tail truncation or VTF deduplication is combined into the first
  universal candidate.
- No GMod harness expansion is required before the offline candidates exist.
- No claim that a lower-resolution image is pixel-identical to the original.
- No forced percentage of textures beyond level 1; quality metrics decide after
  every texture has actually been evaluated.

## Completion criteria

The implementation milestone is complete only when:

- all 1,152 VTFs appear in the frozen census and manifest;
- every spatially reducible VTF is at least level 1;
- every valid higher level was attempted with the correct semantic evaluators;
- level-zero rows are independently proven dimensionally irreducible;
- conservative, balanced and aggressive selections are monotonic;
- three full optimized candidates are built from the same approved non-VTF
  authority;
- exact savings and level/semantic distributions are reported;
- all offline tests and a deterministic rebuild pass;
- the original and every approved baseline remain byte-identical;
- candidates remain unpromoted until focused manual Garry's Mod validation.
