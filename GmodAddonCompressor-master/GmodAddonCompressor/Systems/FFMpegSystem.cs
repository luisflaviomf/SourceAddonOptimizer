using GmodAddonCompressor.Properties;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Systems.Tools;
using Microsoft.Extensions.Logging;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems
{
    internal class FFMpegSystem
    {
        private const string _toolName = "ffmpeg";
        private const string _toolVersion = "2022-09-22";
        private static readonly Lazy<string> _ffmpegFilePathCache =
            new Lazy<string>(ResolveFfmpegFilePath, LazyThreadSafetyMode.ExecutionAndPublication);
        private static readonly object _audioLogSync = new object();
        private readonly string _ffmpegFilePath;
        private readonly ILogger _logger = LogSystem.CreateLogger<FFMpegSystem>();

        internal sealed class FfmpegRunResult
        {
            public int ExitCode { get; set; } = -1;
            public bool TimedOut { get; set; }
            public string StdOut { get; set; } = string.Empty;
            public string StdErr { get; set; } = string.Empty;
        }

        internal static string AudioLogPath =>
            Path.Combine(ToolPaths.AppDataRoot, "logs", "audio-compress.log");

        public FFMpegSystem()
            : this(null)
        {
        }

        private readonly Func<string, Task<FfmpegRunResult>> _runWav;

        internal FFMpegSystem(Func<string, Task<FfmpegRunResult>>? runWav)
        {
            _ffmpegFilePath = _ffmpegFilePathCache.Value;
            _runWav = runWav ?? StartFFMpegProcess;
        }

        internal static void AppendAudioLog(string message)
        {
            try
            {
                lock (_audioLogSync)
                {
                    string? dir = Path.GetDirectoryName(AudioLogPath);
                    if (!string.IsNullOrWhiteSpace(dir))
                        Directory.CreateDirectory(dir);

                    File.AppendAllText(AudioLogPath, message + Environment.NewLine, Encoding.UTF8);
                }
            }
            catch
            {
                // Avoid breaking the pipeline on logging errors.
            }
        }

        private static string ResolveFfmpegFilePath()
        {
            string toolRoot = ToolExtractionSystem.EnsureExtracted(
                _toolName,
                _toolVersion,
                Resources.ffmpeg,
                new[] { "ffmpeg.exe" }
            );

            return Path.Combine(toolRoot, "ffmpeg.exe");
        }

        private static string TrimLog(string text, int maxLines)
        {
            if (string.IsNullOrWhiteSpace(text))
                return string.Empty;

            string[] lines = text.Replace("\r\n", "\n").Split('\n', StringSplitOptions.RemoveEmptyEntries);
            if (lines.Length <= maxLines)
                return string.Join(Environment.NewLine, lines);

            return string.Join(Environment.NewLine, lines.Skip(lines.Length - maxLines));
        }

        private static void WriteRunLog(string label, string inputPath, string outputPath, string arguments, FfmpegRunResult result)
        {
            var sb = new StringBuilder();
            sb.AppendLine($"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {label}");
            sb.AppendLine($"Input: {inputPath}");
            sb.AppendLine($"Output: {outputPath}");
            sb.AppendLine($"Args: {arguments}");
            sb.AppendLine($"ExitCode: {result.ExitCode} | TimedOut: {result.TimedOut}");

            bool logProcessOutput = result.TimedOut || result.ExitCode != 0;
            string err = TrimLog(result.StdErr, 40);
            if (logProcessOutput && !string.IsNullOrWhiteSpace(err))
            {
                sb.AppendLine("STDERR:");
                sb.AppendLine(err);
            }

            string output = TrimLog(result.StdOut, 20);
            if (logProcessOutput && !string.IsNullOrWhiteSpace(output))
            {
                sb.AppendLine("STDOUT:");
                sb.AppendLine(output);
            }

            sb.AppendLine(new string('-', 60));
            AppendAudioLog(sb.ToString());
        }

        private static void TryDeleteFile(string outputFilePath)
        {
            try
            {
                if (File.Exists(outputFilePath))
                    File.Delete(outputFilePath);
            }
            catch
            {
                // Best-effort cleanup.
            }
        }

        private static bool OutputLooksValid(string outputFilePath, FfmpegRunResult result, Func<string, bool>? extraValidator = null)
        {
            if (result.TimedOut || result.ExitCode != 0)
                return false;

            if (!File.Exists(outputFilePath))
                return false;

            long length = new FileInfo(outputFilePath).Length;
            if (length <= 0)
            {
                TryDeleteFile(outputFilePath);
                return false;
            }

            if (extraValidator != null)
            {
                bool validated = false;
                try
                {
                    validated = extraValidator(outputFilePath);
                }
                catch
                {
                    validated = false;
                }

                if (!validated)
                {
                    TryDeleteFile(outputFilePath);
                    return false;
                }
            }

            return true;
        }

        private static int VorbisBitrateFromQuality(double quality)
        {
            if (quality <= -1.0)
                return 48;
            if (quality <= 0.0)
                return 56;
            if (quality <= 1.0)
                return 64;
            if (quality <= 2.0)
                return 80;
            if (quality <= 3.0)
                return 96;
            if (quality <= 4.0)
                return 112;
            if (quality <= 5.0)
                return 128;
            if (quality <= 6.0)
                return 160;
            if (quality <= 7.0)
                return 192;
            if (quality <= 8.0)
                return 224;
            if (quality <= 9.0)
                return 256;
            return 320;
        }

        private Task<FfmpegRunResult> StartFFMpegProcess(string arguments) =>
            StartFFMpegProcess(arguments, TimeSpan.FromMinutes(2));

        private async Task<FfmpegRunResult> StartFFMpegProcess(string arguments, TimeSpan timeout)
        {
            Process? ffMpegProcess = null;
            var stdout = new StringBuilder();
            var stderr = new StringBuilder();
            var result = new FfmpegRunResult();

            try
            {
                ffMpegProcess = new Process();
                ffMpegProcess.StartInfo.FileName = _ffmpegFilePath;
                ffMpegProcess.StartInfo.Arguments = arguments;
                ffMpegProcess.StartInfo.UseShellExecute = false;
                ffMpegProcess.StartInfo.CreateNoWindow = true;
                ffMpegProcess.StartInfo.RedirectStandardOutput = true;
                ffMpegProcess.StartInfo.RedirectStandardError = true;
                ffMpegProcess.OutputDataReceived += (sender, args) =>
                {
                    if (!string.IsNullOrWhiteSpace(args.Data))
                        stdout.AppendLine(args.Data);
                };
                ffMpegProcess.ErrorDataReceived += (sender, args) =>
                {
                    if (!string.IsNullOrWhiteSpace(args.Data))
                        stderr.AppendLine(args.Data);
                };
                ffMpegProcess.Start();
                ffMpegProcess.BeginOutputReadLine();
                ffMpegProcess.BeginErrorReadLine();
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                stderr.AppendLine(ex.ToString());
            }

            if (ffMpegProcess != null)
            {
                var waitTask = ffMpegProcess.WaitForExitAsync();
                var completed = await Task.WhenAny(waitTask, Task.Delay(timeout));
                if (completed == waitTask)
                    result.ExitCode = ffMpegProcess.ExitCode;
                else
                {
                    result.TimedOut = true;
                    try { ffMpegProcess.Kill(true); } catch { }
                }
            }

            result.StdOut = stdout.ToString();
            result.StdErr = stderr.ToString();
            return result;
        }

        internal async Task<bool> ReencodeMp3Async(string filePath, string outputFilePath, int sampleRate, int bitrateKbps)
        {
            try
            {
                if (File.Exists(outputFilePath))
                    File.Delete(outputFilePath);

                string arguments = $"-y -i \"{filePath}\" -c:a libmp3lame -b:a {bitrateKbps}k -ar {sampleRate} \"{outputFilePath}\"";
                var run = await StartFFMpegProcess(arguments);
                WriteRunLog("mp3", filePath, outputFilePath, arguments, run);

                if (!OutputLooksValid(outputFilePath, run))
                {
                    arguments = $"-y -i \"{filePath}\" -c:a mp3 -b:a {bitrateKbps}k -ar {sampleRate} \"{outputFilePath}\"";
                    run = await StartFFMpegProcess(arguments);
                    WriteRunLog("mp3-fallback", filePath, outputFilePath, arguments, run);
                }

                return OutputLooksValid(outputFilePath, run);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return false;
            }
        }

        internal async Task<bool> ReencodeOggAsync(string filePath, string outputFilePath, int sampleRate, int channels, double quality, Func<string, bool>? outputValidator = null)
        {
            try
            {
                int fallbackBitrate = VorbisBitrateFromQuality(quality);
                string qualityArg = quality.ToString("0.0", CultureInfo.InvariantCulture);
                var attempts = new (string Label, string Arguments)[]
                {
                    ("ogg-q", $"-fflags +genpts -y -i \"{filePath}\" -c:a libvorbis -q:a {qualityArg} -ar {sampleRate} -ac {channels} \"{outputFilePath}\""),
                    ("ogg-q-fallback", $"-fflags +genpts -y -i \"{filePath}\" -c:a vorbis -q:a {qualityArg} -ar {sampleRate} -ac {channels} \"{outputFilePath}\""),
                    ("ogg-b", $"-fflags +genpts -y -i \"{filePath}\" -c:a libvorbis -b:a {fallbackBitrate}k -ar {sampleRate} -ac {channels} \"{outputFilePath}\""),
                    ("ogg-b-fallback", $"-fflags +genpts -y -i \"{filePath}\" -c:a vorbis -b:a {fallbackBitrate}k -ar {sampleRate} -ac {channels} \"{outputFilePath}\""),
                };

                foreach (var attempt in attempts)
                {
                    TryDeleteFile(outputFilePath);
                    var run = await StartFFMpegProcess(attempt.Arguments);
                    WriteRunLog(attempt.Label, filePath, outputFilePath, attempt.Arguments, run);

                    if (OutputLooksValid(outputFilePath, run, outputValidator))
                        return true;
                }

                return false;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return false;
            }
        }

        // Output arguments of the three WAV commands of the adaptive profile. The one-sound commands and the
        // batched ones (BatchedFfmpegAdaptiveAudioBackend: many sounds in one ffmpeg process) are built from
        // the same strings, so a sound gets the same bytes either way.
        internal static string BuildWavOutputArguments(int sampleRate, int channels, AudioContext.WavCodecKind codec)
        {
            string codecArg = codec == AudioContext.WavCodecKind.AdpcmMs ? "adpcm_ms" : "pcm_s16le";
            return $"-c:a {codecArg} -ar {sampleRate} -ac {channels}";
        }

        internal static string BuildAdaptiveWavOutputArguments(
            int sampleRate,
            int channels,
            AudioContext.WavCodecKind codec,
            int blockAlign,
            int sourceSampleRate)
        {
            string codecArguments = codec == AudioContext.WavCodecKind.AdpcmMs
                ? $"-c:a adpcm_ms -block_size {blockAlign}"
                : "-c:a pcm_s16le";
            string filter = BuildAdaptiveResampleFilter(sourceSampleRate, sampleRate);
            return $"-map_metadata -1 -vn -af \"{filter}\" -ac {channels} -ar {sampleRate} {codecArguments}";
        }

        internal static string BuildDecodeFloatOutputArguments(int sampleRate, int channels) =>
            $"-vn -ac {channels} -ar {sampleRate} -f f32le -acodec pcm_f32le";

        /// <summary>One ffmpeg process for a prepared argument string (inputs and outputs of many sounds).</summary>
        internal async Task<FfmpegRunResult> RunBatchAsync(string arguments, TimeSpan timeout, string label)
        {
            FfmpegRunResult run = await StartFFMpegProcess(arguments, timeout).ConfigureAwait(false);
            if (run.TimedOut || run.ExitCode != 0)
                WriteRunLog(label, "(batch)", "(batch)", arguments, run);
            return run;
        }

        internal static bool OutputFileLooksValid(string outputFilePath) =>
            File.Exists(outputFilePath) && new FileInfo(outputFilePath).Length > 0;

        internal async Task<bool> ReencodeWavAsync(string filePath, string outputFilePath, int sampleRate, int channels, AudioContext.WavCodecKind codec)
        {
            try
            {
                if (File.Exists(outputFilePath))
                    File.Delete(outputFilePath);

                string arguments = $"-y -i \"{filePath}\" {BuildWavOutputArguments(sampleRate, channels, codec)} \"{outputFilePath}\"";
                var run = await _runWav(arguments);
                WriteRunLog("wav", filePath, outputFilePath, arguments, run);
                return OutputLooksValid(outputFilePath, run);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return false;
            }
        }

        /// <summary>
        /// Resample filter of the adaptive WAV candidates. With s16 in and out, swr picks an S16P internal
        /// format, so libsoxr gets INT16 I/O and applies its own TPDF dither to the output, seeded from
        /// time(0) and a heap address (FFmpeg never passes SOXR_NO_DITHER; dither_method=none only covers
        /// swr's own dither). That made every resampled candidate differ by up to 2 LSB between runs.
        /// A double internal format keeps soxr on float I/O and swr rounds once at the end without dither,
        /// so the bytes are the same on every run. Same-rate candidates never create a soxr context and
        /// keep the S16P path, so their bytes stay what they were.
        /// </summary>
        internal static string BuildAdaptiveResampleFilter(int sourceSampleRate, int targetSampleRate)
        {
            string filter = $"aresample=osr={targetSampleRate}:resampler=soxr:precision=28:cheby=1:osf=s16:dither_method=none";
            if (sourceSampleRate != targetSampleRate)
                filter += ":internal_sample_fmt=dblp";
            return filter;
        }

        internal async Task<bool> ReencodeAdaptiveWavAsync(
            string filePath,
            string outputFilePath,
            int sampleRate,
            int channels,
            AudioContext.WavCodecKind codec,
            int blockAlign,
            int sourceSampleRate)
        {
            try
            {
                TryDeleteFile(outputFilePath);
                string arguments =
                    $"-hide_banner -nostdin -y -loglevel warning -i \"{filePath}\" " +
                    $"{BuildAdaptiveWavOutputArguments(sampleRate, channels, codec, blockAlign, sourceSampleRate)} " +
                    $"\"{outputFilePath}\"";
                FfmpegRunResult run = await StartFFMpegProcess(arguments).ConfigureAwait(false);
                WriteRunLog("wav-adaptive", filePath, outputFilePath, arguments, run);
                return OutputLooksValid(outputFilePath, run);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                TryDeleteFile(outputFilePath);
                return false;
            }
        }

        internal async Task<float[]?> DecodeWavFloatAsync(string filePath, int sampleRate, int channels)
        {
            Process? process = null;
            try
            {
                string arguments =
                    $"-hide_banner -nostdin -loglevel error -i \"{filePath}\" " +
                    $"{BuildDecodeFloatOutputArguments(sampleRate, channels)} pipe:1";
                process = new Process
                {
                    StartInfo = new ProcessStartInfo
                    {
                        FileName = _ffmpegFilePath,
                        Arguments = arguments,
                        UseShellExecute = false,
                        CreateNoWindow = true,
                        RedirectStandardOutput = true,
                        RedirectStandardError = true
                    }
                };
                if (!process.Start())
                    return null;

                using var pcm = new MemoryStream();
                Task copyTask = process.StandardOutput.BaseStream.CopyToAsync(pcm);
                Task<string> errorTask = process.StandardError.ReadToEndAsync();
                Task waitTask = process.WaitForExitAsync();
                Task all = Task.WhenAll(copyTask, errorTask, waitTask);
                if (await Task.WhenAny(all, Task.Delay(TimeSpan.FromMinutes(2))).ConfigureAwait(false) != all)
                {
                    try { process.Kill(true); } catch { }
                    AppendAudioLog($"WAV-ADAPTIVE-DECODE-TIMEOUT | {filePath}");
                    return null;
                }
                await all.ConfigureAwait(false);
                string stderr = await errorTask.ConfigureAwait(false);
                if (process.ExitCode != 0)
                {
                    AppendAudioLog($"WAV-ADAPTIVE-DECODE-FAIL | {filePath} | exit={process.ExitCode} | {TrimLog(stderr, 20)}");
                    return null;
                }
                byte[] bytes = pcm.ToArray();
                if (bytes.Length == 0 || bytes.Length % (sizeof(float) * channels) != 0)
                    return null;
                var samples = new float[bytes.Length / sizeof(float)];
                Buffer.BlockCopy(bytes, 0, samples, 0, bytes.Length);
                return samples;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return null;
            }
            finally
            {
                process?.Dispose();
            }
        }
    }
}
