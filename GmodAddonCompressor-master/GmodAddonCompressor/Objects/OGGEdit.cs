using GmodAddonCompressor.CustomExtensions;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Systems;
using Microsoft.Extensions.Logging;
using NAudio.Vorbis;
using System;
using System.IO;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Objects
{
    internal class OGGEdit : ICompress
    {
        private readonly ILogger _logger = LogSystem.CreateLogger<OGGEdit>();

        public async Task Compress(string oggFilePath)
        {
            string newOggFilePath = oggFilePath + "____TEMP.ogg";

            if (File.Exists(newOggFilePath))
                File.Delete(newOggFilePath);

            bool converted = false;
            try
            {
                converted = await new FFMpegSystem().ReencodeOggAsync(
                    oggFilePath,
                    newOggFilePath,
                    AudioContext.OggSampleRate,
                    AudioContext.OggChannels,
                    AudioContext.OggQuality,
                    path => ValidateOggOutput(path, AudioContext.OggSampleRate, AudioContext.OggChannels));

                if (!converted)
                {
                    _logger.LogError($"OGG compression failed: {oggFilePath.GAC_ToLocalPath()}");
                }
                else
                {
                    if (!TryReplaceFile(newOggFilePath, oggFilePath, out string? replaceError))
                    {
                        _logger.LogError($"OGG replace failed: {oggFilePath.GAC_ToLocalPath()}");
                        if (!string.IsNullOrWhiteSpace(replaceError))
                            FFMpegSystem.AppendAudioLog($"OGG-REPLACE-FAIL | {oggFilePath} | {replaceError}");
                    }
                    else
                    {
                        _logger.LogInformation($"Successful OGG re-encode: {oggFilePath.GAC_ToLocalPath()}");
                        AppendOggInfo(oggFilePath);
                    }
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
            }
            finally
            {
                if (File.Exists(newOggFilePath))
                    File.Delete(newOggFilePath);
            }
        }

        private static void AppendOggInfo(string oggFilePath)
        {
            try
            {
                using var reader = new VorbisWaveReader(oggFilePath);
                double seconds = reader.TotalTime.TotalSeconds;
                long bytes = new FileInfo(oggFilePath).Length;
                double kbps = seconds > 0 ? (bytes * 8.0 / 1000.0) / seconds : 0;

                FFMpegSystem.AppendAudioLog(
                    $"OGG-INFO | {oggFilePath} | {reader.WaveFormat.SampleRate} Hz | {reader.WaveFormat.Channels} ch | ~{Math.Round(kbps)} kbps (VBR)");
            }
            catch (Exception ex)
            {
                FFMpegSystem.AppendAudioLog($"OGG-INFO-FAIL | {oggFilePath} | {ex.Message}");
            }
        }

        private static bool ValidateOggOutput(string oggFilePath, int expectedSampleRate, int expectedChannels)
        {
            try
            {
                using var reader = new VorbisWaveReader(oggFilePath);
                if (reader.WaveFormat.SampleRate != expectedSampleRate)
                {
                    FFMpegSystem.AppendAudioLog(
                        $"OGG-VALIDATE-FAIL | {oggFilePath} | Expected {expectedSampleRate} Hz | Actual {reader.WaveFormat.SampleRate} Hz");
                    return false;
                }
                if (reader.WaveFormat.Channels != expectedChannels)
                {
                    FFMpegSystem.AppendAudioLog(
                        $"OGG-VALIDATE-FAIL | {oggFilePath} | Expected {expectedChannels} ch | Actual {reader.WaveFormat.Channels} ch");
                    return false;
                }

                return true;
            }
            catch (Exception ex)
            {
                FFMpegSystem.AppendAudioLog($"OGG-VALIDATE-FAIL | {oggFilePath} | {ex.Message}");
                return false;
            }
        }

        private static bool TryReplaceFile(string tempFilePath, string destinationPath, out string? error)
        {
            error = null;
            try
            {
                if (File.Exists(destinationPath))
                {
                    File.Replace(tempFilePath, destinationPath, null, true);
                }
                else
                {
                    File.Move(tempFilePath, destinationPath, true);
                }

                return true;
            }
            catch (Exception ex)
            {
                error = ex.Message;
                return false;
            }
        }
    }
}
