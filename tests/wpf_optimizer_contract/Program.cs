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
        toolVersion = "0.1.19",
        silhouette = new
        {
            apiVersion = "1.0.0",
            buildId = "maximum-silhouette-raw-v1-20260722",
            architecture = "x64",
            dllPath = "_internal/maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll",
            sha256 = dll.sha256,
            size = dll.size,
            minimumWorkerContract = "0.1.19",
            minimumWpfContract = "0.1.19",
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

// Models "Tela" mode: worker command modscreen, no Blender/StudioMDL, profile mapped to balanced/strong/strong_mirror.
var screenOptions = new SourceAddonOptimizerRunOptions
{
    AddonPath = @"C:\addon folder",
    WorkDir = @"C:\work",
    OptimizerMode = "screen",
    ScreenProfile = "strong",
    Suffix = "_optimized",
    BlenderPath = @"C:\blender.exe",
    StudioMdlPath = @"C:\studiomdl.exe",
    Jobs = 4,
    Overwrite = true,
    RestoreSkins = true,
};
var screenArguments = SourceAddonOptimizerCommandBuilder.BuildArguments(screenOptions).ToArray();
Assert(screenArguments[0] == "modscreen", "Tela mode must start the modscreen worker command.");
Assert(screenArguments[1] == @"C:\addon folder", "Tela mode lost the addon path.");
int screenProfileFlag = Array.IndexOf(screenArguments, "--profile");
Assert(screenProfileFlag >= 0 && screenArguments[screenProfileFlag + 1] == "strong", "Tela strong profile missing.");
Assert(!screenArguments.Contains("--blender") && !screenArguments.Contains("--studiomdl") && !screenArguments.Contains("--optimizer-mode"), "Tela mode must not pass Blender/StudioMDL/optimizer-mode.");
Assert(screenArguments.Contains("--overwrite") && screenArguments.Contains("--jobs"), "Tela mode lost overwrite/jobs.");
var screenDefault = SourceAddonOptimizerCommandBuilder.BuildArguments(new SourceAddonOptimizerRunOptions { AddonPath = @"C:\a", WorkDir = @"C:\w", OptimizerMode = "screen" }).ToArray();
Assert(screenDefault[Array.IndexOf(screenDefault, "--profile") + 1] == "strong_mirror", "Tela mode must default to the strong_mirror (Forte + espelho) profile.");
var screenMirror = SourceAddonOptimizerCommandBuilder.BuildArguments(new SourceAddonOptimizerRunOptions { AddonPath = @"C:", WorkDir = @"C:\w", OptimizerMode = "screen", ScreenProfile = "strong_mirror" }).ToArray();
Assert(screenMirror[Array.IndexOf(screenMirror, "--profile") + 1] == "strong_mirror", "Tela mode lost the explicit strong_mirror profile.");
var screenUnknown = SourceAddonOptimizerCommandBuilder.BuildArguments(new SourceAddonOptimizerRunOptions { AddonPath = @"C:", WorkDir = @"C:\w", OptimizerMode = "screen", ScreenProfile = "whatever" }).ToArray();
Assert(screenUnknown[Array.IndexOf(screenUnknown, "--profile") + 1] == "strong_mirror", "An unknown Tela profile must fall back to strong_mirror.");
var screenBalanced = SourceAddonOptimizerCommandBuilder.BuildArguments(new SourceAddonOptimizerRunOptions { AddonPath = @"C:\a", WorkDir = @"C:\w", OptimizerMode = "screen", ScreenProfile = "balanced" }).ToArray();
Assert(screenBalanced[Array.IndexOf(screenBalanced, "--profile") + 1] == "balanced", "Tela mode lost the explicit balanced (Equilibrado) profile.");
var screenStream = SourceAddonOptimizerCommandBuilder.BuildArguments(new SourceAddonOptimizerRunOptions { AddonPath = @"C:\a", WorkDir = @"C:\w", OptimizerMode = "screen", ScreenProfile = "stream" }).ToArray();
Assert(screenStream[Array.IndexOf(screenStream, "--profile") + 1] == "stream", "Tela mode lost the explicit stream profile.");

string runtimeProfileWithSpaces = @"C:\work folder\runtime profile snapshot";
var maximumProfileOptions = new SourceAddonOptimizerRunOptions
{
    AddonPath = @"C:\addon",
    WorkDir = @"C:\work",
    OptimizerMode = "maximum",
    MaximumRuntimeProfilePath = runtimeProfileWithSpaces,
    RestoreSkins = true,
};
var maximumProfileArguments = SourceAddonOptimizerCommandBuilder.BuildArguments(maximumProfileOptions);
int runtimeProfileFlag = Array.IndexOf(maximumProfileArguments.ToArray(), "--maximum-runtime-profile");
Assert(runtimeProfileFlag >= 0, "Maximum did not emit its explicitly selected runtime profile.");
Assert(runtimeProfileFlag + 1 < maximumProfileArguments.Count, "Maximum runtime profile flag has no value.");
Assert(maximumProfileArguments[runtimeProfileFlag + 1] == runtimeProfileWithSpaces, "A runtime profile path containing spaces was split or changed.");
Assert(maximumProfileArguments.Count(value => value == runtimeProfileWithSpaces) == 1, "Maximum emitted the runtime profile path more than once.");

foreach (string mode in new[] { "normal", "fidelity" })
{
    var nonMaximumProfileOptions = new SourceAddonOptimizerRunOptions
    {
        AddonPath = @"C:\addon",
        WorkDir = @"C:\work",
        OptimizerMode = mode,
        MaximumRuntimeProfilePath = runtimeProfileWithSpaces,
        RestoreSkins = true,
    };
    Assert(
        !SourceAddonOptimizerCommandBuilder.BuildArguments(nonMaximumProfileOptions).Contains("--maximum-runtime-profile"),
        $"{mode} leaked the Maximum runtime profile."
    );
}

var uncheckedMaximumOptions = new SourceAddonOptimizerRunOptions
{
    AddonPath = @"C:\addon",
    WorkDir = @"C:\work",
    OptimizerMode = "maximum",
    MaximumRuntimeProfilePath = null,
    RestoreSkins = true,
};
Assert(
    !SourceAddonOptimizerCommandBuilder.BuildArguments(uncheckedMaximumOptions).Contains("--maximum-runtime-profile"),
    "Unchecked Maximum emitted a runtime profile."
);
var invalidMaximumOptions = new SourceAddonOptimizerRunOptions
{
    AddonPath = @"C:\addon",
    WorkDir = @"C:\work",
    OptimizerMode = "maximum",
    MaximumRuntimeProfilePath = "   ",
    RestoreSkins = true,
};
Assert(
    !SourceAddonOptimizerCommandBuilder.BuildArguments(invalidMaximumOptions).Contains("--maximum-runtime-profile"),
    "Maximum emitted an invalid runtime profile path."
);

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
        firstPackage, testRoot, "SourceAddonOptimizer", "0.1.19", TimeSpan.FromSeconds(5)
    );
    string expectedHash = Digest(firstPackage);
    Assert(firstRoot.EndsWith(Path.Combine("0.1.19", expectedHash), StringComparison.OrdinalIgnoreCase), "Package root is not content-addressed.");
    Assert(File.ReadAllText(Path.Combine(firstRoot, "SourceAddonOptimizerWorker.exe")) == "worker-first", "Installed worker differs from package.");

    string partial = Path.Combine(testRoot, "SourceAddonOptimizer", "0.1.19", ".interrupted.partial");
    Directory.CreateDirectory(partial);
    File.WriteAllText(Path.Combine(partial, "stale.txt"), "partial");
    File.WriteAllText(Path.Combine(firstRoot, "SourceAddonOptimizerWorker.exe"), "tampered");
    string repairedRoot = SourceAddonOptimizerPackageInstaller.Install(
        firstPackage, testRoot, "SourceAddonOptimizer", "0.1.19", TimeSpan.FromSeconds(5)
    );
    Assert(repairedRoot == firstRoot, "Tampered package repaired to a different content root.");
    Assert(File.ReadAllText(Path.Combine(firstRoot, "SourceAddonOptimizerWorker.exe")) == "worker-first", "Tampered installed worker was not repaired.");
    Assert(!Directory.Exists(partial), "Interrupted partial extraction was selected or retained.");

    string concurrentRoot = Path.Combine(testRoot, "concurrent");
    Task<string>[] installs = Enumerable.Range(0, 2)
        .Select(_ => Task.Run(() => SourceAddonOptimizerPackageInstaller.Install(
            firstPackage, concurrentRoot, "SourceAddonOptimizer", "0.1.19", TimeSpan.FromSeconds(10)
        )))
        .ToArray();
    Task.WaitAll(installs);
    Assert(installs[0].Result == installs[1].Result, "Concurrent installs activated different roots.");

    byte[] secondPackage = BuildToolPackage("second");
    string secondRoot = SourceAddonOptimizerPackageInstaller.Install(
        secondPackage, testRoot, "SourceAddonOptimizer", "0.1.19", TimeSpan.FromSeconds(5)
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
            "0.1.19",
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
            "0.1.19",
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

// Texturas "Stream" (texture_stream): Perfil Tela index 4 -> texscreen profile "stream"; the Máximo assistant
// profile and the Personalizado "Stream" chip use it; its blobs count as textures in the size summary.
var streamChoice = GmodAddonCompressor.Systems.Optimizer.WizardProfiles.Find(
    GmodAddonCompressor.Systems.Optimizer.WizardProfiles.TextureChoices, "stream");
Assert(streamChoice.Key == "stream" && streamChoice.Index == 4, "Texture choice Stream lost its Perfil Tela index.");
var maxProfile = GmodAddonCompressor.Systems.Optimizer.WizardProfiles.For(GmodAddonCompressor.Systems.Optimizer.WizardProfile.Max);
Assert(maxProfile.Textures && maxProfile.CompressScreenProfileIndex == 5, "Máximo no longer uses Stream máximo (31 dB).");
var balancedProfile = GmodAddonCompressor.Systems.Optimizer.WizardProfiles.For(GmodAddonCompressor.Systems.Optimizer.WizardProfile.Balanced);
Assert(balancedProfile.Textures && balancedProfile.CompressScreenProfileIndex == 4, "Custo-benefício no longer uses the texture stream.");
var streamMaxChoice = GmodAddonCompressor.Systems.Optimizer.WizardProfiles.Find(
    GmodAddonCompressor.Systems.Optimizer.WizardProfiles.TextureChoices, "stream_max");
Assert(streamMaxChoice.Key == "stream_max" && streamMaxChoice.Index == 5, "Texture choice Stream máximo lost its Perfil Tela index.");
var customStream = GmodAddonCompressor.Systems.Optimizer.WizardProfiles.ForCustom("strong", "stream", "adaptive", 0, 0, 0, false);
Assert(customStream.CompressScreenProfileIndex == 4, "Personalizado Stream chip does not select the texture stream.");
Assert(GmodAddonCompressor.Systems.Reporting.AddonInventory.Classify("data_static/gmot/ab12/3.dat")
       == GmodAddonCompressor.Systems.Reporting.AddonFileKind.Textures, "Texture stream blobs are not counted as textures.");
Assert(GmodAddonCompressor.Systems.Reporting.AddonInventory.Classify("data_static/gmo/ab12.dat")
       == GmodAddonCompressor.Systems.Reporting.AddonFileKind.Models, "Model stream blobs are not counted as models.");

// PNG sem perda (worker pnglossless): every preset recompresses the PNG files with the same pixels and says so
// on its textures line; Personalizado has its own switch, on by default.
foreach (var preset in new[]
{
    GmodAddonCompressor.Systems.Optimizer.WizardProfile.Lossless,
    GmodAddonCompressor.Systems.Optimizer.WizardProfile.Balanced,
    GmodAddonCompressor.Systems.Optimizer.WizardProfile.Max,
})
{
    var presetSettings = GmodAddonCompressor.Systems.Optimizer.WizardProfiles.For(preset);
    Assert(presetSettings.Png, $"Preset {preset} no longer recompresses PNG without loss.");
    Assert(presetSettings.TexturesLine.EndsWith(GmodAddonCompressor.Systems.Optimizer.WizardProfiles.PngSuffix, StringComparison.Ordinal),
        $"Preset {preset} does not mention the lossless PNG step.");
}
Assert(customStream.Png, "Personalizado no longer recompresses PNG by default.");
var customPngOnly = GmodAddonCompressor.Systems.Optimizer.WizardProfiles.ForCustom("none", "none", "none", 0, 0, 0, false, true);
Assert(customPngOnly.Png && !customPngOnly.Textures && !customPngOnly.IncludeWav,
    "Personalizado cannot run the lossless PNG step on its own.");
var customNoPng = GmodAddonCompressor.Systems.Optimizer.WizardProfiles.ForCustom("none", "stream", "none", 0, 0, 0, false, false);
Assert(!customNoPng.Png && !customNoPng.TexturesLine.Contains("PNG", StringComparison.Ordinal),
    "Personalizado with PNG off still announces the PNG step.");
Assert(GmodAddonCompressor.Systems.Reporting.AddonInventory.Classify("materials/entities/weapon.png")
       == GmodAddonCompressor.Systems.Reporting.AddonFileKind.Textures, "PNG files are not counted as textures.");

Console.WriteLine("WPF optimizer contract tests passed.");
