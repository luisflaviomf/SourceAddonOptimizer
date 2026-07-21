using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Tools;
using Microsoft.Extensions.Logging;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using VtfMaximumLab.Experiment;
using VtfMaximumLab.Inventory;
using VtfMaximumLab.Reporting;

namespace GmodAddonCompressor.Objects
{
    internal sealed record MaximumVtfProgress(
        string RelativePath,
        string Stage,
        string Candidate,
        long OriginalBytes,
        long ResultBytes,
        int AcceptedCandidates,
        int RejectedCandidates,
        double DurationMilliseconds);

    internal sealed class MaximumVTFEdit : ICompress, ICompressPreparation, ICompressFinalizer
    {
        private readonly string _addonRoot;
        private readonly VTFEdit _currentCompressor;
        private readonly Lazy<IReadOnlyDictionary<string, VtfInventoryEntry>> _inventory;
        private readonly Lazy<VtfMaximumExperimentRunner> _runner;
        private readonly ConcurrentBag<VtfTextureExperimentRecord> _records = new();
        private readonly ILogger _logger = LogSystem.CreateLogger<MaximumVTFEdit>();
        private readonly Stopwatch _stopwatch = new();
        private readonly string _workRoot;

        internal MaximumVTFEdit(string addonRoot)
        {
            _addonRoot = Path.GetFullPath(addonRoot);
            _currentCompressor = new VTFEdit(
                _addonRoot,
                new VtfResizePolicy(ResolutionFactor: 2, MinimumWidth: 8, MinimumHeight: 8, KeepAspectRatio: true));
            _workRoot = Path.Combine(
                ToolPaths.WorkRoot,
                "vtf-maximum",
                $"{Path.GetFileName(_addonRoot.TrimEnd(Path.DirectorySeparatorChar))}_{DateTime.UtcNow:yyyyMMdd_HHmmss}");
            _inventory = new Lazy<IReadOnlyDictionary<string, VtfInventoryEntry>>(
                BuildInventory,
                LazyThreadSafetyMode.ExecutionAndPublication);
            _runner = new Lazy<VtfMaximumExperimentRunner>(
                () => new VtfMaximumExperimentRunner(AppContext.BaseDirectory),
                LazyThreadSafetyMode.ExecutionAndPublication);
        }

        internal event Action<MaximumVtfProgress>? ProgressChanged;
        internal string ReportPath => Path.Combine(_addonRoot, "gmod_optimizer_maximum_report.json");

        public void Prepare()
        {
            _currentCompressor.Prepare();
            Directory.CreateDirectory(_workRoot);
            _stopwatch.Start();
        }

        public async Task Compress(string filePath)
        {
            string fullPath = Path.GetFullPath(filePath);
            string relativePath = Normalize(Path.GetRelativePath(_addonRoot, fullPath));
            if (!_inventory.Value.TryGetValue(relativePath, out VtfInventoryEntry? entry))
            {
                _logger.LogWarning($"[MAXIMUM] Preserving unscanned VTF: {relativePath}");
                return;
            }

            string id = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(
                Encoding.UTF8.GetBytes(relativePath))).Substring(0, 16).ToLowerInvariant();
            string textureRoot = Path.Combine(_workRoot, id);
            string originalPath = Path.Combine(textureRoot, "original.vtf");
            string maximumPath = Path.Combine(textureRoot, "maximum.vtf");
            Directory.CreateDirectory(textureRoot);
            File.Copy(fullPath, originalPath, overwrite: true);
            File.Copy(fullPath, maximumPath, overwrite: true);
            ProgressChanged?.Invoke(new MaximumVtfProgress(
                relativePath, "analyzing", "Compress atual", entry.SizeBytes, entry.SizeBytes, 0, 0, 0));

