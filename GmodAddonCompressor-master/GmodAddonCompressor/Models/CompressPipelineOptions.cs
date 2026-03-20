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
            if (!IsMagickMode)
            {
                return UseLegacyStandardVtfDemo
                    ? "Routing: Standard for all selected types. Legacy Standard VTF demo is enabled."
                    : "Routing: Standard for all selected types.";
            }

            string vtfText = ShouldUseMagickForCommonVtf
                ? "Common VTF => Magick first, then Standard fallback for special/problematic VTF or no-gain cases."
                : "VTF => Standard.";
            string pngText = ShouldUseMagickForAggressivePng
                ? "PNG => Magick q256 first, then Standard fallback on failure or no gain."
                : "PNG => Standard.";

            return $"{vtfText} {pngText} JPG/JPEG, WAV, MP3, OGG and LUA => Standard.";
        }
    }
}
