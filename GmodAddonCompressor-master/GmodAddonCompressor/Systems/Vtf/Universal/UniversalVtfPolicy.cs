using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfThresholds(
    double CommonRgbPsnrMin,
    double CommonRgbSsimMin,
    double EdgeRetentionMin,
    double NormalMeanMax,
    double NormalP95Max,
    double NormalMax,
    double AlphaSsimMin,
    double AlphaMaeMax,
    double AlphaP99Max,
    double CutoutCoverageTopMax,
    double CutoutCoverageMipsMax,
    double CutoutIouMin,
    double EmissiveEnergyDriftMax,
    double EmissiveHotspotIouMin,
    double GlassDeltaEMax,
    double GlassEdgeRetentionMin,
    double TextPsnrMin,
    double TextSsimMin,
    double TextEdgeRetentionMin,
    double DetailFrequencyRetentionMin,
    double DetailSeamIncreaseMax,
    double CubemapSeamIncreaseMax,
    double TemporalPsnrMin,
    double ComponentRetentionMin,
    double HausdorffMax);

internal sealed partial record UniversalVtfToolchain(
    string MagickVersion,
    long MagickManagedBytes,
    string MagickManagedSha256,
    IReadOnlyDictionary<string, string> MagickNativeSha256,
    string MagickFilter,
    string ComparisonFilter,
    string VtfCmdVersion,
    long VtfCmdBytes,
    string VtfCmdSha256,
    string VtfEditArchiveSha256,
    IReadOnlyList<string> VtfCmdArgumentTemplate);

internal sealed class UniversalVtfPolicy
{
    private const string FixtureResourceName =
        "GmodAddonCompressor.Resources.universal-vtf-v1-fixtures.json";
    private const string ExpectedFixtureSha256 =
        "9adb529e565f9e1ba54f0833af21c59db8674b5bed286d9cac37c6f8ace13d7b";
    private const string ExpectedMagickManagedSha256 =
        "5e2ff85094dfd8fa9e15c3f884c3e3ef483bc074538b8c4fc8e1e0aaa643dfd6";
    private const string ExpectedVtfCmdSha256 =
        "30d185a6df81d0c5d09c7ecda91a3f49ff183c14a2db12aaa845dc88059ee11e";
    private const string ExpectedVtfEditArchiveSha256 =
        "87d0280223f60be947723cb67076ed47db55221ee69ca60a7f773e4926f11c3c";

    private static readonly string[] ExpectedVtfCmdArguments =
    {
        "-file", "<input.png>", "-output", "<isolated-dir>",
        "-format", "<DXT1|DXT5>", "-alphaformat", "<same>",
        "-version", "<source-version-clamped-to-7.4>"
    };

    private static readonly UniversalVtfThresholds Conservative = new(
        CommonRgbPsnrMin: 26,
        CommonRgbSsimMin: .950,
        EdgeRetentionMin: .50,
        NormalMeanMax: 3,
        NormalP95Max: 8,
        NormalMax: 25,
        AlphaSsimMin: .980,
        AlphaMaeMax: .020,
        AlphaP99Max: .080,
        CutoutCoverageTopMax: .01,
        CutoutCoverageMipsMax: .02,
        CutoutIouMin: .980,
        EmissiveEnergyDriftMax: .05,
        EmissiveHotspotIouMin: .950,
        GlassDeltaEMax: 2,
        GlassEdgeRetentionMin: .65,
        TextPsnrMin: 30,
        TextSsimMin: .970,
        TextEdgeRetentionMin: .70,
        DetailFrequencyRetentionMin: .70,
        DetailSeamIncreaseMax: 1d / 255,
        CubemapSeamIncreaseMax: 2d / 255,
        TemporalPsnrMin: 26,
        ComponentRetentionMin: 1.00,
        HausdorffMax: 2);

