using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfLegacyImportRequest(
    string RawRoot,
    string SafeRoot,
    string BcBridgePath,
    string BcBridgeSha256,
    string LegacyRoot,
    string LegacyBuildLogPath,
    string LegacyBuildLogSha256,
    IReadOnlyDictionary<UniversalVtfProfileKind, string> ManifestSha256,
    UniversalVtfBuildAnchors Anchors);

internal sealed record UniversalVtfLegacyImportProgress(
    int Completed,
    int Total,
    string RelativePath);

internal sealed record UniversalVtfLegacyImportResult(
    int TotalVtfs,
    int ImportedVtfs,
    int RecalculatedVtfs,
    int UniqueFinalBlobs,
    double ElapsedSeconds);

internal static class UniversalVtfLegacyCheckpointImporter
{
    private static readonly JsonSerializerOptions LegacyOptions = CreateLegacyOptions();

    internal static async Task<UniversalVtfLegacyImportResult> ImportAsync(
        UniversalVtfLegacyImportRequest request,
        UniversalVtfPreparationCache cache,
        IProgress<UniversalVtfLegacyImportProgress>? progress,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        ArgumentNullException.ThrowIfNull(cache);
        ValidatePinnedHashes(request);
        Stopwatch elapsed = Stopwatch.StartNew();

        byte[] buildLog = ReadPinned(
            request.LegacyBuildLogPath,
            request.LegacyBuildLogSha256,
            "legacy_build_log");
        var manifestBytes = new Dictionary<UniversalVtfProfileKind, byte[]>();
        foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
        {
            manifestBytes.Add(
                profile,
                ReadPinned(
                    ManifestPath(request.LegacyRoot, profile),
                    request.ManifestSha256[profile],
                    $"legacy_{profile.ToString().ToLowerInvariant()}_manifest"));
        }

        cancellationToken.ThrowIfCancellationRequested();
        string rawRoot = RequireDirectory(request.RawRoot);
        string safeRoot = RequireDirectory(request.SafeRoot);
        string legacyRoot = RequireDirectory(request.LegacyRoot);
        UniversalVtfTreeSnapshot rawTree = UniversalVtfTreeInventory.Capture(rawRoot);
        UniversalVtfTreeSnapshot safeTree = UniversalVtfTreeInventory.Capture(safeRoot);
        UniversalVtfTreeInventory.RequireAnchor(rawTree, request.Anchors.RawAnchor);
        UniversalVtfTreeInventory.RequireAnchor(safeTree, request.Anchors.SafeAnchor);
        Bc3RawBridge bridge = Bc3RawBridgeStore.LoadAndVerify(
            request.BcBridgePath,
            request.BcBridgeSha256);
        ValidateBridgeHeader(bridge, rawTree, safeTree);
        UniversalVtfInventory rawInventory = UniversalVtfInventoryScanner.Scan(rawRoot);
        UniversalVtfInventory safeInventory = UniversalVtfInventoryScanner.Scan(safeRoot);
        if (!rawInventory.IsComplete || !safeInventory.IsComplete ||
            rawInventory.Entries.Count != request.Anchors.ExpectedVtfCount ||
            safeInventory.Entries.Count != request.Anchors.ExpectedVtfCount)
        {
            throw new InvalidDataException("legacy_import_inventory_incomplete");
        }

        UniversalVtfPolicyReceipt policy =
            UniversalVtfCorpusBuilder.ResolveEmbeddedPolicyReceipt(request.BcBridgeSha256);
        var manifests = manifestBytes.ToDictionary(
            pair => pair.Key,
            pair => ParseLegacyManifest(pair.Value, pair.Key));
        ValidateManifestAuthorities(
            request,
            manifests,
            rawTree,
            safeTree,
            policy);
        var finalTrees = new Dictionary<UniversalVtfProfileKind, UniversalVtfTreeSnapshot>();
        foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
        {
            string root = RequireDirectory(ProfileRoot(legacyRoot, profile));
            UniversalVtfTreeSnapshot tree = UniversalVtfTreeInventory.Capture(root);
            RequireTree(manifests[profile].FinalTree, tree, $"legacy_{profile}_final");
            finalTrees.Add(profile, tree);
        }

        ValidateBuildLog(buildLog, request, manifests, finalTrees, policy);
        IReadOnlyList<string> frameworkRoots = DiscoverFrameworkRoots(rawRoot);
        VtfConsumerGraph graph = VtfConsumerGraphBuilder.Build(
            rawRoot,
            safeRoot,
            frameworkRoots);
        Dictionary<string, UniversalVtfInventoryEntry> rawByPath = rawInventory.Entries
            .ToDictionary(value => value.RelativePath, StringComparer.OrdinalIgnoreCase);
        Dictionary<string, UniversalVtfTreeFile> safeByPath = safeTree.Files
            .ToDictionary(value => value.RelativePath, StringComparer.OrdinalIgnoreCase);
        Dictionary<string, Bc3RawBridgeRow> bridgeByPath = bridge.Rows
            .ToDictionary(value => value.RelativePath, StringComparer.OrdinalIgnoreCase);
        string[] paths = manifests[UniversalVtfProfileKind.Conservative].Rows
            .Select(row => row.RelativePath)
            .ToArray();
        RequireSharedPathSets(manifests, paths, rawByPath.Keys);

        var uniqueFinal = new HashSet<string>(StringComparer.Ordinal);
        int imported = 0;
        foreach (string path in paths)
        {
            cancellationToken.ThrowIfCancellationRequested();
            UniversalVtfManifestRow conservative =
                manifests[UniversalVtfProfileKind.Conservative].Row(path);
            UniversalVtfManifestRow balanced =
                manifests[UniversalVtfProfileKind.Balanced].Row(path);
            UniversalVtfManifestRow aggressive =
                manifests[UniversalVtfProfileKind.Aggressive].Row(path);
            RequireSharedRow(conservative, balanced, aggressive);
            UniversalVtfInventoryEntry raw = rawByPath[path];
            UniversalVtfTreeFile safe = safeByPath[path];
            ValidateRawAndSafeRow(conservative, raw, safe);
            UniversalVtfSemanticDecision semantics = UniversalVtfSemanticResolver.Resolve(
                raw,
                graph);
            ValidateSemantics(conservative, semantics);

            string replayRoot = Path.Combine(
                Path.GetTempPath(),
                "GmodAddonOptimizer",
                "UniversalVtfLegacyReplay");
            UniversalVtfAttemptMatrix matrix = UniversalVtfSelector.ReplayMeasuredAttempts(
                path,
                raw.Flags,
                conservative.RouteAttempts,
                (level, route) => Path.Combine(
                    replayRoot,
                    $"{level:D2}-{route}.vtf"));
            UniversalVtfProfileSelection selection = UniversalVtfSelector.Select(
                matrix,
                UniversalVtfRuntimeCaps.Empty);
            ValidateReplay(
                path,
                selection,
                conservative,
                balanced,
                aggressive);

            Bc3RawBridgeRow? bridgeRow = bridgeByPath.GetValueOrDefault(path);
            UniversalVtfAnalysisIdentity analysisIdentity =
                UniversalVtfCorpusBuilder.CreateAnalysisIdentity(
                    raw,
                    semantics,
                    bridgeRow,
                    policy.PolicyFingerprint,
                    policy.ToolchainSha256,
                    request.BcBridgeSha256);
            string analysisKey = UniversalVtfCacheKey.Analysis(analysisIdentity);
            var analysis = new UniversalVtfAnalysisReceipt(
                UniversalVtfCacheContracts.AnalysisReceiptSchema,
                analysisKey,
                analysisIdentity,
                UniversalVtfCacheCoverage.ImportedExactProfileFinals,
                UniversalVtfCachedAttemptMatrix.FromRuntime(matrix),
                new Dictionary<
                    UniversalVtfCachedArtifactKey,
                    UniversalVtfArtifactReference>());
            UniversalVtfCompletionIdentity completionIdentity =
                UniversalVtfCorpusBuilder.CreateCompletionIdentity(
                    safe,
                    null,
                    analysisKey,
                    selection);
            var completedProfiles = new Dictionary<
                UniversalVtfProfileKind,
                UniversalVtfCompletedProfile>();
            var sources = new Dictionary<string, string>(StringComparer.Ordinal);
            foreach ((UniversalVtfProfileKind profile, UniversalVtfManifestRow row) in new[]
                     {
                         (UniversalVtfProfileKind.Conservative, conservative),
                         (UniversalVtfProfileKind.Balanced, balanced),
                         (UniversalVtfProfileKind.Aggressive, aggressive)
                     })
            {
                string finalPath = CombineAuthority(ProfileRoot(legacyRoot, profile), path);
                UniversalVtfArtifactReference final =
                    UniversalVtfArtifactReference.FromVtf(finalPath);
                ValidateFinalRow(row, final);
                uniqueFinal.Add(final.Sha256);
                sources.TryAdd(final.Sha256, finalPath);
                UniversalVtfCachedSelection cached =
                    completionIdentity.Selections[profile];
                completedProfiles.Add(
                    profile,
                    new UniversalVtfCompletedProfile(
                        profile,
                        cached,
                        row.WorstGate,
                        final,
                        row.SpatialBytes,
                        row.SpatialSha256,
                        row.ByteFloor,
                        row.Blocker,
                        row.LowResolutionImageRegenerated,
                        row.ResourceCount));
            }

            var completion = new UniversalVtfCompletionReceipt(
                UniversalVtfCacheContracts.CompletionReceiptSchema,
                UniversalVtfCacheKey.Completion(completionIdentity),
                completionIdentity,
                completedProfiles);
            await cache.PublishAsync(
                new UniversalVtfCachedAsset(analysis, completion, sources),
                cancellationToken).ConfigureAwait(false);
            imported++;
            progress?.Report(new UniversalVtfLegacyImportProgress(
                imported,
                paths.Length,
                path));
        }

        elapsed.Stop();
        return new UniversalVtfLegacyImportResult(
            paths.Length,
            imported,
            0,
            uniqueFinal.Count,
            elapsed.Elapsed.TotalSeconds);
    }

