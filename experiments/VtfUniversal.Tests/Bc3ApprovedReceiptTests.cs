using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class Bc3ApprovedReceiptTests : IDisposable
{
    private readonly string _root = Path.Combine(
        Path.GetTempPath(),
        "GmodAddonOptimizer",
        "Bc3ApprovedReceiptTests",
        Guid.NewGuid().ToString("N"));

    public Bc3ApprovedReceiptTests() => Directory.CreateDirectory(_root);

    [Fact]
    public void RejectsSafeHashAsRawAuthority()
    {
        Bc3RawBridge bridge = FixtureBridge() with
        {
            Rows = new[]
            {
                FixtureBridge().Rows[0] with
                {
                    RawSha256 = FixtureBridge().Rows[0].ApprovedSafeSha256
                }
            }
        };

        Assert.Throws<InvalidDataException>(() =>
            Bc3ApprovedReceipt.ValidateRawAuthority(
                bridge,
                new Dictionary<string, (long Bytes, string Sha256)>(
                    StringComparer.OrdinalIgnoreCase)
                {
                    ["materials/a.vtf"] = (32, Hash('a'))
                }));
    }

    [Fact]
    public void CanonicalBridgeRequiresItsExplicitHash()
    {
        string path = Path.Combine(_root, "bridge.json");
        Bc3RawBridgeFrozen frozen = Bc3RawBridgeStore.WriteCanonical(path, FixtureBridge());
        File.AppendAllText(path, " ");

        Assert.Throws<InvalidDataException>(() =>
            Bc3RawBridgeStore.LoadAndVerify(frozen.Path, frozen.Sha256));
    }

    [Fact]
    public void CanonicalBridgeRoundTripsByteIdentically()
    {
        string first = Path.Combine(_root, "first.json");
        string second = Path.Combine(_root, "second.json");
        Bc3RawBridgeFrozen frozen = Bc3RawBridgeStore.WriteCanonical(first, FixtureBridge());
        Bc3RawBridge loaded = Bc3RawBridgeStore.LoadAndVerify(first, frozen.Sha256);
        Bc3RawBridgeFrozen repeated = Bc3RawBridgeStore.WriteCanonical(second, loaded);

        Assert.Equal(frozen.Sha256, repeated.Sha256);
        Assert.Equal(File.ReadAllBytes(first), File.ReadAllBytes(second));
    }

    [Fact]
    public void DuplicateCaseFoldedPathIsRejected()
    {
        Bc3RawBridge original = FixtureBridge();
        Bc3RawBridge duplicate = original with
        {
            Rows = new[]
            {
                original.Rows[0],
                original.Rows[0] with { RelativePath = "MATERIALS/A.VTF" }
            },
            PhysicalOpaqueBc3Count = 2
        };

        Assert.Throws<InvalidDataException>(() =>
            Bc3RawBridgeStore.WriteCanonical(
                Path.Combine(_root, "duplicate.json"), duplicate));
    }

    public void Dispose()
    {
        if (Directory.Exists(_root))
        {
            Directory.Delete(_root, recursive: true);
        }
    }

    private static Bc3RawBridge FixtureBridge()
    {
        var row = new Bc3RawBridgeRow(
            RelativePath: "materials/a.vtf",
            RawBytes: 32,
            RawSha256: Hash('a'),
            PriorSafeSourceBytes: 24,
            PriorSafeSourceSha256: Hash('b'),
            ApprovedSafeBytes: 16,
            ApprovedSafeSha256: Hash('c'),
            ConsumerReceiptSha256: Hash('d'),
            RawFormat: 15,
            EligibilityMode: Bc3EligibilityMode.PhysicalOpaque,
            ApprovedOutputPath: "materials/a.vtf",
            PriorSafeColorBlocksEquivalent: false,
            RawMips: new[]
            {
                new Bc3RawMipReceipt(1, 2, 2, 1, 1, 16, Hash('e'),
                    new Bc3AlphaReceipt(16, 255, 255, 1, true))
            });
        return new Bc3RawBridge(
            Schema: Bc3RawBridgeStore.Schema,
            PriorManifestSha256: Bc3ApprovedReceipt.RequiredPriorManifestSha256,
            RawTreeSha256: Hash('f'),
            RawTreeBytes: 32,
            RawTreeFileCount: 1,
            ApprovedSafeTreeSha256: Hash('1'),
            ApprovedSafeTreeBytes: 16,
            ApprovedSafeTreeFileCount: 1,
            PhysicalOpaqueBc3Count: 1,
            RawDxt1Count: 0,
            SemanticExceptionBc3Count: 0,
            EquivalentPriorSafeColorChainCount: 0,
            Rows: new[] { row });
    }

    private static string Hash(char value) => new(value, 64);
}
