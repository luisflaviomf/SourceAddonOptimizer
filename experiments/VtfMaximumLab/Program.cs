using System.Diagnostics;
using System.Security.Cryptography;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;
using VtfMaximumLab.Baseline;
using VtfMaximumLab.Candidates;
using VtfMaximumLab.Encoding;
using VtfMaximumLab.Experiment;
using VtfMaximumLab.Images;
using VtfMaximumLab.Inventory;
using VtfMaximumLab.Metrics;
using VtfMaximumLab.Sampling;
using VtfMaximumLab.Tools;
using VtfMaximumLab.Validation;
using GmodAddonCompressor.Systems;
using ImageMagick;

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
        "full-compress" => await RunFullCompressAsync(args.Skip(1).ToArray()),
        "full-validate" => RunFullValidate(args.Skip(1).ToArray()),
        "full-metrics" => RunFullMetrics(args.Skip(1).ToArray()),
        _ => UnknownCommand(args[0])
    };
}
catch (Exception exception)
{
    Console.Error.WriteLine($"ERROR: {exception.Message}");
    return 1;
}

static async Task<int> RunFullCompressAsync(string[] args)
{
    IReadOnlyDictionary<string, string> options = ParseOptions(args);
    if (!options.TryGetValue("--root", out string? addonRoot) ||
        !options.TryGetValue("--mode", out string? modeText))
    {
        Console.Error.WriteLine("full-compress requires --root and --mode magick|magick-plus|maximum.");
        return 2;
    }

    CompressPipelineMode mode = modeText.Equals("maximum", StringComparison.OrdinalIgnoreCase)
        ? CompressPipelineMode.Maximum
        : modeText.Equals("magick-plus", StringComparison.OrdinalIgnoreCase)
            ? CompressPipelineMode.MagickPlus
            : modeText.Equals("magick", StringComparison.OrdinalIgnoreCase)
                ? CompressPipelineMode.Magick
                : throw new ArgumentException("full-compress mode must be magick, magick-plus or maximum.");
    string root = Path.GetFullPath(addonRoot);
    if (!Directory.Exists(root))
        throw new DirectoryNotFoundException(root);

    ImageContext.Resolution = 2;
    ImageContext.TaargetWidth = 8;
    ImageContext.TargetHeight = 8;
    ImageContext.SkipWidth = 0;
    ImageContext.SkipHeight = 0;
    ImageContext.ReduceExactlyToLimits = false;
    ImageContext.KeepImageAspectRatio = true;
    AudioContext.SamplingFrequency = 22050;
    AudioContext.WavSampleRate = 22050;
    AudioContext.WavChannels = 1;
    AudioContext.WavCodec = AudioContext.WavCodecKind.Pcm16;
    AudioContext.PreserveLoopMetadata = true;

    VmtSyntaxRepairService.RepairUnder(root);
    long beforeBytes = SumTree(root, excludedReports: true);
    long beforeVtfBytes = SumExtension(root, ".vtf");
    int beforeFiles = Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories).Count();
    var stopwatch = Stopwatch.StartNew();
    var pipelineOptions = new CompressPipelineOptions
    {
        Mode = mode,
        UseMagickForAggressivePng = false,
        MaximumVtfParallelism = 10
    };
    var compressor = new CompressAddonSystem(root, pipelineOptions: pipelineOptions);
    compressor.IncludeVTF();
    compressor.IncludeWAV();
    compressor.IncludeMP3();
    compressor.IncludeOGG();
    compressor.IncludeJPG();
    compressor.IncludePNG();
    int completedFiles = 0;
    int totalFiles = 0;
    compressor.e_ProgressChanged += (path, completed, total) =>
    {
        completedFiles = completed;
        totalFiles = total;
        if (completed == 1 || completed == total || completed % 25 == 0)
            Console.WriteLine($"PROGRESS {mode} {completed}/{total} {path}");
    };
    compressor.e_MaximumProgress += progress =>
    {
        if (progress.Stage is "rejected" or "preserved")
            Console.WriteLine($"MAXIMUM {progress.Stage} {progress.RelativePath} {progress.Candidate}");
    };
    var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
    compressor.e_CompletedCompress += () => completion.TrySetResult(true);
    compressor.StartCompress();
    await completion.Task.ConfigureAwait(false);
    stopwatch.Stop();

    string[] vtfs = Directory.EnumerateFiles(root, "*.vtf", SearchOption.AllDirectories).ToArray();
    int invalidVtfs = vtfs.Count(path => !VtfDocumentReader.TryRead(path, out _, out _));
    var report = new
    {
        mode = mode.ToString(),
        root,
        maximumVtfParallelism = mode == CompressPipelineMode.Maximum ? 10 : 0,
        magickAggressivePng = false,
        settings = new { resolutionFactor = 2, minimumWidth = 8, minimumHeight = 8, keepAspectRatio = true },
        durationMilliseconds = stopwatch.Elapsed.TotalMilliseconds,
        beforeFiles,
        completedFiles,
        scheduledFiles = totalFiles,
        beforeBytes,
        afterBytes = SumTree(root, excludedReports: true),
        beforeVtfBytes,
        afterVtfBytes = SumExtension(root, ".vtf"),
        vtfCount = vtfs.Length,
        invalidVtfs
    };
    string reportPath = root.TrimEnd(Path.DirectorySeparatorChar) + ".full-compress-report.json";
    File.WriteAllText(reportPath, System.Text.Json.JsonSerializer.Serialize(
        report, new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
    Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(report));
    Console.WriteLine($"REPORT {reportPath}");
    return invalidVtfs == 0 ? 0 : 1;
}

