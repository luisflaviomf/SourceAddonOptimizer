using System.Security.Cryptography;
using GmodAddonCompressor.Models;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record VtfByteRange(long Offset, int Length, string Sha256)
{
    internal long End => checked(Offset + Length);
}

internal sealed record VtfSubresourceRange(
    int MipLevel,
    int Frame,
    int Face,
    int DepthSlice,
    int Width,
    int Height,
    VtfByteRange Range);

internal sealed record VtfResourceChunk(
    int Tag,
    byte Flags,
    uint? InlineValue,
    VtfByteRange? PayloadRange);

internal sealed record VtfContainerLayout(
    VtfDocumentInfo Document,
    VtfByteRange Header,
    VtfByteRange? LowResolutionImage,
    IReadOnlyList<VtfSubresourceRange> Subresources,
    IReadOnlyList<VtfResourceChunk> Resources,
    IReadOnlyList<VtfByteRange> UnmappedRanges,
    string FileSha256);

internal static class VtfContainerLayoutReader
{
    internal const int LowResolutionImageResourceTag = 0x000001;
    internal const int HighResolutionImageResourceTag = 0x000030;
    internal const byte ResourceHasNoDataChunk = 0x02;

    internal static VtfContainerLayout Read(string filePath)
    {
        if (!VtfDocumentReader.TryRead(filePath, out VtfDocumentInfo document, out string error))
        {
            throw new InvalidDataException(error);
        }

        byte[] bytes = ReadStableBytes(filePath, document.FileLength);
        var header = CreateRange(bytes, 0, document.HeaderSize);

        int lowResolutionBytes = VtfDocumentReader.GetImageByteCount(
            document.LowResFormat,
            document.LowResWidth,
            document.LowResHeight);
        VtfByteRange? lowResolutionImage = null;
        if (lowResolutionBytes > 0)
        {
            long lowResolutionOffset = ResolveLowResolutionOffset(document);
            lowResolutionImage = CreateRange(bytes, lowResolutionOffset, lowResolutionBytes);
        }

        long highResolutionBytes = document.MipLevels.Sum(x => (long)x.ByteCount);
        if (highResolutionBytes <= 0 || highResolutionBytes > int.MaxValue)
        {
            throw new InvalidDataException("high_resolution_range_size_invalid");
        }

        var highResolutionRange = CreateRange(
            bytes,
            document.HighResDataOffset,
            checked((int)highResolutionBytes));
        IReadOnlyList<VtfSubresourceRange> subresources =
            CreateSubresourceRanges(bytes, document, highResolutionRange);
        IReadOnlyList<VtfResourceChunk> resources = CreateResourceChunks(
            bytes,
            document,
            lowResolutionImage,
            highResolutionRange);

        var occupied = new List<VtfByteRange> { header, highResolutionRange };
        if (lowResolutionImage is not null)
        {
            occupied.Add(lowResolutionImage);
        }

        foreach (VtfResourceChunk resource in resources)
        {
            if (resource.PayloadRange is null ||
                resource.Tag is LowResolutionImageResourceTag or HighResolutionImageResourceTag)
            {
                continue;
            }

            occupied.Add(resource.PayloadRange);
        }

        IReadOnlyList<VtfByteRange> unmapped = PartitionUnmappedRanges(bytes, occupied);
        return new VtfContainerLayout(
            document,
            header,
            lowResolutionImage,
            subresources,
            resources,
            unmapped,
            Hash(bytes));
    }

