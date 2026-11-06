# Adaptive Exact Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose authenticated adaptive exact distribution compression in the existing WPF product without changing approved codecs or originals.

**Architecture:** Package byte-identical codec sources under their existing Python import namespace and wrap them in a small `exact_distribution` package. A new worker command and WPF panel export independent candidates; current native quality optimization stays separate.

**Tech Stack:** Python 3.11 standard library, existing vpk dependency, WPF net6.0-windows, existing PyInstaller tool packaging, GLua runtime template.

**Spec:** `docs/superpowers/specs/2026-09-05-adaptive-exact-distribution.md`

## Global Constraints

- All approved/frozen baselines, pointer, codecs, manifests, original addons and rollbacks stay read-only.
- No legacy `gui/` changes. No automatic baseline promotion or addon activation.
- Frozen codec files are copied byte-for-byte; only new wrappers change.
- Every selected blob must independently decode equal to input bytes and SHA-256.
- Output/work/source must not overlap; existing output and reparse paths fail before writes.
- Exclude `.dx80.vtx` and `.sw.vtx` only from new output; preserve `.dx90.vtx` and source.
- No filename/model-specific codec exceptions. Unsupported/no-net-gain inputs remain native.
- Exact distribution and lossy native quality optimization are separate claims and controls.
- No universal-default claim until integrated cars/weapons/props and engine gates pass.
- Preserve dirty checkout changes. Commit only newly owned files; mixed existing files remain uncommitted for review against pre-task snapshots.

### Task 1: Package immutable codec methods and exact selection adapter

**Files:** Create `exact_distribution/__init__.py`, `exact_distribution/codecs.py`, `exact_distribution/vendor-pins.json`, minimum `_runtime_validation/high_impact_research/` codec modules/templates, `tests/test_exact_distribution_codecs.py`.

**Interfaces:** Produces `encode_options(target_path: str, source: bytes) -> list[dict]`; each option carries `family`, `codec_domain`, `blob`, `source_size`, `source_sha256`, `blob_size`, `blob_sha256`, `codec_meta` or VTF `layout`/`codec_parameters` where needed. Produces `decode_option(option: dict) -> bytes`, `verify_vendor() -> dict[str,str]`. Empty options mean native. Unexpected independent reconstruction mismatch must raise, not become fallback.

**References:** Research root is `C:/WorkshopDL/steamcmd/steamapps/workshop/content/4000/3027256228/_runtime_validation/high_impact_research`. Copy only P1 encoder/decoder/format, portable encoder/decoder/format/init, VTF P2 encoder/independent_decoder/format, runtime templates, source_gma and engine_overlap helpers. Portable absolute Python imports require retaining the existing package namespace, not editing its frozen bytes. LZMA source algorithm is in `tacrpjunto_fullstack_v1/exact_assets.py`; metadata examples in portability `candidate_builder.py:115` onward. P1 raises ValueError for unsupported layout; inspect compatibility before encoding and do not catch decoder failure as incompatibility. VTF runtime source limit 8 MiB, general exact assets 64 MiB.

- [ ] Write tests for P1 disjoint, portable no-fixup and padded with active LOD zero, VTF P2, LZMA full byte identity, corrupt/trailing blob rejection, unsupported/native, output caps and arbitrary renamed paths. Example behavior:

```python
raw = b'RIFF' + b'abc123' * 20000
options = encode_options('sound/example/test.wav', raw)
assert options and all(decode_option(item) == raw for item in options)
assert encode_options('lua/example.lua', b'print(1)') == []
```

- [ ] Run `python -m pytest tests/test_exact_distribution_codecs.py -q`; record RED before implementation.
- [ ] Vendor the minimum frozen files using byte-preserving copy with source/destination SHA checks; this is vendoring, not editing source. Add provenance manifest and wrapper. Do not import corpus-specific builders.
- [ ] Run focused tests and vendor pin verification; record GREEN and self-review. Commit only task-owned new files.

### Task 2: Deterministic independent candidate builder and generic runtime wrapper

**Files:** Create `exact_distribution/builder.py`, `exact_distribution/runtime.py`, `exact_distribution/cli.py`, `tests/test_exact_distribution_builder.py`, `tests/test_exact_distribution_runtime.py`.

**Interfaces:** Consumes Task 1 options. Produces `build_candidate(source: Path, output: Path, work: Path, engine_root: Path, progress=None) -> dict`, and `cli.main(argv: list[str]) -> int`. CLI: `exactpack SOURCE --output OUTPUT --work WORK --engine-root GMOD_INSTALL`. Emit `EXACT_PROGRESS: phase|index|total|path`, `EXACT_REPORT: absolute-json-path`, `EXACT_OUTPUT: absolute-candidate-path`.

