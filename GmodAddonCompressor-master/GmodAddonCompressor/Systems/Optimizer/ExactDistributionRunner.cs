using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Optimizer
{
    internal sealed class ExactDistributionRunOptions
    {
        internal string WorkerExePath { get; init; } = string.Empty;
        internal string SourcePath { get; init; } = string.Empty;
        internal string OutputPath { get; init; } = string.Empty;
        internal string WorkDir { get; init; } = string.Empty;
        internal string EngineRoot { get; init; } = string.Empty;
    }

    internal sealed record ExactDistributionProgress(string Phase, int Index, int Total, string Path);
    internal sealed record ExactDistributionResult(int ExitCode, string? OutputPath, string? ReportPath, string Summary)
    {
        internal bool Success => ExitCode == 0 && OutputPath != null && ReportPath != null;
    }

    internal sealed class ExactDistributionRunner
    {
        private int _running;
        internal event Action<string>? LogLine;
        internal event Action<ExactDistributionProgress>? ProgressUpdate;

        internal static void ValidatePaths(ExactDistributionRunOptions options)
        {
            foreach (string path in new[] { options.SourcePath, options.OutputPath, options.WorkDir, options.EngineRoot })
                if (string.IsNullOrWhiteSpace(path)) throw new ArgumentException("Informe entrada, saída nova, engine e work.");
            foreach (string path in new[] { options.SourcePath, options.OutputPath, options.WorkDir, options.EngineRoot, options.WorkDir + ".log" })
                ValidateNoReparseAncestors(path);
            if (!Directory.Exists(options.SourcePath)) throw new DirectoryNotFoundException("Addon de entrada não encontrado.");
            if (!Directory.Exists(options.EngineRoot)) throw new DirectoryNotFoundException("Diretório da engine não encontrado.");
            if (Directory.Exists(options.OutputPath) || File.Exists(options.OutputPath))
                throw new IOException("A saída deve ser uma pasta nova, ainda inexistente.");
            if (Directory.Exists(options.WorkDir) || File.Exists(options.WorkDir))
                throw new IOException("A pasta work deve ser nova.");
            if (!Regex.IsMatch(Path.GetFileName(Path.GetFullPath(options.OutputPath).TrimEnd('\\', '/')), "^[a-z0-9_-]+$"))
                throw new ArgumentException("O nome final da pasta de saída deve usar apenas a-z, 0-9, _ e -.");
            string[] paths = { options.SourcePath, options.OutputPath, options.WorkDir, options.EngineRoot };
            for (int i = 0; i < paths.Length; i++)
                for (int j = i + 1; j < paths.Length; j++)
                {
                    // Installed addons may be read; only generated roots must stay outside the engine.
                    if (i == 0 && j == 3) continue;
                    if (Overlap(paths[i], paths[j])) throw new IOException("Entrada, saída, work e engine não podem se sobrepor.");
                }
        }

        private static void ValidateNoReparseAncestors(string path)
        {
            string? current = Path.TrimEndingDirectorySeparator(Path.GetFullPath(path));
            while (current != null)
            {
                try
                {
                    if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                        throw new IOException("Reparse point/junction não permitido: " + current);
                }
                catch (FileNotFoundException) { }
                catch (DirectoryNotFoundException) { }
                current = Path.GetDirectoryName(current);
            }
        }

        internal static string PrepareWorkLog(ExactDistributionRunOptions options)
        {
            // Validate every generated destination before even creating the shared parent.
            ValidatePaths(options);
            string logPath = Path.GetFullPath(options.WorkDir) + ".log";
            if (File.Exists(logPath) || Directory.Exists(logPath))
                throw new IOException("O log da exportação deve ser novo.");
            Directory.CreateDirectory(Path.GetDirectoryName(logPath)!);
            ValidateNoReparseAncestors(logPath);
            using (new FileStream(logPath, FileMode.CreateNew, FileAccess.Write, FileShare.Read)) { }
            return logPath;
        }

        internal static void AppendWorkLog(string logPath, string line)
        {
            ValidateNoReparseAncestors(logPath);
            File.AppendAllText(logPath, line + Environment.NewLine);
        }

        internal static void CopyWorkLog(string logPath, string workDir)
        {
            string destination = Path.Combine(workDir, "wpf-worker.log");
            ValidateNoReparseAncestors(logPath);
            ValidateNoReparseAncestors(destination);
            if (Directory.Exists(workDir) && File.Exists(logPath))
                File.Copy(logPath, destination, overwrite: false);
        }

        private static bool Overlap(string first, string second)
        {
            string a = Path.GetFullPath(first).TrimEnd('\\', '/') + Path.DirectorySeparatorChar;
            string b = Path.GetFullPath(second).TrimEnd('\\', '/') + Path.DirectorySeparatorChar;
            return a.StartsWith(b, StringComparison.OrdinalIgnoreCase) || b.StartsWith(a, StringComparison.OrdinalIgnoreCase);
        }

        internal static ProcessStartInfo CreateStartInfo(ExactDistributionRunOptions options)
        {
            ValidatePaths(options);
            if (!File.Exists(options.WorkerExePath)) throw new FileNotFoundException("Worker não encontrado.", options.WorkerExePath);
            var info = new ProcessStartInfo(options.WorkerExePath)
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
                WorkingDirectory = Path.GetDirectoryName(Path.GetFullPath(options.WorkerExePath))!
            };
            foreach (string arg in new[] { "exactpack", Path.GetFullPath(options.SourcePath), "--output", Path.GetFullPath(options.OutputPath),
                "--work", Path.GetFullPath(options.WorkDir), "--engine-root", Path.GetFullPath(options.EngineRoot) })
                info.ArgumentList.Add(arg);
            return info;
        }

        internal Task<ExactDistributionResult> RunAsync(ExactDistributionRunOptions options, CancellationToken token) =>
            RunProcessAsync(CreateStartInfo(options), options, token);

        internal async Task<ExactDistributionResult> RunProcessAsync(ProcessStartInfo info, ExactDistributionRunOptions options, CancellationToken token)
        {
            if (Interlocked.CompareExchange(ref _running, 1, 0) != 0)
                throw new InvalidOperationException("Uma exportação exata já está em andamento.");
            try
            {
                token.ThrowIfCancellationRequested();
                string? output = null, report = null;
                using var process = new Process { StartInfo = info };
                async Task ReadLinesAsync(StreamReader reader, bool protocol)
                {
                    while (await reader.ReadLineAsync().ConfigureAwait(false) is string line)
                    {
                        LogLine?.Invoke(line);
                        if (!protocol) continue;
                        var progress = ParseProgress(line);
                        if (progress != null) ProgressUpdate?.Invoke(progress);
                        if (line.StartsWith("EXACT_OUTPUT: ", StringComparison.Ordinal)) output = line[14..].Trim();
                        if (line.StartsWith("EXACT_REPORT: ", StringComparison.Ordinal)) report = line[14..].Trim();
                    }
                }
                if (!process.Start()) throw new InvalidOperationException("Falha ao iniciar worker exato.");
                Task stdout = ReadLinesAsync(process.StandardOutput, true);
                Task stderr = ReadLinesAsync(process.StandardError, false);
                try
                {
                    await process.WaitForExitAsync(token).ConfigureAwait(false);
                    await Task.WhenAll(stdout, stderr).ConfigureAwait(false);
                    token.ThrowIfCancellationRequested();
                }
                catch
                {
                    try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
                    catch (InvalidOperationException) { }
                    await process.WaitForExitAsync(CancellationToken.None).ConfigureAwait(false);
                    await Task.WhenAll(stdout, stderr).ConfigureAwait(false);
                    throw;
                }
                if (process.ExitCode != 0)
                    return new(process.ExitCode, null, null, $"Worker falhou ({process.ExitCode}). Verifique o log/work.");
                return ReadResult(options, output, report);
            }
            finally { Volatile.Write(ref _running, 0); }
        }

        internal static ExactDistributionProgress? ParseProgress(string line)
        {
            const string prefix = "EXACT_PROGRESS: ";
            if (!line.StartsWith(prefix, StringComparison.Ordinal)) return null;
            string[] fields = line[prefix.Length..].Split('|', 4);
            if (fields.Length != 4 || !int.TryParse(fields[1], out int index) || !int.TryParse(fields[2], out int total)
                || index < 0 || total < 0 || index > total) return null;
            return new(fields[0], index, total, fields[3]);
        }

        internal static ExactDistributionResult ReadResult(ExactDistributionRunOptions options, string? output, string? report)
        {
            static bool Same(string? a, string b) => a != null && string.Equals(Path.GetFullPath(a), Path.GetFullPath(b), StringComparison.OrdinalIgnoreCase);
            if (!Same(output, options.OutputPath) || !Same(report, Path.Combine(options.WorkDir, "candidate-report.json"))
                || !Directory.Exists(output) || !File.Exists(report))
                throw new InvalidDataException("Worker terminou sem saída/relatório esperado. Candidato não aprovado.");
            using var document = JsonDocument.Parse(File.ReadAllText(report!));
            JsonElement root = document.RootElement;
            if (root.GetProperty("schema").GetString() != "exact-distribution-report/v1"
                || root.GetProperty("status").GetString() != "OFFLINE_PENDING_GMOD"
                || !Same(root.GetProperty("candidate").GetString(), options.OutputPath)
                || !root.GetProperty("byte_exact").GetBoolean() || root.GetProperty("new_quality_loss").GetBoolean()
                || root.GetProperty("installed").GetBoolean())
                throw new InvalidDataException("Relatório exato inválido. Candidato não aprovado.");
            var excluded = root.GetProperty("excluded");
            string summary = $"OFFLINE_PENDING_GMOD — validar no runtime alvo antes de ativar manualmente.\n" +
                $"Entrada: {root.GetProperty("source_bytes").GetInt64():N0} bytes | Candidato: {root.GetProperty("candidate_bytes").GetInt64():N0} bytes\n" +
                $"Economia líquida codec: {root.GetProperty("codec_net_savings_bytes").GetInt64():N0} bytes\n" +
                $"Exclusões DX80/SW: {excluded.GetProperty("count").GetInt32()} arquivos, {excluded.GetProperty("bytes").GetInt64():N0} bytes (separados do codec).\n" +
                $"Saída: {output}\nRelatório: {report}";
            return new(0, output, report, summary);
        }
    }
}
