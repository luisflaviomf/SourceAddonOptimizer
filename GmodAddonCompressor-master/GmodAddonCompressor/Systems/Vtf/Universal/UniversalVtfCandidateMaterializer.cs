using GmodAddonCompressor.Systems.Vtf;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed class UniversalVtfEncodingSession : IDisposable
{
    private readonly string _sessionRoot;
    private bool _disposed;

    internal UniversalVtfEncodingSession(
        string sessionRoot,
        UniversalVtfToolchainFingerprint toolchain,
        string workDirectory)
    {
        _sessionRoot = sessionRoot;
        Fingerprint = toolchain;
        Encoder = new VtfCmdBlockEncoder(toolchain);
        WorkDirectory = workDirectory;
    }

    internal UniversalVtfToolchainFingerprint Fingerprint { get; }

    internal VtfCmdBlockEncoder Encoder { get; }

    internal string WorkDirectory { get; }

    internal Task MaterializeGeneratedAsync(
        string source,
        int level,
        UniversalVtfFilterRoute route,
        string output,
        CancellationToken cancellationToken)
    {
        ThrowIfDisposed();
        return UniversalVtfCandidateMaterializer.MaterializeGeneratedWithSessionAsync(
            source,
            level,
            UniversalVtfCandidateMaterializer.CreateRouteOnlySemantics(route),
            output,
            this,
            cancellationToken);
    }

    internal Task MaterializeAuthoredAsync(
        string source,
        int level,
        UniversalVtfFilterRoute route,
        string output,
        CancellationToken cancellationToken)
    {
        ThrowIfDisposed();
        return UniversalVtfCandidateMaterializer.MaterializeAuthoredWithSessionAsync(
            source,
            level,
            UniversalVtfCandidateMaterializer.CreateRouteOnlySemantics(route),
            output,
            this,
            cancellationToken);
    }

    internal Task MaterializeAuthoredAsync(
        string source,
        int level,
        UniversalVtfSemanticDecision semantics,
        string output,
        CancellationToken cancellationToken)
    {
        ThrowIfDisposed();
        return UniversalVtfCandidateMaterializer.MaterializeAuthoredWithSessionAsync(
            source,
            level,
            semantics,
            output,
            this,
            cancellationToken);
    }

    internal Task MaterializeGeneratedAsync(
        string source,
        int level,
        UniversalVtfSemanticDecision semantics,
        string output,
        CancellationToken cancellationToken)
    {
        ThrowIfDisposed();
        return UniversalVtfCandidateMaterializer.MaterializeGeneratedWithSessionAsync(
            source,
            level,
            semantics,
            output,
            this,
            cancellationToken);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        string baseRoot = Path.GetFullPath(Path.Combine(
                Path.GetTempPath(),
                "GmodAddonOptimizer",
                "UniversalVtfEncoding"))
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string root = Path.GetFullPath(_sessionRoot)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        bool directChild = string.Equals(
            Path.GetDirectoryName(root)?.TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar),
            baseRoot,
            StringComparison.OrdinalIgnoreCase);
        bool ownedName = Path.GetFileName(root)
            .StartsWith("session-", StringComparison.Ordinal);
        if (!directChild || !ownedName)
        {
            throw new InvalidOperationException(
                $"Refusing to remove an unowned universal VTF encoding session: {root}");
        }

        if (Directory.Exists(root))
        {
            Directory.Delete(root, recursive: true);
        }
    }

    private void ThrowIfDisposed()
    {
        if (_disposed)
        {
            throw new ObjectDisposedException(nameof(UniversalVtfEncodingSession));
        }
    }
}

internal static class UniversalVtfCandidateMaterializer
{
    internal static UniversalVtfEncodingSession CreateEncodingSession()
    {
        string baseRoot = Path.GetFullPath(Path.Combine(
            Path.GetTempPath(),
            "GmodAddonOptimizer",
            "UniversalVtfEncoding"));
        Directory.CreateDirectory(baseRoot);
        string sessionRoot = Path.Combine(baseRoot, $"session-{Guid.NewGuid():N}");
        Directory.CreateDirectory(sessionRoot);
        try
        {
            string toolchainRoot = Path.Combine(sessionRoot, "toolchain");
            UniversalVtfToolchain.ExtractEmbeddedTo(toolchainRoot);
            UniversalVtfToolchainFingerprint fingerprint =
                UniversalVtfToolchain.Verify(toolchainRoot);
            string workDirectory = Path.Combine(sessionRoot, "work");
            Directory.CreateDirectory(workDirectory);
            return new UniversalVtfEncodingSession(
                sessionRoot,
                fingerprint,
                workDirectory);
        }
        catch
        {
            DeleteFailedSession(baseRoot, sessionRoot);
            throw;
        }
    }

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