    private static UniversalVtfManifest ParseLegacyManifest(
        byte[] bytes,
        UniversalVtfProfileKind expectedProfile)
    {
        UniversalVtfManifest manifest;
        try
        {
            manifest = JsonSerializer.Deserialize<UniversalVtfManifest>(bytes, LegacyOptions)
                ?? throw new InvalidDataException("legacy_manifest_null");
        }
        catch (JsonException error)
        {
            throw new InvalidDataException("legacy_manifest_invalid_json", error);
        }

        UniversalVtfManifest validated = UniversalVtfManifestWriter.ValidateManifest(manifest);
        if (validated.Profile != expectedProfile)
        {
            throw new InvalidDataException(
                $"legacy_manifest_profile:{validated.Profile}!={expectedProfile}");
        }

        return validated;
    }

    private static void ValidateManifestAuthorities(
        UniversalVtfLegacyImportRequest request,
        IReadOnlyDictionary<UniversalVtfProfileKind, UniversalVtfManifest> manifests,
        UniversalVtfTreeSnapshot raw,
        UniversalVtfTreeSnapshot safe,
        UniversalVtfPolicyReceipt policy)
    {
        foreach ((UniversalVtfProfileKind profile, UniversalVtfManifest manifest) in manifests)
        {
            if (!manifest.PolicyFingerprint.Equals(policy.PolicyFingerprint, StringComparison.Ordinal) ||
                !manifest.FixtureSha256.Equals(
                    UniversalVtfPolicy.V1.FixtureSha256,
                    StringComparison.Ordinal) ||
                !manifest.ToolchainSha256.Equals(policy.ToolchainSha256, StringComparison.Ordinal) ||
                !manifest.BcBridgeSha256.Equals(
                    request.BcBridgeSha256,
                    StringComparison.OrdinalIgnoreCase) ||
                manifest.RuntimeCapsSha256 is not null)
            {
                throw new InvalidDataException($"legacy_manifest_authority_drift:{profile}");
            }

            RequireTree(manifest.RawTree, raw, $"legacy_{profile}_raw");
            RequireTree(manifest.SafeTree, safe, $"legacy_{profile}_safe");
        }
    }

