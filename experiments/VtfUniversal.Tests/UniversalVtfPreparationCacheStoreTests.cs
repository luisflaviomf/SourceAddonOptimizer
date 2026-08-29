using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversal.Tests.Fixtures;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfPreparationCacheStoreTests : IDisposable
{
    private readonly string _root = Path.Combine(
        Path.GetTempPath(),
        $"universal-vtf-persistent-cache-{Guid.NewGuid():N}");
    private readonly VtfFixtureFile _source =
        VtfFixtureBuilder.WriteResource75WithMips(16, 16, 5);

    [Fact]
    public async Task MissThenSecondProcessInstanceHitsPersistedCompletion()
    {
        var first = new UniversalVtfPreparationCache(_root);
        UniversalVtfCachedAsset asset = UniversalVtfCacheFixture.Asset(_source.Path);
        UniversalVtfCacheLookup miss = await first.LookupAsync(
            asset.Analysis.Identity,
            asset.Completion.Identity,
            CancellationToken.None);
        Assert.Equal(UniversalVtfCacheHitKind.Miss, miss.HitKind);

        UniversalVtfCachePublishResult published = await first.PublishAsync(
            asset,
            CancellationToken.None);
        var second = new UniversalVtfPreparationCache(_root);
        UniversalVtfCacheLookup lookup = await second.LookupAsync(
            asset.Analysis.Identity,
            asset.Completion.Identity,
            CancellationToken.None);

        Assert.True(published.AnalysisPublished);
        Assert.True(published.CompletionPublished);
        Assert.Equal(UniversalVtfCacheHitKind.Completed, lookup.HitKind);
        Assert.Empty(lookup.Issues);
        Assert.NotNull(lookup.Analysis);
        Assert.NotNull(lookup.Completion);
    }

    [Fact]
    public async Task CancellationBeforeReceiptCommitPublishesNoHit()
    {
        using var cancellation = new CancellationTokenSource();
        var observer = new CancellingObserver(
            UniversalVtfCacheCommitPhase.BeforeReceiptCommit,
            cancellation);
        var cache = new UniversalVtfPreparationCache(_root, observer);
        UniversalVtfCachedAsset asset = UniversalVtfCacheFixture.Asset(_source.Path);

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            cache.PublishAsync(asset, cancellation.Token));
        UniversalVtfCacheLookup lookup = await new UniversalVtfPreparationCache(_root)
            .LookupAsync(
                asset.Analysis.Identity,
                asset.Completion.Identity,
                CancellationToken.None);

        Assert.Equal(UniversalVtfCacheHitKind.Miss, lookup.HitKind);
        Assert.Null(lookup.Analysis);
        Assert.Null(lookup.Completion);
    }

    [Fact]
    public async Task CancellationAfterReceiptCommitLeavesCompletedHit()
    {
        using var cancellation = new CancellationTokenSource();
        var observer = new CancellingObserver(
            UniversalVtfCacheCommitPhase.AfterReceiptCommit,
            cancellation);
        var cache = new UniversalVtfPreparationCache(_root, observer);
        UniversalVtfCachedAsset asset = UniversalVtfCacheFixture.Asset(_source.Path);

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            cache.PublishAsync(asset, cancellation.Token));
        UniversalVtfCacheLookup lookup = await new UniversalVtfPreparationCache(_root)
            .LookupAsync(
                asset.Analysis.Identity,
                asset.Completion.Identity,
                CancellationToken.None);

        Assert.Equal(UniversalVtfCacheHitKind.Completed, lookup.HitKind);
        Assert.Empty(lookup.Issues);
    }

    [Fact]
    public async Task MissingOrChangedBlobCannotHit()
    {
        UniversalVtfCachedAsset asset = UniversalVtfCacheFixture.Asset(_source.Path);
        var cache = new UniversalVtfPreparationCache(_root);
        await cache.PublishAsync(asset, CancellationToken.None);
        UniversalVtfArtifactReference artifact =
            asset.Completion.Profiles[UniversalVtfProfileKind.Conservative].FinalArtifact;
        string blob = cache.ResolveBlob(artifact);
        string preserved = blob + ".preserved";
        File.Move(blob, preserved);

        UniversalVtfCacheLookup missing = await cache.LookupAsync(
            asset.Analysis.Identity,
            asset.Completion.Identity,
            CancellationToken.None);
        Assert.Equal(UniversalVtfCacheHitKind.Miss, missing.HitKind);
        Assert.Contains(missing.Issues, issue => issue.Contains("missing_blob"));

        File.Move(preserved, blob);
        await cache.PublishAsync(asset, CancellationToken.None);
        await File.AppendAllTextAsync(blob, "drift");
        UniversalVtfCacheLookup changed = await cache.LookupAsync(
            asset.Analysis.Identity,
            asset.Completion.Identity,
            CancellationToken.None);
        Assert.Equal(UniversalVtfCacheHitKind.Miss, changed.HitKind);
        Assert.Contains(changed.Issues, issue => issue.Contains("blob_"));
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task TruncatedOrNoncanonicalReceiptCannotHit(bool noncanonical)
    {
        UniversalVtfCachedAsset asset = UniversalVtfCacheFixture.Asset(_source.Path);
        var cache = new UniversalVtfPreparationCache(_root);
        await cache.PublishAsync(asset, CancellationToken.None);
        string receipt = CompletionReceiptPath(asset.Completion.CompletionKey);
        byte[] original = await File.ReadAllBytesAsync(receipt);
        byte[] invalid = noncanonical
            ? original.Take(1).Concat(new byte[] { (byte)' ' }).Concat(original.Skip(1)).ToArray()
            : original.Take(original.Length / 2).ToArray();
        await File.WriteAllBytesAsync(receipt, invalid);

        UniversalVtfCacheLookup lookup = await cache.LookupAsync(
            asset.Analysis.Identity,
            asset.Completion.Identity,
            CancellationToken.None);

        Assert.Equal(UniversalVtfCacheHitKind.Miss, lookup.HitKind);
        Assert.NotEmpty(lookup.Issues);
    }

    [Fact]
    public async Task PartialStagingDirectoryIsNeverVisibleAsAHit()
    {
        UniversalVtfCachedAsset asset = UniversalVtfCacheFixture.Asset(_source.Path);
        string partial = Path.Combine(_root, "v1", "staging", $"owned-{Guid.NewGuid():N}");
        Directory.CreateDirectory(partial);
        await File.WriteAllBytesAsync(
            Path.Combine(partial, "receipt.json"),
            UniversalVtfCanonicalJson.Serialize(asset.Completion));

        UniversalVtfCacheLookup lookup = await new UniversalVtfPreparationCache(_root)
            .LookupAsync(
                asset.Analysis.Identity,
                asset.Completion.Identity,
                CancellationToken.None);

        Assert.Equal(UniversalVtfCacheHitKind.Miss, lookup.HitKind);
        Assert.Empty(lookup.Issues);
        Assert.True(Directory.Exists(partial));
    }

    [Fact]
    public async Task SameKeySingleFlightInvokesFactoryOnce()
    {
        UniversalVtfCachedAsset asset = UniversalVtfCacheFixture.Asset(_source.Path);
        var cache = new UniversalVtfPreparationCache(_root);
        int calls = 0;
        Task<UniversalVtfCacheLookup>[] requests = Enumerable.Range(0, 8)
            .Select(_ => cache.GetOrCreateAsync(
                asset.Analysis.Identity,
                asset.Completion.Identity,
                async token =>
                {
                    Interlocked.Increment(ref calls);
                    await Task.Delay(80, token);
                    return asset;
                },
                CancellationToken.None))
            .ToArray();

        UniversalVtfCacheLookup[] results = await Task.WhenAll(requests);

        Assert.Equal(1, calls);
        Assert.All(results, result =>
            Assert.Equal(UniversalVtfCacheHitKind.Completed, result.HitKind));
    }

    [Fact]
    public async Task DifferentKeysDoNotSerializeFactoriesGlobally()
    {
        UniversalVtfCachedAsset first = UniversalVtfCacheFixture.Asset(
            _source.Path,
            "materials/models/diggercars/cache/first.vtf");
        UniversalVtfCachedAsset second = UniversalVtfCacheFixture.Asset(
            _source.Path,
            "materials/models/diggercars/cache/second.vtf");
        var cache = new UniversalVtfPreparationCache(_root);
        var bothEntered = new TaskCompletionSource<bool>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        int active = 0;
        int maximum = 0;

        async Task<UniversalVtfCachedAsset> Factory(
            UniversalVtfCachedAsset value,
            CancellationToken token)
        {
            int now = Interlocked.Increment(ref active);
            UpdateMaximum(ref maximum, now);
            if (now == 2)
            {
                bothEntered.TrySetResult(true);
            }

            await bothEntered.Task.WaitAsync(TimeSpan.FromSeconds(3), token);
            Interlocked.Decrement(ref active);
            return value;
        }

        await Task.WhenAll(
            cache.GetOrCreateAsync(
                first.Analysis.Identity,
                first.Completion.Identity,
                token => Factory(first, token),
                CancellationToken.None),
            cache.GetOrCreateAsync(
                second.Analysis.Identity,
                second.Completion.Identity,
                token => Factory(second, token),
                CancellationToken.None));

        Assert.Equal(2, maximum);
    }

    [Fact]
    public async Task ClosedStaleLockFileDoesNotBlockCreation()
    {
        UniversalVtfCachedAsset asset = UniversalVtfCacheFixture.Asset(_source.Path);
        string key = asset.Completion.CompletionKey;
        string lockPath = Path.Combine(_root, "v1", "locks", key[..2], $"{key}.lock");
        Directory.CreateDirectory(Path.GetDirectoryName(lockPath)!);
        await File.WriteAllTextAsync(lockPath, "stale-owner-is-gone");
        var cache = new UniversalVtfPreparationCache(_root);

        UniversalVtfCacheLookup result = await cache.GetOrCreateAsync(
            asset.Analysis.Identity,
            asset.Completion.Identity,
            _ => Task.FromResult(asset),
            CancellationToken.None);

        Assert.Equal(UniversalVtfCacheHitKind.Completed, result.HitKind);
    }

    [Fact]
    public async Task DifferentValidPayloadForSameKeyIsDeterminismConflict()
    {
        using VtfFixtureFile alternative =
            VtfFixtureBuilder.WriteResource74WithInlineCrc(0x12345678);
        UniversalVtfCachedAsset original = UniversalVtfCacheFixture.Asset(_source.Path);
        UniversalVtfCachedAsset conflict = WithAlternateFinalArtifact(original, alternative.Path);
        var cache = new UniversalVtfPreparationCache(_root);
        await cache.PublishAsync(original, CancellationToken.None);

        InvalidDataException error = await Assert.ThrowsAsync<InvalidDataException>(() =>
            cache.PublishAsync(conflict, CancellationToken.None));

        Assert.Contains("cache_determinism_conflict", error.Message);
    }

    [Fact]
    public async Task CorruptReceiptMovesToQuarantineAndReturnsMiss()
    {
        UniversalVtfCachedAsset asset = UniversalVtfCacheFixture.Asset(_source.Path);
        var cache = new UniversalVtfPreparationCache(_root);
        await cache.PublishAsync(asset, CancellationToken.None);
        string entry = Path.GetDirectoryName(
            CompletionReceiptPath(asset.Completion.CompletionKey))!;
        string receipt = Path.Combine(entry, "receipt.json");
        byte[] corrupt = { (byte)'{', (byte)'x', (byte)'}' };
        await File.WriteAllBytesAsync(receipt, corrupt);

        UniversalVtfCacheLookup lookup = await cache.LookupAsync(
            asset.Analysis.Identity,
            asset.Completion.Identity,
            CancellationToken.None);

        Assert.Equal(UniversalVtfCacheHitKind.Miss, lookup.HitKind);
        Assert.True(lookup.Quarantined);
        Assert.False(Directory.Exists(entry));
        string[] preserved = Directory.GetFiles(
            Path.Combine(_root, "v1", "quarantine"),
            "receipt.json",
            SearchOption.AllDirectories);
        Assert.Single(preserved);
        Assert.Equal(corrupt, await File.ReadAllBytesAsync(preserved[0]));
    }

    public void Dispose()
    {
        _source.Dispose();
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }

    private string CompletionReceiptPath(string key) => Path.Combine(
        _root,
        "v1",
        "completed",
        key[..2],
        key,
        "receipt.json");

    private static void UpdateMaximum(ref int maximum, int candidate)
    {
        int observed;
        do
        {
            observed = maximum;
            if (observed >= candidate)
            {
                return;
            }
        } while (Interlocked.CompareExchange(ref maximum, candidate, observed) != observed);
    }

    private static UniversalVtfCachedAsset WithAlternateFinalArtifact(
        UniversalVtfCachedAsset original,
        string alternatePath)
    {
        string source = Path.GetFullPath(alternatePath);
        UniversalVtfArtifactReference alternate = UniversalVtfArtifactReference.FromVtf(source);
        UniversalVtfCompletionReceipt completion = original.Completion with
        {
            Profiles = original.Completion.Profiles.ToDictionary(
                pair => pair.Key,
                pair => pair.Value with
                {
                    FinalArtifact = alternate,
                    SpatialBytes = alternate.Bytes,
                    SpatialSha256 = alternate.Sha256,
                    ResourceCount = 1
                })
        };
        var sources = original.ArtifactSources.ToDictionary(
            pair => pair.Key,
            pair => pair.Value,
            StringComparer.Ordinal);
        sources[alternate.Sha256] = source;
        return original with
        {
            Completion = completion,
            ArtifactSources = sources
        };
    }

    private sealed class CancellingObserver : IUniversalVtfCacheCommitObserver
    {
        private readonly UniversalVtfCacheCommitPhase _phase;
        private readonly CancellationTokenSource _cancellation;

        internal CancellingObserver(
            UniversalVtfCacheCommitPhase phase,
            CancellationTokenSource cancellation)
        {
            _phase = phase;
            _cancellation = cancellation;
        }

        public void Observe(UniversalVtfCacheCommitPhase phase)
        {
            if (phase == _phase)
            {
                _cancellation.Cancel();
            }
        }
    }
}