static long SumExtension(string root, string extension) =>
    Directory.EnumerateFiles(root, "*" + extension, SearchOption.AllDirectories)
        .Sum(path => new FileInfo(path).Length);

static long SumTree(string root, bool excludedReports) =>
    Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
        .Where(path => !excludedReports || !Path.GetFileName(path).StartsWith("gmod_optimizer_", StringComparison.OrdinalIgnoreCase))
        .Sum(path => new FileInfo(path).Length);

static int RunFullValidate(string[] args)
{
    IReadOnlyDictionary<string, string> options = ParseOptions(args);
    if (!options.TryGetValue("--original", out string? originalRoot) ||
        !options.TryGetValue("--baseline", out string? baselineRoot) ||
        !options.TryGetValue("--maximum", out string? maximumRoot))
    {
        Console.Error.WriteLine("full-validate requires --original, --baseline and --maximum.");
        return 2;
    }

    string original = Path.GetFullPath(originalRoot);
    string baseline = Path.GetFullPath(baselineRoot);
    string maximum = Path.GetFullPath(maximumRoot);
    string[] relativeVtfs = Directory.EnumerateFiles(original, "*.vtf", SearchOption.AllDirectories)
        .Select(path => Path.GetRelativePath(original, path))
        .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
        .ToArray();
    var textureResults = new List<object>(relativeVtfs.Length);
    int parseErrors = 0;
    int baselineStructuralErrors = 0;
    int maximumStructuralErrors = 0;
    int baselineLargerThanOriginal = 0;
    int maximumLargerThanOriginal = 0;
    foreach (string relative in relativeVtfs)
    {
        string originalPath = Path.Combine(original, relative);
        string baselinePath = Path.Combine(baseline, relative);
        string maximumPath = Path.Combine(maximum, relative);
        var baselineErrors = new List<string>();
        var maximumErrors = new List<string>();
        if (!VtfDocumentReader.TryRead(originalPath, out VtfDocumentInfo originalDocument, out string originalError))
        {
            parseErrors++;
            textureResults.Add(new { relative, originalError });
            continue;
        }
        if (!VtfDocumentReader.TryRead(baselinePath, out VtfDocumentInfo baselineDocument, out string baselineError))
        {
            parseErrors++;
            baselineErrors.Add("parse:" + baselineError);
        }
        else
        {
            baselineErrors.AddRange(VtfMaximumStructuralValidator.ValidatePair(originalDocument, baselineDocument));
        }
        if (!VtfDocumentReader.TryRead(maximumPath, out VtfDocumentInfo maximumDocument, out string maximumError))
        {
            parseErrors++;
            maximumErrors.Add("parse:" + maximumError);
        }
        else
        {
            maximumErrors.AddRange(VtfMaximumStructuralValidator.ValidatePair(originalDocument, maximumDocument));
        }
        if (new FileInfo(baselinePath).Length > new FileInfo(originalPath).Length)
        {
            baselineLargerThanOriginal++;
            baselineErrors.Add("larger_than_original");
        }
        if (new FileInfo(maximumPath).Length > new FileInfo(originalPath).Length)
        {
            maximumLargerThanOriginal++;
            maximumErrors.Add("larger_than_original");
        }
        baselineStructuralErrors += baselineErrors.Count;
        maximumStructuralErrors += maximumErrors.Count;
        textureResults.Add(new { relative, baselineErrors, maximumErrors });
    }

    string[] processedNonVtfExtensions = { ".wav", ".mp3", ".ogg", ".jpg", ".jpeg", ".png", ".vmt" };
    bool processedNonVtfEqual = CompareSelectedFiles(baseline, maximum,
        path => processedNonVtfExtensions.Contains(Path.GetExtension(path), StringComparer.OrdinalIgnoreCase) &&
                !path.EndsWith(".png", StringComparison.OrdinalIgnoreCase)) &&
        ComparePngPixels(baseline, maximum);
    bool untouchedFilesEqual = CompareSelectedFiles(original, baseline, path =>
        !Path.GetFileName(path).StartsWith("gmod_optimizer_", StringComparison.OrdinalIgnoreCase) &&
        !processedNonVtfExtensions.Contains(Path.GetExtension(path), StringComparer.OrdinalIgnoreCase) &&
        !path.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase)) &&
        CompareSelectedFiles(original, maximum, path =>
            !Path.GetFileName(path).StartsWith("gmod_optimizer_", StringComparison.OrdinalIgnoreCase) &&
            !processedNonVtfExtensions.Contains(Path.GetExtension(path), StringComparer.OrdinalIgnoreCase) &&
            !path.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase));
    var report = new
    {
        original,
        baseline,
        maximum,
        textureCount = relativeVtfs.Length,
        parseErrors,
        baselineStructuralErrors,
        maximumStructuralErrors,
        baselineLargerThanOriginal,
        maximumLargerThanOriginal,
        processedNonVtfEqual,
        untouchedFilesEqual,
        textures = textureResults
    };
    string reportPath = maximum.TrimEnd(Path.DirectorySeparatorChar) + ".full-validation-report.json";
    File.WriteAllText(reportPath, System.Text.Json.JsonSerializer.Serialize(
        report, new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
    Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(new
    {
        report.textureCount,
        report.parseErrors,
        report.baselineStructuralErrors,
        report.maximumStructuralErrors,
        report.baselineLargerThanOriginal,
        report.maximumLargerThanOriginal,
        report.processedNonVtfEqual,
        report.untouchedFilesEqual,
        reportPath
    }));
    return parseErrors == 0 && maximumStructuralErrors == 0 &&
           maximumLargerThanOriginal == 0 && processedNonVtfEqual && untouchedFilesEqual ? 0 : 1;
}