            try
            {
                await _currentCompressor.Compress(fullPath).ConfigureAwait(false);
                VtfTextureExperimentRecord record = await _runner.Value.ProcessTextureAsync(
                    entry,
                    originalPath,
                    fullPath,
                    maximumPath,
                    textureRoot,
                    CancellationToken.None).ConfigureAwait(false);
                File.Copy(maximumPath, fullPath, overwrite: true);
                _records.Add(record);

                int accepted = record.Candidates.Count(candidate => candidate.Accepted);
                int rejected = record.Candidates.Count - accepted;
                string candidate = string.IsNullOrWhiteSpace(record.WinnerEncoder)
                    ? "original preservado"
                    : $"{record.WinnerEncoder} x{record.WinnerScaleDivisor}";
                ProgressChanged?.Invoke(new MaximumVtfProgress(
                    relativePath,
                    record.Decision == "reduced" ? "accepted" : "preserved",
                    candidate,
                    record.OriginalBytes,
                    record.MaximumBytes,
                    accepted,
                    rejected,
                    record.DurationMilliseconds));
                _logger.LogInformation(
                    $"[MAXIMUM] {relativePath}: {candidate}, {FormatBytes(record.OriginalBytes)} -> {FormatBytes(record.MaximumBytes)}, accepted={accepted}, rejected={rejected}, {record.DurationMilliseconds / 1000:0.00}s");
            }
            catch (Exception exception)
            {
                File.Copy(originalPath, fullPath, overwrite: true);
                _logger.LogError(exception, $"[MAXIMUM] Rejected all processing for {relativePath}; original restored.");
                ProgressChanged?.Invoke(new MaximumVtfProgress(
                    relativePath, "rejected", exception.GetType().Name, entry.SizeBytes, entry.SizeBytes, 0, 1, 0));
            }
            finally
            {
                TryDeleteTextureWork(textureRoot);
            }
        }

        public Task CompleteAsync()
        {
            _stopwatch.Stop();
            VtfTextureExperimentRecord[] records = _records
                .OrderBy(record => record.RelativePath, StringComparer.OrdinalIgnoreCase)
                .ToArray();
            long originalBytes = records.Sum(record => record.OriginalBytes);
            long currentBytes = records.Sum(record => record.CurrentBytes);
            long maximumBytes = records.Sum(record => record.MaximumBytes);
            int accepted = records.Sum(record => record.Candidates.Count(candidate => candidate.Accepted));
            int rejected = records.Sum(record => record.Candidates.Count(candidate => !candidate.Accepted));
            var report = new
            {
                mode = "Maximum",
                startedUtc = DateTimeOffset.UtcNow - _stopwatch.Elapsed,
                durationMilliseconds = _stopwatch.Elapsed.TotalMilliseconds,
                originalBytes,
                currentBytes,
                maximumBytes,
                reductionVsOriginalPercent = Percent(originalBytes, maximumBytes),
                reductionVsCurrentPercent = Percent(currentBytes, maximumBytes),
                preservedTextures = records.Count(record => record.Decision == "preserved"),
                reducedTextures = records.Count(record => record.Decision == "reduced"),
                acceptedCandidates = accepted,
                rejectedCandidates = rejected,
                textures = records
            };
            var options = new JsonSerializerOptions { WriteIndented = true };
            string json = JsonSerializer.Serialize(report, options);
            File.WriteAllText(ReportPath, json);
            File.WriteAllText(Path.Combine(_workRoot, "maximum-report.json"), json);
            WriteCsv(Path.Combine(_addonRoot, "gmod_optimizer_maximum_report.csv"), records);

            ProgressChanged?.Invoke(new MaximumVtfProgress(
                "summary", "completed", $"{records.Length} VTFs", originalBytes, maximumBytes,
                accepted, rejected, _stopwatch.Elapsed.TotalMilliseconds));
            return Task.CompletedTask;
        }

        private IReadOnlyDictionary<string, VtfInventoryEntry> BuildInventory()
        {
            ProgressChanged?.Invoke(new MaximumVtfProgress(
                "inventory", "analyzing", "VMT/Lua/PCF + VTF", 0, 0, 0, 0, 0));
            return VtfInventoryScanner.Scan(_addonRoot)
                .ToDictionary(entry => Normalize(entry.RelativePath), StringComparer.OrdinalIgnoreCase);
        }

