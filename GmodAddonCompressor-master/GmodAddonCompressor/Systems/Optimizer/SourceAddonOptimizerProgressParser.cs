using System;
using System.Collections.Generic;
using System.Text.Json;
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
        internal string? MaximumKind { get; init; }
        internal string? FamilyId { get; init; }
        internal int? FamilyIndex { get; init; }
        internal int? FamilyTotal { get; init; }
        internal string? CandidateId { get; init; }
        internal int? CandidateIndex { get; init; }
        internal int? CandidateTotal { get; init; }
        internal string? Stage { get; init; }
        internal long? BestBytes { get; init; }
        internal double? ReductionPercent { get; init; }
        internal string? GateStatus { get; init; }
        internal string? ReportPath { get; init; }
    }

    internal sealed class SourceAddonOptimizerProgressParser
    {
        private const string MaximumPrefix = "MAXIMUM_EVENT ";
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

            // Maximum is a machine-readable protocol and must be handled before
            // the legacy human-readable regexes below.
            if (line.StartsWith(MaximumPrefix, StringComparison.Ordinal))
                return ParseMaximum(line.Substring(MaximumPrefix.Length));

            var match = _step.Match(line);
            if (match.Success)
            {
                if (!int.TryParse(match.Groups[1].Value, out var stepIndex)
                    || !int.TryParse(match.Groups[2].Value, out var stepTotal))
                    return null;
                return new SourceAddonOptimizerProgressUpdate
                {
                    StepIndex = stepIndex,
                    StepTotal = stepTotal,
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
                if (!int.TryParse(match.Groups[1].Value, out var batchAddonIndex)
                    || !int.TryParse(match.Groups[2].Value, out var batchAddonTotal))
                    return null;
                return new SourceAddonOptimizerProgressUpdate
                {
                    BatchAddonIndex = batchAddonIndex,
                    BatchAddonTotal = batchAddonTotal,
                    BatchAddonName = match.Groups[3].Value
                };
            }

            match = _itemCompleted.Match(line);
            if (match.Success)
            {
                if (!int.TryParse(match.Groups[1].Value, out var completedItemIndex)
                    || !int.TryParse(match.Groups[2].Value, out var completedItemTotal))
                    return null;
                return new SourceAddonOptimizerProgressUpdate
                {
                    ItemIndex = completedItemIndex,
                    ItemTotal = completedItemTotal,
                    ItemType = match.Groups[3].Value,
                    ItemPath = match.Groups[4].Value,
                    IsItemCompletion = true
                };
            }

            match = _item.Match(line);
            if (match.Success)
            {
                if (!int.TryParse(match.Groups[1].Value, out var itemIndex)
                    || !int.TryParse(match.Groups[2].Value, out var itemTotal))
                    return null;
                return new SourceAddonOptimizerProgressUpdate
                {
                    ItemIndex = itemIndex,
                    ItemTotal = itemTotal,
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

        private static SourceAddonOptimizerProgressUpdate? ParseMaximum(string payload)
        {
            try
            {
                using var document = JsonDocument.Parse(
                    payload,
                    new JsonDocumentOptions
                    {
                        AllowTrailingCommas = false,
                        CommentHandling = JsonCommentHandling.Disallow,
                        MaxDepth = 16
                    });
                var root = document.RootElement;
                if (root.ValueKind != JsonValueKind.Object || HasDuplicateProperties(root))
                    return null;

                if (!root.TryGetProperty("schema", out var schema)
                    || schema.ValueKind != JsonValueKind.Number
                    || !IsIntegerToken(schema)
                    || !schema.TryGetInt32(out var schemaNumber)
                    || schemaNumber != 1)
                    return null;

                if (!root.TryGetProperty("kind", out var kindElement)
                    || kindElement.ValueKind != JsonValueKind.String)
                    return null;
                var kind = kindElement.GetString();
                if (string.IsNullOrWhiteSpace(kind))
                    return null;

                if (!TryReadNullableString(root, "family_id", out var familyId)
                    || !TryReadNullableInt32(root, "family_index", out var familyIndex)
                    || !TryReadNullableInt32(root, "family_total", out var familyTotal)
                    || !TryReadNullableString(root, "candidate_id", out var candidateId)
                    || !TryReadNullableInt32(root, "candidate_index", out var candidateIndex)
                    || !TryReadNullableInt32(root, "candidate_total", out var candidateTotal)
                    || !TryReadNullableString(root, "stage", out var stage)
                    || !TryReadNullableInt64(root, "best_bytes", out var bestBytes)
                    || !TryReadNullableDouble(root, "reduction_percent", out var reductionPercent)
                    || !TryReadNullableString(root, "gate_status", out var gateStatus)
                    || !TryReadNullableString(root, "report_path", out var reportPath))
                    return null;

                if (!HasValidCounterPair(familyIndex, familyTotal)
                    || !HasValidCounterPair(candidateIndex, candidateTotal)
                    || bestBytes < 0
                    || reductionPercent > 100)
                    return null;

                return new SourceAddonOptimizerProgressUpdate
                {
                    MaximumKind = kind,
                    FamilyId = familyId,
                    FamilyIndex = familyIndex,
                    FamilyTotal = familyTotal,
                    CandidateId = candidateId,
                    CandidateIndex = candidateIndex,
                    CandidateTotal = candidateTotal,
                    Stage = stage,
                    BestBytes = bestBytes,
                    ReductionPercent = reductionPercent,
                    GateStatus = gateStatus,
                    ReportPath = reportPath
                };
            }
            catch (JsonException)
            {
                return null;
            }
            catch (InvalidOperationException)
            {
                return null;
            }
            catch (FormatException)
            {
                return null;
            }
            catch (OverflowException)
            {
                return null;
            }
        }

        private static bool HasDuplicateProperties(JsonElement root)
        {
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (var property in root.EnumerateObject())
            {
                if (!names.Add(property.Name))
                    return true;
            }
            return false;
        }

        private static bool TryReadNullableString(JsonElement root, string name, out string? value)
        {
            value = null;
            if (!root.TryGetProperty(name, out var element) || element.ValueKind == JsonValueKind.Null)
                return true;
            if (element.ValueKind != JsonValueKind.String)
                return false;
            value = element.GetString();
            return true;
        }

        private static bool TryReadNullableInt32(JsonElement root, string name, out int? value)
        {
            value = null;
            if (!root.TryGetProperty(name, out var element) || element.ValueKind == JsonValueKind.Null)
                return true;
            if (element.ValueKind != JsonValueKind.Number
                || !IsIntegerToken(element)
                || !element.TryGetInt32(out var parsed))
                return false;
            value = parsed;
            return true;
        }

        private static bool TryReadNullableInt64(JsonElement root, string name, out long? value)
        {
            value = null;
            if (!root.TryGetProperty(name, out var element) || element.ValueKind == JsonValueKind.Null)
                return true;
            if (element.ValueKind != JsonValueKind.Number
                || !IsIntegerToken(element)
                || !element.TryGetInt64(out var parsed))
                return false;
            value = parsed;
            return true;
        }

        private static bool TryReadNullableDouble(JsonElement root, string name, out double? value)
        {
            value = null;
            if (!root.TryGetProperty(name, out var element) || element.ValueKind == JsonValueKind.Null)
                return true;
            if (element.ValueKind != JsonValueKind.Number
                || !element.TryGetDouble(out var parsed)
                || double.IsNaN(parsed)
                || double.IsInfinity(parsed))
                return false;
            value = parsed;
            return true;
        }

        private static bool IsIntegerToken(JsonElement element)
        {
            var raw = element.GetRawText();
            return raw.IndexOf('.') < 0
                && raw.IndexOf('e') < 0
                && raw.IndexOf('E') < 0;
        }

        private static bool HasValidCounterPair(int? index, int? total)
        {
            if (index < 0 || total < 0)
                return false;
            if (index.HasValue && total.HasValue)
                return total.Value > 0 && index.Value < total.Value;
            return true;
        }
    }
}
