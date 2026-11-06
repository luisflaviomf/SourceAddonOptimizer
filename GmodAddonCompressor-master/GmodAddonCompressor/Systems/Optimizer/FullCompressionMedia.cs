using GmodAddonCompressor.Bases;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Objects;
using GmodAddonCompressor.Systems.Vtf;
using Microsoft.Extensions.Logging;
using NAudio.Wave;
using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems.Optimizer;

internal static class FullCompressionMedia
{
    private static readonly SemaphoreSlim Gate = new(1, 1);

    // The legacy media services use static contexts. Own and restore that small boundary;
    // never use the values saved by another tab as this workflow's profile.
    internal static async Task RunAsync(string root, Action<string> log, CancellationToken token)
    {
        await Gate.WaitAsync(token);
        int[] image = { ImageContext.Resolution, ImageContext.TaargetWidth, ImageContext.TargetHeight, ImageContext.SkipWidth, ImageContext.SkipHeight };
        bool exact = ImageContext.ReduceExactlyToLimits, aspect = ImageContext.KeepImageAspectRatio;
        int rate = AudioContext.WavSampleRate, channels = AudioContext.WavChannels;
        var codec = AudioContext.WavCodec;
        bool loops = AudioContext.PreserveLoopMetadata;
        string oldRoot = CompressDirectoryContext.DirectoryPath;
        var oldFactory = LogSystem.LoggerFactory;
        using var capture = new MediaLog(log);
        using var factory = new LoggerFactory(new[] { capture });
        try
        {
            LogSystem.LoggerFactory = factory;
            CompressDirectoryContext.DirectoryPath = root;
            ImageContext.Resolution = 2;
            ImageContext.TaargetWidth = ImageContext.TargetHeight = 8;
            ImageContext.SkipWidth = ImageContext.SkipHeight = 0;
            ImageContext.ReduceExactlyToLimits = false;
            ImageContext.KeepImageAspectRatio = true;
            AudioContext.WavSampleRate = 22050;
            AudioContext.WavCodec = AudioContext.WavCodecKind.Pcm16;
            AudioContext.PreserveLoopMetadata = true;
            var files = FullCompressionRunner.Inventory(root, token)
                .Where(p => new[] { ".vtf", ".png", ".wav" }.Contains(Path.GetExtension(p.Path).ToLowerInvariant())).ToArray();
            VTFEdit? vtf = null; WAVEdit? wav = null;
            for (int i = 0; i < files.Length; i++)
            {
                token.ThrowIfCancellationRequested();
                string path = Path.Combine(root, files[i].Path);
                log($"Mídia {i + 1}/{files.Length}: {files[i].Path}");
                string extension = Path.GetExtension(path).ToLowerInvariant();
                if (extension == ".vtf")
                {
                    if (vtf == null) { vtf = new VTFEdit(root, new(2, 8, 8, true)); vtf.Prepare(); }
                    if (!AddonVtfCompressionPlanner.TryReadMetadata(path, out var metadata))
                        throw new IOException("Cabeçalho VTF inválido: " + files[i].Path);
                    var result = await vtf.TryCompressWithSplitRebuildAsync(path, metadata, "[Completa]");
                    log($"VTF: {(result.Applied ? "reduzido" : "preservado")} — {result.Reason}");
                    if (result.IsOperationalFailure) throw new IOException("Falha operacional VTF: " + result.Reason);
                }
                else if (extension == ".png")
                    await new CheckedPng().Run(path);
                else
                {
                    // Restrict the native resample to the approved PCM16/44100 domain.
                    // Other WAVs remain native for the exact stage; never upsample/downmix.
                    using var reader = new WaveFileReader(path);
                    if (reader.WaveFormat.Encoding != WaveFormatEncoding.Pcm || reader.WaveFormat.BitsPerSample != 16 ||
                        reader.WaveFormat.SampleRate != 44100 || reader.WaveFormat.Channels is < 1 or > 2)
                    { log("WAV preservado: fora do domínio PCM16/44100."); continue; }
                    AudioContext.WavChannels = reader.WaveFormat.Channels;
                    reader.Dispose();
                    if (new FileInfo(path).Length > 64 * 1024 * 1024)
                    { log("WAV preservado: limite de validação nativa 64 MiB."); continue; }
                    byte[] originalWav = File.ReadAllBytes(path);
                    if (!FullCompressionWav.HasSupportedMetadata(originalWav))
                    { log("WAV preservado: metadados fora do domínio aprovado."); continue; }
                    wav ??= new WAVEdit();
                    await wav.Compress(path);
                    if (capture.Error != null) throw new IOException("Falha na etapa nativa: " + capture.Error);
                    FullCompressionWav.Validate(originalWav, File.ReadAllBytes(path));
                }
                if (capture.Error != null) throw new IOException("Falha na etapa nativa: " + capture.Error);
                if (!File.Exists(path) || new FileInfo(path).Length == 0) throw new IOException("Saída nativa ausente/vazia: " + files[i].Path);
                token.ThrowIfCancellationRequested();
            }
        }
        catch (Exception ex) when (ex is not OperationCanceledException && ex is not IOException)
        {
            throw new IOException("Falha na etapa nativa: " + ex.Message, ex);
        }
        finally
        {
            ImageContext.Resolution = image[0]; ImageContext.TaargetWidth = image[1]; ImageContext.TargetHeight = image[2];
            ImageContext.SkipWidth = image[3]; ImageContext.SkipHeight = image[4];
            ImageContext.ReduceExactlyToLimits = exact; ImageContext.KeepImageAspectRatio = aspect;
            AudioContext.WavSampleRate = rate; AudioContext.WavChannels = channels; AudioContext.WavCodec = codec;
            AudioContext.PreserveLoopMetadata = loops; CompressDirectoryContext.DirectoryPath = oldRoot;
            LogSystem.LoggerFactory = oldFactory;
            Gate.Release();
        }
    }

    private sealed class CheckedPng : ImageEditBase
    {
        internal CheckedPng() => SetImageFileExtension(".png");
        internal Task Run(string path) => ImageCompress(path);
    }

    private sealed class MediaLog : ILoggerProvider, ILogger
    {
        private readonly Action<string> _log;
        internal string? Error;
        internal MediaLog(Action<string> log) => _log = log;
        public ILogger CreateLogger(string categoryName) => this;
        public bool IsEnabled(LogLevel level) => true;
        public IDisposable BeginScope<TState>(TState state) => this;
        public void Log<TState>(LogLevel level, EventId id, TState state, Exception? error, Func<TState, Exception?, string> formatter)
        {
            string text = formatter(state, error);
            if (level >= LogLevel.Error) Error = text;
            _log(text);
        }
        public void Dispose() { }
    }
}
