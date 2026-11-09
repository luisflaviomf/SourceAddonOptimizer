using System;
using System.Linq;

namespace GmodAddonCompressor.Systems.Optimizer
{
    /// <summary>What the "Otimizar" assistant works on.</summary>
    internal enum WizardGoal
    {
        Full,
        Models,
        Media
    }

    /// <summary>The assistant profiles (Sem perda, Custo-benefício, Máximo, Personalizado).</summary>
    internal enum WizardProfile
    {
        Lossless,
        Balanced,
        Max,
        Custom
    }

    /// <summary>
    /// Settings one profile applies to the shared Models/Mídia options for the length of an assistant run.
    /// Indices refer to the lists in MainWindowContext (Models "Tela" profiles, Mídia "Perfil Tela",
    /// WAV rate/channel/codec/optimization lists).
    /// </summary>
    internal sealed record WizardSettings(
        bool Models,
        int ModelsScreenProfileIndex,
        string ModelsLine,
        bool Textures,
        int CompressScreenProfileIndex,
        string TexturesLine,
        bool IncludeWav,
        int WavOptimizationIndex,
        int WavRateIndex,
        int WavChannelsIndex,
        int WavCodecIndex,
        string SoundsLine,
        bool Lua = false);

    /// <summary>One option of the Personalizado chips.</summary>
    internal sealed record WizardChoice(string Key, int Index, string Label, string Description);

    internal static class WizardProfiles
    {
        /// <summary>Models mode "Tela" (OptimizerModeIndex): no Blender/studiomdl needed.</summary>
        internal const int ModelsScreenModeIndex = 3;

        /// <summary>Mídia e recursos mode "Tela" (CompressModeIndex).</summary>
        internal const int CompressScreenModeIndex = 4;

        internal const string None = "none";

        // Models "Tela" profile indices (MainWindowContext._modelsScreenProfileList)
        internal static readonly WizardChoice[] ModelChoices =
        {
            new(None, -1, "Não mexer", "Os modelos ficam como estão."),
            new("exact", 4, "Sem perda", "Modelos intactos; só o empacotamento que não muda a aparência."),
            new("balanced", 0, "Equilibrado", "Quase idêntico ao original."),
            new("strong", 1, "Forte", "Cerca de 50% menor; pequenas diferenças de perto."),
            new("strong_mirror", 2, "Forte + espelho", "Como o Forte, usando a simetria do modelo. Cerca de 60% menor."),
            new("stream", 3, "Stream", "Maior redução: modelos grandes desenhados por Lua no jogo.")
        };

        // Mídia "Perfil Tela" indices (MainWindowContext._compressScreenProfileList)
        internal static readonly WizardChoice[] TextureChoices =
        {
            new(None, -1, "Não mexer", "As texturas ficam como estão."),
            new("exact", 3, "Sem perda", "Sem reduzir; só a conversão BC3→BC1 que não muda nenhuma cor."),
            new("q50", 0, "Equilibrado", "Reduz só onde a perda é menor que a de um 2x."),
            new("strong", 1, "Forte", "Até 8x onde nunca aparece de perto; textos e texturas pequenas em 2x."),
            new("max", 2, "Máximo", "No mínimo 4x, até 8x. Perda visível de perto."),
            new("stream", 4, "Stream", "Forte + formato compacto decodificado no jogo (cerca de 8x menor). Resolução menor nos primeiros segundos de cada modelo na primeira vez."),
            new("stream_max", 5, "Stream máximo", "Como o Stream, a 31 dB: ainda menor, um pouco de granulado perdido de perto.")
        };

        internal static readonly WizardChoice[] SoundChoices =
        {
            new(None, -1, "Não mexer", "Os sons ficam como estão."),
            new("adaptive", 1, "Adaptativo", "Escolhe por som o menor formato sem piorar. Loops preservados."),
            new("manual", 0, "Manual", "Você escolhe taxa, canais e formato. Loops preservados.")
        };

        internal static readonly string[] WavRates = { "44100 Hz", "22050 Hz", "11025 Hz" };
        internal static readonly string[] WavChannels = { "Estéreo", "Mono" };
        internal static readonly string[] WavCodecs = { "PCM 16-bit", "ADPCM" };

