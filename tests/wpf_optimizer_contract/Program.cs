using GmodAddonCompressor.Systems.Optimizer;
using GmodAddonCompressor.Systems.Tools;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;

static void Assert(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}

static byte[] FakePe(ushort machine = 0x8664)
{
    byte[] bytes = new byte[128];
    bytes[0] = (byte)'M';
    bytes[1] = (byte)'Z';
    BitConverter.GetBytes(0x40).CopyTo(bytes, 0x3c);
    bytes[0x40] = (byte)'P';
    bytes[0x41] = (byte)'E';
    BitConverter.GetBytes(machine).CopyTo(bytes, 0x44);
    return bytes;
}

static string Digest(byte[] payload) =>
    Convert.ToHexString(SHA256.HashData(payload)).ToLowerInvariant();

static byte[] BuildToolPackage(string marker, ushort machine = 0x8664)
{
    var files = new SortedDictionary<string, byte[]>(StringComparer.Ordinal)
    {
        ["CrowbarCommandLineDecomp.exe"] = System.Text.Encoding.UTF8.GetBytes("crowbar-" + marker),
        ["SourceAddonOptimizerWorker.exe"] = System.Text.Encoding.UTF8.GetBytes("worker-" + marker),
        ["_internal/base_library.zip"] = System.Text.Encoding.UTF8.GetBytes("base-" + marker),
        ["_internal/maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll"] = FakePe(machine),
        ["_internal/python311.dll"] = System.Text.Encoding.UTF8.GetBytes("python-" + marker),
    };
    var declared = files.Select(pair => new
    {
        path = pair.Key,
        size = pair.Value.LongLength,
        sha256 = Digest(pair.Value),
    }).ToArray();
    var dll = declared.Single(item => item.path.EndsWith("meshopt_bridge.dll", StringComparison.Ordinal));
    byte[] manifest = JsonSerializer.SerializeToUtf8Bytes(new
    {
        schemaVersion = 1,
        toolName = "SourceAddonOptimizer",
        toolVersion = "0.1.18",
        silhouette = new
        {
            apiVersion = "1.0.0",
            buildId = "maximum-silhouette-raw-v1-20260722",
            architecture = "x64",
            dllPath = "_internal/maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll",
            sha256 = dll.sha256,
            size = dll.size,
            minimumWorkerContract = "0.1.18",
            minimumWpfContract = "0.1.18",
        },
        files = declared,
    });
    using var output = new MemoryStream();
    using (var archive = new ZipArchive(output, ZipArchiveMode.Create, leaveOpen: true))
    {
        foreach ((string path, byte[] payload) in files)
        {
            using Stream stream = archive.CreateEntry(path, CompressionLevel.Fastest).Open();
            stream.Write(payload);
        }
        using Stream manifestStream = archive.CreateEntry(
            SourceAddonOptimizerPackageManifest.RelativePath,
            CompressionLevel.Fastest
        ).Open();
        manifestStream.Write(manifest);
    }
    return output.ToArray();
}

static byte[] RemoveZipEntry(byte[] source, string excluded)
{
    using var input = new MemoryStream(source, writable: false);
    using var original = new ZipArchive(input, ZipArchiveMode.Read, leaveOpen: false);
    using var output = new MemoryStream();
    using (var replacement = new ZipArchive(output, ZipArchiveMode.Create, leaveOpen: true))
    {
        foreach (ZipArchiveEntry entry in original.Entries.Where(item => !item.FullName.Equals(excluded, StringComparison.Ordinal)))
        {
            using Stream from = entry.Open();
            using Stream to = replacement.CreateEntry(entry.FullName, CompressionLevel.Fastest).Open();
            from.CopyTo(to);
        }
    }
    return output.ToArray();
}

var options = new SourceAddonOptimizerRunOptions
{
    AddonPath = @"C:\addon",
    WorkDir = @"C:\work",
    OptimizerMode = "maximum",
    Ratio = 0.75,
    RestoreSkins = true,
    ExperimentalGroundPolicy = true,
    ExperimentalRoundPartsPolicy = true,
    ExperimentalSteerTurnBasisFix = true
};
var arguments = SourceAddonOptimizerCommandBuilder.BuildArguments(options);
Assert(arguments.Contains("maximum"), "Maximum mode missing from worker arguments.");
Assert(arguments.Contains("0.75"), "Invariant ratio missing from worker arguments.");
Assert(!arguments.Contains("--experimental-ground-policy"), "Maximum leaked the experimental ground policy.");
Assert(!arguments.Contains("--experimental-round-parts-policy"), "Maximum leaked the experimental round-parts policy.");
Assert(!arguments.Contains("--experimental-steer-turn-basis-fix"), "Maximum leaked the experimental steer policy.");

var normalOptions = new SourceAddonOptimizerRunOptions
{
    AddonPath = @"C:\addon",
    WorkDir = @"C:\work",
    OptimizerMode = "normal",
    RestoreSkins = true,
    ExperimentalGroundPolicy = true,
    ExperimentalRoundPartsPolicy = true,
    ExperimentalSteerTurnBasisFix = true
};
var normalArguments = SourceAddonOptimizerCommandBuilder.BuildArguments(normalOptions);
Assert(normalArguments.Contains("--experimental-ground-policy"), "Normal lost the experimental ground policy.");
Assert(normalArguments.Contains("--experimental-round-parts-policy"), "Normal lost the experimental round-parts policy.");
Assert(normalArguments.Contains("--experimental-steer-turn-basis-fix"), "Normal lost the experimental steer policy.");

