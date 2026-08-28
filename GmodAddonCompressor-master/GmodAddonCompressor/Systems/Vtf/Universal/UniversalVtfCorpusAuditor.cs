namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfAuditRequest(
    string RawRoot,
    string SafeRoot,
    string CandidateRoot,
    string ManifestPath,
    string ManifestSha256,
    string BcBridgePath,
    string BcBridgeSha256,
    UniversalVtfBuildAnchors Anchors,
    string? RuntimeCapsPath = null,
    string? RuntimeCapsSha256 = null,
    string? PolicyFingerprint = null,
    UniversalVtfTreeSnapshot? RawSnapshot = null,
    UniversalVtfTreeSnapshot? SafeSnapshot = null);

internal sealed record UniversalVtfAuditResult(
    bool Clean,
    IReadOnlyList<string> Issues);

internal static class UniversalVtfCorpusAuditor
{
    internal static UniversalVtfAuditResult Audit(UniversalVtfAuditRequest request)
    {
        ArgumentNullException.ThrowIfNull(request);
        var issues = new List<string>();
        try
        {
            UniversalVtfTreeSnapshot raw = request.RawSnapshot ??
                UniversalVtfTreeInventory.Capture(request.RawRoot);
            UniversalVtfTreeSnapshot safe = request.SafeSnapshot ??
                UniversalVtfTreeInventory.Capture(request.SafeRoot);
            UniversalVtfTreeSnapshot candidate = UniversalVtfTreeInventory.Capture(
                request.CandidateRoot);
            CheckAnchor(issues, raw, request.Anchors.RawAnchor, "raw");
            CheckAnchor(issues, safe, request.Anchors.SafeAnchor, "safe");

            Bc3RawBridge bridge = Bc3RawBridgeStore.LoadAndVerify(
                request.BcBridgePath,
                request.BcBridgeSha256);
            CheckBridge(issues, bridge, raw, safe, request.Anchors);
            UniversalVtfManifest manifest = UniversalVtfManifestStore.Load(
                request.ManifestPath,
                request.ManifestSha256);
            if (request.PolicyFingerprint is not null &&
                !manifest.PolicyFingerprint.Equals(
                    request.PolicyFingerprint,
                    StringComparison.OrdinalIgnoreCase))
            {
                issues.Add("policy_fingerprint_mismatch");
            }

            if (!manifest.BcBridgeSha256.Equals(
                    request.BcBridgeSha256,
                    StringComparison.OrdinalIgnoreCase))
            {
                issues.Add("bc3_bridge_manifest_hash_mismatch");
            }

            UniversalVtfRuntimeCaps caps = UniversalVtfRuntimeCaps.Empty;
            if ((request.RuntimeCapsPath is null) != (request.RuntimeCapsSha256 is null))
            {
                issues.Add("runtime_caps_path_hash_pair");
            }
            else if (request.RuntimeCapsPath is not null)
            {
                caps = UniversalVtfRuntimeCaps.LoadCanonical(
                    request.RuntimeCapsPath,
                    request.RuntimeCapsSha256!);
                if (!string.Equals(
                        manifest.RuntimeCapsSha256,
                        request.RuntimeCapsSha256,
                        StringComparison.OrdinalIgnoreCase))
                {
                    issues.Add("runtime_caps_manifest_hash_mismatch");
                }
            }
            else if (manifest.RuntimeCapsSha256 is not null)
            {
                issues.Add("runtime_caps_unverified_manifest_input");
            }

            CheckManifestTree(issues, manifest.RawTree, raw, "raw");
            CheckManifestTree(issues, manifest.SafeTree, safe, "safe");
            CheckManifestTree(issues, manifest.FinalTree, candidate, "final");
            CheckPathSets(issues, raw, safe, candidate, manifest);
            CheckFiles(issues, request, raw, safe, candidate, manifest, bridge, caps);
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or
                                      InvalidDataException or ArgumentException or
                                      InvalidOperationException or OverflowException)
        {
            issues.Add($"audit_exception:{error.GetType().Name}:{error.Message}");
        }

        return new UniversalVtfAuditResult(issues.Count == 0, issues.AsReadOnly());
    }

