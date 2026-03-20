using GmodAddonCompressor.Bases;
using GmodAddonCompressor.CustomExtensions;
using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Systems;
using ImageMagick;
using Microsoft.Extensions.Logging;
using System;
using System.IO;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Objects
{
    internal sealed class MagickPngEdit : ImageEditBase, ICompress
    {
        private readonly ILogger _logger = LogSystem.CreateLogger<MagickPngEdit>();
        private readonly PNGEdit _standardFallback;

        public MagickPngEdit()
            : this(new PNGEdit())
        {
        }

        internal MagickPngEdit(PNGEdit standardFallback)
        {
            _standardFallback = standardFallback;
        }

        public async Task Compress(string pngFilePath)
        {
            try
            {
                if (TryCompressWithMagick(pngFilePath))
                    return;

                _logger.LogInformation($"[MAGICK][PNG] Falling back to Standard pipeline: {pngFilePath.GAC_ToLocalPath()}");
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                _logger.LogInformation($"[MAGICK][PNG] Exception, falling back to Standard pipeline: {pngFilePath.GAC_ToLocalPath()}");
            }

            await _standardFallback.Compress(pngFilePath);
        }

        private bool TryCompressWithMagick(string pngFilePath)
        {
            string tempPath = pngFilePath + ".__magick_png_tmp";
            long originalBytes = new FileInfo(pngFilePath).Length;

            try
            {
                using MagickImage image = new MagickImage(pngFilePath);
                if (!TryApplyResize(image, pngFilePath))
                    return false;

                image.Strip();
                image.Quantize(new QuantizeSettings
                {
                    Colors = 256,
                    DitherMethod = DitherMethod.No
                });
                image.Format = MagickFormat.Png8;
                image.Settings.SetDefine(MagickFormat.Png, "compression-level", "9");
                image.Settings.SetDefine(MagickFormat.Png, "compression-filter", "5");
                image.Settings.SetDefine(MagickFormat.Png, "compression-strategy", "1");
                image.Write(tempPath);

                long newBytes = new FileInfo(tempPath).Length;
                if (newBytes >= originalBytes)
                    return false;

                File.Copy(tempPath, pngFilePath, true);
                _logger.LogInformation(
                    $"[MAGICK][PNG] Optimized {pngFilePath.GAC_ToLocalPath()}: {FormatBytes(originalBytes)} -> {FormatBytes(newBytes)}");
                return true;
            }
            finally
            {
                TryDelete(tempPath);
            }
        }

        private bool TryApplyResize(MagickImage image, string sourcePath)
        {
            int originalWidth = (int)image.Width;
            int originalHeight = (int)image.Height;
            int[] reducedSize = GetReduceResolutionSize(originalWidth, originalHeight);
            int newWidth = reducedSize[0];
            int newHeight = reducedSize[1];
            bool isSingleColor = ImageIsSingleColor(sourcePath);

            newWidth = isSingleColor ? 1 : Math.Max(ImageContext.TaargetWidth, newWidth);
            newHeight = isSingleColor ? 1 : Math.Max(ImageContext.TargetHeight, newHeight);

            if (newWidth <= 0 || newHeight <= 0)
                return false;

            if (newWidth > originalWidth || newHeight > originalHeight)
                return false;

            if (newWidth == originalWidth && newHeight == originalHeight)
                return true;

            image.FilterType = FilterType.LanczosSharp;
            var geometry = new MagickGeometry((uint)newWidth, (uint)newHeight)
            {
                IgnoreAspectRatio = isSingleColor || !ImageContext.KeepImageAspectRatio
            };
            image.Resize(geometry);
            return true;
        }

        private static void TryDelete(string path)
        {
            try
            {
                if (File.Exists(path))
                    File.Delete(path);
            }
            catch
            {
            }
        }

        private static string FormatBytes(long bytes)
        {
            double value = bytes;
            string[] units = { "B", "KB", "MB", "GB", "TB" };
            int unitIndex = 0;

            while (value >= 1024 && unitIndex < units.Length - 1)
            {
                value /= 1024;
                unitIndex++;
            }

            return $"{value:0.##} {units[unitIndex]}";
        }
    }
}
