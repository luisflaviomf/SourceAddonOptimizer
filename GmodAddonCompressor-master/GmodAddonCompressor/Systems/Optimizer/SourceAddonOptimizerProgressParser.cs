using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems.Optimizer
{
    internal sealed class SourceAddonOptimizerProgressUpdate
    {
        internal int? StepIndex { get; init; }
        internal int? StepTotal { get; init; }
        internal string? Phase { get; init; }
        internal int? ItemIndex { get; init; }
        internal int? ItemTotal { get; init; }
        internal string? ItemType { get; init; }
        internal string? ItemPath { get; init; }
        internal string? OutputAddonPath { get; init; }
        internal string? WorkDirPath { get; init; }
        internal int? BatchAddonIndex { get; init; }
        internal int? BatchAddonTotal { get; init; }
        internal string? BatchAddonName { get; init; }
        internal bool IsPackaging { get; init; }
        internal bool IsFinalize { get; init; }
        internal bool IsItemCompletion { get; init; }
    }

    internal sealed class SourceAddonOptimizerProgressParser
    {
        private readonly Regex _step = new Regex(@"^== Step (\d+)/(\d+): (.+) ==$", RegexOptions.Compiled);
        private readonly Regex _item = new Regex(@"^=== \((\d+)/(\d+)\) (MDL|QC):\s+(.+)$", RegexOptions.Compiled);
        private readonly Regex _itemCompleted = new Regex(@"^>>> DONE \((\d+)/(\d+)\) (MDL|QC):\s+(.+)$", RegexOptions.Compiled);
        private readonly Regex _packaging = new Regex(@"^== Packaging: (.+) ==$", RegexOptions.Compiled);
        private readonly Regex _finalize = new Regex(@"^== Finalize: (.+) ==$", RegexOptions.Compiled);
        private readonly Regex _batchAddon = new Regex(@"^== Batch addon (\d+)/(\d+): (.+) ==$", RegexOptions.Compiled);
        private readonly Regex _output = new Regex(@"^Output addon:\s+(.+)$", RegexOptions.Compiled);
        private readonly Regex _workDir = new Regex(@"^Work dir:\s+(.+)$", RegexOptions.Compiled);

        internal SourceAddonOptimizerProgressUpdate? Parse(string line)
        {
            if (string.IsNullOrWhiteSpace(line))
                return null;

            var match = _step.Match(line);
            if (match.Success)
            {
                return new SourceAddonOptimizerProgressUpdate
                {
                    StepIndex = int.Parse(match.Groups[1].Value),
                    StepTotal = int.Parse(match.Groups[2].Value),
                    Phase = match.Groups[3].Value
                };
            }

            match = _packaging.Match(line);
            if (match.Success)
            {
                return new SourceAddonOptimizerProgressUpdate
                {
                    IsPackaging = true,
                    Phase = match.Groups[1].Value
                };
            }

            match = _finalize.Match(line);
            if (match.Success)
            {
                return new SourceAddonOptimizerProgressUpdate
                {
                    IsFinalize = true,
                    Phase = match.Groups[1].Value
                };
            }

            match = _batchAddon.Match(line);
            if (match.Success)
            {
                return new SourceAddonOptimizerProgressUpdate
                {
                    BatchAddonIndex = int.Parse(match.Groups[1].Value),
                    BatchAddonTotal = int.Parse(match.Groups[2].Value),
                    BatchAddonName = match.Groups[3].Value
                };
            }

            match = _itemCompleted.Match(line);
            if (match.Success)
            {
                return new SourceAddonOptimizerProgressUpdate
                {
                    ItemIndex = int.Parse(match.Groups[1].Value),
                    ItemTotal = int.Parse(match.Groups[2].Value),
                    ItemType = match.Groups[3].Value,
                    ItemPath = match.Groups[4].Value,
                    IsItemCompletion = true
                };
            }

            match = _item.Match(line);
            if (match.Success)
            {
                return new SourceAddonOptimizerProgressUpdate
                {
                    ItemIndex = int.Parse(match.Groups[1].Value),
                    ItemTotal = int.Parse(match.Groups[2].Value),
                    ItemType = match.Groups[3].Value,
                    ItemPath = match.Groups[4].Value
                };
            }

            match = _output.Match(line);
            if (match.Success)
            {
                return new SourceAddonOptimizerProgressUpdate
                {
                    OutputAddonPath = match.Groups[1].Value
                };
            }

            match = _workDir.Match(line);
            if (match.Success)
            {
                return new SourceAddonOptimizerProgressUpdate
                {
                    WorkDirPath = match.Groups[1].Value
                };
            }

            return null;
        }
    }
}
