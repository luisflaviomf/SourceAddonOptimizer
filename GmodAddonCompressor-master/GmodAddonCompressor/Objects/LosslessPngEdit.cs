using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Tools;
using Microsoft.Extensions.Logging;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Objects
{
    /// <summary>
    /// "PNG sem perda": the worker command <c>pnglossless</c> recompresses the PNG files (oxipng) and keeps a
    /// result only when it is smaller and decodes to exactly the same pixels. The image size never changes,
    /// unlike the Clássico PNG path (<see cref="PNGEdit"/>), which reduces the resolution.
    /// </summary>
    internal sealed class LosslessPngEdit : ICompress, ICompressBatch, ICompressSummary
    {
        private static readonly System.Globalization.CultureInfo PtBr = System.Globalization.CultureInfo.GetCultureInfo("pt-BR");

        private readonly string _addonRoot;
        private readonly ILogger _logger = LogSystem.CreateLogger<LosslessPngEdit>();

        internal LosslessPngEdit(string addonRoot)
        {
            _addonRoot = addonRoot;
        }

        internal string? LastReportPath { get; private set; }

        /// <summary>What the last run did, in the words of the interface (shown in the assistant's details).</summary>
        public string? Summary { get; private set; }

        public Task Compress(string pngFilePath) =>
            CompressBatchAsync(new[] { new FileInfo(pngFilePath) }, CancellationToken.None);

        public async Task CompressBatchAsync(IReadOnlyList<FileInfo> files, CancellationToken cancellationToken)
        {
            if (files.Count == 0)
                return;

            ToolExtractionSystem.EnsureSourceAddonOptimizerExtracted();

            string workDir = Path.Combine(ToolPaths.WorkRoot, "pnglossless");
            Directory.CreateDirectory(workDir);
            string stamp = $"{DateTime.Now:yyyyMMdd_HHmmss}_{Guid.NewGuid():N}";
            string report = Path.Combine(workDir, $"pnglossless_{stamp}.json");
            string list = Path.Combine(workDir, $"pnglossless_{stamp}.txt");
            // the worker only touches the files this run was asked to process
            await File.WriteAllLinesAsync(list, files.Select(f => f.FullName), new UTF8Encoding(false), cancellationToken)
                .ConfigureAwait(false);
            int threads = Math.Clamp(Environment.ProcessorCount / 2, 1, 16);

            var arguments = new List<string>
            {
                "pnglossless", _addonRoot, "--work", workDir, "--report", report, "--files-from", list,
                "--threads", threads.ToString(System.Globalization.CultureInfo.InvariantCulture)
            };
            _logger.LogInformation($"[PNG] Lossless recompression of {files.Count} PNG file(s).");
            try
            {
                int exitCode = await RunWorkerAsync(arguments, cancellationToken).ConfigureAwait(false);
                if (exitCode != 0 || !File.Exists(report))
                    throw new InvalidOperationException($"Lossless PNG recompression failed (exit {exitCode}).");
            }
            finally
            {
                try { File.Delete(list); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            }
            LastReportPath = report;

            using var json = JsonDocument.Parse(await File.ReadAllTextAsync(report, cancellationToken).ConfigureAwait(false));
            long before = json.RootElement.GetProperty("before").GetInt64();
            long after = json.RootElement.GetProperty("after").GetInt64();
            int changed = json.RootElement.GetProperty("changed").GetInt32();
            int failed = json.RootElement.GetProperty("failed").GetArrayLength();
            _logger.LogInformation(
                $"[PNG] {before:N0} -> {after:N0} bytes ({(before > 0 ? 100.0 * after / before : 100):0.0}%), " +
                $"{changed} of {files.Count} file(s) smaller with the same pixels" +
                (failed > 0 ? $", {failed} kept as they were after a failed check" : string.Empty) +
                $". Report: {report}");
            Summary =
                $"PNG sem perda: {(before / 1048576.0).ToString("N1", PtBr)} MiB → {(after / 1048576.0).ToString("N1", PtBr)} MiB · " +
                $"{changed.ToString("N0", PtBr)} de {files.Count.ToString("N0", PtBr)} arquivos menores, com os mesmos pixels" +
                (failed > 0 ? $" · {failed.ToString("N0", PtBr)} mantidos como estavam (conferência falhou)" : string.Empty);
        }

        private async Task<int> RunWorkerAsync(IReadOnlyList<string> arguments, CancellationToken cancellationToken)
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = ToolPaths.WorkerExePath,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
                WorkingDirectory = Path.GetDirectoryName(ToolPaths.WorkerExePath) ?? string.Empty
            };
            foreach (string argument in arguments)
                startInfo.ArgumentList.Add(argument);

            using var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };
            int lastLogged = -1;
            process.OutputDataReceived += (_, e) =>
            {
                if (e.Data == null)
                    return;
                if (e.Data.StartsWith("PNGLOSSLESS_PROGRESS: ", StringComparison.Ordinal))
                {
                    string[] parts = e.Data.Substring(22).Split('|');
                    if (parts.Length >= 2 && int.TryParse(parts[0], out int i) && int.TryParse(parts[1], out int n))
                    {
                        int step = n > 0 ? i * 10 / n : 0;
                        if (step != lastLogged)
                        {
                            lastLogged = step;
                            _logger.LogInformation($"[PNG] {i}/{n}");
                        }
                    }
                    return;
                }
                _logger.LogInformation("[PNG] " + e.Data);
            };
            process.ErrorDataReceived += (_, e) =>
            {
                if (!string.IsNullOrWhiteSpace(e.Data))
                    _logger.LogWarning("[PNG] " + e.Data);
            };
            if (!process.Start())
                throw new InvalidOperationException("Failed to start the optimizer worker.");
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            try
            {
                await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                if (!process.HasExited)
                    process.Kill(true);
                throw;
            }
            return process.ExitCode;
        }
    }
}
