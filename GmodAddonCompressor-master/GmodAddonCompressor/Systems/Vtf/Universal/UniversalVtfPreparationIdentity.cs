namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfStructuralIdentity(
    int MajorVersion,
    int MinorVersion,
    int HeaderSize,
    int Flags,
    int Width,
    int Height,
    int Depth,
    int Frames,
    int FirstFrame,
    int Faces,
    int MipCount,
    int HighResFormat,
    int LowResFormat,
    int LowResWidth,
    int LowResHeight,
    bool IsNomip,
    string ResourceFingerprint,
    string SubresourceFingerprint,
    string UnmappedFingerprint);

internal sealed record UniversalVtfAnalysisIdentity(
    string Schema,
    string AlgorithmFingerprint,
    string RelativePath,
    string RawSha256,
    long RawBytes,
    UniversalVtfStructuralIdentity Structure,
    string ConsumerProofSha256,
    IReadOnlyList<string> SemanticClasses,
    UniversalVtfFilterRoute SemanticRoute,
    string PolicyFingerprint,
    string FixtureSha256,
    string ToolchainSha256,
    string BcBridgeSha256,
    string BridgeRowSha256,
    string CandidateParametersFingerprint);

internal sealed record UniversalVtfCachedSelection(
    UniversalVtfProfileKind Profile,
    int Level,
    UniversalVtfFilterRoute Route);

internal sealed record UniversalVtfCompletionIdentity(
    string Schema,
    string AnalysisKey,
    string SafeSha256,
    long SafeBytes,
    string RuntimeCapsSha256,
    IReadOnlyDictionary<UniversalVtfProfileKind, UniversalVtfCachedSelection> Selections);

internal static class UniversalVtfPreparationFingerprint
{
    internal static string Current { get; } = Fingerprint(new
    {
        schema = "universal-vtf-preparation-algorithm-v1",
        materializer = "universal-vtf-candidate-materializer-v1",
        downsampler = "universal-vtf-downsampler-v1",
        decoder = "vtf-high-res-image-decoder-v1",
        metrics = "universal-vtf-metric-calculator-v1",
        selector = "universal-vtf-selector-v1",
        thumbnail = "universal-vtf-thumbnail-regeneration-v1",
        containerWriter = "vtf-container-writer-v1"
    });

    internal static string CandidateParameters { get; } = Fingerprint(new
    {
        schema = "universal-vtf-candidate-parameters-v1",
        mandatoryFloor = 1,
        authored = "promote-authored-mip-v1",
        generated = "raw-mip0-domain-aware-v1",
        compositeRoutes = new[] { "srgbColor", "numericLinear" },
        generatedFilter = "LanczosSharp",
        comparisonFilter = "bilinear-domain-aware-v1",
        outputFormats = new[] { 13, 15 },
        lowResolutionImage = "regenerate-from-selected-top-v1"
    });

    private static string Fingerprint<T>(T value) =>
        UniversalVtfCanonicalJson.Sha256(UniversalVtfCanonicalJson.Serialize(value));
}

internal static class UniversalVtfCacheKey
{
    internal const string AnalysisSchema = "universal-vtf-analysis-identity-v1";
    internal const string CompletionSchema = "universal-vtf-completion-identity-v1";
    internal const string None = "none";
    private static readonly IReadOnlySet<string> CanonicalSemanticClasses =
        Enum.GetValues<UniversalVtfSemanticClass>()
            .Select(value =>
            {
                string name = value.ToString();
                return char.ToLowerInvariant(name[0]) + name[1..];
            })
            .ToHashSet(StringComparer.Ordinal);

