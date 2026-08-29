using System.Text;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal enum UniversalVtfMonotonicComposePhase
{
    BeforeCommit,
    AfterTreeCommit,
    AfterManifestCommit
}

internal interface IUniversalVtfMonotonicComposeObserver
{
    void Observe(UniversalVtfMonotonicComposePhase phase);
}

internal sealed record UniversalVtfMonotonicAlternativeAuthority(
    string Name,
    string Root,
    string ManifestPath,
    string ManifestSha256);

internal sealed record UniversalVtfMonotonicProtectedAuthority(
    string Name,
    string Root,
    UniversalVtfTreeAnchor Anchor);

internal sealed record UniversalVtfMonotonicComposeRequest(
    UniversalVtfProfileKind Profile,
    string IncumbentRoot,
    UniversalVtfTreeAnchor IncumbentAnchor,
    IReadOnlyList<UniversalVtfMonotonicAlternativeAuthority> Alternatives,
    string OutputRoot,
    string ManifestPath,
    IReadOnlyList<UniversalVtfMonotonicProtectedAuthority>? ProtectedAuthorities = null,
    IUniversalVtfMonotonicComposeObserver? Observer = null);

internal sealed record UniversalVtfMonotonicComposeResult(
    string Root,
    long Bytes,
    string TreeSha256,
    long SavedBytes,
    int ReplacedVtfs,
    string ManifestPath,
    string ManifestSha256);

internal sealed record UniversalVtfMonotonicManifestAlternative(
    string Name,
    string ManifestSha256,
    string PolicyFingerprint,
    UniversalVtfManifestTree Tree);

internal sealed record UniversalVtfMonotonicManifestProtectedAuthority(
    string Name,
    UniversalVtfManifestTree Tree);

internal sealed record UniversalVtfMonotonicManifestEvaluation(
    string Name,
    bool Eligible,
    string Reason,
    long Bytes,
    string Sha256,
    int ProposedLevel,
    UniversalVtfFilterRoute ProposedRoute);

internal sealed record UniversalVtfMonotonicManifestRow(
    string RelativePath,
    long IncumbentBytes,
    string IncumbentSha256,
    IReadOnlyList<UniversalVtfMonotonicManifestEvaluation> Alternatives,
    string Winner,
    long FinalBytes,
    string FinalSha256)
{
    internal long SavedBytes => checked(IncumbentBytes - FinalBytes);
}

internal sealed record UniversalVtfMonotonicManifestAggregate(
    int VtfCount,
    int ReplacedVtfs,
    int PreservedVtfs,
    long IncumbentBytes,
    long FinalBytes,
    long SavedBytes);

internal sealed record UniversalVtfMonotonicManifest(
    string Schema,
    string CompositionPolicyFingerprint,
    UniversalVtfProfileKind Profile,
    string IncumbentAuthority,
    UniversalVtfManifestTree IncumbentTree,
    IReadOnlyList<UniversalVtfMonotonicManifestProtectedAuthority> ProtectedAuthorities,
    IReadOnlyList<UniversalVtfMonotonicManifestAlternative> AlternativeAuthorities,
    UniversalVtfManifestTree FinalTree,
    UniversalVtfMonotonicManifestAggregate Aggregate,
    IReadOnlyList<UniversalVtfMonotonicManifestRow> Rows)
{
    internal UniversalVtfMonotonicManifestRow Row(string relativePath) => Rows.Single(row =>
        row.RelativePath.Equals(relativePath, StringComparison.OrdinalIgnoreCase));
}

internal static class UniversalVtfMonotonicManifestStore
{
    internal const string Schema = "universal-vtf-monotonic-manifest-v3";

    internal static string CompositionPolicyFingerprint { get; } =
        UniversalVtfCanonicalJson.Sha256(Encoding.UTF8.GetBytes(
            "universal-vtf-monotonic-v3|pinned-raw-safe-protected-authorities|" +
            "pinned-non-raw-incumbent|finalized-bytes|" +
            "strictly-smaller|blocker-null|selected-gate-passed|tie-keeps-incumbent|" +
            "selector-replay|transactional-publication"));

    internal static async Task<string> WriteAsync(
        string path,
        UniversalVtfMonotonicManifest manifest,
        CancellationToken cancellationToken)
    {
        Validate(manifest);
        byte[] bytes = UniversalVtfCanonicalJson.Serialize(manifest);
        string fullPath = Path.GetFullPath(path);
        Directory.CreateDirectory(Path.GetDirectoryName(fullPath)!);
        await using var stream = new FileStream(
            fullPath,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None,
            1024 * 1024,
            FileOptions.Asynchronous | FileOptions.WriteThrough);
        await stream.WriteAsync(bytes, cancellationToken).ConfigureAwait(false);
        await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
        stream.Flush(flushToDisk: true);
        return UniversalVtfCanonicalJson.Sha256(bytes);
    }

