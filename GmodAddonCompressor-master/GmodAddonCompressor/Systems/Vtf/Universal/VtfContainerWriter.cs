using System.Buffers.Binary;
using System.Security.Cryptography;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record VtfContainerWriteRequest(
    string SourcePath,
    int Width,
    int Height,
    int Depth,
    int MipCount,
    byte[] HighResolutionPayload,
    int LowResolutionWidth,
    int LowResolutionHeight,
    byte[]? LowResolutionPayload,
    int? RetainedSourceLevel,
    bool LowResolutionPayloadWasCopied);

internal static class VtfContainerWriter
{
    private const int InlineCrcTag = 0x435243;
    private const uint ApprovedInlineCrc = 0x3C66AA52;
    private const string ApprovedInlineCrcPath =
        "materials/models/diggercars/mb_w123/w123_int_envmap.vtf";

    internal static async Task WriteAsync(
        VtfContainerWriteRequest request,
        string outputPath,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        string sourcePath = RequireExistingFile(request.SourcePath, nameof(request.SourcePath));
        string destination = RequireDestination(outputPath);
        if (string.Equals(sourcePath, destination, StringComparison.OrdinalIgnoreCase))
        {
            throw new IOException("The source VTF cannot also be the candidate output path.");
        }

        if (File.Exists(destination) || Directory.Exists(destination))
        {
            throw new IOException($"Candidate output already exists: {destination}");
        }

        VtfContainerLayout sourceLayout = VtfContainerLayoutReader.Read(sourcePath);
        byte[] sourceBytes = await ReadStableSourceAsync(
            sourcePath,
            sourceLayout,
            cancellationToken).ConfigureAwait(false);
        ValidateRequest(request, sourceLayout);
        ValidateInlineResources(sourcePath, sourceLayout);

        string directory = Path.GetDirectoryName(destination)
            ?? throw new InvalidDataException("Candidate output has no parent directory.");
        Directory.CreateDirectory(directory);
        string temporary = Path.Combine(
            directory,
            $".{Path.GetFileName(destination)}.universal-vtf-{Guid.NewGuid():N}.vtf");
        try
        {
            byte[] candidate = BuildContainer(request, sourceLayout, sourceBytes);
            await WriteNewFileAsync(temporary, candidate, cancellationToken).ConfigureAwait(false);
            cancellationToken.ThrowIfCancellationRequested();
            AuditCandidate(sourcePath, sourceLayout, request, temporary);
            cancellationToken.ThrowIfCancellationRequested();
            File.Move(temporary, destination, overwrite: false);
        }
        finally
        {
            DeleteOwnedTemporary(directory, temporary);
        }
    }

    private static byte[] BuildContainer(
        VtfContainerWriteRequest request,
        VtfContainerLayout layout,
        byte[] sourceBytes)
    {
        VtfByteRange highResolutionRange = GetHighResolutionRange(layout, sourceBytes);
        bool resourceLayout = layout.Document.Resources.Count > 0;
        if (resourceLayout && !layout.Resources.Any(resource =>
                resource.Tag == VtfContainerLayoutReader.HighResolutionImageResourceTag &&
                resource.PayloadRange is not null))
        {
            throw new InvalidDataException("resource_directory_lacks_high_resolution_image");
        }

        if (resourceLayout && request.LowResolutionPayload is not null &&
            !layout.Resources.Any(resource =>
                resource.Tag == VtfContainerLayoutReader.LowResolutionImageResourceTag &&
                resource.PayloadRange is not null))
        {
            throw new InvalidDataException("resource_directory_lacks_low_resolution_image");
        }

        byte[] header = Slice(sourceBytes, layout.Header);
        var stream = new MemoryStream(capacity: checked(
            header.Length + request.HighResolutionPayload.Length +
            (request.LowResolutionPayload?.Length ?? 0) + 4096));
        stream.Write(header, 0, header.Length);

        Dictionary<RangeKey, long> relocatedOffsets;
        if (resourceLayout)
        {
            relocatedOffsets = WriteResourceLayoutPayloads(
                stream,
                sourceBytes,
                layout,
                highResolutionRange,
                request.HighResolutionPayload,
                request.LowResolutionPayload);
        }
        else
        {
            relocatedOffsets = WriteLegacyLayoutPayloads(
                stream,
                sourceBytes,
                layout,
                highResolutionRange,
                request.HighResolutionPayload,
                request.LowResolutionPayload);
        }

        byte[] output = stream.ToArray();
        PatchHeader(output, layout, request, relocatedOffsets, highResolutionRange);
        return output;
    }

