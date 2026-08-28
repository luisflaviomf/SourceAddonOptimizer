namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfInventoryFailure(
    string RelativePath,
    string Stage,
    string Error);

internal sealed record UniversalVtfInventoryEntry(
    string RelativePath,
    long Bytes,
    string Sha256,
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
    IReadOnlyList<VtfResourceChunk> Resources,
    IReadOnlyList<VtfSubresourceRange> Subresources,
    IReadOnlyList<VtfByteRange> UnmappedRanges);

internal sealed record UniversalVtfInventory(
    string Root,
    int DiscoveredCount,
    IReadOnlyList<UniversalVtfInventoryEntry> Entries,
    IReadOnlyList<UniversalVtfInventoryFailure> Failures)
{
    internal bool IsComplete => Failures.Count == 0 && Entries.Count == DiscoveredCount;

    internal long VtfBytes => checked(Entries.Sum(x => x.Bytes));
}

internal sealed record UniversalVtfCorpusExpectation(
    int VtfCount,
    long VtfBytes,
    IReadOnlyDictionary<int, int> Formats,
    IReadOnlyDictionary<string, int> Versions,
    int AuthoredChains,
    int NomipFiles,
    int SingleFaceFiles,
    int SevenFaceFiles,
    int SingleFrameFiles,
    int DepthOneFiles,
    int DimensionallyIrreducible,
    int ResourceDirectoryFiles)
{
    internal void Verify(
        UniversalVtfInventory inventory,
        UniversalVtfTreeSnapshot snapshot)
    {
        ArgumentNullException.ThrowIfNull(inventory);
        ArgumentNullException.ThrowIfNull(snapshot);

        var issues = new List<string>();
        Check(issues, "complete", inventory.IsComplete, true);
        Check(issues, "vtf_count", inventory.Entries.Count, VtfCount);
        Check(issues, "vtf_bytes", inventory.VtfBytes, VtfBytes);
        CheckDictionary(
            issues,
            "formats",
            inventory.Entries.GroupBy(x => x.HighResFormat)
                .ToDictionary(x => x.Key, x => x.Count()),
            Formats);
        CheckDictionary(
            issues,
            "versions",
            inventory.Entries.GroupBy(x => $"{x.MajorVersion}.{x.MinorVersion}")
                .ToDictionary(x => x.Key, x => x.Count(), StringComparer.Ordinal),
            Versions);
        Check(issues, "authored_chains", inventory.Entries.Count(x => x.MipCount > 1), AuthoredChains);
        Check(
            issues,
            "nomip_files",
            inventory.Entries.Count(x => x.MipCount == 1 && x.IsNomip),
            NomipFiles);
        Check(issues, "single_face_files", inventory.Entries.Count(x => x.Faces == 1), SingleFaceFiles);
        Check(issues, "seven_face_files", inventory.Entries.Count(x => x.Faces == 7), SevenFaceFiles);
        Check(issues, "single_frame_files", inventory.Entries.Count(x => x.Frames == 1), SingleFrameFiles);
        Check(issues, "depth_one_files", inventory.Entries.Count(x => x.Depth == 1), DepthOneFiles);
        Check(
            issues,
            "dimensionally_irreducible",
            inventory.Entries.Count(x => x.Width == 1 && x.Height == 1),
            DimensionallyIrreducible);

        UniversalVtfInventoryEntry[] resourceFiles = inventory.Entries
            .Where(x => x.Resources.Count > 0)
            .ToArray();
        Check(issues, "resource_directory_files", resourceFiles.Length, ResourceDirectoryFiles);
        VerifyResourceCohort(issues, resourceFiles);

        try
        {
            UniversalVtfTreeInventory.RequireAnchor(
                snapshot,
                UniversalVtfCorpusExpectations.RawTreeAnchor);
        }
        catch (InvalidDataException error)
        {
            issues.Add(error.Message);
        }

        if (issues.Count > 0)
        {
            throw new InvalidDataException(
                "lvs_v1_census_drift:" + string.Join(';', issues));
        }
    }

    private static void VerifyResourceCohort(
        ICollection<string> issues,
        IReadOnlyCollection<UniversalVtfInventoryEntry> resourceFiles)
    {
        int version75 = resourceFiles.Count(entry =>
            entry.MinorVersion == 5 &&
            HasOffsetResource(entry, VtfContainerLayoutReader.LowResolutionImageResourceTag) &&
            HasOffsetResource(entry, VtfContainerLayoutReader.HighResolutionImageResourceTag));
        int version74 = resourceFiles.Count(entry =>
            entry.MinorVersion == 4 &&
            HasOffsetResource(entry, VtfContainerLayoutReader.HighResolutionImageResourceTag) &&
            entry.Resources.Any(resource =>
                resource.Tag == 0x435243 &&
                resource.InlineValue is not null &&
                resource.PayloadRange is null));
        Check(issues, "resource_v75_lrs_hrs", version75, 2);
        Check(issues, "resource_v74_hrs_inline_crc", version74, 1);
    }

    private static bool HasOffsetResource(UniversalVtfInventoryEntry entry, int tag) =>
        entry.Resources.Any(resource =>
            resource.Tag == tag &&
            resource.InlineValue is null &&
            resource.PayloadRange is not null);

    private static void Check<T>(
        ICollection<string> issues,
        string name,
        T actual,
        T expected)
        where T : IEquatable<T>
    {
        if (!actual.Equals(expected))
        {
            issues.Add($"{name}:{actual}!={expected}");
        }
    }

    private static void CheckDictionary<TKey>(
        ICollection<string> issues,
        string name,
        IReadOnlyDictionary<TKey, int> actual,
        IReadOnlyDictionary<TKey, int> expected)
        where TKey : notnull
    {
        string actualText = string.Join(
            ',',
            actual.OrderBy(x => x.Key).Select(x => $"{x.Key}={x.Value}"));
        string expectedText = string.Join(
            ',',
            expected.OrderBy(x => x.Key).Select(x => $"{x.Key}={x.Value}"));
        if (!actualText.Equals(expectedText, StringComparison.Ordinal))
        {
            issues.Add($"{name}:{actualText}!={expectedText}");
        }
    }
}

