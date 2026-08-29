# Universal VTF Persistent Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a persistent, content-addressed, fail-closed Universal VTF cache that imports the 1,152 verified prior results, resumes per file, and reduces identical corpus builds from 7h30m55.624s to warm replay with 1,152 hits and zero image recalculations.

**Architecture:** Separate immutable analysis checkpoints from completed three-profile checkpoints. Keys are SHA-256 of canonical input identities; artifacts live in a deduplicated SHA-256 blob store, entries publish atomically, and legacy named-NaN manifests can enter only through a hash-pinned importer. The builder resolves semantics, attempts a completed hit, then an analysis hit, and invokes the existing materializer/metric path only on a verified miss.

**Tech Stack:** C#/.NET 6 WPF assembly, `System.Text.Json`, SHA-256, xUnit, existing Universal VTF parser/materializer/selector/auditors, Python 3 independent auditor, PowerShell orchestration on Windows.

**Spec:** `docs/superpowers/specs/2026-08-29-universal-vtf-persistent-cache-design.md`

## Global Constraints

- Raw tree remains 3,827 files / 1,441,918,651 bytes / `5f7b31c77e34608c4a40c6b945a7ad24b151be4ad067761c24aa1f93ed908758`.
- Safe tree remains 3,664 files / 438,230,044 bytes / `8e60b8b46744a192fd5ab1ab77848ab9fd4123f1dd042c31d365e16505a698af`.
- Policy, fixture, toolchain, and bridge hashes remain exactly those frozen in the spec.
- Do not alter compression thresholds, semantic policy, codec policy, selected levels, or final VTF bytes.
- Never treat malformed, incomplete, stale, conflicting, or unverifiable cache state as a hit.
- Never modify or delete raw, safe, the legacy DIRTY_NAN production, or the interrupted pre-cache recovery.
- Cache statistics stay outside official candidate manifests.
- Production outputs always target absent directories and publish only after both existing internal audits pass.
- Use `apply_patch` for source and documentation edits; use physical copies from cache blobs into candidate staging trees.
- Follow RED → GREEN → full-suite verification for every production behavior.

---