        private static void WriteCsv(string path, IEnumerable<VtfTextureExperimentRecord> records)
        {
            var builder = new StringBuilder();
            builder.AppendLine("path,original_bytes,current_bytes,maximum_bytes,decision,encoder,scale,rgb_ssim,rgb_psnr,flip_mean,flip_p95,alpha_ssim,alpha_mae,alpha_p99,cutout_coverage_top,cutout_coverage_mips,cutout_iou,normal_mean_deg,normal_p95_deg,normal_max_deg,duration_ms");
            foreach (VtfTextureExperimentRecord record in records)
            {
                var metrics = record.MaximumMetrics ?? record.CurrentMetrics;
                builder.AppendLine(string.Join(",", new[]
                {
                    Csv(record.RelativePath), record.OriginalBytes.ToString(CultureInfo.InvariantCulture),
                    record.CurrentBytes.ToString(CultureInfo.InvariantCulture), record.MaximumBytes.ToString(CultureInfo.InvariantCulture),
                    record.Decision, record.WinnerEncoder, record.WinnerScaleDivisor.ToString(CultureInfo.InvariantCulture),
                    metrics.RgbSsim.ToString("R", CultureInfo.InvariantCulture), metrics.RgbPsnr.ToString("R", CultureInfo.InvariantCulture),
                    metrics.FlipMean.ToString("R", CultureInfo.InvariantCulture), metrics.FlipP95.ToString("R", CultureInfo.InvariantCulture),
                    metrics.AlphaSsim.ToString("R", CultureInfo.InvariantCulture), metrics.AlphaMae.ToString("R", CultureInfo.InvariantCulture),
                    metrics.AlphaP99.ToString("R", CultureInfo.InvariantCulture), metrics.CutoutCoverageErrorTop.ToString("R", CultureInfo.InvariantCulture),
                    metrics.CutoutMaxMipCoverageError.ToString("R", CultureInfo.InvariantCulture), metrics.CutoutIou.ToString("R", CultureInfo.InvariantCulture),
                    metrics.NormalMeanDegrees.ToString("R", CultureInfo.InvariantCulture), metrics.NormalP95Degrees.ToString("R", CultureInfo.InvariantCulture),
                    metrics.NormalMaxDegrees.ToString("R", CultureInfo.InvariantCulture), record.DurationMilliseconds.ToString("R", CultureInfo.InvariantCulture)
                }));
            }
            File.WriteAllText(path, builder.ToString(), new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
        }

        private static double Percent(long before, long after) => before <= 0 ? 0 : (1.0 - (double)after / before) * 100;
        private void TryDeleteTextureWork(string textureRoot)
        {
            try
            {
                string workRoot = Path.GetFullPath(_workRoot).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                string target = Path.GetFullPath(textureRoot).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                if (!target.StartsWith(workRoot, StringComparison.OrdinalIgnoreCase))
                {
                    _logger.LogWarning($"[MAXIMUM] Refused to clean unexpected work path: {textureRoot}");
                    return;
                }
                if (Directory.Exists(textureRoot))
                    Directory.Delete(textureRoot, recursive: true);
            }
            catch (Exception exception)
            {
                _logger.LogWarning(exception, $"[MAXIMUM] Could not clean temporary candidate files: {textureRoot}");
            }
        }

        private static string Normalize(string path) => path.Replace('\\', '/');
        private static string Csv(string value) => $"\"{value.Replace("\"", "\"\"")}\"";
        private static string FormatBytes(long bytes) => bytes >= 1024 * 1024
            ? $"{bytes / 1024d / 1024d:0.00} MB"
            : bytes >= 1024 ? $"{bytes / 1024d:0.00} KB" : $"{bytes} B";
    }
}
