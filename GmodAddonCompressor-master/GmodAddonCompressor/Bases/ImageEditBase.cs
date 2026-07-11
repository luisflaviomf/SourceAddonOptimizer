using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Systems;
using ImageMagick;
using Microsoft.Extensions.Logging;
using System;
using System.Drawing;
using System.IO;
using System.Threading.Tasks;
using GmodAddonCompressor.CustomExtensions;

namespace GmodAddonCompressor.Bases
{
    internal abstract class ImageEditBase
    {
        protected string _fileExtension = string.Empty;
        private readonly ILogger _logger = LogSystem.CreateLogger<ImageEditBase>();

        protected int[] GetImageSize(string imageFilePath)
        {
            int width = 0;
            int height = 0;

            using (FileStream fs = new FileStream(imageFilePath, FileMode.Open, FileAccess.Read))
            {
                using (Image image = Image.FromStream(fs))
                {
                    try
                    {
                        Bitmap original = (Bitmap)image;

                        width = original.Width;
                        height = original.Height;
                    }
                    catch (Exception ex)
                    {
                         _logger.LogError(ex.ToString());
                    }
                }
            }

            return new int[]
            {
                width,
                height,
            };
        }

        protected int[] GetReduceResolutionSize(int width, int height)
        {
            int skipWidth = ImageContext.SkipWidth;
            int skipHeight = ImageContext.SkipHeight;

            int newWidth = 0;
            int newHeight = 0;

            if (
                width != 0 && height != 0
                && (skipWidth == 0 || width > skipWidth)
                && (skipHeight == 0 || height > skipHeight)
            )
            {
                if (ImageContext.ReduceExactlyToLimits)
                {
                    newWidth = ImageContext.TaargetWidth;
                    newHeight = ImageContext.TargetHeight;
                }
                else
                {
                    int resolution = ImageContext.Resolution;
                    newWidth = FloorPowerTwo(width / resolution);
                    newHeight = FloorPowerTwo(height / resolution);
                }
            }

            return new int[]
            {
                newWidth,
                newHeight,
            };
        }

        protected bool TryGetResizeBounds(int originalWidth, int originalHeight, bool isSingleColor, out int resizeWidth, out int resizeHeight)
        {
            resizeWidth = 0;
            resizeHeight = 0;

            int[] newImageSize = GetReduceResolutionSize(originalWidth, originalHeight);
            int newWidth = newImageSize[0];
            int newHeight = newImageSize[1];

            if (newWidth <= 0 || newHeight <= 0)
                return false;

            resizeWidth = isSingleColor ? 1 : Math.Max(ImageContext.TaargetWidth, newWidth);
            resizeHeight = isSingleColor ? 1 : Math.Max(ImageContext.TargetHeight, newHeight);

            if (resizeWidth > originalWidth || resizeHeight > originalHeight)
            {
                resizeWidth = 0;
                resizeHeight = 0;
                return false;
            }

            return true;
        }

        protected bool ImageIsSingleColor(string imageFilePath)
        {
            IMagickColor<ushort>? firstColorPixel = null;
            bool isFindedColor = false;
            bool isSingleColor = true;

            using (var image = new MagickImage(imageFilePath))
            {
                using (IPixelCollection<ushort> pixels = image.GetPixels())
                {
                    try
                    {
                        for (int xPixel = 0; xPixel < image.Width; xPixel++)
                        {
                            for (int yPixel = 0; yPixel < image.Height; yPixel++)
                            {
                                IPixel<ushort> getPixel = pixels.GetPixel(xPixel, yPixel);
                                IMagickColor<ushort>? getColor = getPixel.ToColor();

                                if (!isFindedColor)
                                {
                                    firstColorPixel = getColor;
                                    isFindedColor = true;
                                }
                                else if (firstColorPixel == null || getColor == null || !firstColorPixel.Equals(getColor))
                                {
                                    isSingleColor = false;
                                    break;
                                }
                            }

                            if (!isSingleColor) break;
                        }
                    }
                    catch (Exception ex)
                    {
                        _logger.LogError(ex.ToString());
                    }
                }
            }

            /*
            using (FileStream fs = new FileStream(imageFilePath, FileMode.Open, FileAccess.Read))
            {
                using (Image image = Image.FromStream(fs))
                {
                    try
                    {
                        Bitmap original = (Bitmap)image;

                        for (int xPixel = 0; xPixel < original.Width; xPixel++)
                        {
                            for (int yPixel = 0; yPixel < original.Height; yPixel++)
                            {
                                Color getColor = original.GetPixel(xPixel, yPixel);

                                if (!isFindedColor)
                                {
                                    firstColorPixel = getColor.ToArgb();
                                    Console.WriteLine($"Pxl : {firstColorPixel}");
                                    isFindedColor = true;
                                }
                                else if (firstColorPixel != getColor.ToArgb())
                                {
                                    Console.WriteLine($"Pxl wrong : {firstColorPixel} != {getColor.ToArgb()}");
                                    isSingleColor = false;
                                    break;
                                }
                            }

                            if (!isSingleColor) break;
                        }
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine(ex);
                    }
                }
            }
            */

            return isSingleColor;
        }

        protected bool ImageIsSingleColor(MagickImage image)
        {
            IMagickColor<ushort>? firstColorPixel = null;
            bool isFindedColor = false;
            bool isSingleColor = true;

            using (IPixelCollection<ushort> pixels = image.GetPixels())
            {
                try
                {
                    for (int xPixel = 0; xPixel < image.Width; xPixel++)
                    {
                        for (int yPixel = 0; yPixel < image.Height; yPixel++)
                        {
                            IPixel<ushort> getPixel = pixels.GetPixel(xPixel, yPixel);
                            IMagickColor<ushort>? getColor = getPixel.ToColor();

                            if (!isFindedColor)
                            {
                                firstColorPixel = getColor;
                                isFindedColor = true;
                            }
                            else if (firstColorPixel == null || getColor == null || !firstColorPixel.Equals(getColor))
                            {
                                isSingleColor = false;
                                break;
                            }
                        }

                        if (!isSingleColor)
                            break;
                    }
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex.ToString());
                }
            }