### Task 1: Shared Canonical JSON and Floating-Point Contract

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCanonicalJson.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfManifestWriter.cs`
- Test: `experiments/VtfUniversal.Tests/UniversalVtfCanonicalJsonTests.cs`
- Test: `experiments/VtfUniversal.Tests/UniversalVtfManifestWriterTests.cs`

**Interfaces:**
- Produces: `UniversalVtfCanonicalJson.Serialize<T>(T value)`, `Deserialize<T>(ReadOnlySpan<byte> bytes)`, and `Sha256(ReadOnlySpan<byte> bytes)`.
- Produces: shared `UniversalVtfManifestDoubleConverter` with NaN ↔ JSON null and infinities ↔ named strings.
- Consumed by: manifest writer, cache receipts, run receipts, and legacy importer.

- [ ] **Step 1: Write failing canonical JSON tests**

Add tests proving property sorting, deterministic dictionary output, null/NaN round-trip, infinity round-trip, named-NaN rejection, and byte-identical second serialization:

```csharp
[Fact]
public void CanonicalRoundTripWritesNanAsNullAndRejectsNamedNan()
{
    var value = new CanonicalFixture(
        new Dictionary<string, double> { ["z"] = double.NaN, ["a"] = 1 },
        double.NegativeInfinity);

    byte[] first = UniversalVtfCanonicalJson.Serialize(value);
    Assert.DoesNotContain("\"NaN\"", Encoding.UTF8.GetString(first));
    Assert.Contains("\"z\":null", Encoding.UTF8.GetString(first));
    CanonicalFixture loaded = UniversalVtfCanonicalJson.Deserialize<CanonicalFixture>(first);
    Assert.True(double.IsNaN(loaded.Values["z"]));
    Assert.Equal(first, UniversalVtfCanonicalJson.Serialize(loaded));
    Assert.Throws<InvalidDataException>(() =>
        UniversalVtfCanonicalJson.Deserialize<CanonicalFixture>(
            Encoding.UTF8.GetBytes("{\"values\":{\"z\":\"NaN\"},\"margin\":0}")));
}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter FullyQualifiedName~UniversalVtfCanonicalJsonTests --logger "console;verbosity=minimal"
```

Expected: compile failure because `UniversalVtfCanonicalJson` does not exist.

- [ ] **Step 3: Implement canonical serialization and migrate the manifest writer**

Implement a generic serializer that serializes with the shared converter, recursively sorts JSON object properties ordinally, preserves array order, and deserializes only canonical typed values. Replace the private canonical writer and private double converter in `UniversalVtfManifestWriter` with this shared implementation. Keep `UniversalVtfManifestStore.Load` byte-canonicality enforcement unchanged.

```csharp
internal static class UniversalVtfCanonicalJson
{
    internal static byte[] Serialize<T>(T value);
    internal static T Deserialize<T>(ReadOnlySpan<byte> bytes);
    internal static string Sha256(ReadOnlySpan<byte> bytes);
    internal static JsonSerializerOptions SerializerOptions { get; }
}
```

- [ ] **Step 4: Run focused and manifest tests GREEN**

Run:

```powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --filter "FullyQualifiedName~UniversalVtfCanonicalJsonTests|FullyQualifiedName~UniversalVtfManifestWriterTests" --logger "console;verbosity=minimal"
```

Expected: all selected tests pass; no serialized literal `"NaN"`.

- [ ] **Step 5: Commit**

```powershell
git add -- GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCanonicalJson.cs GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfManifestWriter.cs experiments/VtfUniversal.Tests/UniversalVtfCanonicalJsonTests.cs experiments/VtfUniversal.Tests/UniversalVtfManifestWriterTests.cs
git commit -m "refactor: share canonical universal VTF JSON"
```

### Task 2: Preparation Identities and Semantic Proof

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfPreparationIdentity.cs`
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfSemanticProof.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusBuilder.cs`
- Test: `experiments/VtfUniversal.Tests/UniversalVtfPreparationIdentityTests.cs`

**Interfaces:**
- Produces: `UniversalVtfAnalysisIdentity`, `UniversalVtfCompletionIdentity`, `UniversalVtfCachedSelection`, `UniversalVtfPreparationFingerprint.Current`, and key functions.
- Produces: `UniversalVtfSemanticProof.Compute(UniversalVtfSemanticDecision semantics)`.
- Consumes: canonical JSON from Task 1 and current inventory/semantic records.

- [ ] **Step 1: Write failing key-stability and invalidation tests**

Build a base identity fixture and use record `with` mutations. Assert that identical values and timestamp-only changes keep the key, while each relevant field changes it:

```csharp
[Theory]
[InlineData("raw")]
[InlineData("path")]
[InlineData("algorithm")]
[InlineData("policy")]
[InlineData("fixture")]
[InlineData("toolchain")]
[InlineData("bridge")]
[InlineData("semantics")]
[InlineData("parameters")]
public void RelevantAnalysisMutationChangesKey(string field)
{
    UniversalVtfAnalysisIdentity baseline = IdentityFixture.Analysis();
    UniversalVtfAnalysisIdentity changed = IdentityFixture.Change(baseline, field);
    Assert.NotEqual(
        UniversalVtfCacheKey.Analysis(baseline),
        UniversalVtfCacheKey.Analysis(changed));
}

[Fact]
public void SafeAndCapsChangeCompletionButNotAnalysis()
{
    var analysis = IdentityFixture.Analysis();
    var first = IdentityFixture.Completion(analysis);
    var second = first with { RuntimeCapsSha256 = Sha('c') };
    Assert.Equal(first.AnalysisKey, second.AnalysisKey);
    Assert.NotEqual(UniversalVtfCacheKey.Completion(first),
                    UniversalVtfCacheKey.Completion(second));
}
```

- [ ] **Step 2: Run focused test and verify RED**

Expected: compile failure for missing identity and key types.

- [ ] **Step 3: Implement exact identities and preparation fingerprint**

Use records containing every field listed in the spec. `UniversalVtfPreparationFingerprint.Current` hashes a canonical descriptor containing explicit component IDs for materializer, downsampler, decoder, metrics, selector, thumbnail, and container writer. Extract the existing private `ConsumerProof` logic unchanged into `UniversalVtfSemanticProof.Compute` and update the builder to call it without changing behavior.

```csharp
internal sealed record UniversalVtfAnalysisIdentity(
    string Schema,
    string AlgorithmFingerprint,
    string RelativePath,
    string RawSha256,
    long RawBytes,
    UniversalVtfStructuralIdentity Structure,
    string ConsumerProofSha256,
    IReadOnlyList<string> SemanticClasses,
    UniversalVtfFilterRoute SemanticRoute,
    string PolicyFingerprint,
    string FixtureSha256,
    string ToolchainSha256,
    string BcBridgeSha256,
    string BridgeRowSha256,
    string CandidateParametersFingerprint);

