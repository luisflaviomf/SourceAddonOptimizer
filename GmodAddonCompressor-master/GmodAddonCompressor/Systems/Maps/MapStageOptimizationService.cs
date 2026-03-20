using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Systems.Reporting;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Maps
{
    internal sealed class MapStageOptimizationOptions
    {
        internal IReadOnlyList<string> StageRoots { get; init; } = Array.Empty<string>();
        internal string WorkDir { get; init; } = string.Empty;
        internal string SummaryPath { get; init; } = string.Empty;
        internal bool IncludeVtf { get; init; }
        internal bool IncludeWav { get; init; }
        internal bool IncludeMp3 { get; init; }
        internal bool IncludeOgg { get; init; }
        internal bool IncludeJpg { get; init; }
        internal bool IncludePng { get; init; }
        internal bool IncludeLua { get; init; }
        internal int WavSampleRate { get; init; }
        internal int WavChannels { get; init; }
        internal AudioContext.WavCodecKind WavCodec { get; init; }
        internal int Mp3SampleRate { get; init; }
        internal int Mp3BitrateKbps { get; init; }
        internal int OggSampleRate { get; init; }
        internal int OggChannels { get; init; }
        internal double OggQuality { get; init; }
        internal bool PreserveLoopMetadata { get; init; }
        internal int ImageResolution { get; init; }
        internal int TargetWidth { get; init; }
        internal int TargetHeight { get; init; }
        internal int SkipWidth { get; init; }
        internal int SkipHeight { get; init; }
        internal bool ReduceExactlyToLimits { get; init; }
        internal bool KeepImageAspectRatio { get; init; }
        internal bool ImageMagickVtfCompress { get; init; }
        internal bool LuaMinimalistic { get; init; }
        internal Action<string>? Log { get; init; }
        internal Action<string, int, int>? Progress { get; init; }
    }

    internal sealed class MapStageOptimizationResult
    {
        internal string SummaryPath { get; init; } = string.Empty;
        internal DirectoryExtensionInventorySnapshot Before { get; init; } = new(Array.Empty<string>(), 0, 0, Array.Empty<DirectoryExtensionInventoryEntry>());
        internal DirectoryExtensionInventorySnapshot After { get; init; } = new(Array.Empty<string>(), 0, 0, Array.Empty<DirectoryExtensionInventoryEntry>());
        internal IReadOnlyList<string> ProcessedExtensions { get; init; } = Array.Empty<string>();
        internal IReadOnlyList<string> InventoriedOnlyExtensions { get; init; } = Array.Empty<string>();
        internal int StageRootsVisited { get; init; }
        internal int SafeVtfCount { get; init; }
        internal int SkippedSpecialVtfCount { get; init; }
        internal long SkippedSpecialVtfBytes { get; init; }
        internal IReadOnlyList<MapVtfSkipReasonSummary> SkippedSpecialVtfReasons { get; init; } = Array.Empty<MapVtfSkipReasonSummary>();
        internal int OptimizedSpecialVtfCount { get; init; }
        internal long OptimizedSpecialVtfBeforeBytes { get; init; }
        internal long OptimizedSpecialVtfAfterBytes { get; init; }
    }

    internal static class MapStageOptimizationService
    {
        internal static async Task<MapStageOptimizationResult> RunAsync(MapStageOptimizationOptions options)
        {
            var stageRoots = options.StageRoots
                .Where(path => !string.IsNullOrWhiteSpace(path) && Directory.Exists(path))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();

            if (stageRoots.Length == 0)
                throw new InvalidOperationException("No valid staging directories were provided.");

            ConfigureContexts(options);

            options.Log?.Invoke($"[MAP-OPT] Stage roots: {stageRoots.Length}");

            var before = await DirectoryExtensionInventoryScanner.ScanAsync(stageRoots, default);

            MapVtfSafetyReport? vtfSafety = null;
            MapSpecialVtfOptimizationSummary specialVtfOptimization = new();
            if (options.IncludeVtf)
            {
                vtfSafety = MapVtfSafetyClassifier.Analyze(stageRoots);
                if (vtfSafety.SkippedVtfCount > 0)
                {
                    string topReasons = string.Join(
                        ", ",
                        vtfSafety.ReasonSummaries
                            .Take(4)
                            .Select(reason => $"{reason.Reason}={reason.FileCount}"));
                    options.Log?.Invoke(
                        $"[MAP-OPT] Preserving {vtfSafety.SkippedVtfCount} special VTF(s) unchanged " +
                        $"({FormatBytes(vtfSafety.SkippedVtfBytes)}). Reasons: {topReasons}");
                }
                else
                {
                    options.Log?.Invoke("[MAP-OPT] No special staged VTFs needed preservation.");
                }

                specialVtfOptimization = await new MapSpecialVtfOptimizer()
                    .OptimizeSkyboxAsync(vtfSafety.SkippedFiles, options.WorkDir, options.Log, options.Progress);

                if (specialVtfOptimization.OptimizedCount > 0)
                {
                    options.Log?.Invoke(
                        $"[MAP-OPT] Optimized {specialVtfOptimization.OptimizedCount} special skybox VTF(s): " +
                        $"{FormatBytes(specialVtfOptimization.BeforeBytes)} -> {FormatBytes(specialVtfOptimization.AfterBytes)}");
                }
            }

            var enabledExtensions = BuildEnabledExtensions(options);
            var processedExtensions = before.Entries
                .Where(entry =>
                {
                    if (!enabledExtensions.Contains(entry.Extension))
                        return false;

                    if (string.Equals(entry.Extension, ".vtf", StringComparison.OrdinalIgnoreCase) &&
                        vtfSafety != null &&
                        vtfSafety.SafeVtfCount == 0)
                    {
                        return false;
                    }

                    return true;
                })
                .Select(entry => entry.Extension)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(ext => ext, StringComparer.OrdinalIgnoreCase)
                .ToArray();
            var inventoriedOnlyExtensions = before.Entries
                .Where(entry =>
                {
                    if (!enabledExtensions.Contains(entry.Extension))
                        return true;

                    return string.Equals(entry.Extension, ".vtf", StringComparison.OrdinalIgnoreCase) &&
                        vtfSafety != null &&
                        vtfSafety.SafeVtfCount == 0;
                })
                .Select(entry => entry.Extension)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(ext => ext, StringComparer.OrdinalIgnoreCase)
                .ToArray();

            if (processedExtensions.Length == 0)
                options.Log?.Invoke("[MAP-OPT] No staged files match the currently enabled compression types.");

            int visitedRoots = 0;
            foreach (string stageRoot in stageRoots)
            {
                bool hasTargets = StageHasEnabledTargets(stageRoot, enabledExtensions, vtfSafety);
                if (!hasTargets)
                {
                    options.Log?.Invoke($"[MAP-OPT] Skipping {stageRoot}: no supported staged assets.");
                    continue;
                }

                visitedRoots++;
                options.Log?.Invoke($"[MAP-OPT] Compressing staged assets in {stageRoot}");
                await RunCompressStageAsync(stageRoot, options, vtfSafety);
            }

            var after = await DirectoryExtensionInventoryScanner.ScanAsync(stageRoots, default);

            string summaryPath = string.IsNullOrWhiteSpace(options.SummaryPath)
                ? Path.Combine(options.WorkDir, "map_stage_optimize_summary.json")
                : options.SummaryPath;

            Directory.CreateDirectory(Path.GetDirectoryName(summaryPath) ?? options.WorkDir);
            WriteSummary(summaryPath, options, before, after, processedExtensions, inventoriedOnlyExtensions, visitedRoots, vtfSafety, specialVtfOptimization);

            var remainingSkippedFiles = BuildRemainingSkippedFiles(vtfSafety, specialVtfOptimization);

            return new MapStageOptimizationResult
            {
                SummaryPath = summaryPath,
                Before = before,
                After = after,
                ProcessedExtensions = processedExtensions,
                InventoriedOnlyExtensions = inventoriedOnlyExtensions,
                StageRootsVisited = visitedRoots,
                SafeVtfCount = vtfSafety?.SafeVtfCount ?? 0,
                SkippedSpecialVtfCount = remainingSkippedFiles.Count,
                SkippedSpecialVtfBytes = remainingSkippedFiles.Sum(file => file.TotalBytes),
                SkippedSpecialVtfReasons = BuildReasonSummaries(remainingSkippedFiles),
                OptimizedSpecialVtfCount = specialVtfOptimization.OptimizedCount,
                OptimizedSpecialVtfBeforeBytes = specialVtfOptimization.BeforeBytes,
                OptimizedSpecialVtfAfterBytes = specialVtfOptimization.AfterBytes
            };
        }

        private static async Task RunCompressStageAsync(string stageRoot, MapStageOptimizationOptions options, MapVtfSafetyReport? vtfSafety)
        {
            Func<FileInfo, bool>? fileFilter = null;
            if (vtfSafety != null)
            {
                fileFilter = file =>
                {
                    if (!string.Equals(file.Extension, ".vtf", StringComparison.OrdinalIgnoreCase))
                        return true;

                    return vtfSafety.IsSafeVtf(file.FullName);
                };
            }

            var compressSystem = new CompressAddonSystem(stageRoot, fileFilter);

            if (options.IncludeVtf) compressSystem.IncludeVTF();
            if (options.IncludeWav) compressSystem.IncludeWAV();
            if (options.IncludeMp3) compressSystem.IncludeMP3();
            if (options.IncludeOgg) compressSystem.IncludeOGG();
            if (options.IncludeJpg) compressSystem.IncludeJPG();
            if (options.IncludePng) compressSystem.IncludePNG();
            if (options.IncludeLua) compressSystem.IncludeLUA();

            var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            compressSystem.e_ProgressChanged += (filePath, fileIndex, filesCount) =>
            {
                options.Progress?.Invoke(filePath, fileIndex, filesCount);
            };
            compressSystem.e_CompletedCompress += () =>
            {
                completion.TrySetResult(true);
            };

            compressSystem.StartCompress();
            await completion.Task;
        }

        private static HashSet<string> BuildEnabledExtensions(MapStageOptimizationOptions options)
        {
            var extensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (options.IncludeVtf) extensions.Add(".vtf");
            if (options.IncludeWav) extensions.Add(".wav");
            if (options.IncludeMp3) extensions.Add(".mp3");
            if (options.IncludeOgg) extensions.Add(".ogg");
            if (options.IncludeJpg)
            {
                extensions.Add(".jpg");
                extensions.Add(".jpeg");
            }
            if (options.IncludePng) extensions.Add(".png");
            if (options.IncludeLua) extensions.Add(".lua");
            return extensions;
        }

        private static bool StageHasEnabledTargets(string stageRoot, HashSet<string> enabledExtensions, MapVtfSafetyReport? vtfSafety)
        {
            foreach (string filePath in Directory.EnumerateFiles(stageRoot, "*", SearchOption.AllDirectories))
            {
                string extension = Path.GetExtension(filePath);
                if (!enabledExtensions.Contains(extension))
                    continue;

                if (string.Equals(extension, ".vtf", StringComparison.OrdinalIgnoreCase) &&
                    vtfSafety != null &&
                    !vtfSafety.IsSafeVtf(filePath))
                {
                    continue;
                }

                return true;
            }

            return false;
        }

        private static void ConfigureContexts(MapStageOptimizationOptions options)
        {
            AudioContext.WavSampleRate = options.WavSampleRate;
            AudioContext.WavChannels = options.WavChannels;
            AudioContext.WavCodec = options.WavCodec;
            AudioContext.Mp3SampleRate = options.Mp3SampleRate;
            AudioContext.Mp3BitrateKbps = options.Mp3BitrateKbps;
            AudioContext.OggSampleRate = options.OggSampleRate;
            AudioContext.OggChannels = options.OggChannels;
            AudioContext.OggQuality = options.OggQuality;
            AudioContext.PreserveLoopMetadata = options.PreserveLoopMetadata;

            ImageContext.Resolution = options.ImageResolution;
            ImageContext.TaargetWidth = options.TargetWidth;
            ImageContext.TargetHeight = options.TargetHeight;
            ImageContext.SkipWidth = options.SkipWidth;
            ImageContext.SkipHeight = options.SkipHeight;
            ImageContext.ReduceExactlyToLimits = options.ReduceExactlyToLimits;
            ImageContext.KeepImageAspectRatio = options.KeepImageAspectRatio;
            ImageContext.ImageMagickVTFCompress = options.ImageMagickVtfCompress;

            LuaContext.ChangeOriginalCodeToMinimalistic = options.LuaMinimalistic;
        }

        private static void WriteSummary(
            string summaryPath,
            MapStageOptimizationOptions options,
            DirectoryExtensionInventorySnapshot before,
            DirectoryExtensionInventorySnapshot after,
            IReadOnlyList<string> processedExtensions,
            IReadOnlyList<string> inventoriedOnlyExtensions,
            int visitedRoots,
            MapVtfSafetyReport? vtfSafety,
            MapSpecialVtfOptimizationSummary specialVtfOptimization)
        {
            var beforeByExtension = before.Entries.ToDictionary(entry => entry.Extension, StringComparer.OrdinalIgnoreCase);
            var afterByExtension = after.Entries.ToDictionary(entry => entry.Extension, StringComparer.OrdinalIgnoreCase);
            var allExtensions = beforeByExtension.Keys
                .Concat(afterByExtension.Keys)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderByDescending(ext =>
                {
                    long beforeBytes = beforeByExtension.TryGetValue(ext, out var beforeEntry) ? beforeEntry.TotalBytes : 0;
                    long afterBytes = afterByExtension.TryGetValue(ext, out var afterEntry) ? afterEntry.TotalBytes : 0;
                    return Math.Max(beforeBytes, afterBytes);
                })
                .ThenBy(ext => ext, StringComparer.OrdinalIgnoreCase)
                .ToArray();

            var entryDiffs = allExtensions.Select(ext =>
            {
                beforeByExtension.TryGetValue(ext, out var beforeEntry);
                afterByExtension.TryGetValue(ext, out var afterEntry);
                long beforeBytes = beforeEntry?.TotalBytes ?? 0;
                long afterBytes = afterEntry?.TotalBytes ?? 0;
                int beforeFiles = beforeEntry?.FileCount ?? 0;
                int afterFiles = afterEntry?.FileCount ?? 0;
                long deltaBytes = afterBytes - beforeBytes;
                double? deltaPercent = beforeBytes > 0
                    ? Math.Round(((double)deltaBytes / beforeBytes) * 100.0, 2)
                    : null;

                return new
                {
                    extension = ext,
                    processed = processedExtensions.Contains(ext, StringComparer.OrdinalIgnoreCase),
                    before_files = beforeFiles,
                    after_files = afterFiles,
                    before_bytes = beforeBytes,
                    after_bytes = afterBytes,
                    delta_bytes = deltaBytes,
                    delta_percent = deltaPercent
                };
            }).ToArray();

            long deltaTotalBytes = after.TotalBytes - before.TotalBytes;
            double? deltaTotalPercent = before.TotalBytes > 0
                ? Math.Round(((double)deltaTotalBytes / before.TotalBytes) * 100.0, 2)
                : null;

            var remainingSkippedFiles = BuildRemainingSkippedFiles(vtfSafety, specialVtfOptimization);
            long skippedSpecialBytes = remainingSkippedFiles.Sum(file => file.TotalBytes);
            int skippedSpecialCount = remainingSkippedFiles.Count;
            var remainingReasonSummaries = BuildReasonSummaries(remainingSkippedFiles);

            var summary = new
            {
                summary_version = 1,
                run = new
                {
                    work_dir = options.WorkDir,
                    summary_path = summaryPath,
                    stage_roots = before.RootPaths,
                    visited_stage_roots = visitedRoots,
                    processed_extensions = processedExtensions,
                    inventoried_only_extensions = inventoriedOnlyExtensions
                },
                totals = new
                {
                    before_files = before.TotalFiles,
                    after_files = after.TotalFiles,
                    before_bytes = before.TotalBytes,
                    after_bytes = after.TotalBytes,
                    delta_bytes = deltaTotalBytes,
                    delta_percent = deltaTotalPercent
                },
                vtf_safety = new
                {
                    safe_vtf_count = vtfSafety?.SafeVtfCount ?? 0,
                    optimized_special_vtf_count = specialVtfOptimization.OptimizedCount,
                    optimized_special_vtf_before_bytes = specialVtfOptimization.BeforeBytes,
                    optimized_special_vtf_after_bytes = specialVtfOptimization.AfterBytes,
                    skipped_special_vtf_count = skippedSpecialCount,
                    skipped_special_vtf_bytes = skippedSpecialBytes,
                    skipped_special_vtf_reasons = remainingReasonSummaries
                        .Select(reason => new
                        {
                            reason = reason.Reason,
                            file_count = reason.FileCount,
                            total_bytes = reason.TotalBytes
                        })
                        .ToArray()
                },
                entries = entryDiffs
            };

            var json = JsonSerializer.Serialize(summary, new JsonSerializerOptions
            {
                WriteIndented = true
            });

            File.WriteAllText(summaryPath, json);
        }

        private static IReadOnlyList<MapVtfSkippedFile> BuildRemainingSkippedFiles(
            MapVtfSafetyReport? vtfSafety,
            MapSpecialVtfOptimizationSummary specialVtfOptimization)
        {
            if (vtfSafety == null || vtfSafety.SkippedFiles.Count == 0)
                return Array.Empty<MapVtfSkippedFile>();

            if (specialVtfOptimization.OptimizedPaths.Count == 0)
                return vtfSafety.SkippedFiles.ToArray();

            var optimizedPaths = new HashSet<string>(specialVtfOptimization.OptimizedPaths, StringComparer.OrdinalIgnoreCase);
            return vtfSafety.SkippedFiles
                .Where(file => !optimizedPaths.Contains(file.FullPath))
                .ToArray();
        }

        private static IReadOnlyList<MapVtfSkipReasonSummary> BuildReasonSummaries(IReadOnlyList<MapVtfSkippedFile> skippedFiles)
        {
            return skippedFiles
                .GroupBy(file => file.PrimaryReason, StringComparer.OrdinalIgnoreCase)
                .Select(group => new MapVtfSkipReasonSummary(
                    group.Key,
                    group.Count(),
                    group.Sum(file => file.TotalBytes)))
                .Where(item => item.FileCount > 0 || item.TotalBytes > 0)
                .OrderByDescending(item => item.TotalBytes)
                .ThenBy(item => item.Reason, StringComparer.OrdinalIgnoreCase)
                .ToArray();
        }

        private static string FormatBytes(long bytes)
        {
            double value = bytes;
            string[] units = { "B", "KB", "MB", "GB", "TB" };
            int unitIndex = 0;

            while (value >= 1024 && unitIndex < units.Length - 1)
            {
                value /= 1024;
                unitIndex++;
            }

            return $"{value:0.##} {units[unitIndex]}";
        }
    }
}
