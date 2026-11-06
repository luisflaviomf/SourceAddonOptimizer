using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Optimizer;

internal sealed record FullCompressionOptions(string SourcePath, string OutputPath, string WorkDir,
    string EngineRoot, string WorkerExePath, string BlenderPath, string StudioMdlPath);

internal sealed record FullCompressionResult(ExactDistributionResult Exact, string ReportPath,
    long SourceBytes, long NativeBytes, long FinalBytes, double Seconds)
{
    internal long SavedBytes => SourceBytes - FinalBytes;
    internal string Summary => $"Entrada: {SourceBytes:N0} B → final: {FinalBytes:N0} B\n" +
        $"Economia total: {SavedBytes:N0} B ({(SourceBytes == 0 ? 0 : 100.0 * SavedBytes / SourceBytes):N2}%)\n" +
        $"Intermediário nativo: {NativeBytes:N0} B | Tempo: {Seconds:N1} s\n" +
        "A redução nativa pode alterar qualidade; apenas a distribuição final é byte-exata em relação ao intermediário.\n" +
        "OFFLINE_PENDING_GMOD — teste no jogo antes de distribuir.\n" +
        $"Saída: {Exact.OutputPath}\nRelatório completo: {ReportPath}";
}

/// <summary>Own profile and lifecycle. Never reads MainWindowContext or mutates an input addon.</summary>
internal sealed class FullCompressionRunner
{
    internal const string Profile = "complete-safe-native-exact/v1";
    private readonly Func<SourceAddonOptimizerRunOptions, CancellationToken, Task<string>> _models;
    private readonly Func<string, CancellationToken, Task> _media;
    private readonly Func<ExactDistributionRunOptions, CancellationToken, Task<ExactDistributionResult>> _exact;
    private int _running;
    internal event Action<string>? LogLine;
    internal event Action<ExactDistributionProgress>? Progress;

    internal FullCompressionRunner(
        Func<SourceAddonOptimizerRunOptions, CancellationToken, Task<string>>? models = null,
        Func<string, CancellationToken, Task>? media = null,
        Func<ExactDistributionRunOptions, CancellationToken, Task<ExactDistributionResult>>? exact = null)
    {
        _models = models ?? RunModels;
        _media = media ?? ((path, token) => FullCompressionMedia.RunAsync(path, Emit, token));
        _exact = exact ?? RunExact;
    }

    private void Emit(string line) => LogLine?.Invoke(line);
    private void Phase(string phase) { Emit(phase); Progress?.Invoke(new(phase, 0, 1, "")); }

    internal static ExactDistributionRunOptions PreflightOptions(FullCompressionOptions o) => new()
    {
        SourcePath = o.SourcePath, OutputPath = o.OutputPath, WorkDir = o.WorkDir,
        EngineRoot = o.EngineRoot, WorkerExePath = o.WorkerExePath
    };

    internal static SourceAddonOptimizerRunOptions ModelOptions(FullCompressionOptions o, string copy) => new()
    {
        WorkerExePath = o.WorkerExePath, AddonPath = copy, WorkDir = Path.Combine(o.WorkDir, "models-work"),
        BlenderPath = o.BlenderPath, StudioMdlPath = o.StudioMdlPath, Suffix = "_native",
        OptimizerMode = "normal", Ratio = .75, Merge = 0, AutoSmooth = 45, Format = "smd",
        Jobs = 1, DecompileJobs = 1, CompileJobs = 1, Strict = true, SingleAddonOnly = true,
        RestoreSkins = true, UseVtxTriStrips = true,
        Overwrite = false, OverwriteWork = false, ResumeOpt = false,
        ExperimentalGroundPolicy = false, ExperimentalRoundPartsPolicy = false,
        ExperimentalSteerTurnBasisFix = false, UsePlanar = false
    };

