using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems.Companion
{
    internal sealed record CompanionMaximumRuntimeSummary(
        bool EvidenceApplied,
        int MatchedModels,
        int MatchedMaterials,
        int MatchedRegions,
        int ScheduledValidations,
        int RegionalRecoveries,
        double AverageProtectionIncrease,
        double MaximumProtectionIncrease,
        string BundleSha256,
        Guid RunId,
        string DisplayText
    );

    internal static class CompanionMaximumReportReader
    {
        private const int MaximumReportBytes = 20_000_000;
        private const int MaximumRegions = 100_000;
        private static readonly Regex Sha256Pattern = new("^[0-9a-f]{64}$", RegexOptions.CultureInvariant);

        public static CompanionMaximumRuntimeSummary Read(string reportPath)
        {
            string fullPath = Path.GetFullPath(reportPath ?? string.Empty);
            if (!File.Exists(fullPath))
                throw new InvalidOperationException("Maximum adaptive report is missing.");
            var info = new FileInfo(fullPath);
            if (info.Length <= 0 || info.Length > MaximumReportBytes)
                throw new InvalidOperationException("Maximum adaptive report exceeds its byte limit.");
            byte[] raw = File.ReadAllBytes(fullPath);
            if (raw.LongLength != info.Length || raw.LongLength > MaximumReportBytes)
                throw new InvalidOperationException("Maximum adaptive report changed while reading.");

            JsonDocument document;
            try
            {
                document = JsonDocument.Parse(raw, new JsonDocumentOptions
                {
                    AllowTrailingCommas = false,
                    CommentHandling = JsonCommentHandling.Disallow,
                    MaxDepth = 16,
                });
            }
            catch (JsonException ex)
            {
                throw new InvalidOperationException("Maximum adaptive report is invalid JSON.", ex);
            }
            using (document)
            {
                JsonElement root = document.RootElement;
                if (root.ValueKind != JsonValueKind.Object)
                    throw new InvalidOperationException("Maximum adaptive report root must be an object.");
                RejectDuplicateKeys(root);
                if (RequiredInteger(root, "schema", 1, 1) != 1)
                    throw new InvalidOperationException("Maximum adaptive report schema is unsupported.");
                if (!root.TryGetProperty("runtime_profile", out JsonElement runtime))
                    return NoEvidence();
                if (runtime.ValueKind != JsonValueKind.Object)
                    throw new InvalidOperationException("Maximum runtime profile report must be an object.");

                string bundleSha = RequiredString(runtime, "bundle_sha256", 64, 64);
                if (!Sha256Pattern.IsMatch(bundleSha))
                    throw new InvalidOperationException("Maximum runtime profile report has an invalid bundle hash.");
                string rawRunId = RequiredString(runtime, "run_id", 36, 36);
                if (!Guid.TryParseExact(rawRunId, "D", out Guid runId))
                    throw new InvalidOperationException("Maximum runtime profile report has an invalid run ID.");
                int matchedModels = RequiredInteger(runtime, "matched_models", 0, CompanionContract.MaxModels);
                int matchedMaterials = RequiredInteger(runtime, "matched_materials", 0, CompanionContract.MaxMaterials);
                int matchedRegions = RequiredInteger(runtime, "matched_regions", 1, MaximumRegions);
                int scheduled = RequiredInteger(runtime, "scheduled_validations", 0, matchedRegions);
                int recoveries = RequiredInteger(runtime, "regional_recoveries", 0, scheduled);
                if (!runtime.TryGetProperty("regions", out JsonElement regions)
                    || regions.ValueKind != JsonValueKind.Array
                    || regions.GetArrayLength() != matchedRegions)
                    throw new InvalidOperationException("Maximum runtime region count is inconsistent.");

                double totalProtection = 0.0;
                double maximumProtection = 0.0;
                int countedScheduled = 0;
                int countedRecoveries = 0;
                foreach (JsonElement region in regions.EnumerateArray())
                {
                    if (region.ValueKind != JsonValueKind.Object)
                        throw new InvalidOperationException("Maximum runtime region must be an object.");
                    RequiredString(region, "key", 1, 256);
                    double geometric = RequiredFinite(region, "geometric_risk", 0.0, 1.0);
                    double effective = RequiredFinite(region, "effective_risk", geometric, 1.0);
                    RequiredFinite(region, "runtime_importance", 0.0, 1.0);
                    bool regionScheduled = RequiredBoolean(region, "scheduled_validation");
                    bool recovered = RequiredBoolean(region, "regional_recovery");
                    if (recovered && !regionScheduled)
                        throw new InvalidOperationException("Maximum runtime recovery lacks a scheduled validation.");
                    countedScheduled += regionScheduled ? 1 : 0;
                    countedRecoveries += recovered ? 1 : 0;
                    double increase = effective - geometric;
                    totalProtection += increase;
                    maximumProtection = Math.Max(maximumProtection, increase);
                    if (!region.TryGetProperty("reasons", out JsonElement reasons)
                        || reasons.ValueKind != JsonValueKind.Array
                        || reasons.GetArrayLength() > 32)
                        throw new InvalidOperationException("Maximum runtime region reasons are invalid.");
                    foreach (JsonElement reason in reasons.EnumerateArray())
                    {
                        if (reason.ValueKind != JsonValueKind.String || (reason.GetString()?.Length ?? 0) > 512)
                            throw new InvalidOperationException("Maximum runtime region reason is invalid.");
                    }
                }
                if (countedScheduled != scheduled || countedRecoveries != recoveries)
                    throw new InvalidOperationException("Maximum runtime validation/recovery counts are inconsistent.");

                double averageProtection = totalProtection / matchedRegions;
                string display = string.Format(
                    CultureInfo.InvariantCulture,
                    "Runtime evidence applied: {0} models, {1} materials, {2} regions; protection +{3:0.0} points average / +{4:0.0} max; {5} added validations, {6} local recoveries.",
                    matchedModels,
                    matchedMaterials,
                    matchedRegions,
                    averageProtection * 100.0,
                    maximumProtection * 100.0,
                    scheduled,
                    recoveries
                );
                return new CompanionMaximumRuntimeSummary(
                    true,
                    matchedModels,
                    matchedMaterials,
                    matchedRegions,
                    scheduled,
                    recoveries,
                    averageProtection,
                    maximumProtection,
                    bundleSha,
                    runId,
                    display
                );
            }
        }

        private static CompanionMaximumRuntimeSummary NoEvidence() => new(
            false,
            0,
            0,
            0,
            0,
            0,
            0.0,
            0.0,
            string.Empty,
            Guid.Empty,
            "Runtime evidence was not applied in this Maximum v2 run."
        );

        private static void RejectDuplicateKeys(JsonElement value)
        {
            if (value.ValueKind == JsonValueKind.Object)
            {
                var names = new HashSet<string>(StringComparer.Ordinal);
                foreach (JsonProperty property in value.EnumerateObject())
                {
                    if (!names.Add(property.Name))
                        throw new InvalidOperationException("Maximum adaptive report contains duplicate JSON keys.");
                    RejectDuplicateKeys(property.Value);
                }
            }
            else if (value.ValueKind == JsonValueKind.Array)
            {
                foreach (JsonElement item in value.EnumerateArray())
                    RejectDuplicateKeys(item);
            }
        }

        private static string RequiredString(JsonElement parent, string name, int minimum, int maximum)
        {
            if (!parent.TryGetProperty(name, out JsonElement value) || value.ValueKind != JsonValueKind.String)
                throw new InvalidOperationException($"Maximum adaptive report field {name} must be a string.");
            string result = value.GetString() ?? string.Empty;
            if (result.Length < minimum || result.Length > maximum)
                throw new InvalidOperationException($"Maximum adaptive report field {name} is out of bounds.");
            return result;
        }

        private static int RequiredInteger(JsonElement parent, string name, int minimum, int maximum)
        {
            if (!parent.TryGetProperty(name, out JsonElement value)
                || value.ValueKind != JsonValueKind.Number
                || !value.TryGetInt32(out int result)
                || result < minimum
                || result > maximum)
                throw new InvalidOperationException($"Maximum adaptive report field {name} must be a bounded integer.");
            return result;
        }

        private static double RequiredFinite(JsonElement parent, string name, double minimum, double maximum)
        {
            if (!parent.TryGetProperty(name, out JsonElement value)
                || value.ValueKind != JsonValueKind.Number
                || !value.TryGetDouble(out double result)
                || !double.IsFinite(result)
                || result < minimum
                || result > maximum)
                throw new InvalidOperationException($"Maximum adaptive report field {name} must be finite and bounded.");
            return result;
        }

        private static bool RequiredBoolean(JsonElement parent, string name)
        {
            if (!parent.TryGetProperty(name, out JsonElement value)
                || value.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                throw new InvalidOperationException($"Maximum adaptive report field {name} must be boolean.");
            return value.GetBoolean();
        }
    }
}
