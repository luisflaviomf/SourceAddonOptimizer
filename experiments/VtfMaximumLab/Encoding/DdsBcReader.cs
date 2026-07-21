namespace VtfMaximumLab.Encoding;

internal enum DdsBcFormat
{
    Bc1,
    Bc3
}

internal sealed record DdsBcMip(int Level, int Width, int Height, byte[] Bytes);

internal sealed record DdsBcDocument(
    int Width,
    int Height,
    DdsBcFormat Format,
    IReadOnlyList<DdsBcMip> Mips);

internal static class DdsBcReader
{
    internal static DdsBcDocument Read(string path)
    {
        using FileStream stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        using var reader = new BinaryReader(stream, System.Text.Encoding.ASCII, leaveOpen: true);
        if (stream.Length < 128 || System.Text.Encoding.ASCII.GetString(reader.ReadBytes(4)) != "DDS ")
            throw new InvalidDataException("DDS header is missing or truncated.");
        if (reader.ReadInt32() != 124)
            throw new InvalidDataException("DDS header size is not 124 bytes.");

        _ = reader.ReadInt32();
        int height = reader.ReadInt32();
        int width = reader.ReadInt32();
        _ = reader.ReadInt32();
        _ = reader.ReadInt32();
        int mipCount = Math.Max(1, reader.ReadInt32());
        stream.Position = 76;
        if (reader.ReadInt32() != 32)
            throw new InvalidDataException("DDS pixel format header size is invalid.");
        _ = reader.ReadInt32();
        string fourCc = System.Text.Encoding.ASCII.GetString(reader.ReadBytes(4));
        DdsBcFormat format = fourCc switch
        {
            "DXT1" => DdsBcFormat.Bc1,
            "DXT5" => DdsBcFormat.Bc3,
            "DX10" => throw new InvalidDataException("DX10 DDS headers are not accepted for Source BC payloads."),
            _ => throw new InvalidDataException($"Unsupported DDS FourCC '{fourCc}'.")
        };
        if (width <= 0 || height <= 0 || mipCount <= 0)
            throw new InvalidDataException("DDS dimensions or mip count are invalid.");

        stream.Position = 128;
        int blockBytes = format == DdsBcFormat.Bc1 ? 8 : 16;
        var mips = new List<DdsBcMip>(mipCount);
        for (int level = 0; level < mipCount; level++)
        {
            int mipWidth = Math.Max(1, width >> level);
            int mipHeight = Math.Max(1, height >> level);
            int byteCount = checked(Math.Max(1, (mipWidth + 3) / 4) * Math.Max(1, (mipHeight + 3) / 4) * blockBytes);
            byte[] bytes = reader.ReadBytes(byteCount);
            if (bytes.Length != byteCount)
                throw new InvalidDataException($"DDS mip {level} is truncated.");
            mips.Add(new DdsBcMip(level, mipWidth, mipHeight, bytes));
        }

        return new DdsBcDocument(width, height, format, mips);
    }
}
