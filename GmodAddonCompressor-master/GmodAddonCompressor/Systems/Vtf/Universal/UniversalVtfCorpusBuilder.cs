using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfBuildAnchors(
    UniversalVtfTreeAnchor RawAnchor,
    UniversalVtfTreeAnchor SafeAnchor,
    int ExpectedVtfCount,
    bool RequireFrozenBcPartition);

internal sealed record UniversalVtfPolicyReceipt(
    string PolicyFingerprint,
    string ToolchainSha256);

internal enum UniversalVtfBuildPhase
{
    AfterSourceSnapshots,
    BeforeAudit,
    BeforePublication
}

internal sealed record UniversalVtfBuildObservation(
    IReadOnlyDictionary<UniversalVtfProfileKind, string> ProfileStagingRoots);

internal interface IUniversalVtfBuildObserver
{
    void Observe(
        UniversalVtfBuildPhase phase,
        UniversalVtfBuildObservation observation);
}

internal sealed record UniversalVtfBuildRequest(
    string RawRoot,
    string SafeRoot,
    string OutputParent,
    string ReportRoot,
    string BcBridgePath,
    string BcBridgeSha256,
    string? RuntimeCapsPath,
    string? RuntimeCapsSha256,
    IReadOnlyList<UniversalVtfProfileKind> Profiles,
    string PolicyFingerprint,
    UniversalVtfBuildAnchors? Anchors = null,
    IUniversalVtfBuildObserver? Observer = null);

internal sealed record UniversalVtfBuiltProfile(
    UniversalVtfProfileKind Profile,
    string Root,
    string ManifestPath,
    string ManifestSha256,
    long Bytes,
    string TreeSha256);

internal sealed record UniversalVtfBuildResult(
    UniversalVtfBuiltProfile Conservative,
    UniversalVtfBuiltProfile Balanced,
    UniversalVtfBuiltProfile Aggressive);

internal sealed record UniversalVtfBuildProgress(
    string Stage,
    int Completed,
    int Total,
    string? RelativePath,
    UniversalVtfProfileKind? Profile);

internal static class UniversalVtfCorpusBuilder
{
    private static readonly UniversalVtfTreeAnchor SafeTreeAnchor = new(
        "corpus_bc3_to_bc1_safe",
        FileCount: 3_664,
        TotalBytes: 438_230_044,
        TreeSha256: "8e60b8b46744a192fd5ab1ab77848ab9fd4123f1dd042c31d365e16505a698af");

    private static readonly ConcurrentDictionary<string, UniversalVtfPolicyReceipt>
        EmbeddedPolicyReceipts = new(StringComparer.OrdinalIgnoreCase);

    internal static string ProfileName(UniversalVtfProfileKind profile) => profile switch
    {
        UniversalVtfProfileKind.Conservative => "universal-vtf-conservative",
        UniversalVtfProfileKind.Balanced => "universal-vtf-balanced",
        UniversalVtfProfileKind.Aggressive => "universal-vtf-aggressive",
        _ => throw new ArgumentOutOfRangeException(nameof(profile), profile, null)
    };

    internal static UniversalVtfPolicyReceipt ResolveEmbeddedPolicyReceipt(
        string bcBridgeSha256)
    {
        Bc3RawBridgeStore.RequireSha256(bcBridgeSha256, nameof(bcBridgeSha256));
        return EmbeddedPolicyReceipts.GetOrAdd(
            bcBridgeSha256.ToLowerInvariant(),
            static bridgeSha =>
            {
                using UniversalVtfEncodingSession session =
                    UniversalVtfCandidateMaterializer.CreateEncodingSession();
                return PolicyReceipt(session.Fingerprint, bridgeSha);
            });
    }

