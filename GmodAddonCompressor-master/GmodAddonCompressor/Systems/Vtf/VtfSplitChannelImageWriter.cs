using ImageMagick;
using System;
using System.Collections.Generic;

namespace GmodAddonCompressor.Systems.Vtf
{
    internal static class VtfSplitChannelImageWriter
    {
        internal static bool TryWriteResizedPng(
            MagickImage sourceImage,
            string outputPath,
            int targetWidth,
            int targetHeight,
            bool ignoreAspectRatio,
            bool preserveAlpha,
            bool useAlphaAwareResize)
        {
            if (sourceImage == null)
                throw new ArgumentNullException(nameof(sourceImage));

            if (targetWidth <= 0 || targetHeight <= 0)
                return false;

            if (preserveAlpha && useAlphaAwareResize && sourceImage.HasAlpha)
            {
                bool sourceHasNonOpaqueAlpha = !IsAlphaFullyOpaque(sourceImage);
                if (TryWriteAlphaAwareResizedPng(
                        sourceImage,
                        outputPath,
                        targetWidth,
                        targetHeight,
                        ignoreAspectRatio,
                        sourceHasNonOpaqueAlpha))
                {
                    return true;
                }
            }

            IReadOnlyList<IMagickImage<ushort>> channels = sourceImage.Separate();
            try
            {
                if (channels.Count < 3)
                    return false;

                Resize(channels[0], targetWidth, targetHeight, ignoreAspectRatio);
                Resize(channels[1], targetWidth, targetHeight, ignoreAspectRatio);
                Resize(channels[2], targetWidth, targetHeight, ignoreAspectRatio);

                using var rgbCollection = new MagickImageCollection();
                rgbCollection.Add(channels[0]);
                rgbCollection.Add(channels[1]);
                rgbCollection.Add(channels[2]);

                using var combined = new MagickImage(rgbCollection.Combine(ColorSpace.sRGB));
                if (preserveAlpha && channels.Count > 3)
                {
                    Resize(channels[3], targetWidth, targetHeight, ignoreAspectRatio);
                    combined.Alpha(AlphaOption.Set);
                    combined.Composite(channels[3], 0, 0, CompositeOperator.CopyAlpha);
                }
                else if (combined.HasAlpha)
                {
                    combined.Alpha(AlphaOption.Remove);
                }

                combined.Strip();
                combined.Write(outputPath);
                return true;
            }
            finally
            {
                foreach (IMagickImage<ushort> channel in channels)
                    channel.Dispose();
            }
        }

        private static bool TryWriteAlphaAwareResizedPng(
            MagickImage sourceImage,
            string outputPath,
            int targetWidth,
            int targetHeight,
            bool ignoreAspectRatio,
            bool sourceHasNonOpaqueAlpha)
        {
            using var working = sourceImage.Clone();
            working.Alpha(AlphaOption.Set);
            working.Alpha(AlphaOption.Associate);
            Resize(working, targetWidth, targetHeight, ignoreAspectRatio);
            working.Alpha(AlphaOption.Disassociate);

            if (sourceHasNonOpaqueAlpha && IsAlphaFullyOpaque(working))
                return false;

            working.Strip();
            working.Write(outputPath);
            return true;
        }

        private static bool IsAlphaFullyOpaque(IMagickImage<ushort> image)
        {
            if (!image.HasAlpha)
                return true;

            using IPixelCollection<ushort> pixels = image.GetPixels();
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

        private static void Resize(IMagickImage<ushort> image, int targetWidth, int targetHeight, bool ignoreAspectRatio)
        {
            image.FilterType = FilterType.LanczosSharp;
            image.Resize(new MagickGeometry((uint)targetWidth, (uint)targetHeight)
            {
                IgnoreAspectRatio = ignoreAspectRatio
            });
        }
    }
}