    internal async Task<FullCompressionResult> RunAsync(FullCompressionOptions options, CancellationToken token)
    {
        if (Interlocked.CompareExchange(ref _running, 1, 0) != 0)
            throw new InvalidOperationException("Compressão completa já em execução.");
        StreamWriter? log = null;
        Action<string>? persist = null;
        try
        {
            token.ThrowIfCancellationRequested();
            ExactDistributionRunner.ValidatePaths(PreflightOptions(options));
            var timer = Stopwatch.StartNew();
            Phase("1/4 — Conferindo e copiando entrada");
            var pins = await Task.Run(() => Inventory(options.SourcePath, token), token);
            if (pins.Count == 0) throw new IOException("Addon vazio.");
            if (pins.Any(p => p.Path.EndsWith(".gma", StringComparison.OrdinalIgnoreCase) ||
                p.Path.EndsWith(".bin", StringComparison.OrdinalIgnoreCase) ||
                p.Path.Contains("codex_exact_distribution", StringComparison.OrdinalIgnoreCase) ||
                p.Path.Contains("000000_exact_", StringComparison.OrdinalIgnoreCase)))
                throw new IOException("Selecione um addon nativo descompactado, não um candidato já comprimido.");
            ExactDistributionRunner.ValidatePaths(PreflightOptions(options));
            Directory.CreateDirectory(options.WorkDir);
            log = new StreamWriter(new FileStream(Path.Combine(options.WorkDir, "complete.log"), FileMode.CreateNew, FileAccess.Write, FileShare.Read)) { AutoFlush = true };
            object logLock = new();
            persist = line => { lock (logLock) log.WriteLine(line); };
            LogLine += persist;
            WriteJson(Path.Combine(options.WorkDir, "source-pins.json"), pins);
            string copy = Path.Combine(options.WorkDir, "input");
            await Task.Run(() => CopyPinned(options.SourcePath, copy, pins, token), token);
            string native = copy;
            if (pins.Any(p => p.Path.EndsWith(".mdl", StringComparison.OrdinalIgnoreCase)))
            {
                Phase("2/4 — Models: perfil Safe (75%), sem políticas específicas de carros");
                native = await _models(ModelOptions(options, copy), token);
                string expected = copy + "_native";
                if (!SamePath(native, copy) && !SamePath(native, expected))
                    throw new IOException("Models devolveu uma saída fora do staging esperado.");
                Inventory(native, token); // rejects reparses before in-place media services
            }
            else Emit("2/4 — Models: sem MDL; nenhuma recompilação necessária.");
            token.ThrowIfCancellationRequested();
            Phase("3/4 — Texturas e áudio: perfil próprio 2x/min8, WAV 22050 PCM16");
            await _media(native, token);
            token.ThrowIfCancellationRequested();
            long nativeBytes = Inventory(native, token).Sum(p => p.Bytes);
            VerifyInput(options.SourcePath, pins, token);
            Phase("4/4 — VVD/VTF/MDL/VTX/WAV exatos + exclusão DX80/SW");
            var exact = await _exact(new()
            {
                WorkerExePath = options.WorkerExePath, SourcePath = native, OutputPath = options.OutputPath,
                WorkDir = Path.Combine(options.WorkDir, "exact"), EngineRoot = options.EngineRoot
            }, token);
            token.ThrowIfCancellationRequested();
            if (!exact.Success || !SamePath(exact.OutputPath!, options.OutputPath))
                throw new IOException("Distribuição exata falhou. Nenhum resultado final aprovado.");
            VerifyInput(options.SourcePath, pins, token);
            var finalPins = Inventory(options.OutputPath, token);
            WriteJson(Path.Combine(options.WorkDir, "final-pins.json"), finalPins);
            long sourceBytes = pins.Sum(p => p.Bytes), finalBytes = finalPins.Sum(p => p.Bytes);
            string report = Path.Combine(options.WorkDir, "full-compression-report.json");
            WriteJson(report, new
            {
                schema = "full-compression-report/v1", profile = Profile, status = "OFFLINE_PENDING_GMOD",
                source = options.SourcePath, candidate = options.OutputPath, native,
                source_bytes = sourceBytes, native_bytes = nativeBytes, candidate_bytes = finalBytes,
                savings_bytes = sourceBytes - finalBytes, seconds = timer.Elapsed.TotalSeconds,
                source_pins_sha256 = Hash(Path.Combine(options.WorkDir, "source-pins.json")),
                final_pins_sha256 = Hash(Path.Combine(options.WorkDir, "final-pins.json")),
                exact_report = exact.ReportPath, exact_report_sha256 = Hash(exact.ReportPath!),
                whole_pipeline_byte_exact = false, distribution_byte_exact = true, installed = false,
                model_policy = new { ratio = .75, merge = 0, autosmooth = 45, mode = "normal", car_specific = false },
                media_policy = new { texture_factor = 2, minimum = 8, wav_rate = 22050, preserve_channels = true }
            });
            var result = new FullCompressionResult(exact, report, sourceBytes, nativeBytes, finalBytes, timer.Elapsed.TotalSeconds);
            Emit(result.Summary);
            return result;
        }
        finally
        {
            if (persist != null) LogLine -= persist;
            log?.Dispose();
            Volatile.Write(ref _running, 0);
        }
    }

