using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using GmodAddonCompressor.Systems.Vtf;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfCandidateArtifact(
    int Level,
    string RawSourcePath,
    string CandidatePath,
    UniversalVtfFilterRoute Route);

internal sealed record UniversalVtfRouteAttempt(
    UniversalVtfFilterRoute Route,
    string CandidatePath,
    bool StructurallyValid,
    UniversalVtfMetricSet? Metrics,
    string StructuralReason);

internal sealed record UniversalVtfAttempt(
    int Level,
    bool StructurallyValid,
    UniversalVtfMetricSet? Metrics,
    string StructuralReason,
    UniversalVtfFilterRoute Route,
    string CandidatePath,
    IReadOnlyList<UniversalVtfRouteAttempt> RouteAttempts);

internal sealed record UniversalVtfAttemptMatrix(
    string RelativePath,
    IReadOnlyList<UniversalVtfAttempt> Attempts);

internal sealed record UniversalVtfProfileAttempt(
    int Level,
    bool WasConsidered,
    UniversalVtfEvaluation? Evaluation,
    string Reason);

internal sealed record UniversalVtfSelectedLevel(
    int Level,
    string Reason,
    UniversalVtfFilterRoute Route,
    string CandidatePath);

internal sealed record UniversalVtfProfileSelection(
    UniversalVtfSelectedLevel Conservative,
    UniversalVtfSelectedLevel Balanced,
    UniversalVtfSelectedLevel Aggressive,
    IReadOnlyDictionary<UniversalVtfProfileKind, IReadOnlyList<UniversalVtfProfileAttempt>> Attempts);

internal sealed record UniversalVtfRuntimeCap(
    string RelativePath,
    UniversalVtfProfileKind Profile,
    int MaximumLevel,
    string Evidence);

internal sealed record UniversalVtfRuntimeCapsFrozen(
    string Path,
    string Sha256,
    UniversalVtfRuntimeCaps Caps);

internal sealed record UniversalVtfRuntimeCaps(IReadOnlyList<UniversalVtfRuntimeCap> Caps)
{
    internal const string Schema = "universal-vtf-runtime-caps-v1";

    private static readonly JsonSerializerOptions JsonOptions = CreateJsonOptions();

    internal static UniversalVtfRuntimeCaps Empty { get; } =
        new(Array.Empty<UniversalVtfRuntimeCap>());

    internal static UniversalVtfRuntimeCaps FromVerifiedEntries(
        IEnumerable<UniversalVtfRuntimeCap> entries)
    {
        ArgumentNullException.ThrowIfNull(entries);
        UniversalVtfRuntimeCap[] caps = entries
            .OrderBy(cap => cap.RelativePath, StringComparer.Ordinal)
            .ThenBy(cap => cap.Profile)
            .ToArray();
        Validate(caps);
        return new UniversalVtfRuntimeCaps(caps);
    }

