using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;

namespace VtfMaximumLab.Images;

internal sealed record DecodedVtfImage(byte[] Rgba, int Width, int Height, VtfDocumentInfo Document);

internal static class DecodedVtfImageLoader
{
    internal static DecodedVtfImage LoadTopFace(string path)
    {
        if (!VtfDocumentReader.TryRead(path, out VtfDocumentInfo document, out string error))
            throw new InvalidDataException($"Invalid VTF '{path}': {error}");
        if (!VtfHighResImageDecoder.TryDecodeHighResSliceRgba(path, document, 0, 0, 0, out byte[] rgba))
            throw new InvalidDataException($"Unsupported or undecodable VTF '{path}'.");
        return new DecodedVtfImage(rgba, document.Width, document.Height, document);
    }
}
