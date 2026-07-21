using GmodAddonCompressor.Models;

namespace VtfMaximumLab.Inventory;

internal static class VtfInventorySemantics
{
    private const int NormalTextureFlag = 0x00000080;

    internal static bool IsNormalMap(VtfInventoryEntry entry) =>
        entry.SemanticProfile.IsNormalMap ||
        (entry.Flags & NormalTextureFlag) != 0 ||
        HasConservativeNormalSuffix(entry.RelativePath);

    internal static bool IsNormalMap(string relativePath, int flags, bool vmtIdentified) =>
        vmtIdentified ||
        (flags & NormalTextureFlag) != 0 ||
        HasConservativeNormalSuffix(relativePath);

    internal static bool RequiresAlpha(VtfInventoryEntry entry)
    {
        if (entry.AlphaClass == VtfAlphaClass.Opaque)
            return false;

        VtfSemanticProfile profile = entry.SemanticProfile;
        bool semanticUse = profile.UsesBaseTextureAlpha ||
                           profile.UsesNormalAlpha ||
                           profile.IsTranslucent ||
                           profile.IsCutout ||
                           profile.HasAlphaToCoverage ||
                           profile.IsVehicleGlass ||
                           profile.UsesBaseTextureAsPhongMask ||
                           profile.UsesBaseTextureAsEnvMapMask ||
                           profile.IsEffect;
        if (semanticUse)
            return true;

        // Pixel alpha alone is not proof of shader use. It is removable only when at
        // least one related VMT was parsed and none of the addon-wide VMT/Lua/PCF
        // signals above consumes it. An unreferenced texture remains conservative.
        return entry.RelatedVmtPaths.Count == 0;
    }

    internal static bool IsDxt5NormalMap(VtfInventoryEntry entry, byte[] rgba)
    {
        if (!IsNormalMap(entry) || entry.SemanticProfile.UsesNormalAlpha ||
            entry.Format != 15 || rgba.Length == 0 || rgba.Length % 4 != 0)
            return false;

        double redDeviation = StandardDeviation(rgba, 0);
        double blueDeviation = StandardDeviation(rgba, 2);
        double alphaDeviation = StandardDeviation(rgba, 3);
        return redDeviation < 3 && blueDeviation < 3 && alphaDeviation > 10;
    }

    private static double StandardDeviation(byte[] rgba, int channel)
    {
        int count = rgba.Length / 4;
        double mean = 0;
        for (int offset = channel; offset < rgba.Length; offset += 4)
            mean += rgba[offset];
        mean /= count;
        double sum = 0;
        for (int offset = channel; offset < rgba.Length; offset += 4)
        {
            double delta = rgba[offset] - mean;
            sum += delta * delta;
        }
        return Math.Sqrt(sum / count);
    }

    private static bool HasConservativeNormalSuffix(string relativePath)
    {
        string stem = Path.GetFileNameWithoutExtension(relativePath);
        return stem.EndsWith("_nm", StringComparison.OrdinalIgnoreCase) ||
               stem.EndsWith("_normal", StringComparison.OrdinalIgnoreCase) ||
               stem.EndsWith("_normalmap", StringComparison.OrdinalIgnoreCase) ||
               stem.EndsWith("_bump", StringComparison.OrdinalIgnoreCase);
    }
}
