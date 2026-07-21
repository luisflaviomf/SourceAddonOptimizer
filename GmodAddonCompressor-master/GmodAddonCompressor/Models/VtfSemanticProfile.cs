using System.Collections.Generic;

namespace GmodAddonCompressor.Models
{
    internal sealed record VtfSemanticProfile(
        string TextureKey,
        bool UsesBaseTextureAlpha,
        bool IsNormalMap,
        bool UsesNormalAlpha,
        bool IsTranslucent,
        bool IsCutout,
        bool HasAlphaToCoverage,
        bool IsVehicleGlass,
        bool UsesBaseTextureAsPhongMask,
        bool UsesBaseTextureAsEnvMapMask,
        bool HasPhong,
        bool IsEmissive,
        bool IsDecal,
        bool IsEffect,
        double AlphaTestReference,
        IReadOnlyList<string> Evidence);
}
