using System.Buffers.Binary;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal static class Bc3ColorBlockTranscoder
{
    internal const int Bc3BlockBytes = 16;
    internal const int Bc1BlockBytes = 8;

    internal static byte[] TranscodeBlock(ReadOnlySpan<byte> bc3)
    {
        if (bc3.Length != Bc3BlockBytes)
        {
            throw new ArgumentException(
                $"A BC3 block must contain exactly {Bc3BlockBytes} bytes.",
                nameof(bc3));
        }

        ReadOnlySpan<byte> color = bc3.Slice(8, Bc1BlockBytes);
        ushort color0 = BinaryPrimitives.ReadUInt16LittleEndian(color);
        ushort color1 = BinaryPrimitives.ReadUInt16LittleEndian(color.Slice(2));
        if (color0 > color1)
        {
            return color.ToArray();
        }

        var bc1 = new byte[Bc1BlockBytes];
        if (color0 < color1)
        {
            BinaryPrimitives.WriteUInt16LittleEndian(bc1, color1);
            BinaryPrimitives.WriteUInt16LittleEndian(bc1.AsSpan(2), color0);
            uint indices = BinaryPrimitives.ReadUInt32LittleEndian(color.Slice(4));
            BinaryPrimitives.WriteUInt32LittleEndian(bc1.AsSpan(4), indices ^ 0x55555555u);
            return bc1;
        }

        // Equal endpoints make every BC3 palette entry the same RGB value. An
        // ordinary BC1 block still needs strict endpoint ordering to avoid its
        // transparent branch, so keep one original endpoint and make all texels
        // select it while reserving an unused neighbouring endpoint.
        if (color0 == 0)
        {
            BinaryPrimitives.WriteUInt16LittleEndian(bc1, 1);
            BinaryPrimitives.WriteUInt16LittleEndian(bc1.AsSpan(2), 0);
            BinaryPrimitives.WriteUInt32LittleEndian(bc1.AsSpan(4), 0x55555555u);
        }
        else
        {
            BinaryPrimitives.WriteUInt16LittleEndian(bc1, color0);
            BinaryPrimitives.WriteUInt16LittleEndian(bc1.AsSpan(2), (ushort)(color0 - 1));
            BinaryPrimitives.WriteUInt32LittleEndian(bc1.AsSpan(4), 0u);
        }

        return bc1;
    }

    internal static byte[] TranscodePayload(ReadOnlySpan<byte> bc3Payload)
    {
        if (bc3Payload.Length == 0 || bc3Payload.Length % Bc3BlockBytes != 0)
        {
            throw new ArgumentException(
                "A BC3 payload must contain a positive whole number of blocks.",
                nameof(bc3Payload));
        }

        var bc1 = new byte[bc3Payload.Length / 2];
        for (int sourceOffset = 0, outputOffset = 0;
             sourceOffset < bc3Payload.Length;
             sourceOffset += Bc3BlockBytes, outputOffset += Bc1BlockBytes)
        {
            TranscodeBlock(bc3Payload.Slice(sourceOffset, Bc3BlockBytes))
                .CopyTo(bc1, outputOffset);
        }

        return bc1;
    }
}
