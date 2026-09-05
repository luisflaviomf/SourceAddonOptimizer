# Task 2 report

## Status

Partial implementation. The candidate filesystem/export API, CLI protocol, path/exclusion gates,
input pinning, option selection, blob deduplication, measured loose-artifact overhead gate, and
whole-candidate fallback are implemented. This is **not ready for engine validation or activation**.

## TDD evidence

RED command:

`python -m pytest -q tests/test_exact_distribution_builder.py tests/test_exact_distribution_runtime.py`

Result: collection failed with two expected `ModuleNotFoundError` errors for the not-yet-created
`exact_distribution.builder` and `exact_distribution.runtime` modules.

GREEN/full focused command:

`python -m pytest -q tests/test_exact_distribution_builder.py tests/test_exact_distribution_runtime.py tests/test_exact_distribution_codecs.py`

Result: `13 passed in 0.33s`.

CLI fixture command executed through `exact_distribution.cli.main`; result emitted all three required
protocol lines and returned zero. Offline status was `OFFLINE_PENDING_GMOD`.

## Interfaces and report schema

- `build_candidate(source: Path, output: Path, work: Path, engine_root: Path, progress=None) -> dict`
- `render_runtime(contract: dict) -> bytes`
- `cli.main(argv: list[str] | None = None) -> int`
- Report schema: `exact-distribution-report/v1`; fields include status, absolute candidate,
  content ID, source/candidate byte totals, encoded/native/unique-blob counts, excluded paths/count/bytes,
  fallback reason, byte-exact and quality flags, installed flag, and returned absolute report path.

## Changed files

- `exact_distribution/builder.py`
- `exact_distribution/runtime.py`
- `exact_distribution/cli.py`
- `tests/test_exact_distribution_builder.py`
- `tests/test_exact_distribution_runtime.py`
- this report

## Limitations and self-review

- The generated Lua is a fail-closed product wrapper around a required host bootstrap; it does not yet
  consume the frozen fullstack decoders itself. Therefore the requested realistic Lua cold/warm/corrupt,
  two-candidate execution tests and actual frozen VVD decoder consumption remain missing.
- Support blobs are currently loose `.dat` files, not frozen-writer support GMAs. Engine-overlap scanning,
  cache GMA headers, mounted pre/post identity checks, sharding limits, and unknown external engine mount
  conflict handling remain missing.
- The builder does not catch decode errors as a native fallback, but it currently catches adapter encode
  `ValueError` as unsupported. A stricter distinction between size/unsupported preflight and independent
  verification failure is still needed.
- Input drift is checked during copy and at close, but the supplied test does not induce a mid-build race.
- Windows cannot create two same-directory names differing only by case, so the case-fold collision branch
  is implemented but not exercised by this focused Windows test suite.
- No GMod/game launch, install, baseline promotion, or source mutation was performed.

## Completion pass — 2026-09-05

Status: **DONE_WITH_CONCERNS / OFFLINE_PENDING_GMOD**. The earlier partial commits
remain evidence. Their host-bootstrap/support/runtime limitations above are
superseded by this completion pass. No host global or external bootstrap is needed.

### Implementation and measured gates

- `runtime.py` reads the authenticated vendored 278-line `runtime.lua.in` and its
  real VVD/VTF decoder files. It substitutes product/CID namespaces, authenticated
  contract pointer, realm guard and decoder. The two validated shared VVD boundary
  substitutions affect only generated Lua: P1 physical/LOD0 equality is conditional
  on no fixups; portable retains unconditional equality. Frozen files are unchanged.
- LZMA support reuses the exact_assets `LUA_DECODER` and `asset_header` mechanisms
  from the read-only tacrpjunto fullstack research. No research-path dependency is
  introduced into the product. VVD/VTF cache headers use the frozen GMA header.
  All caches are fingerprinted through the frozen `_ChunkFingerprint` algorithm,
  including header, ordered source bytes and four-byte footer, without storing an
  expanded cache in the candidate or work directory.
