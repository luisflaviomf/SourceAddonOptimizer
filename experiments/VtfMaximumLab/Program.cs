using System.Diagnostics;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;
using VtfMaximumLab.Baseline;
using VtfMaximumLab.Candidates;
using VtfMaximumLab.Encoding;
using VtfMaximumLab.Experiment;
using VtfMaximumLab.Images;
using VtfMaximumLab.Inventory;
using VtfMaximumLab.Sampling;
using VtfMaximumLab.Tools;
using VtfMaximumLab.Validation;
using GmodAddonCompressor.Systems;

if (args.Length == 0 || (args.Length == 1 && args[0] == "--help"))
{
    PrintUsage();
    return args.Length == 0 ? 2 : 0;
}

try
{
    return args[0].ToLowerInvariant() switch
    {
        "sample" => RunSample(args.Skip(1).ToArray()),
        "baseline" => await RunBaselineAsync(args.Skip(1).ToArray()),
        "encoder-smoke" => await RunEncoderSmokeAsync(args.Skip(1).ToArray()),
        "maximum" => await RunMaximumAsync(args.Skip(1).ToArray()),
        "validate" => RunValidate(args.Skip(1).ToArray()),
        "production-smoke" => await RunProductionSmokeAsync(args.Skip(1).ToArray()),
        _ => UnknownCommand(args[0])
    };
}

catch (Exception exception)
{
    Console.Error.WriteLine($"ERROR: {exception.Message}");
    return 1;
}

static async Task<int> RunProductionSmokeAsync(string[] args)
{
    IReadOnlyDictionary<string, string> options = ParseOptions(args);
    if (!options.TryGetValue("--root", out string? addonRoot))
    {
        Console.Error.WriteLine("production-smoke requires --root.");
        return 2;
    }

    var compressor = new CompressAddonSystem(
        Path.GetFullPath(addonRoot),
        pipelineOptions: new CompressPipelineOptions { Mode = CompressPipelineMode.Maximum });
    compressor.IncludeVTF();
    compressor.e_MaximumProgress += progress => Console.WriteLine(
        $"MAXIMUM {progress.Stage} {progress.RelativePath} {progress.Candidate} " +
        $"{progress.OriginalBytes}->{progress.ResultBytes} accepted={progress.AcceptedCandidates} rejected={progress.RejectedCandidates}");
    var completed = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
    compressor.e_CompletedCompress += () => completed.TrySetResult(true);
    compressor.StartCompress();
    await completed.Task.ConfigureAwait(false);
    return 0;
}

