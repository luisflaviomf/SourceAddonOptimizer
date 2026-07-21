using ImageMagick;

namespace VtfMaximumLab.Images;

internal sealed record PreparedImage(
    byte[] Rgba,
    int Width,
    int Height,
    double SourceAlphaCoverage,
    double OutputAlphaCoverage);

internal static class MaximumImagePreprocessor
{
    internal static PreparedImage Resize(
        byte[] sourceRgba,
        int sourceWidth,
        int sourceHeight,
        int targetWidth,
        int targetHeight,
        bool isNormalMap,
        bool isCutout,
        double alphaThreshold,
        bool isDxt5NormalMap = false)
    {
        if (sourceWidth <= 0 || sourceHeight <= 0 || targetWidth <= 0 || targetHeight <= 0)
            throw new ArgumentOutOfRangeException(nameof(sourceWidth), "Image dimensions must be positive.");
        if (sourceRgba.Length != checked(sourceWidth * sourceHeight * 4))
            throw new ArgumentException("RGBA buffer length does not match its dimensions.", nameof(sourceRgba));
        if (alphaThreshold is < 0 or > 1)
            throw new ArgumentOutOfRangeException(nameof(alphaThreshold));

        double sourceCoverage = Coverage(sourceRgba, alphaThreshold);
        byte[] resized = sourceWidth == targetWidth && sourceHeight == targetHeight
            ? (byte[])sourceRgba.Clone()
            : isNormalMap
                ? ResizeNormalArea(sourceRgba, sourceWidth, sourceHeight, targetWidth, targetHeight, isDxt5NormalMap)
                : ResizeWithMagickLinearPremultiplied(sourceRgba, sourceWidth, sourceHeight, targetWidth, targetHeight);

        if (isCutout)
            PreserveCoverage(resized, sourceCoverage, alphaThreshold);

        return new PreparedImage(
            resized,
            targetWidth,
            targetHeight,
            sourceCoverage,
            Coverage(resized, alphaThreshold));
    }

    private static byte[] ResizeWithMagickLinearPremultiplied(
        byte[] source,
        int sourceWidth,
        int sourceHeight,
        int targetWidth,
        int targetHeight)
    {
        int sourcePixels = checked(sourceWidth * sourceHeight);
        var premultipliedLinear = new ushort[checked(sourcePixels * 3)];
        var alphaValues = new ushort[checked(sourcePixels * 3)];
        for (int pixel = 0; pixel < sourcePixels; pixel++)
        {
            int sourceOffset = pixel * 4;
            double alpha = source[sourceOffset + 3] / 255.0;
            ushort alphaShort = ToShort(alpha);
            alphaValues[pixel * 3] = alphaShort;
            alphaValues[pixel * 3 + 1] = alphaShort;
            alphaValues[pixel * 3 + 2] = alphaShort;
            premultipliedLinear[pixel * 3] = ToShort(SrgbToLinear(source[sourceOffset] / 255.0) * alpha);
            premultipliedLinear[pixel * 3 + 1] = ToShort(SrgbToLinear(source[sourceOffset + 1] / 255.0) * alpha);
            premultipliedLinear[pixel * 3 + 2] = ToShort(SrgbToLinear(source[sourceOffset + 2] / 255.0) * alpha);
        }

        using var rgb = new MagickImage(MagickColors.Black, checked((uint)sourceWidth), checked((uint)sourceHeight));
        rgb.ColorSpace = ColorSpace.RGB;
        rgb.ReadPixels(premultipliedLinear, new PixelReadSettings(
            checked((uint)sourceWidth), checked((uint)sourceHeight), StorageType.Quantum, PixelMapping.RGB));
        using var alphaImage = new MagickImage(MagickColors.Black, checked((uint)sourceWidth), checked((uint)sourceHeight));
        alphaImage.ColorSpace = ColorSpace.Gray;
        alphaImage.ReadPixels(alphaValues, new PixelReadSettings(
            checked((uint)sourceWidth), checked((uint)sourceHeight), StorageType.Quantum, PixelMapping.RGB));
        var geometry = new MagickGeometry(checked((uint)targetWidth), checked((uint)targetHeight))
        {
            IgnoreAspectRatio = true
        };
        rgb.FilterType = FilterType.LanczosSharp;
        alphaImage.FilterType = FilterType.LanczosSharp;
        rgb.Resize(geometry);
        alphaImage.Resize(geometry);

        using IPixelCollection<ushort> rgbPixels = rgb.GetPixelsUnsafe();
        using IPixelCollection<ushort> alphaPixels = alphaImage.GetPixelsUnsafe();
        ushort[] associated = rgbPixels.ToShortArray(PixelMapping.RGB) ??
                              throw new InvalidDataException("Magick.NET did not return RGB pixels.");
        ushort[] resizedAlphaRgb = alphaPixels.ToShortArray(PixelMapping.RGB) ??
                                   throw new InvalidDataException("Magick.NET did not return alpha pixels.");
        int outputPixels = checked(targetWidth * targetHeight);
        if (associated.Length != outputPixels * 3 || resizedAlphaRgb.Length != outputPixels * 3)
            throw new InvalidDataException("Magick.NET returned an unexpected pixel buffer size.");

        var result = new byte[checked(outputPixels * 4)];
        for (int pixel = 0; pixel < outputPixels; pixel++)
        {
            double alpha = resizedAlphaRgb[pixel * 3] / 65535.0;
            int outputOffset = pixel * 4;
            if (alpha > 1e-8)
            {
                result[outputOffset] = ToByte(LinearToSrgb(Math.Clamp(associated[pixel * 3] / 65535.0 / alpha, 0, 1)));
                result[outputOffset + 1] = ToByte(LinearToSrgb(Math.Clamp(associated[pixel * 3 + 1] / 65535.0 / alpha, 0, 1)));
                result[outputOffset + 2] = ToByte(LinearToSrgb(Math.Clamp(associated[pixel * 3 + 2] / 65535.0 / alpha, 0, 1)));
            }
            result[outputOffset + 3] = ToByte(alpha);
        }
        return result;
    }

