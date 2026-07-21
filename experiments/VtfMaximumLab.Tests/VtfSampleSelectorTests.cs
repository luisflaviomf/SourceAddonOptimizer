using GmodAddonCompressor.Models;
using VtfMaximumLab.Inventory;
using VtfMaximumLab.Sampling;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class VtfSampleSelectorTests
{
    [Fact]
    public void SelectionIsStableAcrossInputOrderAndCoversRareTags()
    {
        var entries = Enumerable.Range(0, 80)
            .Select(index => Entry(
                $"materials/test/texture_{index:D2}.vtf",
                index switch
                {
                    3 => new[] { "semantic:normal" },
                    9 => new[] { "alpha:cutout" },
                    17 => new[] { "alpha:gradual", "semantic:glass" },
                    31 => new[] { "semantic:emissive" },
                    _ => new[] { index % 2 == 0 ? "format:DXT1" : "format:DXT5" }
                }))
            .ToArray();

        VtfSampleManifest first = VtfSampleSelector.Select(entries, 50, VtfSampleSelector.DefaultSeed, "fixture");
        VtfSampleManifest second = VtfSampleSelector.Select(entries.Reverse(), 50, VtfSampleSelector.DefaultSeed, "fixture");

        Assert.Equal(first.Items.Select(item => item.RelativePath), second.Items.Select(item => item.RelativePath));
        Assert.Contains(first.Items, item => item.Tags.Contains("semantic:normal"));
        Assert.Contains(first.Items, item => item.Tags.Contains("alpha:cutout"));
        Assert.Contains(first.Items, item => item.Tags.Contains("semantic:glass"));
        Assert.Equal(50, first.Items.Count);
        Assert.Equal(first.SelectionSha256, second.SelectionSha256);
    }

    private static VtfInventoryEntry Entry(string relativePath, IReadOnlyCollection<string> tags)
    {
        var profile = new VtfSemanticProfile(
            relativePath, false, false, false, false, false, false, false,
            false, false, false, false, false, false, 0.5, Array.Empty<string>());
        return new VtfInventoryEntry(
            relativePath,
            relativePath,
            1024,
            256,
            256,
            13,
            7,
            2,
            9,
            1,
            1,
            1,
            0,
            VtfAlphaClass.Opaque,
            profile,
            Array.Empty<string>(),
            tags.ToHashSet(StringComparer.Ordinal));
    }
}
