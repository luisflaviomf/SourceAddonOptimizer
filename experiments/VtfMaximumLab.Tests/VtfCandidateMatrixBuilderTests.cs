using GmodAddonCompressor.Models;
using VtfMaximumLab.Candidates;
using VtfMaximumLab.Inventory;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class VtfCandidateMatrixBuilderTests
{
    [Fact]
    public void GradualAlphaNeverOffersDxt1AndOffersThreeScales()
    {
        VtfInventoryEntry entry = Entry(VtfAlphaClass.Gradual, normal: false, usesNormalAlpha: false);

        IReadOnlyList<VtfCandidateSpec> specs = VtfCandidateMatrixBuilder.Build(entry, new[] { "vtfcmd", "texconv" });

        Assert.All(specs, spec => Assert.Equal(VtfTargetFormat.Dxt5, spec.Format));
        Assert.Equal(new[] { 1, 2, 4 }, specs.Select(spec => spec.ScaleDivisor).Distinct().OrderBy(value => value));
        Assert.Equal(6, specs.Count);
    }

    [Fact]
    public void OpaqueTextureAndOpaqueRgbNormalOfferOnlyDxt1()
    {
        Assert.All(
            VtfCandidateMatrixBuilder.Build(Entry(VtfAlphaClass.Opaque, false, false), new[] { "vtfcmd" }),
            spec => Assert.Equal(VtfTargetFormat.Dxt1, spec.Format));
        Assert.All(
            VtfCandidateMatrixBuilder.Build(Entry(VtfAlphaClass.Opaque, true, false), new[] { "vtfcmd" }),
            spec => Assert.Equal(VtfTargetFormat.Dxt1, spec.Format));
    }

    [Fact]
    public void NormalWithSemanticAlphaMaskOffersOnlyDxt5()
    {
        Assert.All(
            VtfCandidateMatrixBuilder.Build(Entry(VtfAlphaClass.Gradual, true, true), new[] { "vtfcmd" }),
            spec => Assert.Equal(VtfTargetFormat.Dxt5, spec.Format));
    }

    [Fact]
    public void CandidateDimensionsNeverGoBelowEightByEight()
    {
        VtfInventoryEntry entry = Entry(VtfAlphaClass.Opaque, normal: false, usesNormalAlpha: false) with
        {
            Width = 16,
            Height = 8
        };

        IReadOnlyList<VtfCandidateSpec> specs = VtfCandidateMatrixBuilder.Build(entry, new[] { "vtfcmd" });

        Assert.All(specs, spec =>
        {
            Assert.True(spec.Width >= 8);
            Assert.True(spec.Height >= 8);
        });
    }

    [Theory]
    [InlineData("materials/test/paint.vtf", 0x80)]
    [InlineData("materials/test/paint_nm.vtf", 0)]
    [InlineData("materials/test/paint_normal.vtf", 0)]
    public void NormalFlagOrConservativeFileSuffixUsesNormalProcessingWithDxt1WhenOpaque(string relativePath, int flags)
    {
        VtfInventoryEntry entry = Entry(VtfAlphaClass.Opaque, normal: false, usesNormalAlpha: false) with
        {
            RelativePath = relativePath,
            Flags = flags
        };

        Assert.All(
            VtfCandidateMatrixBuilder.Build(entry, new[] { "vtfcmd" }),
            spec =>
            {
                Assert.True(spec.IsNormalMap);
                Assert.Equal(VtfTargetFormat.Dxt1, spec.Format);
            });
    }

    [Fact]
    public void GradualPixelAlphaCanUseDxt1OnlyWhenVmtEvidenceProvesAlphaIsUnused()
    {
        VtfInventoryEntry unknownUse = Entry(VtfAlphaClass.Gradual, normal: false, usesNormalAlpha: false) with
        {
            SemanticProfile = Entry(VtfAlphaClass.Opaque, normal: false, usesNormalAlpha: false).SemanticProfile
        };
        VtfInventoryEntry provenUnused = unknownUse with { RelatedVmtPaths = new[] { "materials/test.vmt" } };

        Assert.All(
            VtfCandidateMatrixBuilder.Build(unknownUse, new[] { "vtfcmd" }),
            spec => Assert.Equal(VtfTargetFormat.Dxt5, spec.Format));
        Assert.All(
            VtfCandidateMatrixBuilder.Build(provenUnused, new[] { "vtfcmd" }),
            spec => Assert.Equal(VtfTargetFormat.Dxt1, spec.Format));
    }

    [Fact]
    public void DetectsDxt5NmOnlyWhenAlphaCarriesVariationAndRgbNormalChannelsAreFlat()
    {
        VtfInventoryEntry entry = Entry(VtfAlphaClass.Gradual, normal: true, usesNormalAlpha: false);
        byte[] rgba = Enumerable.Range(0, 64)
            .SelectMany(index => new byte[] { 129, 128, 255, (byte)(index * 4) })
            .ToArray();

        Assert.True(VtfInventorySemantics.IsDxt5NormalMap(entry, rgba));

        VtfInventoryEntry alphaMask = Entry(VtfAlphaClass.Gradual, normal: true, usesNormalAlpha: true);
        Assert.False(VtfInventorySemantics.IsDxt5NormalMap(alphaMask, rgba));
    }

    private static VtfInventoryEntry Entry(VtfAlphaClass alpha, bool normal, bool usesNormalAlpha)
    {
        var profile = new VtfSemanticProfile(
            "test/texture", false, normal, usesNormalAlpha, alpha == VtfAlphaClass.Gradual,
            alpha == VtfAlphaClass.Cutout, false, false, false, false, false, false, false, false,
            0.5, Array.Empty<string>());
        return new VtfInventoryEntry(
            "test.vtf", "materials/test.vtf", 100, 1024, 512, 15, 7, 2, 11,
            1, 1, 1, 0, alpha, profile, Array.Empty<string>(), new HashSet<string>());
    }
}
