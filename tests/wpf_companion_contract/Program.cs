using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Companion;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

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

static JsonNode CloneJson(JsonNode value) =>
    JsonNode.Parse(value.ToJsonString()) ?? throw new InvalidOperationException("Could not clone JSON fixture.");

static JsonObject LoadRuntimeProfileFixture() =>
    (JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "valid", "sao-runtime-profile-v1.json")))
        ?? throw new InvalidOperationException("Runtime profile fixture is missing."))
    .AsObject();

static JsonObject LoadResultFixture(string schema) =>
    (JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "valid", schema + ".json")))
        ?? throw new InvalidOperationException($"Result fixture is missing: {schema}."))
    .AsObject();

static byte[] JsonBytes(JsonNode value) => Encoding.UTF8.GetBytes(value.ToJsonString() + "\n");

static string WriteRuntimeBundle(string root, params JsonObject[] chunks)
{
    Directory.CreateDirectory(root);
    var declarations = new JsonArray();
    for (int index = 0; index < chunks.Length; index++)
    {
        JsonObject chunk = chunks[index];
        chunk["chunk"] = new JsonObject { ["sequence"] = index + 1, ["total"] = chunks.Length };
        byte[] bytes = JsonBytes(chunk);
        string filename = $"chunk-{index + 1:0000}.json";
        File.WriteAllBytes(Path.Combine(root, filename), bytes);
        declarations.Add(new JsonObject
        {
            ["sequence"] = index + 1,
            ["file"] = filename,
            ["bytes"] = bytes.LongLength,
            ["sha256"] = Digest(bytes),
        });
    }

    JsonObject first = chunks[0];
    var runIndex = new JsonObject
    {
        ["schema"] = "sao-run-index-v1",
        ["contract"] = CloneJson(first["contract"]!),
        ["run_id"] = first["run_id"]!.GetValue<string>(),
        ["payload_schema"] = first["schema"]!.GetValue<string>(),
        ["created_utc"] = first["created_utc"]!.GetValue<string>(),
        ["producer"] = CloneJson(first["producer"]!),
        ["environment"] = CloneJson(first["environment"]!),
        ["chunk_count"] = chunks.Length,
        ["total_bytes"] = declarations.Sum(item => item!["bytes"]!.GetValue<long>()),
        ["chunks"] = declarations,
    };
    byte[] indexBytes = JsonBytes(runIndex);
    File.WriteAllBytes(Path.Combine(root, "run-index.json"), indexBytes);
    File.WriteAllBytes(Path.Combine(root, "complete.ready.json"), JsonBytes(new JsonObject
    {
        ["schema"] = "sao-completion-v1",
        ["run_id"] = first["run_id"]!.GetValue<string>(),
        ["index_sha256"] = Digest(indexBytes),
    }));
    return Path.Combine(root, "run-index.json");
}

static void RewriteSingleProfileChunk(string bundleRoot, byte[] bytes)
{
    string chunkPath = Path.Combine(bundleRoot, "chunk-0001.json");
    File.WriteAllBytes(chunkPath, bytes);
    JsonObject index = JsonNode.Parse(File.ReadAllText(Path.Combine(bundleRoot, "run-index.json")))!.AsObject();
    JsonObject declaration = index["chunks"]![0]!.AsObject();
    declaration["bytes"] = bytes.LongLength;
    declaration["sha256"] = Digest(bytes);
    index["total_bytes"] = bytes.LongLength;
    byte[] indexBytes = JsonBytes(index);
    File.WriteAllBytes(Path.Combine(bundleRoot, "run-index.json"), indexBytes);
    JsonObject ready = JsonNode.Parse(File.ReadAllText(Path.Combine(bundleRoot, "complete.ready.json")))!.AsObject();
    ready["index_sha256"] = Digest(indexBytes);
    File.WriteAllBytes(Path.Combine(bundleRoot, "complete.ready.json"), JsonBytes(ready));
}