    internal static UniversalVtfRuntimeCapsFrozen WriteCanonical(
        string path,
        IEnumerable<UniversalVtfRuntimeCap> entries)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new ArgumentException("Runtime caps path cannot be empty.", nameof(path));
        }

        UniversalVtfRuntimeCaps caps = FromVerifiedEntries(entries);
        var document = new RuntimeCapsDocument(Schema, caps.Caps);
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(document, JsonOptions);
        string fullPath = Path.GetFullPath(path);
        string? parent = Path.GetDirectoryName(fullPath);
        if (parent is null || !Directory.Exists(parent))
        {
            throw new DirectoryNotFoundException(parent ?? fullPath);
        }

        using (var stream = new FileStream(
                   fullPath,
                   FileMode.CreateNew,
                   FileAccess.Write,
                   FileShare.None))
        {
            stream.Write(bytes);
            stream.Flush(flushToDisk: true);
        }

        string sha = Sha256(bytes);
        return new UniversalVtfRuntimeCapsFrozen(fullPath, sha, caps);
    }

    internal static UniversalVtfRuntimeCaps LoadCanonical(string path, string expectedSha256)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new ArgumentException("Runtime caps path cannot be empty.", nameof(path));
        }

        Bc3RawBridgeStore.RequireSha256(expectedSha256, nameof(expectedSha256));
        byte[] bytes = File.ReadAllBytes(Path.GetFullPath(path));
        string actual = Sha256(bytes);
        if (!actual.Equals(expectedSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException(
                $"runtime_caps_sha256_mismatch:{actual}!={expectedSha256.ToLowerInvariant()}");
        }

        RuntimeCapsDocument document;
        try
        {
            document = JsonSerializer.Deserialize<RuntimeCapsDocument>(bytes, JsonOptions)
                ?? throw new InvalidDataException("runtime_caps_null_document");
        }
        catch (JsonException error)
        {
            throw new InvalidDataException("runtime_caps_invalid_json", error);
        }

        if (!document.Schema.Equals(Schema, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"runtime_caps_schema:{document.Schema}");
        }

        UniversalVtfRuntimeCaps caps = FromVerifiedEntries(document.Caps);
        byte[] canonical = JsonSerializer.SerializeToUtf8Bytes(
            new RuntimeCapsDocument(Schema, caps.Caps),
            JsonOptions);
        if (!bytes.AsSpan().SequenceEqual(canonical))
        {
            throw new InvalidDataException("runtime_caps_noncanonical_json");
        }

        return caps;
    }

    internal int MaxLevel(string path, UniversalVtfProfileKind profile) =>
        Caps.Where(cap =>
                (int)cap.Profile <= (int)profile &&
                cap.RelativePath.Equals(path, StringComparison.OrdinalIgnoreCase))
            .Select(cap => cap.MaximumLevel)
            .DefaultIfEmpty(int.MaxValue)
            .Min();

    private static void Validate(IReadOnlyList<UniversalVtfRuntimeCap> caps)
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        string? previous = null;
        foreach (UniversalVtfRuntimeCap cap in caps)
        {
            ValidatePath(cap.RelativePath);
            if (cap.MaximumLevel < 1)
            {
                throw new InvalidDataException(
                    $"runtime_cap_below_floor:{cap.RelativePath}:{cap.MaximumLevel}");
            }

            if (!Enum.IsDefined(typeof(UniversalVtfProfileKind), cap.Profile) ||
                string.IsNullOrWhiteSpace(cap.Evidence))
            {
                throw new InvalidDataException($"runtime_cap_invalid:{cap.RelativePath}");
            }

            string key = $"{cap.RelativePath}\0{(int)cap.Profile}";
            if (!seen.Add(key))
            {
                throw new InvalidDataException(
                    $"runtime_cap_duplicate:{cap.RelativePath}:{cap.Profile}");
            }

            string sortKey = $"{cap.RelativePath}\0{(int)cap.Profile:D2}";
            if (previous is not null && StringComparer.Ordinal.Compare(previous, sortKey) >= 0)
            {
                throw new InvalidDataException("runtime_caps_not_canonically_sorted");
            }

            previous = sortKey;
        }
    }

    private static void ValidatePath(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || Path.IsPathRooted(path) || path.Contains('\\') ||
            path != path.ToLowerInvariant() ||
            path.Split('/').Any(segment => string.IsNullOrEmpty(segment) || segment is "." or ".."))
        {
            throw new InvalidDataException($"runtime_cap_invalid_path:{path}");
        }
    }

    private static JsonSerializerOptions CreateJsonOptions()
    {
        var options = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            PropertyNameCaseInsensitive = false,
            WriteIndented = false
        };
        options.Converters.Add(new JsonStringEnumConverter(JsonNamingPolicy.CamelCase));
        return options;
    }

    private static string Sha256(byte[] bytes) =>
        Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private sealed record RuntimeCapsDocument(
        string Schema,
        IReadOnlyList<UniversalVtfRuntimeCap> Caps);
}

internal static class UniversalVtfSelector
{
    private const int TextureFlagsSrgb = 0x00000040;

