using System.Text.Json;
using GmodAddonCompressor.Systems.Vtf;
using GmodAddonCompressor.Systems.Vtf.Universal;

namespace VtfUniversalCorpusDriver;

internal interface IUniversalVtfCorpusBuildService
{
    UniversalVtfPolicyReceipt ResolvePolicy(string bridgeSha256);

    Task<UniversalVtfBuildResult> BuildAsync(
        UniversalVtfBuildRequest request,
        IProgress<UniversalVtfBuildProgress>? progress,
        CancellationToken cancellationToken);
}

internal sealed class UniversalVtfCorpusBuildService : IUniversalVtfCorpusBuildService
{
    public UniversalVtfPolicyReceipt ResolvePolicy(string bridgeSha256) =>
        UniversalVtfCorpusBuilder.ResolveEmbeddedPolicyReceipt(bridgeSha256);

    public Task<UniversalVtfBuildResult> BuildAsync(
        UniversalVtfBuildRequest request,
        IProgress<UniversalVtfBuildProgress>? progress,
        CancellationToken cancellationToken) =>
        UniversalVtfCorpusBuilder.BuildAsync(request, progress, cancellationToken);
}

internal interface IUniversalVtfLegacyImportService
{
    Task<UniversalVtfLegacyImportResult> ImportAsync(
        UniversalVtfLegacyImportRequest request,
        string cacheRoot,
        IProgress<UniversalVtfLegacyImportProgress>? progress,
        CancellationToken cancellationToken);
}

internal sealed class UniversalVtfLegacyImportService : IUniversalVtfLegacyImportService
{
    public Task<UniversalVtfLegacyImportResult> ImportAsync(
        UniversalVtfLegacyImportRequest request,
        string cacheRoot,
        IProgress<UniversalVtfLegacyImportProgress>? progress,
        CancellationToken cancellationToken) =>
        UniversalVtfLegacyCheckpointImporter.ImportAsync(
            request,
            new UniversalVtfPreparationCache(cacheRoot),
            progress,
            cancellationToken);
}

internal interface IUniversalVtfMonotonicComposeService
{
    Task<UniversalVtfMonotonicComposeResult> ComposeAsync(
        UniversalVtfMonotonicComposeRequest request,
        CancellationToken cancellationToken);
}

internal sealed class UniversalVtfMonotonicComposeService :
    IUniversalVtfMonotonicComposeService
{
    public Task<UniversalVtfMonotonicComposeResult> ComposeAsync(
        UniversalVtfMonotonicComposeRequest request,
        CancellationToken cancellationToken) =>
        UniversalVtfMonotonicComposer.ComposeAsync(request, cancellationToken);
}

internal static class Program
{
    private static async Task<int> Main(string[] args)
    {
        using var cancellation = new CancellationTokenSource();
        ConsoleCancelEventHandler handler = (_, eventArgs) =>
        {
            eventArgs.Cancel = true;
            cancellation.Cancel();
        };
        Console.CancelKeyPress += handler;
        try
        {
            return await Driver.RunAsync(args, null, cancellation.Token).ConfigureAwait(false);
        }
        finally
        {
            Console.CancelKeyPress -= handler;
        }
    }
}