    private static byte[] ReadStableBytes(string filePath, long expectedLength)
    {
        if (expectedLength < 0 || expectedLength > int.MaxValue)
        {
            throw new InvalidDataException("file_too_large_for_checked_layout");
        }

        var before = new FileInfo(filePath);
        DateTime beforeWrite = before.LastWriteTimeUtc;
        long beforeLength = before.Length;
        if (beforeLength != expectedLength)
        {
            throw new InvalidDataException("file_length_changed_after_header_parse");
        }

        byte[] bytes = new byte[checked((int)expectedLength)];
        using (var stream = new FileStream(
                   filePath,
                   FileMode.Open,
                   FileAccess.Read,
                   FileShare.Read,
                   bufferSize: 1024 * 1024,
                   FileOptions.SequentialScan))
        {
            int read = 0;
            while (read < bytes.Length)
            {
                int count = stream.Read(bytes, read, bytes.Length - read);
                if (count == 0)
                {
                    throw new EndOfStreamException("file_truncated_during_layout_read");
                }

                read += count;
            }

            if (stream.ReadByte() != -1)
            {
                throw new InvalidDataException("file_grew_during_layout_read");
            }
        }

        before.Refresh();
        if (!before.Exists || before.Length != beforeLength || before.LastWriteTimeUtc != beforeWrite)
        {
            throw new InvalidDataException("file_changed_during_layout_read");
        }

        return bytes;
    }

    private static long ResolveLowResolutionOffset(VtfDocumentInfo document)
    {
        VtfResourceInfo? resource = document.Resources
            .SingleOrDefault(x => x.Tag == LowResolutionImageResourceTag);
        if (resource is null)
        {
            return document.HeaderSize;
        }

        if ((resource.Flags & ResourceHasNoDataChunk) != 0)
        {
            throw new InvalidDataException("low_resolution_image_resource_is_inline");
        }

        return resource.Data;
    }

    private static IReadOnlyList<VtfSubresourceRange> CreateSubresourceRanges(
        byte[] bytes,
        VtfDocumentInfo document,
        VtfByteRange highResolutionRange)
    {
        var ranges = new List<VtfSubresourceRange>();
        foreach (VtfMipLevelInfo mip in document.MipLevels.OrderBy(x => x.Level))
        {
            int imageBytes = VtfDocumentReader.GetImageByteCount(
                document.HighResFormat,
                mip.Width,
                mip.Height);
            long expectedMipBytes = checked(
                (long)imageBytes * document.Frames * document.Faces * mip.Depth);
            if (expectedMipBytes != mip.ByteCount)
            {
                throw new InvalidDataException($"mip_byte_count_mismatch:{mip.Level}");
            }

            long cursor = mip.Offset;
            for (int frame = 0; frame < document.Frames; frame++)
            for (int face = 0; face < document.Faces; face++)
            for (int depthSlice = 0; depthSlice < mip.Depth; depthSlice++)
            {
                VtfByteRange range = CreateRange(bytes, cursor, imageBytes);
                if (range.Offset < highResolutionRange.Offset || range.End > highResolutionRange.End)
                {
                    throw new InvalidDataException($"subresource_outside_high_resolution:{mip.Level}");
                }

                ranges.Add(new VtfSubresourceRange(
                    mip.Level,
                    frame,
                    face,
                    depthSlice,
                    mip.Width,
                    mip.Height,
                    range));
                cursor = range.End;
            }

            if (cursor != checked(mip.Offset + mip.ByteCount))
            {
                throw new InvalidDataException($"mip_partition_mismatch:{mip.Level}");
            }
        }

        VtfByteRange[] ordered = ranges.Select(x => x.Range).OrderBy(x => x.Offset).ToArray();
        long expectedCursor = highResolutionRange.Offset;
        foreach (VtfByteRange range in ordered)
        {
            if (range.Offset != expectedCursor)
            {
                throw new InvalidDataException("high_resolution_subresource_gap_or_overlap");
            }

            expectedCursor = range.End;
        }

        if (expectedCursor != highResolutionRange.End)
        {
            throw new InvalidDataException("high_resolution_subresources_do_not_fill_range");
        }

        return ranges.AsReadOnly();
    }