internal sealed record UniversalVtfCompletionIdentity(
    string Schema,
    string AnalysisKey,
    string SafeSha256,
    long SafeBytes,
    string RuntimeCapsSha256,
    IReadOnlyDictionary<UniversalVtfProfileKind, UniversalVtfCachedSelection> Selections);

internal sealed record UniversalVtfCachedSelection(
    UniversalVtfProfileKind Profile,
    int Level,
    UniversalVtfFilterRoute Route);
```

Represent absence as the exact lowercase value `none`; validate all real SHA values as 64 lowercase hexadecimal characters.

- [ ] **Step 4: Run focused tests GREEN and current builder tests**

Expected: identity tests pass and existing corpus builder tests remain unchanged.

- [ ] **Step 5: Commit**

```powershell
git add -- GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfPreparationIdentity.cs GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfSemanticProof.cs GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusBuilder.cs experiments/VtfUniversal.Tests/UniversalVtfPreparationIdentityTests.cs
git commit -m "feat: fingerprint universal VTF preparation inputs"
```

### Task 3: Portable Analysis and Completion Receipts

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCacheContracts.cs`
- Test: `experiments/VtfUniversal.Tests/UniversalVtfCacheContractTests.cs`
- Test support: `experiments/VtfUniversal.Tests/Fixtures/UniversalVtfCacheFixture.cs`

**Interfaces:**
- Produces: portable attempt matrix, artifact reference, analysis receipt, profile output, completion receipt, coverage enum, `UniversalVtfCachedAsset`, and conversion methods.
- Consumes: selector records and identities from Task 2.

- [ ] **Step 1: Write failing full-metric round-trip tests**

Construct a two-level composite-route matrix with valid, failed, NaN, positive-infinity, and negative-infinity metrics. Assert path-free canonical bytes, reconstruction, and selection equality:

```csharp
[Fact]
public void PortableMatrixRoundTripsEveryMetricAndReplaysSelection()
{
    UniversalVtfAttemptMatrix runtime = UniversalVtfCacheFixture.Matrix();
    UniversalVtfCachedAttemptMatrix portable =
        UniversalVtfCachedAttemptMatrix.FromRuntime(runtime);
    byte[] bytes = UniversalVtfCanonicalJson.Serialize(portable);
    Assert.DoesNotContain(_tempRoot, Encoding.UTF8.GetString(bytes));
    UniversalVtfAttemptMatrix replay = portable.ToRuntime(
        (level, route) => CachePath(level, route));
    Assert.Equal(
        UniversalVtfSelector.Select(runtime, UniversalVtfRuntimeCaps.Empty),
        UniversalVtfSelector.Select(replay, UniversalVtfRuntimeCaps.Empty),
        UniversalVtfCacheFixture.SelectionComparer);
}
```

- [ ] **Step 2: Run focused test and verify RED**

Expected: compile failure for missing cache contracts.

- [ ] **Step 3: Implement portable contracts and strict validation**

Define:

