using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfGeneratedCorpusSmokeTests
{
    [Fact]
    [Trait("Category", "RealCorpus")]
    public async Task EveryRawNomipGeneratesAllPhysicalLevelsWithoutSourceMutation()
    {
        string? requestedRoot = Environment.GetEnvironmentVariable("VTF_UNIVERSAL_RAW_ROOT");
        if (string.IsNullOrWhiteSpace(requestedRoot))
        {
            return;
        }

        string rawRoot = Path.GetFullPath(requestedRoot);
        UniversalVtfTreeSnapshot before = UniversalVtfTreeInventory.Capture(rawRoot);
        UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(rawRoot);
        Assert.True(inventory.IsComplete);
        UniversalVtfInventoryEntry[] entries = inventory.Entries
            .Where(entry => entry.MipCount == 1 && entry.IsNomip)
            .ToArray();
        Assert.Equal(7, entries.Length);

        using var outputRoot = new UniversalVtfToolchainTests.ToolchainTempDirectory();
        using UniversalVtfEncodingSession session =
            UniversalVtfCandidateMaterializer.CreateEncodingSession();
        int candidateIndex = 0;
        foreach (UniversalVtfInventoryEntry entry in entries)
        foreach (int level in UniversalVtfLevels.Enumerate(entry.Width, entry.Height))
        {
            string source = Path.Combine(
                rawRoot,
                entry.RelativePath.Replace('/', Path.DirectorySeparatorChar));
            string output = Path.Combine(
                outputRoot.Path,
                $"candidate-{candidateIndex++:D4}.vtf");
            await session.MaterializeGeneratedAsync(
                source,
                level,
                UniversalVtfFilterRoute.NumericLinear,
                output,
                CancellationToken.None);

            VtfContainerLayout candidate = VtfContainerLayoutReader.Read(output);
            UniversalVtfDimensions expected = UniversalVtfLevels.At(
                entry.Width,
                entry.Height,
                level);
            Assert.Equal((expected.Width, expected.Height, 1),
                (candidate.Document.Width, candidate.Document.Height,
                    candidate.Document.MipCount));
            Assert.Equal((entry.Frames, entry.Faces, entry.Depth, entry.HighResFormat),
                (candidate.Document.Frames, candidate.Document.Faces,
                    candidate.Document.Depth, candidate.Document.HighResFormat));
            Assert.True((candidate.Document.Flags & VtfFlags.NoMip) != 0);
        }

        UniversalVtfTreeSnapshot after = UniversalVtfTreeInventory.Capture(rawRoot);
        Assert.Equal(before.Files.Count, after.Files.Count);
        Assert.Equal(before.TotalBytes, after.TotalBytes);
        Assert.Equal(before.TreeSha256, after.TreeSha256);
    }
}
