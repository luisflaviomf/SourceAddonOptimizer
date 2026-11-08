namespace GmodAddonCompressor.Systems.Optimizer
{
    /// <summary>What the "Otimizar" assistant works on.</summary>
    internal enum WizardGoal
    {
        Full,
        Models,
        Media
    }

    /// <summary>The three assistant profiles (Sem perda, Custo-benefício, Máximo).</summary>
    internal enum WizardProfile
    {
        Lossless,
        Balanced,
        Max
    }

    /// <summary>
    /// Settings one profile applies to the shared Models/Mídia options for the length of an assistant run.
    /// Indices refer to the lists in MainWindowContext (Models "Tela" profiles, Mídia "Perfil Tela",
    /// WAV rate/channel/codec/optimization lists).
    /// </summary>
    internal sealed record WizardSettings(
        int ModelsScreenProfileIndex,
        string ModelsLine,
        int CompressScreenProfileIndex,
        string TexturesLine,
        bool IncludeWav,
        int WavOptimizationIndex,
        int WavRateIndex,
        int WavChannelsIndex,
        int WavCodecIndex,
        string SoundsLine);

    internal static class WizardProfiles
    {
        /// <summary>Models mode "Tela" (OptimizerModeIndex): no Blender/studiomdl needed.</summary>
        internal const int ModelsScreenModeIndex = 3;

        /// <summary>Mídia e recursos mode "Tela" (CompressModeIndex).</summary>
        internal const int CompressScreenModeIndex = 4;

        // Models "Tela" profile indices (MainWindowContext._modelsScreenProfileList)
        private const int ModelsStream = 3;
        private const int ModelsExact = 4;

        // Mídia "Perfil Tela" indices (MainWindowContext._compressScreenProfileList)
        private const int TexturesStrong = 1;
        private const int TexturesMax = 2;
        private const int TexturesExact = 3;

        internal static WizardSettings For(WizardProfile profile) => profile switch
        {
            // Only changes that look and sound the same in game.
            WizardProfile.Lossless => new WizardSettings(
                ModelsExact, "Geometria intacta, só empacotamento exato",
                TexturesExact, "Sem reduzir, só conversão exata BC3→BC1",
                IncludeWav: false, WavOptimizationIndex: 0, WavRateIndex: 0, WavChannelsIndex: 0, WavCodecIndex: 0,
                "Não mexe nos sons"),
            // Smallest size, visible loss up close accepted.
            WizardProfile.Max => new WizardSettings(
                ModelsStream, "Stream (experimental)",
                TexturesMax, "Máximo: no mínimo 4x, até 8x",
                IncludeWav: true, WavOptimizationIndex: 0, WavRateIndex: 1, WavChannelsIndex: 1, WavCodecIndex: 1,
                "22050 Hz mono ADPCM, loops preservados"),
            // The LVS ~132 MiB configuration Luis tested in game (2026-11-08). The adaptive WAV profile
            // compares against the manual reference: 22050 Hz mono PCM16 as in his runs (sounds 28.3 -> 9.5 MB);
            // a 44100 Hz stereo reference makes the fallback files bigger than the originals.
            _ => new WizardSettings(
                ModelsStream, "Stream (experimental)",
                TexturesStrong, "Tela Forte (textos e texturas pequenas em 2x)",
                IncludeWav: true, WavOptimizationIndex: 1, WavRateIndex: 1, WavChannelsIndex: 1, WavCodecIndex: 0,
                "Adaptativo conservador")
        };

        internal static string Title(WizardProfile profile) => profile switch
        {
            WizardProfile.Lossless => "Sem perda",
            WizardProfile.Max => "Máximo",
            _ => "Custo-benefício"
        };

        internal static string GoalTitle(WizardGoal goal) => goal switch
        {
            WizardGoal.Models => "Só modelos",
            WizardGoal.Media => "Só texturas e sons",
            _ => "Addon completo"
        };
    }
}