    private static void ValidateBuildLog(
        byte[] bytes,
        UniversalVtfLegacyImportRequest request,
        IReadOnlyDictionary<UniversalVtfProfileKind, UniversalVtfManifest> manifests,
        IReadOnlyDictionary<UniversalVtfProfileKind, UniversalVtfTreeSnapshot> finalTrees,
        UniversalVtfPolicyReceipt policy)
    {
        string[] lines = System.Text.Encoding.UTF8.GetString(bytes)
            .Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries);
        if (lines.Length == 0)
        {
            throw new InvalidDataException("legacy_build_log_empty");
        }

        using JsonDocument document = JsonDocument.Parse(lines[^1]);
        JsonElement root = document.RootElement;
        if (root.GetProperty("status").GetString() != "CLEAN" ||
            root.GetProperty("schema").GetString() != "universal-vtf-build-result-v1" ||
            root.GetProperty("policyFingerprint").GetString() != policy.PolicyFingerprint ||
            root.GetProperty("toolchainSha256").GetString() != policy.ToolchainSha256 ||
            !string.Equals(
                root.GetProperty("bcBridgeSha256").GetString(),
                request.BcBridgeSha256,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException("legacy_build_log_authority_drift");
        }

        JsonElement[] profiles = root.GetProperty("profiles").EnumerateArray().ToArray();
        if (profiles.Length != 3)
        {
            throw new InvalidDataException("legacy_build_log_profiles_incomplete");
        }

        foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
        {
            string name = profile.ToString().ToLowerInvariant();
            JsonElement item = profiles.Single(value =>
                value.GetProperty("profile").GetString() == name);
            if (item.GetProperty("manifestSha256").GetString() !=
                request.ManifestSha256[profile] ||
                item.GetProperty("bytes").GetInt64() != finalTrees[profile].TotalBytes ||
                item.GetProperty("treeSha256").GetString() != finalTrees[profile].TreeSha256 ||
                manifests[profile].FinalTree.TreeSha256 != finalTrees[profile].TreeSha256)
            {
                throw new InvalidDataException($"legacy_build_log_profile_drift:{profile}");
            }
        }
    }