    private static Dictionary<RangeKey, long> WriteResourceLayoutPayloads(
        MemoryStream output,
        byte[] sourceBytes,
        VtfContainerLayout layout,
        VtfByteRange highResolutionRange,
        byte[] highResolutionPayload,
        byte[]? lowResolutionPayload)
    {
        var ranges = new Dictionary<RangeKey, SourceSegment>();
        AddSegment(ranges, highResolutionRange, SourceSegmentKind.HighResolution);
        if (layout.LowResolutionImage is not null)
        {
            AddSegment(ranges, layout.LowResolutionImage, SourceSegmentKind.LowResolution);
        }

        foreach (VtfResourceChunk resource in layout.Resources)
        {
            if (resource.PayloadRange is not null)
            {
                AddSegment(ranges, resource.PayloadRange, SourceSegmentKind.Preserved);
            }
        }

        foreach (VtfByteRange unmapped in layout.UnmappedRanges)
        {
            if (unmapped.Offset >= layout.Header.End)
            {
                AddSegment(ranges, unmapped, SourceSegmentKind.Preserved);
            }
        }

        SourceSegment[] ordered = ranges.Values
            .OrderBy(segment => segment.Range.Offset)
            .ThenBy(segment => segment.Range.Length)
            .ToArray();
        long sourceCursor = layout.Header.End;
        foreach (SourceSegment segment in ordered)
        {
            if (segment.Range.Offset < sourceCursor)
            {
                throw new InvalidDataException("source_payload_ranges_overlap");
            }

            sourceCursor = segment.Range.End;
        }

        var relocated = new Dictionary<RangeKey, long>();
        foreach (SourceSegment segment in ordered)
        {
            relocated.Add(RangeKey.From(segment.Range), output.Position);
            byte[] payload = segment.Kind switch
            {
                SourceSegmentKind.HighResolution => highResolutionPayload,
                SourceSegmentKind.LowResolution => lowResolutionPayload
                    ?? throw new InvalidDataException("low_resolution_payload_missing"),
                _ => Slice(sourceBytes, segment.Range)
            };
            output.Write(payload, 0, payload.Length);
        }

        return relocated;
    }

    private static Dictionary<RangeKey, long> WriteLegacyLayoutPayloads(
        MemoryStream output,
        byte[] sourceBytes,
        VtfContainerLayout layout,
        VtfByteRange highResolutionRange,
        byte[] highResolutionPayload,
        byte[]? lowResolutionPayload)
    {
        long expectedLowOffset = layout.Header.End;
        if (layout.LowResolutionImage is not null &&
            layout.LowResolutionImage.Offset != expectedLowOffset)
        {
            throw new InvalidDataException("legacy_low_resolution_image_is_not_contiguous");
        }

        long expectedHighOffset = checked(
            expectedLowOffset + (layout.LowResolutionImage?.Length ?? 0));
        if (highResolutionRange.Offset != expectedHighOffset)
        {
            throw new InvalidDataException("legacy_high_resolution_image_is_not_contiguous");
        }

        var relocated = new Dictionary<RangeKey, long>();
        if (layout.LowResolutionImage is not null)
        {
            relocated.Add(RangeKey.From(layout.LowResolutionImage), output.Position);
            byte[] low = lowResolutionPayload
                ?? throw new InvalidDataException("low_resolution_payload_missing");
            output.Write(low, 0, low.Length);
        }

        relocated.Add(RangeKey.From(highResolutionRange), output.Position);
        output.Write(highResolutionPayload, 0, highResolutionPayload.Length);

        foreach (VtfByteRange tail in layout.UnmappedRanges.OrderBy(range => range.Offset))
        {
            if (tail.Offset < highResolutionRange.End)
            {
                throw new InvalidDataException("legacy_container_has_unmapped_interior_bytes");
            }

            byte[] bytes = Slice(sourceBytes, tail);
            relocated.Add(RangeKey.From(tail), output.Position);
            output.Write(bytes, 0, bytes.Length);
        }

        return relocated;
    }

