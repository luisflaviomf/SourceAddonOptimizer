using GmodAddonCompressor.Systems.Vtf;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class VtfDocumentReaderTests
{
    [Fact]
    public void ReadsLegacy72MipLayout()
    {
        string path = VtfFixtureBuilder.WriteLegacy72(8, 8, 13, 4);

        Assert.True(VtfDocumentReader.TryRead(path, out var document, out string error), error);
        Assert.Equal(4, document.MipLevels.Count);
        Assert.Equal((8, 8), (document.MipLevels[0].Width, document.MipLevels[0].Height));
        Assert.Equal(document.HighResDataOffset + 24, document.MipLevels[0].Offset);
        Assert.Equal(32, document.MipLevels[0].ByteCount);
    }

    [Fact]
    public void Reads75ResourceOffsetsWithoutAssumingAdjacentPayload()
    {
        string path = VtfFixtureBuilder.WriteResource75(8, 8, 15, 4);

        Assert.True(VtfDocumentReader.TryRead(path, out var document, out string error), error);
        Assert.Equal(104, document.HighResDataOffset);
        Assert.Single(document.Resources);
        Assert.Equal(0x30, document.Resources[0].Tag);
    }

    [Fact]
    public void RejectsTruncatedTopMip()
    {
        string path = VtfFixtureBuilder.WriteTruncated();

        Assert.False(VtfDocumentReader.TryRead(path, out _, out string error));
        Assert.Contains("truncated", error, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void DecoderUsesResourceDictionaryOffsetForTopMip()
    {
        string path = VtfFixtureBuilder.WriteResource75(8, 8, 15, 4);
        Assert.True(AddonVtfCompressionPlanner.TryReadMetadata(path, out var metadata));

        Assert.True(VtfHighResImageDecoder.TryDecodeHighResRgba(path, metadata, out byte[] rgba));
        Assert.Equal(4, rgba[3]);
    }

    [Fact]
    public void DecoderCanReadARequestedMipLevel()
    {
        string path = VtfFixtureBuilder.WriteLegacy72(8, 8, 13, 4);
        Assert.True(VtfDocumentReader.TryRead(path, out var document, out string error), error);

        Assert.True(VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
            path, document, mipLevel: 1, frame: 0, face: 0, depthSlice: 0, out byte[] rgba));

        Assert.Equal(4 * 4 * 4, rgba.Length);
    }
}