    private static IReadOnlyList<VtfResourceChunk> CreateResourceChunks(
        byte[] bytes,
        VtfDocumentInfo document,
        VtfByteRange? lowResolutionImage,
        VtfByteRange highResolutionImage)
    {
        long[] offsetBoundaries = document.Resources
            .Where(x => (x.Flags & ResourceHasNoDataChunk) == 0)
            .Select(x => x.Data)
            .Append(bytes.LongLength)
            .Distinct()
            .OrderBy(x => x)
            .ToArray();

        var resources = new List<VtfResourceChunk>(document.Resources.Count);
        foreach (VtfResourceInfo resource in document.Resources)
        {
            if ((resource.Flags & ResourceHasNoDataChunk) != 0)
            {
                resources.Add(new VtfResourceChunk(
                    resource.Tag,
                    resource.Flags,
                    checked((uint)resource.Data),
                    null));
                continue;
            }

            VtfByteRange payload;
            if (resource.Tag == LowResolutionImageResourceTag)
            {
                payload = lowResolutionImage
                    ?? throw new InvalidDataException("low_resolution_resource_without_payload");
            }
            else if (resource.Tag == HighResolutionImageResourceTag)
            {
                payload = highResolutionImage;
            }
            else
            {
                if (resource.Data < document.HeaderSize || resource.Data >= bytes.LongLength)
                {
                    throw new InvalidDataException($"resource_offset_invalid:{resource.Tag:x6}");
                }

                long end = offsetBoundaries.FirstOrDefault(x => x > resource.Data);
                if (end <= resource.Data)
                {
                    throw new InvalidDataException($"resource_range_unbounded:{resource.Tag:x6}");
                }

                long length = checked(end - resource.Data);
                if (length > int.MaxValue)
                {
                    throw new InvalidDataException($"resource_range_too_large:{resource.Tag:x6}");
                }

                payload = CreateRange(bytes, resource.Data, checked((int)length));
            }

            resources.Add(new VtfResourceChunk(resource.Tag, resource.Flags, null, payload));
        }

        return resources.AsReadOnly();
    }

    private static IReadOnlyList<VtfByteRange> PartitionUnmappedRanges(
        byte[] bytes,
        IEnumerable<VtfByteRange> occupiedRanges)
    {
        VtfByteRange[] ordered = occupiedRanges.OrderBy(x => x.Offset).ToArray();
        long cursor = 0;
        var unmapped = new List<VtfByteRange>();
        foreach (VtfByteRange range in ordered)
        {
            if (range.Length <= 0 || range.Offset < 0 || range.End > bytes.LongLength)
            {
                throw new InvalidDataException("occupied_range_invalid");
            }

            if (range.Offset < cursor)
            {
                throw new InvalidDataException("container_ranges_overlap");
            }

            if (range.Offset > cursor)
            {
                unmapped.Add(CreateRange(bytes, cursor, checked((int)(range.Offset - cursor))));
            }

            cursor = range.End;
        }

        if (cursor < bytes.LongLength)
        {
            unmapped.Add(CreateRange(bytes, cursor, checked((int)(bytes.LongLength - cursor))));
        }

        return unmapped.AsReadOnly();
    }

    private static VtfByteRange CreateRange(byte[] bytes, long offset, int length)
    {
        if (offset < 0 || length <= 0 || offset > bytes.LongLength - length)
        {
            throw new InvalidDataException($"range_out_of_bounds:{offset}+{length}/{bytes.LongLength}");
        }

        int intOffset = checked((int)offset);
        using SHA256 sha256 = SHA256.Create();
        string hash = Convert.ToHexString(sha256.ComputeHash(bytes, intOffset, length))
            .ToLowerInvariant();
        return new VtfByteRange(offset, length, hash);
    }

    private static string Hash(byte[] bytes)
    {
        using SHA256 sha256 = SHA256.Create();
        return Convert.ToHexString(sha256.ComputeHash(bytes)).ToLowerInvariant();
    }
}
