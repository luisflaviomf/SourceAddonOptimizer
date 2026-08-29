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
        CancellationToken cancellationToken = default)
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
                                      OverflowException or JsonException)
        {
            int code = command == "audit" ? 5 : 4;
            Console.Error.WriteLine($"{Label(code)} {error.Message}");
            return code;
        }
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
            policy.PolicyFingerprint);
        var progress = new InlineProgress<UniversalVtfBuildProgress>(item => WriteJson(new
        {
            type = "progress",
            item.Stage,
            item.Completed,
            item.Total,
            item.RelativePath,
            profile = item.Profile?.ToString().ToLowerInvariant()
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
            profiles = new[]
            {
                Result(result.Conservative),
                Result(result.Balanced),
                Result(result.Aggressive)
            }
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
            "--bc-bridge-sha256 <sha256> --output-parent <new-dir> --profiles all " +
            "[--report-root <new-dir>] [--runtime-caps <json> " +
            "--runtime-caps-sha256 <sha256>]");
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
