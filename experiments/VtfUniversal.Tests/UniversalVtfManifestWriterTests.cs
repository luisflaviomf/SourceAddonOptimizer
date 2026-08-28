using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfManifestWriterTests : IDisposable
{
    private readonly string _root = Path.Combine(
        Path.GetTempPath(),
        "GmodAddonOptimizer",
        "UniversalVtfManifestWriterTests",
        Guid.NewGuid().ToString("N"));

    public UniversalVtfManifestWriterTests() => Directory.CreateDirectory(_root);

    [Fact]
    public void SeparatesSavings()
    {
        UniversalVtfManifestRow row = Row(rawBytes: 1000, spatialBytes: 250, finalBytes: 125);

        Assert.Equal(750, row.ResolutionSavedBytes);
        Assert.Equal(125, row.FormatSavedBytes);
        Assert.Equal(875, row.TotalSavedBytes);
    }

    [Fact]
    public void DuplicatePathFails()
    {
        UniversalVtfBuildReceipt receipt = Receipt(new[]
        {
            Row(),
            Row() with { RelativePath = "MATERIALS/A.VTF" }
        });

        Assert.Throws<InvalidDataException>(() => UniversalVtfManifestWriter.Validate(receipt));
    }

    [Fact]
    public void RecordsEveryDecisionInputHash()
    {
        UniversalVtfBuildReceipt receipt = Receipt(new[] { Row() });

        AssertSha256(receipt.PolicyFingerprint);
        AssertSha256(receipt.FixtureSha256);
        AssertSha256(receipt.ToolchainSha256);
        AssertSha256(receipt.BcBridgeSha256);
        Assert.True(receipt.RuntimeCapsSha256 is null || IsSha256(receipt.RuntimeCapsSha256));
    }

    [Fact]
    public async Task CanonicalManifestRoundTripsByteIdentically()
    {
        string first = Path.Combine(_root, "first.json");
        string second = Path.Combine(_root, "second.json");
        UniversalVtfManifestFrozen frozen = await UniversalVtfManifestWriter.WriteAsync(
            first,
            Receipt(new[] { Row() }),
            CancellationToken.None);
        UniversalVtfManifest loaded = UniversalVtfManifestStore.Load(first, frozen.Sha256);
        UniversalVtfManifestFrozen repeated = await UniversalVtfManifestWriter.WriteManifestAsync(
            second,
            loaded,
            CancellationToken.None);

        Assert.Equal(frozen.Sha256, repeated.Sha256);
        Assert.Equal(File.ReadAllBytes(first), File.ReadAllBytes(second));
    }

    [Fact]
    public async Task AggregateExposesRawSafeAndFinalViews()
    {
        string path = Path.Combine(_root, "aggregate.json");
        UniversalVtfManifestFrozen frozen = await UniversalVtfManifestWriter.WriteAsync(
            path,
            Receipt(new[] { Row(rawBytes: 1000, spatialBytes: 250, finalBytes: 125) }),
            CancellationToken.None);

        Assert.Equal(875, frozen.Manifest.Aggregate.Vtf.RawToFinal.SavedBytes);
        Assert.Equal(375, frozen.Manifest.Aggregate.Vtf.SafeToFinal.SavedBytes);
        Assert.Equal(1000, frozen.Manifest.Aggregate.Vtf.RawBytes);
        Assert.Equal(500, frozen.Manifest.Aggregate.Vtf.SafeBytes);
        Assert.Equal(125, frozen.Manifest.Aggregate.Vtf.FinalBytes);
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }

    private static UniversalVtfBuildReceipt Receipt(IReadOnlyList<UniversalVtfManifestRow> rows) =>
        new(
            UniversalVtfProfileKind.Balanced,
            Hash('a'),
            Hash('b'),
            Hash('c'),
            Hash('d'),
            null,
            new UniversalVtfManifestTree("raw", 10, 10_000, Hash('e')),
            new UniversalVtfManifestTree("safe", 10, 5_000, Hash('f')),
            new UniversalVtfManifestTree("final", 10, 4_000, Hash('1')),
            rows);

    private static UniversalVtfManifestRow Row(
        long rawBytes = 1000,
        long spatialBytes = 500,
        long finalBytes = 500) =>
        new(
            RelativePath: "materials/a.vtf",
            Family: "a",
            SemanticClasses: new[] { "diffuse" },
            ConsumerProofSha256: Hash('2'),
            RawWidth: 8,
            RawHeight: 8,
            RawDepth: 1,
            RawFrames: 1,
            RawFaces: 1,
            RawMipCount: 4,
            RawFormat: 15,
            FinalWidth: 4,
            FinalHeight: 4,
            FinalDepth: 1,
            FinalMipCount: 3,
            FinalFormat: finalBytes < spatialBytes ? 13 : 15,
            Authored: true,
            Generated: false,
            SelectedLevel: 1,
            ConservativeLevel: 1,
            BalancedLevel: 1,
            AggressiveLevel: 2,
            Route: UniversalVtfFilterRoute.NumericLinear,
            ProfileAttempts: new Dictionary<UniversalVtfProfileKind,
                IReadOnlyList<UniversalVtfProfileAttempt>>(),
            RouteAttempts: Array.Empty<UniversalVtfManifestRouteAttempt>(),
            WorstGate: null,
            RawBytes: rawBytes,
            RawSha256: Hash('3'),
            SafeBytes: 500,
            SafeSha256: Hash('4'),
            SpatialBytes: spatialBytes,
            SpatialSha256: Hash('5'),
            FinalBytes: finalBytes,
            FinalSha256: finalBytes == spatialBytes ? Hash('5') : Hash('6'),
            MandatoryFloorApplied: true,
            ByteFloor: false,
            Blocker: null,
            BcEligibilityMode: finalBytes < spatialBytes
                ? GmodAddonCompressor.Systems.Vtf.Universal.Bc3EligibilityMode.PhysicalOpaque
                : null,
            BcReceiptSha256: finalBytes < spatialBytes ? Hash('d') : null,
            LowResolutionImageRegenerated: false,
            ResourceCount: 0);

    private static void AssertSha256(string value) => Assert.True(IsSha256(value));

    private static bool IsSha256(string value) =>
        value.Length == 64 && value.All(Uri.IsHexDigit);

    private static string Hash(char value) => new(value, 64);
}
