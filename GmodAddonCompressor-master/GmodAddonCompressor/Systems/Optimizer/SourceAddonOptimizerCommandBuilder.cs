using System;
using System.Collections.Generic;
using System.Globalization;

namespace GmodAddonCompressor.Systems.Optimizer
{
    internal static class SourceAddonOptimizerCommandBuilder
    {
        internal static IReadOnlyList<string> BuildArguments(SourceAddonOptimizerRunOptions options)
        {
            if (string.Equals(options.OptimizerMode?.Trim(), "screen", StringComparison.OrdinalIgnoreCase))
                return BuildScreenArguments(options);

            var arguments = new List<string>
            {
                options.AddonPath,
                "--work",
                options.WorkDir
            };

            AddValue(arguments, "--suffix", options.Suffix);
            AddValue(arguments, "--optimizer-mode", options.OptimizerMode);
            bool maximumMode = string.Equals(options.OptimizerMode?.Trim(), "maximum", StringComparison.OrdinalIgnoreCase);
            if (maximumMode)
                AddValue(arguments, "--maximum-runtime-profile", options.MaximumRuntimeProfilePath);
            AddValue(arguments, "--blender", options.BlenderPath);
            AddValue(arguments, "--studiomdl", options.StudioMdlPath);
            AddNumber(arguments, "--ratio", options.Ratio);
            AddNumber(arguments, "--merge", options.Merge);
            AddNumber(arguments, "--autosmooth", options.AutoSmooth);

            if (options.UsePlanar)
            {
                arguments.Add("--use-planar");
                AddNumber(arguments, "--planar-angle", options.PlanarAngle);
            }

            bool normalMode = string.Equals(options.OptimizerMode?.Trim(), "normal", StringComparison.OrdinalIgnoreCase);
            AddFlag(arguments, "--experimental-ground-policy", normalMode && options.ExperimentalGroundPolicy);
            AddFlag(arguments, "--experimental-round-parts-policy", normalMode && options.ExperimentalRoundPartsPolicy);
            AddFlag(arguments, "--experimental-steer-turn-basis-fix", normalMode && options.ExperimentalSteerTurnBasisFix);
            AddFlag(arguments, "--vtx-tristrips", options.UseVtxTriStrips);
            AddValue(arguments, "--format", options.Format);
            AddInteger(arguments, "--jobs", options.Jobs);
            AddInteger(arguments, "--decompile-jobs", options.DecompileJobs);
            AddInteger(arguments, "--compile-jobs", options.CompileJobs);
            AddFlag(arguments, "--strict", options.Strict);
            AddFlag(arguments, "--resume-opt", options.ResumeOpt);
            AddFlag(arguments, "--overwrite", options.Overwrite);
            AddFlag(arguments, "--overwrite-work", options.OverwriteWork);
            AddFlag(arguments, "--no-restore-skins", !options.RestoreSkins);
            AddFlag(arguments, "--compile-verbose", options.CompileVerbose);
            AddFlag(arguments, "--cleanup-work-model-artifacts", options.CleanupWorkModelArtifacts);
            AddFlag(arguments, "--single-addon-only", options.SingleAddonOnly);
            return arguments;
        }

        // Models "Tela" mode: worker command modscreen (direct binary simplification, no Blender/studiomdl).
        private static IReadOnlyList<string> BuildScreenArguments(SourceAddonOptimizerRunOptions options)
        {
            var arguments = new List<string> { "modscreen", options.AddonPath, "--work", options.WorkDir };
            AddValue(arguments, "--suffix", options.Suffix);
            // Forte + espelho ("strong_mirror") is the default; "balanced" (Equilibrado), "strong"
            // (Forte), "stream" (Lua-drawn geometry) and "exact" (Sem perda: packaging only)
            // are only used when asked for explicitly.
            string requested = options.ScreenProfile?.Trim().ToLowerInvariant() ?? "";
            string profile = requested is "balanced" or "strong" or "stream" or "exact" ? requested : "strong_mirror";
            arguments.Add("--profile");
            arguments.Add(profile);
            AddInteger(arguments, "--jobs", options.Jobs);
            AddFlag(arguments, "--overwrite", options.Overwrite);
            return arguments;
        }

        private static void AddValue(List<string> arguments, string name, string? value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return;
            arguments.Add(name);
            arguments.Add(value);
        }

        private static void AddNumber(List<string> arguments, string name, double? value)
        {
            if (!value.HasValue)
                return;
            arguments.Add(name);
            arguments.Add(value.Value.ToString(CultureInfo.InvariantCulture));
        }

        private static void AddInteger(List<string> arguments, string name, int? value)
        {
            if (!value.HasValue)
                return;
            arguments.Add(name);
            arguments.Add(value.Value.ToString(CultureInfo.InvariantCulture));
        }

        private static void AddFlag(List<string> arguments, string name, bool enabled)
        {
            if (enabled)
                arguments.Add(name);
        }
    }
}