    internal static UniversalVtfAttemptMatrix BuildAttempts(
        UniversalVtfInventoryEntry entry,
        IReadOnlyList<UniversalVtfCandidateArtifact> candidates,
        UniversalVtfSemanticDecision semantics)
    {
        ArgumentNullException.ThrowIfNull(entry);
        ArgumentNullException.ThrowIfNull(candidates);
        ArgumentNullException.ThrowIfNull(semantics);
        if (candidates.Count == 0)
        {
            throw new ArgumentException("At least one candidate is required.", nameof(candidates));
        }

        string rawPath = Path.GetFullPath(candidates[0].RawSourcePath);
        if (candidates.Any(candidate =>
                !Path.GetFullPath(candidate.RawSourcePath)
                    .Equals(rawPath, StringComparison.OrdinalIgnoreCase)))
        {
            throw new InvalidDataException("attempt_matrix_multiple_raw_authorities");
        }

        VtfContainerLayout rawLayout = VtfContainerLayoutReader.Read(rawPath);
        if (!rawLayout.FileSha256.Equals(entry.Sha256, StringComparison.OrdinalIgnoreCase) ||
            rawLayout.Document.FileLength != entry.Bytes ||
            rawLayout.Document.Width != entry.Width || rawLayout.Document.Height != entry.Height ||
            rawLayout.Document.Frames != entry.Frames || rawLayout.Document.Faces != entry.Faces ||
            rawLayout.Document.Depth != entry.Depth)
        {
            throw new InvalidDataException($"attempt_matrix_raw_authority_mismatch:{entry.RelativePath}");
        }

        IReadOnlyList<UniversalVtfImagePlane> references = DecodeTopPlanes(rawPath, rawLayout);
        var attempts = new List<UniversalVtfAttempt>();
        foreach (IGrouping<int, UniversalVtfCandidateArtifact> levelGroup in candidates
                     .OrderBy(candidate => candidate.Level)
                     .ThenBy(candidate => candidate.Route)
                     .GroupBy(candidate => candidate.Level))
        {
            int level = levelGroup.Key;
            if (level < 1)
            {
                throw new InvalidDataException($"attempt_level_below_floor:{level}");
            }

            UniversalVtfCandidateArtifact[] artifacts = levelGroup.ToArray();
            if (artifacts.Select(artifact => artifact.Route).Distinct().Count() != artifacts.Length)
            {
                throw new InvalidDataException($"attempt_duplicate_route:{level}");
            }

            var routes = artifacts.Select(artifact =>
                    MeasureRoute(entry, level, artifact, references, semantics))
                .ToArray();
            UniversalVtfRouteAttempt? selected = SelectRoute(entry.Flags, routes);
            attempts.Add(selected is null
                ? new UniversalVtfAttempt(
                    level,
                    false,
                    null,
                    string.Join('|', routes.Select(route =>
                        $"{route.Route}:{route.StructuralReason}")),
                    routes.OrderBy(route => route.Route).First().Route,
                    routes.OrderBy(route => route.Route).First().CandidatePath,
                    routes)
                : new UniversalVtfAttempt(
                    level,
                    true,
                    selected.Metrics,
                    "ok",
                    selected.Route,
                    selected.CandidatePath,
                    routes));
        }

        return new UniversalVtfAttemptMatrix(entry.RelativePath, attempts.AsReadOnly());
    }