```csharp
internal enum UniversalVtfCacheCoverage
{
    FullAttempts,
    ImportedExactProfileFinals
}

internal sealed record UniversalVtfArtifactReference(
    string Sha256,
    long Bytes,
    int Width,
    int Height,
    int Depth,
    int Frames,
    int Faces,
    int MipCount,
    int Format);

internal sealed record UniversalVtfCachedArtifactKey(
    int Level,
    UniversalVtfFilterRoute Route);

internal sealed record UniversalVtfAnalysisReceipt(
    string Schema,
    string AnalysisKey,
    UniversalVtfAnalysisIdentity Identity,
    UniversalVtfCacheCoverage Coverage,
    UniversalVtfCachedAttemptMatrix Matrix,
    IReadOnlyDictionary<UniversalVtfCachedArtifactKey, UniversalVtfArtifactReference> Artifacts);

internal sealed record UniversalVtfCompletedProfile(
    UniversalVtfProfileKind Profile,
    UniversalVtfCachedSelection Selection,
    UniversalVtfGateResult? WorstGate,
    UniversalVtfArtifactReference FinalArtifact,
    long SpatialBytes,
    string SpatialSha256,
    bool ByteFloor,
    string? Blocker,
    bool LowResolutionImageRegenerated,
    int ResourceCount);

internal sealed record UniversalVtfCompletionReceipt(
    string Schema,
    string CompletionKey,
    UniversalVtfCompletionIdentity Identity,
    IReadOnlyDictionary<UniversalVtfProfileKind, UniversalVtfCompletedProfile> Profiles);

internal sealed record UniversalVtfCachedAsset(
    UniversalVtfAnalysisReceipt Analysis,
    UniversalVtfCompletionReceipt Completion,
    IReadOnlyDictionary<string, string> ArtifactSources);
```

Validation requires consecutive levels beginning at one, unique routes, all three profiles, nested levels, complete metric records, and no absolute paths.

- [ ] **Step 4: Run focused tests GREEN**

Expected: portable receipts round-trip byte-identically and replay the selector.

- [ ] **Step 5: Commit**

```powershell
git add -- GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCacheContracts.cs experiments/VtfUniversal.Tests/UniversalVtfCacheContractTests.cs experiments/VtfUniversal.Tests/Fixtures/UniversalVtfCacheFixture.cs
git commit -m "feat: define universal VTF cache receipts"
```

### Task 4: Content-Addressed Blob Store and Atomic Receipt Store

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfPreparationCache.cs`
- Test: `experiments/VtfUniversal.Tests/UniversalVtfPreparationCacheStoreTests.cs`

**Interfaces:**
- Produces: `UniversalVtfPreparationCache`, lookup results, atomic publish methods, blob resolution, and quarantine receipts.
- Consumes: canonical JSON and receipt contracts.

- [ ] **Step 1: Write failing persistent hit/miss and atomicity tests**

Test two store instances against the same root, cancellation before commit, cancellation after commit, missing blob, changed blob, truncated receipt, noncanonical JSON, and a partial staging directory:

```csharp
[Fact]
public async Task MissThenSecondProcessInstanceHitsPersistedCompletion()
{
    var first = new UniversalVtfPreparationCache(_root);
    UniversalVtfCachedAsset asset = UniversalVtfCacheFixture.Asset(_sourceVtf);
    await first.PublishAsync(asset, CancellationToken.None);

    var second = new UniversalVtfPreparationCache(_root);
    UniversalVtfCacheLookup lookup = await second.LookupAsync(
        asset.Analysis.Identity,
        asset.Completion.Identity,
        CancellationToken.None);

    Assert.Equal(UniversalVtfCacheHitKind.Completed, lookup.HitKind);
    Assert.Equal(0, lookup.Issues.Count);
}
```

- [ ] **Step 2: Run focused test and verify RED**

Expected: compile failure because store types do not exist.

- [ ] **Step 3: Implement immutable blobs and atomic entry publication**

Use this layout exactly:

```text
v1/objects/sha256/ab/<sha>.vtf
v1/analysis/ab/<key>/receipt.json
v1/completed/ab/<key>/receipt.json
v1/staging/<guid>/
v1/quarantine/<guid>/
```

Write to owned sibling staging, flush with `Flush(flushToDisk: true)`, hash and parse every blob, re-read canonical receipts, then atomically rename. Lookup validates containment, no reparse point, canonical receipt bytes, recomputed keys, SHA, length, VTF layout, and referenced object presence.

```csharp
internal sealed class UniversalVtfPreparationCache
{
    internal UniversalVtfPreparationCache(string cacheRoot);
    internal Task<UniversalVtfCacheLookup> LookupAsync(
        UniversalVtfAnalysisIdentity analysis,
        UniversalVtfCompletionIdentity? completion,
        CancellationToken cancellationToken);
    internal Task<UniversalVtfCachePublishResult> PublishAsync(
        UniversalVtfCachedAsset asset,
        CancellationToken cancellationToken);
    internal string ResolveBlob(UniversalVtfArtifactReference reference);
}
```

- [ ] **Step 4: Run focused tests GREEN**

Expected: all atomicity/tamper tests pass and partial state never becomes a hit.

- [ ] **Step 5: Commit**

```powershell
git add -- GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfPreparationCache.cs experiments/VtfUniversal.Tests/UniversalVtfPreparationCacheStoreTests.cs
git commit -m "feat: persist universal VTF checkpoints atomically"
```

### Task 5: Concurrency, Quarantine, and Determinism Conflicts

**Files:**
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfPreparationCache.cs`
- Test: `experiments/VtfUniversal.Tests/UniversalVtfPreparationCacheStoreTests.cs`

