using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;
using VtfMaximumLab.Encoding;
using VtfMaximumLab.Inventory;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class VtfLosslessBc3RepackTests
{
    [Fact]
    public void AcceptsReferencedBc3WhoseNonOpaqueAlphaIsSemanticallyUnused()
    {
        VtfInventoryEntry entry = Entry(VtfAlphaClass.Cutout, usesAlpha: false);

        Assert.True(VtfLosslessBc3Repack.CanRepack(entry));
    }

    [Fact]
    public void RejectsAlphaConsumersNormalsUnknownAlphaAndUnsupportedStructure()
    {
        Assert.False(VtfLosslessBc3Repack.CanRepack(Entry(VtfAlphaClass.Cutout, usesAlpha: true)));
        Assert.False(VtfLosslessBc3Repack.CanRepack(Entry(VtfAlphaClass.Cutout, usesAlpha: false, normal: true)));
        Assert.False(VtfLosslessBc3Repack.CanRepack(Entry(VtfAlphaClass.Unknown, usesAlpha: false)));
        Assert.False(VtfLosslessBc3Repack.CanRepack(Entry(VtfAlphaClass.Opaque, usesAlpha: false)));
        Assert.False(VtfLosslessBc3Repack.CanRepack(Entry(VtfAlphaClass.Cutout, usesAlpha: false) with { Frames = 2 }));
    }

    [Fact]
    public void RejectsUnreferencedTextureWhenNonOpaqueAlphaCannotBeProvenUnused()
    {
        VtfInventoryEntry entry = Entry(VtfAlphaClass.Cutout, usesAlpha: false) with
        {
            RelatedVmtPaths = Array.Empty<string>()
        };

        Assert.False(VtfLosslessBc3Repack.CanRepack(entry));
    }

    [Fact]
    public void BuildsHalfSizedPayloadWithIdenticalDecodedRgbAtEveryMip()
    {
        string source = VtfFixtureBuilder.WriteLegacy72(8, 8, format: 15, mipCount: 4);
        string output = Path.Combine(Path.GetTempPath(), $"vtf_lossless_repack_{Guid.NewGuid():N}.vtf");
        try
        {
            VtfLosslessBc3Repack.Build(source, output);

            Assert.True(VtfDocumentReader.TryRead(source, out VtfDocumentInfo original, out string sourceError), sourceError);
            Assert.True(VtfDocumentReader.TryRead(output, out VtfDocumentInfo rebuilt, out string outputError), outputError);
            Assert.True(VtfLosslessBc3Repack.TryEstimateOutputBytes(source, out long estimatedBytes));
            Assert.Equal(new FileInfo(output).Length, estimatedBytes);
            Assert.Equal(13, rebuilt.HighResFormat);
            Assert.Equal(original.MipCount, rebuilt.MipCount);
            Assert.Empty(VtfMaximumLab.Validation.VtfMaximumStructuralValidator.ValidatePair(original, rebuilt));
            Assert.True(new FileInfo(output).Length < new FileInfo(source).Length);

            for (int mip = 0; mip < original.MipCount; mip++)
            {
                Assert.True(VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                    source, original, mip, 0, 0, 0, out byte[] sourceRgba));
                Assert.True(VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                    output, rebuilt, mip, 0, 0, 0, out byte[] outputRgba));
                AssertRgbEqual(sourceRgba, outputRgba);
            }
        }
        finally
        {
            File.Delete(source);
            File.Delete(output);
        }
    }

    private static void AssertRgbEqual(byte[] expected, byte[] actual)
    {
        Assert.Equal(expected.Length, actual.Length);
        for (int offset = 0; offset < expected.Length; offset += 4)
        {
            Assert.Equal(expected[offset], actual[offset]);
            Assert.Equal(expected[offset + 1], actual[offset + 1]);
            Assert.Equal(expected[offset + 2], actual[offset + 2]);
        }
    }

    private static VtfInventoryEntry Entry(VtfAlphaClass alpha, bool usesAlpha, bool normal = false)
    {
        var profile = new VtfSemanticProfile(
            "test/texture",
            UsesBaseTextureAlpha: usesAlpha,
            IsNormalMap: normal,
            UsesNormalAlpha: normal && usesAlpha,
            IsTranslucent: false,
            IsCutout: usesAlpha,
            HasAlphaToCoverage: false,
            IsVehicleGlass: false,
            UsesBaseTextureAsPhongMask: false,
            UsesBaseTextureAsEnvMapMask: false,
            HasPhong: false,
            IsEmissive: false,
            IsDecal: false,
            IsEffect: false,
            AlphaTestReference: 0.5,
            Evidence: Array.Empty<string>());
        return new VtfInventoryEntry(
            "test.vtf", "materials/test/texture.vtf", 1000,
            256, 256, 15, 7, 2, 9, 1, 1, 1, 0,
            alpha, profile, new[] { "materials/test/texture.vmt" }, new HashSet<string>());
    }
}