    internal static UniversalVtfProfileSelection Select(
        UniversalVtfAttemptMatrix matrix,
        UniversalVtfRuntimeCaps runtimeCaps)
    {
        ArgumentNullException.ThrowIfNull(matrix);
        ArgumentNullException.ThrowIfNull(runtimeCaps);
        ValidateMatrix(matrix);

        var selections = new Dictionary<UniversalVtfProfileKind, UniversalVtfSelectedLevel>();
        var profileAttempts = new Dictionary<
            UniversalVtfProfileKind,
            IReadOnlyList<UniversalVtfProfileAttempt>>();
        foreach (UniversalVtfProfileKind profile in Enum.GetValues<UniversalVtfProfileKind>())
        {
            UniversalVtfThresholds thresholds = UniversalVtfPolicy.V1.GetThresholds(profile);
            UniversalVtfAttempt floor = matrix.Attempts[0];
            if (!floor.StructurallyValid || floor.Metrics is null)
            {
                throw new InvalidDataException(
                    $"mandatory_level1_structural_failure:{floor.StructuralReason}");
            }

            var attempts = new List<UniversalVtfProfileAttempt>(matrix.Attempts.Count);
            UniversalVtfEvaluation floorEvaluation =
                UniversalVtfMetricCalculator.EvaluateWorstPlane(floor.Metrics, thresholds);
            attempts.Add(new UniversalVtfProfileAttempt(
                1,
                true,
                floorEvaluation,
                floorEvaluation.Passed
                    ? "forced_floor_pass"
                    : "forced_floor_quality_deficit"));
            UniversalVtfAttempt selectedAttempt = floor;
            bool stopped = false;
            foreach (UniversalVtfAttempt attempt in matrix.Attempts.Skip(1))
            {
                if (stopped)
                {
                    attempts.Add(new UniversalVtfProfileAttempt(
                        attempt.Level,
                        false,
                        null,
                        "not_considered_after_first_failure"));
                    continue;
                }

                if (!attempt.StructurallyValid || attempt.Metrics is null)
                {
                    attempts.Add(new UniversalVtfProfileAttempt(
                        attempt.Level,
                        true,
                        null,
                        $"structural_failure:{attempt.StructuralReason}"));
                    stopped = true;
                    continue;
                }

                UniversalVtfEvaluation evaluation =
                    UniversalVtfMetricCalculator.EvaluateWorstPlane(attempt.Metrics, thresholds);
                attempts.Add(new UniversalVtfProfileAttempt(
                    attempt.Level,
                    true,
                    evaluation,
                    evaluation.Passed
                        ? "quality_pass"
                        : $"quality_failure:{evaluation.WorstGate.Name}"));
                if (!evaluation.Passed)
                {
                    stopped = true;
                    continue;
                }

                selectedAttempt = attempt;
            }

            int cap = runtimeCaps.MaxLevel(matrix.RelativePath, profile);
            string reason = selectedAttempt.Level == 1 && !floorEvaluation.Passed
                ? "forced_floor_quality_deficit"
                : "highest_consecutive_passing_level";
            if (cap < selectedAttempt.Level)
            {
                selectedAttempt = matrix.Attempts.Single(attempt => attempt.Level == cap);
                if (!selectedAttempt.StructurallyValid)
                {
                    throw new InvalidDataException(
                        $"runtime_cap_targets_invalid_level:{matrix.RelativePath}:{profile}:{cap}");
                }

                reason = "runtime_cap";
            }

            selections.Add(profile, new UniversalVtfSelectedLevel(
                selectedAttempt.Level,
                reason,
                selectedAttempt.Route,
                selectedAttempt.CandidatePath));
            profileAttempts.Add(profile, attempts.AsReadOnly());
        }

        UniversalVtfSelectedLevel conservative = selections[UniversalVtfProfileKind.Conservative];
        UniversalVtfSelectedLevel balanced = selections[UniversalVtfProfileKind.Balanced];
        UniversalVtfSelectedLevel aggressive = selections[UniversalVtfProfileKind.Aggressive];
        if (!(conservative.Level <= balanced.Level && balanced.Level <= aggressive.Level))
        {
            throw new InvalidDataException(
                $"profile_nesting_violation:{matrix.RelativePath}:" +
                $"{conservative.Level}/{balanced.Level}/{aggressive.Level}");
        }

        return new UniversalVtfProfileSelection(
            conservative,
            balanced,
            aggressive,
            profileAttempts);
    }

