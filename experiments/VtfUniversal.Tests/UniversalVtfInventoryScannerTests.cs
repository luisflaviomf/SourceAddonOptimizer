using GmodAddonCompressor.Systems.Vtf.Universal;
using VtfUniversal.Tests.Fixtures;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfInventoryScannerTests
{
    [Fact]
    public void RecordsEveryParseFailure()
    {
        using var tree = new VtfTreeFixture();
        using VtfFixtureFile first = VtfFixtureBuilder.WriteVolumeWithFrames(
            width: 8, height: 8, depth: 1, frames: 1, mips: 2);
        using VtfFixtureFile second = VtfFixtureBuilder.WriteVolumeWithFrames(
            width: 4, height: 4, depth: 1, frames: 1, mips: 1);
        tree.Copy(first.Path, "materials/first.vtf");
        tree.Copy(second.Path, "materials/second.VTF");
        tree.Write("materials/malformed.vtf", new byte[] { 0x56, 0x54, 0x46 });
        tree.Write("materials/not-a-texture.txt", new byte[] { 0x41 });

        UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(tree.Root);

        Assert.Equal(3, inventory.DiscoveredCount);
        Assert.Equal(2, inventory.Entries.Count);
        UniversalVtfInventoryFailure failure = Assert.Single(inventory.Failures);
        Assert.Equal("materials/malformed.vtf", failure.RelativePath);
        Assert.Equal("parse", failure.Stage);
        Assert.False(inventory.IsComplete);
    }

    [Fact]
    public void CompleteScanRecordsStructuralFieldsAndRanges()
    {
        using var tree = new VtfTreeFixture();
        using VtfFixtureFile fixture = VtfFixtureBuilder.WriteVolumeWithFrames(
            width: 8, height: 4, depth: 2, frames: 2, mips: 3);
        tree.Copy(fixture.Path, "materials/volume.vtf");

        UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(tree.Root);
        UniversalVtfInventoryEntry entry = Assert.Single(inventory.Entries);

        Assert.True(inventory.IsComplete);
        Assert.Equal((8, 4, 2, 2, 1, 3),
            (entry.Width, entry.Height, entry.Depth, entry.Frames, entry.Faces, entry.MipCount));
        Assert.Equal(8, entry.Subresources.Count);
        Assert.Equal(entry.Bytes, inventory.VtfBytes);
        Assert.Matches("^[0-9a-f]{64}$", entry.Sha256);
    }

    [Fact]
    public void OrdersEntriesByCaseFoldedOrdinalPath()
    {
        using var tree = new VtfTreeFixture();
        using VtfFixtureFile fixture = VtfFixtureBuilder.WriteVolumeWithFrames(
            width: 4, height: 4, depth: 1, frames: 1, mips: 1);
        tree.Copy(fixture.Path, "materials/a_u.vtf");
        tree.Copy(fixture.Path, "materials/au.vtf");

        UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(tree.Root);

        Assert.Equal(
            new[] { "materials/a_u.vtf", "materials/au.vtf" },
            inventory.Entries.Select(entry => entry.RelativePath));
    }

    [Fact]
    public void FrozenExpectationRejectsCorpusDrift()
    {
        using var tree = new VtfTreeFixture();
        using VtfFixtureFile fixture = VtfFixtureBuilder.WriteVolumeWithFrames(
            width: 4, height: 4, depth: 1, frames: 1, mips: 1);
        tree.Copy(fixture.Path, "materials/only.vtf");
        UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(tree.Root);
        UniversalVtfTreeSnapshot snapshot = UniversalVtfTreeInventory.Capture(tree.Root);

        InvalidDataException error = Assert.Throws<InvalidDataException>(() =>
            UniversalVtfCorpusExpectations.LvsV1.Verify(inventory, snapshot));

        Assert.Contains("vtf_count", error.Message, StringComparison.Ordinal);
    }

    private sealed class VtfTreeFixture : IDisposable
    {
        internal VtfTreeFixture()
        {
            Root = Path.Combine(
                Path.GetTempPath(),
                "VtfUniversal.Tests",
                "Inventory",
                Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Root);
        }

        internal string Root { get; }

        internal void Copy(string source, string relativePath)
        {
            string destination = Resolve(relativePath);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.Copy(source, destination);
        }

        internal void Write(string relativePath, byte[] bytes)
        {
            string destination = Resolve(relativePath);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.WriteAllBytes(destination, bytes);
        }

        public void Dispose()
        {
            if (Directory.Exists(Root))
            {
                Directory.Delete(Root, recursive: true);
            }
        }

        private string Resolve(string relativePath)
        {
            string rootPrefix = Path.GetFullPath(Root) + Path.DirectorySeparatorChar;
            string path = Path.GetFullPath(Path.Combine(Root, relativePath));
            if (!path.StartsWith(rootPrefix, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException("Fixture path escaped its root.");
            }

            return path;
        }
    }
}