- Actual support GMAs are built and independently audited with the vendored
  `build_gma_file`/`audit_gma_file`; contracts carry exact support and cache chunk
  identities. Identical blobs deduplicate within each family support GMA. Distinct
  codec families retain independent support archives; a digest shared across those
  archives may physically occur in both. All physical archive bytes count in the
  measured gate; `unique_blob_count` is the distinct digest count, not physical copies.
- Options compete by actual blob size, then codec domain for deterministic ties.
  P2 is limited to 8 MiB; a larger VTF can still use the 64 MiB LZMA option. Unsupported,
  unsafe/non-ASCII and empty/over-limit sources remain native. Independent codec
  mismatches propagate; no catch-all `encode_options` ValueError fallback remains.
- Per-family support + authenticated contract + full autorun must be strictly
  smaller than the family's native files. The whole-candidate gate includes the
  manifest. Native fallback is decided before output creation, with no recursive
  deletion. Rejected support evidence remains in the new work folder only.
- Entry/aggregate bounds use the existing 2048 entries, 64 MiB entry and 2 GiB
  aggregate caps. This implementation keeps the cap-bound remainder native with
  `native_family_cap` evidence; it does not introduce startup-oriented sharding.
- Source inventory preserves actual spelling and hashes. Only encoded targets are
  canonical lowercase ASCII; both source and target appear in contracts/decisions.
  Native decision target paths preserve actual spelling. Case-fold collisions are
  rejected. Runtime producer/vendor hashes and source identity are pinned into CID;
  output folder basename participates, while absolute parent location does not.
- Source/work/output lexical and resolved overlaps are rejected, including `..`.
  Windows `FILE_ATTRIBUTE_REPARSE_POINT` catches junctions in roots/ancestors and
  inventory traversal. Existing output/work are refused. Engine overlaps use the
  vendored read-only loose/VPK scanner and remain native, including identical ones.
  The runtime engine allow-map is empty, so unknown external mounted targets fail
  the frozen preabsence checks instead of being assumed absent.
- Nested `.gma`/`.bin` sources and recognized already-packed runtime namespaces are
  refused. DX80/SW exclusion occurs before options/contracts/GMA generation; source
  files are untouched. Final loose and support-GMA gates check zero legacy entries;
  DX90 coverage counts native and reconstructed targets, with identities checked.
- Before success, source hashes are rechecked before publication and at close;
  emitted output is checked against the exact expected closed set, native hashes,
  runtime/contract/manifest hashes, audited support hashes and measured byte total.
  Failure may leave a new incomplete output/work for diagnosis but emits no success
  report or CLI success protocol. There is no destructive cleanup fallback.
- CLI handles redirected Windows streams as UTF-8, including native Unicode paths,
  and supports `python -m exact_distribution.cli` as well as `main(argv)`.

### RED/GREEN evidence

Initial completion RED command:

`python -m pytest -q tests/test_exact_distribution_builder.py tests/test_exact_distribution_runtime.py`

Result: **9 failed, 10 passed in 2.05s**. Concrete failures included nonexistent
host bootstrap, engine overlaps selected, swallowed independent mismatch,
unsafe/native inventory drift and nested archive acceptance. Lupa on Windows emits
an exception diagnostic for intentionally raised Lua errors when pytest's generic
faulthandler is active; subsequent runs disable that pytest plugin, retaining real
Lua exceptions/assertions and the actual LuaJIT 2.1 interpreter.

After implementing full runtime and correcting the boundary emulator to match
Bootil's known-size raw-LZMA consumption: **19 passed in 2.83s**. The emulator's
initial ALONE decoder rejected the explicit-size/EOS combination; product bytes
and frozen decoder did not change for this harness correction.

Additional observed RED/GREEN rounds:

- Redirected ASCII stdout with a Unicode filename: **1 failed, 13 passed**;
  UTF-8 CLI stream setup fixed it; combined suite **23 passed in 2.80s**.