    internal static async Task<UniversalVtfBuildResult> BuildAsync(
        UniversalVtfBuildRequest request,
        IProgress<UniversalVtfBuildProgress>? progress,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        ValidateProfiles(request.Profiles);
        ValidateRuntimeCapsPair(request.RuntimeCapsPath, request.RuntimeCapsSha256);
        Bc3RawBridgeStore.RequireSha256(request.BcBridgeSha256, nameof(request.BcBridgeSha256));
        Bc3RawBridgeStore.RequireSha256(request.PolicyFingerprint, nameof(request.PolicyFingerprint));

        string rawRoot = RequireDirectory(request.RawRoot, nameof(request.RawRoot));
        string safeRoot = RequireDirectory(request.SafeRoot, nameof(request.SafeRoot));
        string outputParent = RequireDirectory(request.OutputParent, nameof(request.OutputParent));
        string reportRoot = RequireDirectory(request.ReportRoot, nameof(request.ReportRoot));
        UniversalVtfBuildAnchors anchors = request.Anchors ?? new UniversalVtfBuildAnchors(
            UniversalVtfCorpusExpectations.RawTreeAnchor,
            SafeTreeAnchor,
            UniversalVtfCorpusExpectations.LvsV1.VtfCount,
            RequireFrozenBcPartition: true);

        cancellationToken.ThrowIfCancellationRequested();
        UniversalVtfTreeSnapshot rawBefore = UniversalVtfTreeInventory.Capture(rawRoot);
        UniversalVtfTreeSnapshot safeBefore = UniversalVtfTreeInventory.Capture(safeRoot);
        UniversalVtfTreeInventory.RequireAnchor(rawBefore, anchors.RawAnchor);
        UniversalVtfTreeInventory.RequireAnchor(safeBefore, anchors.SafeAnchor);

        Bc3RawBridge bridge = Bc3RawBridgeStore.LoadAndVerify(
            request.BcBridgePath,
            request.BcBridgeSha256);
        UniversalVtfRuntimeCaps runtimeCaps = request.RuntimeCapsPath is null
            ? UniversalVtfRuntimeCaps.Empty
            : UniversalVtfRuntimeCaps.LoadCanonical(
                request.RuntimeCapsPath,
                request.RuntimeCapsSha256!);
        ValidateBridge(bridge, rawBefore, safeBefore, anchors);

        UniversalVtfInventory rawInventory = UniversalVtfInventoryScanner.Scan(rawRoot);
        if (!rawInventory.IsComplete || rawInventory.Entries.Count != anchors.ExpectedVtfCount)
        {
            throw new InvalidDataException(
                $"raw_vtf_inventory_incomplete:{rawInventory.Entries.Count}/" +
                $"{rawInventory.DiscoveredCount}/{anchors.ExpectedVtfCount}");
        }

        UniversalVtfInventory safeInventory = UniversalVtfInventoryScanner.Scan(safeRoot);
        if (!safeInventory.IsComplete || safeInventory.Entries.Count != anchors.ExpectedVtfCount)
        {
            throw new InvalidDataException(
                $"safe_vtf_inventory_incomplete:{safeInventory.Entries.Count}/" +
                $"{safeInventory.DiscoveredCount}/{anchors.ExpectedVtfCount}");
        }

        RequireSameVtfPathSet(rawInventory, safeInventory);
        var emptyObservation = new UniversalVtfBuildObservation(
            new Dictionary<UniversalVtfProfileKind, string>());
        request.Observer?.Observe(UniversalVtfBuildPhase.AfterSourceSnapshots, emptyObservation);
        RequireSnapshotsUnchanged(rawBefore, rawRoot, "raw");
        RequireSnapshotsUnchanged(safeBefore, safeRoot, "safe");

        foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
        {
            _ = ResolveNewDirectoryDestination(outputParent, ProfileName(profile));
            _ = ResolveNewFileDestination(
                reportRoot,
                ProfileName(profile) + ".manifest.json");
        }

        string recoveryRoot = UniversalVtfTreeInventory.ResolveNewDestination(
            outputParent,
            $".universal-vtf-recovery-{Guid.NewGuid():N}");
        Directory.CreateDirectory(recoveryRoot);

        var stagingRoots = new Dictionary<UniversalVtfProfileKind, string>();
        foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
        {
            string staging = Path.Combine(recoveryRoot, ProfileName(profile) + ".staging");
            Directory.CreateDirectory(staging);
            stagingRoots.Add(profile, staging);
        }

        var observation = new UniversalVtfBuildObservation(stagingRoots);
        string candidateRoot = Path.Combine(recoveryRoot, "raw-derived-candidates");
        Directory.CreateDirectory(candidateRoot);
        string manifestStagingRoot = Path.Combine(recoveryRoot, "manifests");
        Directory.CreateDirectory(manifestStagingRoot);

        using UniversalVtfEncodingSession encodingSession =
            UniversalVtfCandidateMaterializer.CreateEncodingSession();
        UniversalVtfPolicyReceipt policy = PolicyReceipt(
            encodingSession.Fingerprint,
            request.BcBridgeSha256);
        if (!policy.PolicyFingerprint.Equals(
                request.PolicyFingerprint,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException(
                $"policy_fingerprint_mismatch:{policy.PolicyFingerprint}!=" +
                request.PolicyFingerprint.ToLowerInvariant());
        }

        IReadOnlyList<string> frameworkRoots = DiscoverFrameworkRoots(rawRoot);
        VtfConsumerGraph consumerGraph = VtfConsumerGraphBuilder.Build(
            rawRoot,
            safeRoot,
            frameworkRoots);
        Dictionary<string, UniversalVtfTreeFile> safeFiles = safeBefore.Files
            .ToDictionary(file => file.RelativePath, StringComparer.OrdinalIgnoreCase);
        Dictionary<string, UniversalVtfInventoryEntry> safeVtfs = safeInventory.Entries
            .ToDictionary(entry => entry.RelativePath, StringComparer.OrdinalIgnoreCase);
        Dictionary<string, Bc3RawBridgeRow> bridgeRows = bridge.Rows
            .ToDictionary(row => row.RelativePath, StringComparer.OrdinalIgnoreCase);

        var prepared = new List<PreparedAsset>(rawInventory.Entries.Count);
        int preparedCount = 0;
        foreach (UniversalVtfInventoryEntry rawEntry in rawInventory.Entries)
        {
            cancellationToken.ThrowIfCancellationRequested();
            string canonicalPath = rawEntry.RelativePath.ToLowerInvariant();
            if (!safeFiles.TryGetValue(rawEntry.RelativePath, out UniversalVtfTreeFile? safeFile) ||
                !safeVtfs.ContainsKey(rawEntry.RelativePath))
            {
                throw new InvalidDataException($"safe_vtf_missing:{canonicalPath}");
            }

            UniversalVtfSemanticDecision semantics = UniversalVtfSemanticResolver.Resolve(
                rawEntry,
                consumerGraph);
            string source = CombineAuthority(rawRoot, rawEntry.RelativePath);
            string assetRoot = Path.Combine(candidateRoot, preparedCount.ToString("D4"));
            Directory.CreateDirectory(assetRoot);
            IReadOnlyList<UniversalVtfCandidateArtifact> candidates =
                await MaterializeCandidatesAsync(
                    source,
                    rawEntry,
                    semantics,
                    assetRoot,
                    encodingSession,
                    cancellationToken).ConfigureAwait(false);
            UniversalVtfAttemptMatrix matrix = UniversalVtfSelector.BuildAttempts(
                rawEntry,
                candidates,
                semantics);
            UniversalVtfProfileSelection selection = UniversalVtfSelector.Select(
                matrix,
                runtimeCaps);
            prepared.Add(new PreparedAsset(
                rawEntry,
                safeFile,
                semantics,
                matrix,
                selection,
                bridgeRows.GetValueOrDefault(canonicalPath)));
            preparedCount++;
            progress?.Report(new UniversalVtfBuildProgress(
                "prepare",
                preparedCount,
                rawInventory.Entries.Count,
                canonicalPath,
                null));
        }

        foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
        {
            CopyTreePhysical(safeBefore, safeRoot, stagingRoots[profile], cancellationToken);
        }

        var frozenManifests = new Dictionary<
            UniversalVtfProfileKind,
            UniversalVtfManifestFrozen>();
        var finalTrees = new Dictionary<UniversalVtfProfileKind, UniversalVtfTreeSnapshot>();
        foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
        {
            cancellationToken.ThrowIfCancellationRequested();
            var rows = new List<UniversalVtfManifestRow>(prepared.Count);
            int completed = 0;
            foreach (PreparedAsset asset in prepared)
            {
                cancellationToken.ThrowIfCancellationRequested();
                UniversalVtfSelectedLevel selected = Selected(asset.Selection, profile);
                string destination = CombineAuthority(
                    stagingRoots[profile],
                    asset.SafeFile.RelativePath);
                PrepareForReplacement(destination);
                string spatialPath = selected.CandidatePath;
                string finalSource = spatialPath;
                if (asset.BridgeRow?.EligibilityMode is
                    Bc3EligibilityMode.PhysicalOpaque or
                    Bc3EligibilityMode.SemanticDetailRgbOnly)
                {
                    ValidateBridgeSelection(asset, selected.Level);
                    string bcOutput = Path.Combine(
                        candidateRoot,
                        $"bc1-{(int)profile}-{completed:D4}.vtf");
                    await MaterializeOpaqueBc1Async(
                        spatialPath,
                        bcOutput,
                        cancellationToken).ConfigureAwait(false);
                    finalSource = bcOutput;
                }

                File.Copy(finalSource, destination, overwrite: true);
                rows.Add(CreateManifestRow(
                    asset,
                    profile,
                    selected,
                    spatialPath,
                    destination,
                    request.BcBridgeSha256));
                completed++;
                progress?.Report(new UniversalVtfBuildProgress(
                    "compose",
                    completed,
                    prepared.Count,
                    asset.Raw.RelativePath,
                    profile));
            }

            UniversalVtfTreeSnapshot finalTree = UniversalVtfTreeInventory.Capture(
                stagingRoots[profile]);
            finalTrees.Add(profile, finalTree);
            string manifestPath = Path.Combine(
                manifestStagingRoot,
                ProfileName(profile) + ".manifest.json");
            UniversalVtfBuildReceipt receipt = new(
                profile,
                policy.PolicyFingerprint,
                UniversalVtfPolicy.V1.FixtureSha256,
                policy.ToolchainSha256,
                request.BcBridgeSha256,
                request.RuntimeCapsSha256,
                ManifestTree("raw", rawBefore),
                ManifestTree("safe", safeBefore),
                ManifestTree("final", finalTree),
                rows.OrderBy(row => row.RelativePath, StringComparer.Ordinal).ToArray());
            frozenManifests.Add(
                profile,
                await UniversalVtfManifestWriter.WriteAsync(
                    manifestPath,
                    receipt,
                    cancellationToken).ConfigureAwait(false));
        }

        RequireSnapshotsUnchanged(rawBefore, rawRoot, "raw");
        RequireSnapshotsUnchanged(safeBefore, safeRoot, "safe");
        request.Observer?.Observe(UniversalVtfBuildPhase.BeforeAudit, observation);
        AuditAll(
            request,
            anchors,
            rawBefore,
            safeBefore,
            stagingRoots,
            frozenManifests);

        request.Observer?.Observe(UniversalVtfBuildPhase.BeforePublication, observation);
        RequireSnapshotsUnchanged(rawBefore, rawRoot, "raw");
        RequireSnapshotsUnchanged(safeBefore, safeRoot, "safe");
        AuditAll(
            request,
            anchors,
            rawBefore,
            safeBefore,
            stagingRoots,
            frozenManifests);
        cancellationToken.ThrowIfCancellationRequested();

        var publishedDirectories = new List<(string Final, string Staging)>();
        var publishedManifests = new List<(string Final, string Staging)>();
        try
        {
            foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
            {
                string final = Path.Combine(outputParent, ProfileName(profile));
                EnsureAbsent(final);
                Directory.Move(stagingRoots[profile], final);
                publishedDirectories.Add((final, stagingRoots[profile]));
            }

            foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
            {
                string final = Path.Combine(
                    reportRoot,
                    ProfileName(profile) + ".manifest.json");
                EnsureAbsent(final);
                string staging = frozenManifests[profile].Path;
                File.Move(staging, final, overwrite: false);
                publishedManifests.Add((final, staging));
            }
        }
        catch
        {
            RollBackPublications(publishedManifests, publishedDirectories);
            throw;
        }

        UniversalVtfBuildResult result = new(
            Built(UniversalVtfProfileKind.Conservative),
            Built(UniversalVtfProfileKind.Balanced),
            Built(UniversalVtfProfileKind.Aggressive));
        TryDeleteOwnedRecovery(outputParent, recoveryRoot);
        return result;

        UniversalVtfBuiltProfile Built(UniversalVtfProfileKind profile)
        {
            UniversalVtfTreeSnapshot tree = finalTrees[profile];
            return new UniversalVtfBuiltProfile(
                profile,
                Path.Combine(outputParent, ProfileName(profile)),
                Path.Combine(reportRoot, ProfileName(profile) + ".manifest.json"),
                frozenManifests[profile].Sha256,
                tree.TotalBytes,
                tree.TreeSha256);
        }
    }

    private static async Task<IReadOnlyList<UniversalVtfCandidateArtifact>>
        MaterializeCandidatesAsync(
            string source,
            UniversalVtfInventoryEntry entry,
            UniversalVtfSemanticDecision semantics,
            string assetRoot,
            UniversalVtfEncodingSession session,
            CancellationToken cancellationToken)
    {
        IReadOnlyList<int> levels = entry.MipCount > 1
            ? Enumerable.Range(1, entry.MipCount - 1).ToArray()
            : UniversalVtfLevels.Enumerate(entry.Width, entry.Height);
        if (levels.Count == 0)
        {
            throw new InvalidDataException($"dimensionally_irreducible:{entry.RelativePath}");
        }

        UniversalVtfFilterRoute[] routes = semantics.Route == UniversalVtfFilterRoute.Composite
            ? new[] { UniversalVtfFilterRoute.SrgbColor, UniversalVtfFilterRoute.NumericLinear }
            : new[] { semantics.Route };
        var artifacts = new List<UniversalVtfCandidateArtifact>(levels.Count * routes.Length);
        foreach (int level in levels)
        foreach (UniversalVtfFilterRoute route in routes)
        {
            cancellationToken.ThrowIfCancellationRequested();
            string output = Path.Combine(assetRoot, $"level-{level:D2}-{route}.vtf");
            UniversalVtfSemanticDecision routed = semantics with { Route = route };
            try
            {
                if (entry.MipCount > 1)
                {
                    await session.MaterializeAuthoredAsync(
                        source,
                        level,
                        routed,
                        output,
                        cancellationToken).ConfigureAwait(false);
                }
                else
                {
                    await session.MaterializeGeneratedAsync(
                        source,
                        level,
                        routed,
                        output,
                        cancellationToken).ConfigureAwait(false);
                }
            }
            catch (Exception error) when (
                level > 1 && error is IOException or InvalidDataException or
                InvalidOperationException or NotSupportedException or ArgumentException or
                OverflowException)
            {
                // A missing path becomes a structural failure in the shared attempt matrix.
            }

            artifacts.Add(new UniversalVtfCandidateArtifact(level, source, output, route));
        }

        return artifacts.AsReadOnly();
    }

    private static UniversalVtfManifestRow CreateManifestRow(
        PreparedAsset asset,
        UniversalVtfProfileKind profile,
        UniversalVtfSelectedLevel selected,
        string spatialPath,
        string finalPath,
        string bridgeSha256)
    {
        VtfContainerLayout spatial = VtfContainerLayoutReader.Read(spatialPath);
        VtfContainerLayout final = VtfContainerLayoutReader.Read(finalPath);
        UniversalVtfProfileAttempt? selectedAttempt = asset.Selection.Attempts[profile]
            .SingleOrDefault(attempt => attempt.Level == selected.Level);
        IReadOnlyList<string> classes = asset.Semantics.Classes
            .Select(value => ToCamelCase(value.ToString()))
            .OrderBy(value => value, StringComparer.Ordinal)
            .ToArray();
        bool lrsRegenerated = LowResolutionChanged(asset.Raw, spatial);
        return new UniversalVtfManifestRow(
            asset.Raw.RelativePath.ToLowerInvariant(),
            Family(asset.Raw.RelativePath),
            classes,
            ConsumerProof(asset.Semantics),
            asset.Raw.Width,
            asset.Raw.Height,
            asset.Raw.Depth,
            asset.Raw.Frames,
            asset.Raw.Faces,
            asset.Raw.MipCount,
            asset.Raw.HighResFormat,
            final.Document.Width,
            final.Document.Height,
            final.Document.Depth,
            final.Document.MipCount,
            final.Document.HighResFormat,
            Authored: asset.Raw.MipCount > 1,
            Generated: asset.Raw.MipCount == 1,
            selected.Level,
            asset.Selection.Conservative.Level,
            asset.Selection.Balanced.Level,
            asset.Selection.Aggressive.Level,
            selected.Route,
            asset.Selection.Attempts,
            asset.Matrix.Attempts.SelectMany(attempt => attempt.RouteAttempts.Select(route =>
                    new UniversalVtfManifestRouteAttempt(
                        attempt.Level,
                        route.Route,
                        route.StructurallyValid,
                        route.StructuralReason,
                        route.Metrics)))
                .ToArray(),
            selectedAttempt?.Evaluation?.WorstGate,
            asset.Raw.Bytes,
            asset.Raw.Sha256,
            asset.SafeFile.Length,
            asset.SafeFile.Sha256,
            spatial.Document.FileLength,
            spatial.FileSha256,
            final.Document.FileLength,
            final.FileSha256,
            MandatoryFloorApplied: true,
            ByteFloor: spatial.Document.FileLength == asset.Raw.Bytes,
            Blocker: selected.Reason == "forced_floor_quality_deficit"
                ? selected.Reason
                : null,
            asset.BridgeRow?.EligibilityMode,
            asset.BridgeRow is null ? null : bridgeSha256,
            lrsRegenerated,
            final.Resources.Count);
    }

    private static bool LowResolutionChanged(
        UniversalVtfInventoryEntry raw,
        VtfContainerLayout spatial)
    {
        if (raw.LowResWidth == 0 || raw.LowResHeight == 0)
        {
            return false;
        }

        return spatial.Document.LowResWidth != raw.LowResWidth ||
               spatial.Document.LowResHeight != raw.LowResHeight;
    }

    private static async Task MaterializeOpaqueBc1Async(
        string spatialPath,
        string output,
        CancellationToken cancellationToken)
    {
        VtfContainerLayout layout = VtfContainerLayoutReader.Read(spatialPath);
        if (layout.Document.HighResFormat != 15)
        {
            throw new InvalidDataException(
                $"bc3_bridge_selected_non_bc3:{layout.Document.HighResFormat}");
        }

        byte[] source = await File.ReadAllBytesAsync(spatialPath, cancellationToken)
            .ConfigureAwait(false);
        byte[] bc3 = ConcatenateRanges(source, layout.Subresources
            .OrderBy(range => range.Range.Offset)
            .Select(range => range.Range));
        byte[] bc1 = Bc3ColorBlockTranscoder.TranscodePayload(bc3);
        byte[]? low = layout.LowResolutionImage is null
            ? null
            : Slice(source, layout.LowResolutionImage);
        var write = new VtfContainerWriteRequest(
            spatialPath,
            layout.Document.Width,
            layout.Document.Height,
            layout.Document.Depth,
            layout.Document.MipCount,
            bc1,
            layout.Document.LowResWidth,
            layout.Document.LowResHeight,
            low,
            RetainedSourceLevel: null,
            LowResolutionPayloadWasCopied: low is not null,
            HighResolutionFormat: 13);
        await VtfContainerWriter.WriteAsync(write, output, cancellationToken)
            .ConfigureAwait(false);
    }

    private static byte[] ConcatenateRanges(
        byte[] source,
        IEnumerable<VtfByteRange> requested)
    {
        VtfByteRange[] ranges = requested.ToArray();
        long total = ranges.Sum(range => (long)range.Length);
        if (total <= 0 || total > int.MaxValue)
        {
            throw new InvalidDataException("bc3_selected_payload_size_invalid");
        }

        var result = new byte[checked((int)total)];
        int cursor = 0;
        foreach (VtfByteRange range in ranges)
        {
            Buffer.BlockCopy(source, checked((int)range.Offset), result, cursor, range.Length);
            cursor = checked(cursor + range.Length);
        }

        return result;
    }

    private static byte[] Slice(byte[] source, VtfByteRange range)
    {
        var result = new byte[range.Length];
        Buffer.BlockCopy(source, checked((int)range.Offset), result, 0, range.Length);
        return result;
    }

    private static void ValidateBridgeSelection(PreparedAsset asset, int level)
    {
        Bc3RawBridgeRow bridge = asset.BridgeRow
            ?? throw new InvalidDataException("bc3_bridge_row_missing");
        if (bridge.RawBytes != asset.Raw.Bytes ||
            !bridge.RawSha256.Equals(asset.Raw.Sha256, StringComparison.OrdinalIgnoreCase) ||
            bridge.RawFormat != asset.Raw.HighResFormat)
        {
            throw new InvalidDataException($"bc3_bridge_raw_drift:{asset.Raw.RelativePath}");
        }

        Bc3RawMipReceipt mip = bridge.RawMips.SingleOrDefault(item => item.Level == level)
            ?? throw new InvalidDataException(
                $"bc3_bridge_selected_level_missing:{asset.Raw.RelativePath}:{level}");
        UniversalVtfDimensions expected = UniversalVtfLevels.At(
            asset.Raw.Width,
            asset.Raw.Height,
            level);
        if (mip.Width != expected.Width || mip.Height != expected.Height)
        {
            throw new InvalidDataException(
                $"bc3_bridge_selected_geometry:{asset.Raw.RelativePath}:{level}");
        }
    }

    private static void AuditAll(
        UniversalVtfBuildRequest request,
        UniversalVtfBuildAnchors anchors,
        UniversalVtfTreeSnapshot raw,
        UniversalVtfTreeSnapshot safe,
        IReadOnlyDictionary<UniversalVtfProfileKind, string> stagingRoots,
        IReadOnlyDictionary<UniversalVtfProfileKind, UniversalVtfManifestFrozen> manifests)
    {
        var issues = new List<string>();
        foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
        {
            UniversalVtfAuditResult audit = UniversalVtfCorpusAuditor.Audit(
                new UniversalVtfAuditRequest(
                    request.RawRoot,
                    request.SafeRoot,
                    stagingRoots[profile],
                    manifests[profile].Path,
                    manifests[profile].Sha256,
                    request.BcBridgePath,
                    request.BcBridgeSha256,
                    anchors,
                    request.RuntimeCapsPath,
                    request.RuntimeCapsSha256,
                    request.PolicyFingerprint,
                    raw,
                    safe));
            issues.AddRange(audit.Issues.Select(issue => $"{profile}:{issue}"));
        }

        if (issues.Count > 0)
        {
            throw new InvalidDataException("universal_vtf_audit_dirty:" + string.Join(';', issues));
        }
    }

    private static UniversalVtfManifestTree ManifestTree(
        string authority,
        UniversalVtfTreeSnapshot snapshot) =>
        new(authority, snapshot.Files.Count, snapshot.TotalBytes, snapshot.TreeSha256);

    private static UniversalVtfSelectedLevel Selected(
        UniversalVtfProfileSelection selection,
        UniversalVtfProfileKind profile) => profile switch
    {
        UniversalVtfProfileKind.Conservative => selection.Conservative,
        UniversalVtfProfileKind.Balanced => selection.Balanced,
        UniversalVtfProfileKind.Aggressive => selection.Aggressive,
        _ => throw new ArgumentOutOfRangeException(nameof(profile), profile, null)
    };

    private static void ValidateBridge(
        Bc3RawBridge bridge,
        UniversalVtfTreeSnapshot raw,
        UniversalVtfTreeSnapshot safe,
        UniversalVtfBuildAnchors anchors)
    {
        if (!bridge.RawTreeSha256.Equals(raw.TreeSha256, StringComparison.OrdinalIgnoreCase) ||
            bridge.RawTreeBytes != raw.TotalBytes ||
            bridge.RawTreeFileCount != raw.Files.Count ||
            !bridge.ApprovedSafeTreeSha256.Equals(
                safe.TreeSha256,
                StringComparison.OrdinalIgnoreCase) ||
            bridge.ApprovedSafeTreeBytes != safe.TotalBytes ||
            bridge.ApprovedSafeTreeFileCount != safe.Files.Count)
        {
            throw new InvalidDataException("bc3_bridge_authority_header_mismatch");
        }

        if (anchors.RequireFrozenBcPartition &&
            (bridge.Rows.Count != 193 || bridge.PhysicalOpaqueBc3Count != 155 ||
             bridge.RawDxt1Count != 32 || bridge.SemanticExceptionBc3Count != 6 ||
             bridge.EquivalentPriorSafeColorChainCount != 0 ||
             !bridge.PriorManifestSha256.Equals(
                 Bc3ApprovedReceipt.RequiredPriorManifestSha256,
                 StringComparison.Ordinal)))
        {
            throw new InvalidDataException("bc3_bridge_frozen_partition_mismatch");
        }
    }

    private static UniversalVtfPolicyReceipt PolicyReceipt(
        UniversalVtfToolchainFingerprint fingerprint,
        string bridgeSha256)
    {
        UniversalVtfToolBinary managed = FindBinary(
            fingerprint,
            "magick/Magick.NET-Q16-AnyCPU.dll");
        UniversalVtfToolBinary vtfCmd = FindBinary(fingerprint, "VTFEdit/VTFCmd.exe");
        UniversalVtfToolBinary archive = FindBinary(fingerprint, "VTFEdit.zip");
        var native = fingerprint.Binaries
            .Where(binary => binary.Name.StartsWith(
                "magick/native/",
                StringComparison.OrdinalIgnoreCase))
            .ToDictionary(binary => binary.Name, binary => binary.Sha256, StringComparer.Ordinal);
        var toolchain = new UniversalVtfToolchain(
            managed.Version,
            managed.Bytes,
            managed.Sha256,
            native,
            "LanczosSharp",
            "bilinear-domain-aware-v1",
            vtfCmd.Version,
            vtfCmd.Bytes,
            vtfCmd.Sha256,
            archive.Sha256,
            new[]
            {
                "-file", "<input.png>", "-output", "<isolated-dir>",
                "-format", "<DXT1|DXT5>", "-alphaformat", "<same>",
                "-version", "<source-version-clamped-to-7.4>"
            });
        return new UniversalVtfPolicyReceipt(
            UniversalVtfPolicy.V1.ComputeFingerprint(toolchain, bridgeSha256),
            fingerprint.Sha256);
    }

    private static UniversalVtfToolBinary FindBinary(
        UniversalVtfToolchainFingerprint fingerprint,
        string name) => fingerprint.Binaries.Single(binary =>
        binary.Name.Equals(name, StringComparison.OrdinalIgnoreCase));

    private static string ConsumerProof(UniversalVtfSemanticDecision semantics)
    {
        var lines = new List<string>
        {
            $"route={semantics.Route}",
            $"unknown={semantics.HasUnknownConsumer}",
            $"alpha={semantics.AlphaTestReference:R}"
        };
        lines.AddRange(semantics.Classes.OrderBy(value => value).Select(value => $"class={value}"));
        foreach (VtfConsumerEvidence consumer in semantics.Consumers
                     .OrderBy(value => value.ConsumerPath, StringComparer.Ordinal)
                     .ThenBy(value => value.Key, StringComparer.Ordinal))
        {
            lines.Add(
                $"consumer={consumer.ConsumerPath}|{consumer.Shader}|{consumer.Key}|" +
                $"{consumer.TextureKey}|{consumer.Dynamic}|{consumer.ResolutionTrace}|" +
                string.Join(',', consumer.ReadChannels.OrderBy(value => value)));
            foreach ((string key, IReadOnlyList<string> values) in consumer.Parameters
                         .OrderBy(pair => pair.Key, StringComparer.OrdinalIgnoreCase))
            {
                lines.Add($"parameter={key.ToLowerInvariant()}={string.Join('|', values)}");
            }
        }

        lines.AddRange(semantics.Uncertainties
            .OrderBy(value => value, StringComparer.Ordinal)
            .Select(value => $"uncertainty={value}"));
        return Convert.ToHexString(SHA256.HashData(
                Encoding.UTF8.GetBytes(string.Join('\n', lines))))
            .ToLowerInvariant();
    }

    private static string Family(string relativePath)
    {
        string[] segments = relativePath.ToLowerInvariant().Split('/');
        if (segments.Length >= 4 && segments[0] == "materials" &&
            segments[1] == "models" && segments[2] == "diggercars")
        {
            return segments[3];
        }

        string directory = Path.GetDirectoryName(relativePath)?.Replace('\\', '/').ToLowerInvariant()
            ?? "root";
        return directory.Length == 0 ? "root" : directory;
    }

    private static string ToCamelCase(string value) =>
        value.Length == 0 ? value : char.ToLowerInvariant(value[0]) + value[1..];

    private static void RequireSameVtfPathSet(
        UniversalVtfInventory raw,
        UniversalVtfInventory safe)
    {
        string[] rawPaths = raw.Entries.Select(entry => entry.RelativePath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        string[] safePaths = safe.Entries.Select(entry => entry.RelativePath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        if (!rawPaths.SequenceEqual(safePaths, StringComparer.OrdinalIgnoreCase))
        {
            throw new InvalidDataException("raw_safe_vtf_path_set_mismatch");
        }
    }

    private static IReadOnlyList<string> DiscoverFrameworkRoots(string rawRoot)
    {
        string? parent = Path.GetDirectoryName(rawRoot);
        if (parent is null)
        {
            return Array.Empty<string>();
        }

        string candidate = Path.Combine(parent, "lvs_framework");
        return Directory.Exists(candidate) ? new[] { candidate } : Array.Empty<string>();
    }

    private static void CopyTreePhysical(
        UniversalVtfTreeSnapshot snapshot,
        string sourceRoot,
        string destinationRoot,
        CancellationToken cancellationToken)
    {
        foreach (UniversalVtfTreeFile file in snapshot.Files)
        {
            cancellationToken.ThrowIfCancellationRequested();
            string source = CombineAuthority(sourceRoot, file.RelativePath);
            string destination = CombineAuthority(destinationRoot, file.RelativePath);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.Copy(source, destination, overwrite: false);
        }
    }

    private static void PrepareForReplacement(string path)
    {
        if (!File.Exists(path))
        {
            throw new FileNotFoundException("Safe-tree VTF missing from staging.", path);
        }

        File.SetAttributes(path, FileAttributes.Normal);
    }

    private static string CombineAuthority(string root, string relativePath)
    {
        string authority = Path.GetFullPath(root).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string combined = Path.GetFullPath(Path.Combine(
            authority,
            relativePath.Replace('/', Path.DirectorySeparatorChar)));
        if (!combined.StartsWith(authority + Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"authority_path_escape:{relativePath}");
        }

        return combined;
    }

    private static void RequireSnapshotsUnchanged(
        UniversalVtfTreeSnapshot before,
        string root,
        string authority)
    {
        UniversalVtfTreeSnapshot after = UniversalVtfTreeInventory.Capture(root);
        if (before.Files.Count != after.Files.Count || before.TotalBytes != after.TotalBytes ||
            !before.TreeSha256.Equals(after.TreeSha256, StringComparison.OrdinalIgnoreCase) ||
            !before.Files.Zip(after.Files).All(pair =>
                pair.First.RelativePath.Equals(pair.Second.RelativePath, StringComparison.Ordinal) &&
                pair.First.LastWriteTimeUtc == pair.Second.LastWriteTimeUtc))
        {
            throw new InvalidDataException($"source_tree_changed:{authority}");
        }
    }

    private static void ValidateProfiles(IReadOnlyList<UniversalVtfProfileKind> profiles)
    {
        ArgumentNullException.ThrowIfNull(profiles);
        UniversalVtfProfileKind[] expected = Enum.GetValues<UniversalVtfProfileKind>();
        if (profiles.Count != expected.Length ||
            !profiles.OrderBy(value => value).SequenceEqual(expected))
        {
            throw new InvalidDataException("build_requires_all_three_profiles");
        }
    }

    private static void ValidateRuntimeCapsPair(string? path, string? sha256)
    {
        if ((path is null) != (sha256 is null))
        {
            throw new InvalidDataException("runtime_caps_path_hash_pair_required");
        }

        if (sha256 is not null)
        {
            Bc3RawBridgeStore.RequireSha256(sha256, nameof(sha256));
        }
    }

    private static string ResolveNewDirectoryDestination(string parent, string name) =>
        UniversalVtfTreeInventory.ResolveNewDestination(parent, name);

    private static string ResolveNewFileDestination(string parent, string name)
    {
        string fullParent = RequireDirectory(parent, nameof(parent));
        if (Path.IsPathRooted(name) || Path.GetFileName(name) != name)
        {
            throw new InvalidDataException($"invalid_destination_name:{name}");
        }

        string destination = Path.Combine(fullParent, name);
        if (Directory.EnumerateFileSystemEntries(fullParent)
            .Any(path => Path.GetFileName(path).Equals(name, StringComparison.OrdinalIgnoreCase)))
        {
            throw new IOException($"destination_exists:{destination}");
        }

        return destination;
    }

    private static void EnsureAbsent(string path)
    {
        if (File.Exists(path) || Directory.Exists(path))
        {
            throw new IOException($"publication_collision:{path}");
        }
    }

    private static void RollBackPublications(
        IEnumerable<(string Final, string Staging)> manifests,
        IEnumerable<(string Final, string Staging)> directories)
    {
        foreach ((string final, string staging) in manifests.Reverse())
        {
            if (File.Exists(final) && !File.Exists(staging))
            {
                File.Move(final, staging, overwrite: false);
            }
        }

        foreach ((string final, string staging) in directories.Reverse())
        {
            if (Directory.Exists(final) && !Directory.Exists(staging))
            {
                Directory.Move(final, staging);
            }
        }
    }

    private static void TryDeleteOwnedRecovery(string parent, string recovery)
    {
        string fullParent = Path.GetFullPath(parent).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string fullRecovery = Path.GetFullPath(recovery).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        if (!string.Equals(
                Path.GetDirectoryName(fullRecovery)?.TrimEnd(
                    Path.DirectorySeparatorChar,
                    Path.AltDirectorySeparatorChar),
                fullParent,
                StringComparison.OrdinalIgnoreCase) ||
            !Path.GetFileName(fullRecovery).StartsWith(
                ".universal-vtf-recovery-",
                StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                $"Refusing to remove unowned recovery directory: {fullRecovery}");
        }

        if (!Directory.Exists(fullRecovery))
        {
            return;
        }

        try
        {
            Directory.Delete(fullRecovery, recursive: true);
        }
        catch (IOException)
        {
            // Publication is already complete. A recoverable scratch directory
            // must not turn a successful all-profile rename into a false failure.
        }
        catch (UnauthorizedAccessException)
        {
            // Same as above; the owned recovery path remains available for cleanup.
        }
    }

    private static string RequireDirectory(string path, string parameterName)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new ArgumentException("Directory path cannot be empty.", parameterName);
        }

        string full = Path.GetFullPath(path);
        if (!Directory.Exists(full))
        {
            throw new DirectoryNotFoundException(full);
        }

        return full;
    }

    private sealed record PreparedAsset(
        UniversalVtfInventoryEntry Raw,
        UniversalVtfTreeFile SafeFile,
        UniversalVtfSemanticDecision Semantics,
        UniversalVtfAttemptMatrix Matrix,
        UniversalVtfProfileSelection Selection,
        Bc3RawBridgeRow? BridgeRow);
}
