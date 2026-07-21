using GmodAddonCompressor.Systems.Vtf;
using VtfMaximumLab.Encoding;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class VtfBcPayloadTransplanterTests
{
    [Fact]
    public void TransplantMapsDdsLargestFirstToVtfSmallestFirst()
    {
        string ddsPath = DdsFixtureBuilder.Write(8, 8, "DXT1", 4);
        DdsBcDocument dds = DdsBcReader.Read(ddsPath);
        string canonical = VtfFixtureBuilder.WriteLegacy72(8, 8, 13, 4);

        VtfBcPayloadTransplanter.Replace(canonical, dds);

        Assert.True(VtfDocumentReader.TryRead(canonical, out var document, out string error), error);
        byte[] bytes = File.ReadAllBytes(canonical);
        Assert.Equal(dds.Mips[0].Bytes, bytes
            .Skip((int)document.MipLevels[0].Offset)
            .Take(document.MipLevels[0].ByteCount));
    }

    [Fact]
    public void TransplantRejectsMismatchedMipCount()
    {
        string canonical = VtfFixtureBuilder.WriteLegacy72(8, 8, 13, 4);
        DdsBcDocument dds = DdsBcReader.Read(DdsFixtureBuilder.Write(8, 8, "DXT1", 1));

        Assert.Throws<InvalidDataException>(() => VtfBcPayloadTransplanter.Replace(canonical, dds));
    }

    [Fact]
    public void BuilderDownscalesLegacyVtfWithoutChangingItsVersion()
    {
        string original = VtfFixtureBuilder.WriteLegacy72(8, 8, 15, 4);
        DdsBcDocument dds = DdsBcReader.Read(DdsFixtureBuilder.Write(4, 4, "DXT1", 3));
        string output = Path.Combine(Path.GetTempPath(), $"vtf_built_{Guid.NewGuid():N}.vtf");

        VtfBcFileBuilder.Build(original, dds, output, oneBitAlpha: false, preserveAlpha: false);

        Assert.True(VtfDocumentReader.TryRead(output, out var document, out string error), error);
        Assert.Equal((7, 2), (document.MajorVersion, document.MinorVersion));
        Assert.Equal((4, 4, 13, 3), (document.Width, document.Height, document.HighResFormat, document.MipCount));
    }

    [Fact]
    public void BuilderDownscalesResourceVtf75WithoutChangingVersionOrHighResResourceOffset()
    {
        string original = VtfFixtureBuilder.WriteResource75(8, 8, 15, 4);
        DdsBcDocument dds = DdsBcReader.Read(DdsFixtureBuilder.Write(4, 4, "DXT1", 3));
        string output = Path.Combine(Path.GetTempPath(), $"vtf_built_75_{Guid.NewGuid():N}.vtf");

        VtfBcFileBuilder.Build(original, dds, output, oneBitAlpha: false, preserveAlpha: false);

        Assert.True(VtfDocumentReader.TryRead(output, out var document, out string error), error);
        Assert.Equal((7, 5), (document.MajorVersion, document.MinorVersion));
        Assert.Equal((4, 4, 13, 3), (document.Width, document.Height, document.HighResFormat, document.MipCount));
        Assert.Equal(104, document.HighResDataOffset);
        Assert.Equal(new byte[16], File.ReadAllBytes(output).Skip(88).Take(16));
    }

    [Fact]
    public void ExtractedCurrentBcPayloadCanBeRebuiltIntoOriginalVtf75Container()
    {
        string current = VtfFixtureBuilder.WriteLegacy72(8, 8, 13, 4);
        string original = VtfFixtureBuilder.WriteResource75(16, 16, 15, 5);
        DdsBcDocument payload = VtfBcPayloadExtractor.Extract(current);
        string output = Path.Combine(Path.GetTempPath(), $"vtf_current_exact_{Guid.NewGuid():N}.vtf");

        VtfBcFileBuilder.Build(original, payload, output, oneBitAlpha: false, preserveAlpha: false);

        Assert.True(VtfDocumentReader.TryRead(output, out var rebuilt, out string error), error);
        Assert.Equal((7, 5, 8, 8, 13, 4),
            (rebuilt.MajorVersion, rebuilt.MinorVersion, rebuilt.Width, rebuilt.Height, rebuilt.HighResFormat, rebuilt.MipCount));
        DdsBcDocument roundTrip = VtfBcPayloadExtractor.Extract(output);
        Assert.Equal(payload.Mips.SelectMany(mip => mip.Bytes), roundTrip.Mips.SelectMany(mip => mip.Bytes));
    }

    [Fact]
    public void ExtractorCanPromoteAnExistingMipToTopLevelWithoutReencoding()
    {
        string original = VtfFixtureBuilder.WriteLegacy72(8, 8, 15, 4);

        DdsBcDocument promoted = VtfBcPayloadExtractor.Extract(original, firstMipLevel: 1);

        Assert.Equal((4, 4, 3), (promoted.Width, promoted.Height, promoted.Mips.Count));
        Assert.Equal(new byte[] { 3, 3, 3, 3 }, promoted.Mips[0].Bytes.Take(4));
        Assert.Equal(new[] { 0, 1, 2 }, promoted.Mips.Select(mip => mip.Level));
    }
}

internal static class DdsFixtureBuilder
{
    internal static string Write(int width, int height, string fourCc, int mipCount)
    {
        string path = Path.Combine(Path.GetTempPath(), $"dds_fixture_{Guid.NewGuid():N}.dds");
        using var stream = File.Create(path);
        using var writer = new BinaryWriter(stream);
        writer.Write(new byte[] { (byte)'D', (byte)'D', (byte)'S', (byte)' ' });
        writer.Write(124);
        writer.Write(0x0002100F);
        writer.Write(height);
        writer.Write(width);
        writer.Write(0);
        writer.Write(0);
        writer.Write(mipCount);
        writer.Write(new byte[44]);
        writer.Write(32);
        writer.Write(0x4);
        writer.Write(System.Text.Encoding.ASCII.GetBytes(fourCc));
        writer.Write(new byte[20]);
        writer.Write(0x00401008);
        writer.Write(0);
        writer.Write(0);
        writer.Write(0);
        writer.Write(0);

        int blockBytes = fourCc == "DXT1" ? 8 : 16;
        for (int level = 0; level < mipCount; level++)
        {
            int mipWidth = Math.Max(1, width >> level);
            int mipHeight = Math.Max(1, height >> level);
            int byteCount = Math.Max(1, (mipWidth + 3) / 4) * Math.Max(1, (mipHeight + 3) / 4) * blockBytes;
            writer.Write(Enumerable.Repeat((byte)(level + 1), byteCount).ToArray());
        }
        return path;
    }
}
