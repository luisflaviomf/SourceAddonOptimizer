using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Companion;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

static void Assert(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}

static void ExpectFailure(Action action, string expected)
{
    try
    {
        action();
    }
    catch (InvalidOperationException ex) when (ex.Message.Contains(expected, StringComparison.OrdinalIgnoreCase))
    {
        return;
    }

    throw new InvalidOperationException($"Expected failure containing '{expected}'.");
}

static string Digest(byte[] payload) =>
    Convert.ToHexString(SHA256.HashData(payload)).ToLowerInvariant();

static byte[] BuildPackage(string version, IReadOnlyDictionary<string, byte[]> files)
{
    var sorted = new SortedDictionary<string, byte[]>(StringComparer.Ordinal);
    foreach ((string path, byte[] payload) in files)
        sorted.Add(path, payload);
    byte[] manifest = JsonSerializer.SerializeToUtf8Bytes(new
    {
        schemaVersion = 1,
        packageVersion = version,
        addonDirectory = "sourceaddonoptimizer_companion",
        files = sorted.Select(pair => new
        {
            path = pair.Key,
            bytes = pair.Value.LongLength,
            sha256 = Digest(pair.Value),
        }).ToArray(),
    });

    using var output = new MemoryStream();
    using (var archive = new ZipArchive(output, ZipArchiveMode.Create, leaveOpen: true))
    {
        using (Stream stream = archive.CreateEntry(
            CompanionPackageInstaller.PackageManifestName,
            CompressionLevel.Fastest
        ).Open())
        {
            stream.Write(manifest);
        }

        foreach ((string path, byte[] payload) in sorted)
        {
            using Stream stream = archive.CreateEntry(
                "sourceaddonoptimizer_companion/" + path,
                CompressionLevel.Fastest
            ).Open();
            stream.Write(payload);
        }
    }

    return output.ToArray();
}

static byte[] AddEntry(byte[] source, string path, byte[] payload)
{
    using var input = new MemoryStream(source, writable: false);
    using var original = new ZipArchive(input, ZipArchiveMode.Read, leaveOpen: false);
    using var output = new MemoryStream();
    using (var replacement = new ZipArchive(output, ZipArchiveMode.Create, leaveOpen: true))
    {
        foreach (ZipArchiveEntry entry in original.Entries)
        {
            using Stream from = entry.Open();
            using Stream to = replacement.CreateEntry(entry.FullName, CompressionLevel.Fastest).Open();
            from.CopyTo(to);
        }

        using Stream added = replacement.CreateEntry(path, CompressionLevel.Fastest).Open();
        added.Write(payload);
    }

    return output.ToArray();
}

static string CreateGameRoot(string parent, string name)
{
    string root = Path.Combine(parent, name);
    Directory.CreateDirectory(Path.Combine(root, "garrysmod", "addons"));
    File.WriteAllBytes(Path.Combine(root, "gmod.exe"), Array.Empty<byte>());
    return root;
}

