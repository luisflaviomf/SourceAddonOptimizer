using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Vtf;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json;
using VtfMaximumLab.Reporting;
using VtfMaximumLab.Sampling;

namespace VtfMaximumLab.Baseline;

internal sealed record CurrentCompressSettings(
    int ResolutionFactor,
    int MinimumWidth,
    int MinimumHeight,
    int SkipWidth,
    int SkipHeight,
    bool ReduceExactlyToLimits,
    bool KeepAspectRatio);

internal interface ICurrentVtfCompressor
{
    Task RunAsync(
        string treeRoot,
        CurrentCompressSettings settings,
        Action<string> onFileCompleted,
        CancellationToken cancellationToken);
}

internal sealed class ExistingCurrentVtfCompressor : ICurrentVtfCompressor
{
    public async Task RunAsync(
        string treeRoot,
        CurrentCompressSettings settings,
        Action<string> onFileCompleted,
        CancellationToken cancellationToken)
    {
        ImageContext.Resolution = settings.ResolutionFactor;
        ImageContext.TaargetWidth = settings.MinimumWidth;
        ImageContext.TargetHeight = settings.MinimumHeight;
        ImageContext.SkipWidth = settings.SkipWidth;
        ImageContext.SkipHeight = settings.SkipHeight;
        ImageContext.ReduceExactlyToLimits = settings.ReduceExactlyToLimits;
        ImageContext.KeepImageAspectRatio = settings.KeepAspectRatio;

        var pipelineOptions = new CompressPipelineOptions { Mode = CompressPipelineMode.Standard };
        var compressor = new CompressAddonSystem(treeRoot, pipelineOptions: pipelineOptions);
        compressor.IncludeVTF();
        var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        compressor.e_ProgressChanged += (path, _, _) => onFileCompleted(path);
        compressor.e_CompletedCompress += () => completion.TrySetResult(true);

        using CancellationTokenRegistration registration = cancellationToken.Register(() =>
        {
            compressor.StopCompress();
            completion.TrySetCanceled(cancellationToken);
        });
        compressor.StartCompress();
        await completion.Task.ConfigureAwait(false);
    }
}

internal sealed class CurrentCompressBaselineRunner
{
    private static readonly CurrentCompressSettings BaselineSettings = new(
        ResolutionFactor: 2,
        MinimumWidth: 8,
        MinimumHeight: 8,
        SkipWidth: 0,
        SkipHeight: 0,
        ReduceExactlyToLimits: false,
        KeepAspectRatio: true);

    private readonly ICurrentVtfCompressor _compressor;

    internal CurrentCompressBaselineRunner(ICurrentVtfCompressor compressor) => _compressor = compressor;

    internal async Task<CurrentCompressBaselineReport> RunAsync(
        string treeRoot,
        CancellationToken cancellationToken,
        string? reportPath = null)
    {
        string root = Path.GetFullPath(treeRoot);
        if (!Directory.Exists(root))
            throw new DirectoryNotFoundException($"Baseline tree was not found: {root}");

        IReadOnlyDictionary<string, VtfFileSnapshot> before = CaptureSnapshots(root);
        long originalMaterialsBytes = SumFileBytes(root);
        DateTimeOffset startedUtc = DateTimeOffset.UtcNow;
        var stopwatch = Stopwatch.StartNew();
        var completionTimes = new ConcurrentDictionary<string, double>(StringComparer.OrdinalIgnoreCase);

        await _compressor.RunAsync(
            root,
            BaselineSettings,
            path => completionTimes[Normalize(Path.GetRelativePath(root, path))] = stopwatch.Elapsed.TotalMilliseconds,
            cancellationToken).ConfigureAwait(false);
        stopwatch.Stop();

        IReadOnlyDictionary<string, VtfFileSnapshot> after = CaptureSnapshots(root);
        var records = before.Keys.OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .Select(path =>
            {
                VtfFileSnapshot beforeSnapshot = before[path];
                VtfFileSnapshot afterSnapshot = after.TryGetValue(path, out VtfFileSnapshot? value)
                    ? value
                    : MissingSnapshot("File disappeared during baseline.");
                bool preserved = beforeSnapshot.Sha256 == afterSnapshot.Sha256;
                return new VtfRunRecord(
                    path,
                    beforeSnapshot,
                    afterSnapshot,
                    completionTimes.TryGetValue(path, out double elapsed) ? elapsed : stopwatch.Elapsed.TotalMilliseconds,
                    preserved,
                    afterSnapshot.IsValid && afterSnapshot.SizeBytes < beforeSnapshot.SizeBytes);
            })
            .ToArray();

        var report = new CurrentCompressBaselineReport(
            root,
            startedUtc,
            stopwatch.Elapsed.TotalMilliseconds,
            records.Sum(record => record.Before.SizeBytes),
            records.Sum(record => record.After.SizeBytes),
            originalMaterialsBytes,
            SumFileBytes(root),
            records.Count(record => record.Preserved),
            records.Count(record => record.Reduced),
            records.Count(record => !record.After.IsValid),
            records);

        string outputPath = reportPath ?? Path.Combine(Directory.GetParent(root)?.FullName ?? root, "current-results.json");
        File.WriteAllText(outputPath, JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
        return report;
    }

    private static IReadOnlyDictionary<string, VtfFileSnapshot> CaptureSnapshots(string root)
    {
        return Directory.EnumerateFiles(root, "*.vtf", SearchOption.AllDirectories)
            .ToDictionary(
                path => Normalize(Path.GetRelativePath(root, path)),
                CaptureSnapshot,
                StringComparer.OrdinalIgnoreCase);
    }

    private static VtfFileSnapshot CaptureSnapshot(string path)
    {
        long size = new FileInfo(path).Length;
        string hash = VtfSampleSelector.Sha256File(path);
        if (!VtfDocumentReader.TryRead(path, out VtfDocumentInfo document, out string error))
            return new VtfFileSnapshot(size, hash, false, error, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0);

        return new VtfFileSnapshot(
            size,
            hash,
            true,
            string.Empty,
            document.MajorVersion,
            document.MinorVersion,
            document.Width,
            document.Height,
            document.HighResFormat,
            document.MipCount,
            document.Frames,
            document.Faces,
            document.Depth,
            document.Flags);
    }

    private static VtfFileSnapshot MissingSnapshot(string error) =>
        new(0, string.Empty, false, error, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0);

    private static long SumFileBytes(string root) =>
        Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories).Sum(path => new FileInfo(path).Length);

    private static string Normalize(string path) => path.Replace('\\', '/');
}