**Interfaces:**
- Extends: per-key single-flight, exclusive cross-process lock, quarantine result, and deterministic-conflict error.

- [ ] **Step 1: Write failing concurrency and quarantine tests**

Add real concurrent tasks with a counting factory. Same-key work must execute once; different keys must overlap. A pre-existing closed lock file must not block. Two different valid payloads for one key must throw `cache_determinism_conflict`. Corrupt entries must move to quarantine and return a miss without deletion.

- [ ] **Step 2: Verify RED**

Expected: duplicate factory calls and missing quarantine behavior.

- [ ] **Step 3: Implement single-flight and cross-process locking**

Use `ConcurrentDictionary<string, Lazy<Task<T>>>` in-process and an exclusive `FileStream` lock per key. Validate an entry published by a competing process before using it. Preserve invalid state by exact `Directory.Move` into an owned quarantine child; never recursively delete an unknown path.

- [ ] **Step 4: Verify GREEN and rerun Task 4 tests**

Expected: concurrency tests pass repeatedly without global serialization.

- [ ] **Step 5: Commit**

```powershell
git add -- GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfPreparationCache.cs experiments/VtfUniversal.Tests/UniversalVtfPreparationCacheStoreTests.cs
git commit -m "feat: harden universal VTF cache concurrency"
```

### Task 6: Builder Miss Path Publishes Per-file Checkpoints

