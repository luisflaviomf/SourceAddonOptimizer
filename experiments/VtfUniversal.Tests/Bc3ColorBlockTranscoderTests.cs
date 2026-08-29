using System.Buffers.Binary;
using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class Bc3ColorBlockTranscoderTests
{
    public static IEnumerable<object[]> BothEndpointOrders()
    {
        yield return new object[] { Block(0xf800, 0x07e0, 0xe4e4e4e4) };
        yield return new object[] { Block(0x001f, 0xffff, 0x1b1b1b1b) };
        yield return new object[] { Block(0x7bef, 0x7bef, 0xffffffff) };
        yield return new object[] { Block(0x0000, 0x0000, 0x39393939) };
    }

    [Theory]
    [MemberData(nameof(BothEndpointOrders))]
    public void TranscodePreservesDecodedRgb(byte[] bc3)
    {
        byte[] bc1 = Bc3ColorBlockTranscoder.TranscodeBlock(bc3);

        Assert.Equal(8, bc1.Length);
        Assert.True(BinaryPrimitives.ReadUInt16LittleEndian(bc1) >
                    BinaryPrimitives.ReadUInt16LittleEndian(bc1.AsSpan(2)));
        Assert.Equal(DecodeBc3Rgb(bc3), DecodeOpaqueBc1Rgb(bc1));
    }

    [Fact]
    public void FourColorOrderCopiesColorBlockExactly()
    {
        byte[] source = Block(0xf800, 0x07e0, 0x01234567);

        Assert.Equal(source.AsSpan(8, 8).ToArray(),
            Bc3ColorBlockTranscoder.TranscodeBlock(source));
    }

    [Fact]
    public void RejectsNonBc3BlockLength()
    {
        Assert.Throws<ArgumentException>(() =>
            Bc3ColorBlockTranscoder.TranscodeBlock(new byte[15]));
    }

    private static byte[] Block(ushort color0, ushort color1, uint indices)
    {
        var result = new byte[16];
        result[0] = 255;
        result[1] = 255;
        BinaryPrimitives.WriteUInt16LittleEndian(result.AsSpan(8), color0);
        BinaryPrimitives.WriteUInt16LittleEndian(result.AsSpan(10), color1);
        BinaryPrimitives.WriteUInt32LittleEndian(result.AsSpan(12), indices);
        return result;
    }

    private static byte[] DecodeBc3Rgb(ReadOnlySpan<byte> block) =>
        DecodeColor(block.Slice(8, 8), fourColor: true);

    private static byte[] DecodeOpaqueBc1Rgb(ReadOnlySpan<byte> block) =>
        DecodeColor(block, fourColor: true);

    private static byte[] DecodeColor(ReadOnlySpan<byte> block, bool fourColor)
    {
        ushort color0 = BinaryPrimitives.ReadUInt16LittleEndian(block);
        ushort color1 = BinaryPrimitives.ReadUInt16LittleEndian(block.Slice(2));
        byte[][] palette = { Expand(color0), Expand(color1), new byte[3], new byte[3] };
        if (!fourColor)
        {
            throw new NotSupportedException();
        }

        for (int channel = 0; channel < 3; channel++)
        {
            palette[2][channel] = (byte)((2 * palette[0][channel] + palette[1][channel]) / 3);
            palette[3][channel] = (byte)((palette[0][channel] + 2 * palette[1][channel]) / 3);
        }

        uint indices = BinaryPrimitives.ReadUInt32LittleEndian(block.Slice(4));
        var rgb = new byte[16 * 3];
        for (int pixel = 0; pixel < 16; pixel++)
        {
            int index = (int)((indices >> (pixel * 2)) & 3);
            palette[index].CopyTo(rgb, pixel * 3);
        }

        return rgb;
    }

    private static byte[] Expand(ushort color)
    {
        int r = (color >> 11) & 31;
        int g = (color >> 5) & 63;
        int b = color & 31;
        return new[]
        {
            (byte)((r << 3) | (r >> 2)),
            (byte)((g << 2) | (g >> 4)),
            (byte)((b << 3) | (b >> 2))
        };
    }
}