    private static void RequireSharedPathSets(
        IReadOnlyDictionary<UniversalVtfProfileKind, UniversalVtfManifest> manifests,
        IReadOnlyList<string> expected,
        IEnumerable<string> inventoryPaths)
    {
        foreach (UniversalVtfManifest manifest in manifests.Values)
        {
            if (!manifest.Rows.Select(row => row.RelativePath).SequenceEqual(
                    expected,
                    StringComparer.Ordinal))
            {
                throw new InvalidDataException("legacy_manifest_path_set_drift");
            }
        }

        if (!inventoryPaths.OrderBy(value => value, StringComparer.Ordinal)
                .SequenceEqual(expected, StringComparer.Ordinal))
        {
            throw new InvalidDataException("legacy_inventory_path_set_drift");
        }
    }

    private static void RequireSharedRow(params UniversalVtfManifestRow[] rows)
    {
        byte[] expected = SharedRowFingerprint(rows[0]);
        if (rows.Skip(1).Any(row =>
                !SharedRowFingerprint(row).AsSpan().SequenceEqual(expected)))
        {
            throw new InvalidDataException($"legacy_cross_profile_row_drift:{rows[0].RelativePath}");
        }
    }

    private static byte[] SharedRowFingerprint(UniversalVtfManifestRow row) =>
        UniversalVtfCanonicalJson.Serialize(new
        {
            row.RelativePath,
            row.Family,
            row.SemanticClasses,
            row.ConsumerProofSha256,
            row.RawWidth,
            row.RawHeight,
            row.RawDepth,
            row.RawFrames,
            row.RawFaces,
            row.RawMipCount,
            row.RawFormat,
            row.Authored,
            row.Generated,
            row.ConservativeLevel,
            row.BalancedLevel,
            row.AggressiveLevel,
            row.ProfileAttempts,
            row.RouteAttempts,
            row.RawBytes,
            row.RawSha256,
            row.SafeBytes,
            row.SafeSha256,
            row.MandatoryFloorApplied,
            row.BcEligibilityMode,
            row.BcReceiptSha256
        });

