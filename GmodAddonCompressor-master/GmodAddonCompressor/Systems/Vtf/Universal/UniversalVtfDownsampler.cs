using ImageMagick;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfImagePlane(
    int Width,
    int Height,
    int Frame,
    int Face,
    int DepthSlice,
    byte[] Rgba);

internal static class UniversalVtfDownsampler
{
    private const double VectorEpsilon = 1e-12;

    internal static UniversalVtfImagePlane Downsample(
        UniversalVtfImagePlane source,
        UniversalVtfDimensions target,
        UniversalVtfFilterRoute route,
        UniversalVtfSemanticDecision semantics)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentNullException.ThrowIfNull(semantics);
        Validate(source, target);

        if (route == UniversalVtfFilterRoute.Composite)
        {
            throw new InvalidOperationException(
                "Composite VTF semantics require an explicit, non-ambiguous filter route.");
        }

        byte[] output = source.Width == target.Width && source.Height == target.Height
            ? (byte[])source.Rgba.Clone()
            : route switch
            {
                UniversalVtfFilterRoute.SrgbColor => ResizeSrgbPremultiplied(source, target),
                UniversalVtfFilterRoute.NumericLinear => ResizeIndependentChannels(source, target),
                UniversalVtfFilterRoute.SsbumpScalars => ResizeIndependentChannels(source, target),
                UniversalVtfFilterRoute.RgbNormal => ResizeRgbNormal(source, target),
                UniversalVtfFilterRoute.Dxt5NmAg => ResizeDxt5Nm(source, target),
                _ => throw new ArgumentOutOfRangeException(nameof(route), route, "Unknown filter route.")
            };

        if (semantics.Classes.Contains(UniversalVtfSemanticClass.Cutout))
        {
            PreserveCutoutCoverage(source.Rgba, output, semantics.AlphaTestReference);
        }

