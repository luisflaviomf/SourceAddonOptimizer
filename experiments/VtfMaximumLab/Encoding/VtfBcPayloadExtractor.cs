using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;

namespace VtfMaximumLab.Encoding;

internal static class VtfBcPayloadExtractor
{
    internal static DdsBcDocument Extract(string vtfPath, int firstMipLevel = 0)
    {
        if (!VtfDocumentReader.TryRead(vtfPath, out VtfDocumentInfo document, out string error))
            throw new InvalidDataException($"VTF payload source is invalid: {error}");
        if (document.Frames != 1 || document.Faces != 1 || document.Depth != 1)
            throw new InvalidDataException("BC payload extraction only supports one frame, one face, and depth one.");
        if (firstMipLevel < 0 || firstMipLevel >= document.MipCount)
            throw new ArgumentOutOfRangeException(nameof(firstMipLevel));

        DdsBcFormat format = document.HighResFormat switch
        {
            13 or 20 => DdsBcFormat.Bc1,
            15 => DdsBcFormat.Bc3,
            _ => throw new InvalidDataException($"VTF format {document.HighResFormat} is not a supported BC payload.")
        };

        using FileStream stream = File.Open(vtfPath, FileMode.Open, FileAccess.Read, FileShare.Read);
        var mips = new List<DdsBcMip>(document.MipCount - firstMipLevel);
        foreach (VtfMipLevelInfo mip in document.MipLevels
                     .Where(mip => mip.Level >= firstMipLevel)
                     .OrderBy(mip => mip.Level))
        {
            stream.Position = mip.Offset;
            var bytes = new byte[mip.ByteCount];
            int read = 0;
            while (read < bytes.Length)
            {
                int count = stream.Read(bytes, read, bytes.Length - read);
                if (count <= 0)
                    throw new InvalidDataException($"VTF mip {mip.Level} is truncated.");
                read += count;
            }
            mips.Add(new DdsBcMip(mip.Level - firstMipLevel, mip.Width, mip.Height, bytes));
        }

        VtfMipLevelInfo promotedTop = document.MipLevels[firstMipLevel];
        return new DdsBcDocument(promotedTop.Width, promotedTop.Height, format, mips);
    }
}