    private static void ValidateRawAndSafeRow(
        UniversalVtfManifestRow row,
        UniversalVtfInventoryEntry raw,
        UniversalVtfTreeFile safe)
    {
        if (row.RawBytes != raw.Bytes || row.RawSha256 != raw.Sha256 ||
            row.RawWidth != raw.Width || row.RawHeight != raw.Height ||
            row.RawDepth != raw.Depth || row.RawFrames != raw.Frames ||
            row.RawFaces != raw.Faces || row.RawMipCount != raw.MipCount ||
            row.RawFormat != raw.HighResFormat || row.SafeBytes != safe.Length ||
            row.SafeSha256 != safe.Sha256)
        {
            throw new InvalidDataException($"legacy_raw_safe_row_drift:{row.RelativePath}");
        }
    }

    private static void ValidateSemantics(
        UniversalVtfManifestRow row,
        UniversalVtfSemanticDecision semantics)
    {
        string proof = UniversalVtfSemanticProof.Compute(semantics);
        string[] classes = semantics.Classes.Select(value =>
                char.ToLowerInvariant(value.ToString()[0]) + value.ToString()[1..])
            .OrderBy(value => value, StringComparer.Ordinal)
            .ToArray();
        if (row.ConsumerProofSha256 != proof ||
            !row.SemanticClasses.SequenceEqual(classes, StringComparer.Ordinal))
        {
            throw new InvalidDataException($"legacy_semantic_drift:{row.RelativePath}");
        }
    }

    private static void ValidateReplay(
        string path,
        UniversalVtfProfileSelection replay,
        UniversalVtfManifestRow conservative,
        UniversalVtfManifestRow balanced,
        UniversalVtfManifestRow aggressive)
    {
        if (replay.Conservative.Level != conservative.ConservativeLevel ||
            replay.Balanced.Level != balanced.BalancedLevel ||
            replay.Aggressive.Level != aggressive.AggressiveLevel ||
            replay.Conservative.Route != conservative.Route ||
            replay.Balanced.Route != balanced.Route ||
            replay.Aggressive.Route != aggressive.Route ||
            !UniversalVtfCanonicalJson.Serialize(replay.Attempts).AsSpan().SequenceEqual(
                UniversalVtfCanonicalJson.Serialize(conservative.ProfileAttempts)))
        {
            throw new InvalidDataException($"legacy_selector_replay_drift:{path}");
        }
    }

    private static void ValidateFinalRow(
        UniversalVtfManifestRow row,
        UniversalVtfArtifactReference final)
    {
        if (row.FinalSha256 != final.Sha256 || row.FinalBytes != final.Bytes ||
            row.FinalWidth != final.Width || row.FinalHeight != final.Height ||
            row.FinalDepth != final.Depth || row.FinalMipCount != final.MipCount ||
            row.FinalFormat != final.Format)
        {
            throw new InvalidDataException($"legacy_final_file_drift:{row.RelativePath}");
        }
    }

    private static void ValidatePinnedHashes(UniversalVtfLegacyImportRequest request)
    {
        Bc3RawBridgeStore.RequireSha256(
            request.LegacyBuildLogSha256,
            nameof(request.LegacyBuildLogSha256));
        Bc3RawBridgeStore.RequireSha256(
            request.BcBridgeSha256,
            nameof(request.BcBridgeSha256));
        UniversalVtfProfileKind[] profiles = Enum.GetValues<UniversalVtfProfileKind>();
        if (request.ManifestSha256 is null || request.ManifestSha256.Count != profiles.Length)
        {
            throw new InvalidDataException("legacy_manifest_hashes_incomplete");
        }

        foreach (UniversalVtfProfileKind profile in profiles)
        {
            if (!request.ManifestSha256.TryGetValue(profile, out string? hash))
            {
                throw new InvalidDataException($"legacy_manifest_hash_missing:{profile}");
            }

            Bc3RawBridgeStore.RequireSha256(hash, $"{profile}ManifestSha256");
        }
    }

