using GmodAddonCompressor.CustomExtensions;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Systems;
using Microsoft.Extensions.Logging;
using System;
using System.IO;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Objects
{
    internal class MP3Edit : ICompress
    {
        private readonly ILogger _logger = LogSystem.CreateLogger<WAVEdit>();

        public async Task Compress(string mp3FilePath)
        {
            string tempMp3Path = mp3FilePath + "____TEMP.mp3";

            if (File.Exists(tempMp3Path))
                File.Delete(tempMp3Path);

            bool converted = false;
            try
            {
                converted = await new FFMpegSystem().ReencodeMp3Async(
                    mp3FilePath,
                    tempMp3Path,
                    AudioContext.Mp3SampleRate,
                    AudioContext.Mp3BitrateKbps);

                if (!converted)
                {
                    _logger.LogError($"MP3 compression failed: {mp3FilePath.GAC_ToLocalPath()}");
                }
                else
                {
                    File.Delete(mp3FilePath);
                    File.Move(tempMp3Path, mp3FilePath);
                    _logger.LogInformation($"Successful MP3 re-encode: {mp3FilePath.GAC_ToLocalPath()}");
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
            }
            finally
            {
                if (File.Exists(tempMp3Path))
                    File.Delete(tempMp3Path);
            }
        }
    }
}