    private static UniversalVtfRouteAttempt MeasureRoute(
        UniversalVtfInventoryEntry entry,
        int level,
        UniversalVtfCandidateArtifact artifact,
        IReadOnlyList<UniversalVtfImagePlane> references,
        UniversalVtfSemanticDecision semantics)
    {
        try
        {
            if (semantics.Route != UniversalVtfFilterRoute.Composite &&
                artifact.Route != semantics.Route)
            {
                throw new InvalidDataException(
                    $"candidate_route_disagrees_with_semantics:{artifact.Route}!={semantics.Route}");
            }

            VtfContainerLayout layout = VtfContainerLayoutReader.Read(artifact.CandidatePath);
            int expectedWidth = Math.Max(1, entry.Width >> level);
            int expectedHeight = Math.Max(1, entry.Height >> level);
            if (layout.Document.Width != expectedWidth || layout.Document.Height != expectedHeight ||
                layout.Document.Frames != entry.Frames || layout.Document.Faces != entry.Faces ||
                layout.Document.HighResFormat is not (13 or 15))
            {
                throw new InvalidDataException("candidate_container_shape_or_format");
            }

            IReadOnlyList<UniversalVtfImagePlane> candidatePlanes =
                DecodeTopPlanes(artifact.CandidatePath, layout);
            UniversalVtfSemanticDecision routed = semantics with { Route = artifact.Route };
            UniversalVtfMetricSet metrics = UniversalVtfMetricCalculator.MeasureAllPlanes(
                references,
                candidatePlanes,
                routed);
            return new UniversalVtfRouteAttempt(
                artifact.Route,
                Path.GetFullPath(artifact.CandidatePath),
                true,
                metrics,
                "ok");
        }
        catch (Exception error) when (error is IOException or InvalidDataException or
                                      InvalidOperationException or NotSupportedException or
                                      ArgumentException or OverflowException)
        {
            return new UniversalVtfRouteAttempt(
                artifact.Route,
                Path.GetFullPath(artifact.CandidatePath),
                false,
                null,
                $"{error.GetType().Name}:{error.Message}");
        }
    }

    private static UniversalVtfRouteAttempt? SelectRoute(
        int flags,
        IReadOnlyList<UniversalVtfRouteAttempt> routes)
    {
        return routes
            .Where(route => route.StructurallyValid && route.Metrics is not null)
            .Select(route => new
            {
                Route = route,
                Evaluation = UniversalVtfMetricCalculator.EvaluateWorstPlane(
                    route.Metrics!,
                    UniversalVtfPolicy.V1.GetThresholds(UniversalVtfProfileKind.Conservative))
            })
            .OrderByDescending(item => item.Evaluation.WorstGate.NormalizedMargin)
            .ThenBy(item => RouteTieRank(flags, item.Route.Route))
            .ThenBy(item => item.Route.Route)
            .Select(item => item.Route)
            .FirstOrDefault();
    }

    private static int RouteTieRank(int flags, UniversalVtfFilterRoute route)
    {
        UniversalVtfFilterRoute flagRoute = (flags & TextureFlagsSrgb) != 0
            ? UniversalVtfFilterRoute.SrgbColor
            : UniversalVtfFilterRoute.NumericLinear;
        if (route == flagRoute)
        {
            return 0;
        }

        return route == UniversalVtfFilterRoute.NumericLinear ? 1 : 2;
    }

    private static IReadOnlyList<UniversalVtfImagePlane> DecodeTopPlanes(
        string path,
        VtfContainerLayout layout)
    {
        var result = new List<UniversalVtfImagePlane>();
        for (int frame = 0; frame < layout.Document.Frames; frame++)
        for (int face = 0; face < layout.Document.Faces; face++)
        for (int depth = 0; depth < layout.Document.Depth; depth++)
        {
            if (!VtfHighResImageDecoder.TryDecodeHighResMipSliceRgba(
                    path,
                    layout.Document,
                    0,
                    frame,
                    face,
                    depth,
                    out byte[] rgba))
            {
                throw new InvalidDataException(
                    $"candidate_decode_failed:{frame}/{face}/{depth}");
            }

            result.Add(new UniversalVtfImagePlane(
                layout.Document.Width,
                layout.Document.Height,
                frame,
                face,
                depth,
                rgba));
        }

        return result.AsReadOnly();
    }

    private static void ValidateMatrix(UniversalVtfAttemptMatrix matrix)
    {
        if (string.IsNullOrWhiteSpace(matrix.RelativePath) || matrix.Attempts is null ||
            matrix.Attempts.Count == 0)
        {
            throw new InvalidDataException("empty_attempt_matrix");
        }

        for (int index = 0; index < matrix.Attempts.Count; index++)
        {
            if (matrix.Attempts[index].Level != index + 1)
            {
                throw new InvalidDataException(
                    $"nonconsecutive_attempt_matrix:{matrix.RelativePath}:{matrix.Attempts[index].Level}");
            }
        }
    }
}
