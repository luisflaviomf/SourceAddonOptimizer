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
