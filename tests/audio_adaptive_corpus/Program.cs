using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Systems;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;

if (args.Length != 3)
{
    Console.Error.WriteLine("Usage: AudioAdaptiveCorpus <original-addon-root> <manual-baseline-root> <fresh-output-root>");
    return 2;
}

string originalRoot = Path.GetFullPath(args[0]);
string baselineRoot = Path.GetFullPath(args[1]);
string outputRoot = Path.GetFullPath(args[2]);
if (!Directory.Exists(originalRoot) || !Directory.Exists(baselineRoot))
    throw new DirectoryNotFoundException("Original and baseline roots must exist.");
if (Directory.Exists(outputRoot) && Directory.EnumerateFileSystemEntries(outputRoot).Any())
    throw new InvalidOperationException("Output root must be new or empty: " + outputRoot);
Directory.CreateDirectory(outputRoot);

string[] originals = Directory.GetFiles(originalRoot, "*.wav", SearchOption.AllDirectories);
if (originals.Length == 0)
    throw new InvalidOperationException("The original corpus contains no WAV files.");
foreach (string original in originals)
{
    string relative = Path.GetRelativePath(originalRoot, original);
    string baseline = Path.Combine(baselineRoot, relative);
    if (!File.Exists(baseline))
        throw new FileNotFoundException("Matching baseline WAV is missing.", baseline);
    string output = Path.Combine(outputRoot, relative);
    Directory.CreateDirectory(Path.GetDirectoryName(output)!);
    File.Copy(original, output, overwrite: false);
}

string dataStatic = Path.Combine(originalRoot, "data_static");
if (Directory.Exists(dataStatic))
{
    foreach (string json in Directory.GetFiles(dataStatic, "*.json", SearchOption.AllDirectories))
    {
        string relative = Path.GetRelativePath(originalRoot, json);
        string output = Path.Combine(outputRoot, relative);
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        File.Copy(json, output, overwrite: false);
    }
}

var originalHashes = originals.ToDictionary(
    path => Path.GetRelativePath(originalRoot, path).Replace('\\', '/'),
    Sha256,
    StringComparer.OrdinalIgnoreCase);
string[] baselines = originals.Select(path => Path.Combine(baselineRoot, Path.GetRelativePath(originalRoot, path))).ToArray();
var baselineHashes = baselines.ToDictionary(
    path => Path.GetRelativePath(baselineRoot, path).Replace('\\', '/'),
    Sha256,
    StringComparer.OrdinalIgnoreCase);

AudioContext.WavSampleRate = 22050;
AudioContext.WavChannels = 1;
AudioContext.WavCodec = AudioContext.WavCodecKind.Pcm16;
AudioContext.WavOptimization = AudioContext.WavOptimizationKind.AdaptiveConservative;
AudioContext.PreserveLoopMetadata = true;

var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
var compressor = new CompressAddonSystem(outputRoot);
compressor.IncludeWAV();
compressor.e_CompletedCompress += () => completion.TrySetResult(true);
var stopwatch = Stopwatch.StartNew();
compressor.StartCompress();
await completion.Task.ConfigureAwait(false);
stopwatch.Stop();

string[] outputs = Directory.GetFiles(outputRoot, "*.wav", SearchOption.AllDirectories);
long originalBytes = originals.Sum(path => new FileInfo(path).Length);
long baselineBytes = baselines.Sum(path => new FileInfo(path).Length);
long outputBytes = outputs.Sum(path => new FileInfo(path).Length);
bool originalsUnchanged = originals.All(path => originalHashes[Path.GetRelativePath(originalRoot, path).Replace('\\', '/')] == Sha256(path));
bool baselinesUnchanged = baselines.All(path => baselineHashes[Path.GetRelativePath(baselineRoot, path).Replace('\\', '/')] == Sha256(path));
string adaptiveReportPath = GmodAddonCompressor.Objects.WAVEdit.LastAdaptiveReportPath ?? "";
bool reportOutsideAddon = File.Exists(adaptiveReportPath) &&
    !Directory.GetFiles(outputRoot, "*.json", SearchOption.TopDirectoryOnly).Any();
var summary = new
{
    OriginalRoot = originalRoot,
    BaselineRoot = baselineRoot,
    OutputRoot = outputRoot,
    FileCount = outputs.Length,
    OriginalBytes = originalBytes,
    BaselineBytes = baselineBytes,
    OutputBytes = outputBytes,
    SavedVsBaselineBytes = baselineBytes - outputBytes,
    SavedVsBaselinePercent = 100.0 * (baselineBytes - outputBytes) / baselineBytes,
    WallClockMilliseconds = stopwatch.Elapsed.TotalMilliseconds,
    OriginalsUnchanged = originalsUnchanged,
    BaselinesUnchanged = baselinesUnchanged,
    AdaptiveReportPath = adaptiveReportPath,
    ReportOutsideAddon = reportOutsideAddon
};
string summaryPath = Path.Combine(outputRoot, "corpus_comparison.json");
await File.WriteAllTextAsync(summaryPath, JsonSerializer.Serialize(summary, new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine(JsonSerializer.Serialize(summary, new JsonSerializerOptions { WriteIndented = true }));
return originalsUnchanged && baselinesUnchanged && reportOutsideAddon && outputs.Length == originals.Length ? 0 : 1;

static string Sha256(string path)
{
    using FileStream stream = File.OpenRead(path);
    using SHA256 algorithm = SHA256.Create();
    return Convert.ToHexString(algorithm.ComputeHash(stream));
}