internal static class Driver
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    internal static async Task<int> RunAsync(
        string[] args,
        IUniversalVtfCorpusBuildService? buildService = null,
        CancellationToken cancellationToken = default,
        IUniversalVtfLegacyImportService? legacyImportService = null,
        IUniversalVtfMonotonicComposeService? monotonicComposeService = null)
    {
        ArgumentNullException.ThrowIfNull(args);
        if (args.Length == 0 || args.Contains("--help", StringComparer.OrdinalIgnoreCase) ||
            args[0].Equals("help", StringComparison.OrdinalIgnoreCase))
        {
            PrintHelp();
            return 0;
        }

        string command = args[0].ToLowerInvariant();
        buildService ??= new UniversalVtfCorpusBuildService();
        legacyImportService ??= new UniversalVtfLegacyImportService();
        monotonicComposeService ??= new UniversalVtfMonotonicComposeService();
        try
        {
            cancellationToken.ThrowIfCancellationRequested();
            IReadOnlyDictionary<string, string> options = ParseOptions(args.Skip(1).ToArray());
            return command switch
            {
                "toolchain" => RunToolchain(options),
                "inventory" => RunInventory(options, cancellationToken),
                "bc3-bridge" => RunBc3Bridge(options, cancellationToken),
                "generated-smoke" => await RunGeneratedSmokeAsync(
                    options,
                    cancellationToken).ConfigureAwait(false),
                "build" => await RunBuildAsync(
                    options,
                    buildService,
                    cancellationToken).ConfigureAwait(false),
                "seed-cache" => await RunSeedCacheAsync(
                    options,
                    legacyImportService,
                    cancellationToken).ConfigureAwait(false),
                "compose-monotonic" => await RunComposeMonotonicAsync(
                    options,
                    monotonicComposeService,
                    cancellationToken).ConfigureAwait(false),
                "audit" => RunAudit(options, buildService, cancellationToken),
                _ => throw new ArgumentException($"Unknown command: {command}")
            };
        }
        catch (OperationCanceledException)
        {
            Console.Error.WriteLine("CANCELLED");
            return 130;
        }
        catch (ArgumentException error)
        {
            Console.Error.WriteLine($"ARGUMENT_ERROR {error.Message}");
            return 2;
        }
        catch (InvalidDataException error)
        {
            int code = ClassifyInvalidData(command, error.Message);
            Console.Error.WriteLine($"{Label(code)} {error.Message}");
            return code;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or
                                      NotSupportedException or InvalidOperationException or
                                      OverflowException or JsonException or AggregateException)
        {
            int code = command == "audit" ? 5 : 4;
            Console.Error.WriteLine($"{Label(code)} {error.Message}");
            return code;
        }
    }

    private static async Task<int> RunComposeMonotonicAsync(
        IReadOnlyDictionary<string, string> options,
        IUniversalVtfMonotonicComposeService service,
        CancellationToken cancellationToken)
    {
        RequireOnly(
            options,
            "--profile",
            "--incumbent-root",
            "--incumbent-name",
            "--incumbent-file-count",
            "--incumbent-bytes",
            "--incumbent-tree-sha256",
            "--raw-root",
            "--safe-root",
            "--alternative-name",
            "--alternative-root",
            "--alternative-manifest",
            "--alternative-manifest-sha256",
            "--output-root",
            "--manifest");
        string profileValue = RequireOption(options, "--profile");
        if (!Enum.TryParse(profileValue, ignoreCase: true, out UniversalVtfProfileKind profile) ||
            !Enum.IsDefined(typeof(UniversalVtfProfileKind), profile))
        {
            throw new ArgumentException($"Invalid profile: {profileValue}");
        }

        string incumbentRoot = RequireExistingDirectory(options, "--incumbent-root");
        string rawRoot = RequireExistingDirectory(options, "--raw-root");
        string safeRoot = RequireExistingDirectory(options, "--safe-root");
        string alternativeRoot = RequireExistingDirectory(options, "--alternative-root");
        string alternativeManifest = RequireExistingFile(options, "--alternative-manifest");
        string outputRoot = Path.GetFullPath(RequireOption(options, "--output-root"));
        string manifestPath = Path.GetFullPath(RequireOption(options, "--manifest"));
        var anchor = new UniversalVtfTreeAnchor(
            RequireOption(options, "--incumbent-name"),
            RequirePositiveInt(options, "--incumbent-file-count"),
            RequirePositiveLong(options, "--incumbent-bytes"),
            RequireSha256(options, "--incumbent-tree-sha256"));
        var alternative = new UniversalVtfMonotonicAlternativeAuthority(
            RequireOption(options, "--alternative-name"),
            alternativeRoot,
            alternativeManifest,
            RequireSha256(options, "--alternative-manifest-sha256"));
        var request = new UniversalVtfMonotonicComposeRequest(
            profile,
            incumbentRoot,
            anchor,
            new[] { alternative },
            outputRoot,
            manifestPath,
            new[]
            {
                new UniversalVtfMonotonicProtectedAuthority(
                    "raw",
                    rawRoot,
                    UniversalVtfCorpusBuilder.DefaultAnchors.RawAnchor),
                new UniversalVtfMonotonicProtectedAuthority(
                    "safe",
                    safeRoot,
                    UniversalVtfCorpusBuilder.DefaultAnchors.SafeAnchor)
            });
        UniversalVtfMonotonicComposeResult result = await service.ComposeAsync(
            request,
            cancellationToken).ConfigureAwait(false);
        WriteJson(new
        {
            status = "CLEAN",
            schema = "universal-vtf-monotonic-compose-result-v1",
            profile = profile.ToString().ToLowerInvariant(),
            result.Root,
            result.Bytes,
            result.TreeSha256,
            result.SavedBytes,
            result.ReplacedVtfs,
            result.ManifestPath,
            result.ManifestSha256
        });
        return 0;
    }

    private static int RunToolchain(IReadOnlyDictionary<string, string> options)
    {
        RequireOnly(options);
        using UniversalVtfEncodingSession session =
            UniversalVtfCandidateMaterializer.CreateEncodingSession();
        UniversalVtfToolchainFingerprint fingerprint = session.Fingerprint;
        WriteJson(new
        {
            status = "CLEAN",
            schema = "universal-vtf-toolchain-v1",
            fingerprint.Sha256,
            fingerprint.CanonicalArguments,
            fingerprint.Binaries
        });
        return 0;
    }

    private static int RunInventory(
        IReadOnlyDictionary<string, string> options,
        CancellationToken cancellationToken)
    {
        RequireOnly(options, "--raw-root", "--output");
        string rawRoot = RequireExistingDirectory(options, "--raw-root");
        string requestedOutput = Path.GetFullPath(RequireOption(options, "--output"));
        if (Overlaps(rawRoot, requestedOutput))
        {
            throw new ArgumentException("Inventory output cannot overlap the raw authority.");
        }

        string outputParent = Path.GetDirectoryName(requestedOutput)
            ?? throw new ArgumentException("Inventory output has no parent directory.");
        Directory.CreateDirectory(outputParent);
        string output = UniversalVtfTreeInventory.ResolveNewDestination(
            outputParent,
            Path.GetFileName(requestedOutput));

        cancellationToken.ThrowIfCancellationRequested();
        UniversalVtfTreeSnapshot snapshot = UniversalVtfTreeInventory.Capture(rawRoot);
        UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(rawRoot);
        UniversalVtfCorpusExpectations.LvsV1.Verify(inventory, snapshot);
        cancellationToken.ThrowIfCancellationRequested();

        var payload = new
        {
            schema = "universal-vtf-raw-inventory-v1",
            rawRoot,
            tree = snapshot,
            vtf = new
            {
                inventory.DiscoveredCount,
                inventory.IsComplete,
                inventory.VtfBytes,
                inventory.Entries,
                inventory.Failures
            }
        };
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(
            payload,
            new JsonSerializerOptions
            {
                PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
                WriteIndented = true
            });
        using (var stream = new FileStream(
                   output,
                   FileMode.CreateNew,
                   FileAccess.Write,
                   FileShare.None))
        {
            stream.Write(bytes);
            stream.Flush(flushToDisk: true);
        }

        WriteJson(new
        {
            status = "CLEAN",
            vtfCount = inventory.Entries.Count,
            vtfBytes = inventory.VtfBytes,
            treeSha256 = snapshot.TreeSha256,
            output
        });
        return 0;
    }

    private static int RunBc3Bridge(
        IReadOnlyDictionary<string, string> options,
        CancellationToken cancellationToken)
    {
        RequireOnly(
            options,
            "--raw-root",
            "--safe-root",
            "--prior-manifest",
            "--output");
        string rawRoot = RequireExistingDirectory(options, "--raw-root");
        string safeRoot = RequireExistingDirectory(options, "--safe-root");
        string priorManifest = Path.GetFullPath(RequireOption(options, "--prior-manifest"));
        string requestedOutput = Path.GetFullPath(RequireOption(options, "--output"));
        RequireSeparateAuthorities(rawRoot, safeRoot, requestedOutput, requestedOutput);

        string outputParent = Path.GetDirectoryName(requestedOutput)
            ?? throw new ArgumentException("Bridge output has no parent directory.");
        Directory.CreateDirectory(outputParent);
        string output = UniversalVtfTreeInventory.ResolveNewDestination(
            outputParent,
            Path.GetFileName(requestedOutput));

        cancellationToken.ThrowIfCancellationRequested();
        UniversalVtfTreeSnapshot rawBefore = UniversalVtfTreeInventory.Capture(rawRoot);
        UniversalVtfTreeSnapshot safeBefore = UniversalVtfTreeInventory.Capture(safeRoot);
        UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(rawRoot);
        Bc3RawBridge bridge = Bc3ApprovedReceipt.Build(
            inventory,
            priorManifest,
            safeBefore,
            rawBefore);
        cancellationToken.ThrowIfCancellationRequested();
        Bc3RawBridgeFrozen frozen = Bc3RawBridgeStore.WriteCanonical(output, bridge);

        RequireUnchanged(rawBefore, UniversalVtfTreeInventory.Capture(rawRoot), "raw");
        RequireUnchanged(safeBefore, UniversalVtfTreeInventory.Capture(safeRoot), "safe");
        WriteJson(new
        {
            status = "CLEAN",
            rows = bridge.Rows.Count,
            physicalOpaqueBc3 = bridge.PhysicalOpaqueBc3Count,
            rawDxt1 = bridge.RawDxt1Count,
            semanticExceptions = bridge.SemanticExceptionBc3Count,
            equivalentPriorSafeColorChains = bridge.EquivalentPriorSafeColorChainCount,
            sha256 = frozen.Sha256,
            output = frozen.Path
        });
        return 0;
    }

    private static async Task<int> RunGeneratedSmokeAsync(
        IReadOnlyDictionary<string, string> options,
        CancellationToken cancellationToken)
    {
        RequireOnly(options, "--raw-root");
        string rawRoot = RequireExistingDirectory(options, "--raw-root");
        UniversalVtfTreeSnapshot before = UniversalVtfTreeInventory.Capture(rawRoot);
        UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(rawRoot);
        UniversalVtfCorpusExpectations.LvsV1.Verify(inventory, before);
        UniversalVtfInventoryEntry[] entries = inventory.Entries
            .Where(entry => entry.MipCount == 1 && entry.IsNomip)
            .ToArray();
        if (entries.Length != 7)
        {
            throw new InvalidDataException($"generated_smoke_nomip_count:{entries.Length}!=7");
        }

        using UniversalVtfEncodingSession session =
            UniversalVtfCandidateMaterializer.CreateEncodingSession();
        int candidates = 0;
        foreach (UniversalVtfInventoryEntry entry in entries)
        foreach (int level in UniversalVtfLevels.Enumerate(entry.Width, entry.Height))
        {
            cancellationToken.ThrowIfCancellationRequested();
            string source = CombineAuthority(rawRoot, entry.RelativePath);
            string output = Path.Combine(session.WorkDirectory, $"smoke-{candidates:D4}.vtf");
            await session.MaterializeGeneratedAsync(
                source,
                level,
                UniversalVtfFilterRoute.NumericLinear,
                output,
                cancellationToken).ConfigureAwait(false);
            VtfContainerLayout layout = VtfContainerLayoutReader.Read(output);
            UniversalVtfDimensions expected = UniversalVtfLevels.At(
                entry.Width,
                entry.Height,
                level);
            if ((layout.Document.Width, layout.Document.Height, layout.Document.MipCount) !=
                (expected.Width, expected.Height, 1))
            {
                throw new InvalidDataException(
                    $"generated_smoke_shape:{entry.RelativePath}:{level}");
            }

            candidates++;
        }

        RequireUnchanged(before, UniversalVtfTreeInventory.Capture(rawRoot), "raw");
        WriteJson(new
        {
            status = "CLEAN",
            nomipAssets = entries.Length,
            candidates,
            treeSha256 = before.TreeSha256,
            toolchainSha256 = session.Fingerprint.Sha256
        });
        return 0;
    }

    private static async Task<int> RunBuildAsync(
        IReadOnlyDictionary<string, string> options,
        IUniversalVtfCorpusBuildService service,
        CancellationToken cancellationToken)
    {
        RequireOnly(
            options,
            "--raw-root",
            "--safe-root",
            "--output-parent",
            "--cache-root",
            "--report-root",
            "--profiles",
            "--bc-bridge",
            "--bc-bridge-sha256",
            "--runtime-caps",
            "--runtime-caps-sha256");
        RequirePair(options, "--bc-bridge", "--bc-bridge-sha256", required: true);
        RequirePair(options, "--runtime-caps", "--runtime-caps-sha256", required: false);
        string profiles = RequireOption(options, "--profiles");
        if (!profiles.Equals("all", StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("V1 build requires --profiles all.");
        }

        string rawRoot = RequireExistingDirectory(options, "--raw-root");
        string safeRoot = RequireExistingDirectory(options, "--safe-root");
        string outputParent = Path.GetFullPath(RequireOption(options, "--output-parent"));
        string cacheRoot = Path.GetFullPath(RequireOption(options, "--cache-root"));
        string reportRoot = options.TryGetValue("--report-root", out string? requestedReport)
            ? Path.GetFullPath(requestedReport)
            : outputParent;
        string bridgePath = Path.GetFullPath(RequireOption(options, "--bc-bridge"));
        string bridgeSha256 = RequireSha256(options, "--bc-bridge-sha256");
        string? capsPath = options.TryGetValue("--runtime-caps", out string? requestedCaps)
            ? Path.GetFullPath(requestedCaps)
            : null;
        string? capsSha256 = options.ContainsKey("--runtime-caps-sha256")
            ? RequireSha256(options, "--runtime-caps-sha256")
            : null;
        RequireSeparateAuthorities(rawRoot, safeRoot, outputParent, reportRoot);
        if (Overlaps(cacheRoot, rawRoot) || Overlaps(cacheRoot, safeRoot) ||
            Overlaps(cacheRoot, outputParent) || Overlaps(cacheRoot, reportRoot))
        {
            throw new ArgumentException(
                "Cache authority must be physically separate from raw, safe, output and reports.");
        }
        if (Directory.Exists(outputParent) || File.Exists(outputParent))
        {
            throw new IOException($"Output parent already exists: {outputParent}");
        }

        if (!reportRoot.Equals(outputParent, StringComparison.OrdinalIgnoreCase) &&
            (Directory.Exists(reportRoot) || File.Exists(reportRoot)))
        {
            throw new IOException($"Report root already exists: {reportRoot}");
        }

        cancellationToken.ThrowIfCancellationRequested();
        Directory.CreateDirectory(outputParent);
        Directory.CreateDirectory(reportRoot);
        UniversalVtfPolicyReceipt policy = service.ResolvePolicy(bridgeSha256);
        var request = new UniversalVtfBuildRequest(
            rawRoot,
            safeRoot,
            outputParent,
            reportRoot,
            bridgePath,
            bridgeSha256,
            capsPath,
            capsSha256,
            Enum.GetValues<UniversalVtfProfileKind>(),
            policy.PolicyFingerprint,
            cacheRoot);
        var progress = new InlineProgress<UniversalVtfBuildProgress>(item => WriteJson(new
        {
            type = "progress",
            item.Stage,
            item.Completed,
            item.Total,
            item.RelativePath,
            profile = item.Profile?.ToString().ToLowerInvariant(),
            cacheStatus = item.CacheStatus?.ToString().ToLowerInvariant(),
            item.ElapsedMilliseconds
        }));
        UniversalVtfBuildResult result = await service.BuildAsync(
            request,
            progress,
            cancellationToken).ConfigureAwait(false);
        WriteJson(new
        {
            status = "CLEAN",
            schema = "universal-vtf-build-result-v1",
            policyFingerprint = policy.PolicyFingerprint,
            toolchainSha256 = policy.ToolchainSha256,
            bcBridgeSha256 = bridgeSha256,
            runtimeCapsSha256 = capsSha256,
            cache = result.Cache,
            result.RunJournalPath,
            result.RunJournalSha256,
            profiles = new[]
            {
                Result(result.Conservative),
                Result(result.Balanced),
                Result(result.Aggressive)
            }
        });
        return 0;
    }

    private static async Task<int> RunSeedCacheAsync(
        IReadOnlyDictionary<string, string> options,
        IUniversalVtfLegacyImportService service,
        CancellationToken cancellationToken)
    {
        RequireOnly(
            options,
            "--raw-root",
            "--safe-root",
            "--bc-bridge",
            "--bc-bridge-sha256",
            "--legacy-root",
            "--legacy-build-log",
            "--legacy-build-log-sha256",
            "--conservative-manifest-sha256",
            "--balanced-manifest-sha256",
            "--aggressive-manifest-sha256",
            "--cache-root");
        string rawRoot = RequireExistingDirectory(options, "--raw-root");
        string safeRoot = RequireExistingDirectory(options, "--safe-root");
        string legacyRoot = RequireExistingDirectory(options, "--legacy-root");
        string bridgePath = RequireExistingFile(options, "--bc-bridge");
        string buildLogPath = RequireExistingFile(options, "--legacy-build-log");
        string cacheRoot = Path.GetFullPath(RequireOption(options, "--cache-root"));
        if (Overlaps(cacheRoot, rawRoot) || Overlaps(cacheRoot, safeRoot) ||
            Overlaps(cacheRoot, legacyRoot))
        {
            throw new ArgumentException(
                "Cache authority must be physically separate from raw, safe and legacy authorities.");
        }

        var manifests = new Dictionary<UniversalVtfProfileKind, string>
        {
            [UniversalVtfProfileKind.Conservative] =
                RequireSha256(options, "--conservative-manifest-sha256"),
            [UniversalVtfProfileKind.Balanced] =
                RequireSha256(options, "--balanced-manifest-sha256"),
            [UniversalVtfProfileKind.Aggressive] =
                RequireSha256(options, "--aggressive-manifest-sha256")
        };
        var request = new UniversalVtfLegacyImportRequest(
            rawRoot,
            safeRoot,
            bridgePath,
            RequireSha256(options, "--bc-bridge-sha256"),
            legacyRoot,
            buildLogPath,
            RequireSha256(options, "--legacy-build-log-sha256"),
            manifests,
            UniversalVtfCorpusBuilder.DefaultAnchors);
        var progress = new InlineProgress<UniversalVtfLegacyImportProgress>(item => WriteJson(new
        {
            type = "progress",
            stage = "seed-cache",
            item.Completed,
            item.Total,
            item.RelativePath
        }));
        UniversalVtfLegacyImportResult result = await service.ImportAsync(
            request,
            cacheRoot,
            progress,
            cancellationToken).ConfigureAwait(false);
        WriteJson(new
        {
            status = "CLEAN",
            schema = "universal-vtf-cache-seed-result-v1",
            result.TotalVtfs,
            result.ImportedVtfs,
            result.RecalculatedVtfs,
            result.UniqueFinalBlobs,
            result.ElapsedSeconds,
            cacheRoot
        });
        return 0;
    }

    private static int RunAudit(
        IReadOnlyDictionary<string, string> options,
        IUniversalVtfCorpusBuildService service,
        CancellationToken cancellationToken)
    {
        RequireOnly(
            options,
            "--raw-root",
            "--safe-root",
            "--candidate-root",
            "--manifest",
            "--manifest-sha256",
            "--bc-bridge",
            "--bc-bridge-sha256",
            "--runtime-caps",
            "--runtime-caps-sha256");
        RequirePair(options, "--bc-bridge", "--bc-bridge-sha256", required: true);
        RequirePair(options, "--runtime-caps", "--runtime-caps-sha256", required: false);
        string rawRoot = RequireExistingDirectory(options, "--raw-root");
        string safeRoot = RequireExistingDirectory(options, "--safe-root");
        string candidateRoot = RequireExistingDirectory(options, "--candidate-root");
        string manifest = Path.GetFullPath(RequireOption(options, "--manifest"));
        string manifestSha256 = RequireSha256(options, "--manifest-sha256");
        string bridge = Path.GetFullPath(RequireOption(options, "--bc-bridge"));
        string bridgeSha256 = RequireSha256(options, "--bc-bridge-sha256");
        string? caps = options.TryGetValue("--runtime-caps", out string? requestedCaps)
            ? Path.GetFullPath(requestedCaps)
            : null;
        string? capsSha256 = options.ContainsKey("--runtime-caps-sha256")
            ? RequireSha256(options, "--runtime-caps-sha256")
            : null;
        cancellationToken.ThrowIfCancellationRequested();
        UniversalVtfPolicyReceipt policy = service.ResolvePolicy(bridgeSha256);
        UniversalVtfAuditResult audit = UniversalVtfCorpusAuditor.Audit(
            new UniversalVtfAuditRequest(
                rawRoot,
                safeRoot,
                candidateRoot,
                manifest,
                manifestSha256,
                bridge,
                bridgeSha256,
                UniversalVtfCorpusBuilder.DefaultAnchors,
                caps,
                capsSha256,
                policy.PolicyFingerprint));
        WriteJson(new
        {
            status = audit.Clean ? "CLEAN" : "DIRTY",
            schema = "universal-vtf-audit-result-v1",
            issues = audit.Issues
        });
        return audit.Clean ? 0 : 5;
    }

    private static object Result(UniversalVtfBuiltProfile profile) => new
    {
        profile = profile.Profile.ToString().ToLowerInvariant(),
        profile.Root,
        profile.ManifestPath,
        profile.ManifestSha256,
        profile.Bytes,
        profile.TreeSha256
    };

    private static IReadOnlyDictionary<string, string> ParseOptions(string[] args)
    {
        if (args.Length % 2 != 0)
        {
            throw new ArgumentException("Options must be supplied as --name value pairs.");
        }

        var options = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (int index = 0; index < args.Length; index += 2)
        {
            string key = args[index];
            string value = args[index + 1];
            if (!key.StartsWith("--", StringComparison.Ordinal) ||
                string.IsNullOrWhiteSpace(value) ||
                !options.TryAdd(key, value))
            {
                throw new ArgumentException($"Invalid or duplicate option: {key}");
            }
        }

        return options;
    }

    private static void RequireOnly(
        IReadOnlyDictionary<string, string> options,
        params string[] allowed)
    {
        var allow = new HashSet<string>(allowed, StringComparer.OrdinalIgnoreCase);
        string? unknown = options.Keys.FirstOrDefault(key => !allow.Contains(key));
        if (unknown is not null)
        {
            throw new ArgumentException($"Unknown option: {unknown}");
        }
    }

    private static string RequireOption(
        IReadOnlyDictionary<string, string> options,
        string name) =>
        options.TryGetValue(name, out string? value)
            ? value
            : throw new ArgumentException($"Missing required option: {name}");

    private static string RequireSha256(
        IReadOnlyDictionary<string, string> options,
        string name)
    {
        string value = RequireOption(options, name);
        Bc3RawBridgeStore.RequireSha256(value, name);
        return value.ToLowerInvariant();
    }

    private static int RequirePositiveInt(
        IReadOnlyDictionary<string, string> options,
        string name)
    {
        string value = RequireOption(options, name);
        if (!int.TryParse(value, out int parsed) || parsed <= 0)
        {
            throw new ArgumentException($"Invalid positive integer for {name}: {value}");
        }

        return parsed;
    }

    private static long RequirePositiveLong(
        IReadOnlyDictionary<string, string> options,
        string name)
    {
        string value = RequireOption(options, name);
        if (!long.TryParse(value, out long parsed) || parsed <= 0)
        {
            throw new ArgumentException($"Invalid positive integer for {name}: {value}");
        }

        return parsed;
    }

    private static string RequireExistingDirectory(
        IReadOnlyDictionary<string, string> options,
        string name)
    {
        string path = Path.GetFullPath(RequireOption(options, name));
        if (!Directory.Exists(path))
        {
            throw new ArgumentException($"Directory does not exist for {name}: {path}");
        }

        return path.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }

    private static string RequireExistingFile(
        IReadOnlyDictionary<string, string> options,
        string name)
    {
        string path = Path.GetFullPath(RequireOption(options, name));
        if (!File.Exists(path))
        {
            throw new ArgumentException($"File does not exist for {name}: {path}");
        }

        return path;
    }

    private static void RequirePair(
        IReadOnlyDictionary<string, string> options,
        string path,
        string hash,
        bool required)
    {
        bool hasPath = options.ContainsKey(path);
        bool hasHash = options.ContainsKey(hash);
        if (hasPath != hasHash || required && !hasPath)
        {
            throw new ArgumentException($"Options {path} and {hash} must be supplied as a pair.");
        }
    }

    private static void RequireSeparateAuthorities(
        string raw,
        string safe,
        string output,
        string reports)
    {
        if (Overlaps(raw, safe) || Overlaps(output, raw) || Overlaps(output, safe) ||
            Overlaps(reports, raw) || Overlaps(reports, safe))
        {
            throw new ArgumentException(
                "Raw, safe, output and report authorities must be physically separate.");
        }
    }

    private static bool Overlaps(string left, string right) =>
        IsWithin(left, right) || IsWithin(right, left);

    private static bool IsWithin(string path, string root)
    {
        string fullRoot = Path.GetFullPath(root).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string fullPath = Path.GetFullPath(path).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        return fullPath.Equals(fullRoot, StringComparison.OrdinalIgnoreCase) ||
               fullPath.StartsWith(
                   fullRoot + Path.DirectorySeparatorChar,
                   StringComparison.OrdinalIgnoreCase);
    }

    private static string CombineAuthority(string root, string relativePath)
    {
        string path = Path.GetFullPath(Path.Combine(
            root,
            relativePath.Replace('/', Path.DirectorySeparatorChar)));
        if (!IsWithin(path, root))
        {
            throw new InvalidDataException($"relative_path_escape:{relativePath}");
        }

        return path;
    }

    private static void RequireUnchanged(
        UniversalVtfTreeSnapshot before,
        UniversalVtfTreeSnapshot after,
        string authority)
    {
        if (before.Files.Count != after.Files.Count ||
            before.TotalBytes != after.TotalBytes ||
            !before.TreeSha256.Equals(after.TreeSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"{authority}_authority_changed");
        }
    }

    private static int ClassifyInvalidData(string command, string message)
    {
        if (command == "audit" || message.Contains("audit", StringComparison.OrdinalIgnoreCase))
        {
            return 5;
        }

        if (command is "inventory" or "bc3-bridge" ||
            message.StartsWith("anchor:", StringComparison.OrdinalIgnoreCase) ||
            message.Contains("inventory", StringComparison.OrdinalIgnoreCase) ||
            message.Contains("census", StringComparison.OrdinalIgnoreCase) ||
            message.Contains("bridge_authority", StringComparison.OrdinalIgnoreCase) ||
            message.Contains("bridge_frozen", StringComparison.OrdinalIgnoreCase))
        {
            return 3;
        }

        return 4;
    }

    private static string Label(int code) => code switch
    {
        3 => "ANCHOR_OR_CENSUS_ERROR",
        4 => "TRANSFORM_ERROR",
        5 => "AUDIT_ERROR",
        _ => "ERROR"
    };

    private static void WriteJson(object value) =>
        Console.WriteLine(JsonSerializer.Serialize(value, JsonOptions));

    private static void PrintHelp()
    {
        Console.WriteLine("Universal VTF V1 corpus driver");
        Console.WriteLine("  toolchain");
        Console.WriteLine("  inventory --raw-root <dir> --output <json>");
        Console.WriteLine(
            "  bc3-bridge --raw-root <dir> --safe-root <dir> " +
            "--prior-manifest <json> --output <json>");
        Console.WriteLine("  generated-smoke --raw-root <dir>");
        Console.WriteLine(
            "  build --raw-root <dir> --safe-root <dir> --bc-bridge <json> " +
            "--bc-bridge-sha256 <sha256> --output-parent <new-dir> " +
            "--cache-root <dir> --profiles all " +
            "[--report-root <new-dir>] [--runtime-caps <json> " +
            "--runtime-caps-sha256 <sha256>]");
        Console.WriteLine(
            "  seed-cache --raw-root <dir> --safe-root <dir> --bc-bridge <json> " +
            "--bc-bridge-sha256 <sha256> --legacy-root <dir> " +
            "--legacy-build-log <jsonl> --legacy-build-log-sha256 <sha256> " +
            "--conservative-manifest-sha256 <sha256> " +
            "--balanced-manifest-sha256 <sha256> " +
            "--aggressive-manifest-sha256 <sha256> --cache-root <dir>");
        Console.WriteLine(
            "  compose-monotonic --profile <profile> --incumbent-root <dir> " +
            "--incumbent-name <name> --incumbent-file-count <n> " +
            "--incumbent-bytes <n> --incumbent-tree-sha256 <sha256> " +
            "--raw-root <frozen-dir> --safe-root <frozen-dir> " +
            "--alternative-name <name> --alternative-root <dir> " +
            "--alternative-manifest <json> --alternative-manifest-sha256 <sha256> " +
            "--output-root <new-dir> --manifest <new-json>");
        Console.WriteLine(
            "  audit --raw-root <dir> --safe-root <dir> --candidate-root <dir> " +
            "--manifest <json> --manifest-sha256 <sha256> --bc-bridge <json> " +
            "--bc-bridge-sha256 <sha256> [--runtime-caps <json> " +
            "--runtime-caps-sha256 <sha256>]");
        Console.WriteLine(
            "Exit codes: 0 CLEAN; 2 args; 3 anchor/census; 4 transform; " +
            "5 audit; 130 cancelled.");
    }

    private sealed class InlineProgress<T> : IProgress<T>
    {
        private readonly Action<T> _report;

        public InlineProgress(Action<T> report)
        {
            _report = report;
        }

        public void Report(T value) => _report(value);
    }
}