**Path mapping:** Inventory retains each original spelling. Candidate runtime target paths use canonical lowercase ASCII GAME paths while reading the original source spelling. Reject case-fold collisions; never choose one silently. Native copies retain their spelling. Record source_path and target_path separately when case differs. This is necessary for real props such as `Bar_Caddy.vvd`, not a per-model exception. Unsafe/non-ASCII transport paths remain native, not renamed arbitrarily.

**Runtime:** Reuse pinned portability runtime template. Apply already validated fullstack shared VVD header correction: conditional P1 physical/LOD0 equality only for no-fixup, retain portable unconditional equality. Replace experimental namespace and registry with content-ID-isolated product namespaces. Family cache/support can be sharded only to respect existing 2048 entries/64 MiB entry/2 GiB aggregate caps, not for startup optimization. Include shard in family identity where needed, with a separate codec-family field for dispatch. Support GMA uses frozen writer, cache headers use existing P1/VTF header or exact_assets asset header. Preserve exact chunk verification, target preabsence and post-mount identity checks. Scan engine overlaps read-only and keep them native; engine mounts outside known roots remain a runtime fail-closed conflict, not an assumed clean scan. No permission to weaken gates.

- [ ] Write failing real filesystem tests: source unchanged, deterministic same basename outputs, duplicate blob dedup, all per-file bytes restored, output overlap/reparse/existing output refused, DX80/SW removed only in output, unsupported formats native, no-gain after runtime overhead native, no success after input drift, prepacked runtime refused.
- [ ] Execute RED before implementation. Implement inventory, per-file selection, support contracts and exact measured net-size gate. Reject a family/shard if actual support+contract+autorun cost is not lower than its native files. Account final manifest overhead at whole-candidate gate; fall back to a native copied tree with exclusions when exact transport would enlarge it. Keep report/evidence outside addon.
- [ ] Test runtime behavior with the existing Lua test harness where available, including two independent addons, corrupt rejection, cold byte equality and warm zero decode/writes. Do not treat source substring tests as runtime proof. Source runtime remains unchanged; generated wrapper is new product code.
- [ ] Run CLI against small independent fixture trees. Record offline status and any engine validation still pending. Commit only task-owned files and report tests.

### Task 3: WPF/worker integration, current packaging and verification

**Files:** Modify `worker/worker_main.py`, `pyinstaller/worker.spec`, `pyinstaller/package_wpf_tools.ps1` if needed for fresh packaging, `GmodAddonCompressor-master/GmodAddonCompressor/MainWindow.xaml`, `MainWindow.xaml.cs`; create partial `MainWindow.ExactDistribution.cs`, `Systems/Optimizer/ExactDistributionRunner.cs`, focused integration tests/docs. Touch settings/context for the exact-stage opt-in and persisted engine path using the established UI pattern. Bump `Systems/Tools/ToolPaths.cs` ToolVersion to a new version so previous extracted tools remain usable by the preserved old release.

**Interfaces:** Worker routes `exactpack` to Task 2 CLI. New WPF exact distribution panel takes input addon, new output directory and engine directory. Existing quality tabs stay functional and separate; user can select their output as exact-stage input. ArgumentList and cancellation kill only owned worker process tree. Logs/progress/report/output controls follow existing runners. No game launch/install from export button.

**Combined workflow:** Add optional `Distribuição exata no final (experimental)` to Pipeline, default off. Only after both native stages succeed, run exact export on the native output with a fresh sibling output folder; leave the native intermediate intact. Add PipelineStage.ExactDistribution cancellation routing and separate exact result/report/output summary fields. Opening final pipeline output should resolve the exact candidate only when its worker succeeds; failure/cancel must never report overall OK. Do not change existing native quality settings or apply car-specific policy automatically. The standalone panel remains available to users who want no quality optimization.

- [ ] Write failing CLI routing/argument/progress/cancellation tests against a small actual input; inspect existing WPF testing projects for runner behavior tests. Preserve pre-task snapshots of mixed files.
- [ ] Implement new worker route and WPF panel with clear exact/experimental status and exclusions/runtime/cache explanation. Enforce new output, report failures and prevent concurrent self-overlapping export. Add correct Python module and template data to worker spec, verify bundled pins at runtime.
- [ ] Build fresh worker then use official `build_release_wpf.ps1`; before its cleanup preserve the previous published executable/embedded zip if needed, validate exact targets and disk headroom. Do not restore deleted experiments.
- [ ] Execute fresh packaged worker on real car/weapon/prop subsets in independent output roots, measure bytes and independently reconstruct. Engine corrupt/cold/warm and WPF published UI checks must be recorded honestly, not inherited from old research receipts. Do not overwrite active manual candidate or official pointers.
- [ ] Run focused Python/WPF tests and actual publish, independent final code review, document final executable and remaining gates. Commit only new task-owned files; retain mixed user edits uncommitted.