static void RewriteRuntimeIndex(string bundleRoot, Action<JsonObject> mutate)
{
    string indexPath = Path.Combine(bundleRoot, "run-index.json");
    JsonObject index = JsonNode.Parse(File.ReadAllText(indexPath))!.AsObject();
    mutate(index);
    byte[] indexBytes = JsonBytes(index);
    File.WriteAllBytes(indexPath, indexBytes);
    JsonObject ready = JsonNode.Parse(File.ReadAllText(Path.Combine(bundleRoot, "complete.ready.json")))!.AsObject();
    ready["index_sha256"] = Digest(indexBytes);
    File.WriteAllBytes(Path.Combine(bundleRoot, "complete.ready.json"), JsonBytes(ready));
}

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
        ExpectFailure(() => CompanionBundleReader.ReadValidationResult(knownReparse!), "reparse");
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
    Assert(!context.CanGeneratePlan, "Validation plan generation must start unavailable without an optimization result.");
    context.SetOptimizationResultAvailable(true);
    Assert(context.CanGeneratePlan, "A verified optimization result did not enable validation plan generation.");
    context.SetOptimizationResultAvailable(false);
    context.UseWithMaximumV2 = true;
    context.ResetImportedProfile("C:/profiles/example/run-index.json");
    Assert(!context.UseWithMaximumV2, "Import/reset must disable Maximum profile use.");
    Assert(context.LastProfilePath.EndsWith("run-index.json", StringComparison.Ordinal), "Profile location was not presented.");
    context.ApplyInstallStatus(compatible);
    Assert(context.InstallationStatusText == "Compatible" && context.CanRemove, "Compatible state was not presented by the WPF context.");
    Assert(!context.CanInstall && !context.CanUpdate, "WPF context exposed invalid compatible-state actions.");

    DateTimeOffset now = new(2026, 8, 16, 12, 0, 0, TimeSpan.Zero);
    JsonObject validProfile = LoadRuntimeProfileFixture();
    string validBundleRoot = Path.Combine(testRoot, "runtime-valid");
    string validIndex = WriteRuntimeBundle(validBundleRoot, validProfile);
    CompanionBundleInspection inspection = CompanionBundleReader.Inspect(validIndex, now);
    Assert(inspection.Schema == CompanionContract.RuntimeProfileSchema, "Runtime profile schema was not inspected.");
    Assert(inspection.ContractMajor == 1 && inspection.ContractMinor == 0 && inspection.IsComplete && inspection.IsCompatible, "Valid runtime metadata was not compatible.");
    CompanionRuntimeProfile validRuntime = CompanionBundleReader.ReadRuntimeProfile(validIndex, now);
    Assert(validRuntime.Models.Count == 1 && validRuntime.Materials.Count == 1, "Valid runtime profile assets were not read.");
    Assert(validRuntime.Models["models/lvs/example.mdl"].Evidence == CompanionEvidenceTier.Focused, "Runtime evidence tier was not preserved.");

    JsonObject invalidCases = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "invalid", "cases.json")))!.AsObject();
    foreach (JsonNode? unsafePath in invalidCases["unsafe_paths"]!.AsArray())
        ExpectFailure(() => CompanionContract.NormalizeVirtualPath(unsafePath!.GetValue<string>()), "virtual path");
    foreach ((string source, JsonNode? expected) in invalidCases["safe_paths"]!.AsObject())
        Assert(CompanionContract.NormalizeVirtualPath(source) == expected!.GetValue<string>(), $"Safe path normalization differs for {source}.");

    string unknownPayloadRoot = Path.Combine(testRoot, "runtime-unknown-payload");
    JsonObject unknownPayload = (JsonObject)CloneJson(LoadRuntimeProfileFixture());
    unknownPayload["payload"]!["unexpected"] = true;
    string unknownPayloadIndex = WriteRuntimeBundle(unknownPayloadRoot, unknownPayload);
    ExpectFailure(() => CompanionBundleReader.ReadRuntimeProfile(unknownPayloadIndex, now), "unknown fields");

    string unknownIndexRoot = Path.Combine(testRoot, "runtime-unknown-index");
    string unknownIndex = WriteRuntimeBundle(unknownIndexRoot, (JsonObject)CloneJson(LoadRuntimeProfileFixture()));
    RewriteRuntimeIndex(unknownIndexRoot, index => index["unexpected"] = true);
    ExpectFailure(() => CompanionBundleReader.Inspect(unknownIndex, now), "unknown fields");

    string incompleteRoot = Path.Combine(testRoot, "runtime-incomplete");
    string incompleteIndex = WriteRuntimeBundle(incompleteRoot, (JsonObject)CloneJson(LoadRuntimeProfileFixture()));
    File.Delete(Path.Combine(incompleteRoot, "complete.ready.json"));
    ExpectFailure(() => CompanionBundleReader.Inspect(incompleteIndex, now), "incomplete");

    string indexHashRoot = Path.Combine(testRoot, "runtime-index-hash");
    string indexHashIndex = WriteRuntimeBundle(indexHashRoot, (JsonObject)CloneJson(LoadRuntimeProfileFixture()));
    File.AppendAllText(indexHashIndex, " ");
    ExpectFailure(() => CompanionBundleReader.Inspect(indexHashIndex, now), "hash");

    string chunkHashRoot = Path.Combine(testRoot, "runtime-chunk-hash");
    string chunkHashIndex = WriteRuntimeBundle(chunkHashRoot, (JsonObject)CloneJson(LoadRuntimeProfileFixture()));
    File.AppendAllText(Path.Combine(chunkHashRoot, "chunk-0001.json"), " ");
    ExpectFailure(() => CompanionBundleReader.ReadRuntimeProfile(chunkHashIndex, now), "size");

    string newerMinorRoot = Path.Combine(testRoot, "runtime-newer-minor");
    string newerMinorIndex = WriteRuntimeBundle(newerMinorRoot, (JsonObject)CloneJson(LoadRuntimeProfileFixture()));
    RewriteRuntimeIndex(newerMinorRoot, index => index["contract"]!["minor"] = 1);
    File.Delete(Path.Combine(newerMinorRoot, "chunk-0001.json"));
    CompanionBundleInspection newerMinor = CompanionBundleReader.Inspect(newerMinorIndex, now);
    Assert(!newerMinor.IsCompatible && newerMinor.ContractMajor == 1 && newerMinor.ContractMinor == 1, "Newer minor metadata was not inspectable/incompatible.");
    ExpectFailure(() => CompanionBundleReader.ReadRuntimeProfile(newerMinorIndex, now), "unsupported contract");

    string newerMajorRoot = Path.Combine(testRoot, "runtime-newer-major");
    string newerMajorIndex = WriteRuntimeBundle(newerMajorRoot, (JsonObject)CloneJson(LoadRuntimeProfileFixture()));
    RewriteRuntimeIndex(newerMajorRoot, index => index["contract"]!["major"] = 2);
    Assert(!CompanionBundleReader.Inspect(newerMajorIndex, now).IsCompatible, "Unsupported major metadata appeared compatible.");
    ExpectFailure(() => CompanionBundleReader.ReadRuntimeProfile(newerMajorIndex, now), "unsupported contract");

    string oversizedDeclarationRoot = Path.Combine(testRoot, "runtime-oversized-declaration");
    string oversizedDeclarationIndex = WriteRuntimeBundle(oversizedDeclarationRoot, (JsonObject)CloneJson(LoadRuntimeProfileFixture()));
    RewriteRuntimeIndex(oversizedDeclarationRoot, index =>
    {
        index["chunks"]![0]!["bytes"] = CompanionContract.MaxChunkBytes + 1;
        index["total_bytes"] = CompanionContract.MaxChunkBytes + 1;
    });
    File.WriteAllText(Path.Combine(oversizedDeclarationRoot, "chunk-0001.json"), "not json");
    ExpectFailure(() => CompanionBundleReader.ReadRuntimeProfile(oversizedDeclarationIndex, now), "byte limit");

    string nonFiniteRoot = Path.Combine(testRoot, "runtime-nonfinite");
    string nonFiniteIndex = WriteRuntimeBundle(nonFiniteRoot, (JsonObject)CloneJson(LoadRuntimeProfileFixture()));
    string nonFiniteText = File.ReadAllText(Path.Combine(nonFiniteRoot, "chunk-0001.json")).Replace("0.15", "1e400", StringComparison.Ordinal);
    RewriteSingleProfileChunk(nonFiniteRoot, Encoding.UTF8.GetBytes(nonFiniteText));
    ExpectFailure(() => CompanionBundleReader.ReadRuntimeProfile(nonFiniteIndex, now), "finite");

    string duplicateKeyRoot = Path.Combine(testRoot, "runtime-duplicate-key");
    string duplicateKeyIndex = WriteRuntimeBundle(duplicateKeyRoot, (JsonObject)CloneJson(LoadRuntimeProfileFixture()));
    string duplicateKeyText = File.ReadAllText(Path.Combine(duplicateKeyRoot, "chunk-0001.json")).Replace(
        "\"schema\":\"sao-runtime-profile-v1\"",
        "\"schema\":\"sao-runtime-profile-v1\",\"schema\":\"sao-runtime-profile-v1\"",
        StringComparison.Ordinal
    );
    RewriteSingleProfileChunk(duplicateKeyRoot, Encoding.UTF8.GetBytes(duplicateKeyText));
    ExpectFailure(() => CompanionBundleReader.ReadRuntimeProfile(duplicateKeyIndex, now), "duplicate JSON key");

    string duplicateIdRoot = Path.Combine(testRoot, "runtime-duplicate-id");
    JsonObject duplicateA = (JsonObject)CloneJson(LoadRuntimeProfileFixture());
    JsonObject duplicateB = (JsonObject)CloneJson(LoadRuntimeProfileFixture());
    duplicateB["payload"]!["materials"] = new JsonArray();
    string duplicateIdIndex = WriteRuntimeBundle(duplicateIdRoot, duplicateA, duplicateB);
    ExpectFailure(() => CompanionBundleReader.ReadRuntimeProfile(duplicateIdIndex, now), "duplicate id across chunks");

    string futureRoot = Path.Combine(testRoot, "runtime-future");
    JsonObject futureProfile = (JsonObject)CloneJson(LoadRuntimeProfileFixture());
    futureProfile["created_utc"] = "2999-01-01T00:00:00Z";
    string futureIndex = WriteRuntimeBundle(futureRoot, futureProfile);
    ExpectFailure(() => CompanionBundleReader.Inspect(futureIndex, now), "future");

    JsonObject mergeA = (JsonObject)CloneJson(LoadRuntimeProfileFixture());
    mergeA["payload"]!["models"]![0]!["evidence"] = "registered";
    mergeA["payload"]!["models"]![0]!["skins"] = new JsonArray(0);
    mergeA["payload"]!["models"]![0]!["bodygroups"] = new JsonArray(new JsonObject
    {
        ["name"] = "wheels", ["value"] = 0, ["evidence"] = "instantiated",
    });
    mergeA["payload"]!["models"]![0]!["submaterials"] = new JsonArray();
    mergeA["payload"]!["models"]![0]!["materials"] = new JsonArray();
    mergeA["payload"]!["materials"] = new JsonArray();
    string mergeAIndex = WriteRuntimeBundle(Path.Combine(testRoot, "runtime-merge-a"), mergeA);

    JsonObject mergeB = (JsonObject)CloneJson(LoadRuntimeProfileFixture());
    mergeB["run_id"] = "22222222-2222-4222-8222-222222222222";
    mergeB["payload"]!["models"]![0]!["skins"] = new JsonArray(1, 2);
    mergeB["payload"]!["models"]![0]!["bodygroups"] = new JsonArray(
        new JsonObject { ["name"] = "wheels", ["value"] = 0, ["evidence"] = "focused" },
        new JsonObject { ["name"] = "spoiler", ["value"] = 1, ["evidence"] = "visible" }
    );
    ((JsonArray)mergeB["payload"]!["models"]!.AsArray()).Add(new JsonObject
    {
        ["id"] = "model-external",
        ["path"] = "models/framework/external.mdl",
        ["evidence"] = "focused",
        ["skins"] = new JsonArray(),
        ["bodygroups"] = new JsonArray(),
        ["submaterials"] = new JsonArray(),
        ["materials"] = new JsonArray(),
    });
    string mergeBIndex = WriteRuntimeBundle(Path.Combine(testRoot, "runtime-merge-b"), mergeB);

    string selectedAddon = Path.Combine(testRoot, "selected-addon");
    string selectedModel = Path.Combine(selectedAddon, "models", "LVS", "Example.mdl");
    string selectedMaterial = Path.Combine(selectedAddon, "materials", "Models", "LVS", "Tire.vmt");
    Directory.CreateDirectory(Path.GetDirectoryName(selectedModel)!);
    Directory.CreateDirectory(Path.GetDirectoryName(selectedMaterial)!);
    File.WriteAllBytes(selectedModel, Array.Empty<byte>());
    File.WriteAllBytes(selectedMaterial, Array.Empty<byte>());
    CompanionProfileImportResult merged = CompanionProfileService.Import(
        new[] { mergeAIndex, mergeBIndex }, selectedAddon, now, CancellationToken.None
    );
    Assert(merged.IsEligible && !merged.IsStale, "Valid matched profiles were not eligible.");
    Assert(merged.ObservedModelCount == 2 && merged.MatchedModelCount == 1 && merged.MatchedMaterialCount == 1, "Profile counts were added or matched incorrectly.");
    Assert(merged.IgnoredExternalAssetCount == 1, "External framework asset was not ignored.");
    CompanionObservedModel mergedModel = merged.Profile.Models["models/lvs/example.mdl"];
    Assert(mergedModel.Evidence == CompanionEvidenceTier.Focused, "Maximum model evidence tier did not win.");
    Assert(mergedModel.Skins.SetEquals(new[] { 0, 1, 2 }), "Model skins were not merged by set union.");
    Assert(mergedModel.Bodygroups["wheels=0"] == CompanionEvidenceTier.Focused && mergedModel.Bodygroups.ContainsKey("spoiler=1"), "Bodygroups were not unioned with maximum evidence.");
    Assert(mergedModel.Submaterials.ContainsKey("0=materials/models/lvs/tire.vmt"), "Submaterials were not unioned.");
    Assert(mergedModel.Materials.SetEquals(new[] { "materials/models/lvs/tire.vmt" }), "Model materials were not unioned.");
    Assert(mergedModel.DisplayPath == "models/LVS/Example.mdl", "Addon filesystem casing was not preserved for display.");
    Assert(!merged.Profile.Models.ContainsKey("models/framework/external.mdl"), "External model leaked into the intersected profile.");
    Assert(!merged.Profile.Models.ContainsKey("models/not-observed.mdl"), "Absence was represented as negative evidence.");
    Assert(merged.OriginText.Contains("2 profile", StringComparison.OrdinalIgnoreCase) && merged.AgeText.Length > 0, "Origin or age presentation is missing.");

    string snapshotWork = Path.Combine(testRoot, "work with spaces");
    CompanionRuntimeProfileSnapshot snapshot = CompanionRuntimeProfileSnapshotService.Create(
        new[] { mergeAIndex, mergeBIndex },
        selectedAddon,
        snapshotWork,
        now,
        CancellationToken.None
    );
    Assert(snapshot.ImportResult.IsEligible, "Run-start snapshot did not revalidate profile eligibility.");
    Assert(Directory.Exists(snapshot.BundleRoot), "Run-start profile snapshot was not created.");
    Assert(Path.GetDirectoryName(snapshot.BundleRoot)!.StartsWith(Path.GetFullPath(snapshotWork), StringComparison.OrdinalIgnoreCase), "Profile snapshot escaped its run work directory.");
    CompanionRuntimeProfile snapshottedRuntime = CompanionBundleReader.ReadRuntimeProfile(snapshot.BundleRoot, now);
    Assert(snapshottedRuntime.Models.Count == 1 && snapshottedRuntime.Materials.Count == 1, "Merged snapshot did not retain the matched evidence set.");
    Assert(snapshottedRuntime.Models["models/lvs/example.mdl"].Skins.SetEquals(new[] { 0, 1, 2 }), "Merged snapshot lost observed skins.");
    File.Delete(Path.Combine(Path.GetDirectoryName(mergeAIndex)!, "chunk-0001.json"));
    Assert(CompanionBundleReader.ReadRuntimeProfile(snapshot.BundleRoot, now).Models.Count == 1, "Changing the game-side profile altered an active run snapshot.");

    string corruptSnapshotRoot = Path.Combine(testRoot, "runtime-corrupt-snapshot-source");
    string corruptSnapshotIndex = WriteRuntimeBundle(corruptSnapshotRoot, (JsonObject)CloneJson(LoadRuntimeProfileFixture()));
    File.AppendAllText(Path.Combine(corruptSnapshotRoot, "chunk-0001.json"), " ");
    string rejectedSnapshotWork = Path.Combine(testRoot, "rejected-snapshot-work");
    ExpectFailure(
        () => CompanionRuntimeProfileSnapshotService.Create(
            new[] { corruptSnapshotIndex }, selectedAddon, rejectedSnapshotWork, now, CancellationToken.None
        ),
        "size"
    );
    Assert(
        !Directory.Exists(rejectedSnapshotWork)
        || !Directory.EnumerateFiles(rejectedSnapshotWork, "complete.ready.json", SearchOption.AllDirectories).Any(),
        "A rejected source left a selectable complete snapshot."
    );

    string maximumReportRoot = Path.Combine(testRoot, "maximum-report");
    Directory.CreateDirectory(Path.Combine(maximumReportRoot, "logs"));
    string maximumReportPath = Path.Combine(maximumReportRoot, "logs", "maximum_adaptive_report.json");
    File.WriteAllText(maximumReportPath, "{\"schema\":1,\"family_status\":\"optimized\"}\n");
    CompanionMaximumRuntimeSummary unusedSummary = CompanionMaximumReportReader.Read(maximumReportPath);
    Assert(!unusedSummary.EvidenceApplied, "A report without runtime evidence was labelled as used.");

    File.WriteAllText(maximumReportPath, JsonSerializer.Serialize(new
    {
        schema = 1,
        family_status = "optimized",
        runtime_profile = new
        {
            bundle_sha256 = new string('a', 64),
            run_id = "11111111-1111-4111-8111-111111111111",
            matched_models = 2,
            matched_materials = 3,
            matched_regions = 2,
            scheduled_validations = 1,
            regional_recoveries = 1,
            regions = new object[]
            {
                new { key = "wheel", geometric_risk = 0.20, effective_risk = 0.34, runtime_importance = 0.50, reasons = new[] { "bodygroup" }, scheduled_validation = true, regional_recovery = true },
                new { key = "glass", geometric_risk = 0.70, effective_risk = 0.75, runtime_importance = 0.40, reasons = new[] { "model" }, scheduled_validation = false, regional_recovery = false },
            },
        },
    }) + "\n");
    CompanionMaximumRuntimeSummary appliedSummary = CompanionMaximumReportReader.Read(maximumReportPath);
    Assert(appliedSummary.EvidenceApplied, "Applied runtime evidence was not presented.");
    Assert(appliedSummary.MatchedModels == 2 && appliedSummary.MatchedMaterials == 3 && appliedSummary.MatchedRegions == 2, "Runtime match counts were not presented.");
    Assert(appliedSummary.ScheduledValidations == 1 && appliedSummary.RegionalRecoveries == 1, "Runtime validation/recovery counts were not presented.");
    Assert(Math.Abs(appliedSummary.MaximumProtectionIncrease - 0.14) < 1e-9, "Geometric-to-effective protection was calculated incorrectly.");
    Assert(appliedSummary.DisplayText.Contains("applied", StringComparison.OrdinalIgnoreCase), "Applied evidence summary is unclear.");

    string optimizedAddon = Path.Combine(testRoot, "optimized-addon");
    string optimizedModel = Path.Combine(optimizedAddon, "models", "lvs", "example.mdl");
    string optimizedMaterial = Path.Combine(optimizedAddon, "materials", "models", "lvs", "tire.vmt");
    Directory.CreateDirectory(Path.GetDirectoryName(optimizedModel)!);
    Directory.CreateDirectory(Path.GetDirectoryName(optimizedMaterial)!);
    File.WriteAllBytes(optimizedModel, new byte[100]);
    File.WriteAllBytes(Path.ChangeExtension(optimizedModel, ".vvd"), new byte[60]);
    File.WriteAllBytes(Path.ChangeExtension(optimizedModel, ".dx90.vtx"), new byte[40]);
    File.WriteAllBytes(Path.ChangeExtension(optimizedModel, ".phy"), new byte[20]);
    File.WriteAllText(optimizedMaterial, "\"VertexLitGeneric\" { \"$basetexture\" \"models/lvs/tire_d\" \"$bumpmap\" \"models/lvs/tire_n\" }");
    File.WriteAllBytes(Path.Combine(optimizedAddon, "materials", "models", "lvs", "tire_d.vtf"), new byte[80]);
    File.WriteAllBytes(Path.Combine(optimizedAddon, "materials", "models", "lvs", "tire_n.vtf"), new byte[70]);
    string planReport = Path.Combine(testRoot, "plan-report.json");
    JsonObject planReportJson = new()
    {
        ["schema"] = 1,
        ["model_details"] = new JsonArray(new JsonObject
        {
            ["model_path"] = "models/lvs/example.mdl",
            ["expected_phy"] = true,
            ["changed"] = true,
            ["structural_failure"] = false,
            ["expected_bytes"] = 220,
            ["original_bytes"] = 440,
            ["original_triangles"] = 1000,
            ["final_triangles"] = 400,
            ["geometry_reduction"] = 0.6,
            ["maximum_risk"] = 0.8,
            ["maximum_runtime_importance"] = 0.75,
            ["visual_sensitive"] = true,
            ["materials"] = new JsonArray("materials/models/lvs/tire.vmt"),
            ["skins"] = new JsonArray(0, 2),
            ["bodygroups"] = new JsonArray(new JsonObject { ["name"] = "wheels", ["value"] = 0 }),
        }),
    };
    File.WriteAllText(planReport, planReportJson.ToJsonString());
    DateTimeOffset planNow = new(2026, 8, 16, 13, 0, 0, TimeSpan.Zero);
    CompanionValidationPlanResult firstPlan = CompanionValidationPlanService.Generate(
        optimizedAddon, planReport, Path.Combine(testRoot, "plan-out-a"), planNow
    );
    CompanionValidationPlanResult secondPlan = CompanionValidationPlanService.Generate(
        optimizedAddon, planReport, Path.Combine(testRoot, "plan-out-b"), planNow
    );
    Assert(firstPlan.RunId == secondPlan.RunId && firstPlan.IndexSha256 == secondPlan.IndexSha256, "Deterministic validation input produced different plan identities.");
    string[] firstPlanFiles = Directory.GetFiles(firstPlan.BundleRoot).Select(Path.GetFileName).OrderBy(value => value, StringComparer.Ordinal).ToArray()!;
    string[] secondPlanFiles = Directory.GetFiles(secondPlan.BundleRoot).Select(Path.GetFileName).OrderBy(value => value, StringComparer.Ordinal).ToArray()!;
    Assert(firstPlanFiles.SequenceEqual(secondPlanFiles), "Deterministic validation plans emitted different files.");
    Assert(firstPlanFiles.All(file => File.ReadAllBytes(Path.Combine(firstPlan.BundleRoot, file!)).SequenceEqual(File.ReadAllBytes(Path.Combine(secondPlan.BundleRoot, file!)))), "Deterministic validation plans emitted different bytes.");
    string copiedPlan = CompanionValidationPlanService.CopyBundle(firstPlan.BundleRoot, Path.Combine(testRoot, "plan-copy"));
    Assert(firstPlanFiles.All(file => File.ReadAllBytes(Path.Combine(firstPlan.BundleRoot, file!)).SequenceEqual(File.ReadAllBytes(Path.Combine(copiedPlan, file!)))), "Verified plan copy changed declared bytes.");
    Assert(firstPlan.ModelCount == 1 && firstPlan.ScreenshotCount == 4, "Validation plan model/screenshot counts are incorrect.");
    CompanionValidationTest structureTest = firstPlan.Tests.Single(test => test.Kind == "model_structure");
    Assert(structureTest.Path == "models/lvs/example.mdl", "Validation plan did not use a virtual model path.");
    Assert(structureTest.ExpectedBytes == 220, "Validation plan expected family bytes are incorrect.");
    Assert(structureTest.RequiredFiles.SequenceEqual(new[] { "models/lvs/example.dx90.vtx", "models/lvs/example.mdl", "models/lvs/example.phy", "models/lvs/example.vvd" }), "Required DX90/model/PHY/VVD family is incorrect.");
    Assert(!structureTest.RequiredFiles.Any(path => path.EndsWith(".dx80.vtx", StringComparison.Ordinal)), "DX80 leaked into validation requirements.");
    CompanionValidationTest stateTest = firstPlan.Tests.Single(test => test.Kind == "model_state");
    Assert(stateTest.Skins.SequenceEqual(new[] { 0, 2 }) && stateTest.Bodygroups.Single().Name == "wheels", "Observed/default states were not preserved.");
    CompanionValidationTest renderTest = firstPlan.Tests.Single(test => test.Kind == "model_render");
    Assert(renderTest.Views.SequenceEqual(new[] { "front", "side", "rear", "isometric" }), "High-risk/repeatedly-observed view policy is incorrect.");
    Assert(CompanionValidationPlanService.SelectViewsForContract(0.20, 0.0).SequenceEqual(new[] { "isometric" }), "Low-risk view policy is incorrect.");
    Assert(CompanionValidationPlanService.SelectViewsForContract(0.50, 0.0).SequenceEqual(new[] { "front", "isometric" }), "Medium-risk view policy is incorrect.");
    Assert(CompanionValidationPlanService.SelectViewsForContract(0.80, 0.0).SequenceEqual(new[] { "front", "side", "rear", "isometric" }), "High-risk view policy is incorrect.");
    Assert(CompanionValidationPlanService.SelectViewsForContract(0.20, 0.75).SequenceEqual(new[] { "front", "side", "rear", "isometric" }), "Repeated-observation view policy is incorrect.");
    CompanionValidationTest materialTest = firstPlan.Tests.Single(test => test.Kind == "material_resolve");
    Assert(materialTest.RequiredFiles.Contains("materials/models/lvs/tire_d.vtf") && materialTest.RequiredFiles.Contains("materials/models/lvs/tire_n.vtf"), "VMT texture dependencies are missing.");
    Assert(firstPlan.Dx80Excluded && firstPlan.ScreenshotRetries == 1 && firstPlan.MaximumOrdinaryViews == 4, "Validation plan safety limits are incorrect.");
    CompanionValidationPlanService.ValidateTestsForContract(firstPlan.Tests);
    ExpectFailure(
        () => CompanionValidationPlanService.ValidateTestsForContract(new[] { structureTest with { Path = @"C:\evil.mdl" } }),
        "virtual"
    );
    ExpectFailure(
        () => CompanionValidationPlanService.ValidateTestsForContract(new[] { structureTest with { Kind = "shell_command" } }),
        "kind"
    );
    ExpectFailure(
        () => CompanionValidationPlanService.ValidateTestsForContract(new[] { structureTest with { RequiredFiles = new[] { "lua/autorun/evil.lua" } } }),
        "forbidden"
    );
    ExpectFailure(
        () => CompanionValidationPlanService.ValidateTestsForContract(new[] { structureTest with { RequiredFiles = new[] { "https://evil.invalid/file.vtf" } } }),
        "virtual"
    );
    ExpectFailure(
        () => CompanionValidationPlanService.ValidateTestsForContract(new[] { structureTest with { RequiredFiles = new[] { "models/test/evil.exe" } } }),
        "extension"
    );
    ExpectFailure(
        () => CompanionValidationPlanService.ValidateTestsForContract(Enumerable.Range(0, 251).Select(index => structureTest with { Id = $"model-{index}", Path = $"models/test/model-{index}.mdl" }).ToArray()),
        "250"
    );
    ExpectFailure(
        () => CompanionValidationPlanService.ValidateTestsForContract(new[] { renderTest with { Views = new[] { "front", "side", "rear", "isometric", "closeup" } } }),
        "views"
    );
    ExpectFailure(
        () => CompanionValidationPlanService.ValidateTestsForContract(Enumerable.Range(0, 251).Select(index => renderTest with { Id = $"render-{index}", Views = new[] { "front", "side", "rear", "isometric" } }).ToArray()),
        "1000"
    );
    JsonObject reportWithArguments = (JsonObject)CloneJson(planReportJson);
    reportWithArguments["model_details"]![0]!["arguments"] = new JsonArray("-run");
    string reportWithArgumentsPath = Path.Combine(testRoot, "plan-report-with-arguments.json");
    File.WriteAllText(reportWithArgumentsPath, reportWithArguments.ToJsonString());
    ExpectFailure(
        () => CompanionValidationPlanService.Generate(optimizedAddon, reportWithArgumentsPath, Path.Combine(testRoot, "plan-arguments"), planNow),
        "unknown"
    );
    JsonObject unchangedReport = (JsonObject)CloneJson(planReportJson);
    unchangedReport["model_details"]![0]!["changed"] = false;
    string unchangedReportPath = Path.Combine(testRoot, "plan-report-unchanged.json");
    File.WriteAllText(unchangedReportPath, unchangedReport.ToJsonString());
    ExpectFailure(
        () => CompanionValidationPlanService.Generate(optimizedAddon, unchangedReportPath, Path.Combine(testRoot, "plan-unchanged"), planNow),
        "no changed"
    );

    DateTimeOffset resultNow = new(2026, 8, 16, 18, 0, 0, TimeSpan.Zero);
    string resultDataRoot = Path.Combine(testRoot, "companion-result-data");
    const string resultRunId = "55555555-5555-4555-8555-555555555555";
    const string planHash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    JsonObject structuralResult = LoadResultFixture("sao-structural-result-v1");
    structuralResult["run_id"] = resultRunId;
    structuralResult["created_utc"] = "2026-08-16T17:00:00Z";
    structuralResult["payload"]!["plan_sha256"] = planHash;
    string structuralIndex = WriteRuntimeBundle(
        Path.Combine(resultDataRoot, "validation_results", resultRunId, "structural-0001"),
        structuralResult
    );
    var structuralOnlyService = new CompanionResultService();
    CompanionValidationReport structuralOnly = structuralOnlyService.Import(structuralIndex, resultDataRoot, resultNow);
    Assert(structuralOnly.HasStructural && !structuralOnly.HasVisual, "Structural-only result channels were reported incorrectly.");
    Assert(structuralOnly.OverallStatus == CompanionValidationStatus.Warning, "A structural-only PASS must warn that visual validation was not run.");
    Assert(structuralOnly.SummaryText.Contains("visual validation not run", StringComparison.OrdinalIgnoreCase), "Structural-only warning is not explicit.");

    JsonObject visualIncompleteResult = LoadResultFixture("sao-visual-result-v1");
    visualIncompleteResult["run_id"] = "66666666-6666-4666-8666-666666666666";
    visualIncompleteResult["created_utc"] = "2026-08-16T17:00:00Z";
    visualIncompleteResult["payload"]!["overall_status"] = "INCOMPLETE";
    visualIncompleteResult["payload"]!["resources"]![0]!["status"] = "INCOMPLETE";
    visualIncompleteResult["payload"]!["resources"]![0]!["reason"] = "client disconnected";
    visualIncompleteResult["payload"]!["screenshots"] = new JsonArray();
    visualIncompleteResult["payload"]!["performance"]!["screenshots"] = 0;
    string visualIncompleteIndex = WriteRuntimeBundle(
        Path.Combine(resultDataRoot, "validation_results", "66666666-6666-4666-8666-666666666666", "visual-0001"),
        visualIncompleteResult
    );
    var visualOnlyService = new CompanionResultService();
    CompanionValidationReport visualOnly = visualOnlyService.Import(visualIncompleteIndex, resultDataRoot, resultNow);
    Assert(!visualOnly.HasStructural && visualOnly.HasVisual, "Visual-only result channels were reported incorrectly.");
    Assert(visualOnly.OverallStatus == CompanionValidationStatus.Incomplete, "A visual-only interrupted result must remain INCOMPLETE.");

    byte[] screenshotBytes = Encoding.UTF8.GetBytes("verified local PNG fixture bytes");
    string screenshotRelative = $"screenshots/{resultRunId}/test-1-isometric.png";
    string screenshotFull = Path.Combine(resultDataRoot, screenshotRelative.Replace('/', Path.DirectorySeparatorChar));
    Directory.CreateDirectory(Path.GetDirectoryName(screenshotFull)!);
    File.WriteAllBytes(screenshotFull, screenshotBytes);
    JsonObject visualResult = LoadResultFixture("sao-visual-result-v1");
    visualResult["run_id"] = resultRunId;
    visualResult["created_utc"] = "2026-08-16T17:00:00Z";
    visualResult["payload"]!["plan_sha256"] = planHash;
    visualResult["payload"]!["screenshots"]![0]!["path"] = screenshotRelative;
    visualResult["payload"]!["screenshots"]![0]!["bytes"] = screenshotBytes.LongLength;
    visualResult["payload"]!["screenshots"]![0]!["sha256"] = Digest(screenshotBytes);
    string visualIndex = WriteRuntimeBundle(
        Path.Combine(resultDataRoot, "validation_results", resultRunId, "visual-0001"),
        visualResult
    );
    var mergedResultService = new CompanionResultService();
    mergedResultService.Import(structuralIndex, resultDataRoot, resultNow);
    CompanionValidationReport mergedResult = mergedResultService.Import(visualIndex, resultDataRoot, resultNow);
    Assert(mergedResultService.Imports.Count == 2 && mergedResultService.Reports.Count == 1, "Matching result channels did not merge into one report.");
    Assert(mergedResult.HasStructural && mergedResult.HasVisual && mergedResult.OverallStatus == CompanionValidationStatus.Pass, "Matching PASS channels did not produce a complete PASS report.");
    Assert(mergedResult.Screenshots.Single().FullPath == Path.GetFullPath(screenshotFull), "Verified screenshot was not resolved to its contained full path.");
    mergedResultService.Import(structuralIndex, resultDataRoot, resultNow);
    Assert(mergedResultService.Imports.Count == 3 && mergedResultService.Reports.Single().Sources.Count == 3, "A duplicate import silently replaced prior evidence.");

    JsonObject conflictResult = (JsonObject)CloneJson(visualResult);
    conflictResult["payload"]!["plan_sha256"] = new string('c', 64);
    string conflictIndex = WriteRuntimeBundle(
        Path.Combine(resultDataRoot, "validation_results", resultRunId, "visual-0002"),
        conflictResult
    );
    mergedResultService.Import(conflictIndex, resultDataRoot, resultNow);
    Assert(mergedResultService.Reports.Count == 2 && mergedResultService.Reports.All(report => report.HasPlanConflict), "Mismatched plan hashes were merged or hidden.");
    Assert(mergedResultService.Reports.All(report => report.Sources.Select(source => source.PlanSha256).Distinct(StringComparer.Ordinal).Count() == 1), "A conflicting plan entered a merged report.");

    JsonObject traversalResult = (JsonObject)CloneJson(visualResult);
    traversalResult["run_id"] = "77777777-7777-4777-8777-777777777777";
    traversalResult["payload"]!["screenshots"]![0]!["path"] = "screenshots/77777777-7777-4777-8777-777777777777/../escape.png";
    string traversalIndex = WriteRuntimeBundle(Path.Combine(testRoot, "result-traversal"), traversalResult);
    ExpectFailure(() => new CompanionResultService().Import(traversalIndex, resultDataRoot, resultNow), "screenshot path");

    JsonObject wrongHashResult = (JsonObject)CloneJson(visualResult);
    wrongHashResult["run_id"] = "88888888-8888-4888-8888-888888888888";
    string wrongHashRelative = "screenshots/88888888-8888-4888-8888-888888888888/test-1-isometric.png";
    wrongHashResult["payload"]!["screenshots"]![0]!["path"] = wrongHashRelative;
    string wrongHashFull = Path.Combine(resultDataRoot, wrongHashRelative.Replace('/', Path.DirectorySeparatorChar));
    Directory.CreateDirectory(Path.GetDirectoryName(wrongHashFull)!);
    File.WriteAllBytes(wrongHashFull, screenshotBytes);
    wrongHashResult["payload"]!["screenshots"]![0]!["sha256"] = new string('d', 64);
    string wrongHashIndex = WriteRuntimeBundle(Path.Combine(testRoot, "result-wrong-hash"), wrongHashResult);
    ExpectFailure(() => new CompanionResultService().Import(wrongHashIndex, resultDataRoot, resultNow), "screenshot hash");

    JsonObject understatedResult = (JsonObject)CloneJson(structuralResult);
    understatedResult["run_id"] = "99999999-9999-4999-8999-999999999999";
    understatedResult["payload"]!["resources"]![0]!["status"] = "FAILED";
    understatedResult["payload"]!["overall_status"] = "PASS";
    string understatedIndex = WriteRuntimeBundle(Path.Combine(testRoot, "result-understated"), understatedResult);
    ExpectFailure(() => new CompanionResultService().Import(understatedIndex, resultDataRoot, resultNow), "understates");

    Assert(CompanionResultService.CombineStatusForContract(CompanionValidationStatus.Pass, CompanionValidationStatus.Incomplete) == CompanionValidationStatus.Incomplete, "INCOMPLETE precedence is incorrect.");
    Assert(CompanionResultService.CombineStatusForContract(CompanionValidationStatus.Incomplete, CompanionValidationStatus.Warning) == CompanionValidationStatus.Warning, "WARNING precedence is incorrect.");
    Assert(CompanionResultService.CombineStatusForContract(CompanionValidationStatus.Warning, CompanionValidationStatus.Failed) == CompanionValidationStatus.Failed, "FAILED precedence is incorrect.");

    var validationContext = new CompanionContext();
    validationContext.ApplyValidationReports(mergedResultService.Reports);
    Assert(validationContext.ValidationResources.Count > 0 && validationContext.ValidationSummaryText.Contains("conflict", StringComparison.OrdinalIgnoreCase), "Validation results were not exposed to the WPF context.");
    validationContext.ValidationStatusFilter = "FAILED";
    Assert(validationContext.ValidationResources.All(item => item.Status == "FAILED"), "Validation result status filter leaked another status.");

    string emptyAddon = Path.Combine(testRoot, "empty-addon");
    Directory.CreateDirectory(emptyAddon);
    CompanionProfileImportResult noMatch = CompanionProfileService.Import(new[] { validIndex }, emptyAddon, now, CancellationToken.None);
    Assert(!noMatch.IsEligible && noMatch.MatchedAssetCount == 0, "A zero-match profile became usable.");

    JsonObject staleProfile = (JsonObject)CloneJson(LoadRuntimeProfileFixture());
    staleProfile["created_utc"] = "2025-01-01T00:00:00Z";
    string staleIndex = WriteRuntimeBundle(Path.Combine(testRoot, "runtime-stale"), staleProfile);
    CompanionProfileImportResult stale = CompanionProfileService.Import(new[] { staleIndex }, selectedAddon, now, CancellationToken.None);
    Assert(stale.IsStale && !stale.IsEligible, "A profile older than 180 days remained eligible.");

    var profileContext = new CompanionContext();
    profileContext.SetOptimizerMode(isMaximum: true);
    profileContext.ApplyImportedProfile(merged, mergeBIndex);
    Assert(profileContext.CanUseWithMaximumV2 && !profileContext.UseWithMaximumV2, "Eligible profile should enable but never automatically check the opt-in.");
    profileContext.UseWithMaximumV2 = true;
    Assert(profileContext.UseWithMaximumV2, "Explicit Maximum v2 opt-in was rejected.");
    profileContext.SetOptimizerMode(isMaximum: false);
    Assert(!profileContext.CanUseWithMaximumV2 && !profileContext.UseWithMaximumV2, "Leaving Maximum did not clear and disable the opt-in.");
    profileContext.SetOptimizerMode(isMaximum: true);
    profileContext.ApplyImportedProfile(noMatch, validIndex);
    profileContext.UseWithMaximumV2 = true;
    Assert(!profileContext.UseWithMaximumV2, "An ineligible profile accepted the opt-in.");
    profileContext.ClearForAddonChange();
    Assert(!profileContext.UseWithMaximumV2 && !profileContext.CanUseWithMaximumV2, "Addon change did not clear profile eligibility.");

    if (args.Length >= 3 && args[0] == "--import-result")
    {
        var smokeImport = new CompanionResultService();
        foreach (string indexPath in args.Skip(2))
        {
            CompanionValidationReport report = smokeImport.Import(indexPath, args[1], DateTimeOffset.UtcNow);
            Console.WriteLine("REAL RESULT IMPORT: " + report.SummaryText);
            Assert(report.Screenshots.All(screenshot => screenshot.Bytes == new FileInfo(screenshot.FullPath).Length), "Real result screenshot size was not retained.");
        }
    }

    Console.WriteLine("WPF Companion contract tests passed.");
}
finally
{
    if (Directory.Exists(testRoot))
        Directory.Delete(testRoot, recursive: true);
}