    private static void CheckFiles(
        ICollection<string> issues,
        UniversalVtfAuditRequest request,
        UniversalVtfTreeSnapshot raw,
        UniversalVtfTreeSnapshot safe,
        UniversalVtfTreeSnapshot candidate,
        UniversalVtfManifest manifest,
        Bc3RawBridge bridge,
        UniversalVtfRuntimeCaps caps)
    {
        Dictionary<string, UniversalVtfTreeFile> rawFiles = raw.Files
            .ToDictionary(file => file.RelativePath, StringComparer.OrdinalIgnoreCase);
        Dictionary<string, UniversalVtfTreeFile> safeFiles = safe.Files
            .ToDictionary(file => file.RelativePath, StringComparer.OrdinalIgnoreCase);
        Dictionary<string, UniversalVtfTreeFile> candidateFiles = candidate.Files
            .ToDictionary(file => file.RelativePath, StringComparer.OrdinalIgnoreCase);
        Dictionary<string, Bc3RawBridgeRow> bridgeRows = bridge.Rows
            .ToDictionary(row => row.RelativePath, StringComparer.OrdinalIgnoreCase);
        var manifestPaths = new HashSet<string>(
            manifest.Rows.Select(row => row.RelativePath),
            StringComparer.OrdinalIgnoreCase);

        foreach (UniversalVtfTreeFile safeFile in safe.Files)
        {
            if (!candidateFiles.TryGetValue(safeFile.RelativePath, out UniversalVtfTreeFile? final))
            {
                issues.Add($"candidate_missing:{safeFile.RelativePath}");
                continue;
            }

            if (!safeFile.RelativePath.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase) &&
                (safeFile.Length != final.Length ||
                 !safeFile.Sha256.Equals(final.Sha256, StringComparison.OrdinalIgnoreCase)))
            {
                issues.Add($"non_vtf_changed:{safeFile.RelativePath}");
            }
        }

        foreach (UniversalVtfManifestRow row in manifest.Rows)
        {
            if (!rawFiles.TryGetValue(row.RelativePath, out UniversalVtfTreeFile? rawFile) ||
                !safeFiles.TryGetValue(row.RelativePath, out UniversalVtfTreeFile? safeFile) ||
                !candidateFiles.TryGetValue(row.RelativePath, out UniversalVtfTreeFile? finalFile))
            {
                issues.Add($"manifest_path_missing:{row.RelativePath}");
                continue;
            }

            if (rawFile.Length != row.RawBytes ||
                !rawFile.Sha256.Equals(row.RawSha256, StringComparison.OrdinalIgnoreCase))
            {
                issues.Add($"raw_row_drift:{row.RelativePath}");
            }

            if (safeFile.Length != row.SafeBytes ||
                !safeFile.Sha256.Equals(row.SafeSha256, StringComparison.OrdinalIgnoreCase))
            {
                issues.Add($"safe_row_drift:{row.RelativePath}");
            }

            if (finalFile.Length != row.FinalBytes ||
                !finalFile.Sha256.Equals(row.FinalSha256, StringComparison.OrdinalIgnoreCase))
            {
                issues.Add($"final_row_drift:{row.RelativePath}");
            }

            string finalPath = Combine(request.CandidateRoot, finalFile.RelativePath);
            try
            {
                VtfContainerLayout layout = VtfContainerLayoutReader.Read(finalPath);
                if (layout.Document.HighResFormat is not (13 or 15))
                {
                    issues.Add($"final_format_not_opaque_bc:{row.RelativePath}:" +
                               layout.Document.HighResFormat);
                }

                if (layout.Document.HighResFormat == 20)
                {
                    issues.Add($"format20_forbidden:{row.RelativePath}");
                }

                if (layout.Document.Width != row.FinalWidth ||
                    layout.Document.Height != row.FinalHeight ||
                    layout.Document.Depth != row.FinalDepth ||
                    layout.Document.MipCount != row.FinalMipCount ||
                    layout.Document.HighResFormat != row.FinalFormat ||
                    layout.Resources.Count != row.ResourceCount)
                {
                    issues.Add($"container_row_mismatch:{row.RelativePath}");
                }
            }
            catch (Exception error) when (error is IOException or InvalidDataException or
                                          ArgumentException or OverflowException)
            {
                issues.Add($"container_parse:{row.RelativePath}:{error.Message}");
            }

            int profileLevel = manifest.Profile switch
            {
                UniversalVtfProfileKind.Conservative => row.ConservativeLevel,
                UniversalVtfProfileKind.Balanced => row.BalancedLevel,
                UniversalVtfProfileKind.Aggressive => row.AggressiveLevel,
                _ => -1
            };
            if (row.SelectedLevel != profileLevel || row.SelectedLevel < 1 ||
                row.SelectedLevel > caps.MaxLevel(row.RelativePath, manifest.Profile))
            {
                issues.Add($"selection_policy:{row.RelativePath}");
            }

            if (bridgeRows.TryGetValue(row.RelativePath, out Bc3RawBridgeRow? bridgeRow))
            {
                if (row.BcEligibilityMode != bridgeRow.EligibilityMode ||
                    !string.Equals(
                        row.BcReceiptSha256,
                        request.BcBridgeSha256,
                        StringComparison.OrdinalIgnoreCase) ||
                    bridgeRow.RawBytes != rawFile.Length ||
                    !bridgeRow.RawSha256.Equals(
                        rawFile.Sha256,
                        StringComparison.OrdinalIgnoreCase))
                {
                    issues.Add($"bc3_bridge_row_mismatch:{row.RelativePath}");
                }
            }
            else if (row.BcEligibilityMode is not null || row.BcReceiptSha256 is not null)
            {
                issues.Add($"unexpected_bc3_receipt:{row.RelativePath}");
            }
        }