    internal static string Analysis(UniversalVtfAnalysisIdentity identity)
    {
        ArgumentNullException.ThrowIfNull(identity);
        if (!identity.Schema.Equals(AnalysisSchema, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"cache_identity_analysis_schema:{identity.Schema}");
        }

        ValidatePath(identity.RelativePath);
        RequireSha(identity.AlgorithmFingerprint, nameof(identity.AlgorithmFingerprint));
        RequireSha(identity.RawSha256, nameof(identity.RawSha256));
        RequireSha(identity.ConsumerProofSha256, nameof(identity.ConsumerProofSha256));
        RequireSha(identity.PolicyFingerprint, nameof(identity.PolicyFingerprint));
        RequireSha(identity.FixtureSha256, nameof(identity.FixtureSha256));
        RequireSha(identity.ToolchainSha256, nameof(identity.ToolchainSha256));
        RequireSha(identity.BcBridgeSha256, nameof(identity.BcBridgeSha256));
        RequireShaOrNone(identity.BridgeRowSha256, nameof(identity.BridgeRowSha256));
        RequireSha(
            identity.CandidateParametersFingerprint,
            nameof(identity.CandidateParametersFingerprint));
        ValidateStructure(identity.Structure);
        if (identity.RawBytes <= 0 || identity.SemanticClasses is null ||
            identity.SemanticClasses.Count == 0 ||
            !identity.SemanticClasses.SequenceEqual(
                identity.SemanticClasses.OrderBy(value => value, StringComparer.Ordinal),
                StringComparer.Ordinal) ||
            identity.SemanticClasses.Distinct(StringComparer.Ordinal).Count() !=
            identity.SemanticClasses.Count ||
            identity.SemanticClasses.Any(value =>
                string.IsNullOrWhiteSpace(value) ||
                !CanonicalSemanticClasses.Contains(value)) ||
            !Enum.IsDefined(typeof(UniversalVtfFilterRoute), identity.SemanticRoute))
        {
            throw new InvalidDataException(
                $"cache_identity_invalid_analysis:{identity.RelativePath}");
        }

        return UniversalVtfCanonicalJson.Sha256(
            UniversalVtfCanonicalJson.Serialize(identity));
    }

    internal static string Completion(UniversalVtfCompletionIdentity identity)
    {
        ArgumentNullException.ThrowIfNull(identity);
        if (!identity.Schema.Equals(CompletionSchema, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"cache_identity_completion_schema:{identity.Schema}");
        }

        RequireSha(identity.AnalysisKey, nameof(identity.AnalysisKey));
        RequireSha(identity.SafeSha256, nameof(identity.SafeSha256));
        RequireShaOrNone(identity.RuntimeCapsSha256, nameof(identity.RuntimeCapsSha256));
        UniversalVtfProfileKind[] profiles = Enum.GetValues<UniversalVtfProfileKind>();
        if (identity.SafeBytes <= 0 || identity.Selections is null ||
            identity.Selections.Count != profiles.Length ||
            profiles.Any(profile =>
                !identity.Selections.TryGetValue(profile, out UniversalVtfCachedSelection? value) ||
                value.Profile != profile || value.Level < 1 ||
                !Enum.IsDefined(typeof(UniversalVtfFilterRoute), value.Route)))
        {
            throw new InvalidDataException("cache_identity_invalid_completion");
        }

        int conservative = identity.Selections[UniversalVtfProfileKind.Conservative].Level;
        int balanced = identity.Selections[UniversalVtfProfileKind.Balanced].Level;
        int aggressive = identity.Selections[UniversalVtfProfileKind.Aggressive].Level;
        if (!(conservative <= balanced && balanced <= aggressive))
        {
            throw new InvalidDataException("cache_identity_non_nested_completion");
        }

        return UniversalVtfCanonicalJson.Sha256(
            UniversalVtfCanonicalJson.Serialize(identity));
    }

    internal static void RequireSha(string value, string field)
    {
        if (value is null || value.Length != 64 ||
            value.Any(character =>
                !(character is >= '0' and <= '9' or >= 'a' and <= 'f')))
        {
            throw new InvalidDataException($"cache_identity_invalid_sha256:{field}");
        }
    }

    private static void RequireShaOrNone(string value, string field)
    {
        if (value.Equals(None, StringComparison.Ordinal))
        {
            return;
        }

        RequireSha(value, field);
    }

    private static void ValidatePath(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || Path.IsPathRooted(path) || path.Contains('\\') ||
            path != path.ToLowerInvariant() ||
            path.Split('/').Any(segment => string.IsNullOrEmpty(segment) || segment is "." or ".."))
        {
            throw new InvalidDataException($"cache_identity_invalid_path:{path}");
        }
    }

    private static void ValidateStructure(UniversalVtfStructuralIdentity structure)
    {
        ArgumentNullException.ThrowIfNull(structure);
        RequireSha(structure.ResourceFingerprint, nameof(structure.ResourceFingerprint));
        RequireSha(structure.SubresourceFingerprint, nameof(structure.SubresourceFingerprint));
        RequireSha(structure.UnmappedFingerprint, nameof(structure.UnmappedFingerprint));
        if (structure.MajorVersion != 7 || structure.MinorVersion < 0 ||
            structure.HeaderSize <= 0 || structure.Width <= 0 || structure.Height <= 0 ||
            structure.Depth <= 0 || structure.Frames <= 0 || structure.FirstFrame < 0 ||
            structure.Faces <= 0 || structure.MipCount <= 0 ||
            structure.HighResFormat is not (13 or 15) ||
            structure.LowResWidth < 0 || structure.LowResHeight < 0)
        {
            throw new InvalidDataException("cache_identity_invalid_structure");
        }
    }
}