    private async Task<string> RunModels(SourceAddonOptimizerRunOptions options, CancellationToken token)
    {
        foreach (string? tool in new[] { options.BlenderPath, options.StudioMdlPath })
            if (!File.Exists(tool)) throw new FileNotFoundException("Configure Blender e studiomdl nesta aba.", tool);
        var runner = new SourceAddonOptimizerRunner();
        string? output = null;
        runner.LogLine += Emit;
        runner.ErrorLine += Emit;
        runner.OutputPathFound += path => output = path;
        int code = await runner.RunAsync(options, token);
        if (code != 0 || output == null || !Directory.Exists(output))
            throw new IOException($"Models falhou ({code}) ou não produziu saída.");
        return output;
    }

    private async Task<ExactDistributionResult> RunExact(ExactDistributionRunOptions options, CancellationToken token)
    {
        var runner = new ExactDistributionRunner();
        runner.LogLine += Emit;
        runner.ProgressUpdate += update => Progress?.Invoke(update);
        return await runner.RunAsync(options, token);
    }

    internal sealed record Pin(string Path, long Bytes, string Sha256);
    internal static List<Pin> Inventory(string root, CancellationToken token)
    {
        var pins = new List<Pin>(); var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        void Visit(string directory)
        {
            token.ThrowIfCancellationRequested();
            if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Reparse point não permitido: " + directory);
            foreach (string file in Directory.EnumerateFileSystemEntries(directory).OrderBy(p => p, StringComparer.Ordinal))
            {
                token.ThrowIfCancellationRequested();
                var attributes = File.GetAttributes(file);
                if ((attributes & FileAttributes.ReparsePoint) != 0) throw new IOException("Reparse point não permitido: " + file);
                if ((attributes & FileAttributes.Directory) != 0) { Visit(file); continue; }
                string relative = Path.GetRelativePath(root, file).Replace('\\', '/');
                if (!names.Add(relative)) throw new IOException("Colisão de caminhos: " + relative);
                pins.Add(new(relative, new FileInfo(file).Length, Hash(file)));
            }
        }
        Visit(Path.GetFullPath(root));
        return pins.OrderBy(p => p.Path, StringComparer.Ordinal).ToList();
    }

    private static void CopyPinned(string source, string destination, List<Pin> pins, CancellationToken token)
    {
        Directory.CreateDirectory(destination);
        foreach (var pin in pins)
        {
            token.ThrowIfCancellationRequested();
            string output = Path.Combine(destination, pin.Path);
            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            File.Copy(Path.Combine(source, pin.Path), output, false);
            if (Hash(output) != pin.Sha256) throw new IOException("Entrada mudou durante a cópia: " + pin.Path);
        }
    }
    private static void VerifyInput(string source, List<Pin> pins, CancellationToken token)
    {
        if (!pins.SequenceEqual(Inventory(source, token))) throw new IOException("A entrada original mudou durante a execução. Resultado não aprovado.");
    }
    private static bool SamePath(string a, string b) => string.Equals(Path.GetFullPath(a).TrimEnd('\\', '/'), Path.GetFullPath(b).TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase);
    private static string Hash(string path) { using var file = File.OpenRead(path); using var sha = SHA256.Create(); return Convert.ToHexString(sha.ComputeHash(file)).ToLowerInvariant(); }
    private static void WriteJson(string path, object value)
    {
        using var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read);
        JsonSerializer.Serialize(file, value, new JsonSerializerOptions { WriteIndented = true });
    }
}
