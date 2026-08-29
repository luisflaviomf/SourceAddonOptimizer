using System.Text.Json;
using System.Text.Json.Serialization;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal enum UniversalVtfCacheCoverage
{
    FullAttempts,
    ImportedExactProfileFinals
}

internal sealed record UniversalVtfArtifactReference(
    string Sha256,
    long Bytes,
    int Width,
    int Height,
    int Depth,
    int Frames,
    int Faces,
    int MipCount,
    int Format)
{
    internal static UniversalVtfArtifactReference FromVtf(string path)
    {
        VtfContainerLayout layout = VtfContainerLayoutReader.Read(Path.GetFullPath(path));
        return new UniversalVtfArtifactReference(
            layout.FileSha256,
            layout.Document.FileLength,
            layout.Document.Width,
            layout.Document.Height,
            layout.Document.Depth,
            layout.Document.Frames,
            layout.Document.Faces,
            layout.Document.MipCount,
            layout.Document.HighResFormat);
    }
}

[JsonConverter(typeof(UniversalVtfCachedArtifactKeyJsonConverter))]
internal readonly record struct UniversalVtfCachedArtifactKey(
    int Level,
    UniversalVtfFilterRoute Route);

internal sealed record UniversalVtfCachedRouteAttempt(
    UniversalVtfFilterRoute Route,
    bool StructurallyValid,
    UniversalVtfMetricSet? Metrics,
    string StructuralReason);

internal sealed record UniversalVtfCachedAttempt(
    int Level,
    bool StructurallyValid,
    UniversalVtfMetricSet? Metrics,
    string StructuralReason,
    UniversalVtfFilterRoute Route,
    IReadOnlyList<UniversalVtfCachedRouteAttempt> RouteAttempts);

