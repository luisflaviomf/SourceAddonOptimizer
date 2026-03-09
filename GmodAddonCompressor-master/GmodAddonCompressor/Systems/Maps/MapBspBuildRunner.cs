using System;
using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Maps
{
    internal sealed class MapBspBuildRunOptions
    {
        internal string WorkerExePath { get; init; } = string.Empty;
        internal string RootPath { get; init; } = string.Empty;
        internal string WorkDir { get; init; } = string.Empty;
        internal string OutputDir { get; init; } = string.Empty;
        internal string? CancelFilePath { get; init; }
    }

    internal sealed class MapBspBuildProgressUpdate
    {
        internal string? Phase { get; init; }
        internal int? ItemIndex { get; init; }
        internal int? ItemTotal { get; init; }
        internal string? CurrentPath { get; init; }
        internal string? SummaryPath { get; init; }
        internal string? WorkDir { get; init; }
        internal string? OutputDir { get; init; }
    }

    internal sealed class MapBspBuildRunner
    {
        private static readonly Regex StepRegex = new(@"^== Step \d+/\d+: (.+) ==$", RegexOptions.Compiled);
        private static readonly Regex ItemRegex = new(@"^=== \((\d+)/(\d+)\) BSP: (.+)$", RegexOptions.Compiled);
        private static readonly Regex SummaryRegex = new(@"^MAPBUILD_SUMMARY:\s+(.+)$", RegexOptions.Compiled);
        private static readonly Regex WorkDirRegex = new(@"^MAPBUILD_WORK_DIR:\s+(.+)$", RegexOptions.Compiled);
        private static readonly Regex OutputDirRegex = new(@"^MAPBUILD_OUTPUT_DIR:\s+(.+)$", RegexOptions.Compiled);

        internal event Action<string>? LogLine;
        internal event Action<string>? ErrorLine;
        internal event Action<MapBspBuildProgressUpdate>? ProgressUpdate;
        internal event Action<string>? SummaryPathFound;
        internal event Action<string>? WorkDirFound;
        internal event Action<string>? OutputDirFound;

        internal async Task<int> RunAsync(MapBspBuildRunOptions options, CancellationToken cancellationToken)
        {
            if (string.IsNullOrWhiteSpace(options.WorkerExePath))
                throw new ArgumentException("Worker exe path is required.", nameof(options.WorkerExePath));
            if (!File.Exists(options.WorkerExePath))
                throw new FileNotFoundException("Worker exe not found.", options.WorkerExePath);
            if (string.IsNullOrWhiteSpace(options.RootPath))
                throw new ArgumentException("Root path is required.", nameof(options.RootPath));
            if (string.IsNullOrWhiteSpace(options.WorkDir))
                throw new ArgumentException("Work dir is required.", nameof(options.WorkDir));
            if (string.IsNullOrWhiteSpace(options.OutputDir))
                throw new ArgumentException("Output dir is required.", nameof(options.OutputDir));

            var startInfo = new ProcessStartInfo
            {
                FileName = options.WorkerExePath,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetDirectoryName(options.WorkerExePath) ?? string.Empty
            };

            startInfo.ArgumentList.Add("mapbuild");
            startInfo.ArgumentList.Add(options.RootPath);
            startInfo.ArgumentList.Add("--work");
            startInfo.ArgumentList.Add(options.WorkDir);
            startInfo.ArgumentList.Add("--out");
            startInfo.ArgumentList.Add(options.OutputDir);

            if (!string.IsNullOrWhiteSpace(options.CancelFilePath))
            {
                startInfo.ArgumentList.Add("--cancel-file");
                startInfo.ArgumentList.Add(options.CancelFilePath);
            }

            using var process = new Process { StartInfo = startInfo, EnableRaisingEvents = true };

            process.OutputDataReceived += (_, args) =>
            {
                if (args.Data != null)
                    HandleLine(args.Data, false);
            };
            process.ErrorDataReceived += (_, args) =>
            {
                if (args.Data != null)
                    HandleLine(args.Data, true);
            };

            if (!process.Start())
                throw new InvalidOperationException("Failed to start map build worker.");

            process.BeginOutputReadLine();
            process.BeginErrorReadLine();

            try
            {
                await process.WaitForExitAsync(cancellationToken);
            }
            catch (OperationCanceledException)
            {
                if (!process.HasExited)
                    process.Kill(true);
                throw;
            }

            return process.ExitCode;
        }

        private void HandleLine(string line, bool isError)
        {
            LogLine?.Invoke(line);
            if (isError)
                ErrorLine?.Invoke(line);

            var stepMatch = StepRegex.Match(line);
            if (stepMatch.Success)
            {
                ProgressUpdate?.Invoke(new MapBspBuildProgressUpdate
                {
                    Phase = stepMatch.Groups[1].Value.Trim()
                });
                return;
            }

            var itemMatch = ItemRegex.Match(line);
            if (itemMatch.Success)
            {
                ProgressUpdate?.Invoke(new MapBspBuildProgressUpdate
                {
                    ItemIndex = int.Parse(itemMatch.Groups[1].Value),
                    ItemTotal = int.Parse(itemMatch.Groups[2].Value),
                    CurrentPath = itemMatch.Groups[3].Value.Trim()
                });
                return;
            }

            var summaryMatch = SummaryRegex.Match(line);
            if (summaryMatch.Success)
            {
                string summaryPath = summaryMatch.Groups[1].Value.Trim();
                SummaryPathFound?.Invoke(summaryPath);
                ProgressUpdate?.Invoke(new MapBspBuildProgressUpdate
                {
                    SummaryPath = summaryPath
                });
                return;
            }

            var workDirMatch = WorkDirRegex.Match(line);
            if (workDirMatch.Success)
            {
                string workDir = workDirMatch.Groups[1].Value.Trim();
                WorkDirFound?.Invoke(workDir);
                ProgressUpdate?.Invoke(new MapBspBuildProgressUpdate
                {
                    WorkDir = workDir
                });
                return;
            }

            var outputDirMatch = OutputDirRegex.Match(line);
            if (outputDirMatch.Success)
            {
                string outputDir = outputDirMatch.Groups[1].Value.Trim();
                OutputDirFound?.Invoke(outputDir);
                ProgressUpdate?.Invoke(new MapBspBuildProgressUpdate
                {
                    OutputDir = outputDir
                });
            }
        }
    }
}
