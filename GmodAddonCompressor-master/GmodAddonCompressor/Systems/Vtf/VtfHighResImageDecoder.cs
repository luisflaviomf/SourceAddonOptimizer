using GmodAddonCompressor.Models;
using System;
using System.Collections.Generic;
using System.IO;

namespace GmodAddonCompressor.Systems.Vtf
{
    internal static class VtfHighResImageDecoder
    {
        internal static bool CanDecodeRaw(VtfFileModel metadata)
        {
            return metadata.Frames == 1 &&
                   metadata.Depth <= 1 &&
                   metadata.Width > 0 &&
                   metadata.Height > 0 &&
                   (metadata.HighResImageFormat == 13 ||
                    metadata.HighResImageFormat == 15 ||
                    metadata.HighResImageFormat == 20);
        }

        internal static bool TryDecodeHighResRgba(string vtfFilePath, VtfFileModel metadata, out byte[] rgba)
        {
            rgba = Array.Empty<byte>();
            if (!CanDecodeRaw(metadata))
                return false;

            try
            {
                int topMipBytes = GetImageByteCount(metadata.HighResImageFormat, metadata.Width, metadata.Height);
                byte[] encoded = new byte[topMipBytes];

                using FileStream stream = File.OpenRead(vtfFilePath);
                long offset = GetHighResTopMipOffset(metadata);
                if (offset < 0 || offset + topMipBytes > stream.Length)
                    return false;

                stream.Seek(offset, SeekOrigin.Begin);
                int readBytes = 0;
                while (readBytes < topMipBytes)
                {
                    int chunk = stream.Read(encoded, readBytes, topMipBytes - readBytes);
                    if (chunk <= 0)
                        return false;

                    readBytes += chunk;
                }

                rgba = metadata.HighResImageFormat == 15
                    ? DecodeDxt5(encoded, metadata.Width, metadata.Height)
                    : DecodeDxt1(encoded, metadata.Width, metadata.Height);
                return true;
            }
            catch
            {
                rgba = Array.Empty<byte>();
                return false;
            }
        }

        private static long GetHighResTopMipOffset(VtfFileModel metadata)
        {
            long offset = metadata.HeaderSize +
                          GetImageByteCount(metadata.LowResImageFormat, metadata.LowResWidth, metadata.LowResHeight);

            int width = metadata.Width;
            int height = metadata.Height;
            var mipSizes = new List<int>();

            while (true)
            {
                mipSizes.Add(GetImageByteCount(metadata.HighResImageFormat, width, height));
                if (width == 1 && height == 1)
                    break;

                width = Math.Max(1, width / 2);
                height = Math.Max(1, height / 2);
            }

            for (int index = 1; index < mipSizes.Count; index++)
                offset += mipSizes[index];

            return offset;
        }

        private static int GetImageByteCount(int formatId, int width, int height)
        {
            if (formatId == -1 || width <= 0 || height <= 0)
                return 0;

            if (formatId == 13 || formatId == 20)
                return Math.Max(1, (width + 3) / 4) * Math.Max(1, (height + 3) / 4) * 8;

            if (formatId == 14 || formatId == 15)
                return Math.Max(1, (width + 3) / 4) * Math.Max(1, (height + 3) / 4) * 16;

            if (formatId == 0 || formatId == 1 || formatId == 11 || formatId == 12 || formatId == 16)
                return width * height * 4;

            if (formatId == 2 || formatId == 3)
                return width * height * 3;

            if (formatId == 4 || formatId == 17 || formatId == 18 || formatId == 19 || formatId == 21 || formatId == 22)
                return width * height * 2;

            if (formatId == 5 || formatId == 8)
                return width * height;

            if (formatId == 6)
                return width * height * 2;

            throw new NotSupportedException($"Unsupported VTF image format for byte sizing: {formatId}");
        }

        private static byte[] DecodeDxt1(byte[] encoded, int width, int height)
        {
            byte[] rgba = new byte[width * height * 4];
            int blocksX = Math.Max(1, (width + 3) / 4);
            int blocksY = Math.Max(1, (height + 3) / 4);
            int cursor = 0;

            Span<byte> blockRgba = stackalloc byte[64];
            for (int blockY = 0; blockY < blocksY; blockY++)
            {
                for (int blockX = 0; blockX < blocksX; blockX++)
                {
                    DecodeDxt1Block(encoded.AsSpan(cursor, 8), blockRgba);
                    cursor += 8;
                    BlitBlock(blockRgba, rgba, width, height, blockX, blockY);
                }
            }

            return rgba;
        }

        private static byte[] DecodeDxt5(byte[] encoded, int width, int height)
        {
            byte[] rgba = new byte[width * height * 4];
            int blocksX = Math.Max(1, (width + 3) / 4);
            int blocksY = Math.Max(1, (height + 3) / 4);
            int cursor = 0;

            Span<byte> blockRgba = stackalloc byte[64];
            for (int blockY = 0; blockY < blocksY; blockY++)
            {
                for (int blockX = 0; blockX < blocksX; blockX++)
                {
                    DecodeDxt5Block(encoded.AsSpan(cursor, 16), blockRgba);
                    cursor += 16;
                    BlitBlock(blockRgba, rgba, width, height, blockX, blockY);
                }
            }

            return rgba;
        }