internal sealed record UniversalVtfCachedAttemptMatrix(
    string RelativePath,
    IReadOnlyList<UniversalVtfCachedAttempt> Attempts)
{
    internal static UniversalVtfCachedAttemptMatrix FromRuntime(
        UniversalVtfAttemptMatrix matrix)
    {
        ArgumentNullException.ThrowIfNull(matrix);
        var portable = new UniversalVtfCachedAttemptMatrix(
            matrix.RelativePath,
            matrix.Attempts.Select(attempt => new UniversalVtfCachedAttempt(
                attempt.Level,
                attempt.StructurallyValid,
                attempt.Metrics,
                attempt.StructuralReason,
                attempt.Route,
                attempt.RouteAttempts.Select(route => new UniversalVtfCachedRouteAttempt(
                    route.Route,
                    route.StructurallyValid,
                    route.Metrics,
                    route.StructuralReason)).ToArray())).ToArray());
        portable.Validate();
        return portable;
    }

    internal UniversalVtfAttemptMatrix ToRuntime(
        Func<int, UniversalVtfFilterRoute, string> resolveArtifact)
    {
        ArgumentNullException.ThrowIfNull(resolveArtifact);
        Validate();
        return new UniversalVtfAttemptMatrix(
            RelativePath,
            Attempts.Select(attempt => new UniversalVtfAttempt(
                attempt.Level,
                attempt.StructurallyValid,
                attempt.Metrics,
                attempt.StructuralReason,
                attempt.Route,
                Resolve(resolveArtifact, attempt.Level, attempt.Route),
                attempt.RouteAttempts.Select(route => new UniversalVtfRouteAttempt(
                    route.Route,
                    Resolve(resolveArtifact, attempt.Level, route.Route),
                    route.StructurallyValid,
                    route.Metrics,
                    route.StructuralReason)).ToArray())).ToArray());
    }

    internal void Validate()
    {
        UniversalVtfCacheContracts.ValidateRelativePath(RelativePath);
        if (Attempts is null || Attempts.Count == 0)
        {
            throw new InvalidDataException("cache_empty_attempt_matrix");
        }

        for (int index = 0; index < Attempts.Count; index++)
        {
            UniversalVtfCachedAttempt attempt = Attempts[index]
                ?? throw new InvalidDataException("cache_null_attempt");
            if (attempt.Level != index + 1)
            {
                throw new InvalidDataException(
                    $"cache_nonconsecutive_attempt_matrix:{RelativePath}:{attempt.Level}");
            }

            if (!Enum.IsDefined(typeof(UniversalVtfFilterRoute), attempt.Route) ||
                attempt.RouteAttempts is null || attempt.RouteAttempts.Count == 0 ||
                string.IsNullOrWhiteSpace(attempt.StructuralReason))
            {
                throw new InvalidDataException($"cache_invalid_attempt:{RelativePath}:{attempt.Level}");
            }

            var routes = new HashSet<UniversalVtfFilterRoute>();
            foreach (UniversalVtfCachedRouteAttempt route in attempt.RouteAttempts)
            {
                if (route is null ||
                    !Enum.IsDefined(typeof(UniversalVtfFilterRoute), route.Route) ||
                    !routes.Add(route.Route) || string.IsNullOrWhiteSpace(route.StructuralReason))
                {
                    throw new InvalidDataException(
                        $"cache_invalid_route_attempt:{RelativePath}:{attempt.Level}");
                }

                ValidateMetricPresence(
                    route.StructurallyValid,
                    route.Metrics,
                    $"{RelativePath}:{attempt.Level}:{route.Route}");
            }

            UniversalVtfCachedRouteAttempt selected = attempt.RouteAttempts.SingleOrDefault(
                route => route.Route == attempt.Route)
                ?? throw new InvalidDataException(
                    $"cache_selected_route_missing:{RelativePath}:{attempt.Level}:{attempt.Route}");
            ValidateMetricPresence(
                attempt.StructurallyValid,
                attempt.Metrics,
                $"{RelativePath}:{attempt.Level}:selected");
            if (selected.StructurallyValid != attempt.StructurallyValid ||
                attempt.StructurallyValid &&
                (!selected.StructuralReason.Equals(
                     attempt.StructuralReason,
                     StringComparison.Ordinal) ||
                 !MetricSetsEqual(selected.Metrics, attempt.Metrics)))
            {
                throw new InvalidDataException(
                    $"cache_selected_route_drift:{RelativePath}:{attempt.Level}");
            }
        }
    }

    private static string Resolve(
        Func<int, UniversalVtfFilterRoute, string> resolver,
        int level,
        UniversalVtfFilterRoute route)
    {
        string value = resolver(level, route);
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidDataException($"cache_artifact_resolution_empty:{level}:{route}");
        }

        return Path.GetFullPath(value);
    }

    private static void ValidateMetricPresence(
        bool structurallyValid,
        UniversalVtfMetricSet? metrics,
        string context)
    {
        if (structurallyValid != (metrics is not null))
        {
            throw new InvalidDataException($"cache_metric_presence_mismatch:{context}");
        }

        if (metrics is not null)
        {
            UniversalVtfCacheContracts.Validate(metrics, context);
        }
    }

    private static bool MetricSetsEqual(
        UniversalVtfMetricSet? left,
        UniversalVtfMetricSet? right)
    {
        if (left is null || right is null)
        {
            return left is null && right is null;
        }

        return UniversalVtfCanonicalJson.Serialize(left).AsSpan()
            .SequenceEqual(UniversalVtfCanonicalJson.Serialize(right));
    }
}

internal sealed record UniversalVtfAnalysisReceipt(
    string Schema,
    string AnalysisKey,
    UniversalVtfAnalysisIdentity Identity,
    UniversalVtfCacheCoverage Coverage,
    UniversalVtfCachedAttemptMatrix Matrix,
    IReadOnlyDictionary<UniversalVtfCachedArtifactKey, UniversalVtfArtifactReference> Artifacts);

