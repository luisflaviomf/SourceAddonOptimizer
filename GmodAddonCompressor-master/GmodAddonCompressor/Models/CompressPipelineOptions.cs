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
        // Tela mode: leave the files the engine reports as fallback untouched instead of sending them through
        // the Clássico VTF path (the Otimizar assistant uses this so its result does not depend on hidden options).
        public bool KeepScreenFallbacks { get; init; }
        // PNG: lossless recompression in the worker (pnglossless: same pixels, same image size) instead of
        // the reduction of the selected method. Always on in the Otimizar assistant.
        public bool LosslessPng { get; init; }

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
            const string losslessPng = "PNG: sem perda (mesmos pixels, mesmo tamanho de imagem).";
            string pngText = LosslessPng
                ? losslessPng
                : ShouldUseMagickForAggressivePng
                ? "PNG: ImageMagick agressivo (q256), com o Clássico se falhar."
                : "PNG: Clássico.";
            string others = LosslessPng ? $"{losslessPng} Os outros tipos usam o Clássico." : "Os outros tipos usam o Clássico.";

            if (IsScreenMode)
                return $"VTF: motor Padrão (por visibilidade na tela), perfil {ScreenProfile}. VTF fora de DXT ou sem mipmaps usam o Clássico. {others}";

            if (IsMaximumMode)
                return $"VTF: lote Maximum (VTF universal, perfil {MaximumVtfProfile}) com checagens de qualidade por nível. {others}";

            if (IsMagickPlusMode)
                return $"VTF: Magick e depois conversão exata BC3→BC1 quando o alpha não é usado. {pngText} JPG, sons e Lua: Clássico.";

            if (!IsMagickMode)
                return LosslessPng ? $"{losslessPng} Os outros tipos marcados usam o Clássico." : "Todos os tipos marcados usam o Clássico.";

            return $"VTF: mesmo caminho do Clássico. {pngText} JPG, sons e Lua: Clássico.";
        }
    }
}