var parser = new SourceAddonOptimizerProgressParser();
var update = parser.Parse("[MAXIMUM] stage=adaptive-simplification current=3 total=6 detail=wheel")
    ?? throw new InvalidOperationException("Maximum stage was not parsed.");
Assert(update.MaximumStage == "adaptive-simplification", "Maximum stage was not parsed.");
Assert(update.ItemIndex == 3 && update.ItemTotal == 6, "Maximum counts were not parsed.");
Assert(update.ItemPath == "wheel", "Maximum detail was not parsed.");
Assert(parser.Parse("[MAXIMUM] stage=unknown current=1 total=6 detail=x") == null, "Unknown Maximum stage must fail closed.");
Assert(parser.Parse("[MAXIMUM] stage=packaging current=x total=6 detail=x") == null, "Malformed Maximum counts must fail closed.");

string testRoot = Path.Combine(Path.GetTempPath(), "gaco-wpf-package-contract-" + Guid.NewGuid().ToString("N"));
try
{
    byte[] firstPackage = BuildToolPackage("first");
    string firstRoot = SourceAddonOptimizerPackageInstaller.Install(
        firstPackage, testRoot, "SourceAddonOptimizer", "0.1.18", TimeSpan.FromSeconds(5)
    );
    string expectedHash = Digest(firstPackage);
    Assert(firstRoot.EndsWith(Path.Combine("0.1.18", expectedHash), StringComparison.OrdinalIgnoreCase), "Package root is not content-addressed.");
    Assert(File.ReadAllText(Path.Combine(firstRoot, "SourceAddonOptimizerWorker.exe")) == "worker-first", "Installed worker differs from package.");

    string partial = Path.Combine(testRoot, "SourceAddonOptimizer", "0.1.18", ".interrupted.partial");
    Directory.CreateDirectory(partial);
    File.WriteAllText(Path.Combine(partial, "stale.txt"), "partial");
    File.WriteAllText(Path.Combine(firstRoot, "SourceAddonOptimizerWorker.exe"), "tampered");
    string repairedRoot = SourceAddonOptimizerPackageInstaller.Install(
        firstPackage, testRoot, "SourceAddonOptimizer", "0.1.18", TimeSpan.FromSeconds(5)
    );
    Assert(repairedRoot == firstRoot, "Tampered package repaired to a different content root.");
    Assert(File.ReadAllText(Path.Combine(firstRoot, "SourceAddonOptimizerWorker.exe")) == "worker-first", "Tampered installed worker was not repaired.");
    Assert(!Directory.Exists(partial), "Interrupted partial extraction was selected or retained.");

    string concurrentRoot = Path.Combine(testRoot, "concurrent");
    Task<string>[] installs = Enumerable.Range(0, 2)
        .Select(_ => Task.Run(() => SourceAddonOptimizerPackageInstaller.Install(
            firstPackage, concurrentRoot, "SourceAddonOptimizer", "0.1.18", TimeSpan.FromSeconds(10)
        )))
        .ToArray();
    Task.WaitAll(installs);
    Assert(installs[0].Result == installs[1].Result, "Concurrent installs activated different roots.");

    byte[] secondPackage = BuildToolPackage("second");
    string secondRoot = SourceAddonOptimizerPackageInstaller.Install(
        secondPackage, testRoot, "SourceAddonOptimizer", "0.1.18", TimeSpan.FromSeconds(5)
    );
    Assert(secondRoot != firstRoot, "Updated package reused old content root.");
    Assert(Directory.Exists(firstRoot), "Immutable previous package root was removed during update.");
    Assert(File.ReadAllText(Path.Combine(firstRoot, "SourceAddonOptimizerWorker.exe")) == "worker-first", "Previous package root was overwritten.");

    string adjacentFake = Path.Combine(testRoot, "meshopt_bridge.dll");
    File.WriteAllText(adjacentFake, "old fake DLL beside executable");
    string installedDll = Path.Combine(secondRoot, SourceAddonOptimizerPackageManifest.NativeDllRelativePath.Replace('/', Path.DirectorySeparatorChar));
    Assert(File.Exists(installedDll), "Validated package DLL was not installed.");
    Assert(File.ReadAllText(adjacentFake) == "old fake DLL beside executable", "Adjacent fake DLL was consulted or modified.");

    bool incompleteRejected = false;
    try
    {
        SourceAddonOptimizerPackageInstaller.Install(
            RemoveZipEntry(firstPackage, "SourceAddonOptimizerWorker.exe"),
            Path.Combine(testRoot, "incomplete"),
            "SourceAddonOptimizer",
            "0.1.18",
            TimeSpan.FromSeconds(5)
        );
    }
    catch (InvalidOperationException)
    {
        incompleteRejected = true;
    }
    Assert(incompleteRejected, "Incomplete package ZIP was accepted.");

    bool wrongArchRejected = false;
    try
    {
        SourceAddonOptimizerPackageInstaller.Install(
            BuildToolPackage("x86", 0x014c),
            Path.Combine(testRoot, "x86"),
            "SourceAddonOptimizer",
            "0.1.18",
            TimeSpan.FromSeconds(5)
        );
    }
    catch (InvalidOperationException)
    {
        wrongArchRejected = true;
    }
    Assert(wrongArchRejected, "Non-AMD64 native DLL was accepted.");
}
finally
{
    if (Directory.Exists(testRoot))
        Directory.Delete(testRoot, recursive: true);
}

Console.WriteLine("WPF optimizer contract tests passed.");