    private static byte[] ResizeLinearPremultipliedLanczos(
        byte[] source,
        int sourceWidth,
        int sourceHeight,
        int targetWidth,
        int targetHeight)
    {
        float[] horizontal = new float[checked(targetWidth * sourceHeight * 4)];
        for (int y = 0; y < sourceHeight; y++)
        {
            for (int x = 0; x < targetWidth; x++)
            {
                foreach ((int index, double weight) in Contributions(x, sourceWidth, targetWidth))
                {
                    int sourceOffset = (y * sourceWidth + index) * 4;
                    int targetOffset = (y * targetWidth + x) * 4;
                    double alpha = source[sourceOffset + 3] / 255.0;
                    horizontal[targetOffset] += (float)(SrgbToLinear(source[sourceOffset] / 255.0) * alpha * weight);
                    horizontal[targetOffset + 1] += (float)(SrgbToLinear(source[sourceOffset + 1] / 255.0) * alpha * weight);
                    horizontal[targetOffset + 2] += (float)(SrgbToLinear(source[sourceOffset + 2] / 255.0) * alpha * weight);
                    horizontal[targetOffset + 3] += (float)(alpha * weight);
                }
            }
        }

        byte[] output = new byte[checked(targetWidth * targetHeight * 4)];
        for (int y = 0; y < targetHeight; y++)
        {
            for (int x = 0; x < targetWidth; x++)
            {
                double red = 0, green = 0, blue = 0, alpha = 0;
                foreach ((int index, double weight) in Contributions(y, sourceHeight, targetHeight))
                {
                    int offset = (index * targetWidth + x) * 4;
                    red += horizontal[offset] * weight;
                    green += horizontal[offset + 1] * weight;
                    blue += horizontal[offset + 2] * weight;
                    alpha += horizontal[offset + 3] * weight;
                }

                int outputOffset = (y * targetWidth + x) * 4;
                alpha = Math.Clamp(alpha, 0, 1);
                if (alpha > 1e-8)
                {
                    output[outputOffset] = ToByte(LinearToSrgb(Math.Clamp(red / alpha, 0, 1)));
                    output[outputOffset + 1] = ToByte(LinearToSrgb(Math.Clamp(green / alpha, 0, 1)));
                    output[outputOffset + 2] = ToByte(LinearToSrgb(Math.Clamp(blue / alpha, 0, 1)));
                }
                output[outputOffset + 3] = ToByte(alpha);
            }
        }

        return output;
    }