    private static byte[] ReadPinned(string path, string expectedSha256, string authority)
    {
        string full = Path.GetFullPath(path);
        if (!File.Exists(full))
        {
            throw new FileNotFoundException($"{authority}_missing", full);
        }

        byte[] bytes = File.ReadAllBytes(full);
        string actual = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        if (!actual.Equals(expectedSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException(
                $"{authority}_sha256_mismatch:{actual}!={expectedSha256.ToLowerInvariant()}");
        }

        return bytes;
    }

    private static void ValidateBridgeHeader(
        Bc3RawBridge bridge,
        UniversalVtfTreeSnapshot raw,
        UniversalVtfTreeSnapshot safe)
    {
        if (bridge.RawTreeSha256 != raw.TreeSha256 || bridge.RawTreeBytes != raw.TotalBytes ||
            bridge.RawTreeFileCount != raw.Files.Count ||
            bridge.ApprovedSafeTreeSha256 != safe.TreeSha256 ||
            bridge.ApprovedSafeTreeBytes != safe.TotalBytes ||
            bridge.ApprovedSafeTreeFileCount != safe.Files.Count)
        {
            throw new InvalidDataException("legacy_bridge_authority_header_mismatch");
        }
    }

    private static void RequireTree(
        UniversalVtfManifestTree expected,
        UniversalVtfTreeSnapshot actual,
        string authority)
    {
        if (expected.FileCount != actual.Files.Count || expected.Bytes != actual.TotalBytes ||
            expected.TreeSha256 != actual.TreeSha256)
        {
            throw new InvalidDataException($"{authority}_tree_drift");
        }
    }

    private static string ManifestPath(string legacyRoot, UniversalVtfProfileKind profile) =>
        Path.Combine(
            Path.GetFullPath(legacyRoot),
            UniversalVtfCorpusBuilder.ProfileName(profile) + ".manifest.json");

    private static string ProfileRoot(string legacyRoot, UniversalVtfProfileKind profile) =>
        Path.Combine(Path.GetFullPath(legacyRoot), UniversalVtfCorpusBuilder.ProfileName(profile));

    private static string RequireDirectory(string path)
    {
        string full = Path.GetFullPath(path);
        if (!Directory.Exists(full))
        {
            throw new DirectoryNotFoundException(full);
        }

        return full;
    }

    private static string CombineAuthority(string root, string relativePath)
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
            throw new InvalidDataException($"legacy_relative_path_escape:{relativePath}");
        }

        return path;
    }

    private static IReadOnlyList<string> DiscoverFrameworkRoots(string rawRoot)
    {
        string? parent = Path.GetDirectoryName(rawRoot);
        if (parent is null)
        {
            return Array.Empty<string>();
        }

        string framework = Path.Combine(parent, "lvs_framework");
        return Directory.Exists(framework) ? new[] { framework } : Array.Empty<string>();
    }

    private static JsonSerializerOptions CreateLegacyOptions()
    {
        var options = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            PropertyNameCaseInsensitive = false,
            WriteIndented = false,
            NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals
        };
        options.Converters.Add(new LegacyNamedDoubleConverter());
        options.Converters.Add(new JsonStringEnumConverter(JsonNamingPolicy.CamelCase));
        return options;
    }

    private sealed class LegacyNamedDoubleConverter : JsonConverter<double>
    {
        public override bool HandleNull => true;

        public override double Read(
            ref Utf8JsonReader reader,
            Type typeToConvert,
            JsonSerializerOptions options) => reader.TokenType switch
        {
            JsonTokenType.Null => double.NaN,
            JsonTokenType.Number => reader.GetDouble(),
            JsonTokenType.String => reader.GetString() switch
            {
                "NaN" => double.NaN,
                "Infinity" => double.PositiveInfinity,
                "-Infinity" => double.NegativeInfinity,
                _ => throw new JsonException("legacy_invalid_named_double")
            },
            _ => throw new JsonException($"legacy_invalid_double_token:{reader.TokenType}")
        };

        public override void Write(
            Utf8JsonWriter writer,
            double value,
            JsonSerializerOptions options) => throw new NotSupportedException(
            "Legacy manifests are read-only.");
    }
}
