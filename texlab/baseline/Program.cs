// texlab baseline: run the product's current VTF path ("Mídia e recursos", Standard/Magick VTF route)
// on a copy of an addon with resolution 2x and minimum 8x8, exactly like FullCompressionMedia.
// Usage: TexLab.Baseline <addon-root-copy> [parallelism]
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Objects;
using GmodAddonCompressor.Systems.Vtf;

if (args.Length < 1 || !Directory.Exists(args[0]))
{
    Console.Error.WriteLine("usage: TexLab.Baseline <addon-root-copy> [parallelism]");
    return 2;
}

string root = Path.GetFullPath(args[0]);
int parallelism = args.Length > 1 && int.TryParse(args[1], out int p) ? p : 4;

if (args.Contains("--screen"))
{
    // End-to-end product path: CompressAddonSystem in "Tela" mode (VTF only) -> ScreenVTFEdit -> worker.
    CompressDirectoryContext.DirectoryPath = root;
    ImageContext.Resolution = 2;
    ImageContext.TaargetWidth = ImageContext.TargetHeight = 8;
    ImageContext.KeepImageAspectRatio = true;
    long screenBefore = Directory.GetFiles(root, "*.vtf", SearchOption.AllDirectories).Sum(f => new FileInfo(f).Length);
    var system = new GmodAddonCompressor.Systems.CompressAddonSystem(root,
        pipelineOptions: new GmodAddonCompressor.Models.CompressPipelineOptions
        { Mode = GmodAddonCompressor.Models.CompressPipelineMode.Screen });
    system.IncludeVTF();
    var screenDone = new TaskCompletionSource<bool>();
    system.e_CompletedCompress += () => screenDone.TrySetResult(true);
    var sw = Stopwatch.StartNew();
    system.StartCompress();
    await screenDone.Task;
    long screenAfter = Directory.GetFiles(root, "*.vtf", SearchOption.AllDirectories).Sum(f => new FileInfo(f).Length);
    Console.WriteLine($"[screen] VTF bytes {screenBefore:N0} -> {screenAfter:N0} ({100.0 * screenAfter / Math.Max(1, screenBefore):0.0}%) " +
                      $"in {sw.Elapsed.TotalSeconds:0}s, failures={system.HasFailures}");
    return system.HasFailures ? 1 : 0;
}

ImageContext.Resolution = 2;
ImageContext.TaargetWidth = ImageContext.TargetHeight = 8;
ImageContext.SkipWidth = ImageContext.SkipHeight = 0;
ImageContext.ReduceExactlyToLimits = false;
ImageContext.KeepImageAspectRatio = true;
CompressDirectoryContext.DirectoryPath = root;

var vtf = new VTFEdit(root, new VtfResizePolicy(2, 8, 8, true));
vtf.Prepare();

string[] files = Directory.GetFiles(root, "*.vtf", SearchOption.AllDirectories);
var rows = new ConcurrentBag<object>();
var watch = Stopwatch.StartNew();
int done = 0;
await Parallel.ForEachAsync(files, new ParallelOptions { MaxDegreeOfParallelism = parallelism }, async (path, _) =>
{
    long before = new FileInfo(path).Length;
    string reason;
    bool applied = false, failure = false;
    string target = "";
    if (!AddonVtfCompressionPlanner.TryReadMetadata(path, out var metadata))
    {
        reason = "invalid_header";
        failure = true;
    }
    else
    {
        var result = await vtf.TryCompressWithSplitRebuildAsync(path, metadata, "[Baseline]");
        applied = result.Applied;
        failure = result.IsOperationalFailure;
        reason = result.Reason;
        target = result.TargetFormat;
    }
    long after = new FileInfo(path).Length;
    rows.Add(new
    {
        path = Path.GetRelativePath(root, path).Replace('\\', '/'),
        before,
        after,
        applied,
        failure,
        reason,
        target
    });
    int n = Interlocked.Increment(ref done);
    if (n % 25 == 0 || n == files.Length)
        Console.WriteLine($"{n}/{files.Length} {watch.Elapsed.TotalSeconds:0}s");
});

var ordered = rows.OrderBy(r => (string)r.GetType().GetProperty("path")!.GetValue(r)!).ToArray();
long totalBefore = ordered.Sum(r => (long)r.GetType().GetProperty("before")!.GetValue(r)!);
long totalAfter = ordered.Sum(r => (long)r.GetType().GetProperty("after")!.GetValue(r)!);
string report = Path.Combine(Path.GetDirectoryName(root)!, Path.GetFileName(root) + ".baseline.json");
File.WriteAllText(report, JsonSerializer.Serialize(new
{
    policy = "VTFEdit resize 2x, minimum 8x8, keep aspect (product Standard/Magick VTF route)",
    files = ordered.Length,
    totalBefore,
    totalAfter,
    seconds = watch.Elapsed.TotalSeconds,
    rows = ordered
}, new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine($"VTF bytes {totalBefore:N0} -> {totalAfter:N0} ({100.0 * totalAfter / Math.Max(1, totalBefore):0.0}%) in {watch.Elapsed.TotalSeconds:0}s");
Console.WriteLine($"report: {report}");
return 0;
