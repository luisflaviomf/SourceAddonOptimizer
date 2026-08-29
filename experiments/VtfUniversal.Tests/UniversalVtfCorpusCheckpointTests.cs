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