    private static byte[] ResizeNormalArea(
        byte[] source,
        int sourceWidth,
        int sourceHeight,
        int targetWidth,
        int targetHeight,
        bool isDxt5NormalMap)
    {
        byte[] output = new byte[checked(targetWidth * targetHeight * 4)];
        double scaleX = (double)sourceWidth / targetWidth;
        double scaleY = (double)sourceHeight / targetHeight;

        for (int targetY = 0; targetY < targetHeight; targetY++)
        {
            double startY = targetY * scaleY;
            double endY = (targetY + 1) * scaleY;
            for (int targetX = 0; targetX < targetWidth; targetX++)
            {
                double startX = targetX * scaleX;
                double endX = (targetX + 1) * scaleX;
                double nx = 0, ny = 0, nz = 0, alpha = 0, totalWeight = 0;

                for (int sourceY = (int)Math.Floor(startY); sourceY < Math.Ceiling(endY); sourceY++)
                {
                    double weightY = Math.Max(0, Math.Min(endY, sourceY + 1) - Math.Max(startY, sourceY));
                    int clampedY = Math.Clamp(sourceY, 0, sourceHeight - 1);
                    for (int sourceX = (int)Math.Floor(startX); sourceX < Math.Ceiling(endX); sourceX++)
                    {
                        double weightX = Math.Max(0, Math.Min(endX, sourceX + 1) - Math.Max(startX, sourceX));
                        double weight = weightX * weightY;
                        int clampedX = Math.Clamp(sourceX, 0, sourceWidth - 1);
                        int offset = (clampedY * sourceWidth + clampedX) * 4;
                        double normalX = source[offset + (isDxt5NormalMap ? 3 : 0)] / 127.5 - 1.0;
                        double normalY = source[offset + 1] / 127.5 - 1.0;
                        double normalZ = isDxt5NormalMap
                            ? Math.Sqrt(Math.Max(0, 1 - normalX * normalX - normalY * normalY))
                            : source[offset + 2] / 127.5 - 1.0;
                        nx += normalX * weight;
                        ny += normalY * weight;
                        nz += normalZ * weight;
                        alpha += source[offset + 3] / 255.0 * weight;
                        totalWeight += weight;
                    }
                }

                double length = Math.Sqrt(nx * nx + ny * ny + nz * nz);
                if (length < 1e-8)
                {
                    nx = 0;
                    ny = 0;
                    nz = 1;
                }
                else
                {
                    nx /= length;
                    ny /= length;
                    nz /= length;
                }

                int outputOffset = (targetY * targetWidth + targetX) * 4;
                output[outputOffset] = isDxt5NormalMap ? (byte)128 : ToByte(nx * 0.5 + 0.5);
                output[outputOffset + 1] = ToByte(ny * 0.5 + 0.5);
                output[outputOffset + 2] = isDxt5NormalMap ? (byte)255 : ToByte(nz * 0.5 + 0.5);
                output[outputOffset + 3] = isDxt5NormalMap
                    ? ToByte(nx * 0.5 + 0.5)
                    : ToByte(alpha / Math.Max(totalWeight, 1e-8));
            }
        }

        return output;
    }

    private static IReadOnlyList<(int Index, double Weight)> Contributions(int targetIndex, int sourceSize, int targetSize)
    {
        double scale = (double)sourceSize / targetSize;
        double supportScale = Math.Max(1.0, scale);
        double center = (targetIndex + 0.5) * scale - 0.5;
        int first = (int)Math.Ceiling(center - 3 * supportScale);
        int last = (int)Math.Floor(center + 3 * supportScale);
        var combined = new Dictionary<int, double>();
        double sum = 0;
        for (int sourceIndex = first; sourceIndex <= last; sourceIndex++)
        {
            double distance = (center - sourceIndex) / supportScale;
            double weight = Lanczos3(distance);
            if (Math.Abs(weight) < 1e-12)
                continue;
            int clamped = Math.Clamp(sourceIndex, 0, sourceSize - 1);
            combined[clamped] = combined.TryGetValue(clamped, out double existing) ? existing + weight : weight;
            sum += weight;
        }

        return combined.Select(pair => (pair.Key, pair.Value / sum)).ToArray();
    }

    private static void PreserveCoverage(byte[] rgba, double targetCoverage, double threshold)
    {
        byte[] originalAlpha = new byte[rgba.Length / 4];
        for (int pixel = 0; pixel < originalAlpha.Length; pixel++)
            originalAlpha[pixel] = rgba[pixel * 4 + 3];

        double low = 0;
        double high = 8;
        double bestScale = 1;
        double bestError = double.MaxValue;
        for (int iteration = 0; iteration < 24; iteration++)
        {
            double scale = (low + high) * 0.5;
            int covered = originalAlpha.Count(alpha => Math.Min(255, alpha * scale) >= threshold * 255.0);
            double coverage = (double)covered / originalAlpha.Length;
            double error = Math.Abs(coverage - targetCoverage);
            if (error < bestError)
            {
                bestError = error;
                bestScale = scale;
            }
            if (coverage < targetCoverage)
                low = scale;
            else
                high = scale;
        }

        for (int pixel = 0; pixel < originalAlpha.Length; pixel++)
            rgba[pixel * 4 + 3] = ToByte(Math.Min(1, originalAlpha[pixel] / 255.0 * bestScale));
    }

    private static double Coverage(byte[] rgba, double threshold)
    {
        int covered = 0;
        for (int offset = 3; offset < rgba.Length; offset += 4)
        {
            if (rgba[offset] >= threshold * 255.0)
                covered++;
        }
        return (double)covered / (rgba.Length / 4);
    }

    private static double Lanczos3(double value)
    {
        value = Math.Abs(value);
        if (value < 1e-12)
            return 1;
        if (value >= 3)
            return 0;
        return Math.Sin(Math.PI * value) * Math.Sin(Math.PI * value / 3) /
               (Math.PI * Math.PI * value * value / 3);
    }

    private static double SrgbToLinear(double value) =>
        value <= 0.04045 ? value / 12.92 : Math.Pow((value + 0.055) / 1.055, 2.4);

    private static double LinearToSrgb(double value) =>
        value <= 0.0031308 ? value * 12.92 : 1.055 * Math.Pow(value, 1.0 / 2.4) - 0.055;

    private static byte ToByte(double value) => (byte)Math.Clamp((int)Math.Round(value * 255), 0, 255);

    private static ushort ToShort(double value) => (ushort)Math.Clamp((int)Math.Round(value * 65535), 0, 65535);
}