    internal static async Task MaterializeGeneratedAsync(
        string source,
        int level,
        UniversalVtfFilterRoute route,
        string output,
        CancellationToken cancellationToken)
    {
        using UniversalVtfEncodingSession session = CreateEncodingSession();
        await session.MaterializeGeneratedAsync(
            source,
            level,
            route,
            output,
            cancellationToken).ConfigureAwait(false);
    }

    internal static async Task MaterializeAuthoredWithSessionAsync(
        string source,
        int level,
        UniversalVtfSemanticDecision semantics,
        string output,
        UniversalVtfEncodingSession session,
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

        ArgumentNullException.ThrowIfNull(semantics);
        ArgumentNullException.ThrowIfNull(session);
        if (semantics.Route == UniversalVtfFilterRoute.Composite)
        {
            throw new InvalidOperationException(
                "Composite VTF semantics cannot regenerate a thumbnail.");
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

        VtfSubresourceRange selectedRange = layout.Subresources.Single(range =>
            range.MipLevel == level && range.Frame == 0 && range.Face == 0 &&
            range.DepthSlice == 0);
        if (!VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                sourcePath,
                layout.Document,
                level,
                frame: 0,
                face: 0,
                depthSlice: 0,
                out byte[] selectedRgba) ||
            selectedRgba.Length != checked(width * height * 4))
        {
            throw new InvalidDataException("authored_thumbnail_source_decode_failed");
        }

        var selectedTop = new UniversalVtfImagePlane(
            selectedRange.Width,
            selectedRange.Height,
            Frame: 0,
            Face: 0,
            DepthSlice: 0,
            selectedRgba);
        (int lowWidth, int lowHeight, byte[]? lowPayload, bool copied) =
            await MaterializeLowResolutionImageAsync(
                sourcePath,
                layout,
                selectedTop,
                semantics,
                new Version(layout.Document.MajorVersion, layout.Document.MinorVersion),
                session,
                cancellationToken).ConfigureAwait(false);
        var request = new VtfContainerWriteRequest(
            sourcePath,
            width,
            height,
            depth,
            mipCount,
            highResolutionPayload,
            lowWidth,
            lowHeight,
            lowPayload,
            RetainedSourceLevel: level,
            LowResolutionPayloadWasCopied: copied);
        await VtfContainerWriter.WriteAsync(
            request,
            Path.GetFullPath(output),
            cancellationToken).ConfigureAwait(false);
    }

