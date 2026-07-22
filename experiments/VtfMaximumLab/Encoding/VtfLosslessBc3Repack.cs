using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;
using VtfMaximumLab.Inventory;
using VtfMaximumLab.Validation;

namespace VtfMaximumLab.Encoding;

internal static class VtfLosslessBc3Repack
{
    internal static bool CanRepack(VtfInventoryEntry entry)
    {
        return entry.Format == 15 &&
               entry.Frames == 1 &&
               entry.Faces == 1 &&
               entry.Depth == 1 &&
               entry.AlphaClass is VtfAlphaClass.Cutout or VtfAlphaClass.Gradual &&
               !VtfInventorySemantics.IsNormalMap(entry) &&
               !VtfInventorySemantics.RequiresAlpha(entry);
    }

    internal static bool TryEstimateOutputBytes(string sourceVtfPath, out long bytes)
    {
        bytes = 0;
        if (!VtfDocumentReader.TryRead(sourceVtfPath, out VtfDocumentInfo source, out _) ||
            source.HighResFormat != 15 ||
            source.Frames != 1 ||
            source.Faces != 1 ||
            source.Depth != 1)
        {
            return false;
        }

        try
        {
            long bc3PayloadBytes = source.MipLevels.Sum(mip => (long)mip.ByteCount);
            bytes = checked(source.HighResDataOffset + bc3PayloadBytes / 2);
            return bytes > 0;
        }
        catch (OverflowException)
        {
            bytes = 0;
            return false;
        }
    }

    internal static void Build(string sourceVtfPath, string outputVtfPath)
    {
        DdsBcDocument sourcePayload = VtfBcPayloadExtractor.Extract(sourceVtfPath);
        DdsBcDocument losslessColorPayload = Bc3ColorBlockTranscoder.Transcode(sourcePayload);
        VtfBcFileBuilder.Build(
            sourceVtfPath,
            losslessColorPayload,
            outputVtfPath,
            oneBitAlpha: false,
            preserveAlpha: false);

        if (!VtfDocumentReader.TryRead(sourceVtfPath, out VtfDocumentInfo source, out string sourceError))
            throw new InvalidDataException($"Source VTF validation failed: {sourceError}");
        if (!VtfDocumentReader.TryRead(outputVtfPath, out VtfDocumentInfo output, out string outputError))
            throw new InvalidDataException($"Repacked VTF validation failed: {outputError}");

        IReadOnlyList<string> structuralErrors = VtfMaximumStructuralValidator.ValidatePair(source, output);
        if (structuralErrors.Count > 0)
            throw new InvalidDataException($"Repacked VTF changed structure: {string.Join(',', structuralErrors)}");

        for (int mip = 0; mip < source.MipCount; mip++)
        {
            if (!VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                    sourceVtfPath, source, mip, 0, 0, 0, out byte[] sourceRgba) ||
                !VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                    outputVtfPath, output, mip, 0, 0, 0, out byte[] outputRgba) ||
                !RgbChannelsEqual(sourceRgba, outputRgba))
            {
                throw new InvalidDataException($"Repacked VTF changed decoded RGB at mip {mip}.");
            }
        }
    }

    private static bool RgbChannelsEqual(byte[] left, byte[] right)
    {
        if (left.Length != right.Length || left.Length % 4 != 0)
            return false;

        for (int offset = 0; offset < left.Length; offset += 4)
        {
            if (left[offset] != right[offset] ||
                left[offset + 1] != right[offset + 1] ||
                left[offset + 2] != right[offset + 2])
            {
                return false;
            }
        }
        return true;
    }
}
