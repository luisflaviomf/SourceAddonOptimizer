using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfPolicyTests
{
    [Fact]
    public void ThresholdsAreNestedAcrossEveryMinimumAndMaximumGate()
    {
        UniversalVtfThresholds conservative = UniversalVtfPolicy.V1.GetThresholds(
            UniversalVtfProfileKind.Conservative);
        UniversalVtfThresholds balanced = UniversalVtfPolicy.V1.GetThresholds(
            UniversalVtfProfileKind.Balanced);
        UniversalVtfThresholds aggressive = UniversalVtfPolicy.V1.GetThresholds(
            UniversalVtfProfileKind.Aggressive);

        AssertMinimum(conservative.CommonRgbPsnrMin, balanced.CommonRgbPsnrMin, aggressive.CommonRgbPsnrMin);
        AssertMinimum(conservative.CommonRgbSsimMin, balanced.CommonRgbSsimMin, aggressive.CommonRgbSsimMin);
        AssertMinimum(conservative.EdgeRetentionMin, balanced.EdgeRetentionMin, aggressive.EdgeRetentionMin);
        AssertMaximum(conservative.NormalMeanMax, balanced.NormalMeanMax, aggressive.NormalMeanMax);
        AssertMaximum(conservative.NormalP95Max, balanced.NormalP95Max, aggressive.NormalP95Max);
        AssertMaximum(conservative.NormalMax, balanced.NormalMax, aggressive.NormalMax);
        AssertMinimum(conservative.AlphaSsimMin, balanced.AlphaSsimMin, aggressive.AlphaSsimMin);
        AssertMaximum(conservative.AlphaMaeMax, balanced.AlphaMaeMax, aggressive.AlphaMaeMax);
        AssertMaximum(conservative.AlphaP99Max, balanced.AlphaP99Max, aggressive.AlphaP99Max);
        AssertMaximum(conservative.CutoutCoverageTopMax, balanced.CutoutCoverageTopMax, aggressive.CutoutCoverageTopMax);
        AssertMaximum(conservative.CutoutCoverageMipsMax, balanced.CutoutCoverageMipsMax, aggressive.CutoutCoverageMipsMax);
        AssertMinimum(conservative.CutoutIouMin, balanced.CutoutIouMin, aggressive.CutoutIouMin);
        AssertMaximum(conservative.EmissiveEnergyDriftMax, balanced.EmissiveEnergyDriftMax, aggressive.EmissiveEnergyDriftMax);
        AssertMinimum(conservative.EmissiveHotspotIouMin, balanced.EmissiveHotspotIouMin, aggressive.EmissiveHotspotIouMin);
        AssertMaximum(conservative.GlassDeltaEMax, balanced.GlassDeltaEMax, aggressive.GlassDeltaEMax);
        AssertMinimum(conservative.GlassEdgeRetentionMin, balanced.GlassEdgeRetentionMin, aggressive.GlassEdgeRetentionMin);
        AssertMinimum(conservative.TextPsnrMin, balanced.TextPsnrMin, aggressive.TextPsnrMin);
        AssertMinimum(conservative.TextSsimMin, balanced.TextSsimMin, aggressive.TextSsimMin);
        AssertMinimum(conservative.TextEdgeRetentionMin, balanced.TextEdgeRetentionMin, aggressive.TextEdgeRetentionMin);
        AssertMinimum(conservative.DetailFrequencyRetentionMin, balanced.DetailFrequencyRetentionMin, aggressive.DetailFrequencyRetentionMin);
        AssertMaximum(conservative.DetailSeamIncreaseMax, balanced.DetailSeamIncreaseMax, aggressive.DetailSeamIncreaseMax);
        AssertMaximum(conservative.CubemapSeamIncreaseMax, balanced.CubemapSeamIncreaseMax, aggressive.CubemapSeamIncreaseMax);
        AssertMinimum(conservative.TemporalPsnrMin, balanced.TemporalPsnrMin, aggressive.TemporalPsnrMin);
        AssertMinimum(conservative.ComponentRetentionMin, balanced.ComponentRetentionMin, aggressive.ComponentRetentionMin);
        AssertMaximum(conservative.HausdorffMax, balanced.HausdorffMax, aggressive.HausdorffMax);
    }

    [Fact]
    public void BcBridgeHashIsARequiredPolicyInput()
    {
        string first = UniversalVtfPolicy.V1.ComputeFingerprint(
            FakeToolchain.Valid(),
            "00" + new string('1', 62));
        string second = UniversalVtfPolicy.V1.ComputeFingerprint(
            FakeToolchain.Valid(),
            "00" + new string('2', 62));

        Assert.NotEqual(first, second);
        Assert.Throws<ArgumentException>(() =>
            UniversalVtfPolicy.V1.ComputeFingerprint(
                FakeToolchain.Valid(),
                "not-a-sha256"));
    }

    [Fact]
    public void NativeHashOrderDoesNotChangeCanonicalFingerprint()
    {
        UniversalVtfToolchain first = FakeToolchain.Valid(new Dictionary<string, string>
        {
            { "runtimes/win-x64/native/a.dll", new string('a', 64) },
            { "runtimes/win-x64/native/b.dll", new string('b', 64) }
        });
        UniversalVtfToolchain second = FakeToolchain.Valid(new Dictionary<string, string>
        {
            { "runtimes/win-x64/native/b.dll", new string('b', 64) },
            { "runtimes/win-x64/native/a.dll", new string('a', 64) }
        });

        Assert.Equal(
            UniversalVtfPolicy.V1.ComputeFingerprint(first, new string('c', 64)),
            UniversalVtfPolicy.V1.ComputeFingerprint(second, new string('c', 64)));
    }

    [Fact]
    public void EmbeddedFixtureIsCanonicalTimestampFreeAndHasRequiredCases()
    {
        byte[] bytes = UniversalVtfPolicy.V1.LoadFixtureBytes();
        string actualSha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        using JsonDocument fixture = JsonDocument.Parse(bytes);

        Assert.Equal(
            "9adb529e565f9e1ba54f0833af21c59db8674b5bed286d9cac37c6f8ace13d7b",
            actualSha256);
        Assert.Equal(UniversalVtfPolicy.V1.FixtureSha256, actualSha256);
        Assert.Equal("universal-vtf-v1-fixtures", fixture.RootElement.GetProperty("schema").GetString());
        string json = Encoding.UTF8.GetString(bytes);
        Assert.DoesNotContain("generated", json, StringComparison.OrdinalIgnoreCase);
        JsonElement cases = fixture.RootElement.GetProperty("cases");
        foreach (string required in new[]
                 {
                     "identityRgba", "halfPlaneRgb", "alphaClassification",
                     "normalAngles", "normalLayouts", "nearestRank",
                     "reflect101", "retentionZero", "routeTie",
                     "coverageScale32", "temporalFrames", "cubemapSeam"
                 })
        {
            Assert.True(cases.TryGetProperty(required, out _), required);
        }
    }

    [Fact]
    public void CanonicalFingerprintIsFrozen()
    {
        string actual = UniversalVtfPolicy.V1.ComputeFingerprint(
            FakeToolchain.Valid(),
            new string('c', 64));

        Assert.Equal(
            "4f344e9bee7a1f0695321a964519ed40abce6a9b9f89756d03c18aa5ef77a33e",
            actual);
    }

    private static void AssertMinimum(double conservative, double balanced, double aggressive) =>
        Assert.True(conservative >= balanced && balanced >= aggressive);

    private static void AssertMaximum(double conservative, double balanced, double aggressive) =>
        Assert.True(conservative <= balanced && balanced <= aggressive);

    private static class FakeToolchain
    {
        internal static UniversalVtfToolchain Valid(
            IReadOnlyDictionary<string, string>? nativeHashes = null) =>
            new(
                MagickVersion: "14.10.4",
                MagickManagedBytes: 1_126_696,
                MagickManagedSha256: "5e2ff85094dfd8fa9e15c3f884c3e3ef483bc074538b8c4fc8e1e0aaa643dfd6",
                MagickNativeSha256: nativeHashes ?? new Dictionary<string, string>
                {
                    { "runtimes/win-x64/native/Magick.Native-Q16-x64.dll", new string('d', 64) }
                },
                MagickFilter: "LanczosSharp",
                ComparisonFilter: "bilinear-domain-aware-v1",
                VtfCmdVersion: "1.0.10",
                VtfCmdBytes: 102_400,
                VtfCmdSha256: "30d185a6df81d0c5d09c7ecda91a3f49ff183c14a2db12aaa845dc88059ee11e",
                VtfEditArchiveSha256: "87d0280223f60be947723cb67076ed47db55221ee69ca60a7f773e4926f11c3c",
                VtfCmdArgumentTemplate: new[]
                {
                    "-file", "<input.png>", "-output", "<isolated-dir>",
                    "-format", "<DXT1|DXT5>", "-alphaformat", "<same>",
                    "-version", "<source-version-clamped-to-7.4>"
                });
    }
}
