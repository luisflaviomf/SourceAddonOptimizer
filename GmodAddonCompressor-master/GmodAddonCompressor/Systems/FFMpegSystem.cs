using GmodAddonCompressor.Properties;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Systems.Tools;
using Microsoft.Extensions.Logging;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Systems
{
    internal class FFMpegSystem
    {
        private const string _mainDirectoryFFMpeg = "ffmpeg";
        private readonly string _ffmpegFilePath;
        private readonly ILogger _logger = LogSystem.CreateLogger<FFMpegSystem>();

        private sealed class FfmpegRunResult
        {
            public int ExitCode { get; set; } = -1;
            public bool TimedOut { get; set; }
            public string StdOut { get; set; } = string.Empty;
            public string StdErr { get; set; } = string.Empty;
        }

        internal static string AudioLogPath =>
            Path.Combine(ToolPaths.AppDataRoot, "logs", "audio-compress.log");

        public FFMpegSystem()
        {
            string baseDirectory = AppDomain.CurrentDomain.BaseDirectory;
            string appDirectory = Path.Combine(baseDirectory, _mainDirectoryFFMpeg);

            if (!Directory.Exists(appDirectory))
            {
                string zipResourcePath = Path.Combine(baseDirectory, _mainDirectoryFFMpeg + ".zip");

                if (!File.Exists(zipResourcePath))
                    File.WriteAllBytes(zipResourcePath, Resources.ffmpeg);

                ZipFile.ExtractToDirectory(zipResourcePath, appDirectory);
                File.Delete(zipResourcePath);
            }

            _ffmpegFilePath = Path.Combine(appDirectory, "ffmpeg.exe");
        }

        internal static void AppendAudioLog(string message)
        {
            try
            {
                string? dir = Path.GetDirectoryName(AudioLogPath);
                if (!string.IsNullOrWhiteSpace(dir))
                    Directory.CreateDirectory(dir);

                File.AppendAllText(AudioLogPath, message + Environment.NewLine, Encoding.UTF8);
            }
            catch
            {
                // Avoid breaking the pipeline on logging errors.
            }
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

            string err = TrimLog(result.StdErr, 40);
            if (!string.IsNullOrWhiteSpace(err))
            {
                sb.AppendLine("STDERR:");
                sb.AppendLine(err);
            }

            string output = TrimLog(result.StdOut, 20);
            if (!string.IsNullOrWhiteSpace(output))
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

        private async Task<FfmpegRunResult> StartFFMpegProcess(string arguments)
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
                    {
                        stdout.AppendLine(args.Data);
                        _logger.LogDebug(args.Data);
                    }
                };
                ffMpegProcess.ErrorDataReceived += (sender, args) =>
                {
                    if (!string.IsNullOrWhiteSpace(args.Data))
                    {
                        stderr.AppendLine(args.Data);
                        _logger.LogDebug(args.Data);
                    }
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
                var completed = await Task.WhenAny(waitTask, Task.Delay(TimeSpan.FromMinutes(2)));
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

        internal async Task<bool> ReencodeWavAsync(string filePath, string outputFilePath, int sampleRate, int channels, AudioContext.WavCodecKind codec)
        {
            try
            {
                if (File.Exists(outputFilePath))
                    File.Delete(outputFilePath);

                string codecArg = codec == AudioContext.WavCodecKind.AdpcmMs ? "adpcm_ms" : "pcm_s16le";
                string arguments = $"-y -i \"{filePath}\" -c:a {codecArg} -ar {sampleRate} -ac {channels} \"{outputFilePath}\"";
                var run = await StartFFMpegProcess(arguments);
                WriteRunLog("wav", filePath, outputFilePath, arguments, run);

                if (!File.Exists(outputFilePath))
                    return false;

                if (new FileInfo(outputFilePath).Length == 0)
                {
                    File.Delete(outputFilePath);
                    return false;
                }

                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return false;
            }
        }
    }
}
