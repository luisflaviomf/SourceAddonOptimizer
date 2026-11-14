using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Models;

namespace GmodAddonCompressor.Systems.Maps
{
    /// <summary>
    /// The three "Quanto reduzir" choices of the Mapas tab. Every one rebuilds each .bsp in the engine's own
    /// compressed format (LZMA lumps and pakfile files: the same map once the game reads it) and leaves out the
    /// model mesh files the PC game never reads; they differ in what happens to the textures and sounds, inside
    /// the .bsp and in the addon folder alike.
    /// Textures use the screen-aware engine (worker texscreen) with the assistant's profiles. The texture stream
    /// of the addon profiles is not used here: on a map everything shows the moment it loads, so the whole
    /// world would start at the stream's low resolution. The textures a map draws at their own pixel size
    /// (the $basetexture of world materials, infodecal, env_sprite) keep it in every profile: the engine
    /// divides the world's texture coordinates by it when the map loads (texlab/size_bound.py).
    /// </summary>
    internal sealed record MapProfile(
        string Name,
        string ScreenProfile,
        bool AdaptiveWav,
        string FpsLevel,
        string Description)
    {
        internal CompressPipelineOptions PipelineOptions() => new()
        {
            Mode = CompressPipelineMode.Screen,
            ScreenProfile = ScreenProfile,
            // files the engine does not handle (no mipmaps, not DXT) stay as they are, whatever the
            // Clássico options of the advanced tabs say
            KeepScreenFallbacks = true,
            LosslessPng = true
        };

        // reference of the adaptive WAV profile: 22050 Hz mono PCM16, as in the assistant (a 44100 Hz stereo
        // reference makes the fallback files bigger than the originals)
        internal const int WavReferenceRate = 22050;
        internal const int WavReferenceChannels = 1;
    }

    internal static class MapProfiles
    {
        internal static readonly MapProfile[] All =
        {
            new("Sem perda", "exact", false, "none",
                "O mesmo mapa no jogo: .bsp comprimido no formato do próprio jogo, conversão exata BC3→BC1, PNG sem perda. Sons intactos."),
            new("Custo-benefício", "strong", true, "balanced",
                "Texturas dos modelos reduzidas onde a perda é pequena, sons no Adaptativo conservador e o .bsp comprimido. Mais FPS: holofotes sem a luz dinâmica (o facho continua) e brilhos de lâmpada (sprites) desenhados de um jeito mais leve. Paredes, chão, céu, cubemaps, decalques e sprites ficam no tamanho original."),
            new("Máximo", "max", true, "max",
                "Texturas dos modelos no mínimo 4x menores (perda visível de perto), sons no Adaptativo conservador e o .bsp comprimido. Mais FPS: holofotes sem a luz dinâmica (o facho continua) e brilhos de lâmpada (sprites) desenhados de um jeito mais leve. Paredes, chão, céu, cubemaps, decalques e sprites ficam no tamanho original.")
        };

        internal static MapProfile At(int index) => All[index < 0 || index >= All.Length ? 1 : index];

        internal static AudioContext.WavOptimizationKind WavOptimization(MapProfile profile) =>
            profile.AdaptiveWav ? AudioContext.WavOptimizationKind.AdaptiveConservative : AudioContext.WavOptimizationKind.Manual;
    }
}
