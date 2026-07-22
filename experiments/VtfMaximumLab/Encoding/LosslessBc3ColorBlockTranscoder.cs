using System.Buffers.Binary;

namespace VtfMaximumLab.Encoding;

internal static class LosslessBc3ColorBlockTranscoder
{
    private const uint ToggleEndpointIndex = 0x55555555u;

    internal static DdsBcDocument Transcode(DdsBcDocument source)
    {
        if (source.Format != DdsBcFormat.Bc3)
            throw new InvalidDataException("Lossless color-block transcode requires BC3 input.");

        var mips = new List<DdsBcMip>(source.Mips.Count);
        foreach (DdsBcMip mip in source.Mips)
        {
            int blockCount = checked(Math.Max(1, (mip.Width + 3) / 4) * Math.Max(1, (mip.Height + 3) / 4));
            if (mip.Bytes.Length != checked(blockCount * 16))
                throw new InvalidDataException($"BC3 mip {mip.Level} has an invalid payload length.");

            var output = new byte[checked(blockCount * 8)];
            for (int block = 0; block < blockCount; block++)
            {
                ReadOnlySpan<byte> color = mip.Bytes.AsSpan(block * 16 + 8, 8);
                Span<byte> target = output.AsSpan(block * 8, 8);
                CopyOpaqueColorBlock(color, target);
            }
            mips.Add(new DdsBcMip(mip.Level, mip.Width, mip.Height, output));
        }

        return new DdsBcDocument(source.Width, source.Height, DdsBcFormat.Bc1, mips);
    }

    private static void CopyOpaqueColorBlock(ReadOnlySpan<byte> source, Span<byte> target)
    {
        ushort color0 = BinaryPrimitives.ReadUInt16LittleEndian(source);
        ushort color1 = BinaryPrimitives.ReadUInt16LittleEndian(source[2..]);
        uint indices = BinaryPrimitives.ReadUInt32LittleEndian(source[4..]);

        if (color0 > color1)
        {
            source.CopyTo(target);
            return;
        }

        if (color0 < color1)
        {
            BinaryPrimitives.WriteUInt16LittleEndian(target, color1);
            BinaryPrimitives.WriteUInt16LittleEndian(target[2..], color0);
            BinaryPrimitives.WriteUInt32LittleEndian(target[4..], indices ^ ToggleEndpointIndex);
            return;
        }

        if (color0 == 0)
        {
            BinaryPrimitives.WriteUInt16LittleEndian(target, 1);
            BinaryPrimitives.WriteUInt16LittleEndian(target[2..], 0);
            BinaryPrimitives.WriteUInt32LittleEndian(target[4..], ToggleEndpointIndex);
            return;
        }

        BinaryPrimitives.WriteUInt16LittleEndian(target, color0);
        BinaryPrimitives.WriteUInt16LittleEndian(target[2..], checked((ushort)(color0 - 1)));
        BinaryPrimitives.WriteUInt32LittleEndian(target[4..], 0);
    }
}
