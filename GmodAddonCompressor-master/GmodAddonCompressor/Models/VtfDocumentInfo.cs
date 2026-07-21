using System.Collections.Generic;

namespace GmodAddonCompressor.Models
{
    internal sealed record VtfMipLevelInfo(
        int Level,
        int Width,
        int Height,
        int Depth,
        long Offset,
        int ByteCount);

    internal sealed record VtfResourceInfo(int Tag, byte Flags, long Data);

    internal sealed record VtfDocumentInfo(
        int MajorVersion,
        int MinorVersion,
        int HeaderSize,
        int Width,
        int Height,
        int Flags,
        int Frames,
        int Faces,
        int Depth,
        int HighResFormat,
        int MipCount,
        int LowResFormat,
        int LowResWidth,
        int LowResHeight,
        long HighResDataOffset,
        IReadOnlyList<VtfMipLevelInfo> MipLevels,
        IReadOnlyList<VtfResourceInfo> Resources,
        long FileLength);
}
