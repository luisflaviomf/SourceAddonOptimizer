using VtfMaximumLab.Inventory;

namespace VtfMaximumLab.Candidates;

internal static class VtfCandidateMatrixBuilder
{
    internal static IReadOnlyList<VtfCandidateSpec> Build(
        VtfInventoryEntry entry,
        IReadOnlyCollection<string> encoders)
    {
        if (encoders.Count == 0)
            throw new ArgumentException("At least one encoder is required.", nameof(encoders));

        bool isNormalMap = VtfInventorySemantics.IsNormalMap(entry);
        if (entry.Frames != 1 || entry.Faces != 1 || entry.Depth != 1 || entry.AlphaClass == VtfAlphaClass.Unknown)
        {
            return new[]
            {
                new VtfCandidateSpec(1, entry.Width, entry.Height, VtfTargetFormat.Preserve,
                    "preserve", true, isNormalMap, false)
            };
        }

        bool requiresAlpha = VtfInventorySemantics.RequiresAlpha(entry);
        bool cutout = requiresAlpha &&
                      (entry.AlphaClass == VtfAlphaClass.Cutout || entry.SemanticProfile.IsCutout);
        bool needsGradualAlpha = requiresAlpha && !cutout;
        VtfTargetFormat format = needsGradualAlpha
            ? VtfTargetFormat.Dxt5
            : cutout
                ? VtfTargetFormat.Dxt1OneBitAlpha
                : VtfTargetFormat.Dxt1;

        var specs = new List<VtfCandidateSpec>();
        foreach (int divisor in new[] { 1, 2, 4 })
        {
            int width = Math.Max(8, entry.Width / divisor);
            int height = Math.Max(8, entry.Height / divisor);
            foreach (string encoder in encoders.OrderBy(value => value, StringComparer.OrdinalIgnoreCase))
            {
                specs.Add(new VtfCandidateSpec(
                    divisor,
                    width,
                    height,
                    format,
                    encoder,
                    format == VtfTargetFormat.Dxt5 || format == VtfTargetFormat.Dxt1OneBitAlpha,
                    isNormalMap,
                    format == VtfTargetFormat.Dxt1OneBitAlpha));
            }
        }

        return specs;
    }
}
