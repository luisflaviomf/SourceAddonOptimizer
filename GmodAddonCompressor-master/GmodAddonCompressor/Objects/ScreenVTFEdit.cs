using GmodAddonCompressor.CustomExtensions;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Properties;
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
    /// "Tela" VTF mode: screen-aware resolution per texture (texlab texscreen engine in the worker),
    /// Lanczos top level, exact original lower mips and alpha-safe DXT1 selection. Files the
    /// engine reports as fallback (non-DXT formats, NOMIP) go through the regular VTF path.
    /// </summary>
    internal sealed class ScreenVTFEdit : ICompress, ICompressPreparation, ICompressBatch, ICompressStatus
    {
        public Action<string>? StatusChanged { get; set; }

        /// <summary>The worker's progress line as the screen shows it.</summary>
        internal static string DescribeProgress(string stage, int index, int total, string item)
        {
            string what = stage switch
            {
                "analyze" => "Texturas: medindo como cada uma aparece no jogo",
                "rebuild" => "Texturas: reduzindo",
                "write" => "Texturas: gravando",
                "stream" => "Texturas: convertendo para stream",
                _ => "Texturas: " + stage
            };
            if (stage == "analyze" || total <= 0)
                return what + "…";
            return string.IsNullOrWhiteSpace(item)
                ? $"{what} {index} de {total}"
                : $"{what} {index} de {total} · {item.Replace('\\', '/')}";
        }

        internal const string DefaultProfile = "q50";
        private const string VtfEditToolName = "VTFEdit";
        private const string VtfEditToolVersion = "1";

        private readonly string _addonRoot;
        private readonly string _profile;
        private readonly VTFEdit _fallback;
        private readonly bool _keepFallbacks;
        private readonly ILogger _logger = LogSystem.CreateLogger<ScreenVTFEdit>();

        /// <param name="keepFallbacks">leave the files the engine reports as fallback untouched instead of
        /// sending them through the regular (Clássico) VTF path, which depends on the advanced-tab options</param>
        internal ScreenVTFEdit(string addonRoot, string profile = DefaultProfile, bool keepFallbacks = false)
        {
            _addonRoot = addonRoot;
            _profile = profile;
            _keepFallbacks = keepFallbacks;
            _fallback = new VTFEdit(addonRoot);
        }

        internal string? LastReportPath { get; private set; }

        public void Prepare() => _fallback.Prepare();

        public Task Compress(string vtfFilePath) =>
            CompressBatchAsync(new[] { new FileInfo(vtfFilePath) }, CancellationToken.None);

        public async Task CompressBatchAsync(IReadOnlyList<FileInfo> files, CancellationToken cancellationToken)
        {
            if (files.Count == 0)
                return;

            ToolExtractionSystem.EnsureSourceAddonOptimizerExtracted();
            string vtfEditRoot = ToolExtractionSystem.EnsureExtracted(
                VtfEditToolName,
                VtfEditToolVersion,
                Resources.VTFEdit,
                new[] { Path.Combine("VTFEdit", "VTFCmd.exe") });
            string vtfCmd = Path.Combine(vtfEditRoot, "VTFEdit", "VTFCmd.exe");

            string workDir = Path.Combine(ToolPaths.WorkRoot, "texscreen");
            Directory.CreateDirectory(workDir);
            string report = Path.Combine(workDir, $"texscreen_{DateTime.Now:yyyyMMdd_HHmmss}_{Guid.NewGuid():N}.json");
            // analysis processes + image threads: 0 = the worker picks (every logical CPU but two, bounded by
            // the free memory; texlab/jobs.py). The result does not depend on the count.
            int jobs = 0;

            var arguments = new List<string>
            {
                "texscreen", _addonRoot, "--vtfcmd", vtfCmd, "--profile", _profile,
                "--jobs", jobs.ToString(System.Globalization.CultureInfo.InvariantCulture), "--report", report
            };
            _logger.LogInformation($"[Tela] Screen-aware VTF engine ({_profile}) on {files.Count} VTF files.");
            int exitCode = await RunWorkerAsync(arguments, cancellationToken).ConfigureAwait(false);
            if (exitCode != 0 || !File.Exists(report))
                throw new InvalidOperationException($"Screen-aware VTF engine failed (exit {exitCode}).");
            LastReportPath = report;

            using var json = JsonDocument.Parse(await File.ReadAllTextAsync(report, cancellationToken).ConfigureAwait(false));
            long before = json.RootElement.GetProperty("before").GetInt64();
            long after = json.RootElement.GetProperty("after").GetInt64();
            var wanted = new HashSet<string>(files.Select(f => Path.GetFullPath(f.FullName)), StringComparer.OrdinalIgnoreCase);
            var fallback = json.RootElement.GetProperty("fallback").EnumerateArray()
                .Select(e => Path.GetFullPath(Path.Combine(_addonRoot, e.GetString() ?? string.Empty)))
                .Where(p => wanted.Contains(p) && File.Exists(p))
                .ToArray();

            _logger.LogInformation(
                $"[Tela] {before:N0} -> {after:N0} bytes ({(before > 0 ? 100.0 * after / before : 100):0.0}%). " +
                (_keepFallbacks ? $"{fallback.Length} file(s) the engine does not handle kept unchanged." : $"Regular path for {fallback.Length} file(s).") +
                $" Report: {report}");
            if (json.RootElement.TryGetProperty("stream", out JsonElement stream) && stream.TryGetProperty("streamed", out JsonElement streamed))
            {
                // profile "stream": textures models draw became GMOT blobs the Lua runtime decodes in game
                _logger.LogInformation(
                    $"[Tela] Stream: {streamed.GetInt32()} texture(s) decoded in game; " +
                    $"{stream.GetProperty("before").GetInt64():N0} -> {stream.GetProperty("blobs").GetInt64():N0} bytes " +
                    $"+ placeholders {stream.GetProperty("placeholders").GetInt64():N0} + runtime {stream.GetProperty("runtime").GetInt64():N0}.");
            }
            if (_keepFallbacks)
                return;

            await Parallel.ForEachAsync(
                fallback,
                new ParallelOptions { MaxDegreeOfParallelism = 4, CancellationToken = cancellationToken },
                async (path, _) => await _fallback.Compress(path).ConfigureAwait(false)).ConfigureAwait(false);
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
            long lastStatusTicks = 0;
            process.OutputDataReceived += (_, e) =>
            {
                if (e.Data == null)
                    return;
                if (e.Data.StartsWith("TEXSCREEN_PROGRESS: ", StringComparison.Ordinal))
                {
                    string[] parts = e.Data.Substring(20).Split('|');
                    if (parts.Length >= 3 && int.TryParse(parts[1], out int i) && int.TryParse(parts[2], out int n))
                    {
                        // the screen: at most a few lines per second
                        long now = Environment.TickCount64;
                        if (i == n || now - lastStatusTicks >= 150)
                        {
                            lastStatusTicks = now;
                            try { StatusChanged?.Invoke(DescribeProgress(parts[0], i, n, parts.Length > 3 ? parts[3] : string.Empty)); }
                            catch { }
                        }

                        int step = n > 0 ? i * 20 / n : 0;
                        if (step != lastLogged || i == n)
                        {
                            lastLogged = step;
                            _logger.LogInformation($"[Tela] {parts[0]} {i}/{n} {(parts.Length > 3 ? parts[3] : string.Empty)}");
                        }
                    }
                    return;
                }
                _logger.LogInformation("[Tela] " + e.Data);
            };
            process.ErrorDataReceived += (_, e) =>
            {
                if (!string.IsNullOrWhiteSpace(e.Data))
                    _logger.LogWarning("[Tela] " + e.Data);
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