internal sealed record UniversalVtfCompletedProfile(
    UniversalVtfProfileKind Profile,
    UniversalVtfCachedSelection Selection,
    UniversalVtfGateResult? WorstGate,
    UniversalVtfArtifactReference FinalArtifact,
    long SpatialBytes,
    string SpatialSha256,
    bool ByteFloor,
    string? Blocker,
    bool LowResolutionImageRegenerated,
    int ResourceCount);

internal sealed record UniversalVtfCompletionReceipt(
    string Schema,
    string CompletionKey,
    UniversalVtfCompletionIdentity Identity,
    IReadOnlyDictionary<UniversalVtfProfileKind, UniversalVtfCompletedProfile> Profiles);

internal sealed record UniversalVtfCachedAsset(
    UniversalVtfAnalysisReceipt Analysis,
    UniversalVtfCompletionReceipt Completion,
    IReadOnlyDictionary<string, string> ArtifactSources);

internal static class UniversalVtfCacheContracts
{
    internal const string AnalysisReceiptSchema = "universal-vtf-analysis-receipt-v1";
    internal const string CompletionReceiptSchema = "universal-vtf-completion-receipt-v1";

    internal static void Validate(UniversalVtfAnalysisReceipt receipt)
    {
        ArgumentNullException.ThrowIfNull(receipt);
        if (!receipt.Schema.Equals(AnalysisReceiptSchema, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"cache_analysis_receipt_schema:{receipt.Schema}");
        }

        if (!Enum.IsDefined(typeof(UniversalVtfCacheCoverage), receipt.Coverage))
        {
            throw new InvalidDataException($"cache_unsupported_coverage:{receipt.Coverage}");
        }

        string key = UniversalVtfCacheKey.Analysis(receipt.Identity);
        if (!receipt.AnalysisKey.Equals(key, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"cache_analysis_key_mismatch:{receipt.AnalysisKey}!={key}");
        }

        receipt.Matrix.Validate();
        if (!receipt.Matrix.RelativePath.Equals(
                receipt.Identity.RelativePath,
                StringComparison.Ordinal))
        {
            throw new InvalidDataException("cache_analysis_path_mismatch");
        }

        if (receipt.Artifacts is null)
        {
            throw new InvalidDataException("cache_null_analysis_artifacts");
        }

        var available = new HashSet<UniversalVtfCachedArtifactKey>();
        foreach ((UniversalVtfCachedArtifactKey artifactKey, UniversalVtfArtifactReference artifact)
                 in receipt.Artifacts)
        {
            Validate(artifactKey);
            Validate(artifact);
            if (!available.Add(artifactKey) ||
                !receipt.Matrix.Attempts.Any(attempt =>
                    attempt.Level == artifactKey.Level &&
                    attempt.RouteAttempts.Any(route => route.Route == artifactKey.Route)))
            {
                throw new InvalidDataException(
                    $"cache_unmatched_analysis_artifact:{artifactKey.Level}:{artifactKey.Route}");
            }
        }

        if (receipt.Coverage == UniversalVtfCacheCoverage.FullAttempts)
        {
            foreach (UniversalVtfCachedAttempt attempt in receipt.Matrix.Attempts)
            foreach (UniversalVtfCachedRouteAttempt route in attempt.RouteAttempts
                         .Where(route => route.StructurallyValid))
            {
                var required = new UniversalVtfCachedArtifactKey(attempt.Level, route.Route);
                if (!available.Contains(required))
                {
                    throw new InvalidDataException(
                        $"cache_missing_full_attempt_artifact:{attempt.Level}:{route.Route}");
                }
            }
        }
    }