    private static void PatchHeader(
        byte[] output,
        VtfContainerLayout source,
        VtfContainerWriteRequest request,
        IReadOnlyDictionary<RangeKey, long> relocatedOffsets,
        VtfByteRange highResolutionRange)
    {
        BinaryPrimitives.WriteUInt16LittleEndian(output.AsSpan(16, 2), checked((ushort)request.Width));
        BinaryPrimitives.WriteUInt16LittleEndian(output.AsSpan(18, 2), checked((ushort)request.Height));
        output[56] = checked((byte)request.MipCount);
        output[61] = checked((byte)request.LowResolutionWidth);
        output[62] = checked((byte)request.LowResolutionHeight);
        if (source.Document.MinorVersion >= 2)
        {
            BinaryPrimitives.WriteUInt16LittleEndian(
                output.AsSpan(63, 2),
                checked((ushort)request.Depth));
        }

        for (int index = 0; index < source.Resources.Count; index++)
        {
            VtfResourceChunk resource = source.Resources[index];
            if (resource.InlineValue.HasValue)
            {
                BinaryPrimitives.WriteUInt32LittleEndian(
                    output.AsSpan(checked(84 + index * 8), 4),
                    resource.InlineValue.Value);
                continue;
            }

            VtfByteRange range = resource.PayloadRange
                ?? throw new InvalidDataException($"offset_resource_without_range:{resource.Tag:x6}");
            RangeKey key = resource.Tag == VtfContainerLayoutReader.HighResolutionImageResourceTag
                ? RangeKey.From(highResolutionRange)
                : RangeKey.From(range);
            if (!relocatedOffsets.TryGetValue(key, out long relocated) || relocated > uint.MaxValue)
            {
                throw new InvalidDataException($"resource_relocation_missing:{resource.Tag:x6}");
            }

            BinaryPrimitives.WriteUInt32LittleEndian(
                output.AsSpan(checked(84 + index * 8), 4),
                checked((uint)relocated));
        }
    }

    private static void ValidateRequest(
        VtfContainerWriteRequest request,
        VtfContainerLayout source)
    {
        if (request.Width <= 0 || request.Width > ushort.MaxValue ||
            request.Height <= 0 || request.Height > ushort.MaxValue ||
            request.Depth <= 0 || request.Depth > ushort.MaxValue ||
            request.MipCount <= 0 || request.MipCount > byte.MaxValue)
        {
            throw new ArgumentOutOfRangeException(nameof(request),
                "Candidate dimensions, depth, or mip count are outside VTF limits.");
        }

        if (source.Document.MinorVersion < 2 && request.Depth != 1)
        {
            throw new InvalidDataException("pre_7_2_vtf_cannot_store_volume_depth");
        }

        int expectedHighBytes = ExpectedHighResolutionBytes(source.Document, request);
        if (request.HighResolutionPayload is null ||
            request.HighResolutionPayload.Length != expectedHighBytes)
        {
            throw new InvalidDataException(
                $"high_resolution_payload_size:{request.HighResolutionPayload?.Length ?? -1}!={expectedHighBytes}");
        }

        if (request.LowResolutionPayload is null)
        {
            if (request.LowResolutionWidth != 0 || request.LowResolutionHeight != 0 ||
                source.LowResolutionImage is not null)
            {
                throw new InvalidDataException("low_resolution_payload_or_dimensions_missing");
            }
        }
        else
        {
            if (source.LowResolutionImage is null ||
                request.LowResolutionWidth <= 0 || request.LowResolutionWidth > byte.MaxValue ||
                request.LowResolutionHeight <= 0 || request.LowResolutionHeight > byte.MaxValue)
            {
                throw new InvalidDataException("low_resolution_payload_is_not_representable");
            }

            int expectedLowBytes = VtfDocumentReader.GetImageByteCount(
                source.Document.LowResFormat,
                request.LowResolutionWidth,
                request.LowResolutionHeight);
            if (request.LowResolutionPayload.Length != expectedLowBytes)
            {
                throw new InvalidDataException(
                    $"low_resolution_payload_size:{request.LowResolutionPayload.Length}!={expectedLowBytes}");
            }
        }
    }