static bool CompareSelectedFiles(string leftRoot, string rightRoot, Func<string, bool> predicate)
{
    Dictionary<string, string> left = Directory.EnumerateFiles(leftRoot, "*", SearchOption.AllDirectories)
        .Where(predicate)
        .ToDictionary(path => Path.GetRelativePath(leftRoot, path), HashFile, StringComparer.OrdinalIgnoreCase);
    Dictionary<string, string> right = Directory.EnumerateFiles(rightRoot, "*", SearchOption.AllDirectories)
        .Where(predicate)
        .ToDictionary(path => Path.GetRelativePath(rightRoot, path), HashFile, StringComparer.OrdinalIgnoreCase);
    return left.Count == right.Count && left.All(pair =>
        right.TryGetValue(pair.Key, out string? hash) && hash == pair.Value);
}

static bool ComparePngPixels(string leftRoot, string rightRoot)
{
    string[] leftFiles = Directory.EnumerateFiles(leftRoot, "*.png", SearchOption.AllDirectories).ToArray();
    string[] rightFiles = Directory.EnumerateFiles(rightRoot, "*.png", SearchOption.AllDirectories).ToArray();
    if (leftFiles.Length != rightFiles.Length)
        return false;
    foreach (string leftPath in leftFiles)
    {
        string rightPath = Path.Combine(rightRoot, Path.GetRelativePath(leftRoot, leftPath));
        if (!File.Exists(rightPath))
            return false;
        using var left = new MagickImage(leftPath);
        using var right = new MagickImage(rightPath);
        if (left.Width != right.Width || left.Height != right.Height ||
            left.Compare(right, ErrorMetric.RootMeanSquared) != 0)
            return false;
    }
    return true;
}

