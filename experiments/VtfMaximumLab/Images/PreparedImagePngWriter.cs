using ImageMagick;

namespace VtfMaximumLab.Images;

internal static class PreparedImagePngWriter
{
    internal static void Write(PreparedImage image, string path)
    {
        var settings = new MagickReadSettings
        {
            Width = checked((uint)image.Width),
            Height = checked((uint)image.Height),
            Format = MagickFormat.Rgba,
            Depth = 8
        };
        using var magickImage = new MagickImage(image.Rgba, settings);
        magickImage.Format = MagickFormat.Png32;
        magickImage.Depth = 8;
        magickImage.Strip();
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        magickImage.Write(path);
    }
}