**Files:**
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusBuilder.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfSelector.cs`
- Test: `experiments/VtfUniversal.Tests/UniversalVtfCorpusCheckpointTests.cs`
- Test support: `experiments/VtfUniversal.Tests/UniversalVtfCorpusBuilderTests.cs`

**Interfaces:**
- Extends `UniversalVtfBuildRequest` with required `CacheRoot`.
- Produces `UniversalVtfCacheAssetStatus` (`CompletedHit`, `AnalysisHit`, `Miss`, `Invalid`) and extends progress with that status plus per-file elapsed time.
- Produces full-attempt analysis and completion checkpoints on a miss.

- [ ] **Step 1: Write failing miss/checkpoint tests**

Extend the synthetic corpus fixture to accept a persistent cache root and renewable output roots. Inject a counting materialization boundary and assert one factory call per raw VTF on cold build, an atomically complete entry immediately after each prepared asset, and no entry for a cancellation injected before commit.

- [ ] **Step 2: Verify RED**

Expected: build request has no cache root and no checkpoints are published.

- [ ] **Step 3: Integrate the cold miss path**

After semantic resolution, create the identities and perform lookup. On miss, retain the existing `MaterializeCandidatesAsync → BuildAttempts → Select` behavior. Materialize each profile's final artifact before marking that VTF prepared, build full-attempt receipts, publish cache, and make `PreparedAsset` reference immutable cache blobs.

Move BC3→BC1 finalization into the per-file preparation boundary so an entry is a complete checkpoint. Do not change transcoder inputs or output bytes.

- [ ] **Step 4: Verify GREEN and compare cold output**

Run the focused synthetic corpus tests. Require the existing tree hashes/manifests from cold fixture tests to remain unchanged.

- [ ] **Step 5: Commit**

```powershell
git add -- GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusBuilder.cs GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfSelector.cs experiments/VtfUniversal.Tests/UniversalVtfCorpusCheckpointTests.cs experiments/VtfUniversal.Tests/UniversalVtfCorpusBuilderTests.cs
git commit -m "feat: checkpoint universal VTF preparation per file"
```

### Task 7: Builder Warm Hit, Resume, and Cache Telemetry

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCacheTelemetry.cs`
- Modify: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusBuilder.cs`
- Test: `experiments/VtfUniversal.Tests/UniversalVtfCorpusCheckpointTests.cs`

**Interfaces:**
- Extends `UniversalVtfBuildResult` with `UniversalVtfCacheTelemetry` and run-journal path/hash.
- Produces completed hits, analysis hits, misses, invalid entries, reused count, hit rate, preparation duration, and total duration.

- [ ] **Step 1: Write failing warm/resume/invalidation tests**

Add:

```csharp
[Fact]
public async Task InterruptedBuildResumesPerFileAndWarmTreeMatchesCold()
{
    SyntheticUniversalCorpus corpus = CreateCorpus(vtfCount: 3);
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
        corpus.BuildAndCancelAfterCheckpointAsync(2));
    UniversalVtfBuildResult resumed = await corpus.BuildToNewOutputAsync();
    Assert.Equal(2, resumed.Cache.CompletedHits);
    Assert.Equal(1, resumed.Cache.RecalculatedVtfs);
    UniversalVtfBuildResult warm = await corpus.BuildToNewOutputAsync();
    Assert.Equal(3, warm.Cache.CompletedHits);
    Assert.Equal(0, warm.Cache.RecalculatedVtfs);
    Assert.Equal(resumed.Conservative.TreeSha256, warm.Conservative.TreeSha256);
    Assert.Equal(File.ReadAllBytes(resumed.Conservative.ManifestPath),
                 File.ReadAllBytes(warm.Conservative.ManifestPath));
}
```

Also mutate one raw VTF, one VMT role, policy, toolchain, bridge, caps, and safe VTF and assert exact hit/miss scope from the spec.

- [ ] **Step 2: Verify RED**

Expected: second run still calls materializer and telemetry is missing.

- [ ] **Step 3: Implement hit paths and run journal**

On completed hit, load validated profile blobs and build manifest rows without decoding or measuring images. On full analysis hit, reconstruct the matrix, rerun `Select`, and create only a new completion if necessary. For imported exact coverage, any completion-identity mismatch is a full miss.

Write a separate canonical JSONL run journal with `start`, one `asset` record per VTF, and `complete` or `cancelled`. Include monotonic elapsed milliseconds and content identities, but do not put telemetry in candidate manifests.

- [ ] **Step 4: Verify GREEN**

Expected: synthetic warm build has 100% hits, zero factory calls, byte-identical manifests and trees, and accurate journal counts.

- [ ] **Step 5: Commit**

```powershell
git add -- GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCacheTelemetry.cs GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfCorpusBuilder.cs experiments/VtfUniversal.Tests/UniversalVtfCorpusCheckpointTests.cs
git commit -m "feat: resume universal VTF builds from cache"
```

### Task 8: Strict Legacy Named-NaN Importer

**Files:**
- Create: `GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfLegacyCheckpointImporter.cs`
- Test: `experiments/VtfUniversal.Tests/UniversalVtfLegacyCheckpointImporterTests.cs`

**Interfaces:**
- Produces: `UniversalVtfLegacyImportRequest`, `UniversalVtfLegacyImportResult`, and `ImportAsync`.
- Consumes: raw/safe/bridge authorities, three legacy roots/manifests/hashes, build log/hash, cache store.

- [ ] **Step 1: Write failing legacy import tests**

Create a synthetic three-profile legacy fixture whose typed metric fields contain named NaN. Assert:

- normal manifest loader still rejects it;
- wrong log or manifest hash rejects before import;
- missing profile, tree drift, file drift, cross-profile metric drift, and selection drift reject;
- correct import invokes zero metric/materializer callbacks;
- imported receipt contains JSON null, gives a completed hit, and recreates the original tree.

- [ ] **Step 2: Verify RED**

Expected: importer type is missing.

- [ ] **Step 3: Implement the dedicated importer**

Use a private legacy double converter that accepts named floats only for typed doubles. Require explicit expected hashes; never route legacy bytes through the normal manifest loader. Validate three manifests and trees, 3 × N final VTF SHA/bytes/layout, shared route metrics, profile selections, semantic proof, policy/toolchain/fixture/bridge/anchors, and current selector replay. Publish `ImportedExactProfileFinals` entries through the same atomic cache store.

```csharp
internal static class UniversalVtfLegacyCheckpointImporter
{
    internal static Task<UniversalVtfLegacyImportResult> ImportAsync(
        UniversalVtfLegacyImportRequest request,
        UniversalVtfPreparationCache cache,
        IProgress<UniversalVtfLegacyImportProgress>? progress,
        CancellationToken cancellationToken);
}
```

- [ ] **Step 4: Verify GREEN**

Expected: strict import passes, normal loader rejection remains, and no test callback for image computation runs.

- [ ] **Step 5: Commit**

```powershell
git add -- GmodAddonCompressor-master/GmodAddonCompressor/Systems/Vtf/Universal/UniversalVtfLegacyCheckpointImporter.cs experiments/VtfUniversal.Tests/UniversalVtfLegacyCheckpointImporterTests.cs
git commit -m "feat: import verified legacy universal VTF checkpoints"
```

### Task 9: Corpus Driver Cache Commands and Reporting

**Files:**
- Modify: `experiments/VtfUniversalCorpusDriver/Program.cs`
- Modify: `experiments/VtfUniversal.Tests/VtfUniversalCorpusDriverTests.cs`

**Interfaces:**
- Adds mandatory build option `--cache-root`.
- Adds command `seed-cache` with explicit legacy hashes and authorities.
- Adds canonical result schemas `universal-vtf-cache-seed-result-v1` and cache fields to `universal-vtf-build-result-v1`.

- [ ] **Step 1: Write failing CLI tests**

Require `build` to reject missing/overlapping cache roots. Require `seed-cache` to reject missing hashes and propagate every verified option. Assert emitted JSON has exact integer counters and elapsed milliseconds.

- [ ] **Step 2: Verify RED**

Expected: `seed-cache` is unknown and `--cache-root` is rejected as an extra option.

- [ ] **Step 3: Implement CLI and canonical reports**

Add this invocation shape:

```text
seed-cache --raw-root <dir> --safe-root <dir> --bc-bridge <json>
  --bc-bridge-sha256 <sha> --legacy-root <dir>
  --legacy-build-log <jsonl> --legacy-build-log-sha256 <sha>
  --conservative-manifest-sha256 <sha>
  --balanced-manifest-sha256 <sha>
  --aggressive-manifest-sha256 <sha>
  --cache-root <dir>
