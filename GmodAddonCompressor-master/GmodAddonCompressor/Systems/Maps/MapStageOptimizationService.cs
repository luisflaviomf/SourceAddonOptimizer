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
            var enabledExtensions = BuildEnabledExtensions(options);
            var processedExtensions = before.Entries
                .Where(entry => enabledExtensions.Contains(entry.Extension))
                .Select(entry => entry.Extension)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(ext => ext, StringComparer.OrdinalIgnoreCase)
                .ToArray();
            var inventoriedOnlyExtensions = before.Entries
                .Where(entry => !enabledExtensions.Contains(entry.Extension))
                .Select(entry => entry.Extension)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(ext => ext, StringComparer.OrdinalIgnoreCase)
                .ToArray();

            if (processedExtensions.Length == 0)
                options.Log?.Invoke("[MAP-OPT] No staged files match the currently enabled compression types.");

            int visitedRoots = 0;
            foreach (string stageRoot in stageRoots)
            {
                var stageInventory = await DirectoryExtensionInventoryScanner.ScanAsync(new[] { stageRoot }, default);
                bool hasTargets = stageInventory.Entries.Any(entry => enabledExtensions.Contains(entry.Extension));
                if (!hasTargets)
                {
                    options.Log?.Invoke($"[MAP-OPT] Skipping {stageRoot}: no supported staged assets.");
                    continue;
                }

                visitedRoots++;
                options.Log?.Invoke($"[MAP-OPT] Compressing staged assets in {stageRoot}");
                await RunCompressStageAsync(stageRoot, options);
            }

            var after = await DirectoryExtensionInventoryScanner.ScanAsync(stageRoots, default);

            string summaryPath = string.IsNullOrWhiteSpace(options.SummaryPath)
                ? Path.Combine(options.WorkDir, "map_stage_optimize_summary.json")
                : options.SummaryPath;

            Directory.CreateDirectory(Path.GetDirectoryName(summaryPath) ?? options.WorkDir);
            WriteSummary(summaryPath, options, before, after, processedExtensions, inventoriedOnlyExtensions, visitedRoots);

            return new MapStageOptimizationResult
            {
                SummaryPath = summaryPath,
                Before = before,
                After = after,
                ProcessedExtensions = processedExtensions,
                InventoriedOnlyExtensions = inventoriedOnlyExtensions,
                StageRootsVisited = visitedRoots
            };
        }

        private static async Task RunCompressStageAsync(string stageRoot, MapStageOptimizationOptions options)
        {
            var compressSystem = new CompressAddonSystem(stageRoot);

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
            int visitedRoots)
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
                entries = entryDiffs
            };

            var json = JsonSerializer.Serialize(summary, new JsonSerializerOptions
            {
                WriteIndented = true
            });

            File.WriteAllText(summaryPath, json);
        }
    }
}