        private static void BlitBlock(ReadOnlySpan<byte> blockRgba, byte[] destination, int width, int height, int blockX, int blockY)
        {
            int x0 = blockX * 4;
            int y0 = blockY * 4;

            for (int y = 0; y < 4; y++)
            {
                int destY = y0 + y;
                if (destY >= height)
                    break;

                for (int x = 0; x < 4; x++)
                {
                    int destX = x0 + x;
                    if (destX >= width)
                        break;

                    int blockOffset = (y * 4 + x) * 4;
                    int destinationOffset = (destY * width + destX) * 4;
                    destination[destinationOffset + 0] = blockRgba[blockOffset + 0];
                    destination[destinationOffset + 1] = blockRgba[blockOffset + 1];
                    destination[destinationOffset + 2] = blockRgba[blockOffset + 2];
                    destination[destinationOffset + 3] = blockRgba[blockOffset + 3];
                }
            }
        }

        private static void DecodeDxt1Block(ReadOnlySpan<byte> block, Span<byte> rgba)
        {
            DecodeDxt1ColorBlock(block, rgba, allowTransparentMode: true);
        }

        private static void DecodeDxt1ColorBlock(ReadOnlySpan<byte> block, Span<byte> rgba, bool allowTransparentMode)
        {
            ushort color0 = (ushort)(block[0] | (block[1] << 8));
            ushort color1 = (ushort)(block[2] | (block[3] << 8));
            uint lookup = (uint)(block[4] |
                                 (block[5] << 8) |
                                 (block[6] << 16) |
                                 (block[7] << 24));

            Span<byte> colors = stackalloc byte[16];
            WriteColor(colors, 0, color0, 255);
            WriteColor(colors, 4, color1, 255);

            if (!allowTransparentMode || color0 > color1)
            {
                InterpolateColor(colors, 8, colors, 0, colors, 4, 2, 1, 3, 255);
                InterpolateColor(colors, 12, colors, 0, colors, 4, 1, 2, 3, 255);
            }
            else
            {
                InterpolateColor(colors, 8, colors, 0, colors, 4, 1, 1, 2, 255);
                colors[12] = 0;
                colors[13] = 0;
                colors[14] = 0;
                colors[15] = 0;
            }

            for (int pixelIndex = 0; pixelIndex < 16; pixelIndex++)
            {
                int paletteIndex = (int)((lookup >> (pixelIndex * 2)) & 0x03);
                int sourceOffset = paletteIndex * 4;
                int targetOffset = pixelIndex * 4;

                rgba[targetOffset + 0] = colors[sourceOffset + 0];
                rgba[targetOffset + 1] = colors[sourceOffset + 1];
                rgba[targetOffset + 2] = colors[sourceOffset + 2];
                rgba[targetOffset + 3] = colors[sourceOffset + 3];
            }
        }

        private static void DecodeDxt5Block(ReadOnlySpan<byte> block, Span<byte> rgba)
        {
            DecodeDxt1ColorBlock(block.Slice(8, 8), rgba, allowTransparentMode: false);

            byte alpha0 = block[0];
            byte alpha1 = block[1];
            Span<byte> alphaPalette = stackalloc byte[8];
            alphaPalette[0] = alpha0;
            alphaPalette[1] = alpha1;

            if (alpha0 > alpha1)
            {
                for (int index = 1; index <= 6; index++)
                    alphaPalette[index + 1] = (byte)(((7 - index) * alpha0 + index * alpha1) / 7);
            }
            else
            {
                for (int index = 1; index <= 4; index++)
                    alphaPalette[index + 1] = (byte)(((5 - index) * alpha0 + index * alpha1) / 5);

                alphaPalette[6] = 0;
                alphaPalette[7] = 255;
            }

            ulong alphaLookup = 0;
            for (int index = 0; index < 6; index++)
                alphaLookup |= (ulong)block[index + 2] << (8 * index);

            for (int pixelIndex = 0; pixelIndex < 16; pixelIndex++)
            {
                int alphaIndex = (int)((alphaLookup >> (pixelIndex * 3)) & 0x07);
                rgba[pixelIndex * 4 + 3] = alphaPalette[alphaIndex];
            }
        }

        private static void WriteColor(Span<byte> colors, int offset, ushort rgb565, byte alpha)
        {
            byte red = (byte)((rgb565 >> 11) & 0x1F);
            byte green = (byte)((rgb565 >> 5) & 0x3F);
            byte blue = (byte)(rgb565 & 0x1F);

            colors[offset + 0] = (byte)((red << 3) | (red >> 2));
            colors[offset + 1] = (byte)((green << 2) | (green >> 4));
            colors[offset + 2] = (byte)((blue << 3) | (blue >> 2));
            colors[offset + 3] = alpha;
        }

        private static void InterpolateColor(
            Span<byte> destination,
            int destinationOffset,
            ReadOnlySpan<byte> first,
            int firstOffset,
            ReadOnlySpan<byte> second,
            int secondOffset,
            int firstWeight,
            int secondWeight,
            int divisor,
            byte alpha)
        {
            destination[destinationOffset + 0] = (byte)((first[firstOffset + 0] * firstWeight + second[secondOffset + 0] * secondWeight) / divisor);
            destination[destinationOffset + 1] = (byte)((first[firstOffset + 1] * firstWeight + second[secondOffset + 1] * secondWeight) / divisor);
            destination[destinationOffset + 2] = (byte)((first[firstOffset + 2] * firstWeight + second[secondOffset + 2] * secondWeight) / divisor);
            destination[destinationOffset + 3] = alpha;
        }
    }
}