var firstFiles = new SortedDictionary<string, byte[]>(StringComparer.Ordinal)
{
    ["addon.json"] = Encoding.UTF8.GetBytes("{\"title\":\"Companion\"}"),
    ["lua/autorun/sourceaddonoptimizer_loader.lua"] = Encoding.UTF8.GetBytes("SAO = SAO or {}\n"),
    ["lua/sourceaddonoptimizer/shared/old.lua"] = Encoding.UTF8.GetBytes("return 'old'\n"),
};
var secondFiles = new SortedDictionary<string, byte[]>(StringComparer.Ordinal)
{
    ["addon.json"] = Encoding.UTF8.GetBytes("{\"title\":\"Companion v2\"}"),
    ["lua/autorun/sourceaddonoptimizer_loader.lua"] = Encoding.UTF8.GetBytes("SAO = SAO or { version = 2 }\n"),
    ["lua/sourceaddonoptimizer/shared/new.lua"] = Encoding.UTF8.GetBytes("return 'new'\n"),
};
byte[] firstPackage = BuildPackage("1.0.0", firstFiles);
byte[] secondPackage = BuildPackage("1.1.0", secondFiles);
string testRoot = Path.Combine(Path.GetTempPath(), "gaco-companion-contract-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(testRoot);

try
{
    string freshGame = CreateGameRoot(testRoot, "fresh");
    Assert(CompanionPathPolicy.DetectGarrysModRoot(freshGame) == Path.GetFullPath(freshGame), "Explicit game root was not preferred.");
    CompanionInstallStatus notInstalled = CompanionPackageInstaller.Inspect(firstPackage, freshGame);
    Assert(notInstalled.State == CompanionInstallState.NotInstalled, "Missing addon did not present Not installed.");
    Assert(notInstalled.CanInstall && !notInstalled.CanUpdate && !notInstalled.CanRemove, "Not installed actions are unsafe.");
    string destination = CompanionPathPolicy.ResolveAddonDestination(freshGame);
    CompanionInstallResult fresh = CompanionPackageInstaller.Install(firstPackage, freshGame, TimeSpan.FromSeconds(5));
    Assert(fresh.Disposition == CompanionInstallDisposition.Installed, "Fresh install did not report Installed.");
    Assert(fresh.Destination == destination, "Fresh install used the wrong destination.");
    Assert(File.Exists(Path.Combine(destination, CompanionPackageInstaller.OwnershipMarkerName)), "Ownership marker is missing.");
    Assert(File.ReadAllBytes(Path.Combine(destination, "lua", "sourceaddonoptimizer", "shared", "old.lua")).SequenceEqual(firstFiles["lua/sourceaddonoptimizer/shared/old.lua"]), "Fresh payload differs from package.");
    CompanionInstallStatus compatible = CompanionPackageInstaller.Inspect(firstPackage, freshGame);
    Assert(compatible.State == CompanionInstallState.Compatible && compatible.StatusText == "Compatible", "Current owned addon did not present Compatible.");
    Assert(!compatible.CanInstall && !compatible.CanUpdate && compatible.CanRemove, "Compatible actions are unsafe.");
    CompanionInstallStatus updateAvailable = CompanionPackageInstaller.Inspect(secondPackage, freshGame);
    Assert(updateAvailable.State == CompanionInstallState.UpdateAvailable && updateAvailable.StatusText == "Update available", "Older owned addon did not present Update available.");
    Assert(!updateAvailable.CanInstall && updateAvailable.CanUpdate && updateAvailable.CanRemove, "Update actions are unsafe.");

    string dataEvidence = Path.Combine(freshGame, "garrysmod", "data", "sourceaddonoptimizer", "profiles", "keep.json");
    Directory.CreateDirectory(Path.GetDirectoryName(dataEvidence)!);
    File.WriteAllText(dataEvidence, "preserve");
    string unknown = Path.Combine(destination, "operator-notes.txt");
    File.WriteAllText(unknown, "keep this unknown file");
    CompanionInstallResult update = CompanionPackageInstaller.Install(secondPackage, freshGame, TimeSpan.FromSeconds(5));
    Assert(update.Disposition == CompanionInstallDisposition.Updated, "Owned update did not report Updated.");
    Assert(File.Exists(Path.Combine(destination, "lua", "sourceaddonoptimizer", "shared", "new.lua")), "Updated payload is missing.");
    Assert(!File.Exists(Path.Combine(destination, "lua", "sourceaddonoptimizer", "shared", "old.lua")), "Retired declared payload was retained.");
    Assert(File.ReadAllText(unknown) == "keep this unknown file", "Owned update changed an unknown file.");
    Assert(File.ReadAllText(dataEvidence) == "preserve", "Owned update changed game data evidence.");

    string unknownGame = CreateGameRoot(testRoot, "unknown-destination");
    string unknownDestination = CompanionPathPolicy.ResolveAddonDestination(unknownGame);
    Directory.CreateDirectory(unknownDestination);
    File.WriteAllText(Path.Combine(unknownDestination, "foreign.txt"), "not ours");
    CompanionInstallStatus unknownStatus = CompanionPackageInstaller.Inspect(firstPackage, unknownGame);
    Assert(unknownStatus.State == CompanionInstallState.UnknownFolder && unknownStatus.StatusText == "Unknown folder", "Foreign destination did not present Unknown folder.");
    Assert(!unknownStatus.CanInstall && !unknownStatus.CanUpdate && !unknownStatus.CanRemove, "Unknown folder exposed a destructive action.");
    ExpectFailure(() => CompanionPackageInstaller.Install(firstPackage, unknownGame), "unknown");

    string incompatibleGame = CreateGameRoot(testRoot, "incompatible");
    string incompatibleDestination = CompanionPathPolicy.ResolveAddonDestination(incompatibleGame);
    Directory.CreateDirectory(incompatibleDestination);
    File.WriteAllText(
        Path.Combine(incompatibleDestination, CompanionPackageInstaller.OwnershipMarkerName),
        "{\"schemaVersion\":2,\"packageVersion\":\"2.0.0\",\"packageSha256\":\"" + new string('0', 64) + "\",\"files\":[]}"
    );
    CompanionInstallStatus incompatible = CompanionPackageInstaller.Inspect(firstPackage, incompatibleGame);
    Assert(incompatible.State == CompanionInstallState.Incompatible && incompatible.StatusText == "Incompatible", "Unsupported ownership schema did not present Incompatible.");
    Assert(!incompatible.CanInstall && !incompatible.CanUpdate && !incompatible.CanRemove, "Incompatible addon exposed a destructive action.");

    string missingMarkerGame = CreateGameRoot(testRoot, "missing-marker");
    CompanionPackageInstaller.Install(firstPackage, missingMarkerGame);
    File.Delete(Path.Combine(CompanionPathPolicy.ResolveAddonDestination(missingMarkerGame), CompanionPackageInstaller.OwnershipMarkerName));
    ExpectFailure(() => CompanionPackageInstaller.Install(secondPackage, missingMarkerGame), "ownership");

    string tamperedMarkerGame = CreateGameRoot(testRoot, "tampered-marker");
    CompanionPackageInstaller.Install(firstPackage, tamperedMarkerGame);
    File.WriteAllText(
        Path.Combine(CompanionPathPolicy.ResolveAddonDestination(tamperedMarkerGame), CompanionPackageInstaller.OwnershipMarkerName),
        "{}"
    );
    ExpectFailure(() => CompanionPackageInstaller.Install(secondPackage, tamperedMarkerGame), "ownership");

    string oversizedMarkerGame = CreateGameRoot(testRoot, "oversized-marker");
    CompanionPackageInstaller.Install(firstPackage, oversizedMarkerGame);
    File.WriteAllBytes(
        Path.Combine(CompanionPathPolicy.ResolveAddonDestination(oversizedMarkerGame), CompanionPackageInstaller.OwnershipMarkerName),
        new byte[1_000_001]
    );
    CompanionInstallStatus oversizedMarkerStatus = CompanionPackageInstaller.Inspect(firstPackage, oversizedMarkerGame);
    Assert(oversizedMarkerStatus.State == CompanionInstallState.UnknownFolder, "Oversized ownership marker did not close installation actions.");
    ExpectFailure(() => CompanionPackageInstaller.Install(secondPackage, oversizedMarkerGame), "ownership marker exceeds");

    string traversalGame = CreateGameRoot(testRoot, "traversal");
    byte[] traversalPackage = AddEntry(firstPackage, "sourceaddonoptimizer_companion/../escape.lua", Encoding.UTF8.GetBytes("escape"));
    ExpectFailure(() => CompanionPackageInstaller.Install(traversalPackage, traversalGame), "unsafe");
    Assert(!File.Exists(Path.Combine(traversalGame, "garrysmod", "addons", "escape.lua")), "Traversal payload escaped the destination.");

    string oversizedGame = CreateGameRoot(testRoot, "oversized");
    var oversizedFiles = new SortedDictionary<string, byte[]>(firstFiles, StringComparer.Ordinal)
    {
        ["lua/sourceaddonoptimizer/shared/large-a.lua"] = new byte[11_000_000],
        ["lua/sourceaddonoptimizer/shared/large-b.lua"] = new byte[11_000_000],
    };
    byte[] oversizedPackage = BuildPackage("1.0.0", oversizedFiles);
    ExpectFailure(() => CompanionPackageInstaller.Install(oversizedPackage, oversizedGame), "byte limit");

    string uppercaseGame = CreateGameRoot(testRoot, "uppercase");
    byte[] uppercasePackage = BuildPackage("1.0.0", new SortedDictionary<string, byte[]>(firstFiles, StringComparer.Ordinal)
    {
        ["lua/Bad.lua"] = Encoding.UTF8.GetBytes("bad"),
    });
    ExpectFailure(() => CompanionPackageInstaller.Install(uppercasePackage, uppercaseGame), "lowercase");

    string interruptedGame = CreateGameRoot(testRoot, "interrupted");
    string interrupted = Path.Combine(interruptedGame, "garrysmod", "addons", ".sourceaddonoptimizer_companion.123.11111111111111111111111111111111.staging");
    Directory.CreateDirectory(interrupted);
    File.WriteAllText(
        Path.Combine(interrupted, CompanionPackageInstaller.StagingOwnershipMarkerName),
        CompanionPackageInstaller.StagingOwnershipMarkerContent
    );
    File.WriteAllText(Path.Combine(interrupted, "stale.txt"), "stale");
    string foreignStaging = Path.Combine(interruptedGame, "garrysmod", "addons", ".sourceaddonoptimizer_companion.456.22222222222222222222222222222222.staging");
    Directory.CreateDirectory(foreignStaging);
    File.WriteAllText(Path.Combine(foreignStaging, "foreign.txt"), "not installer owned");
    CompanionPackageInstaller.Install(firstPackage, interruptedGame);
    Assert(!Directory.Exists(interrupted), "Interrupted owned staging directory was retained.");
    Assert(Directory.Exists(foreignStaging), "A staging-like directory without ownership was removed.");

    string concurrentGame = CreateGameRoot(testRoot, "concurrent");
    Task<CompanionInstallResult>[] installs = Enumerable.Range(0, 2)
        .Select(_ => Task.Run(() => CompanionPackageInstaller.Install(firstPackage, concurrentGame, TimeSpan.FromSeconds(10))))
        .ToArray();
    Task.WaitAll(installs);
    Assert(installs.All(task => task.Result.Destination == CompanionPathPolicy.ResolveAddonDestination(concurrentGame)), "Concurrent callers selected different destinations.");
    Assert(File.Exists(Path.Combine(installs[0].Result.Destination, CompanionPackageInstaller.OwnershipMarkerName)), "Concurrent install did not leave an owned package.");

    string symlinkGame = CreateGameRoot(testRoot, "symlink");
    string addons = Path.Combine(symlinkGame, "garrysmod", "addons");
    string outside = Path.Combine(testRoot, "outside-addons");
    Directory.CreateDirectory(outside);
    Directory.Delete(addons);
    bool symlinkCreated = false;
    try
    {
        Directory.CreateSymbolicLink(addons, outside);
        symlinkCreated = true;
    }
    catch (Exception ex) when (ex is UnauthorizedAccessException || ex is IOException || ex is PlatformNotSupportedException)
    {
        Console.WriteLine("Symlink creation unavailable; reparse escape test skipped by platform: " + ex.GetType().Name);
    }
    if (symlinkCreated)
    {
        ExpectFailure(() => CompanionPackageInstaller.Install(firstPackage, symlinkGame), "reparse");
        Assert(!Directory.Exists(Path.Combine(outside, "sourceaddonoptimizer_companion")), "Reparse point escaped the addons root.");
    }
    if (OperatingSystem.IsWindows())
    {
        string? knownReparse = Directory.EnumerateFileSystemEntries(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                "*",
                SearchOption.TopDirectoryOnly
            )
            .FirstOrDefault(path => (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0);
        Assert(knownReparse != null, "The Windows reparse policy test could not find a system reparse point.");
        ExpectFailure(() => CompanionPathPolicy.AssertNotReparsePoint(knownReparse!, "test path"), "reparse");
    }

    string unknownEmptyDirectory = Path.Combine(destination, "operator-empty-directory");
    Directory.CreateDirectory(unknownEmptyDirectory);
    CompanionUninstallResult uninstall = CompanionPackageInstaller.Uninstall(freshGame, TimeSpan.FromSeconds(5));
    Assert(!uninstall.AddonDirectoryRemoved, "Uninstall recursively removed a directory containing unknown files.");
    Assert(uninstall.PreservedUnknownPaths.Contains("operator-notes.txt", StringComparer.Ordinal), "Uninstall did not report the preserved unknown file.");
    Assert(uninstall.PreservedUnknownPaths.Contains("operator-empty-directory/", StringComparer.Ordinal), "Uninstall did not report the preserved unknown directory.");
    Assert(File.Exists(unknown), "Uninstall removed an unknown file.");
    Assert(!File.Exists(Path.Combine(destination, "addon.json")), "Uninstall retained a declared file.");
    Assert(!File.Exists(Path.Combine(destination, CompanionPackageInstaller.OwnershipMarkerName)), "Uninstall retained the ownership marker.");
    Assert(File.ReadAllText(dataEvidence) == "preserve", "Uninstall changed game data evidence.");

    Assert(typeof(AppSettingsModel).GetProperty("CompanionGarrysModRoot") != null, "Companion game root is not persisted.");
    Assert(typeof(AppSettingsModel).GetProperty("CompanionLastProfilePath") != null, "Companion profile location is not persisted.");
    Assert(typeof(AppSettingsModel).GetProperty("CompanionUseWithMaximumV2") == null, "Maximum opt-in must never be persisted.");

    var context = new CompanionContext();
    Assert(!context.UseWithMaximumV2, "Maximum profile use must start disabled.");
    context.UseWithMaximumV2 = true;
    context.ResetImportedProfile("C:/profiles/example/run-index.json");
    Assert(!context.UseWithMaximumV2, "Import/reset must disable Maximum profile use.");
    Assert(context.LastProfilePath.EndsWith("run-index.json", StringComparison.Ordinal), "Profile location was not presented.");
    context.ApplyInstallStatus(compatible);
    Assert(context.InstallationStatusText == "Compatible" && context.CanRemove, "Compatible state was not presented by the WPF context.");
    Assert(!context.CanInstall && !context.CanUpdate, "WPF context exposed invalid compatible-state actions.");

    Console.WriteLine("WPF Companion contract tests passed.");
}
finally
{
    if (Directory.Exists(testRoot))
        Directory.Delete(testRoot, recursive: true);
}