internal static class UniversalVtfCorpusExpectations
{
    internal static readonly UniversalVtfTreeAnchor RawTreeAnchor = new(
        "lvs_cars_pack_raw",
        FileCount: 3_827,
        TotalBytes: 1_441_918_651,
        TreeSha256: "5f7b31c77e34608c4a40c6b945a7ad24b151be4ad067761c24aa1f93ed908758");

    internal static readonly UniversalVtfCorpusExpectation LvsV1 = new(
        VtfCount: 1_152,
        VtfBytes: 835_331_232,
        Formats: new Dictionary<int, int> { { 13, 200 }, { 15, 952 } },
        Versions: new Dictionary<string, int>(StringComparer.Ordinal)
        {
            { "7.1", 4 },
            { "7.2", 1_145 },
            { "7.4", 1 },
            { "7.5", 2 }
        },
        AuthoredChains: 1_145,
        NomipFiles: 7,
        SingleFaceFiles: 1_151,
        SevenFaceFiles: 1,
        SingleFrameFiles: 1_152,
        DepthOneFiles: 1_152,
        DimensionallyIrreducible: 0,
        ResourceDirectoryFiles: 3);
}

internal static class UniversalVtfInventoryScanner
{
    internal static UniversalVtfInventory Scan(string rawRoot)
    {
        if (string.IsNullOrWhiteSpace(rawRoot))
        {
            throw new ArgumentException("Raw root cannot be null or whitespace.", nameof(rawRoot));
        }

        string root = Path.GetFullPath(rawRoot);
        if (!Directory.Exists(root))
        {
            throw new DirectoryNotFoundException(root);
        }

        var failures = new List<UniversalVtfInventoryFailure>();
        IReadOnlyList<string> paths = EnumerateVtfPaths(root, failures);
        var entries = new List<UniversalVtfInventoryEntry>(paths.Count);

        foreach (string relativePath in paths)
        {
            string fullPath = Path.GetFullPath(Path.Combine(
                root,
                relativePath.Replace('/', Path.DirectorySeparatorChar)));
            try
            {
                VtfContainerLayout layout = VtfContainerLayoutReader.Read(fullPath);
                entries.Add(CreateEntry(relativePath, layout));
            }
            catch (Exception error) when (IsExpectedFileFailure(error))
            {
                failures.Add(new UniversalVtfInventoryFailure(
                    relativePath,
                    "parse",
                    $"{error.GetType().Name}:{error.Message}"));
            }
        }

        return new UniversalVtfInventory(
            root,
            paths.Count,
            entries.AsReadOnly(),
            failures.AsReadOnly());
    }