    internal static void Validate(UniversalVtfCompletionReceipt receipt)
    {
        ArgumentNullException.ThrowIfNull(receipt);
        if (!receipt.Schema.Equals(CompletionReceiptSchema, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"cache_completion_receipt_schema:{receipt.Schema}");
        }

        string key = UniversalVtfCacheKey.Completion(receipt.Identity);
        if (!receipt.CompletionKey.Equals(key, StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                $"cache_completion_key_mismatch:{receipt.CompletionKey}!={key}");
        }

        UniversalVtfProfileKind[] profiles = Enum.GetValues<UniversalVtfProfileKind>();
        if (receipt.Profiles is null || receipt.Profiles.Count != profiles.Length)
        {
            throw new InvalidDataException("cache_completion_profiles_incomplete");
        }

        foreach (UniversalVtfProfileKind profile in profiles)
        {
            if (!receipt.Profiles.TryGetValue(profile, out UniversalVtfCompletedProfile? output) ||
                output is null || output.Profile != profile || output.Selection.Profile != profile ||
                !receipt.Identity.Selections.TryGetValue(profile, out UniversalVtfCachedSelection? selected) ||
                output.Selection != selected)
            {
                throw new InvalidDataException($"cache_completion_profile_drift:{profile}");
            }

            Validate(output.FinalArtifact);
            UniversalVtfCacheKey.RequireSha(output.SpatialSha256, nameof(output.SpatialSha256));
            if (output.SpatialBytes <= 0 || output.ResourceCount < 0 ||
                output.Blocker is not null && string.IsNullOrWhiteSpace(output.Blocker))
            {
                throw new InvalidDataException($"cache_completion_profile_invalid:{profile}");
            }

            if (output.WorstGate is not null)
            {
                Validate(output.WorstGate, profile);
            }
        }
    }

    internal static void Validate(UniversalVtfCachedAsset asset)
    {
        ArgumentNullException.ThrowIfNull(asset);
        Validate(asset.Analysis);
        Validate(asset.Completion);
        if (!asset.Completion.Identity.AnalysisKey.Equals(
                asset.Analysis.AnalysisKey,
                StringComparison.Ordinal))
        {
            throw new InvalidDataException("cache_asset_analysis_completion_mismatch");
        }

        if (asset.ArtifactSources is null)
        {
            throw new InvalidDataException("cache_null_artifact_sources");
        }

        HashSet<string> expected = asset.Analysis.Artifacts.Values
            .Select(artifact => artifact.Sha256)
            .Concat(asset.Completion.Profiles.Values.Select(output => output.FinalArtifact.Sha256))
            .ToHashSet(StringComparer.Ordinal);
        if (asset.ArtifactSources.Count != expected.Count)
        {
            throw new InvalidDataException("cache_artifact_source_set_mismatch");
        }

        foreach ((string sha256, string source) in asset.ArtifactSources)
        {
            UniversalVtfCacheKey.RequireSha(sha256, nameof(asset.ArtifactSources));
            if (!expected.Contains(sha256) || string.IsNullOrWhiteSpace(source) ||
                !Path.IsPathRooted(source))
            {
                throw new InvalidDataException($"cache_invalid_artifact_source:{sha256}");
            }
        }
    }

    internal static void Validate(UniversalVtfArtifactReference artifact)
    {
        ArgumentNullException.ThrowIfNull(artifact);
        UniversalVtfCacheKey.RequireSha(artifact.Sha256, nameof(artifact.Sha256));
        if (artifact.Bytes <= 0 || artifact.Width <= 0 || artifact.Height <= 0 ||
            artifact.Depth <= 0 || artifact.Frames <= 0 || artifact.Faces <= 0 ||
            artifact.MipCount <= 0 || artifact.Format is not (13 or 15))
        {
            throw new InvalidDataException($"cache_invalid_artifact:{artifact.Sha256}");
        }
    }

