using System.Collections.Generic;
using System.Globalization;

namespace GmodAddonCompressor.Systems.Optimizer
{
    internal static class SourceAddonOptimizerCommandBuilder
    {
        internal static IReadOnlyList<string> BuildArguments(SourceAddonOptimizerRunOptions options)
        {
            var arguments = new List<string>
            {
                options.AddonPath,
                "--work",
                options.WorkDir
            };

            AddValue(arguments, "--suffix", options.Suffix);
            AddValue(arguments, "--optimizer-mode", options.OptimizerMode);
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

            AddFlag(arguments, "--experimental-ground-policy", options.ExperimentalGroundPolicy);
            AddFlag(arguments, "--experimental-round-parts-policy", options.ExperimentalRoundPartsPolicy);
            AddFlag(arguments, "--experimental-steer-turn-basis-fix", options.ExperimentalSteerTurnBasisFix);
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