```

`build` requires `--cache-root <existing-dir>`. Reject overlap/reparse/path escape against raw, safe, output, report, bridge, and legacy authorities.

- [ ] **Step 4: Verify GREEN and run all driver tests**

Expected: arguments, output schemas, and exit codes are deterministic.

- [ ] **Step 5: Commit**

```powershell
git add -- experiments/VtfUniversalCorpusDriver/Program.cs experiments/VtfUniversal.Tests/VtfUniversalCorpusDriverTests.cs
git commit -m "feat: expose universal VTF cache controls"
```

### Task 10: Full Automated Verification Before Real-data Import

**Files:**
- Modify only if a test exposes a real defect: cache production/test files from Tasks 1–9.

**Interfaces:**
- Verifies every cache behavior before touching the real frozen corpus.

- [ ] **Step 1: Run the complete C# suite**

```powershell
dotnet test .\experiments\VtfUniversal.Tests\VtfUniversal.Tests.csproj -c Release --logger "console;verbosity=minimal"
```

Expected: all tests pass, including existing transactional/auditor tests.

- [ ] **Step 2: Run independent Python tests**

```powershell
python -m pytest .\tests\test_audit_universal_vtf_v1.py -q
```

Expected: 8 tests pass.

- [ ] **Step 3: Build Windows application**

```powershell
dotnet build .\GmodAddonCompressor-master\GmodAddonCompressor\GmodAddonCompressor.csproj -c Release -r win-x64 -p:NuGetAudit=false -v minimal
```

Expected: build succeeds; existing .NET 6 EOL warnings are recorded but no errors occur.

- [ ] **Step 4: Run a deterministic synthetic benchmark**

Execute cold, interrupted/resumed, warm process one, and warm process two on the synthetic corpus. Require cold materializer calls = N, resumed calls = remaining N, and both warm calls = 0. Do not assert wall-clock speed in unit tests.

- [ ] **Step 5: Commit any test-driven corrections, or record no-change verification**

If a defect is found, add a reproducing RED test before its fix and commit only that fix. Otherwise make no commit for this task.

### Task 11: Import the Frozen 1,152-file Production and Run Two Warm Builds

**Files:**
- Create outside Git through the driver: persistent cache root, seed report, two candidate roots, two build logs, six C#/Python audit logs.
- Preserve: all existing runtime-validation trees and logs.

**Interfaces:**
- Consumes the exact frozen hashes from the spec.
- Produces 1,152 imported checkpoints and two independently built three-profile corpora.

- [ ] **Step 1: Verify authorities and absent destinations read-only**

Resolve absolute paths; require raw/safe/legacy/bridge/log files and hashes; require new cache and output destinations to be outside all authorities and either absent or an empty explicitly created cache root.

- [ ] **Step 2: Seed the persistent cache**

Run `seed-cache` with the three manifest hashes and build-log hash from the spec. Preserve its JSONL. Expected final receipt:

```text
totalVtfs=1152
importedVtfs=1152
recalculatedVtfs=0
uniqueFinalBlobs=1113
status=CLEAN
```

- [ ] **Step 3: Run warm build one to a new output**

Run `build --profiles all --cache-root <cache>` with the frozen bridge. Expected: completed hits 1,152, recalculated 0, hit rate 100%, both internal audit passes CLEAN.

- [ ] **Step 4: Run warm build two in a new process and different output**

Use the same cache and authorities. Require the same counters and independently captured duration.

- [ ] **Step 5: Compare determinism**

Require profile tree hashes to equal:

```text
conservative ca20f2d97e397a2f421bcc81c812a491779c8e2c0014d34cd73c0107f4afac15
balanced     409ea63136540d6dcd6a6f69e03aa516ed55653bef5f22af53df83ab65da850e
aggressive   5751602cc4dd3a1ca0c3e21d4e4d92500ebe60fca77e28919de6bf0a19797573
```

Require warm-one and warm-two canonical manifest bytes and SHA-256 values to be identical for each profile. Require no literal `"NaN"` in any manifest.

- [ ] **Step 6: Run C# and Python corpus audits for all six trees**

For each profile/run, execute the C# driver `audit` command with explicit manifest hash, bridge hash, raw, safe, and candidate. Then run:

```powershell
python .\scripts\audit_universal_vtf_v1.py corpus --manifest <manifest> --candidate <tree> --raw <raw> --safe <safe> --bc-bridge <bridge>
```

Expected: all 12 audits report CLEAN.

### Task 12: Benchmark Report, Final Verification, and Manual Handoff

**Files:**
- Create: `docs/superpowers/results/2026-08-29-universal-vtf-persistent-cache-results.md`
- Modify: `docs/superpowers/plans/2026-08-29-universal-vtf-persistent-cache.md` only to check completed boxes.

**Interfaces:**
- Produces the immutable evidence report and manual GMod handoff without promotion.

- [ ] **Step 1: Calculate measured performance**

Report:

- reused VTFs;
- recalculated VTFs;
- hit rate;
- seed time;
- old 27,055.6239045-second time;
- warm-one and warm-two times;
- speedup using `27055.6239045 / max(warm1, warm2)`;
- cache bytes, unique blobs, quarantined entries, and journal hashes.

- [ ] **Step 2: Record exact candidate evidence**

Include all profile paths, bytes, tree hashes, canonical manifest hashes, cache/algorithm fingerprints, audit log paths, and deterministic-repeat comparison. Record the preserved DIRTY_NAN and pre-cache recovery paths and explain why neither was deleted.

- [ ] **Step 3: Run final fresh verification**

Run the full C# suite, Python tests, win-x64 build, `git diff --check`, and `git status --short`. Re-run the six C# and six Python corpus audits if any production code changed after Task 11.

- [ ] **Step 4: Mark status without promotion**

Use exact status `OFFLINE_CLEAN_MANUAL_GMOD_PENDING`. State that candidate bytes match the prior production and only manifest NaN encoding changed. Do not replace the safe baseline or mount an addon automatically.

- [ ] **Step 5: Commit documentation**

```powershell
git add -- docs/superpowers/results/2026-08-29-universal-vtf-persistent-cache-results.md docs/superpowers/plans/2026-08-29-universal-vtf-persistent-cache.md
git commit -m "docs: record universal VTF cache benchmark"
```

- [ ] **Step 6: Hand off manual validation**

Report the three candidate paths and a concise GMod checklist covering close/medium/far views, skins, interior, glass, lights, gauges, wheels, and LOD transitions. Keep every baseline and recovery available for rollback.