        return new UniversalVtfImagePlane(
            target.Width,
            target.Height,
            source.Frame,
            source.Face,
            source.DepthSlice,
            output);
    }

    private static void Validate(
        UniversalVtfImagePlane source,
        UniversalVtfDimensions target)
    {
        if (source.Width <= 0 || source.Height <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(source), "Source dimensions must be positive.");
        }

        if (target.Width <= 0 || target.Height <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(target), "Target dimensions must be positive.");
        }

        if (target.Width > source.Width || target.Height > source.Height)
        {
            throw new ArgumentOutOfRangeException(nameof(target), "The downsampler cannot enlarge a VTF plane.");
        }

        ArgumentNullException.ThrowIfNull(source.Rgba);
        if (source.Rgba.Length != checked(source.Width * source.Height * 4))
        {
            throw new ArgumentException(
                "RGBA buffer length does not match the source dimensions.",
                nameof(source));
        }
    }

    private static byte[] ResizeSrgbPremultiplied(
        UniversalVtfImagePlane source,
        UniversalVtfDimensions target)
    {
        int sourcePixels = checked(source.Width * source.Height);
        var premultipliedLinear = new ushort[checked(sourcePixels * 3)];
        var alphaRgb = new ushort[checked(sourcePixels * 3)];
        for (int pixel = 0; pixel < sourcePixels; pixel++)
        {
            int sourceOffset = pixel * 4;
            double alpha = source.Rgba[sourceOffset + 3] / 255.0;
            ushort alphaQuantum = ToQuantum(alpha);
            int quantumOffset = pixel * 3;
            alphaRgb[quantumOffset] = alphaQuantum;
            alphaRgb[quantumOffset + 1] = alphaQuantum;
            alphaRgb[quantumOffset + 2] = alphaQuantum;
            premultipliedLinear[quantumOffset] =
                ToQuantum(SrgbToLinear(source.Rgba[sourceOffset] / 255.0) * alpha);
            premultipliedLinear[quantumOffset + 1] =
                ToQuantum(SrgbToLinear(source.Rgba[sourceOffset + 1] / 255.0) * alpha);
            premultipliedLinear[quantumOffset + 2] =
                ToQuantum(SrgbToLinear(source.Rgba[sourceOffset + 2] / 255.0) * alpha);
        }

        ushort[] associated = ResizeQuantumRgb(
            premultipliedLinear,
            source.Width,
            source.Height,
            target);
        ushort[] resizedAlpha = ResizeQuantumRgb(
            alphaRgb,
            source.Width,
            source.Height,
            target);

        int outputPixels = checked(target.Width * target.Height);
        var result = new byte[checked(outputPixels * 4)];
        for (int pixel = 0; pixel < outputPixels; pixel++)
        {
            int quantumOffset = pixel * 3;
            int outputOffset = pixel * 4;
            double alpha = resizedAlpha[quantumOffset] / 65535.0;
            if (alpha > 1e-8)
            {
                result[outputOffset] = ToByte(LinearToSrgb(Math.Clamp(
                    associated[quantumOffset] / 65535.0 / alpha,
                    0,
                    1)));
                result[outputOffset + 1] = ToByte(LinearToSrgb(Math.Clamp(
                    associated[quantumOffset + 1] / 65535.0 / alpha,
                    0,
                    1)));
                result[outputOffset + 2] = ToByte(LinearToSrgb(Math.Clamp(
                    associated[quantumOffset + 2] / 65535.0 / alpha,
                    0,
                    1)));
            }

            result[outputOffset + 3] = ToByte(alpha);
        }

        return result;
    }

    private static byte[] ResizeIndependentChannels(
        UniversalVtfImagePlane source,
        UniversalVtfDimensions target)
    {
        int sourcePixels = checked(source.Width * source.Height);
        int outputPixels = checked(target.Width * target.Height);
        var result = new byte[checked(outputPixels * 4)];
        for (int channel = 0; channel < 4; channel++)
        {
            var input = new ushort[checked(sourcePixels * 3)];
            for (int pixel = 0; pixel < sourcePixels; pixel++)
            {
                ushort value = ToQuantum(source.Rgba[pixel * 4 + channel] / 255.0);
                int offset = pixel * 3;
                input[offset] = value;
                input[offset + 1] = value;
                input[offset + 2] = value;
            }

            ushort[] resized = ResizeQuantumRgb(input, source.Width, source.Height, target);
            for (int pixel = 0; pixel < outputPixels; pixel++)
            {
                result[pixel * 4 + channel] = ToByte(resized[pixel * 3] / 65535.0);
            }
        }

        return result;
    }

    private static byte[] ResizeRgbNormal(
        UniversalVtfImagePlane source,
        UniversalVtfDimensions target)
    {
        byte[] alpha = ResizeChannel(source, target, channel: 3);
        return ResizeNormalArea(source, target, dxt5Nm: false, alpha, null, null);
    }

    private static byte[] ResizeDxt5Nm(
        UniversalVtfImagePlane source,
        UniversalVtfDimensions target)
    {
        byte[] red = ResizeChannel(source, target, channel: 0);
        byte[] blue = ResizeChannel(source, target, channel: 2);
        return ResizeNormalArea(source, target, dxt5Nm: true, null, red, blue);
    }

    private static byte[] ResizeNormalArea(
        UniversalVtfImagePlane source,
        UniversalVtfDimensions target,
        bool dxt5Nm,
        byte[]? alpha,
        byte[]? auxiliaryRed,
        byte[]? auxiliaryBlue)
    {
        var output = new byte[checked(target.Width * target.Height * 4)];
        double scaleX = (double)source.Width / target.Width;
        double scaleY = (double)source.Height / target.Height;

        for (int targetY = 0; targetY < target.Height; targetY++)
        {
            double startY = targetY * scaleY;
            double endY = (targetY + 1) * scaleY;
            int firstY = (int)Math.Floor(startY);
            int lastY = (int)Math.Ceiling(endY);
            for (int targetX = 0; targetX < target.Width; targetX++)
            {
                double startX = targetX * scaleX;
                double endX = (targetX + 1) * scaleX;
                int firstX = (int)Math.Floor(startX);
                int lastX = (int)Math.Ceiling(endX);
                double nx = 0;
                double ny = 0;
                double nz = 0;
                double totalWeight = 0;

                for (int sourceY = firstY; sourceY < lastY; sourceY++)
                {
                    double weightY = Math.Max(
                        0,
                        Math.Min(endY, sourceY + 1.0) - Math.Max(startY, sourceY));
                    for (int sourceX = firstX; sourceX < lastX; sourceX++)
                    {
                        double weightX = Math.Max(
                            0,
                            Math.Min(endX, sourceX + 1.0) - Math.Max(startX, sourceX));
                        double weight = weightX * weightY;
                        if (weight <= 0)
                        {
                            continue;
                        }

                        int offset = (sourceY * source.Width + sourceX) * 4;
                        (double x, double y, double z) = DecodeNormal(source.Rgba, offset, dxt5Nm);
                        nx += x * weight;
                        ny += y * weight;
                        nz += z * weight;
                        totalWeight += weight;
                    }
                }

                if (!double.IsFinite(totalWeight) || totalWeight <= 0)
                {
                    throw new InvalidDataException(
                        $"Normal footprint ({targetX},{targetY}) has no source area.");
                }

                nx /= totalWeight;
                ny /= totalWeight;
                nz /= totalWeight;
                double length = Math.Sqrt(nx * nx + ny * ny + nz * nz);
                if (!double.IsFinite(length) || length <= VectorEpsilon)
                {
                    throw new InvalidDataException(
                        $"Normal footprint ({targetX},{targetY}) has a zero-length average.");
                }

                nx /= length;
                ny /= length;
                nz /= length;
                int outputPixel = targetY * target.Width + targetX;
                int outputOffset = outputPixel * 4;
                if (dxt5Nm)
                {
                    output[outputOffset] = auxiliaryRed![outputPixel];
                    output[outputOffset + 1] = EncodeSigned(ny);
                    output[outputOffset + 2] = auxiliaryBlue![outputPixel];
                    output[outputOffset + 3] = EncodeSigned(nx);
                }
                else
                {
                    output[outputOffset] = EncodeSigned(nx);
                    output[outputOffset + 1] = EncodeSigned(ny);
                    output[outputOffset + 2] = EncodeSigned(nz);
                    output[outputOffset + 3] = alpha![outputPixel];
                }
            }
        }

        return output;
    }

    private static (double X, double Y, double Z) DecodeNormal(
        byte[] rgba,
        int offset,
        bool dxt5Nm)
    {
        double x = DecodeSigned(rgba[offset + (dxt5Nm ? 3 : 0)]);
        double y = DecodeSigned(rgba[offset + 1]);
        double z = dxt5Nm
            ? Math.Sqrt(Math.Max(0, 1 - x * x - y * y))
            : DecodeSigned(rgba[offset + 2]);
        if (!double.IsFinite(x) || !double.IsFinite(y) || !double.IsFinite(z))
        {
            throw new InvalidDataException(
                $"Source normal at byte offset {offset} is non-finite.");
        }

        return (x, y, z);
    }

    private static byte[] ResizeChannel(
        UniversalVtfImagePlane source,
        UniversalVtfDimensions target,
        int channel)
    {
        int sourcePixels = checked(source.Width * source.Height);
        var input = new ushort[checked(sourcePixels * 3)];
        for (int pixel = 0; pixel < sourcePixels; pixel++)
        {
            ushort value = ToQuantum(source.Rgba[pixel * 4 + channel] / 255.0);
            int offset = pixel * 3;
            input[offset] = value;
            input[offset + 1] = value;
            input[offset + 2] = value;
        }

        ushort[] resized = ResizeQuantumRgb(input, source.Width, source.Height, target);
        var output = new byte[checked(target.Width * target.Height)];
        for (int pixel = 0; pixel < output.Length; pixel++)
        {
            output[pixel] = ToByte(resized[pixel * 3] / 65535.0);
        }

        return output;
    }

    private static ushort[] ResizeQuantumRgb(
        ushort[] source,
        int sourceWidth,
        int sourceHeight,
        UniversalVtfDimensions target)
    {
        using var image = new MagickImage(
            MagickColors.Black,
            checked((uint)sourceWidth),
            checked((uint)sourceHeight));
        image.ColorSpace = ColorSpace.RGB;
        image.ReadPixels(
            source,
            new PixelReadSettings(
                checked((uint)sourceWidth),
                checked((uint)sourceHeight),
                StorageType.Quantum,
                PixelMapping.RGB));
        image.FilterType = FilterType.LanczosSharp;
        image.Resize(new MagickGeometry(
            checked((uint)target.Width),
            checked((uint)target.Height))
        {
            IgnoreAspectRatio = true
        });

        using IPixelCollection<ushort> pixels = image.GetPixelsUnsafe();
        ushort[] result = pixels.ToShortArray(PixelMapping.RGB) ??
                          throw new InvalidDataException(
                              "Magick.NET did not return resized RGB pixels.");
        int expected = checked(target.Width * target.Height * 3);
        if (result.Length != expected)
        {
            throw new InvalidDataException(
                $"Magick.NET returned {result.Length} samples; expected {expected}.");
        }

        return result;
    }

    private static void PreserveCutoutCoverage(
        byte[] source,
        byte[] output,
        double threshold)
    {
        byte[] sourceAlpha = ExtractAlpha(source);
        byte[] outputAlpha = ExtractAlpha(output);
        double targetCoverage = UniversalVtfCoverageScaler.Coverage(sourceAlpha, threshold);
        UniversalVtfCoverageScale scale = UniversalVtfCoverageScaler.FindScale(
            outputAlpha,
            threshold,
            targetCoverage);
        for (int pixel = 0; pixel < outputAlpha.Length; pixel++)
        {
            output[pixel * 4 + 3] = UniversalVtfCoverageScaler.ApplyScale(
                outputAlpha[pixel],
                scale.Scale);
        }
    }

    private static byte[] ExtractAlpha(byte[] rgba)
    {
        var alpha = new byte[rgba.Length / 4];
        for (int pixel = 0; pixel < alpha.Length; pixel++)
        {
            alpha[pixel] = rgba[pixel * 4 + 3];
        }

        return alpha;
    }

    private static double DecodeSigned(byte value) => value / 127.5 - 1.0;

    private static byte EncodeSigned(double value) => ToByte(value * 0.5 + 0.5);

    private static double SrgbToLinear(double value) =>
        value <= 0.04045
            ? value / 12.92
            : Math.Pow((value + 0.055) / 1.055, 2.4);

    private static double LinearToSrgb(double value) =>
        value <= 0.0031308
            ? value * 12.92
            : 1.055 * Math.Pow(value, 1.0 / 2.4) - 0.055;

    private static byte ToByte(double value) =>
        (byte)Math.Clamp(
            (int)Math.Round(value * 255.0, MidpointRounding.ToEven),
            0,
            255);

    private static ushort ToQuantum(double value) =>
        (ushort)Math.Clamp(
            (int)Math.Round(value * 65535.0, MidpointRounding.ToEven),
            0,
            65535);
}
