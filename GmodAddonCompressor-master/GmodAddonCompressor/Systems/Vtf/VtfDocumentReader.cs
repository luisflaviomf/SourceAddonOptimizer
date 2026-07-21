using GmodAddonCompressor.Models;
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace GmodAddonCompressor.Systems.Vtf
{
    internal static class VtfDocumentReader
    {
        private const int TextureFlagsEnvMap = 0x00004000;
        private const int HighResolutionImageResourceTag = 0x000030;

        internal static bool TryRead(string filePath, out VtfDocumentInfo document, out string error)
        {
            document = null!;
            error = string.Empty;

            try
            {
                using FileStream stream = File.Open(filePath, FileMode.Open, FileAccess.Read, FileShare.Read);
                using var reader = new BinaryReader(stream, Encoding.ASCII, leaveOpen: true);

                if (stream.Length < 63)
                    return Fail("VTF header is truncated.", out document, out error);

                byte[] signature = reader.ReadBytes(4);
                if (signature.Length != 4 || signature[0] != 'V' || signature[1] != 'T' ||
                    signature[2] != 'F' || signature[3] != 0)
                {
                    return Fail("File does not have a valid VTF signature.", out document, out error);
                }

                int majorVersion = reader.ReadInt32();
                int minorVersion = reader.ReadInt32();
                if (majorVersion != 7 || minorVersion < 0 || minorVersion > 5)
                    return Fail($"Unsupported VTF version {majorVersion}.{minorVersion}.", out document, out error);

                int headerSize = reader.ReadInt32();
                int width = reader.ReadUInt16();
                int height = reader.ReadUInt16();
                int flags = reader.ReadInt32();
                int frames = reader.ReadUInt16();
                int firstFrame = reader.ReadUInt16();

                stream.Position = 52;
                int highResFormat = reader.ReadInt32();
                int mipCount = reader.ReadByte();
                int lowResFormat = reader.ReadInt32();
                int lowResWidth = reader.ReadByte();
                int lowResHeight = reader.ReadByte();
                int depth = minorVersion >= 2 ? reader.ReadUInt16() : 1;

                if (headerSize < stream.Position || headerSize > stream.Length)
                    return Fail("VTF header size is invalid or truncated.", out document, out error);
                if (width <= 0 || height <= 0 || frames <= 0 || depth <= 0 || mipCount <= 0)
                    return Fail("VTF dimensions, frames, depth, or mip count are invalid.", out document, out error);

                var resources = new List<VtfResourceInfo>();
                if (minorVersion >= 3)
                {
                    if (headerSize < 80 || stream.Length < 80)
                        return Fail("VTF resource header is truncated.", out document, out error);

                    stream.Position = 68;
                    int resourceCount = reader.ReadInt32();
                    if (resourceCount < 0 || resourceCount > 4096 || 80L + resourceCount * 8L > headerSize)
                        return Fail("VTF resource dictionary is invalid or truncated.", out document, out error);

                    stream.Position = 80;
                    for (int index = 0; index < resourceCount; index++)
                    {
                        int tag = reader.ReadByte() | reader.ReadByte() << 8 | reader.ReadByte() << 16;
                        byte resourceFlags = reader.ReadByte();
                        long data = reader.ReadUInt32();
                        resources.Add(new VtfResourceInfo(tag, resourceFlags, data));
                    }
                }

                int lowResByteCount = GetImageByteCount(lowResFormat, lowResWidth, lowResHeight);
                long highResDataOffset = checked((long)headerSize + lowResByteCount);
                foreach (VtfResourceInfo resource in resources)
                {
                    if (resource.Tag == HighResolutionImageResourceTag)
                    {
                        highResDataOffset = resource.Data;
                        break;
                    }
                }

                if (highResDataOffset < headerSize || highResDataOffset > stream.Length)
                    return Fail("VTF high-resolution image offset is invalid or truncated.", out document, out error);

                bool isEnvMap = (flags & TextureFlagsEnvMap) != 0;
                int faces = isEnvMap ? (minorVersion < 5 && firstFrame != ushort.MaxValue ? 7 : 6) : 1;
                var largestToSmallest = new List<(int Level, int Width, int Height, int Depth, int ByteCount)>();

                for (int level = 0; level < mipCount; level++)
                {
                    int mipWidth = Math.Max(1, width >> level);
                    int mipHeight = Math.Max(1, height >> level);
                    int mipDepth = Math.Max(1, depth >> level);
                    int imageBytes = GetImageByteCount(highResFormat, mipWidth, mipHeight);
                    long levelBytes = checked((long)imageBytes * frames * faces * mipDepth);
                    if (levelBytes > int.MaxValue)
                        return Fail("A VTF mip level is too large to process safely.", out document, out error);

                    largestToSmallest.Add((level, mipWidth, mipHeight, mipDepth, (int)levelBytes));
                }

                long cursor = highResDataOffset;
                var offsetsByLevel = new long[mipCount];
                for (int index = largestToSmallest.Count - 1; index >= 0; index--)
                {
                    offsetsByLevel[largestToSmallest[index].Level] = cursor;
                    cursor = checked(cursor + largestToSmallest[index].ByteCount);
                }

                if (cursor > stream.Length)
                    return Fail($"VTF high-resolution image data is truncated by {cursor - stream.Length} bytes.", out document, out error);

                var mipLevels = new List<VtfMipLevelInfo>(mipCount);
                foreach ((int level, int mipWidth, int mipHeight, int mipDepth, int byteCount) in largestToSmallest)
                {
                    mipLevels.Add(new VtfMipLevelInfo(
                        level,
                        mipWidth,
                        mipHeight,
                        mipDepth,
                        offsetsByLevel[level],
                        byteCount));
                }

                document = new VtfDocumentInfo(
                    majorVersion,
                    minorVersion,
                    headerSize,
                    width,
                    height,
                    flags,
                    frames,
                    faces,
                    depth,
                    highResFormat,
                    mipCount,
                    lowResFormat,
                    lowResWidth,
                    lowResHeight,
                    highResDataOffset,
                    mipLevels,
                    resources,
                    stream.Length);
                return true;
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or EndOfStreamException or OverflowException or NotSupportedException)
            {
                return Fail(exception.Message, out document, out error);
            }
        }

        private static int GetImageByteCount(int format, int width, int height)
        {
            if (width <= 0 || height <= 0 || format < 0)
                return 0;

            return format switch
            {
                13 or 20 => checked(Math.Max(1, (width + 3) / 4) * Math.Max(1, (height + 3) / 4) * 8),
                14 or 15 => checked(Math.Max(1, (width + 3) / 4) * Math.Max(1, (height + 3) / 4) * 16),
                0 or 1 or 11 or 12 or 16 or 23 or 26 => checked(width * height * 4),
                2 or 3 or 9 or 10 => checked(width * height * 3),
                4 or 6 or 17 or 18 or 19 or 21 or 22 => checked(width * height * 2),
                5 or 7 or 8 => checked(width * height),
                24 or 25 => checked(width * height * 8),
                _ => throw new NotSupportedException($"Unsupported VTF image format {format}.")
            };
        }

        private static bool Fail(string message, out VtfDocumentInfo document, out string error)
        {
            document = null!;
            error = message;
            return false;
        }
    }
}