    internal static UniversalVtfMonotonicManifest Load(
        string path,
        string expectedSha256)
    {
        RequireSha(expectedSha256, nameof(expectedSha256));
        byte[] bytes = File.ReadAllBytes(Path.GetFullPath(path));
        string actual = UniversalVtfCanonicalJson.Sha256(bytes);
        if (!actual.Equals(expectedSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException(
                $"monotonic_manifest_sha256_mismatch:{actual}!={expectedSha256.ToLowerInvariant()}");
        }

        try
        {
            UniversalVtfMonotonicManifest manifest =
                UniversalVtfCanonicalJson.Deserialize<UniversalVtfMonotonicManifest>(bytes);
            Validate(manifest);
            return manifest;
        }
        catch (InvalidDataException)
        {
            throw;
        }
        catch (Exception exception) when (exception is System.Text.Json.JsonException or
                                          ArgumentException or InvalidOperationException or
                                          NullReferenceException)
        {
            throw new InvalidDataException("monotonic_manifest_malformed", exception);
        }
    }

    internal static void Validate(UniversalVtfMonotonicManifest manifest)
    {
        ArgumentNullException.ThrowIfNull(manifest);
        if (!string.Equals(manifest.Schema, Schema, StringComparison.Ordinal) ||
            !string.Equals(manifest.CompositionPolicyFingerprint,
                CompositionPolicyFingerprint,
                StringComparison.Ordinal))
        {
            throw new InvalidDataException("monotonic_manifest_schema_or_policy");
        }

        ValidateIncumbentAuthorityName(manifest.IncumbentAuthority);
        if (!Enum.IsDefined(typeof(UniversalVtfProfileKind), manifest.Profile))
        {
            throw new InvalidDataException("monotonic_manifest_profile");
        }

        ValidateTree(manifest.IncumbentTree, "incumbent");
        ValidateTree(manifest.FinalTree, "final");
        RejectRawTree(manifest.IncumbentTree);
        if (manifest.ProtectedAuthorities is null ||
            manifest.ProtectedAuthorities.Count != 2 ||
            manifest.ProtectedAuthorities.Any(authority => authority is null) ||
            manifest.AlternativeAuthorities is null ||
            manifest.AlternativeAuthorities.Count == 0 ||
            manifest.Rows is null || manifest.Rows.Count == 0)
        {
            throw new InvalidDataException("monotonic_manifest_empty");
        }

        string[] protectedNames = manifest.ProtectedAuthorities
            .Select(authority => authority.Name)
            .ToArray();
        if (!protectedNames.SequenceEqual(new[] { "raw", "safe" }, StringComparer.Ordinal))
        {
            throw new InvalidDataException("monotonic_manifest_protected_authorities");
        }

        foreach (UniversalVtfMonotonicManifestProtectedAuthority authority in
                 manifest.ProtectedAuthorities)
        {
            ValidateTree(authority.Tree, authority.Name);
        }

        if (manifest.AlternativeAuthorities.Any(authority => authority is null) ||
            manifest.Rows.Any(row => row is null))
        {
            throw new InvalidDataException("monotonic_manifest_null_entry");
        }

        string[] authorityNames = manifest.AlternativeAuthorities
            .Select(authority => authority.Name).ToArray();
        if (authorityNames.Any(string.IsNullOrWhiteSpace) ||
            authorityNames.Distinct(StringComparer.Ordinal).Count() != authorityNames.Length ||
            !authorityNames.SequenceEqual(
                authorityNames.OrderBy(name => name, StringComparer.Ordinal),
                StringComparer.Ordinal))
        {
            throw new InvalidDataException("monotonic_manifest_alternative_order");
        }

        foreach (UniversalVtfMonotonicManifestAlternative authority in
                 manifest.AlternativeAuthorities)
        {
            RequireSha(authority.ManifestSha256, nameof(authority.ManifestSha256));
            RequireSha(authority.PolicyFingerprint, nameof(authority.PolicyFingerprint));
            ValidateTree(authority.Tree, authority.Name);
        }

        if (manifest.FinalTree.FileCount != manifest.IncumbentTree.FileCount ||
            manifest.AlternativeAuthorities.Any(authority =>
                authority.Tree.FileCount != manifest.IncumbentTree.FileCount) ||
            manifest.Rows.Count > manifest.IncumbentTree.FileCount)
        {
            throw new InvalidDataException("monotonic_manifest_file_count_drift");
        }

        var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        string? previous = null;
        foreach (UniversalVtfMonotonicManifestRow row in manifest.Rows)
        {
            ValidatePath(row.RelativePath);
            if (!paths.Add(row.RelativePath) || previous is not null &&
                StringComparer.Ordinal.Compare(previous, row.RelativePath) >= 0)
            {
                throw new InvalidDataException("monotonic_manifest_row_order");
            }

            previous = row.RelativePath;
            RequireSha(row.IncumbentSha256, nameof(row.IncumbentSha256));
            RequireSha(row.FinalSha256, nameof(row.FinalSha256));
            if (row.Alternatives is null || row.Winner is null ||
                row.IncumbentBytes <= 0 || row.FinalBytes <= 0 ||
                row.FinalBytes > row.IncumbentBytes ||
                row.Alternatives.Any(evaluation => evaluation is null) ||
                !row.Alternatives.Select(item => item.Name)
                    .SequenceEqual(authorityNames, StringComparer.Ordinal))
            {
                throw new InvalidDataException(
                    $"monotonic_manifest_row_physical:{row.RelativePath}");
            }

            foreach (UniversalVtfMonotonicManifestEvaluation evaluation in row.Alternatives)
            {
                RequireSha(evaluation.Sha256, nameof(evaluation.Sha256));
                if (evaluation.Bytes <= 0 || string.IsNullOrWhiteSpace(evaluation.Reason) ||
                    evaluation.ProposedLevel < 1 ||
                    !Enum.IsDefined(typeof(UniversalVtfFilterRoute), evaluation.ProposedRoute))
                {
                    throw new InvalidDataException(
                        $"monotonic_manifest_evaluation:{row.RelativePath}:{evaluation.Name}");
                }
            }

            var incumbentOption = new UniversalVtfMonotonicOption(
                "incumbent",
                row.IncumbentBytes,
                row.IncumbentSha256,
                Eligible: true,
                IneligibleReason: null);
            UniversalVtfMonotonicOption[] options = row.Alternatives.Select(evaluation =>
                new UniversalVtfMonotonicOption(
                    evaluation.Name,
                    evaluation.Bytes,
                    evaluation.Sha256,
                    evaluation.Eligible,
                    evaluation.Eligible ? null : evaluation.Reason)).ToArray();
            UniversalVtfMonotonicDecision replay = UniversalVtfMonotonicSelector.Select(
                incumbentOption,
                options);
            if (!replay.Winner.Name.Equals(row.Winner, StringComparison.Ordinal) ||
                replay.Winner.Bytes != row.FinalBytes ||
                !replay.Winner.Sha256.Equals(
                    row.FinalSha256,
                    StringComparison.OrdinalIgnoreCase) ||
                replay.Alternatives.Where((decision, index) =>
                        !decision.Reason.Equals(
                            row.Alternatives[index].Reason,
                            StringComparison.Ordinal))
                    .Any())
            {
                throw new InvalidDataException(
                    $"monotonic_manifest_selector_replay:{row.RelativePath}");
            }

            if (row.Winner.Equals("incumbent", StringComparison.Ordinal))
            {
                if (row.FinalBytes != row.IncumbentBytes ||
                    !row.FinalSha256.Equals(
                        row.IncumbentSha256,
                        StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException(
                        $"monotonic_manifest_incumbent_winner:{row.RelativePath}");
                }
            }
            else
            {
                UniversalVtfMonotonicManifestEvaluation? winner = row.Alternatives.SingleOrDefault(
                    item => item.Name.Equals(row.Winner, StringComparison.Ordinal))
                    ?? throw new InvalidDataException(
                        $"monotonic_manifest_unknown_winner:{row.RelativePath}:{row.Winner}");
                if (!winner.Eligible || winner.Reason != "selected_smallest_safe" ||
                    winner.Bytes != row.FinalBytes ||
                    !winner.Sha256.Equals(row.FinalSha256, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException(
                        $"monotonic_manifest_winner_drift:{row.RelativePath}");
                }
            }
        }

        try
        {
            int replaced = manifest.Rows.Count(row => row.Winner != "incumbent");
            long saved = checked(manifest.IncumbentTree.Bytes - manifest.FinalTree.Bytes);
            var expected = new UniversalVtfMonotonicManifestAggregate(
                manifest.Rows.Count,
                replaced,
                manifest.Rows.Count - replaced,
                manifest.IncumbentTree.Bytes,
                manifest.FinalTree.Bytes,
                saved);
            if (manifest.Aggregate != expected || saved < 0 ||
                manifest.Rows.Sum(row => row.SavedBytes) != saved)
            {
                throw new InvalidDataException("monotonic_manifest_aggregate");
            }
        }
        catch (OverflowException exception)
        {
            throw new InvalidDataException("monotonic_manifest_aggregate_overflow", exception);
        }
    }

    private static void RejectRawTree(UniversalVtfManifestTree tree)
    {
        UniversalVtfTreeAnchor raw = UniversalVtfCorpusExpectations.RawTreeAnchor;
        if (tree.FileCount == raw.FileCount && tree.Bytes == raw.TotalBytes &&
            tree.TreeSha256.Equals(raw.TreeSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException("monotonic_raw_incumbent_forbidden");
        }
    }

    internal static void ValidateIncumbentAuthorityName(string? authority)
    {
        if (string.IsNullOrWhiteSpace(authority) || authority == "incumbent" ||
            !authority.Equals(authority.ToLowerInvariant(), StringComparison.Ordinal) ||
            authority.Any(character => !((character >= 'a' && character <= 'z') ||
                                          (character >= '0' && character <= '9') ||
                                          character is '-' or '_')) ||
            authority.Contains("raw", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException(
                $"monotonic_invalid_incumbent_authority:{authority}");
        }
    }

    private static void ValidateTree(UniversalVtfManifestTree? tree, string authority)
    {
        if (tree is null || !string.Equals(tree.Authority, authority, StringComparison.Ordinal) ||
            tree.FileCount <= 0 || tree.Bytes <= 0)
        {
            throw new InvalidDataException($"monotonic_manifest_tree:{authority}");
        }

        RequireSha(tree.TreeSha256, nameof(tree.TreeSha256));
    }

    private static void ValidatePath(string? path)
    {
        if (string.IsNullOrWhiteSpace(path) || Path.IsPathRooted(path) ||
            path.Contains('\\') || path != path.ToLowerInvariant() ||
            path.Split('/').Any(segment => segment is "" or "." or ".."))
        {
            throw new InvalidDataException($"monotonic_manifest_path:{path}");
        }
    }

    private static void RequireSha(string value, string field)
    {
        if (value is null || value.Length != 64 || value.Any(character => !Uri.IsHexDigit(character)))
        {
            throw new InvalidDataException($"monotonic_manifest_sha256:{field}");
        }
    }
}

internal static class UniversalVtfMonotonicComposer
{
    internal static async Task<UniversalVtfMonotonicComposeResult> ComposeAsync(
        UniversalVtfMonotonicComposeRequest request,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        ArgumentNullException.ThrowIfNull(request.IncumbentAnchor);
        if (!Enum.IsDefined(typeof(UniversalVtfProfileKind), request.Profile))
        {
            throw new InvalidDataException("monotonic_invalid_profile");
        }

        UniversalVtfMonotonicManifestStore.ValidateIncumbentAuthorityName(
            request.IncumbentAnchor.Name);
        RejectRawIncumbent(request.IncumbentAnchor);
        if (request.Alternatives is null || request.Alternatives.Count == 0 ||
            request.Alternatives.Any(authority => authority is null))
        {
            throw new InvalidDataException("monotonic_empty_authority");
        }

        if (request.ProtectedAuthorities is null ||
            request.ProtectedAuthorities.Count != 2 ||
            request.ProtectedAuthorities.Any(authority => authority is null) ||
            !request.ProtectedAuthorities.Select(authority => authority.Name)
                .OrderBy(name => name, StringComparer.Ordinal)
                .SequenceEqual(new[] { "raw", "safe" }, StringComparer.Ordinal))
        {
            throw new InvalidDataException("monotonic_protected_authorities_required");
        }

        string incumbentRoot = RequireDirectory(request.IncumbentRoot, "incumbent");
        string outputRoot = Path.GetFullPath(request.OutputRoot);
        string manifestPath = Path.GetFullPath(request.ManifestPath);
        if (Directory.Exists(outputRoot) || File.Exists(outputRoot) || File.Exists(manifestPath) ||
            Directory.Exists(manifestPath))
        {
            throw new IOException("monotonic_destination_exists");
        }

        string[] alternativeRoots = request.Alternatives
            .Select(authority => RequireDirectory(authority.Root, authority.Name))
            .ToArray();
        string[] alternativeManifests = request.Alternatives
            .Select(authority => RequireFile(authority.ManifestPath, authority.Name + "_manifest"))
            .ToArray();
        string[] protectedRoots = request.ProtectedAuthorities
            .Select(authority => RequireDirectory(authority.Root, authority.Name))
            .ToArray();
        PreflightIsolation(
            incumbentRoot,
            alternativeRoots,
            alternativeManifests,
            protectedRoots,
            outputRoot,
            manifestPath);

        PreparedProtectedAuthority[] protectedAuthorities = request.ProtectedAuthorities
            .OrderBy(authority => authority.Name, StringComparer.Ordinal)
            .Select(PrepareProtectedAuthority)
            .ToArray();
        IReadOnlyDictionary<string, PreparedProtectedAuthority> protectedByName =
            protectedAuthorities.ToDictionary(
                authority => authority.Authority.Name,
                StringComparer.Ordinal);

        UniversalVtfTreeSnapshot incumbent = UniversalVtfTreeInventory.Capture(incumbentRoot);
        UniversalVtfTreeInventory.RequireAnchor(incumbent, request.IncumbentAnchor);
        UniversalVtfTreeFile[] incumbentVtfs = incumbent.Files
            .Where(file => file.RelativePath.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
            .OrderBy(file => file.RelativePath, StringComparer.Ordinal)
            .ToArray();
        if (incumbentVtfs.Length == 0)
        {
            throw new InvalidDataException("monotonic_empty_authority");
        }

        PreparedAlternative[] alternatives = request.Alternatives
            .OrderBy(authority => authority.Name, StringComparer.Ordinal)
            .Select(authority => PrepareAlternative(
                request.Profile,
                authority,
                incumbent,
                incumbentRoot,
                protectedByName))
            .ToArray();
        if (alternatives.Select(item => item.Authority.Name)
                .Distinct(StringComparer.Ordinal).Count() != alternatives.Length)
        {
            throw new InvalidDataException("monotonic_duplicate_alternative");
        }

        var rows = new List<UniversalVtfMonotonicManifestRow>(incumbentVtfs.Length);
        var winnerPaths = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (UniversalVtfTreeFile incumbentFile in incumbentVtfs)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var incumbentOption = new UniversalVtfMonotonicOption(
                "incumbent",
                incumbentFile.Length,
                incumbentFile.Sha256,
                Eligible: true,
                IneligibleReason: null);
            UniversalVtfMonotonicOption[] options = alternatives.Select(alternative =>
            {
                UniversalVtfManifestRow row = alternative.Rows[incumbentFile.RelativePath];
                bool gatePassed = row.WorstGate?.Passed == true;
                if ((row.Blocker is null) != gatePassed)
                {
                    throw new InvalidDataException(
                        $"monotonic_gate_blocker_drift:{incumbentFile.RelativePath}:" +
                        alternative.Authority.Name);
                }

                return new UniversalVtfMonotonicOption(
                    alternative.Authority.Name,
                    row.FinalBytes,
                    row.FinalSha256,
                    gatePassed,
                    gatePassed ? null : row.Blocker ?? "selected_gate_failed");
            }).ToArray();
            UniversalVtfMonotonicDecision decision = UniversalVtfMonotonicSelector.Select(
                incumbentOption,
                options);
            if (decision.Winner.Name != "incumbent")
            {
                PreparedAlternative winner = alternatives.Single(item =>
                    item.Authority.Name.Equals(decision.Winner.Name, StringComparison.Ordinal));
                winnerPaths.Add(
                    incumbentFile.RelativePath,
                    Combine(winner.Root, incumbentFile.RelativePath));
            }

            UniversalVtfMonotonicManifestEvaluation[] evaluations = alternatives.Select(
                alternative =>
                {
                    UniversalVtfManifestRow proposed =
                        alternative.Rows[incumbentFile.RelativePath];
                    UniversalVtfMonotonicAlternativeDecision evaluated =
                        decision.Alternatives.Single(item => item.Option.Name.Equals(
                            alternative.Authority.Name,
                            StringComparison.Ordinal));
                    return new UniversalVtfMonotonicManifestEvaluation(
                        alternative.Authority.Name,
                        evaluated.Option.Eligible,
                        evaluated.Reason,
                        evaluated.Option.Bytes,
                        evaluated.Option.Sha256,
                        proposed.SelectedLevel,
                        proposed.Route);
                }).ToArray();
            rows.Add(new UniversalVtfMonotonicManifestRow(
                incumbentFile.RelativePath,
                incumbentFile.Length,
                incumbentFile.Sha256,
                evaluations,
                decision.Winner.Name,
                decision.Winner.Bytes,
                decision.Winner.Sha256));
        }

        Directory.CreateDirectory(Path.GetDirectoryName(outputRoot)!);
        Directory.CreateDirectory(Path.GetDirectoryName(manifestPath)!);
        RequireNoReparseAncestor(outputRoot, "output");
        RequireNoReparseAncestor(manifestPath, "manifest");
        string transaction = Guid.NewGuid().ToString("N");
        string stagingRoot = outputRoot + $".monotonic-owned-{transaction}";
        string stagingManifest = manifestPath + $".monotonic-owned-{transaction}.tmp";
        EnsureAbsent(stagingRoot);
        EnsureAbsent(stagingManifest);
        RequireSeparated(stagingRoot, incumbentRoot, "staging_incumbent_overlap");
        foreach (PreparedAlternative alternative in alternatives)
        {
            RequireSeparated(
                stagingRoot,
                alternative.Root,
                $"staging_alternative_overlap:{alternative.Authority.Name}");
        }

        foreach (PreparedProtectedAuthority authority in protectedAuthorities)
        {
            RequireSeparated(
                stagingRoot,
                authority.Root,
                $"staging_protected_overlap:{authority.Authority.Name}");
            RequireSeparated(
                stagingManifest,
                authority.Root,
                $"staging_manifest_protected_overlap:{authority.Authority.Name}");
        }

        CopyTree(incumbent, incumbentRoot, stagingRoot, cancellationToken);
        foreach ((string relativePath, string source) in winnerPaths)
        {
            cancellationToken.ThrowIfCancellationRequested();
            string destination = Combine(stagingRoot, relativePath);
            File.SetAttributes(destination, FileAttributes.Normal);
            File.Copy(source, destination, overwrite: true);
        }

        UniversalVtfTreeSnapshot final = UniversalVtfTreeInventory.Capture(stagingRoot);
        RequireStagedRows(incumbent, final, rows);
        long savedBytes = checked(incumbent.TotalBytes - final.TotalBytes);
        if (savedBytes != rows.Sum(row => row.SavedBytes) || savedBytes < 0)
        {
            throw new InvalidDataException("monotonic_physical_savings_mismatch");
        }

        var aggregate = new UniversalVtfMonotonicManifestAggregate(
            rows.Count,
            winnerPaths.Count,
            rows.Count - winnerPaths.Count,
            incumbent.TotalBytes,
            final.TotalBytes,
            savedBytes);
        var manifest = new UniversalVtfMonotonicManifest(
            UniversalVtfMonotonicManifestStore.Schema,
            UniversalVtfMonotonicManifestStore.CompositionPolicyFingerprint,
            request.Profile,
            request.IncumbentAnchor.Name,
            ManifestTree("incumbent", incumbent),
            protectedAuthorities.Select(item =>
                new UniversalVtfMonotonicManifestProtectedAuthority(
                    item.Authority.Name,
                    ManifestTree(item.Authority.Name, item.Tree))).ToArray(),
            alternatives.Select(item => new UniversalVtfMonotonicManifestAlternative(
                item.Authority.Name,
                item.Authority.ManifestSha256.ToLowerInvariant(),
                item.Manifest.PolicyFingerprint,
                ManifestTree(item.Authority.Name, item.Tree))).ToArray(),
            ManifestTree("final", final),
            aggregate,
            rows);
        string manifestSha = await UniversalVtfMonotonicManifestStore.WriteAsync(
            stagingManifest,
            manifest,
            cancellationToken).ConfigureAwait(false);

        request.Observer?.Observe(UniversalVtfMonotonicComposePhase.BeforeCommit);
        cancellationToken.ThrowIfCancellationRequested();
        RequireSameSnapshot(
            final,
            UniversalVtfTreeInventory.Capture(stagingRoot),
            "staging_changed_before_commit");
        _ = UniversalVtfMonotonicManifestStore.Load(stagingManifest, manifestSha);
        RequireSameSnapshot(
            incumbent,
            UniversalVtfTreeInventory.Capture(incumbentRoot),
            "incumbent_changed");
        foreach (PreparedProtectedAuthority authority in protectedAuthorities)
        {
            RequireSameSnapshot(
                authority.Tree,
                UniversalVtfTreeInventory.Capture(authority.Root),
                $"protected_authority_changed:{authority.Authority.Name}");
        }

        foreach (PreparedAlternative alternative in alternatives)
        {
            RequireSameSnapshot(
                alternative.Tree,
                UniversalVtfTreeInventory.Capture(alternative.Root),
                $"alternative_changed:{alternative.Authority.Name}");
        }

        cancellationToken.ThrowIfCancellationRequested();
        EnsureAbsent(outputRoot);
        EnsureAbsent(manifestPath);

        bool manifestPublished = false;
        bool outputPublished = false;
        UniversalVtfTreeSnapshot published;
        try
        {
            Directory.Move(stagingRoot, outputRoot);
            outputPublished = true;
            request.Observer?.Observe(UniversalVtfMonotonicComposePhase.AfterTreeCommit);
            EnsureAbsent(manifestPath);
            File.Move(stagingManifest, manifestPath, overwrite: false);
            manifestPublished = true;
            request.Observer?.Observe(UniversalVtfMonotonicComposePhase.AfterManifestCommit);

            published = UniversalVtfTreeInventory.Capture(outputRoot);
            RequireSameSnapshot(final, published, "published_tree_changed");
            _ = UniversalVtfMonotonicManifestStore.Load(manifestPath, manifestSha);
        }
        catch (Exception publicationError)
        {
            var rollbackErrors = new List<Exception>();
            if (manifestPublished)
            {
                if (!File.Exists(manifestPath) || File.Exists(stagingManifest) ||
                    Directory.Exists(stagingManifest))
                {
                    rollbackErrors.Add(new IOException(
                        $"monotonic_manifest_rollback_path_occupied:{stagingManifest}"));
                }
                else
                {
                    try
                    {
                        File.Move(manifestPath, stagingManifest, overwrite: false);
                    }
                    catch (Exception rollbackError)
                    {
                        rollbackErrors.Add(rollbackError);
                    }
                }
            }

            if (outputPublished)
            {
                if (!Directory.Exists(outputRoot) || Directory.Exists(stagingRoot))
                {
                    rollbackErrors.Add(new IOException(
                        $"monotonic_output_rollback_path_occupied:{stagingRoot}"));
                }
                else
                {
                    try
                    {
                        Directory.Move(outputRoot, stagingRoot);
                    }
                    catch (Exception rollbackError)
                    {
                        rollbackErrors.Add(rollbackError);
                    }
                }
            }

            if (rollbackErrors.Count > 0)
            {
                throw new AggregateException(
                    "monotonic_publication_and_rollback_failed:" + stagingRoot,
                    new[] { publicationError }.Concat(rollbackErrors));
            }

            throw;
        }

        return new UniversalVtfMonotonicComposeResult(
            outputRoot,
            published.TotalBytes,
            published.TreeSha256,
            savedBytes,
            winnerPaths.Count,
            manifestPath,
            manifestSha);
    }

    private static PreparedAlternative PrepareAlternative(
        UniversalVtfProfileKind profile,
        UniversalVtfMonotonicAlternativeAuthority authority,
        UniversalVtfTreeSnapshot incumbent,
        string incumbentRoot,
        IReadOnlyDictionary<string, PreparedProtectedAuthority> protectedAuthorities)
    {
        if (string.IsNullOrWhiteSpace(authority.Name) || authority.Name == "incumbent")
        {
            throw new InvalidDataException("monotonic_invalid_alternative_name");
        }

        string root = RequireDirectory(authority.Root, authority.Name);
        RequireSeparated(root, incumbentRoot, $"alternative_incumbent_overlap:{authority.Name}");
        UniversalVtfManifest manifest = UniversalVtfManifestStore.Load(
            authority.ManifestPath,
            authority.ManifestSha256);
        if (manifest.Profile != profile)
        {
            throw new InvalidDataException(
                $"monotonic_profile_mismatch:{authority.Name}:{manifest.Profile}!={profile}");
        }


        RequireTree(
            manifest.RawTree,
            protectedAuthorities["raw"].Tree,
            $"{authority.Name}:raw");
        RequireTree(
            manifest.SafeTree,
            protectedAuthorities["safe"].Tree,
            $"{authority.Name}:safe");

        UniversalVtfTreeSnapshot tree = UniversalVtfTreeInventory.Capture(root);
        RequireTree(manifest.FinalTree, tree, authority.Name);
        RequireSamePathSet(incumbent, tree, authority.Name);
        Dictionary<string, UniversalVtfTreeFile> files = tree.Files.ToDictionary(
            file => file.RelativePath,
            StringComparer.OrdinalIgnoreCase);
        Dictionary<string, UniversalVtfTreeFile> incumbentFiles = incumbent.Files.ToDictionary(
            file => file.RelativePath,
            StringComparer.OrdinalIgnoreCase);
        foreach (UniversalVtfTreeFile file in tree.Files.Where(file =>
                     !file.RelativePath.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase)))
        {
            UniversalVtfTreeFile expected = incumbentFiles[file.RelativePath];
            if (file.Length != expected.Length ||
                !file.Sha256.Equals(expected.Sha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException(
                    $"monotonic_non_vtf_drift:{authority.Name}:{file.RelativePath}");
            }
        }

        var rows = manifest.Rows.ToDictionary(
            row => row.RelativePath,
            StringComparer.OrdinalIgnoreCase);
        string[] vtfPaths = tree.Files.Where(file =>
                file.RelativePath.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
            .Select(file => file.RelativePath).OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        if (!vtfPaths.SequenceEqual(
                rows.Keys.OrderBy(path => path, StringComparer.OrdinalIgnoreCase),
                StringComparer.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"monotonic_vtf_coverage:{authority.Name}");
        }

        foreach ((string path, UniversalVtfManifestRow row) in rows)
        {
            UniversalVtfTreeFile file = files[path];
            if (file.Length != row.FinalBytes ||
                !file.Sha256.Equals(row.FinalSha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException(
                    $"monotonic_alternative_row_drift:{authority.Name}:{path}");
            }
        }

        return new PreparedAlternative(authority, root, manifest, tree, rows);
    }

    private static void CopyTree(
        UniversalVtfTreeSnapshot source,
        string sourceRoot,
        string destinationRoot,
        CancellationToken cancellationToken)
    {
        Directory.CreateDirectory(destinationRoot);
        foreach (UniversalVtfTreeFile file in source.Files)
        {
            cancellationToken.ThrowIfCancellationRequested();
            string destination = Combine(destinationRoot, file.RelativePath);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            File.Copy(Combine(sourceRoot, file.RelativePath), destination, overwrite: false);
            File.SetAttributes(destination, FileAttributes.Normal);
        }
    }

    private static void RequireStagedRows(
        UniversalVtfTreeSnapshot incumbent,
        UniversalVtfTreeSnapshot staged,
        IReadOnlyList<UniversalVtfMonotonicManifestRow> rows)
    {
        RequireSamePathSet(incumbent, staged, "staged");
        Dictionary<string, UniversalVtfTreeFile> incumbentFiles = incumbent.Files.ToDictionary(
            file => file.RelativePath,
            StringComparer.OrdinalIgnoreCase);
        Dictionary<string, UniversalVtfTreeFile> stagedFiles = staged.Files.ToDictionary(
            file => file.RelativePath,
            StringComparer.OrdinalIgnoreCase);
        var rowPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (UniversalVtfMonotonicManifestRow row in rows)
        {
            if (!rowPaths.Add(row.RelativePath) ||
                !incumbentFiles.TryGetValue(row.RelativePath, out UniversalVtfTreeFile? before) ||
                !stagedFiles.TryGetValue(row.RelativePath, out UniversalVtfTreeFile? after) ||
                before.Length != row.IncumbentBytes ||
                !before.Sha256.Equals(row.IncumbentSha256, StringComparison.OrdinalIgnoreCase) ||
                after.Length != row.FinalBytes ||
                !after.Sha256.Equals(row.FinalSha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException(
                    $"monotonic_staged_row_drift:{row.RelativePath}");
            }
        }

        string[] stagedVtfs = staged.Files.Where(file => file.RelativePath.EndsWith(
                ".vtf",
                StringComparison.OrdinalIgnoreCase))
            .Select(file => file.RelativePath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        if (!stagedVtfs.SequenceEqual(
                rowPaths.OrderBy(path => path, StringComparer.OrdinalIgnoreCase),
                StringComparer.OrdinalIgnoreCase))
        {
            throw new InvalidDataException("monotonic_staged_vtf_coverage");
        }

        foreach (UniversalVtfTreeFile file in staged.Files.Where(file =>
                     !file.RelativePath.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase)))
        {
            UniversalVtfTreeFile expected = incumbentFiles[file.RelativePath];
            if (file.Length != expected.Length ||
                !file.Sha256.Equals(expected.Sha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException(
                    $"monotonic_staged_non_vtf_drift:{file.RelativePath}");
            }
        }
    }

    private static UniversalVtfManifestTree ManifestTree(
        string authority,
        UniversalVtfTreeSnapshot tree) =>
        new(authority, tree.Files.Count, tree.TotalBytes, tree.TreeSha256);

    private static void RequireTree(
        UniversalVtfManifestTree declared,
        UniversalVtfTreeSnapshot actual,
        string authority)
    {
        if (declared.FileCount != actual.Files.Count || declared.Bytes != actual.TotalBytes ||
            !declared.TreeSha256.Equals(actual.TreeSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"monotonic_tree_drift:{authority}");
        }
    }

    private static void RequireSamePathSet(
        UniversalVtfTreeSnapshot incumbent,
        UniversalVtfTreeSnapshot alternative,
        string authority)
    {
        string[] expected = incumbent.Files.Select(file => file.RelativePath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        string[] actual = alternative.Files.Select(file => file.RelativePath)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
        if (!expected.SequenceEqual(actual, StringComparer.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"monotonic_path_set:{authority}");
        }
    }

    private static void RequireSameSnapshot(
        UniversalVtfTreeSnapshot expected,
        UniversalVtfTreeSnapshot actual,
        string reason)
    {
        if (expected.Files.Count != actual.Files.Count || expected.TotalBytes != actual.TotalBytes ||
            !expected.TreeSha256.Equals(actual.TreeSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException(reason);
        }
    }

    private static string RequireDirectory(string path, string authority)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new InvalidDataException($"monotonic_directory_missing:{authority}");
        }

        string full = Path.GetFullPath(path);
        if (!Directory.Exists(full))
        {
            throw new DirectoryNotFoundException($"{authority}:{full}");
        }

        return full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }

    private static string RequireFile(string path, string authority)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new InvalidDataException($"monotonic_file_missing:{authority}");
        }

        string full = Path.GetFullPath(path);
        if (!File.Exists(full))
        {
            throw new FileNotFoundException(authority, full);
        }

        return full;
    }

    private static string Combine(string root, string relativePath)
    {
        string authority = Path.GetFullPath(root).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string path = Path.GetFullPath(Path.Combine(
            authority,
            relativePath.Replace('/', Path.DirectorySeparatorChar)));
        if (!path.StartsWith(
                authority + Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"monotonic_path_escape:{relativePath}");
        }

        return path;
    }

    private static void RequireSeparated(string first, string second, string reason)
    {
        string left = Path.GetFullPath(first).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string right = Path.GetFullPath(second).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        if (left.Equals(right, StringComparison.OrdinalIgnoreCase) ||
            left.StartsWith(right + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) ||
            right.StartsWith(left + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException(reason);
        }
    }

    private static void PreflightIsolation(
        string incumbentRoot,
        IReadOnlyList<string> alternativeRoots,
        IReadOnlyList<string> alternativeManifests,
        IReadOnlyList<string> protectedRoots,
        string outputRoot,
        string manifestPath)
    {
        RequireNoReparseAncestor(incumbentRoot, "incumbent");
        RequireNoReparseAncestor(outputRoot, "output");
        RequireNoReparseAncestor(manifestPath, "manifest");
        RequireSeparated(outputRoot, incumbentRoot, "output_incumbent_overlap");
        RequireSeparated(manifestPath, incumbentRoot, "manifest_incumbent_overlap");
        RequireSeparated(outputRoot, manifestPath, "output_manifest_overlap");

        for (int index = 0; index < protectedRoots.Count; index++)
        {
            string root = protectedRoots[index];
            RequireNoReparseAncestor(root, $"protected:{index}");
            RequireSeparated(root, incumbentRoot, $"protected_incumbent_overlap:{index}");
            RequireSeparated(outputRoot, root, $"output_protected_overlap:{index}");
            RequireSeparated(manifestPath, root, $"manifest_protected_overlap:{index}");
            for (int other = index + 1; other < protectedRoots.Count; other++)
            {
                RequireSeparated(
                    root,
                    protectedRoots[other],
                    $"protected_overlap:{index}:{other}");
            }

            for (int alternative = 0; alternative < alternativeRoots.Count; alternative++)
            {
                RequireSeparated(
                    root,
                    alternativeRoots[alternative],
                    $"protected_alternative_overlap:{index}:{alternative}");
            }
        }

        for (int index = 0; index < alternativeRoots.Count; index++)
        {
            string root = alternativeRoots[index];
            string alternativeManifest = alternativeManifests[index];
            RequireNoReparseAncestor(root, $"alternative:{index}");
            RequireNoReparseAncestor(alternativeManifest, $"alternative_manifest:{index}");
            RequireSeparated(root, incumbentRoot, $"alternative_incumbent_overlap:{index}");
            RequireSeparated(outputRoot, root, $"output_alternative_overlap:{index}");
            RequireSeparated(manifestPath, root, $"manifest_alternative_overlap:{index}");
            RequireSeparated(
                outputRoot,
                alternativeManifest,
                $"output_alternative_manifest_overlap:{index}");
            RequireSeparated(
                manifestPath,
                alternativeManifest,
                $"manifest_alternative_manifest_overlap:{index}");
            for (int other = index + 1; other < alternativeRoots.Count; other++)
            {
                RequireSeparated(
                    root,
                    alternativeRoots[other],
                    $"alternative_overlap:{index}:{other}");
            }
        }
    }

    private static void RequireNoReparseAncestor(string path, string authority)
    {
        string full = Path.GetFullPath(path);
        string? current = File.Exists(full) || Directory.Exists(full)
            ? full
            : Path.GetDirectoryName(full);
        while (!string.IsNullOrEmpty(current))
        {
            if (File.Exists(current) || Directory.Exists(current))
            {
                FileAttributes attributes = File.GetAttributes(current);
                if ((attributes & FileAttributes.ReparsePoint) != 0)
                {
                    throw new InvalidDataException(
                        $"monotonic_reparse_ancestor:{authority}:{current}");
                }
            }

            string? parent = Path.GetDirectoryName(current);
            if (string.Equals(parent, current, StringComparison.OrdinalIgnoreCase))
            {
                break;
            }

            current = parent;
        }
    }

    private static void EnsureAbsent(string path)
    {
        if (File.Exists(path) || Directory.Exists(path))
        {
            throw new IOException($"monotonic_publication_collision:{path}");
        }
    }

    private static void RejectRawIncumbent(UniversalVtfTreeAnchor anchor)
    {
        UniversalVtfTreeAnchor raw = UniversalVtfCorpusExpectations.RawTreeAnchor;
        if (anchor.FileCount == raw.FileCount && anchor.TotalBytes == raw.TotalBytes &&
            anchor.TreeSha256.Equals(raw.TreeSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException("monotonic_raw_incumbent_forbidden");
        }
    }

    private static PreparedProtectedAuthority PrepareProtectedAuthority(
        UniversalVtfMonotonicProtectedAuthority authority)
    {
        if (authority.Name is not ("raw" or "safe") || authority.Anchor is null)
        {
            throw new InvalidDataException(
                $"monotonic_invalid_protected_authority:{authority.Name}");
        }

        string root = RequireDirectory(authority.Root, authority.Name);
        UniversalVtfTreeSnapshot tree = UniversalVtfTreeInventory.Capture(root);
        UniversalVtfTreeInventory.RequireAnchor(tree, authority.Anchor);
        return new PreparedProtectedAuthority(authority, root, tree);
    }

    private sealed record PreparedAlternative(
        UniversalVtfMonotonicAlternativeAuthority Authority,
        string Root,
        UniversalVtfManifest Manifest,
        UniversalVtfTreeSnapshot Tree,
        IReadOnlyDictionary<string, UniversalVtfManifestRow> Rows);

    private sealed record PreparedProtectedAuthority(
        UniversalVtfMonotonicProtectedAuthority Authority,
        string Root,
        UniversalVtfTreeSnapshot Tree);
}
