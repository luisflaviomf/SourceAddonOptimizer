using System.Text;

namespace VtfMaximumLab.Tests;

internal static class VtfFixtureBuilder
{
    internal static string WriteLegacy72(int width, int height, int format, int mipCount)
    {
        const int headerSize = 80;
        string path = Path.Combine(Path.GetTempPath(), $"vtf_fixture_{Guid.NewGuid():N}.vtf");
        using var stream = File.Create(path);
        using var writer = new BinaryWriter(stream, System.Text.Encoding.ASCII, leaveOpen: false);
        WriteCommonHeader(writer, 7, 2, headerSize, width, height, format, mipCount);
        PadTo(writer, headerSize);
        WriteMipPayload(writer, width, height, format, mipCount);
        return path;
    }

    internal static string WriteResource75(int width, int height, int format, int mipCount)
    {
        const int headerSize = 88;
        const int highResDataOffset = 104;
        string path = Path.Combine(Path.GetTempPath(), $"vtf_fixture_{Guid.NewGuid():N}.vtf");
        using var stream = File.Create(path);
        using var writer = new BinaryWriter(stream, System.Text.Encoding.ASCII, leaveOpen: false);
        WriteCommonHeader(writer, 7, 5, headerSize, width, height, format, mipCount);
        writer.Write(new byte[3]);
        writer.Write(1);
        writer.Write(new byte[8]);
        writer.Write(new byte[] { 0x30, 0x00, 0x00 });
        writer.Write((byte)0);
        writer.Write(highResDataOffset);
        PadTo(writer, headerSize);
        PadTo(writer, highResDataOffset);
        WriteMipPayload(writer, width, height, format, mipCount);
        return path;
    }

    internal static string WriteTruncated()
    {
        string path = WriteLegacy72(8, 8, 13, 4);
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Write, FileShare.None);
        stream.SetLength(stream.Length - 1);
        return path;
    }

    private static void WriteCommonHeader(
        BinaryWriter writer,
        int major,
        int minor,
        int headerSize,
        int width,
        int height,
        int format,
        int mipCount)
    {
        writer.Write(new byte[] { (byte)'V', (byte)'T', (byte)'F', 0 });
        writer.Write(major);
        writer.Write(minor);
        writer.Write(headerSize);
        writer.Write((ushort)width);
        writer.Write((ushort)height);
        writer.Write(0);
        writer.Write((ushort)1);
        writer.Write((ushort)0);
        writer.Write(new byte[4]);
        writer.Write(0f);
        writer.Write(0f);
        writer.Write(0f);
        writer.Write(new byte[4]);
        writer.Write(1f);
        writer.Write(format);
        writer.Write((byte)mipCount);
        writer.Write(-1);
        writer.Write((byte)0);
        writer.Write((byte)0);
        writer.Write((ushort)1);
    }

    private static void WriteMipPayload(BinaryWriter writer, int width, int height, int format, int mipCount)
    {
        var levels = new List<(int Width, int Height)>();
        for (int level = 0; level < mipCount; level++)
        {
            levels.Add((Math.Max(1, width >> level), Math.Max(1, height >> level)));
        }

        byte marker = 1;
        foreach ((int mipWidth, int mipHeight) in levels.AsEnumerable().Reverse())
        {
            int byteCount = GetByteCount(format, mipWidth, mipHeight);
            writer.Write(Enumerable.Repeat(marker++, byteCount).ToArray());
        }
    }

    private static int GetByteCount(int format, int width, int height)
    {
        int blockBytes = format is 13 or 20 ? 8 : 16;
        return Math.Max(1, (width + 3) / 4) * Math.Max(1, (height + 3) / 4) * blockBytes;
    }

    private static void PadTo(BinaryWriter writer, int offset)
    {
        if (writer.BaseStream.Position > offset)
            throw new InvalidOperationException("Fixture header exceeded declared size.");

        writer.Write(new byte[offset - writer.BaseStream.Position]);
    }
}
