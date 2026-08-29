using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfPreparationIdentityTests
{
    [Fact]
    public void IdenticalAnalysisInputsProduceIdenticalKey()
    {
        UniversalVtfAnalysisIdentity first = Analysis();
        UniversalVtfAnalysisIdentity second = Analysis();

        Assert.Equal(
            UniversalVtfCacheKey.Analysis(first),
            UniversalVtfCacheKey.Analysis(second));
    }

    [Fact]
    public void EveryRelevantAnalysisMutationChangesKey()
    {
        UniversalVtfAnalysisIdentity baseline = Analysis();
        UniversalVtfAnalysisIdentity[] changed =
        {
            baseline with { RelativePath = "materials/models/diggercars/test/other.vtf" },
            baseline with { RawSha256 = Sha('b') },
            baseline with { RawBytes = 2049 },
            baseline with { Structure = baseline.Structure with { Width = 256 } },
            baseline with { AlgorithmFingerprint = Sha('c') },
            baseline with { ConsumerProofSha256 = Sha('d') },
            baseline with { SemanticClasses = new[] { "detail", "diffuse" } },
            baseline with { SemanticRoute = UniversalVtfFilterRoute.NumericLinear },
            baseline with { PolicyFingerprint = Sha('e') },
            baseline with { FixtureSha256 = Sha('f') },
            baseline with { ToolchainSha256 = Sha('1') },
            baseline with { BcBridgeSha256 = Sha('2') },
            baseline with { BridgeRowSha256 = Sha('3') },
            baseline with { CandidateParametersFingerprint = Sha('4') }
        };
        string expected = UniversalVtfCacheKey.Analysis(baseline);

        foreach (UniversalVtfAnalysisIdentity mutation in changed)
        {
            Assert.NotEqual(expected, UniversalVtfCacheKey.Analysis(mutation));
        }
    }

    [Fact]
    public void SafeAndRuntimeCapsChangeCompletionButNotAnalysis()
    {
        UniversalVtfAnalysisIdentity analysis = Analysis();
        string analysisKey = UniversalVtfCacheKey.Analysis(analysis);
        UniversalVtfCompletionIdentity baseline = Completion(analysisKey);
        UniversalVtfCompletionIdentity safeChanged = baseline with
        {
            SafeSha256 = Sha('5')
        };
        UniversalVtfCompletionIdentity capsChanged = baseline with
        {
            RuntimeCapsSha256 = Sha('6')
        };

        Assert.Equal(analysisKey, baseline.AnalysisKey);
        Assert.Equal(analysisKey, safeChanged.AnalysisKey);
        Assert.Equal(analysisKey, capsChanged.AnalysisKey);
        Assert.NotEqual(
            UniversalVtfCacheKey.Completion(baseline),
            UniversalVtfCacheKey.Completion(safeChanged));
        Assert.NotEqual(
            UniversalVtfCacheKey.Completion(baseline),
            UniversalVtfCacheKey.Completion(capsChanged));
    }

    [Fact]
    public void SemanticProofIgnoresCollectionInsertionOrder()
    {
        UniversalVtfSemanticDecision first = Semantics(reverse: false);
        UniversalVtfSemanticDecision second = Semantics(reverse: true);

        Assert.Equal(
            UniversalVtfSemanticProof.Compute(first),
            UniversalVtfSemanticProof.Compute(second));
    }

    [Fact]
    public void SemanticProofChangesWhenConsumerReadChannelsChange()
    {
        UniversalVtfSemanticDecision baseline = Semantics(reverse: false);
        VtfConsumerEvidence consumer = baseline.Consumers.Single() with
        {
            ReadChannels = new HashSet<int> { 0, 1, 2, 3 }
        };
        UniversalVtfSemanticDecision changed = baseline with
        {
            Consumers = new[] { consumer }
        };

        Assert.NotEqual(
            UniversalVtfSemanticProof.Compute(baseline),
            UniversalVtfSemanticProof.Compute(changed));
    }

    [Fact]
    public void CacheKeyRejectsNoncanonicalRelativePath()
    {
        UniversalVtfAnalysisIdentity invalid = Analysis() with
        {
            RelativePath = "Materials\\Models\\test.vtf"
        };

        InvalidDataException error = Assert.Throws<InvalidDataException>(() =>
            UniversalVtfCacheKey.Analysis(invalid));

        Assert.Equal("cache_identity_invalid_path:Materials\\Models\\test.vtf", error.Message);
    }

    private static UniversalVtfAnalysisIdentity Analysis() => new(
        UniversalVtfCacheKey.AnalysisSchema,
        Sha('a'),
        "materials/models/diggercars/test/body.vtf",
        Sha('0'),
        2048,
        new UniversalVtfStructuralIdentity(
            7,
            5,
            96,
            0x40,
            512,
            512,
            1,
            1,
            0,
            1,
            10,
            15,
            13,
            16,
            16,
            false,
            Sha('7'),
            Sha('8'),
            Sha('9')),
        Sha('b'),
        new[] { "diffuse" },
        UniversalVtfFilterRoute.SrgbColor,
        Sha('c'),
        Sha('d'),
        Sha('e'),
        Sha('f'),
        UniversalVtfCacheKey.None,
        Sha('1'));

    private static UniversalVtfCompletionIdentity Completion(string analysisKey) => new(
        UniversalVtfCacheKey.CompletionSchema,
        analysisKey,
        Sha('2'),
        1024,
        UniversalVtfCacheKey.None,
        new Dictionary<UniversalVtfProfileKind, UniversalVtfCachedSelection>
        {
            [UniversalVtfProfileKind.Conservative] = new(
                UniversalVtfProfileKind.Conservative,
                1,
                UniversalVtfFilterRoute.SrgbColor),
            [UniversalVtfProfileKind.Balanced] = new(
                UniversalVtfProfileKind.Balanced,
                2,
                UniversalVtfFilterRoute.SrgbColor),
            [UniversalVtfProfileKind.Aggressive] = new(
                UniversalVtfProfileKind.Aggressive,
                3,
                UniversalVtfFilterRoute.SrgbColor)
        });

    private static UniversalVtfSemanticDecision Semantics(bool reverse)
    {
        IReadOnlySet<UniversalVtfSemanticClass> classes = reverse
            ? new HashSet<UniversalVtfSemanticClass>
            {
                UniversalVtfSemanticClass.Text,
                UniversalVtfSemanticClass.Diffuse
            }
            : new HashSet<UniversalVtfSemanticClass>
            {
                UniversalVtfSemanticClass.Diffuse,
                UniversalVtfSemanticClass.Text
            };
        IReadOnlyDictionary<string, IReadOnlyList<string>> parameters = reverse
            ? new Dictionary<string, IReadOnlyList<string>>(StringComparer.OrdinalIgnoreCase)
            {
                ["$phong"] = new[] { "1" },
                ["$alpha"] = new[] { "0.5" }
            }
            : new Dictionary<string, IReadOnlyList<string>>(StringComparer.OrdinalIgnoreCase)
            {
                ["$alpha"] = new[] { "0.5" },
                ["$phong"] = new[] { "1" }
            };
        var consumer = new VtfConsumerEvidence(
            "materials/models/diggercars/test/body.vmt",
            "VertexLitGeneric",
            "$basetexture",
            "models/diggercars/test/body",
            new HashSet<int> { 0, 1, 2 },
            false,
            "literal")
        {
            Parameters = parameters,
            Uncertainties = reverse
                ? new[] { "z_uncertainty", "a_uncertainty" }
                : new[] { "a_uncertainty", "z_uncertainty" }
        };
        return new UniversalVtfSemanticDecision(
            classes,
            UniversalVtfFilterRoute.SrgbColor,
            false,
            0.5,
            new[] { consumer },
            reverse
                ? new[] { "global_z", "global_a" }
                : new[] { "global_a", "global_z" });
    }

    private static string Sha(char value) => new(value, 64);
}