static int RunValidate(string[] args)
{
    IReadOnlyDictionary<string, string> options = ParseOptions(args);
    if (!options.TryGetValue("--root", out string? experimentRoot))
    {
        Console.Error.WriteLine("validate requires --root.");
        return 2;
    }
    VtfStructuralValidationReport report = VtfMaximumStructuralValidator.Validate(experimentRoot);
    string reportPath = Path.Combine(Path.GetFullPath(experimentRoot), "structural-validation.json");
    File.WriteAllText(reportPath, System.Text.Json.JsonSerializer.Serialize(
        report, new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
    Console.WriteLine($"Textures: {report.TextureCount}");
    Console.WriteLine($"Valid: {report.ValidCount}");
    Console.WriteLine($"Invalid: {report.InvalidCount}");
    Console.WriteLine($"Source addon unchanged: {report.SourceAddonUnchanged}");
    Console.WriteLine($"Related files unchanged: {report.RelatedFilesUnchanged}");
    Console.WriteLine($"Report: {reportPath}");
    foreach (VtfStructuralValidationItem item in report.Textures.Where(item => !item.Valid))
        Console.WriteLine($"INVALID {item.RelativePath}: {string.Join(',', item.Errors)}");
    return report.InvalidCount == 0 && report.GlobalErrors.Count == 0 ? 0 : 1;
}

static async Task<int> RunMaximumAsync(string[] args)
{
    IReadOnlyDictionary<string, string> options = ParseOptions(args);
    if (!options.TryGetValue("--root", out string? experimentRoot))
    {
        Console.Error.WriteLine("maximum requires --root.");
        return 2;
    }
    var runner = new VtfMaximumExperimentRunner(Directory.GetCurrentDirectory());
    var report = await runner.RunAsync(experimentRoot, CancellationToken.None);
    Console.WriteLine($"Original VTF bytes: {report.OriginalVtfBytes}");
    Console.WriteLine($"Current VTF bytes: {report.CurrentVtfBytes}");
    Console.WriteLine($"Maximum VTF bytes: {report.MaximumVtfBytes}");
    Console.WriteLine($"Maximum vs current: {(1.0 - (double)report.MaximumVtfBytes / report.CurrentVtfBytes) * 100:0.00}%");
    Console.WriteLine($"Preserved textures: {report.PreservedTextures}");
    Console.WriteLine($"Reduced textures: {report.ReducedTextures}");
    Console.WriteLine($"Rejected candidates: {report.RejectedCandidates}");
    Console.WriteLine($"Duration: {TimeSpan.FromMilliseconds(report.DurationMilliseconds)}");
    return 0;
}

static async Task<int> RunEncoderSmokeAsync(string[] args)
{
    IReadOnlyDictionary<string, string> options = ParseOptions(args);
    if (!options.TryGetValue("--root", out string? experimentRoot))
    {
        Console.Error.WriteLine("encoder-smoke requires --root.");
        return 2;
    }

    string root = Path.GetFullPath(experimentRoot);
    string originalTree = Path.Combine(root, "original");
    string sourcePath = Directory.EnumerateFiles(originalTree, "*.vtf", SearchOption.AllDirectories)
        .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
        .First(path =>
            VtfDocumentReader.TryRead(path, out VtfDocumentInfo document, out _) &&
            document.MinorVersion <= 2 && document.Frames == 1 && document.Faces == 1 && document.Depth == 1 &&
            document.Width >= 64 && document.Height >= 64 && document.HighResFormat is 13 or 15 or 20);
    if (!AddonVtfCompressionPlanner.TryReadMetadata(sourcePath, out VtfFileModel metadata) ||
        !VtfHighResImageDecoder.TryDecodeHighResRgba(sourcePath, metadata, out byte[] rgba))
    {
        throw new InvalidDataException("Could not decode the encoder smoke source.");
    }

    int width = Math.Max(4, metadata.Width / 2);
    int height = Math.Max(4, metadata.Height / 2);
    PreparedImage prepared = MaximumImagePreprocessor.Resize(
        rgba, metadata.Width, metadata.Height, width, height, false, false, 0.5);
    int mipCount = GetFullMipCount(width, height);
    ExternalToolPaths tools = ExternalToolPaths.Resolve(Directory.GetCurrentDirectory());
    IVtfCandidateEncoder[] encoders =
    {
        new TexconvCandidateEncoder("texconv-perceptual", tools.TexconvPath),
        new TexconvCandidateEncoder("texconv-uniform", tools.TexconvPath, "u"),
        new TexconvCandidateEncoder("texconv-dither", tools.TexconvPath, "d"),
        new CompressonatorCandidateEncoder(tools.CompressonatorPath)
    };

    foreach (IVtfCandidateEncoder encoder in encoders)
    {
        foreach (VtfTargetFormat format in new[] { VtfTargetFormat.Dxt1, VtfTargetFormat.Dxt5 })
        {
            string work = Path.Combine(root, "encoder-smoke", encoder.Name, format.ToString());
            Directory.CreateDirectory(work);
            string pngPath = Path.Combine(work, "input.png");
            string ddsPath = Path.Combine(work, "input.dds");
            string vtfPath = Path.Combine(work, "candidate.vtf");
            PreparedImagePngWriter.Write(prepared, pngPath);
            DdsBcDocument dds = await encoder.EncodeAsync(
                new DdsEncodingRequest(pngPath, ddsPath, format, mipCount, 0.5),
                CancellationToken.None);
            VtfBcFileBuilder.Build(sourcePath, dds, vtfPath, oneBitAlpha: false, preserveAlpha: format == VtfTargetFormat.Dxt5);
            if (!VtfDocumentReader.TryRead(vtfPath, out VtfDocumentInfo candidate, out string error))
                throw new InvalidDataException($"{encoder.Name}/{format}: {error}");
            Console.WriteLine($"{encoder.Name}/{format}: {candidate.Width}x{candidate.Height}, mips={candidate.MipCount}, bytes={candidate.FileLength}, VTF={candidate.MajorVersion}.{candidate.MinorVersion}");
        }
    }
    return 0;
}

static int GetFullMipCount(int width, int height)
{
    int count = 1;
    while (width > 1 || height > 1)
    {
        width = Math.Max(1, width / 2);
        height = Math.Max(1, height / 2);
        count++;
    }
    return count;
}
static async Task<int> RunBaselineAsync(string[] args)
{
    IReadOnlyDictionary<string, string> options = ParseOptions(args);
    if (!options.TryGetValue("--root", out string? experimentRoot))
    {
        Console.Error.WriteLine("baseline requires --root.");
        return 2;
    }

    string root = Path.GetFullPath(experimentRoot);
    string tree = Path.Combine(root, "current-2x-8x8");
    var runner = new CurrentCompressBaselineRunner(new ExistingCurrentVtfCompressor());
    var report = await runner.RunAsync(tree, CancellationToken.None, Path.Combine(root, "current-results.json"));
    Console.WriteLine($"Original VTF bytes: {report.OriginalVtfBytes}");
    Console.WriteLine($"Result VTF bytes: {report.ResultVtfBytes}");
    Console.WriteLine($"Original materials bytes: {report.OriginalMaterialsBytes}");
    Console.WriteLine($"Result materials bytes: {report.ResultMaterialsBytes}");
    Console.WriteLine($"Preserved: {report.PreservedCount}");
    Console.WriteLine($"Reduced: {report.ReducedCount}");
    Console.WriteLine($"Invalid: {report.InvalidCount}");
    Console.WriteLine($"Duration: {TimeSpan.FromMilliseconds(report.DurationMilliseconds)}");
    return report.InvalidCount == 0 ? 0 : 1;
}

static int RunSample(string[] args)
{
    IReadOnlyDictionary<string, string> options = ParseOptions(args);
    if (!options.TryGetValue("--addon", out string? addonRoot) ||
        !options.TryGetValue("--out", out string? outputRoot))
    {
        Console.Error.WriteLine("sample requires --addon and --out.");
        return 2;
    }

    var stopwatch = Stopwatch.StartNew();
    Console.WriteLine($"Scanning: {Path.GetFullPath(addonRoot)}");
    IReadOnlyList<VtfInventoryEntry> inventory = VtfInventoryScanner.Scan(addonRoot);
    Console.WriteLine($"Inventory: {inventory.Count} VTFs ({inventory.Sum(entry => entry.SizeBytes)} bytes)");

    VtfSampleManifest manifest = VtfSampleSelector.Select(
        inventory,
        count: 50,
        VtfSampleSelector.DefaultSeed,
        addonRoot);
    VtfSampleTreeCopier.Copy(manifest, outputRoot);
    stopwatch.Stop();

    Console.WriteLine($"Selected: {manifest.Items.Count}");
    Console.WriteLine($"Selection SHA-256: {manifest.SelectionSha256}");
    Console.WriteLine($"Selected bytes: {manifest.Items.Sum(item => item.SizeBytes)}");
    Console.WriteLine($"Output: {Path.GetFullPath(outputRoot)}");
    Console.WriteLine($"Duration: {stopwatch.Elapsed}");
    foreach (IGrouping<string, VtfSampleItem> group in manifest.Items
                 .SelectMany(item => item.Tags.Select(tag => (Tag: tag, Item: item)))
                 .GroupBy(pair => pair.Tag, pair => pair.Item)
                 .OrderBy(group => group.Key, StringComparer.Ordinal))
    {
        Console.WriteLine($"Tag {group.Key}: {group.Count()}");
    }

    return 0;
}

static IReadOnlyDictionary<string, string> ParseOptions(string[] args)
{
    if (args.Length % 2 != 0)
        throw new ArgumentException("Options must be provided as --name value pairs.");

    var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    for (int index = 0; index < args.Length; index += 2)
    {
        if (!args[index].StartsWith("--", StringComparison.Ordinal))
            throw new ArgumentException($"Invalid option: {args[index]}");
        result[args[index]] = args[index + 1];
    }

    return result;
}

static int UnknownCommand(string command)
{
    Console.Error.WriteLine($"Unknown command: {command}");
    PrintUsage();
    return 2;
}

static void PrintUsage()
{
    Console.WriteLine("VtfMaximumLab commands:");
    Console.WriteLine("  sample --addon <addon-root> --out <experiment-root>");
    Console.WriteLine("  baseline --root <experiment-root>");
    Console.WriteLine("  encoder-smoke --root <experiment-root>");
    Console.WriteLine("  maximum --root <experiment-root>");
    Console.WriteLine("  validate --root <experiment-root>");
}
