using System;
using System.Diagnostics;
using System.IO;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Unpack
{
    internal sealed class AddonUnpackRunOptions
    {
        internal string WorkerExePath { get; init; } = string.Empty;
        internal string RootPath { get; init; } = string.Empty;
        internal string WorkDir { get; init; } = string.Empty;
        internal string? GmadExePath { get; init; }
        internal string? OutputRootPath { get; init; }
        internal string ExistingMode { get; init; } = "skip";
        internal bool ScanOnly { get; init; }
        internal bool ExtractMapPakContent { get; init; }
        internal bool DeleteMapBspAfterExtract { get; init; }
        internal string? CancelFilePath { get; init; }
    }

    internal sealed class AddonUnpackProgressUpdate
    {
        internal string? Phase { get; init; }
        internal int? ItemIndex { get; init; }
        internal int? ItemTotal { get; init; }
        internal string? CurrentPath { get; init; }
        internal string? SummaryPath { get; init; }
        internal string? WorkDir { get; init; }
    }

    internal sealed class AddonUnpackRunner
    {
        private static readonly Regex StepRegex = new(@"^== Step \d+/\d+: (.+) ==$", RegexOptions.Compiled);
        private static readonly Regex ItemRegex = new(@"^=== \((\d+)/(\d+)\) ADDON: (.+)$", RegexOptions.Compiled);
        private static readonly Regex SummaryRegex = new(@"^UNPACK_SUMMARY:\s+(.+)$", RegexOptions.Compiled);
        private static readonly Regex WorkDirRegex = new(@"^UNPACK_WORK_DIR:\s+(.+)$", RegexOptions.Compiled);

        internal event Action<string>? LogLine;
        internal event Action<string>? ErrorLine;
        internal event Action<AddonUnpackProgressUpdate>? ProgressUpdate;
        internal event Action<string>? SummaryPathFound;
        internal event Action<string>? WorkDirFound;

        internal async Task<int> RunAsync(AddonUnpackRunOptions options, CancellationToken cancellationToken)
        {
            if (string.IsNullOrWhiteSpace(options.WorkerExePath))
                throw new ArgumentException("Worker exe path is required.", nameof(options.WorkerExePath));
            if (!File.Exists(options.WorkerExePath))
                throw new FileNotFoundException("Worker exe not found.", options.WorkerExePath);
            if (string.IsNullOrWhiteSpace(options.RootPath))
                throw new ArgumentException("Root path is required.", nameof(options.RootPath));
            if (string.IsNullOrWhiteSpace(options.WorkDir))
                throw new ArgumentException("Work dir is required.", nameof(options.WorkDir));

            var startInfo = new ProcessStartInfo
            {
                FileName = options.WorkerExePath,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetDirectoryName(options.WorkerExePath) ?? string.Empty
            };

            startInfo.ArgumentList.Add("unpack");
            startInfo.ArgumentList.Add(options.RootPath);
            startInfo.ArgumentList.Add("--work");
            startInfo.ArgumentList.Add(options.WorkDir);
            startInfo.ArgumentList.Add("--existing");
            startInfo.ArgumentList.Add(string.IsNullOrWhiteSpace(options.ExistingMode) ? "skip" : options.ExistingMode);

            if (options.ScanOnly)
            {
                startInfo.ArgumentList.Add("--scan-only");
            }
            else if (!string.IsNullOrWhiteSpace(options.GmadExePath))
            {
                startInfo.ArgumentList.Add("--gmad");
                startInfo.ArgumentList.Add(options.GmadExePath);
            }

            if (!string.IsNullOrWhiteSpace(options.OutputRootPath))
            {
                startInfo.ArgumentList.Add("--output-root");
                startInfo.ArgumentList.Add(options.OutputRootPath);
            }

            if (options.ExtractMapPakContent)
            {
                startInfo.ArgumentList.Add("--extract-map-pak");
            }

            if (options.DeleteMapBspAfterExtract)
            {
                startInfo.ArgumentList.Add("--delete-map-bsp");
            }

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
                throw new InvalidOperationException("Failed to start unpack worker.");

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
                ProgressUpdate?.Invoke(new AddonUnpackProgressUpdate
                {
                    Phase = stepMatch.Groups[1].Value.Trim()
                });
                return;
            }

            var itemMatch = ItemRegex.Match(line);
            if (itemMatch.Success)
            {
                ProgressUpdate?.Invoke(new AddonUnpackProgressUpdate
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
                ProgressUpdate?.Invoke(new AddonUnpackProgressUpdate
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
                ProgressUpdate?.Invoke(new AddonUnpackProgressUpdate
                {
                    WorkDir = workDir
                });
            }
        }
    }
}