            return isSingleColor;
        }

        protected bool ImageIsFullTransparent(string imageFilePath)
        {
            bool isTransparent = true;

            using (FileStream fs = new FileStream(imageFilePath, FileMode.Open, FileAccess.Read))
            {
                using (Image image = Image.FromStream(fs))
                {
                    try
                    {
                        Bitmap original = (Bitmap)image;
    
                        for (int xPixel = 0; xPixel < original.Width; xPixel++)
                        {
                            for (int yPixel = 0; yPixel < original.Height; yPixel++)
                            {
                                if (original.GetPixel(xPixel, yPixel).A > 0)
                                {
                                    isTransparent = false;
                                    break;
                                }
                            }

                            if (!isTransparent) break;
                        }
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine(ex);
                    }
                }
            }

            return isTransparent;
        }

        protected bool ImageIsFullTransparent(MagickImage image)
        {
            if (!image.HasAlpha)
                return false;

            using IPixelCollection<ushort> pixels = image.GetPixels();
            try
            {
                for (int xPixel = 0; xPixel < image.Width; xPixel++)
                {
                    for (int yPixel = 0; yPixel < image.Height; yPixel++)
                    {
                        IMagickColor<ushort>? color = pixels.GetPixel(xPixel, yPixel).ToColor();
                        if (color != null && color.A > 0)
                            return false;
                    }
                }

                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return false;
            }
        }

        protected bool IsAlphaFullyOpaque(string imageFilePath)
        {
            try
            {
                using var image = new MagickImage(imageFilePath);
                return IsAlphaFullyOpaque(image);
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return false;
            }
        }

        protected bool IsAlphaFullyOpaque(MagickImage image)
        {
            if (!image.HasAlpha)
                return true;

            using IPixelCollection<ushort> pixels = image.GetPixels();
            try
            {
                for (int xPixel = 0; xPixel < image.Width; xPixel++)
                {
                    for (int yPixel = 0; yPixel < image.Height; yPixel++)
                    {
                        IMagickColor<ushort>? color = pixels.GetPixel(xPixel, yPixel).ToColor();
                        if (color != null && color.A < ushort.MaxValue)
                            return false;
                    }
                }

                return true;
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return false;
            }
        }

        protected Task ImageCompress(string imageFilePath)
        {
            if (string.IsNullOrEmpty(_fileExtension))
                throw new Exception("Not set image file extension");

            if (!File.Exists(imageFilePath))
                return Task.CompletedTask;

            string tempImageFilePath = imageFilePath + "____TEMP" + _fileExtension;

            try
            {
                if (File.Exists(tempImageFilePath))
                    File.Delete(tempImageFilePath);

                long originalFileSize = new FileInfo(imageFilePath).Length;
                if (!SaveMagickImage(imageFilePath, tempImageFilePath) || !File.Exists(tempImageFilePath))
                    return Task.CompletedTask;

                long newFileSize = new FileInfo(tempImageFilePath).Length;
                if (newFileSize > originalFileSize)
                {
                    _logger.LogInformation($"Image compression preserved original (no gain): {imageFilePath.GAC_ToLocalPath()}");
                    return Task.CompletedTask;
                }

                File.Copy(tempImageFilePath, imageFilePath, true);
                _logger.LogInformation($"Successful file compression: {imageFilePath.GAC_ToLocalPath()}");
            }
            finally
            {
                if (File.Exists(tempImageFilePath))
                    File.Delete(tempImageFilePath);
            }

            return Task.CompletedTask;
        }

        protected void SetImageFileExtension(string fileExtension)
        {
            _fileExtension = fileExtension;
        }

        private int FloorPowerTwo(int x)
        {
            if (x < 1) return 1;
            return (int)System.Math.Pow(2, (int)System.Math.Log(x, 2));
        }

        private bool SaveMagickImage(string imageSourcePath, string imageSavePath)
        {
            int[] imageSize = GetImageSize(imageSourcePath);
            if (imageSize[0] == 0 || imageSize[1] == 0)
                return false;

            int[] newImageSize = GetReduceResolutionSize(imageSize[0], imageSize[1]);

            int newWidth = newImageSize[0];
            int newHeight = newImageSize[1];

            bool isSingleColor = ImageIsSingleColor(imageSourcePath);
            
            int resizeWidth = isSingleColor ? 1 : (newWidth < ImageContext.TaargetWidth ? ImageContext.TaargetWidth : newWidth);
            int resizeHeight = isSingleColor ? 1 :(newHeight < ImageContext.TargetHeight ? ImageContext.TargetHeight : newHeight);

            if (newWidth > imageSize[0] || newHeight > imageSize[1])
                return false;

            try
            {
                using (var image = new MagickImage(imageSourcePath))
                {
                    if (File.Exists(imageSavePath))
                        File.Delete(imageSavePath);

                    var size = new MagickGeometry((uint)resizeWidth, (uint)resizeHeight);
                    size.IgnoreAspectRatio = isSingleColor ? true : !ImageContext.KeepImageAspectRatio;

                    image.Resize(size);

                    if (!isSingleColor)
                        image.SetCompression(CompressionMethod.LZMA);

                    image.Write(imageSavePath);
                    return File.Exists(imageSavePath);
                }
            }
            catch (Exception ex)
            {
                _logger.LogError(ex.ToString());
                return false;
            }
        }
    }
}