        internal static WizardChoice Find(WizardChoice[] choices, string key) =>
            choices.FirstOrDefault(c => c.Key == key) ?? choices[0];

        internal static WizardSettings For(WizardProfile profile) => profile switch
        {
            // Only changes that look and sound the same in game.
            WizardProfile.Lossless => new WizardSettings(
                true, 4, "Geometria intacta, só empacotamento exato",
                true, 3, "Sem reduzir, só conversão exata BC3→BC1",
                IncludeWav: false, WavOptimizationIndex: 0, WavRateIndex: 0, WavChannelsIndex: 0, WavCodecIndex: 0,
                "Não mexe nos sons"),
            // Smallest size. Textures: the texture stream at 31 dB ("Stream máximo"). Sounds exactly as in
            // Custo-benefício (adaptive conservative, same reference): Luis (2026-11-08) wants that one for every
            // profile, not forced ADPCM.
            WizardProfile.Max => new WizardSettings(
                true, 3, "Stream: modelos grandes desenhados por Lua no jogo",
                true, 5, "Stream máximo: texturas decodificadas no jogo, 31 dB",
                IncludeWav: true, WavOptimizationIndex: 1, WavRateIndex: 1, WavChannelsIndex: 1, WavCodecIndex: 0,
                "Adaptativo conservador"),
            // Models stream + the texture stream at 34 dB (Luis, 2026-11-09: the stream becomes the main method;
            // test bench textures 839 -> 97 MiB). The adaptive WAV profile compares against the manual reference:
            // 22050 Hz mono PCM16 as in his runs (sounds 28.3 -> 9.5 MB); a 44100 Hz stereo reference makes the
            // fallback files bigger than the originals.
            _ => new WizardSettings(
                true, 3, "Stream: modelos grandes desenhados por Lua no jogo",
                true, 4, "Stream: texturas decodificadas no jogo",
                IncludeWav: true, WavOptimizationIndex: 1, WavRateIndex: 1, WavChannelsIndex: 1, WavCodecIndex: 0,
                "Adaptativo conservador")
        };

        /// <summary>Settings of the Personalizado profile from the chips the user picked.</summary>
        internal static WizardSettings ForCustom(string models, string textures, string sounds,
            int wavRateIndex, int wavChannelsIndex, int wavCodecIndex, bool lua)
        {
            var m = Find(ModelChoices, models);
            var t = Find(TextureChoices, textures);
            var s = Find(SoundChoices, sounds);
            int rate = Math.Clamp(wavRateIndex, 0, WavRates.Length - 1);
            int channels = Math.Clamp(wavChannelsIndex, 0, WavChannels.Length - 1);
            int codec = Math.Clamp(wavCodecIndex, 0, WavCodecs.Length - 1);
            string soundsLine = s.Key switch
            {
                None => "Não mexe nos sons",
                "adaptive" => "Adaptativo conservador",
                _ => $"Manual · {WavRates[rate]} · {WavChannels[channels]} · {WavCodecs[codec]}"
            };
            if (lua)
                soundsLine += " · Lua minificado";
            string modelsLine = m.Key == None ? "Não mexe nos modelos" : m.Label;
            string texturesLine = t.Key == None ? "Não mexe nas texturas" : t.Label;
            // adaptive uses the same reference as Custo-benefício (22050 Hz mono PCM16)
            bool adaptive = s.Key == "adaptive";
            return new WizardSettings(
                m.Key != None, Math.Max(0, m.Index), modelsLine,
                t.Key != None, Math.Max(0, t.Index), texturesLine,
                IncludeWav: s.Key != None,
                WavOptimizationIndex: adaptive ? 1 : 0,
                WavRateIndex: adaptive ? 1 : rate,
                WavChannelsIndex: adaptive ? 1 : channels,
                WavCodecIndex: adaptive ? 0 : codec,
                soundsLine,
                lua);
        }

        internal static string Title(WizardProfile profile) => profile switch
        {
            WizardProfile.Lossless => "Sem perda",
            WizardProfile.Max => "Máximo",
            WizardProfile.Custom => "Personalizado",
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