- Same-size emitted autorun corruption: **1 failed, 15 passed**; exact artifact
  hash and closed-set verification fixed the false success.
- Native capitalized `Models/Bar_Caddy.vvd` decision target reported lowercase:
  **1 failed, 15 deselected**; native report mapping corrected while encoded
  targets continue canonical lowercase.

Final focused command:

`python -m pytest -q -p no:faulthandler tests/test_exact_distribution_builder.py tests/test_exact_distribution_runtime.py tests/test_exact_distribution_codecs.py`

Result: **40 passed in 5.52s**. This covers full Lua cold byte equality and warm zero
blob opens/decode/reconstruction writes, all four LZMA asset families, real P2 VTF
selected against LZMA, P1 physical-not-equal-LOD0 fixups, portable padded VVD,
metadata tampering, contract/support/blob/cache corruption, unknown engine mounts,
mounted closed-set/post-mount identity failures, two candidates coexisting, support
alias resolution, dedicated server, real junctions, cap remainder, exact size fallback,
input/output drift, deterministic same-basename output bytes, Unicode copying and CLI.

`test_real_cli_module_process_reports_small_independent_candidate` launches the
actual module in a separate Python process against its newly created small fixture
tree. It returns zero, emits EXACT_PROGRESS/EXACT_REPORT/EXACT_OUTPUT, and produces
one encoded WAV with OFFLINE_PENDING_GMOD. No integration corpus was run.

`git diff --check` on the owned files reported no whitespace errors. Git reports
its existing LF-to-CRLF checkout policy; no frozen bytes were rewritten.

### Public integration paths and schemas

Let `CID` be report `content_id`, `FAMILY` be e.g. `vvd_0000`, `lzwav_0000`,
`vtf_0000`, and `FOLDER` be the requested output basename. Output basename must use
lowercase ASCII letters/digits/underscore/hyphen. It is part of candidate identity;
same basename under different absolute parents yields identical candidate bytes.

- Registry: `_G["CODEX_EXACT_DISTRIBUTION_" .. CID][FAMILY][realm]`, where realm is
  `client` or `server`. Terminal fields include `status`, `family`, `realm`, `mode`
  and failure on error. A READY entry suppresses duplicate execution in that realm;
  duplicate running/failed attempts fail closed. Separate CIDs have separate globals.
- DATA cache: `codex_exact_distribution/CID/realm/FAMILY.gma`.
- DATA temporary cache: same cache path plus `.publishing.dat`.
- DATA receipt: `codex_exact_distribution/CID/realm/FAMILY.receipt.json`.
  Schema is `exact-distribution-runtime-receipt/v1`; status READY or FAILED; mode
  cold/warm/fail_closed; includes CID/family/realm, `counts` (`blob_opens`,
  `decode_invocations`, `reconstruction_writes`, `support_mounts`, `cache_mounts`),
  timings, preabsence flags, support/cache identities and failure. Warm execution
  writes its receipt, but does not rewrite/reconstruct the cache.
- Contract inside candidate: `data_static/codex_exact_distribution/CID/FAMILY_contract.json`.
  Schema `exact-distribution-runtime/v1`, with `codec_family` separate from the
  family/shard identity, `addon_folder`, source/target entry metadata and support/cache
  chunk identities. Autorun embeds the exact contract path, byte length and SHA-256.
- Physical support: `FOLDER/data_static/codex_exact_distribution/CID/FAMILY_support.gma`.
  Primary GAME mount path is `addons/FOLDER/data_static/codex_exact_distribution/CID/FAMILY_support.gma`.
  GAME alias is `data_static/codex_exact_distribution/CID/FAMILY_support.gma`.
- Blob entries inside support: `data_static/codex_exact_distribution/CID/SHA256.dat`.
- Autorun: `lua/autorun/000000_exact_CID_FAMILY.lua`. Texture families run on the
  client. VVD/model/VTX/WAV run on the client and dedicated server, preserving the
  proven realm guards. The candidate manifest is `data_static/codex_exact_distribution/manifest.json`;
  the runtime does not consume that shared marker path.
