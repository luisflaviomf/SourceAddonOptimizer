using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;

namespace VtfMaximumLab.Metrics;

internal static class VtfCutoutMipCoverageCalculator
{
    internal static double ComputeMaxError(string referencePath, string candidatePath, double alphaThreshold)
    {
        if (!VtfDocumentReader.TryRead(referencePath, out VtfDocumentInfo reference, out string referenceError))
            throw new InvalidDataException($"Cutout mip reference is invalid: {referenceError}");
        if (!VtfDocumentReader.TryRead(candidatePath, out VtfDocumentInfo candidate, out string candidateError))
            throw new InvalidDataException($"Cutout mip candidate is invalid: {candidateError}");

        double maximum = 0;
        foreach (VtfMipLevelInfo candidateMip in candidate.MipLevels)
        {
            VtfMipLevelInfo? referenceMip = reference.MipLevels.FirstOrDefault(mip =>
                mip.Width == candidateMip.Width && mip.Height == candidateMip.Height);
            if (referenceMip == null)
                throw new InvalidDataException($"No reference mip matches {candidateMip.Width}x{candidateMip.Height}.");
            if (!VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                    referencePath, reference, referenceMip.Level, 0, 0, 0, out byte[] referenceRgba) ||
                !VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                    candidatePath, candidate, candidateMip.Level, 0, 0, 0, out byte[] candidateRgba))
            {
                throw new InvalidDataException("A cutout mip could not be decoded.");
            }

            maximum = Math.Max(maximum, ComputeCoverageError(referenceRgba, candidateRgba, alphaThreshold));
        }
        return maximum;
    }

    internal static double ComputeCoverageError(byte[] referenceRgba, byte[] candidateRgba, double alphaThreshold)
    {
        if (referenceRgba.Length != candidateRgba.Length || referenceRgba.Length == 0 || referenceRgba.Length % 4 != 0)
            throw new ArgumentException("Cutout mip RGBA buffers must have the same non-zero size.");
        int referenceCovered = 0;
        int candidateCovered = 0;
        double threshold = alphaThreshold * 255.0;
        for (int offset = 3; offset < referenceRgba.Length; offset += 4)
        {
            if (referenceRgba[offset] >= threshold)
                referenceCovered++;
            if (candidateRgba[offset] >= threshold)
                candidateCovered++;
        }
        int pixelCount = referenceRgba.Length / 4;
        return Math.Abs((double)referenceCovered / pixelCount - (double)candidateCovered / pixelCount);
    }
}
