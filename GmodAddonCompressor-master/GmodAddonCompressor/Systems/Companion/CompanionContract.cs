using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace GmodAddonCompressor.Systems.Companion
{
    internal static class CompanionContract
    {
        public const int ContractMajor = 1;
        public const int ContractMinor = 0;
        public const int MaxChunkBytes = 2_000_000;
        public const int MaxCompletionBytes = 4_096;
        public const int MaxIndexBytes = 262_144;
        public const int MaxBundleBytes = 20_000_000;
        public const int MaxJsonDepth = 12;
        public const int MaxTotalKeys = 100_000;
        public const int MaxModels = 4_096;
        public const int MaxMaterials = 16_384;
        public const int MaxResultResources = 5_000;
        public const int MaxScreenshots = 1_000;
        public const int MaxScreenshotBytes = 8_000_000;
        public const int MaxPathBytes = 512;
        public const int MaxPathComponentBytes = 128;
        public const string RuntimeProfileSchema = "sao-runtime-profile-v1";

        private static readonly IReadOnlyDictionary<string, string[]> Extensions =
            new Dictionary<string, string[]>(StringComparer.Ordinal)
            {
                ["models"] = new[] { ".ani", ".mdl", ".phy", ".vtx", ".vvd" },
                ["materials"] = new[] { ".vmt", ".vtf" },
                ["sound"] = new[] { ".mp3", ".ogg", ".wav" },
                ["lua"] = new[] { ".lua" },
                ["maps"] = new[] { ".bsp" },
            };

        public static string NormalizeVirtualPath(string? value)
        {
            if (string.IsNullOrEmpty(value) || value != value.Trim() || value.IndexOf('\0') >= 0)
                throw new InvalidOperationException("Invalid virtual path.");
            if (value.Any(character => character > 0x7f))
                throw new InvalidOperationException("Invalid virtual path: non-ASCII characters are not supported in v1.");

            string normalized = value.Replace('\\', '/').ToLowerInvariant();
            if (normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.Contains("//", StringComparison.Ordinal)
                || normalized.Contains(':')
                || normalized.Length > MaxPathBytes)
                throw new InvalidOperationException("Invalid virtual path.");

            string[] components = normalized.Split('/');
            if (components.Length < 2
                || components.Any(component => component.Length == 0 || component is "." or "..")
                || components.Any(component => component.Length > MaxPathComponentBytes))
                throw new InvalidOperationException("Invalid virtual path component.");
            if (!Extensions.TryGetValue(components[0], out string[]? allowed)
                || !allowed.Contains(Path.GetExtension(normalized), StringComparer.Ordinal))
                throw new InvalidOperationException("Invalid virtual path extension or root.");
            return normalized;
        }

        public static CompanionEvidenceTier ParseEvidence(string? value) => value switch
        {
            "registered" => CompanionEvidenceTier.Registered,
            "instantiated" => CompanionEvidenceTier.Instantiated,
            "visible" => CompanionEvidenceTier.Visible,
            "repeated_visible" => CompanionEvidenceTier.RepeatedVisible,
            "focused" => CompanionEvidenceTier.Focused,
            _ => throw new InvalidOperationException($"Invalid evidence: {value}"),
        };
    }

    internal enum CompanionEvidenceTier
    {
        Registered = 1,
        Instantiated = 2,
        Visible = 3,
        RepeatedVisible = 4,
        Focused = 5,
    }

    internal sealed record CompanionBundleInspection(
        string Root,
        string Schema,
        int ContractMajor,
        int ContractMinor,
        Guid RunId,
        DateTimeOffset CreatedUtc,
        string ProducerName,
        string ProducerVersion,
        bool IsComplete,
        bool IsCompatible,
        string IndexSha256
    );

    internal sealed class CompanionObservedModel
    {
        public CompanionObservedModel(string canonicalPath, string displayPath, CompanionEvidenceTier evidence)
        {
            CanonicalPath = canonicalPath;
            DisplayPath = displayPath;
            Evidence = evidence;
        }

        public string CanonicalPath { get; }
        public string DisplayPath { get; set; }
        public CompanionEvidenceTier Evidence { get; set; }
        public HashSet<int> Skins { get; } = new();
        public Dictionary<string, CompanionEvidenceTier> Bodygroups { get; } = new(StringComparer.Ordinal);
        public Dictionary<string, CompanionEvidenceTier> Submaterials { get; } = new(StringComparer.Ordinal);
        public HashSet<string> Materials { get; } = new(StringComparer.Ordinal);
    }

    internal sealed class CompanionObservedMaterial
    {
        public CompanionObservedMaterial(string canonicalPath, string displayPath, CompanionEvidenceTier evidence)
        {
            CanonicalPath = canonicalPath;
            DisplayPath = displayPath;
            Evidence = evidence;
        }

        public string CanonicalPath { get; }
        public string DisplayPath { get; set; }
        public CompanionEvidenceTier Evidence { get; set; }
    }

    internal sealed class CompanionRuntimeProfile
    {
        public CompanionRuntimeProfile(
            string originPath,
            Guid runId,
            DateTimeOffset createdUtc,
            string producerName,
            string producerVersion,
            Dictionary<string, CompanionObservedModel> models,
            Dictionary<string, CompanionObservedMaterial> materials,
            long truncatedModels,
            long truncatedMaterials,
            long truncatedLuaErrors,
            long truncatedBytes
        )
        {
            OriginPath = originPath;
            RunId = runId;
            CreatedUtc = createdUtc;
            ProducerName = producerName;
            ProducerVersion = producerVersion;
            Models = models;
            Materials = materials;
            TruncatedModels = truncatedModels;
            TruncatedMaterials = truncatedMaterials;
            TruncatedLuaErrors = truncatedLuaErrors;
            TruncatedBytes = truncatedBytes;
        }

        public string OriginPath { get; }
        public Guid RunId { get; }
        public DateTimeOffset CreatedUtc { get; }
        public string ProducerName { get; }
        public string ProducerVersion { get; }
        public Dictionary<string, CompanionObservedModel> Models { get; }
        public Dictionary<string, CompanionObservedMaterial> Materials { get; }
        public long TruncatedModels { get; }
        public long TruncatedMaterials { get; }
        public long TruncatedLuaErrors { get; }
        public long TruncatedBytes { get; }
    }

    internal sealed record CompanionProfileImportResult(
        CompanionRuntimeProfile Profile,
        int SourceProfileCount,
        int ObservedModelCount,
        int ObservedMaterialCount,
        int MatchedModelCount,
        int MatchedMaterialCount,
        int IgnoredExternalAssetCount,
        bool IsStale,
        bool IsEligible,
        string OriginText,
        string AgeText,
        string TruncationText,
        string CompatibilityText
    )
    {
        public int MatchedAssetCount => MatchedModelCount + MatchedMaterialCount;
    }
}
