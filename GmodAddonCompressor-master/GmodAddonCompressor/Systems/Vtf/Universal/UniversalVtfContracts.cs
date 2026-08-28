namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal enum UniversalVtfProfileKind
{
    Conservative,
    Balanced,
    Aggressive
}

[Flags]
internal enum UniversalVtfSemanticClass
{
    Unknown = 1,
    Diffuse = 2,
    Detail = 4,
    Normal = 8,
    ScalarMask = 16,
    Cutout = 32,
    Glass = 64,
    Emissive = 128,
    Text = 256,
    Cubemap = 512,
    Animated = 1024,
    Ssbump = 2048
}

internal enum UniversalVtfFilterRoute
{
    SrgbColor,
    NumericLinear,
    RgbNormal,
    Dxt5NmAg,
    SsbumpScalars,
    Composite
}

internal static class VtfFlags
{
    internal const int NoMip = 0x00000100;
}

internal readonly record struct UniversalVtfDimensions(int Width, int Height);