        string[] finalVtfPaths = candidate.Files
            .Where(file => file.RelativePath.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
            .Select(file => file.RelativePath)
            .ToArray();
        if (finalVtfPaths.Any(path => !manifestPaths.Contains(path)) ||
            manifest.Rows.Count != request.Anchors.ExpectedVtfCount)
        {
            issues.Add("manifest_vtf_path_coverage");
        }

        foreach (Bc3RawBridgeRow bridgeRow in bridge.Rows)
        {
            if (!manifestPaths.Contains(bridgeRow.RelativePath))
            {
                issues.Add($"bc3_bridge_path_not_in_manifest:{bridgeRow.RelativePath}");
            }
        }

        foreach (UniversalVtfRuntimeCap cap in caps.Caps)
        {
            if (!manifestPaths.Contains(cap.RelativePath))
            {
                issues.Add($"runtime_cap_path_not_in_manifest:{cap.RelativePath}");
            }
        }
    }

    private static void CheckPathSets(
        ICollection<string> issues,
        UniversalVtfTreeSnapshot raw,
        UniversalVtfTreeSnapshot safe,
        UniversalVtfTreeSnapshot candidate,
        UniversalVtfManifest manifest)
    {
        string[] rawPaths = raw.Files.Select(file => file.RelativePath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        string[] safePaths = safe.Files.Select(file => file.RelativePath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        string[] finalPaths = candidate.Files.Select(file => file.RelativePath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        if (!safePaths.SequenceEqual(finalPaths, StringComparer.OrdinalIgnoreCase))
        {
            issues.Add("safe_final_path_set_mismatch");
        }

        string[] rawVtfs = rawPaths.Where(path =>
            path.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase)).ToArray();
        string[] safeVtfs = safePaths.Where(path =>
            path.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase)).ToArray();
        if (!rawVtfs.SequenceEqual(safeVtfs, StringComparer.OrdinalIgnoreCase))
        {
            issues.Add("raw_safe_vtf_path_set_mismatch");
        }

        string[] rows = manifest.Rows.Select(row => row.RelativePath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        if (!rawVtfs.SequenceEqual(rows, StringComparer.OrdinalIgnoreCase))
        {
            issues.Add("raw_manifest_vtf_path_set_mismatch");
        }
    }

    private static void CheckBridge(
        ICollection<string> issues,
        Bc3RawBridge bridge,
        UniversalVtfTreeSnapshot raw,
        UniversalVtfTreeSnapshot safe,
        UniversalVtfBuildAnchors anchors)
    {
        if (!bridge.RawTreeSha256.Equals(raw.TreeSha256, StringComparison.OrdinalIgnoreCase) ||
            bridge.RawTreeBytes != raw.TotalBytes || bridge.RawTreeFileCount != raw.Files.Count ||
            !bridge.ApprovedSafeTreeSha256.Equals(
                safe.TreeSha256,
                StringComparison.OrdinalIgnoreCase) ||
            bridge.ApprovedSafeTreeBytes != safe.TotalBytes ||
            bridge.ApprovedSafeTreeFileCount != safe.Files.Count)
        {
            issues.Add("bc3_bridge_authority_header_mismatch");
        }

        if (anchors.RequireFrozenBcPartition &&
            (bridge.Rows.Count != 193 || bridge.PhysicalOpaqueBc3Count != 155 ||
             bridge.RawDxt1Count != 32 || bridge.SemanticExceptionBc3Count != 6 ||
             bridge.EquivalentPriorSafeColorChainCount != 0))
        {
            issues.Add("bc3_bridge_frozen_partition_mismatch");
        }
    }

    private static void CheckManifestTree(
        ICollection<string> issues,
        UniversalVtfManifestTree declared,
        UniversalVtfTreeSnapshot actual,
        string authority)
    {
        if (declared.FileCount != actual.Files.Count || declared.Bytes != actual.TotalBytes ||
            !declared.TreeSha256.Equals(actual.TreeSha256, StringComparison.OrdinalIgnoreCase))
        {
            issues.Add($"manifest_{authority}_tree_mismatch");
        }
    }

    private static void CheckAnchor(
        ICollection<string> issues,
        UniversalVtfTreeSnapshot snapshot,
        UniversalVtfTreeAnchor anchor,
        string authority)
    {
        try
        {
            UniversalVtfTreeInventory.RequireAnchor(snapshot, anchor);
        }
        catch (InvalidDataException error)
        {
            issues.Add($"{authority}_{error.Message}");
        }
    }

    private static string Combine(string root, string relativePath)
    {
        string authority = Path.GetFullPath(root).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string path = Path.GetFullPath(Path.Combine(
            authority,
            relativePath.Replace('/', Path.DirectorySeparatorChar)));
        if (!path.StartsWith(authority + Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"audit_path_escape:{relativePath}");
        }

        return path;
    }
}