    private static int ExpectedHighResolutionBytes(
        VtfDocumentInfo source,
        VtfContainerWriteRequest request)
    {
        long total = 0;
        for (int level = 0; level < request.MipCount; level++)
        {
            int width = Math.Max(1, request.Width >> level);
            int height = Math.Max(1, request.Height >> level);
            int depth = Math.Max(1, request.Depth >> level);
            int imageBytes = VtfDocumentReader.GetImageByteCount(
                source.HighResFormat,
                width,
                height);
            total = checked(total + (long)imageBytes * source.Frames * source.Faces * depth);
        }

        if (total <= 0 || total > int.MaxValue)
        {
            throw new InvalidDataException("candidate_high_resolution_size_invalid");
        }

        return checked((int)total);
    }

    private static void ValidateInlineResources(string sourcePath, VtfContainerLayout layout)
    {
        foreach (VtfResourceChunk resource in layout.Resources.Where(resource =>
                     resource.InlineValue.HasValue))
        {
            bool approved = resource.Tag == InlineCrcTag &&
                            resource.InlineValue == ApprovedInlineCrc &&
                            IsApprovedInlineCrcPath(sourcePath);
            if (!approved)
            {
                throw new InvalidDataException(
                    $"inline_resource_without_policy:{resource.Tag:x6}:{resource.InlineValue:x8}");
            }
        }
    }

    private static bool IsApprovedInlineCrcPath(string sourcePath)
    {
        string normalized = Path.GetFullPath(sourcePath).Replace('\\', '/').TrimEnd('/');
        return normalized.Equals(ApprovedInlineCrcPath, StringComparison.OrdinalIgnoreCase) ||
               normalized.EndsWith('/' + ApprovedInlineCrcPath, StringComparison.OrdinalIgnoreCase);
    }

    private static void AuditCandidate(
        string sourcePath,
        VtfContainerLayout before,
        VtfContainerWriteRequest request,
        string candidatePath)
    {
        VtfContainerLayout after = VtfContainerLayoutReader.Read(candidatePath);
        VtfDocumentInfo oldDocument = before.Document;
        VtfDocumentInfo newDocument = after.Document;
        if (newDocument.MajorVersion != oldDocument.MajorVersion ||
            newDocument.MinorVersion != oldDocument.MinorVersion ||
            newDocument.HeaderSize != oldDocument.HeaderSize ||
            newDocument.Width != request.Width ||
            newDocument.Height != request.Height ||
            newDocument.Depth != request.Depth ||
            newDocument.MipCount != request.MipCount ||
            newDocument.Flags != oldDocument.Flags ||
            newDocument.Frames != oldDocument.Frames ||
            newDocument.FirstFrame != oldDocument.FirstFrame ||
            newDocument.Faces != oldDocument.Faces ||
            newDocument.ReflectivityX != oldDocument.ReflectivityX ||
            newDocument.ReflectivityY != oldDocument.ReflectivityY ||
            newDocument.ReflectivityZ != oldDocument.ReflectivityZ ||
            newDocument.BumpScale != oldDocument.BumpScale ||
            newDocument.HighResFormat != oldDocument.HighResFormat ||
            newDocument.LowResFormat != oldDocument.LowResFormat ||
            newDocument.LowResWidth != request.LowResolutionWidth ||
            newDocument.LowResHeight != request.LowResolutionHeight)
        {
            throw new InvalidDataException("candidate_header_audit_failed");
        }

        AuditResources(before, after);
        AuditUnmappedRanges(before, after);
        if (request.LowResolutionPayloadWasCopied)
        {
            if (before.LowResolutionImage is null || after.LowResolutionImage is null ||
                before.LowResolutionImage.Sha256 != after.LowResolutionImage.Sha256)
            {
                throw new InvalidDataException("copied_low_resolution_image_changed");
            }
        }

        if (request.RetainedSourceLevel.HasValue)
        {
            AuditRetainedSubresources(
                before,
                after,
                request.RetainedSourceLevel.Value);
        }

        foreach (VtfSubresourceRange subresource in after.Subresources)
        {
            if (!VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                    candidatePath,
                    after.Document,
                    subresource.MipLevel,
                    subresource.Frame,
                    subresource.Face,
                    subresource.DepthSlice,
                    out byte[] rgba) ||
                rgba.Length != checked(subresource.Width * subresource.Height * 4))
            {
                throw new InvalidDataException(
                    $"candidate_subresource_decode_failed:{subresource.MipLevel}:" +
                    $"{subresource.Frame}:{subresource.Face}:{subresource.DepthSlice}");
            }
        }

