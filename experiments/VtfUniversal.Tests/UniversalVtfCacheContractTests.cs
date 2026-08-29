using System.Text;
using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversal.Tests.Fixtures;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfCacheContractTests : IDisposable
{
    private readonly string _tempRoot = Path.Combine(
        Path.GetTempPath(),
        $"universal-vtf-cache-contract-{Guid.NewGuid():N}");

    [Fact]
    public void PortableMatrixRoundTripsEveryMetricAndReplaysSelection()
    {
        UniversalVtfAttemptMatrix runtime = UniversalVtfCacheFixture.Matrix(_tempRoot);
        UniversalVtfCachedAttemptMatrix portable =
            UniversalVtfCachedAttemptMatrix.FromRuntime(runtime);

        byte[] bytes = UniversalVtfCanonicalJson.Serialize(portable);
        string json = Encoding.UTF8.GetString(bytes);
        Assert.DoesNotContain(_tempRoot, json, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("\"diagnostic_nan\":null", json, StringComparison.Ordinal);
        Assert.Contains("\"Infinity\"", json, StringComparison.Ordinal);
        Assert.Contains("\"-Infinity\"", json, StringComparison.Ordinal);

        UniversalVtfCachedAttemptMatrix loaded =
            UniversalVtfCanonicalJson.Deserialize<UniversalVtfCachedAttemptMatrix>(bytes);
        Assert.Equal(bytes, UniversalVtfCanonicalJson.Serialize(loaded));
        UniversalVtfAttemptMatrix replay = loaded.ToRuntime(
            (level, route) => UniversalVtfCacheFixture.CandidatePath(_tempRoot, level, route));

        Assert.Equal(
            UniversalVtfSelector.Select(runtime, UniversalVtfRuntimeCaps.Empty),
            UniversalVtfSelector.Select(replay, UniversalVtfRuntimeCaps.Empty),
            UniversalVtfCacheFixture.SelectionComparer);
    }

    [Fact]
    public void PortableMatrixRejectsNonconsecutiveLevels()
    {
        UniversalVtfCachedAttemptMatrix valid = UniversalVtfCachedAttemptMatrix.FromRuntime(
            UniversalVtfCacheFixture.Matrix(_tempRoot));
        UniversalVtfCachedAttemptMatrix invalid = valid with
        {
            Attempts = valid.Attempts.Select(attempt =>
                attempt.Level == 2 ? attempt with { Level = 3 } : attempt).ToArray()
        };

        InvalidDataException error = Assert.Throws<InvalidDataException>(invalid.Validate);

        Assert.Contains("cache_nonconsecutive_attempt_matrix", error.Message);
    }

    [Fact]
    public void AnalysisReceiptRequiresArtifactsForEveryValidFullAttempt()
    {
        UniversalVtfAnalysisIdentity identity = UniversalVtfCacheFixture.AnalysisIdentity();
        var receipt = new UniversalVtfAnalysisReceipt(
            UniversalVtfCacheContracts.AnalysisReceiptSchema,
            UniversalVtfCacheKey.Analysis(identity),
            identity,
            UniversalVtfCacheCoverage.FullAttempts,
            UniversalVtfCachedAttemptMatrix.FromRuntime(
                UniversalVtfCacheFixture.Matrix(_tempRoot)),
            new Dictionary<UniversalVtfCachedArtifactKey, UniversalVtfArtifactReference>());

        InvalidDataException error = Assert.Throws<InvalidDataException>(() =>
            UniversalVtfCacheContracts.Validate(receipt));

        Assert.Contains("cache_missing_full_attempt_artifact", error.Message);
    }

    [Fact]
    public void AnalysisAndCompletionReceiptsRoundTripCanonically()
    {
        UniversalVtfAttemptMatrix runtime = UniversalVtfCacheFixture.Matrix(_tempRoot);
        UniversalVtfCachedAttemptMatrix matrix =
            UniversalVtfCachedAttemptMatrix.FromRuntime(runtime);
        UniversalVtfAnalysisIdentity analysisIdentity =
            UniversalVtfCacheFixture.AnalysisIdentity();
        string analysisKey = UniversalVtfCacheKey.Analysis(analysisIdentity);
        var analysis = new UniversalVtfAnalysisReceipt(
            UniversalVtfCacheContracts.AnalysisReceiptSchema,
            analysisKey,
            analysisIdentity,
            UniversalVtfCacheCoverage.FullAttempts,
            matrix,
            new Dictionary<UniversalVtfCachedArtifactKey, UniversalVtfArtifactReference>
            {
                [new(1, UniversalVtfFilterRoute.SrgbColor)] = Artifact('2', 512, 256),
                [new(2, UniversalVtfFilterRoute.SrgbColor)] = Artifact('3', 256, 128),
                [new(2, UniversalVtfFilterRoute.NumericLinear)] = Artifact('4', 256, 128)
            });
        UniversalVtfProfileSelection runtimeSelection =
            UniversalVtfSelector.Select(runtime, UniversalVtfRuntimeCaps.Empty);
        var selections = new Dictionary<UniversalVtfProfileKind, UniversalVtfCachedSelection>
        {
            [UniversalVtfProfileKind.Conservative] = Cached(
                UniversalVtfProfileKind.Conservative,
                runtimeSelection.Conservative),
            [UniversalVtfProfileKind.Balanced] = Cached(
                UniversalVtfProfileKind.Balanced,
                runtimeSelection.Balanced),
            [UniversalVtfProfileKind.Aggressive] = Cached(
                UniversalVtfProfileKind.Aggressive,
                runtimeSelection.Aggressive)
        };
        var completionIdentity = new UniversalVtfCompletionIdentity(
            UniversalVtfCacheKey.CompletionSchema,
            analysisKey,
            UniversalVtfCacheFixture.Sha('5'),
            1_024,
            UniversalVtfCacheKey.None,
            selections);
        string completionKey = UniversalVtfCacheKey.Completion(completionIdentity);
        var completion = new UniversalVtfCompletionReceipt(
            UniversalVtfCacheContracts.CompletionReceiptSchema,
            completionKey,
            completionIdentity,
            selections.ToDictionary(
                pair => pair.Key,
                pair => new UniversalVtfCompletedProfile(
                    pair.Key,
                    pair.Value,
                    null,
                    Artifact((char)('6' + (int)pair.Key), 256, 128),
                    512,
                    UniversalVtfCacheFixture.Sha('9'),
                    false,
                    null,
                    true,
                    3)));

        byte[] analysisBytes = UniversalVtfCanonicalJson.Serialize(analysis);
        byte[] completionBytes = UniversalVtfCanonicalJson.Serialize(completion);
        UniversalVtfAnalysisReceipt loadedAnalysis =
            UniversalVtfCanonicalJson.Deserialize<UniversalVtfAnalysisReceipt>(analysisBytes);
        UniversalVtfCompletionReceipt loadedCompletion =
            UniversalVtfCanonicalJson.Deserialize<UniversalVtfCompletionReceipt>(completionBytes);

        UniversalVtfCacheContracts.Validate(loadedAnalysis);
        UniversalVtfCacheContracts.Validate(loadedCompletion);
        Assert.Equal(analysisBytes, UniversalVtfCanonicalJson.Serialize(loadedAnalysis));
        Assert.Equal(completionBytes, UniversalVtfCanonicalJson.Serialize(loadedCompletion));
    }

    [Fact]
    public void CompletionReceiptRequiresAllProfilesAndNestedSelections()
    {
        UniversalVtfAnalysisIdentity analysis = UniversalVtfCacheFixture.AnalysisIdentity();
        string analysisKey = UniversalVtfCacheKey.Analysis(analysis);
        var selections = new Dictionary<UniversalVtfProfileKind, UniversalVtfCachedSelection>
        {
            [UniversalVtfProfileKind.Conservative] = new(
                UniversalVtfProfileKind.Conservative,
                2,
                UniversalVtfFilterRoute.SrgbColor),
            [UniversalVtfProfileKind.Balanced] = new(
                UniversalVtfProfileKind.Balanced,
                1,
                UniversalVtfFilterRoute.SrgbColor),
            [UniversalVtfProfileKind.Aggressive] = new(
                UniversalVtfProfileKind.Aggressive,
                2,
                UniversalVtfFilterRoute.NumericLinear)
        };
        var identity = new UniversalVtfCompletionIdentity(
            UniversalVtfCacheKey.CompletionSchema,
            analysisKey,
            UniversalVtfCacheFixture.Sha('2'),
            1_024,
            UniversalVtfCacheKey.None,
            selections);

        InvalidDataException error = Assert.Throws<InvalidDataException>(() =>
            UniversalVtfCacheKey.Completion(identity));

        Assert.Equal("cache_identity_non_nested_completion", error.Message);
    }

    public void Dispose()
    {
        if (Directory.Exists(_tempRoot))
        {
            Directory.Delete(_tempRoot, recursive: true);
        }
    }

    private static UniversalVtfArtifactReference Artifact(
        char sha,
        long bytes,
        int dimension) => new(
        UniversalVtfCacheFixture.Sha(sha),
        bytes,
        dimension,
        dimension,
        1,
        1,
        1,
        4,
        15);

    private static UniversalVtfCachedSelection Cached(
        UniversalVtfProfileKind profile,
        UniversalVtfSelectedLevel selected) => new(
        profile,
        selected.Level,
        selected.Route);
}