    internal static void Validate(UniversalVtfMetricSet metrics, string context)
    {
        ArgumentNullException.ThrowIfNull(metrics);
        if (metrics.Planes is null || metrics.Planes.Count == 0 ||
            metrics.CrossPlaneValues is null ||
            metrics.CrossPlaneValues.Keys.Any(string.IsNullOrWhiteSpace))
        {
            throw new InvalidDataException($"cache_incomplete_metrics:{context}");
        }

        var planes = new HashSet<UniversalVtfPlaneKey>();
        foreach (UniversalVtfPlaneMetricSet plane in metrics.Planes)
        {
            if (plane is null || plane.Common is null || plane.SemanticValues is null ||
                !planes.Add(plane.Key) || plane.Key.Frame < 0 || plane.Key.Face < 0 ||
                plane.Key.DepthSlice < 0 || plane.SemanticValues.Keys.Any(string.IsNullOrWhiteSpace))
            {
                throw new InvalidDataException($"cache_incomplete_metrics:{context}");
            }
        }
    }

    internal static void ValidateRelativePath(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || Path.IsPathRooted(path) || path.Contains('\\') ||
            path != path.ToLowerInvariant() ||
            path.Split('/').Any(segment => string.IsNullOrEmpty(segment) || segment is "." or ".."))
        {
            throw new InvalidDataException($"cache_invalid_relative_path:{path}");
        }
    }

    private static void Validate(UniversalVtfCachedArtifactKey key)
    {
        if (key.Level < 1 || !Enum.IsDefined(typeof(UniversalVtfFilterRoute), key.Route))
        {
            throw new InvalidDataException($"cache_invalid_artifact_key:{key.Level}:{key.Route}");
        }
    }

    private static void Validate(
        UniversalVtfGateResult gate,
        UniversalVtfProfileKind profile)
    {
        if (string.IsNullOrWhiteSpace(gate.Name) || string.IsNullOrWhiteSpace(gate.Operator) ||
            string.IsNullOrWhiteSpace(gate.Reason) ||
            gate.Plane is { Frame: < 0 } or { Face: < 0 } or { DepthSlice: < 0 })
        {
            throw new InvalidDataException($"cache_invalid_worst_gate:{profile}");
        }
    }
}

internal sealed class UniversalVtfCachedArtifactKeyJsonConverter :
    JsonConverter<UniversalVtfCachedArtifactKey>
{
    public override UniversalVtfCachedArtifactKey Read(
        ref Utf8JsonReader reader,
        Type typeToConvert,
        JsonSerializerOptions options)
    {
        if (reader.TokenType != JsonTokenType.String)
        {
            throw new JsonException("cache_artifact_key_invalid_token");
        }

        return Parse(reader.GetString());
    }

    public override void Write(
        Utf8JsonWriter writer,
        UniversalVtfCachedArtifactKey value,
        JsonSerializerOptions options) => writer.WriteStringValue(Format(value));

    public override UniversalVtfCachedArtifactKey ReadAsPropertyName(
        ref Utf8JsonReader reader,
        Type typeToConvert,
        JsonSerializerOptions options) => Parse(reader.GetString());

    public override void WriteAsPropertyName(
        Utf8JsonWriter writer,
        UniversalVtfCachedArtifactKey value,
        JsonSerializerOptions options) => writer.WritePropertyName(Format(value));

    private static string Format(UniversalVtfCachedArtifactKey value)
    {
        string route = value.Route.ToString();
        return $"{value.Level:D4}:{char.ToLowerInvariant(route[0])}{route[1..]}";
    }

    private static UniversalVtfCachedArtifactKey Parse(string? value)
    {
        if (value is null || value.Length < 7 || value[4] != ':' ||
            !int.TryParse(value.AsSpan(0, 4), out int level) || level < 1 ||
            !Enum.TryParse(value[5..], ignoreCase: true, out UniversalVtfFilterRoute route) ||
            !Enum.IsDefined(typeof(UniversalVtfFilterRoute), route))
        {
            throw new JsonException($"cache_artifact_key_invalid:{value}");
        }

        var result = new UniversalVtfCachedArtifactKey(level, route);
        if (!Format(result).Equals(value, StringComparison.Ordinal))
        {
            throw new JsonException($"cache_artifact_key_noncanonical:{value}");
        }

        return result;
    }
}