    internal static async Task MaterializeGeneratedWithSessionAsync(
        string source,
        int level,
        UniversalVtfSemanticDecision semantics,
        string output,
        UniversalVtfEncodingSession session,
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

        ArgumentNullException.ThrowIfNull(semantics);
        ArgumentNullException.ThrowIfNull(session);
        if (semantics.Route == UniversalVtfFilterRoute.Composite)
        {
            throw new InvalidOperationException(
                "Composite VTF semantics cannot be generated without an explicit route.");
        }

        string sourcePath = Path.GetFullPath(source);
        string destination = Path.GetFullPath(output);
        if (File.Exists(destination) || Directory.Exists(destination))
        {
            throw new IOException($"Candidate output already exists: {destination}");
        }

        VtfContainerLayout layout = VtfContainerLayoutReader.Read(sourcePath);
        if (layout.Document.MipCount != 1 ||
            (layout.Document.Flags & VtfFlags.NoMip) == 0)
        {
            throw new InvalidDataException("generated_materialization_requires_nomip_source");
        }

        if (layout.Document.HighResFormat is not (13 or 15))
        {
            throw new NotSupportedException(
                $"Generated VTF format {layout.Document.HighResFormat} is not approved.");
        }

        IReadOnlyList<int> physicalLevels = UniversalVtfLevels.Enumerate(
            layout.Document.Width,
            layout.Document.Height);
        if (level <= 0 || !physicalLevels.Contains(level))
        {
            throw new ArgumentOutOfRangeException(
                nameof(level),
                level,
                "Generated NOMIP level must be one of the physical reduction levels.");
        }

        cancellationToken.ThrowIfCancellationRequested();
        UniversalVtfDimensions target = UniversalVtfLevels.At(
            layout.Document.Width,
            layout.Document.Height,
            level);
        VtfSubresourceRange[] sourceSubresources = layout.Subresources
            .Where(range => range.MipLevel == 0)
            .OrderBy(range => range.Frame)
            .ThenBy(range => range.Face)
            .ThenBy(range => range.DepthSlice)
            .ToArray();
        int expectedPlanes = checked(
            layout.Document.Frames * layout.Document.Faces * layout.Document.Depth);
        if (sourceSubresources.Length != expectedPlanes ||
            sourceSubresources.Select(range =>
                    (range.Frame, range.Face, range.DepthSlice))
                .Distinct()
                .Count() != expectedPlanes)
        {
            throw new InvalidDataException("nomip_source_subresources_incomplete");
        }

        var encodedPlanes = new List<byte[]>(sourceSubresources.Length);
        UniversalVtfImagePlane? thumbnailSource = null;
        var version = new Version(
            layout.Document.MajorVersion,
            layout.Document.MinorVersion);
        foreach (VtfSubresourceRange sourceSubresource in sourceSubresources)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (!VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                    sourcePath,
                    layout.Document,
                    mipLevel: 0,
                    sourceSubresource.Frame,
                    sourceSubresource.Face,
                    sourceSubresource.DepthSlice,
                    out byte[] rgba) ||
                rgba.Length != checked(
                    layout.Document.Width * layout.Document.Height * 4))
            {
                throw new InvalidDataException(
                    $"raw_nomip_decode_failed:{sourceSubresource.Frame}:" +
                    $"{sourceSubresource.Face}:{sourceSubresource.DepthSlice}");
            }

