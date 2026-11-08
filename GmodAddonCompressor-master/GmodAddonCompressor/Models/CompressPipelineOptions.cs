using GmodAddonCompressor.Systems.Vtf.Universal;

namespace GmodAddonCompressor.Models
{
    internal enum CompressPipelineMode
    {
        Standard = 0,
        Magick = 1,
        Maximum = 2,
        MagickPlus = 3,
        Screen = 4
    }

    internal sealed class CompressPipelineOptions
    {
        public CompressPipelineMode Mode { get; init; } = CompressPipelineMode.Standard;
        public bool UseLegacyStandardVtfDemo { get; init; }
        public bool UseMagickForCommonVtf { get; init; }
        public bool UseMagickForAggressivePng { get; init; }
        public int MaximumVtfParallelism { get; init; } = 10;
        public UniversalVtfProfileKind MaximumVtfProfile { get; init; } =
            UniversalVtfProfileKind.Balanced;
        // "Padrão" VTF (screen-aware, worker texscreen): profile q50 = Equilibrado, strong = Forte,
        // max = Máximo, exact = Sem perda. The old "Padrao" (Standard) mode is shown as "Clássico".
        public string ScreenProfile { get; init; } = "q50";

        public bool IsMagickMode => Mode == CompressPipelineMode.Magick;
        public bool IsMaximumMode => Mode == CompressPipelineMode.Maximum;
        public bool IsMagickPlusMode => Mode == CompressPipelineMode.MagickPlus;
        public bool IsScreenMode => Mode == CompressPipelineMode.Screen;
        public bool IsMagickFamilyMode => IsMagickMode || IsMagickPlusMode;
        public bool ShouldUseMagickForCommonVtf => IsMagickFamilyMode && UseMagickForCommonVtf;
        public bool ShouldUseMagickForAggressivePng => IsMagickFamilyMode && UseMagickForAggressivePng;
        public string ModeLabel => IsScreenMode ? "Padrão" : IsMaximumMode ? "Maximum" : IsMagickPlusMode ? "Magick+" : IsMagickMode ? "Magick" : "Clássico";

        public string BuildRoutingSummary()
        {
            string pngText = ShouldUseMagickForAggressivePng
                ? "PNG: ImageMagick agressivo (q256), com o Clássico se falhar."
                : "PNG: Clássico.";

            if (IsScreenMode)
                return $"VTF: motor Padrão (por visibilidade na tela), perfil {ScreenProfile}. VTF fora de DXT ou sem mipmaps usam o Clássico. Os outros tipos usam o Clássico.";

            if (IsMaximumMode)
                return $"VTF: lote Maximum (VTF universal, perfil {MaximumVtfProfile}) com checagens de qualidade por nível. Os outros tipos usam o Clássico.";

            if (IsMagickPlusMode)
                return $"VTF: Magick e depois conversão exata BC3→BC1 quando o alpha não é usado. {pngText} JPG, sons e Lua: Clássico.";

            if (!IsMagickMode)
                return "Todos os tipos marcados usam o Clássico.";

            return $"VTF: mesmo caminho do Clássico. {pngText} JPG, sons e Lua: Clássico.";
        }
    }
}