        VtfContainerLayout currentSource = VtfContainerLayoutReader.Read(sourcePath);
        if (currentSource.FileSha256 != before.FileSha256)
        {
            throw new InvalidDataException("source_vtf_changed_during_materialization");
        }
    }

    private static void AuditResources(VtfContainerLayout before, VtfContainerLayout after)
    {
        if (before.Resources.Count != after.Resources.Count)
        {
            throw new InvalidDataException("resource_count_changed");
        }

        for (int index = 0; index < before.Resources.Count; index++)
        {
            VtfResourceChunk original = before.Resources[index];
            VtfResourceChunk candidate = after.Resources[index];
            if (original.Tag != candidate.Tag || original.Flags != candidate.Flags ||
                original.InlineValue != candidate.InlineValue)
            {
                throw new InvalidDataException($"resource_entry_changed:{index}");
            }

            if (original.Tag is VtfContainerLayoutReader.LowResolutionImageResourceTag or
                VtfContainerLayoutReader.HighResolutionImageResourceTag ||
                original.PayloadRange is null)
            {
                continue;
            }

            if (candidate.PayloadRange is null ||
                original.PayloadRange.Length != candidate.PayloadRange.Length ||
                original.PayloadRange.Sha256 != candidate.PayloadRange.Sha256)
            {
                throw new InvalidDataException($"offset_resource_payload_changed:{original.Tag:x6}");
            }
        }
    }

    private static void AuditUnmappedRanges(VtfContainerLayout before, VtfContainerLayout after)
    {
        string[] oldHashes = before.UnmappedRanges
            .Where(range => range.Offset >= before.Header.End)
            .Select(range => $"{range.Length}:{range.Sha256}")
            .ToArray();
        string[] newHashes = after.UnmappedRanges
            .Where(range => range.Offset >= after.Header.End)
            .Select(range => $"{range.Length}:{range.Sha256}")
            .ToArray();
        if (!oldHashes.SequenceEqual(newHashes, StringComparer.Ordinal))
        {
            throw new InvalidDataException("unmapped_payload_changed");
        }
    }

    private static void AuditRetainedSubresources(
        VtfContainerLayout before,
        VtfContainerLayout after,
        int sourceLevel)
    {
        foreach (VtfSubresourceRange candidate in after.Subresources)
        {
            VtfSubresourceRange[] matches = before.Subresources.Where(original =>
                    original.MipLevel == checked(candidate.MipLevel + sourceLevel) &&
                    original.Frame == candidate.Frame &&
                    original.Face == candidate.Face &&
                    original.DepthSlice == candidate.DepthSlice)
                .ToArray();
            if (matches.Length != 1 ||
                matches[0].Width != candidate.Width ||
                matches[0].Height != candidate.Height ||
                matches[0].Range.Length != candidate.Range.Length ||
                matches[0].Range.Sha256 != candidate.Range.Sha256)
            {
                throw new InvalidDataException(
                    $"retained_subresource_changed:{candidate.MipLevel}:" +
                    $"{candidate.Frame}:{candidate.Face}:{candidate.DepthSlice}");
            }
        }
    }

    private static async Task<byte[]> ReadStableSourceAsync(
        string sourcePath,
        VtfContainerLayout layout,
        CancellationToken cancellationToken)
    {
        if (layout.Document.FileLength > int.MaxValue)
        {
            throw new InvalidDataException("source_vtf_too_large");
        }

        var bytes = new byte[checked((int)layout.Document.FileLength)];
        using var stream = new FileStream(
            sourcePath,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read,
            bufferSize: 1024 * 1024,
            FileOptions.Asynchronous | FileOptions.SequentialScan);
        int read = 0;
        while (read < bytes.Length)
        {
            int count = await stream.ReadAsync(
                bytes.AsMemory(read, bytes.Length - read),
                cancellationToken).ConfigureAwait(false);
            if (count == 0)
            {
                throw new EndOfStreamException("source_vtf_truncated_during_materialization");
            }

            read += count;
        }

        if (stream.ReadByte() != -1 || Hash(bytes) != layout.FileSha256)
        {
            throw new InvalidDataException("source_vtf_changed_after_layout_read");
        }

        return bytes;
    }

    private static async Task WriteNewFileAsync(
        string path,
        byte[] bytes,
        CancellationToken cancellationToken)
    {
        using var stream = new FileStream(
            path,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None,
            bufferSize: 1024 * 1024,
            FileOptions.Asynchronous | FileOptions.WriteThrough);
        await stream.WriteAsync(bytes.AsMemory(), cancellationToken).ConfigureAwait(false);
        await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
    }

    private static void AddSegment(
        IDictionary<RangeKey, SourceSegment> segments,
        VtfByteRange range,
        SourceSegmentKind kind)
    {
        RangeKey key = RangeKey.From(range);
        if (segments.TryGetValue(key, out SourceSegment? existing))
        {
            SourceSegmentKind resolved = ResolveSegmentKind(existing.Kind, kind);
            segments[key] = existing with { Kind = resolved };
            return;
        }

        segments.Add(key, new SourceSegment(range, kind));
    }

    private static SourceSegmentKind ResolveSegmentKind(
        SourceSegmentKind first,
        SourceSegmentKind second)
    {
        if (first == second || second == SourceSegmentKind.Preserved)
        {
            return first;
        }

        if (first == SourceSegmentKind.Preserved)
        {
            return second;
        }

        throw new InvalidDataException("low_and_high_resolution_ranges_alias");
    }

    private static VtfByteRange GetHighResolutionRange(
        VtfContainerLayout layout,
        byte[] sourceBytes)
    {
        VtfSubresourceRange[] ordered = layout.Subresources
            .OrderBy(range => range.Range.Offset)
            .ToArray();
        if (ordered.Length == 0)
        {
            throw new InvalidDataException("high_resolution_image_has_no_subresources");
        }

        long start = ordered[0].Range.Offset;
        long cursor = start;
        foreach (VtfSubresourceRange subresource in ordered)
        {
            if (subresource.Range.Offset != cursor)
            {
                throw new InvalidDataException("high_resolution_subresources_are_not_contiguous");
            }

            cursor = subresource.Range.End;
        }

        long length = checked(cursor - start);
        if (length > int.MaxValue)
        {
            throw new InvalidDataException("high_resolution_image_too_large");
        }

        string sha = Convert.ToHexString(SHA256.HashData(
                sourceBytes.AsSpan(checked((int)start), checked((int)length))))
            .ToLowerInvariant();
        return new VtfByteRange(start, checked((int)length), sha);
    }

    private static byte[] Slice(byte[] source, VtfByteRange range)
    {
        var result = new byte[range.Length];
        Buffer.BlockCopy(source, checked((int)range.Offset), result, 0, range.Length);
        return result;
    }

    private static string Hash(byte[] bytes) =>
        Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private static string RequireExistingFile(string requested, string parameterName)
    {
        if (string.IsNullOrWhiteSpace(requested))
        {
            throw new ArgumentException("A non-empty source path is required.", parameterName);
        }

        string fullPath = Path.GetFullPath(requested);
        if (!File.Exists(fullPath))
        {
            throw new FileNotFoundException("Source VTF does not exist.", fullPath);
        }

        return fullPath;
    }

    private static string RequireDestination(string requested)
    {
        if (string.IsNullOrWhiteSpace(requested))
        {
            throw new ArgumentException("A non-empty candidate path is required.", nameof(requested));
        }

        return Path.GetFullPath(requested);
    }

    private static void DeleteOwnedTemporary(string directory, string temporary)
    {
        string parent = Path.GetFullPath(directory)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string path = Path.GetFullPath(temporary);
        bool directChild = string.Equals(
            Path.GetDirectoryName(path)?.TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar),
            parent,
            StringComparison.OrdinalIgnoreCase);
        bool ownedName = Path.GetFileName(path)
            .Contains(".universal-vtf-", StringComparison.Ordinal);
        if (!directChild || !ownedName)
        {
            throw new InvalidOperationException($"Refusing to remove unowned temporary VTF: {path}");
        }

        if (File.Exists(path))
        {
            File.Delete(path);
        }
    }

    private readonly record struct RangeKey(long Offset, int Length)
    {
        internal static RangeKey From(VtfByteRange range) => new(range.Offset, range.Length);
    }

    private sealed record SourceSegment(VtfByteRange Range, SourceSegmentKind Kind);

    private enum SourceSegmentKind
    {
        Preserved,
        LowResolution,
        HighResolution
    }
}