            var rawPlane = new UniversalVtfImagePlane(
                layout.Document.Width,
                layout.Document.Height,
                sourceSubresource.Frame,
                sourceSubresource.Face,
                sourceSubresource.DepthSlice,
                rgba);
            UniversalVtfImagePlane downsampled = UniversalVtfDownsampler.Downsample(
                rawPlane,
                target,
                semantics.Route,
                semantics);
            VtfEncodedBlockSet encoded = await session.Encoder.EncodeAsync(
                downsampled,
                layout.Document.HighResFormat,
                version,
                session.WorkDirectory,
                cancellationToken).ConfigureAwait(false);
            encodedPlanes.Add(AssertSinglePayload(encoded, downsampled));
            if (sourceSubresource.Frame == 0 &&
                sourceSubresource.Face == 0 &&
                sourceSubresource.DepthSlice == 0)
            {
                thumbnailSource = downsampled;
            }
        }

        if (thumbnailSource is null)
        {
            throw new InvalidDataException("nomip_thumbnail_source_plane_missing");
        }

        byte[] highResolutionPayload = Concatenate(encodedPlanes);
        (int lowWidth, int lowHeight, byte[]? lowPayload, bool copied) =
            await MaterializeLowResolutionImageAsync(
                sourcePath,
                layout,
                thumbnailSource,
                semantics,
                version,
                session,
                cancellationToken).ConfigureAwait(false);
        var request = new VtfContainerWriteRequest(
            sourcePath,
            target.Width,
            target.Height,
            layout.Document.Depth,
            MipCount: 1,
            highResolutionPayload,
            lowWidth,
            lowHeight,
            lowPayload,
            RetainedSourceLevel: null,
            LowResolutionPayloadWasCopied: copied);
        await VtfContainerWriter.WriteAsync(
            request,
            destination,
            cancellationToken).ConfigureAwait(false);
    }

    internal static UniversalVtfSemanticDecision CreateRouteOnlySemantics(
        UniversalVtfFilterRoute route)
    {
        UniversalVtfSemanticClass semanticClass = route switch
        {
            UniversalVtfFilterRoute.SrgbColor => UniversalVtfSemanticClass.Diffuse,
            UniversalVtfFilterRoute.NumericLinear => UniversalVtfSemanticClass.ScalarMask,
            UniversalVtfFilterRoute.RgbNormal => UniversalVtfSemanticClass.Normal,
            UniversalVtfFilterRoute.Dxt5NmAg => UniversalVtfSemanticClass.Normal,
            UniversalVtfFilterRoute.SsbumpScalars => UniversalVtfSemanticClass.Ssbump,
            UniversalVtfFilterRoute.Composite => throw new InvalidOperationException(
                "Composite VTF semantics require a resolved route."),
            _ => throw new ArgumentOutOfRangeException(nameof(route), route, null)
        };
        return new UniversalVtfSemanticDecision(
            new HashSet<UniversalVtfSemanticClass> { semanticClass },
            route,
            HasUnknownConsumer: false,
            AlphaTestReference: 0.5,
            Consumers: Array.Empty<VtfConsumerEvidence>(),
            Uncertainties: Array.Empty<string>());
    }

    private static async Task<(int Width, int Height, byte[]? Payload, bool Copied)>
        MaterializeLowResolutionImageAsync(
            string sourcePath,
            VtfContainerLayout layout,
            UniversalVtfImagePlane selectedTop,
            UniversalVtfSemanticDecision semantics,
            Version sourceVersion,
            UniversalVtfEncodingSession session,
            CancellationToken cancellationToken)
    {
        if (layout.LowResolutionImage is null)
        {
            return (0, 0, null, false);
        }

        if (layout.Document.LowResWidth <= selectedTop.Width &&
            layout.Document.LowResHeight <= selectedTop.Height)
        {
            byte[] copied = await ReadRangesAsync(
                sourcePath,
                new[] { layout.LowResolutionImage },
                cancellationToken).ConfigureAwait(false);
            return (
                layout.Document.LowResWidth,
                layout.Document.LowResHeight,
                copied,
                true);
        }

        if (layout.Document.LowResFormat is not (13 or 15))
        {
            throw new NotSupportedException(
                $"Low-resolution VTF format {layout.Document.LowResFormat} cannot be regenerated.");
        }

        int width = Math.Max(1, Math.Min(
            layout.Document.LowResWidth,
            selectedTop.Width));
        int height = Math.Max(1, Math.Min(
            layout.Document.LowResHeight,
            selectedTop.Height));
        UniversalVtfImagePlane thumbnail = UniversalVtfDownsampler.Downsample(
            selectedTop,
            new UniversalVtfDimensions(width, height),
            semantics.Route,
            semantics);
        VtfEncodedBlockSet encoded = await session.Encoder.EncodeAsync(
            thumbnail,
            layout.Document.LowResFormat,
            sourceVersion,
            session.WorkDirectory,
            cancellationToken).ConfigureAwait(false);
        return (width, height, AssertSinglePayload(encoded, thumbnail), false);
    }

    private static byte[] AssertSinglePayload(
        VtfEncodedBlockSet encoded,
        UniversalVtfImagePlane source)
    {
        if (encoded.Width != source.Width || encoded.Height != source.Height)
        {
            throw new InvalidDataException("encoded_block_dimensions_changed");
        }

        byte[][] payloads = encoded.SubresourcePayloads.ToArray();
        if (payloads.Length != 1 || payloads[0].Length == 0)
        {
            throw new InvalidDataException("encoded_block_payload_is_ambiguous");
        }

        return payloads[0];
    }

    private static byte[] Concatenate(IReadOnlyList<byte[]> payloads)
    {
        long length = payloads.Sum(payload => (long)payload.Length);
        if (length <= 0 || length > int.MaxValue)
        {
            throw new InvalidDataException("generated_payload_size_invalid");
        }

        var result = new byte[checked((int)length)];
        int cursor = 0;
        foreach (byte[] payload in payloads)
        {
            Buffer.BlockCopy(payload, 0, result, cursor, payload.Length);
            cursor = checked(cursor + payload.Length);
        }

        return result;
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

    private static void DeleteFailedSession(string baseRoot, string sessionRoot)
    {
        string expectedParent = Path.GetFullPath(baseRoot)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string root = Path.GetFullPath(sessionRoot)
            .TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        if (!string.Equals(
                Path.GetDirectoryName(root)?.TrimEnd(
                    Path.DirectorySeparatorChar,
                    Path.AltDirectorySeparatorChar),
                expectedParent,
                StringComparison.OrdinalIgnoreCase) ||
            !Path.GetFileName(root).StartsWith("session-", StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                $"Refusing to remove failed unowned encoding session: {root}");
        }

        if (Directory.Exists(root))
        {
            Directory.Delete(root, recursive: true);
        }
    }
}