    private static readonly UniversalVtfThresholds Balanced = new(
        CommonRgbPsnrMin: 24,
        CommonRgbSsimMin: .925,
        EdgeRetentionMin: .40,
        NormalMeanMax: 5,
        NormalP95Max: 14,
        NormalMax: 40,
        AlphaSsimMin: .960,
        AlphaMaeMax: .040,
        AlphaP99Max: .140,
        CutoutCoverageTopMax: .02,
        CutoutCoverageMipsMax: .04,
        CutoutIouMin: .950,
        EmissiveEnergyDriftMax: .10,
        EmissiveHotspotIouMin: .900,
        GlassDeltaEMax: 4,
        GlassEdgeRetentionMin: .55,
        TextPsnrMin: 27,
        TextSsimMin: .950,
        TextEdgeRetentionMin: .55,
        DetailFrequencyRetentionMin: .55,
        DetailSeamIncreaseMax: 2d / 255,
        CubemapSeamIncreaseMax: 4d / 255,
        TemporalPsnrMin: 24,
        ComponentRetentionMin: .99,
        HausdorffMax: 4);

    private static readonly UniversalVtfThresholds Aggressive = new(
        CommonRgbPsnrMin: 22,
        CommonRgbSsimMin: .900,
        EdgeRetentionMin: .30,
        NormalMeanMax: 8,
        NormalP95Max: 22,
        NormalMax: 60,
        AlphaSsimMin: .930,
        AlphaMaeMax: .070,
        AlphaP99Max: .240,
        CutoutCoverageTopMax: .04,
        CutoutCoverageMipsMax: .07,
        CutoutIouMin: .900,
        EmissiveEnergyDriftMax: .20,
        EmissiveHotspotIouMin: .800,
        GlassDeltaEMax: 6,
        GlassEdgeRetentionMin: .45,
        TextPsnrMin: 24,
        TextSsimMin: .920,
        TextEdgeRetentionMin: .40,
        DetailFrequencyRetentionMin: .40,
        DetailSeamIncreaseMax: 4d / 255,
        CubemapSeamIncreaseMax: 8d / 255,
        TemporalPsnrMin: 22,
        ComponentRetentionMin: .97,
        HausdorffMax: 8);

    private readonly byte[] _fixtureBytes;
    private readonly byte[] _canonicalPolicyBytes;

    private UniversalVtfPolicy()
    {
        _fixtureBytes = ReadEmbeddedFixture();
        string actualFixtureSha256 = Sha256(_fixtureBytes);
        if (!actualFixtureSha256.Equals(ExpectedFixtureSha256, StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                $"universal_vtf_fixture_hash:{actualFixtureSha256}!={ExpectedFixtureSha256}");
        }

        using (JsonDocument fixture = JsonDocument.Parse(_fixtureBytes))
        {
            string? schema = fixture.RootElement.GetProperty("schema").GetString();
            if (!"universal-vtf-v1-fixtures".Equals(schema, StringComparison.Ordinal))
            {
                throw new InvalidDataException($"universal_vtf_fixture_schema:{schema}");
            }
        }

        VerifyNesting();
        _canonicalPolicyBytes = WriteCanonicalPolicy();
    }

    internal static UniversalVtfPolicy V1 { get; } = new();

    internal string FixtureSha256 => ExpectedFixtureSha256;

    internal string CanonicalPolicyJson => Encoding.UTF8.GetString(_canonicalPolicyBytes);

    internal byte[] LoadFixtureBytes() => (byte[])_fixtureBytes.Clone();

    internal UniversalVtfThresholds GetThresholds(UniversalVtfProfileKind profile) =>
        profile switch
        {
            UniversalVtfProfileKind.Conservative => Conservative,
            UniversalVtfProfileKind.Balanced => Balanced,
            UniversalVtfProfileKind.Aggressive => Aggressive,
            _ => throw new ArgumentOutOfRangeException(nameof(profile), profile, null)
        };

