namespace GmodAddonCompressor.Models
{
    internal enum CompressPipelineMode
    {
        Standard = 0,
        Magick = 1
    }

    internal sealed class CompressPipelineOptions
    {
        public CompressPipelineMode Mode { get; init; } = CompressPipelineMode.Standard;
        public bool UseLegacyStandardVtfDemo { get; init; }
        public bool UseMagickForCommonVtf { get; init; }
        public bool UseMagickForAggressivePng { get; init; }

        public bool IsMagickMode => Mode == CompressPipelineMode.Magick;
        public bool ShouldUseMagickForCommonVtf => IsMagickMode && UseMagickForCommonVtf;
        public bool ShouldUseMagickForAggressivePng => IsMagickMode && UseMagickForAggressivePng;
        public string ModeLabel => IsMagickMode ? "Magick" : "Standard";

        public string BuildRoutingSummary()
        {
            const string vtfText = "VTF => unified pipeline: raw split first, export split fallback when needed, selective DXT routing plus premultiplied alpha-aware resize for FX-sensitive and opacity-alpha surfaces such as vehicle glass/cutouts, then preserve unchanged on no gain or out-of-scope cases.";

            if (!IsMagickMode)
                return $"Routing: Standard for all selected types. {vtfText}";

            string pngText = ShouldUseMagickForAggressivePng
                ? "PNG => Magick q256 first, then Standard fallback on failure or no gain."
                : "PNG => Standard.";

            return $"{vtfText} {pngText} JPG/JPEG, WAV, MP3, OGG and LUA => Standard.";
        }
    }
}
