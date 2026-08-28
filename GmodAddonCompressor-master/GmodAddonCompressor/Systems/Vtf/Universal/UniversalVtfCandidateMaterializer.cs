using GmodAddonCompressor.Systems.Vtf;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal static class UniversalVtfCandidateMaterializer
{
    internal static async Task MaterializeAuthoredAsync(
        string source,
        int level,
        string output,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(source))
        {
            throw new ArgumentException("A non-empty source path is required.", nameof(source));
        }

        if (string.IsNullOrWhiteSpace(output))
        {
            throw new ArgumentException("A non-empty output path is required.", nameof(output));
        }

        string sourcePath = Path.GetFullPath(source);
        VtfContainerLayout layout = VtfContainerLayoutReader.Read(sourcePath);
        if (level < 0 || level >= layout.Document.MipCount)
        {
            throw new ArgumentOutOfRangeException(
                nameof(level),
                level,
                $"Authored mip level must be in [0,{layout.Document.MipCount - 1}].");
        }

        cancellationToken.ThrowIfCancellationRequested();
        int width = Math.Max(1, layout.Document.Width >> level);
        int height = Math.Max(1, layout.Document.Height >> level);
        int depth = Math.Max(1, layout.Document.Depth >> level);
        int mipCount = checked(layout.Document.MipCount - level);
        ValidateAuthoredChain(layout, level, width, height, depth, mipCount);

        byte[] highResolutionPayload = await ReadRangesAsync(
            sourcePath,
            layout.Subresources
                .Where(range => range.MipLevel >= level)
                .Select(range => range.Range)
                .OrderBy(range => range.Offset)
                .ToArray(),
            cancellationToken).ConfigureAwait(false);
        byte[]? lowResolutionPayload = layout.LowResolutionImage is null
            ? null
            : await ReadRangesAsync(
                sourcePath,
                new[] { layout.LowResolutionImage },
                cancellationToken).ConfigureAwait(false);
        if (lowResolutionPayload is not null &&
            (layout.Document.LowResWidth > width || layout.Document.LowResHeight > height))
        {
            throw new InvalidDataException("low_resolution_thumbnail_requires_regeneration");
        }

        var request = new VtfContainerWriteRequest(
            sourcePath,
            width,
            height,
            depth,
            mipCount,
            highResolutionPayload,
            layout.Document.LowResWidth,
            layout.Document.LowResHeight,
            lowResolutionPayload,
            RetainedSourceLevel: level,
            LowResolutionPayloadWasCopied: lowResolutionPayload is not null);
        await VtfContainerWriter.WriteAsync(
            request,
            Path.GetFullPath(output),
            cancellationToken).ConfigureAwait(false);
    }

    private static void ValidateAuthoredChain(
        VtfContainerLayout layout,
        int sourceLevel,
        int expectedWidth,
        int expectedHeight,
        int expectedDepth,
        int expectedMipCount)
    {
        int expectedSubresources = 0;
        for (int outputLevel = 0; outputLevel < expectedMipCount; outputLevel++)
        {
            int sourceMip = checked(sourceLevel + outputLevel);
            int width = Math.Max(1, expectedWidth >> outputLevel);
            int height = Math.Max(1, expectedHeight >> outputLevel);
            int depth = Math.Max(1, expectedDepth >> outputLevel);
            VtfSubresourceRange[] ranges = layout.Subresources
                .Where(range => range.MipLevel == sourceMip)
                .ToArray();
            int expectedAtLevel = checked(
                layout.Document.Frames * layout.Document.Faces * depth);
            if (ranges.Length != expectedAtLevel || ranges.Any(range =>
                    range.Width != width || range.Height != height ||
                    range.DepthSlice < 0 || range.DepthSlice >= depth))
            {
                throw new InvalidDataException($"authored_mip_chain_incomplete:{sourceMip}");
            }

            int uniqueCoordinates = ranges
                .Select(range => (range.Frame, range.Face, range.DepthSlice))
                .Distinct()
                .Count();
            if (uniqueCoordinates != expectedAtLevel)
            {
                throw new InvalidDataException($"authored_mip_coordinates_ambiguous:{sourceMip}");
            }

            expectedSubresources = checked(expectedSubresources + expectedAtLevel);
        }

        if (layout.Subresources.Count(range => range.MipLevel >= sourceLevel) !=
            expectedSubresources)
        {
            throw new InvalidDataException("authored_mip_chain_has_unexpected_subresources");
        }

        VtfByteRange[] physical = layout.Subresources
            .Where(range => range.MipLevel >= sourceLevel)
            .Select(range => range.Range)
            .OrderBy(range => range.Offset)
            .ToArray();
        if (physical.Length == 0)
        {
            throw new InvalidDataException("authored_mip_chain_is_empty");
        }

        long cursor = physical[0].Offset;
        foreach (VtfByteRange range in physical)
        {
            if (range.Offset != cursor)
            {
                throw new InvalidDataException("authored_mip_chain_is_not_physically_contiguous");
            }

            cursor = range.End;
        }
    }

    private static async Task<byte[]> ReadRangesAsync(
        string path,
        IReadOnlyList<VtfByteRange> ranges,
        CancellationToken cancellationToken)
    {
        long total = ranges.Sum(range => (long)range.Length);
        if (total <= 0 || total > int.MaxValue)
        {
            throw new InvalidDataException("materialized_vtf_range_size_invalid");
        }

        var output = new byte[checked((int)total)];
        using var stream = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read,
            bufferSize: 1024 * 1024,
            FileOptions.Asynchronous | FileOptions.RandomAccess);
        int destination = 0;
        foreach (VtfByteRange range in ranges)
        {
            stream.Position = range.Offset;
            int remaining = range.Length;
            while (remaining > 0)
            {
                int read = await stream.ReadAsync(
                    output.AsMemory(destination, remaining),
                    cancellationToken).ConfigureAwait(false);
                if (read == 0)
                {
                    throw new EndOfStreamException("VTF range truncated during materialization.");
                }

                destination += read;
                remaining -= read;
            }
        }

        return output;
    }
}