    internal string ComputeFingerprint(
        UniversalVtfToolchain toolchain,
        string bcBridgeSha256)
    {
        ArgumentNullException.ThrowIfNull(toolchain);
        string bridge = RequireSha256(bcBridgeSha256, nameof(bcBridgeSha256));
        ValidatedToolchain validated = ValidateToolchain(toolchain);

        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions
               {
                   Indented = false,
                   SkipValidation = false
               }))
        {
            writer.WriteStartObject();
            writer.WriteString("schema", "universal-vtf-policy-fingerprint-v1");
            writer.WritePropertyName("policy");
            writer.WriteRawValue(_canonicalPolicyBytes, skipInputValidation: false);
            writer.WritePropertyName("toolchain");
            WriteToolchain(writer, validated);
            writer.WriteString("bc3RawToApprovedSha256", bridge);
            writer.WriteEndObject();
        }

        return Sha256(stream.ToArray());
    }

    private static byte[] ReadEmbeddedFixture()
    {
        Assembly assembly = typeof(UniversalVtfPolicy).Assembly;
        using Stream stream = assembly.GetManifestResourceStream(FixtureResourceName)
            ?? throw new InvalidDataException($"missing_embedded_resource:{FixtureResourceName}");
        using var output = new MemoryStream();
        stream.CopyTo(output);
        return output.ToArray();
    }

    private static byte[] WriteCanonicalPolicy()
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions
               {
                   Indented = false,
                   SkipValidation = false
               }))
        {
            writer.WriteStartObject();
            writer.WriteString("id", "universal-vtf-v1");
            writer.WriteString("fixtureSha256", ExpectedFixtureSha256);
            writer.WriteNumber("mandatoryFloorLevel", 1);
            writer.WriteString("colorFilter", "Magick.NET-14.10.4-LanczosSharp");
            writer.WriteString("comparisonFilter", "bilinear-domain-aware-v1");
            writer.WriteString("ssim", "rgb-min-channel;11x11;sigma=1.5;k1=.01;k2=.03;population;reflect101");
            writer.WriteString("percentile", "nearest-rank-ceil-pn-minus-one");
            writer.WriteString("deltaE", "CIEDE2000-D65-2degree");
            writer.WriteString("cutoutClassifier", "poles=.1,.9;combined>=.95;each>=.01");
            writer.WriteNumber("coverageBisectionSteps", 32);
            writer.WritePropertyName("coverageSeeds");
            writer.WriteStartArray();
            writer.WriteNumberValue(0);
            writer.WriteNumberValue(1);
            writer.WriteNumberValue(8);
            writer.WriteEndArray();
            writer.WriteString("coverageQuantization", "round-to-nearest-even-uint8");
            writer.WriteString("retentionZeroRule", "both-zero=1;source-zero-candidate-nonzero=fail");
            writer.WriteString("iouEmptyRule", "both-empty=1;one-empty=0");
            writer.WriteString("routeScore", "minimum-normalized-margin;nan-fail;srgb-flag-tie;numeric-final");
            writer.WritePropertyName("thresholds");
            writer.WriteStartObject();
            WriteThresholds(writer, "conservative", Conservative);
            WriteThresholds(writer, "balanced", Balanced);
            WriteThresholds(writer, "aggressive", Aggressive);
            writer.WriteEndObject();
            writer.WriteEndObject();
        }

        return stream.ToArray();
    }

    private static void WriteThresholds(
        Utf8JsonWriter writer,
        string name,
        UniversalVtfThresholds value)
    {
        writer.WritePropertyName(name);
        writer.WriteStartObject();
        writer.WriteNumber("commonRgbPsnrMin", value.CommonRgbPsnrMin);
        writer.WriteNumber("commonRgbSsimMin", value.CommonRgbSsimMin);
        writer.WriteNumber("edgeRetentionMin", value.EdgeRetentionMin);
        writer.WriteNumber("normalMeanMax", value.NormalMeanMax);
        writer.WriteNumber("normalP95Max", value.NormalP95Max);
        writer.WriteNumber("normalMax", value.NormalMax);
        writer.WriteNumber("alphaSsimMin", value.AlphaSsimMin);
        writer.WriteNumber("alphaMaeMax", value.AlphaMaeMax);
        writer.WriteNumber("alphaP99Max", value.AlphaP99Max);
        writer.WriteNumber("cutoutCoverageTopMax", value.CutoutCoverageTopMax);
        writer.WriteNumber("cutoutCoverageMipsMax", value.CutoutCoverageMipsMax);
        writer.WriteNumber("cutoutIouMin", value.CutoutIouMin);
        writer.WriteNumber("emissiveEnergyDriftMax", value.EmissiveEnergyDriftMax);
        writer.WriteNumber("emissiveHotspotIouMin", value.EmissiveHotspotIouMin);
        writer.WriteNumber("glassDeltaEMax", value.GlassDeltaEMax);
        writer.WriteNumber("glassEdgeRetentionMin", value.GlassEdgeRetentionMin);
        writer.WriteNumber("textPsnrMin", value.TextPsnrMin);
        writer.WriteNumber("textSsimMin", value.TextSsimMin);
        writer.WriteNumber("textEdgeRetentionMin", value.TextEdgeRetentionMin);
        writer.WriteNumber("detailFrequencyRetentionMin", value.DetailFrequencyRetentionMin);
        writer.WriteNumber("detailSeamIncreaseMax", value.DetailSeamIncreaseMax);
        writer.WriteNumber("cubemapSeamIncreaseMax", value.CubemapSeamIncreaseMax);
        writer.WriteNumber("temporalPsnrMin", value.TemporalPsnrMin);
        writer.WriteNumber("componentRetentionMin", value.ComponentRetentionMin);
        writer.WriteNumber("hausdorffMax", value.HausdorffMax);
        writer.WriteEndObject();
    }

    private static ValidatedToolchain ValidateToolchain(UniversalVtfToolchain toolchain)
    {
        RequireExact(toolchain.MagickVersion, "14.10.4", nameof(toolchain.MagickVersion));
        RequireExact(toolchain.MagickManagedBytes, 1_126_696, nameof(toolchain.MagickManagedBytes));
        RequireExact(
            RequireSha256(toolchain.MagickManagedSha256, nameof(toolchain.MagickManagedSha256)),
            ExpectedMagickManagedSha256,
            nameof(toolchain.MagickManagedSha256));
        RequireExact(toolchain.MagickFilter, "LanczosSharp", nameof(toolchain.MagickFilter));
        RequireExact(
            toolchain.ComparisonFilter,
            "bilinear-domain-aware-v1",
            nameof(toolchain.ComparisonFilter));
        RequireExact(toolchain.VtfCmdVersion, "1.0.10", nameof(toolchain.VtfCmdVersion));
        RequireExact(toolchain.VtfCmdBytes, 102_400, nameof(toolchain.VtfCmdBytes));
        RequireExact(
            RequireSha256(toolchain.VtfCmdSha256, nameof(toolchain.VtfCmdSha256)),
            ExpectedVtfCmdSha256,
            nameof(toolchain.VtfCmdSha256));
        RequireExact(
            RequireSha256(
                toolchain.VtfEditArchiveSha256,
                nameof(toolchain.VtfEditArchiveSha256)),
            ExpectedVtfEditArchiveSha256,
            nameof(toolchain.VtfEditArchiveSha256));

        if (!toolchain.VtfCmdArgumentTemplate.SequenceEqual(
                ExpectedVtfCmdArguments,
                StringComparer.Ordinal))
        {
            throw new ArgumentException(
                "VTFCmd argument template is not the frozen V1 invocation.",
                nameof(toolchain.VtfCmdArgumentTemplate));
        }

        if (toolchain.MagickNativeSha256.Count == 0)
        {
            throw new ArgumentException(
                "At least one loaded Magick native binary hash is required.",
                nameof(toolchain.MagickNativeSha256));
        }

        var native = new SortedDictionary<string, string>(StringComparer.Ordinal);
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach ((string requestedName, string requestedHash) in toolchain.MagickNativeSha256)
        {
            string name = NormalizeRelativeToolName(requestedName);
            if (!seen.Add(name))
            {
                throw new ArgumentException(
                    $"Case-folded native tool collision: {name}",
                    nameof(toolchain.MagickNativeSha256));
            }

            native.Add(
                name,
                RequireSha256(requestedHash, nameof(toolchain.MagickNativeSha256)));
        }

        return new ValidatedToolchain(toolchain, native);
    }

    private static void WriteToolchain(Utf8JsonWriter writer, ValidatedToolchain value)
    {
        UniversalVtfToolchain toolchain = value.Value;
        writer.WriteStartObject();
        writer.WriteString("magickVersion", toolchain.MagickVersion);
        writer.WriteNumber("magickManagedBytes", toolchain.MagickManagedBytes);
        writer.WriteString("magickManagedSha256", toolchain.MagickManagedSha256.ToLowerInvariant());
        writer.WritePropertyName("magickNativeSha256");
        writer.WriteStartObject();
        foreach ((string name, string sha256) in value.NativeHashes)
        {
            writer.WriteString(name, sha256);
        }

        writer.WriteEndObject();
        writer.WriteString("magickFilter", toolchain.MagickFilter);
        writer.WriteString("comparisonFilter", toolchain.ComparisonFilter);
        writer.WriteString("vtfCmdVersion", toolchain.VtfCmdVersion);
        writer.WriteNumber("vtfCmdBytes", toolchain.VtfCmdBytes);
        writer.WriteString("vtfCmdSha256", toolchain.VtfCmdSha256.ToLowerInvariant());
        writer.WriteString("vtfEditArchiveSha256", toolchain.VtfEditArchiveSha256.ToLowerInvariant());
        writer.WritePropertyName("vtfCmdArgumentTemplate");
        writer.WriteStartArray();
        foreach (string argument in toolchain.VtfCmdArgumentTemplate)
        {
            writer.WriteStringValue(argument);
        }

        writer.WriteEndArray();
        writer.WriteEndObject();
    }

    private static string NormalizeRelativeToolName(string requested)
    {
        if (string.IsNullOrWhiteSpace(requested) || Path.IsPathRooted(requested))
        {
            throw new ArgumentException("Native tool names must be relative.", nameof(requested));
        }

        string normalized = requested.Replace('\\', '/');
        string[] segments = normalized.Split('/');
        if (segments.Any(segment => string.IsNullOrEmpty(segment) || segment is "." or ".."))
        {
            throw new ArgumentException("Native tool name is not canonical.", nameof(requested));
        }

        return string.Join('/', segments);
    }

    private static string RequireSha256(string requested, string parameterName)
    {
        if (requested is null || requested.Length != 64 ||
            requested.Any(value => !Uri.IsHexDigit(value)))
        {
            throw new ArgumentException("A 64-character SHA-256 hex digest is required.", parameterName);
        }

        return requested.ToLowerInvariant();
    }

    private static void RequireExact<T>(T actual, T expected, string parameterName)
        where T : IEquatable<T>
    {
        if (!actual.Equals(expected))
        {
            throw new ArgumentException($"Expected {expected}; received {actual}.", parameterName);
        }
    }

    private static string Sha256(ReadOnlySpan<byte> bytes) =>
        Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private static void VerifyNesting()
    {
        CheckMinimum(Conservative.CommonRgbPsnrMin, Balanced.CommonRgbPsnrMin, Aggressive.CommonRgbPsnrMin);
        CheckMinimum(Conservative.CommonRgbSsimMin, Balanced.CommonRgbSsimMin, Aggressive.CommonRgbSsimMin);
        CheckMinimum(Conservative.EdgeRetentionMin, Balanced.EdgeRetentionMin, Aggressive.EdgeRetentionMin);
        CheckMaximum(Conservative.NormalMeanMax, Balanced.NormalMeanMax, Aggressive.NormalMeanMax);
        CheckMaximum(Conservative.NormalP95Max, Balanced.NormalP95Max, Aggressive.NormalP95Max);
        CheckMaximum(Conservative.NormalMax, Balanced.NormalMax, Aggressive.NormalMax);
        CheckMinimum(Conservative.AlphaSsimMin, Balanced.AlphaSsimMin, Aggressive.AlphaSsimMin);
        CheckMaximum(Conservative.AlphaMaeMax, Balanced.AlphaMaeMax, Aggressive.AlphaMaeMax);
        CheckMaximum(Conservative.AlphaP99Max, Balanced.AlphaP99Max, Aggressive.AlphaP99Max);
        CheckMaximum(Conservative.CutoutCoverageTopMax, Balanced.CutoutCoverageTopMax, Aggressive.CutoutCoverageTopMax);
        CheckMaximum(Conservative.CutoutCoverageMipsMax, Balanced.CutoutCoverageMipsMax, Aggressive.CutoutCoverageMipsMax);
        CheckMinimum(Conservative.CutoutIouMin, Balanced.CutoutIouMin, Aggressive.CutoutIouMin);
        CheckMaximum(Conservative.EmissiveEnergyDriftMax, Balanced.EmissiveEnergyDriftMax, Aggressive.EmissiveEnergyDriftMax);
        CheckMinimum(Conservative.EmissiveHotspotIouMin, Balanced.EmissiveHotspotIouMin, Aggressive.EmissiveHotspotIouMin);
        CheckMaximum(Conservative.GlassDeltaEMax, Balanced.GlassDeltaEMax, Aggressive.GlassDeltaEMax);
        CheckMinimum(Conservative.GlassEdgeRetentionMin, Balanced.GlassEdgeRetentionMin, Aggressive.GlassEdgeRetentionMin);
        CheckMinimum(Conservative.TextPsnrMin, Balanced.TextPsnrMin, Aggressive.TextPsnrMin);
        CheckMinimum(Conservative.TextSsimMin, Balanced.TextSsimMin, Aggressive.TextSsimMin);
        CheckMinimum(Conservative.TextEdgeRetentionMin, Balanced.TextEdgeRetentionMin, Aggressive.TextEdgeRetentionMin);
        CheckMinimum(Conservative.DetailFrequencyRetentionMin, Balanced.DetailFrequencyRetentionMin, Aggressive.DetailFrequencyRetentionMin);
        CheckMaximum(Conservative.DetailSeamIncreaseMax, Balanced.DetailSeamIncreaseMax, Aggressive.DetailSeamIncreaseMax);
        CheckMaximum(Conservative.CubemapSeamIncreaseMax, Balanced.CubemapSeamIncreaseMax, Aggressive.CubemapSeamIncreaseMax);
        CheckMinimum(Conservative.TemporalPsnrMin, Balanced.TemporalPsnrMin, Aggressive.TemporalPsnrMin);
        CheckMinimum(Conservative.ComponentRetentionMin, Balanced.ComponentRetentionMin, Aggressive.ComponentRetentionMin);
        CheckMaximum(Conservative.HausdorffMax, Balanced.HausdorffMax, Aggressive.HausdorffMax);
    }

    private static void CheckMinimum(double conservative, double balanced, double aggressive)
    {
        if (!(conservative >= balanced && balanced >= aggressive))
        {
            throw new InvalidDataException("universal_vtf_policy_not_nested:minimum");
        }
    }

    private static void CheckMaximum(double conservative, double balanced, double aggressive)
    {
        if (!(conservative <= balanced && balanced <= aggressive))
        {
            throw new InvalidDataException("universal_vtf_policy_not_nested:maximum");
        }
    }

    private sealed record ValidatedToolchain(
        UniversalVtfToolchain Value,
        IReadOnlyDictionary<string, string> NativeHashes);
}