    private static IReadOnlyList<string> EnumerateVtfPaths(
        string root,
        ICollection<UniversalVtfInventoryFailure> failures)
    {
        var paths = new List<string>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var pending = new Stack<string>();
        pending.Push(root);

        while (pending.Count > 0)
        {
            string directory = pending.Pop();
            string[] children;
            try
            {
                children = Directory.GetFileSystemEntries(directory);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            {
                failures.Add(new UniversalVtfInventoryFailure(
                    NormalizeRelative(root, directory),
                    "enumeration",
                    $"{error.GetType().Name}:{error.Message}"));
                continue;
            }

            foreach (string child in children)
            {
                string relativePath = NormalizeRelative(root, child);
                FileAttributes attributes;
                try
                {
                    attributes = File.GetAttributes(child);
                }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException)
                {
                    failures.Add(new UniversalVtfInventoryFailure(
                        relativePath,
                        "enumeration",
                        $"{error.GetType().Name}:{error.Message}"));
                    continue;
                }

                if ((attributes & FileAttributes.ReparsePoint) != 0)
                {
                    failures.Add(new UniversalVtfInventoryFailure(
                        relativePath,
                        "enumeration",
                        "reparse_point"));
                    continue;
                }

                if ((attributes & FileAttributes.Directory) != 0)
                {
                    pending.Push(child);
                    continue;
                }

                if (!Path.GetExtension(child).Equals(".vtf", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                if (!seen.Add(relativePath))
                {
                    failures.Add(new UniversalVtfInventoryFailure(
                        relativePath,
                        "enumeration",
                        "casefold_path_collision"));
                    continue;
                }

                paths.Add(relativePath);
            }
        }

        paths.Sort(CompareCanonicalPaths);
        return paths.AsReadOnly();
    }

    private static int CompareCanonicalPaths(string left, string right)
    {
        int folded = StringComparer.Ordinal.Compare(
            left.ToLowerInvariant(),
            right.ToLowerInvariant());
        return folded != 0
            ? folded
            : StringComparer.Ordinal.Compare(left, right);
    }

    private static UniversalVtfInventoryEntry CreateEntry(
        string relativePath,
        VtfContainerLayout layout)
    {
        var document = layout.Document;
        return new UniversalVtfInventoryEntry(
            relativePath,
            document.FileLength,
            layout.FileSha256,
            document.MajorVersion,
            document.MinorVersion,
            document.HeaderSize,
            document.Flags,
            document.Width,
            document.Height,
            document.Depth,
            document.Frames,
            document.FirstFrame,
            document.Faces,
            document.MipCount,
            document.HighResFormat,
            document.LowResFormat,
            document.LowResWidth,
            document.LowResHeight,
            document.MipCount == 1 && (document.Flags & VtfFlags.NoMip) != 0,
            layout.Resources,
            layout.Subresources,
            layout.UnmappedRanges);
    }

    private static string NormalizeRelative(string root, string path) =>
        Path.GetRelativePath(root, path).Replace('\\', '/');

    private static bool IsExpectedFileFailure(Exception error) =>
        error is IOException or UnauthorizedAccessException or InvalidDataException or
            EndOfStreamException or OverflowException or NotSupportedException or
            ArgumentException or InvalidOperationException;
}
