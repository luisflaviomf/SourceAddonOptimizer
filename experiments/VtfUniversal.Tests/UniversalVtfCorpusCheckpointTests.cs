using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfCorpusCheckpointTests
{
    [Fact]
    public async Task ColdBuildPublishesCompleteCheckpointBeforeProgress()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        int observedCompleted = 0;
        var progress = new InlineProgress<UniversalVtfBuildProgress>(item =>
        {
            if (item.Stage != "prepare")
            {
                return;
            }

            int receipts = Directory.GetFiles(
                Path.Combine(corpus.CacheRoot, "v1", "completed"),
                "receipt.json",
                SearchOption.AllDirectories).Length;
            Assert.Equal(item.Completed, receipts);
            Assert.Equal(UniversalVtfCacheAssetStatus.Miss, item.CacheStatus);
            Assert.True(item.ElapsedMilliseconds >= 0);
            observedCompleted = item.Completed;
        });

        UniversalVtfBuildResult result = await UniversalVtfCorpusBuilder.BuildAsync(
            corpus.Request,
            progress,
            CancellationToken.None);

        Assert.Equal(1, observedCompleted);
        Assert.Equal(1, result.Cache.Misses);
        Assert.Equal(1, result.Cache.RecalculatedVtfs);
        Assert.Equal(0, result.Cache.ReusedVtfs);
        string analysisPath = Assert.Single(Directory.GetFiles(
            Path.Combine(corpus.CacheRoot, "v1", "analysis"),
            "receipt.json",
            SearchOption.AllDirectories));
        string completionPath = Assert.Single(Directory.GetFiles(
            Path.Combine(corpus.CacheRoot, "v1", "completed"),
            "receipt.json",
            SearchOption.AllDirectories));
        UniversalVtfAnalysisReceipt analysis =
            UniversalVtfCanonicalJson.Deserialize<UniversalVtfAnalysisReceipt>(
                await File.ReadAllBytesAsync(analysisPath));
        UniversalVtfCompletionReceipt completion =
            UniversalVtfCanonicalJson.Deserialize<UniversalVtfCompletionReceipt>(
                await File.ReadAllBytesAsync(completionPath));
        UniversalVtfCacheContracts.Validate(analysis);
        UniversalVtfCacheContracts.Validate(completion);
        Assert.Equal(UniversalVtfCacheCoverage.FullAttempts, analysis.Coverage);
        Assert.Equal(3, completion.Profiles.Count);

        AssertProfile(result.Conservative, completion, UniversalVtfProfileKind.Conservative);
        AssertProfile(result.Balanced, completion, UniversalVtfProfileKind.Balanced);
        AssertProfile(result.Aggressive, completion, UniversalVtfProfileKind.Aggressive);
    }

    [Fact]
    public async Task InterruptedBuildResumesPerFileAndWarmTreeMatchesCold()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        using var cancellation = new CancellationTokenSource();
        var cancelAfterCheckpoint = new InlineProgress<UniversalVtfBuildProgress>(item =>
        {
            if (item.Stage == "prepare" && item.Completed == 1)
            {
                cancellation.Cancel();
            }
        });
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            UniversalVtfCorpusBuilder.BuildAsync(
                corpus.Request,
                cancelAfterCheckpoint,
                cancellation.Token));

        UniversalVtfBuildResult resumed = await UniversalVtfCorpusBuilder.BuildAsync(
            corpus.Request,
            null,
            CancellationToken.None);
        byte[] resumedManifest = await File.ReadAllBytesAsync(
            resumed.Balanced.ManifestPath);
        Assert.Equal(1, resumed.Cache.CompletedHits);
        Assert.Equal(0, resumed.Cache.RecalculatedVtfs);

        corpus.RenewOutputDestinations();
        UniversalVtfBuildResult warm = await UniversalVtfCorpusBuilder.BuildAsync(
            corpus.Request,
            null,
            CancellationToken.None);

        Assert.Equal(1, warm.Cache.CompletedHits);
        Assert.Equal(0, warm.Cache.AnalysisHits);
        Assert.Equal(0, warm.Cache.RecalculatedVtfs);
        Assert.Equal(1, warm.Cache.ReusedVtfs);
        Assert.Equal(100.0, warm.Cache.HitRatePercent);
        Assert.Equal(resumed.Conservative.TreeSha256, warm.Conservative.TreeSha256);
        Assert.Equal(resumed.Balanced.TreeSha256, warm.Balanced.TreeSha256);
        Assert.Equal(resumed.Aggressive.TreeSha256, warm.Aggressive.TreeSha256);
        Assert.Equal(resumedManifest, await File.ReadAllBytesAsync(warm.Balanced.ManifestPath));
        Assert.True(File.Exists(warm.RunJournalPath));
        Assert.Equal(64, warm.RunJournalSha256.Length);
    }

    [Fact]
    public async Task ChangedRuntimeCapsReusesAnalysisWithoutMetricRecalculation()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        await UniversalVtfCorpusBuilder.BuildAsync(
            corpus.Request,
            null,
            CancellationToken.None);
        string capsPath = Path.Combine(
            Path.GetDirectoryName(corpus.RawRoot)!,
            "changed-runtime-caps.json");
        UniversalVtfRuntimeCapsFrozen caps = UniversalVtfRuntimeCaps.WriteCanonical(
            capsPath,
            new[]
            {
                new UniversalVtfRuntimeCap(
                    "materials/a.vtf",
                    UniversalVtfProfileKind.Conservative,
                    1,
                    "synthetic-completion-invalidation")
            });
        corpus.Request = corpus.Request with
        {
            RuntimeCapsPath = caps.Path,
            RuntimeCapsSha256 = caps.Sha256
        };
        corpus.RenewOutputDestinations();

        UniversalVtfBuildResult result = await UniversalVtfCorpusBuilder.BuildAsync(
            corpus.Request,
            null,
            CancellationToken.None);

        Assert.Equal(0, result.Cache.CompletedHits);
        Assert.Equal(1, result.Cache.AnalysisHits);
        Assert.Equal(0, result.Cache.RecalculatedVtfs);
        UniversalVtfManifest manifest = UniversalVtfManifestStore.Load(
            result.Conservative.ManifestPath,
            result.Conservative.ManifestSha256);
        Assert.Equal(1, manifest.Row("materials/a.vtf").SelectedLevel);
    }

    [Fact]
    public async Task CancellationBeforePerFileCommitPublishesNoReceipt()
    {
        using SyntheticUniversalCorpus corpus = SyntheticUniversalCorpus.Create();
        using var cancellation = new CancellationTokenSource();
        corpus.Request = corpus.Request with
        {
            CacheObserver = new CancelCacheCommitObserver(cancellation)
        };

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            UniversalVtfCorpusBuilder.BuildAsync(
                corpus.Request,
                null,
                cancellation.Token));

        Assert.Empty(Directory.GetFiles(
            Path.Combine(corpus.CacheRoot, "v1", "analysis"),
            "receipt.json",
            SearchOption.AllDirectories));
        Assert.Empty(Directory.GetFiles(
            Path.Combine(corpus.CacheRoot, "v1", "completed"),
            "receipt.json",
            SearchOption.AllDirectories));
    }

    private static void AssertProfile(
        UniversalVtfBuiltProfile built,
        UniversalVtfCompletionReceipt receipt,
        UniversalVtfProfileKind profile)
    {
        string final = Path.Combine(built.Root, "materials", "a.vtf");
        VtfContainerLayout layout = VtfContainerLayoutReader.Read(final);
        UniversalVtfArtifactReference expected = receipt.Profiles[profile].FinalArtifact;
        Assert.Equal(expected.Sha256, layout.FileSha256);
        Assert.Equal(expected.Bytes, layout.Document.FileLength);
    }

    private sealed class InlineProgress<T> : IProgress<T>
    {
        private readonly Action<T> _report;

        internal InlineProgress(Action<T> report) => _report = report;

        public void Report(T value) => _report(value);
    }

    private sealed class CancelCacheCommitObserver : IUniversalVtfCacheCommitObserver
    {
        private readonly CancellationTokenSource _cancellation;

        internal CancelCacheCommitObserver(CancellationTokenSource cancellation) =>
            _cancellation = cancellation;

        public void Observe(UniversalVtfCacheCommitPhase phase)
        {
            if (phase == UniversalVtfCacheCommitPhase.BeforeReceiptCommit)
            {
                _cancellation.Cancel();
            }
        }
    }
}
