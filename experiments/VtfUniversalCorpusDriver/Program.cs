using System.Text.Json;
using GmodAddonCompressor.Systems.Vtf.Universal;

namespace VtfUniversalCorpusDriver;

internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length == 0 || args.Contains("--help", StringComparer.OrdinalIgnoreCase))
        {
            PrintHelp();
            return 0;
        }

        try
        {
            IReadOnlyDictionary<string, string> options = ParseOptions(args.Skip(1).ToArray());
            return args[0].ToLowerInvariant() switch
            {
                "inventory" => RunInventory(options),
                "bc3-bridge" => RunBc3Bridge(options),
                _ => UnknownCommand(args[0])
            };
        }
        catch (ArgumentException error)
        {
            Console.Error.WriteLine($"ARGUMENT_ERROR {error.Message}");
            return 2;
        }
        catch (InvalidDataException error)
        {
            Console.Error.WriteLine($"ANCHOR_OR_CENSUS_ERROR {error.Message}");
            return 3;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException)
        {
            Console.Error.WriteLine($"IO_ERROR {error.Message}");
            return 4;
        }
    }

    private static int RunBc3Bridge(IReadOnlyDictionary<string, string> options)
    {
        string rawRoot = Path.GetFullPath(RequireOption(options, "--raw-root"));
        string safeRoot = Path.GetFullPath(RequireOption(options, "--safe-root"));
        string priorManifest = Path.GetFullPath(RequireOption(options, "--prior-manifest"));
        string requestedOutput = Path.GetFullPath(RequireOption(options, "--output"));
        if (options.Count != 4)
        {
            throw new ArgumentException(
                "bc3-bridge accepts only --raw-root, --safe-root, --prior-manifest and --output.");
        }

        if (rawRoot.Equals(safeRoot, StringComparison.OrdinalIgnoreCase) ||
            IsWithin(requestedOutput, rawRoot) || IsWithin(requestedOutput, safeRoot))
        {
            throw new ArgumentException(
                "Raw, safe and bridge output authorities must be physically separate.");
        }

        string outputParent = Path.GetDirectoryName(requestedOutput)
            ?? throw new ArgumentException("Bridge output has no parent directory.");
        Directory.CreateDirectory(outputParent);
        string output = UniversalVtfTreeInventory.ResolveNewDestination(
            outputParent,
            Path.GetFileName(requestedOutput));

        UniversalVtfTreeSnapshot rawBefore = UniversalVtfTreeInventory.Capture(rawRoot);
        UniversalVtfTreeSnapshot safeBefore = UniversalVtfTreeInventory.Capture(safeRoot);
        UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(rawRoot);
        Bc3RawBridge bridge = Bc3ApprovedReceipt.Build(
            inventory,
            priorManifest,
            safeBefore,
            rawBefore);
        Bc3RawBridgeFrozen frozen = Bc3RawBridgeStore.WriteCanonical(output, bridge);

        RequireUnchanged(rawBefore, UniversalVtfTreeInventory.Capture(rawRoot), "raw");
        RequireUnchanged(safeBefore, UniversalVtfTreeInventory.Capture(safeRoot), "safe");
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            status = "CLEAN",
            rows = bridge.Rows.Count,
            physicalOpaqueBc3 = bridge.PhysicalOpaqueBc3Count,
            rawDxt1 = bridge.RawDxt1Count,
            semanticExceptions = bridge.SemanticExceptionBc3Count,
            equivalentPriorSafeColorChains = bridge.EquivalentPriorSafeColorChainCount,
            sha256 = frozen.Sha256,
            output = frozen.Path
        }));
        return 0;
    }

    private static int RunInventory(IReadOnlyDictionary<string, string> options)
    {
        string rawRoot = RequireOption(options, "--raw-root");
        string requestedOutput = RequireOption(options, "--output");
        if (options.Count != 2)
        {
            throw new ArgumentException("inventory accepts only --raw-root and --output.");
        }

        string fullRawRoot = Path.GetFullPath(rawRoot);
        string fullOutput = Path.GetFullPath(requestedOutput);
        string rawPrefix = fullRawRoot.TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (fullOutput.StartsWith(rawPrefix, StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("Inventory output cannot be inside the raw authority.");
        }

        string outputParent = Path.GetDirectoryName(fullOutput)
            ?? throw new ArgumentException("Inventory output has no parent directory.");
        Directory.CreateDirectory(outputParent);
        fullOutput = UniversalVtfTreeInventory.ResolveNewDestination(
            outputParent,
            Path.GetFileName(fullOutput));

        UniversalVtfTreeSnapshot snapshot = UniversalVtfTreeInventory.Capture(fullRawRoot);
        UniversalVtfInventory inventory = UniversalVtfInventoryScanner.Scan(fullRawRoot);
        UniversalVtfCorpusExpectations.LvsV1.Verify(inventory, snapshot);

        var payload = new
        {
            schema = "universal-vtf-raw-inventory-v1",
            rawRoot = fullRawRoot,
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
        var jsonOptions = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            WriteIndented = true
        };
        byte[] json = JsonSerializer.SerializeToUtf8Bytes(payload, jsonOptions);
        using (var output = new FileStream(
                   fullOutput,
                   FileMode.CreateNew,
                   FileAccess.Write,
                   FileShare.None))
        {
            output.Write(json);
            output.Flush(flushToDisk: true);
        }

        Console.WriteLine(JsonSerializer.Serialize(new
        {
            status = "CLEAN",
            vtfCount = inventory.Entries.Count,
            vtfBytes = inventory.VtfBytes,
            treeSha256 = snapshot.TreeSha256,
            output = fullOutput
        }));
        return 0;
    }

    private static IReadOnlyDictionary<string, string> ParseOptions(string[] args)
    {
        if (args.Length == 0 || args.Length % 2 != 0)
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

    private static string RequireOption(
        IReadOnlyDictionary<string, string> options,
        string name) =>
        options.TryGetValue(name, out string? value)
            ? value
            : throw new ArgumentException($"Missing required option: {name}");

    private static bool IsWithin(string path, string root)
    {
        string fullRoot = Path.GetFullPath(root).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string fullPath = Path.GetFullPath(path);
        return fullPath.Equals(fullRoot, StringComparison.OrdinalIgnoreCase) ||
               fullPath.StartsWith(fullRoot + Path.DirectorySeparatorChar,
                   StringComparison.OrdinalIgnoreCase);
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
            throw new InvalidDataException($"{authority}_authority_changed_during_bridge");
        }
    }

    private static int UnknownCommand(string command)
    {
        Console.Error.WriteLine($"Unknown command: {command}");
        return 2;
    }

    private static void PrintHelp()
    {
        Console.WriteLine("VtfUniversalCorpusDriver inventory --raw-root <dir> --output <json>");
        Console.WriteLine(
            "VtfUniversalCorpusDriver bc3-bridge --raw-root <dir> --safe-root <dir> " +
            "--prior-manifest <json> --output <json>");
    }
}