static string HashFile(string path)
{
    using FileStream stream = File.OpenRead(path);
    using SHA256 sha256 = SHA256.Create();
    return Convert.ToHexString(sha256.ComputeHash(stream));
}

static int RunFullMetrics(string[] args)
{
    IReadOnlyDictionary<string, string> options = ParseOptions(args);
    if (!options.TryGetValue("--original", out string? originalRoot) ||
        !options.TryGetValue("--baseline", out string? baselineRoot) ||
        !options.TryGetValue("--maximum", out string? maximumRoot))
    {
        Console.Error.WriteLine("full-metrics requires --original, --baseline and --maximum.");
        return 2;
    }

    string original = Path.GetFullPath(originalRoot);
    string baseline = Path.GetFullPath(baselineRoot);
    string maximum = Path.GetFullPath(maximumRoot);
    IReadOnlyList<VtfInventoryEntry> inventory = VtfInventoryScanner.Scan(original);
    var records = new List<ExistingMetricRecord>(inventory.Count);
    int completed = 0;
    foreach (VtfInventoryEntry entry in inventory.OrderBy(item => item.RelativePath, StringComparer.OrdinalIgnoreCase))
    {
        string relative = entry.RelativePath.Replace('/', Path.DirectorySeparatorChar);
        string originalPath = Path.Combine(original, relative);
        string baselinePath = Path.Combine(baseline, relative);
        string maximumPath = Path.Combine(maximum, relative);
        DecodedVtfImage originalImage = DecodedVtfImageLoader.LoadTopFace(originalPath);
        DecodedVtfImage baselineImage = DecodedVtfImageLoader.LoadTopFace(baselinePath);
        DecodedVtfImage maximumImage = DecodedVtfImageLoader.LoadTopFace(maximumPath);
        bool requiresAlpha = VtfInventorySemantics.RequiresAlpha(entry);
        bool isCutout = requiresAlpha &&
                        (entry.AlphaClass == VtfAlphaClass.Cutout || entry.SemanticProfile.IsCutout);
        bool isNormal = VtfInventorySemantics.IsNormalMap(entry);
        bool isDxt5Normal = VtfInventorySemantics.IsDxt5NormalMap(entry, originalImage.Rgba);
        bool measureAlpha = requiresAlpha && !isDxt5Normal;
        double alphaThreshold = entry.SemanticProfile.AlphaTestReference;
        VtfQualityMetrics baselineMetrics = VtfQualityMetricCalculator.Compare(
            originalImage.Rgba, originalImage.Width, originalImage.Height,
            baselineImage.Rgba, baselineImage.Width, baselineImage.Height,
            measureAlpha, isCutout, isNormal, alphaThreshold, isDxt5Normal);
        VtfQualityMetrics maximumMetrics = VtfQualityMetricCalculator.Compare(
            originalImage.Rgba, originalImage.Width, originalImage.Height,
            maximumImage.Rgba, maximumImage.Width, maximumImage.Height,
            measureAlpha, isCutout, isNormal, alphaThreshold, isDxt5Normal);
        if (isCutout)
        {
            baselineMetrics = baselineMetrics with
            {
                CutoutMaxMipCoverageError = VtfCutoutMipCoverageCalculator.ComputeMaxError(
                    originalPath, baselinePath, alphaThreshold)
            };
            maximumMetrics = maximumMetrics with
            {
                CutoutMaxMipCoverageError = VtfCutoutMipCoverageCalculator.ComputeMaxError(
                    originalPath, maximumPath, alphaThreshold)
            };
        }
        records.Add(new ExistingMetricRecord(
            entry.RelativePath, measureAlpha, isCutout, isNormal, baselineMetrics, maximumMetrics));
        completed++;
        if (completed % 100 == 0 || completed == inventory.Count)
            Console.WriteLine($"METRICS {completed}/{inventory.Count}");
    }

    ExistingMetricRecord[] alpha = records.Where(record => record.MeasureAlpha).ToArray();
    ExistingMetricRecord[] cutout = records.Where(record => record.IsCutout).ToArray();
    ExistingMetricRecord[] normals = records.Where(record => record.IsNormal).ToArray();
    var summary = new
    {
        textureCount = records.Count,
        alphaTextureCount = alpha.Length,
        cutoutTextureCount = cutout.Length,
        normalTextureCount = normals.Length,
        maximumBetterComposite = records.Count(record =>
            record.Maximum.CompositeError < record.Baseline.CompositeError - 1e-12),
        equalComposite = records.Count(record =>
            Math.Abs(record.Maximum.CompositeError - record.Baseline.CompositeError) <= 1e-12),
        maximumWorseComposite = records.Count(record =>
            record.Maximum.CompositeError > record.Baseline.CompositeError + 1e-12),
        baselineMeanRgbSsim = records.Average(record => record.Baseline.RgbSsim),
        maximumMeanRgbSsim = records.Average(record => record.Maximum.RgbSsim),
        baselineMeanRgbPsnr = records.Average(record => record.Baseline.RgbPsnr),
        maximumMeanRgbPsnr = records.Average(record => record.Maximum.RgbPsnr),
        baselineMeanAlphaSsim = alpha.Length == 0 ? 1 : alpha.Average(record => record.Baseline.AlphaSsim),
        maximumMeanAlphaSsim = alpha.Length == 0 ? 1 : alpha.Average(record => record.Maximum.AlphaSsim),
        baselineMeanAlphaMae = alpha.Length == 0 ? 0 : alpha.Average(record => record.Baseline.AlphaMae),
        maximumMeanAlphaMae = alpha.Length == 0 ? 0 : alpha.Average(record => record.Maximum.AlphaMae),
        baselineMeanCutoutCoverageError = cutout.Length == 0 ? 0 : cutout.Average(record => record.Baseline.CutoutCoverageErrorTop),
        maximumMeanCutoutCoverageError = cutout.Length == 0 ? 0 : cutout.Average(record => record.Maximum.CutoutCoverageErrorTop),
        baselineMaxCutoutMipCoverageError = cutout.Length == 0 ? 0 : cutout.Max(record => record.Baseline.CutoutMaxMipCoverageError),
        maximumMaxCutoutMipCoverageError = cutout.Length == 0 ? 0 : cutout.Max(record => record.Maximum.CutoutMaxMipCoverageError),
        baselineMeanNormalDegrees = normals.Length == 0 ? 0 : normals.Average(record => record.Baseline.NormalMeanDegrees),
        maximumMeanNormalDegrees = normals.Length == 0 ? 0 : normals.Average(record => record.Maximum.NormalMeanDegrees),
        baselineMeanNormalP95Degrees = normals.Length == 0 ? 0 : normals.Average(record => record.Baseline.NormalP95Degrees),
        maximumMeanNormalP95Degrees = normals.Length == 0 ? 0 : normals.Average(record => record.Maximum.NormalP95Degrees)
    };
    string reportPath = maximum.TrimEnd(Path.DirectorySeparatorChar) + ".full-metrics-report.json";
    File.WriteAllText(reportPath, System.Text.Json.JsonSerializer.Serialize(
        new { summary, textures = records },
        new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
    Console.WriteLine(System.Text.Json.JsonSerializer.Serialize(summary));
    Console.WriteLine($"REPORT {reportPath}");
    return 0;
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
    Console.WriteLine("  full-compress --root <isolated-addon-root> --mode magick|magick-plus|maximum");
}

internal sealed record ExistingMetricRecord(
    string RelativePath,
    bool MeasureAlpha,
    bool IsCutout,
    bool IsNormal,
    VtfQualityMetrics Baseline,
    VtfQualityMetrics Maximum);