- Evidence outside addon, in WORK: `candidate-report.json`, `input-pins.json`,
  `engine-overlap.json`, `decisions.json`, plus scratch blobs and support artifacts.
  Report schema remains `exact-distribution-report/v1`; returned and persisted report
  both include `report_path`, candidate/CID, source/candidate/distribution-baseline
  bytes, codec net savings, encoded/native/distinct-blob counts, exclusion paths/count/
  bytes, per-family measured cost/acceptance, fallback reason, DX90 coverage and status.
  `codec_net_savings_bytes` compares against the post-exclusion native baseline, so
  DX80/SW savings are not counted as codec compression.

### Remaining limits and handoff

The test harness executes complete generated autoruns under `lupa.luajit21` with
real SHA-256, JSON, raw LZMA decompression and GMA parsing. It emulates the external
file/DATA/MountGMA APIs; this is **not real-engine proof** of mount priority, timing,
asset loading, rendering or legacy compatibility. GMod validation and manual
activation remain pending. No engine/GUI launch, broad build, real corpus processing,
install or modification of originals/baselines/approved/frozen trees occurred.

Cap-bound assets remain native. Exact export requires an extracted directory:
nested GMA/BIN input requires unpacking first. Case-fold collision rejection is
implemented but its actual two-spelling-files branch cannot be created in the
default case-insensitive Windows fixture directory; real junction branches are tested.
Runtime warm zero-write evidence refers to reconstruction/cache writes, with the
existing receipt write explicitly retained. Cross-family blob duplication is
accounted physically and documented above.

## Review fix round 1 — native/generated Windows directory spelling

The reviewer reproduced valid input `Lua/autorun/native.lua` plus a compressible
WAV failing final artifact identity verification: native publication creates `Lua`,
and Windows reuses that spelling when generated code requests `lua`. The same
defect affected shared `Data_Static` and nested `Autorun` directories.

Builder publication now computes the exact physical artifact path plan from the
retained native file directories before creating the output. Native directories
and filenames retain their spelling. Generated artifact directories sharing those
Windows paths use the planned native spelling; canonical lowercase GAME paths in
the authenticated contracts and Lua remain intact. Publication and final identity
checks use the same explicit plan. The final gate still compares exact spelling,
byte length, SHA-256 and the complete expected file set; it does not substitute
case-insensitive verification or remove any identity gate. POSIX `normcase` is a
no-op, so distinct POSIX directory names are not silently merged by this plan.

RED command:

`python -m pytest -q -p no:faulthandler tests/test_exact_distribution_builder.py -k generated_artifacts_share`

Result: **4 failed, 17 deselected in 1.00s**, all with the expected generated
artifact identity error. Fixtures cover `Lua/autorun`, `lua/Autorun`, `Data_Static`,
and combined `Lua/Autorun` + `Data_Static` native trees.

The same command after implementation: **4 passed, 17 deselected in 0.81s**.
Each regression verifies source bytes unchanged, retained native spelling/bytes,
one selected WAV, exact measured candidate size, and successful full generated Lua
execution that reconstructs the original WAV through its support GMA.

Extended the same-size autorun tampering test to a native `Lua/Autorun` tree too;
the generated artifact remains rejected with no success report. Final command:

`python -m pytest -q -p no:faulthandler tests/test_exact_distribution_builder.py tests/test_exact_distribution_runtime.py tests/test_exact_distribution_codecs.py`

Result: **45 passed in 6.49s**. Files changed in this round: builder, its filesystem
tests, and this report. No runtime/frozen file edits, engine launch, integration
corpus processing or source/approved tree changes. Real GMod validation remains pending.

Integration path clarification: the lowercase support/contract/autorun paths above
are canonical GAME paths. Their physical candidate directory spelling can inherit
retained native `Lua`, `Autorun` or `Data_Static` directories on Windows.
