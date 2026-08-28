using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfManifestTree(
    string Authority,
    int FileCount,
    long Bytes,
    string TreeSha256);

internal sealed record UniversalVtfManifestRouteAttempt(
    int Level,
    UniversalVtfFilterRoute Route,
    bool StructurallyValid,
    string StructuralReason,
    UniversalVtfMetricSet? Metrics);

internal sealed record UniversalVtfManifestRow(
    string RelativePath,
    string Family,
    IReadOnlyList<string> SemanticClasses,
    string ConsumerProofSha256,
    int RawWidth,
    int RawHeight,
    int RawDepth,
    int RawFrames,
    int RawFaces,
    int RawMipCount,
    int RawFormat,
    int FinalWidth,
    int FinalHeight,
    int FinalDepth,
    int FinalMipCount,
    int FinalFormat,
    bool Authored,
    bool Generated,
    int SelectedLevel,
    int ConservativeLevel,
    int BalancedLevel,
    int AggressiveLevel,
    UniversalVtfFilterRoute Route,
    IReadOnlyDictionary<UniversalVtfProfileKind, IReadOnlyList<UniversalVtfProfileAttempt>> ProfileAttempts,
    IReadOnlyList<UniversalVtfManifestRouteAttempt> RouteAttempts,
    UniversalVtfGateResult? WorstGate,
    long RawBytes,
    string RawSha256,
    long SafeBytes,
    string SafeSha256,
    long SpatialBytes,
    string SpatialSha256,
    long FinalBytes,
    string FinalSha256,
    bool MandatoryFloorApplied,
    bool ByteFloor,
    string? Blocker,
    Bc3EligibilityMode? BcEligibilityMode,
    string? BcReceiptSha256,
    bool LowResolutionImageRegenerated,
    int ResourceCount)
{
    public long ResolutionSavedBytes => checked(RawBytes - SpatialBytes);

    public long FormatSavedBytes => checked(SpatialBytes - FinalBytes);

    public long TotalSavedBytes => checked(RawBytes - FinalBytes);

    public long SafeToFinalSavedBytes => checked(SafeBytes - FinalBytes);
}

internal sealed record UniversalVtfBuildReceipt(
    UniversalVtfProfileKind Profile,
    string PolicyFingerprint,
    string FixtureSha256,
    string ToolchainSha256,
    string BcBridgeSha256,
    string? RuntimeCapsSha256,
    UniversalVtfManifestTree RawTree,
    UniversalVtfManifestTree SafeTree,
    UniversalVtfManifestTree FinalTree,
    IReadOnlyList<UniversalVtfManifestRow> Rows);

internal sealed record UniversalVtfSavings(
    long SavedBytes,
    double SavedMb,
    double SavedMib,
    double Percent);

internal sealed record UniversalVtfCorpusAccounting(
    long RawBytes,
    long SafeBytes,
    long FinalBytes,
    UniversalVtfSavings RawToFinal,
    UniversalVtfSavings SafeToFinal);

internal sealed record UniversalVtfAssetAccounting(
    long RawBytes,
    long SafeBytes,
    long SpatialBytes,
    long FinalBytes,
    long ResolutionSavedBytes,
    long FormatSavedBytes,
    UniversalVtfSavings RawToFinal,
    UniversalVtfSavings SafeToFinal);

internal sealed record UniversalVtfManifestAggregate(
    UniversalVtfCorpusAccounting Corpus,
    UniversalVtfAssetAccounting Vtf,
    IReadOnlyDictionary<int, int> Levels,
    IReadOnlyDictionary<string, int> Classes,
    IReadOnlyDictionary<string, int> Families,
    int Authored,
    int Generated,
    int ByteFloor,
    int Blocked,
    int BcReceiptRows);

internal sealed record UniversalVtfManifest(
    string Schema,
    UniversalVtfProfileKind Profile,
    string PolicyFingerprint,
    string FixtureSha256,
    string ToolchainSha256,
    string BcBridgeSha256,
    string? RuntimeCapsSha256,
    UniversalVtfManifestTree RawTree,
    UniversalVtfManifestTree SafeTree,
    UniversalVtfManifestTree FinalTree,
    UniversalVtfManifestAggregate Aggregate,
    IReadOnlyList<UniversalVtfManifestRow> Rows)
{
    internal UniversalVtfManifestRow Row(string relativePath) => Rows.Single(row =>
        row.RelativePath.Equals(relativePath, StringComparison.OrdinalIgnoreCase));
}

internal sealed record UniversalVtfManifestFrozen(
    string Path,
    string Sha256,
    UniversalVtfManifest Manifest);

internal static class UniversalVtfManifestWriter
{
    internal const string Schema = "universal-vtf-manifest-v1";

    private static readonly JsonSerializerOptions JsonOptions = CreateJsonOptions();

    internal static void Validate(UniversalVtfBuildReceipt receipt)
    {
        ArgumentNullException.ThrowIfNull(receipt);
        RequireSha(receipt.PolicyFingerprint, nameof(receipt.PolicyFingerprint));
        RequireSha(receipt.FixtureSha256, nameof(receipt.FixtureSha256));
        RequireSha(receipt.ToolchainSha256, nameof(receipt.ToolchainSha256));
        RequireSha(receipt.BcBridgeSha256, nameof(receipt.BcBridgeSha256));
        if (receipt.RuntimeCapsSha256 is not null)
        {
            RequireSha(receipt.RuntimeCapsSha256, nameof(receipt.RuntimeCapsSha256));
        }

        ValidateTree(receipt.RawTree, "raw");
        ValidateTree(receipt.SafeTree, "safe");
        ValidateTree(receipt.FinalTree, "final");
        if (receipt.Rows is null || receipt.Rows.Count == 0)
        {
            throw new InvalidDataException("manifest_has_no_rows");
        }

        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        string? previous = null;
        foreach (UniversalVtfManifestRow row in receipt.Rows)
        {
            ValidatePath(row.RelativePath);
            if (!seen.Add(row.RelativePath))
            {
                throw new InvalidDataException($"manifest_duplicate_path:{row.RelativePath}");
            }

            if (previous is not null &&
                StringComparer.Ordinal.Compare(previous, row.RelativePath) >= 0)
            {
                throw new InvalidDataException("manifest_rows_not_canonically_sorted");
            }

            previous = row.RelativePath;
            ValidateRow(receipt, row);
        }

        long rawVtf = checked(receipt.Rows.Sum(row => row.RawBytes));
        long safeVtf = checked(receipt.Rows.Sum(row => row.SafeBytes));
        long finalVtf = checked(receipt.Rows.Sum(row => row.FinalBytes));
        if (receipt.RawTree.Bytes < rawVtf || receipt.SafeTree.Bytes < safeVtf ||
            receipt.FinalTree.Bytes < finalVtf ||
            receipt.RawTree.FileCount < receipt.Rows.Count ||
            receipt.SafeTree.FileCount < receipt.Rows.Count ||
            receipt.FinalTree.FileCount < receipt.Rows.Count)
        {
            throw new InvalidDataException("manifest_physical_accounting_exceeds_tree");
        }
    }

    internal static async Task<UniversalVtfManifestFrozen> WriteAsync(
        string path,
        UniversalVtfBuildReceipt buildReceipt,
        CancellationToken cancellationToken)
    {
        Validate(buildReceipt);
        UniversalVtfManifest manifest = BuildManifest(buildReceipt);
        return await WriteManifestAsync(path, manifest, cancellationToken).ConfigureAwait(false);
    }

    internal static async Task<UniversalVtfManifestFrozen> WriteManifestAsync(
        string path,
        UniversalVtfManifest manifest,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new ArgumentException("Manifest path cannot be empty.", nameof(path));
        }

        UniversalVtfManifest canonicalManifest = ValidateManifest(manifest);
        byte[] bytes = SerializeCanonical(canonicalManifest);
        string fullPath = Path.GetFullPath(path);
        string? parent = Path.GetDirectoryName(fullPath);
        if (parent is null || !Directory.Exists(parent))
        {
            throw new DirectoryNotFoundException(parent ?? fullPath);
        }

        cancellationToken.ThrowIfCancellationRequested();
        await using (var stream = new FileStream(
                         fullPath,
                         FileMode.CreateNew,
                         FileAccess.Write,
                         FileShare.None,
                         1024 * 1024,
                         FileOptions.Asynchronous | FileOptions.WriteThrough))
        {
            await stream.WriteAsync(bytes, cancellationToken).ConfigureAwait(false);
            await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
            stream.Flush(flushToDisk: true);
        }

        string sha = Sha256(bytes);
        return new UniversalVtfManifestFrozen(fullPath, sha, canonicalManifest);
    }

    internal static UniversalVtfManifest BuildManifest(UniversalVtfBuildReceipt receipt)
    {
        Validate(receipt);
        return new UniversalVtfManifest(
            Schema,
            receipt.Profile,
            receipt.PolicyFingerprint.ToLowerInvariant(),
            receipt.FixtureSha256.ToLowerInvariant(),
            receipt.ToolchainSha256.ToLowerInvariant(),
            receipt.BcBridgeSha256.ToLowerInvariant(),
            receipt.RuntimeCapsSha256?.ToLowerInvariant(),
            receipt.RawTree,
            receipt.SafeTree,
            receipt.FinalTree,
            BuildAggregate(receipt),
            receipt.Rows);
    }

    internal static UniversalVtfManifest ValidateManifest(UniversalVtfManifest manifest)
    {
        ArgumentNullException.ThrowIfNull(manifest);
        if (!manifest.Schema.Equals(Schema, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"manifest_schema:{manifest.Schema}");
        }

        var receipt = new UniversalVtfBuildReceipt(
            manifest.Profile,
            manifest.PolicyFingerprint,
            manifest.FixtureSha256,
            manifest.ToolchainSha256,
            manifest.BcBridgeSha256,
            manifest.RuntimeCapsSha256,
            manifest.RawTree,
            manifest.SafeTree,
            manifest.FinalTree,
            manifest.Rows);
        UniversalVtfManifest expected = BuildManifest(receipt);
        if (!SerializeCanonical(manifest).AsSpan().SequenceEqual(SerializeCanonical(expected)))
        {
            throw new InvalidDataException("manifest_aggregate_or_canonical_fields_mismatch");
        }

        return expected;
    }

    private static UniversalVtfManifestAggregate BuildAggregate(
        UniversalVtfBuildReceipt receipt)
    {
        long rawVtf = checked(receipt.Rows.Sum(row => row.RawBytes));
        long safeVtf = checked(receipt.Rows.Sum(row => row.SafeBytes));
        long spatialVtf = checked(receipt.Rows.Sum(row => row.SpatialBytes));
        long finalVtf = checked(receipt.Rows.Sum(row => row.FinalBytes));
        var levels = new SortedDictionary<int, int>();
        foreach (IGrouping<int, UniversalVtfManifestRow> group in receipt.Rows.GroupBy(row => row.SelectedLevel))
        {
            levels.Add(group.Key, group.Count());
        }

        var classes = new SortedDictionary<string, int>(StringComparer.Ordinal);
        foreach (string semanticClass in receipt.Rows.SelectMany(row => row.SemanticClasses))
        {
            classes.TryGetValue(semanticClass, out int count);
            classes[semanticClass] = checked(count + 1);
        }

        var families = new SortedDictionary<string, int>(StringComparer.Ordinal);
        foreach (IGrouping<string, UniversalVtfManifestRow> group in receipt.Rows.GroupBy(
                     row => row.Family,
                     StringComparer.Ordinal))
        {
            families.Add(group.Key, group.Count());
        }

        return new UniversalVtfManifestAggregate(
            new UniversalVtfCorpusAccounting(
                receipt.RawTree.Bytes,
                receipt.SafeTree.Bytes,
                receipt.FinalTree.Bytes,
                Savings(receipt.RawTree.Bytes, receipt.FinalTree.Bytes),
                Savings(receipt.SafeTree.Bytes, receipt.FinalTree.Bytes)),
            new UniversalVtfAssetAccounting(
                rawVtf,
                safeVtf,
                spatialVtf,
                finalVtf,
                checked(rawVtf - spatialVtf),
                checked(spatialVtf - finalVtf),
                Savings(rawVtf, finalVtf),
                Savings(safeVtf, finalVtf)),
            levels,
            classes,
            families,
            receipt.Rows.Count(row => row.Authored),
            receipt.Rows.Count(row => row.Generated),
            receipt.Rows.Count(row => row.ByteFloor),
            receipt.Rows.Count(row => row.Blocker is not null),
            receipt.Rows.Count(row => row.BcEligibilityMode is not null));
    }

    private static UniversalVtfSavings Savings(long source, long final)
    {
        long saved = checked(source - final);
        double percent = source == 0 ? 0 : saved * 100d / source;
        return new UniversalVtfSavings(
            saved,
            saved / 1_000_000d,
            saved / 1_048_576d,
            percent);
    }

    private static void ValidateTree(UniversalVtfManifestTree tree, string expectedAuthority)
    {
        ArgumentNullException.ThrowIfNull(tree);
        if (!tree.Authority.Equals(expectedAuthority, StringComparison.Ordinal) ||
            tree.FileCount < 0 || tree.Bytes < 0)
        {
            throw new InvalidDataException($"manifest_invalid_tree:{expectedAuthority}");
        }

        RequireSha(tree.TreeSha256, $"{expectedAuthority}TreeSha256");
    }

    private static void ValidateRow(
        UniversalVtfBuildReceipt receipt,
        UniversalVtfManifestRow row)
    {
        if (string.IsNullOrWhiteSpace(row.Family) || row.Family != row.Family.ToLowerInvariant() ||
            row.SemanticClasses is null || row.SemanticClasses.Count == 0 ||
            !row.SemanticClasses.SequenceEqual(
                row.SemanticClasses.OrderBy(value => value, StringComparer.Ordinal),
                StringComparer.Ordinal) ||
            row.SemanticClasses.Distinct(StringComparer.Ordinal).Count() != row.SemanticClasses.Count)
        {
            throw new InvalidDataException($"manifest_invalid_classification:{row.RelativePath}");
        }

        RequireSha(row.ConsumerProofSha256, nameof(row.ConsumerProofSha256));
        RequireSha(row.RawSha256, nameof(row.RawSha256));
        RequireSha(row.SafeSha256, nameof(row.SafeSha256));
        RequireSha(row.SpatialSha256, nameof(row.SpatialSha256));
        RequireSha(row.FinalSha256, nameof(row.FinalSha256));
        if (row.RawBytes <= 0 || row.SafeBytes <= 0 || row.SpatialBytes <= 0 || row.FinalBytes <= 0 ||
            row.SpatialBytes > row.RawBytes || row.FinalBytes > row.SpatialBytes ||
            row.RawWidth <= 0 || row.RawHeight <= 0 || row.RawDepth <= 0 ||
            row.RawFrames <= 0 || row.RawFaces <= 0 || row.RawMipCount <= 0 ||
            row.RawFormat is not (13 or 15) || row.FinalFormat is not (13 or 15))
        {
            throw new InvalidDataException($"manifest_invalid_physical_row:{row.RelativePath}");
        }

        if (row.Authored == row.Generated || row.SelectedLevel < 1 || !row.MandatoryFloorApplied ||
            !(row.ConservativeLevel <= row.BalancedLevel &&
              row.BalancedLevel <= row.AggressiveLevel))
        {
            throw new InvalidDataException($"manifest_invalid_level_policy:{row.RelativePath}");
        }

        int profileLevel = receipt.Profile switch
        {
            UniversalVtfProfileKind.Conservative => row.ConservativeLevel,
            UniversalVtfProfileKind.Balanced => row.BalancedLevel,
            UniversalVtfProfileKind.Aggressive => row.AggressiveLevel,
            _ => throw new InvalidDataException("manifest_invalid_profile")
        };
        if (row.SelectedLevel != profileLevel ||
            row.FinalWidth != Math.Max(1, row.RawWidth >> row.SelectedLevel) ||
            row.FinalHeight != Math.Max(1, row.RawHeight >> row.SelectedLevel))
        {
            throw new InvalidDataException($"manifest_selected_level_mismatch:{row.RelativePath}");
        }

        int expectedDepth = row.Authored
            ? Math.Max(1, row.RawDepth >> row.SelectedLevel)
            : row.RawDepth;
        int expectedMips = row.Authored
            ? row.RawMipCount - row.SelectedLevel
            : 1;
        if (row.FinalDepth != expectedDepth || row.FinalMipCount != expectedMips ||
            row.FinalMipCount < 1 || row.ResourceCount < 0)
        {
            throw new InvalidDataException($"manifest_final_shape_mismatch:{row.RelativePath}");
        }

        if (row.BcEligibilityMode is null)
        {
            if (row.BcReceiptSha256 is not null || row.FormatSavedBytes != 0 ||
                row.SpatialBytes != row.FinalBytes ||
                !row.SpatialSha256.Equals(row.FinalSha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException($"manifest_unreceipted_format_change:{row.RelativePath}");
            }
        }
        else
        {
            RequireSha(row.BcReceiptSha256!, nameof(row.BcReceiptSha256));
            if (!row.BcReceiptSha256!.Equals(receipt.BcBridgeSha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException($"manifest_bc_receipt_hash_mismatch:{row.RelativePath}");
            }

            if (row.BcEligibilityMode == Bc3EligibilityMode.RawDxt1 &&
                (row.RawFormat != 13 || row.FinalFormat != 13 || row.FormatSavedBytes != 0 ||
                 row.SpatialBytes != row.FinalBytes ||
                 !row.SpatialSha256.Equals(row.FinalSha256, StringComparison.OrdinalIgnoreCase)))
            {
                throw new InvalidDataException($"manifest_raw_dxt1_format_drift:{row.RelativePath}");
            }

            if (row.BcEligibilityMode is Bc3EligibilityMode.PhysicalOpaque or
                Bc3EligibilityMode.SemanticDetailRgbOnly &&
                (row.RawFormat != 15 || row.FinalFormat != 13 || row.FormatSavedBytes <= 0))
            {
                throw new InvalidDataException($"manifest_bc3_conversion_format:{row.RelativePath}");
            }
        }

        if (row.ResolutionSavedBytes < 0 || row.FormatSavedBytes < 0 ||
            row.TotalSavedBytes != checked(row.ResolutionSavedBytes + row.FormatSavedBytes))
        {
            throw new InvalidDataException($"manifest_savings_mismatch:{row.RelativePath}");
        }
    }

    private static void ValidatePath(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || Path.IsPathRooted(path) || path.Contains('\\') ||
            path != path.ToLowerInvariant() ||
            path.Split('/').Any(segment => string.IsNullOrEmpty(segment) || segment is "." or ".."))
        {
            throw new InvalidDataException($"manifest_invalid_path:{path}");
        }
    }

    private static void RequireSha(string value, string field)
    {
        if (value is null || value.Length != 64 || value.Any(character => !Uri.IsHexDigit(character)))
        {
            throw new InvalidDataException($"manifest_invalid_sha256:{field}");
        }
    }

    internal static byte[] SerializeCanonical(UniversalVtfManifest manifest)
    {
        byte[] serialized = JsonSerializer.SerializeToUtf8Bytes(manifest, JsonOptions);
        using JsonDocument document = JsonDocument.Parse(serialized);
        using var output = new MemoryStream();
        using (var writer = new Utf8JsonWriter(output))
        {
            WriteCanonical(writer, document.RootElement);
        }

        return output.ToArray();
    }

    private static void WriteCanonical(Utf8JsonWriter writer, JsonElement element)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                foreach (JsonProperty property in element.EnumerateObject()
                             .OrderBy(property => property.Name, StringComparer.Ordinal))
                {
                    writer.WritePropertyName(property.Name);
                    WriteCanonical(writer, property.Value);
                }

                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (JsonElement item in element.EnumerateArray())
                {
                    WriteCanonical(writer, item);
                }

                writer.WriteEndArray();
                break;
            case JsonValueKind.String:
                writer.WriteStringValue(element.GetString());
                break;
            case JsonValueKind.Number:
                element.WriteTo(writer);
                break;
            case JsonValueKind.True:
                writer.WriteBooleanValue(true);
                break;
            case JsonValueKind.False:
                writer.WriteBooleanValue(false);
                break;
            case JsonValueKind.Null:
                writer.WriteNullValue();
                break;
            default:
                throw new InvalidDataException($"manifest_unsupported_json:{element.ValueKind}");
        }
    }

    internal static JsonSerializerOptions SerializerOptions => JsonOptions;

    private static JsonSerializerOptions CreateJsonOptions()
    {
        var options = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            PropertyNameCaseInsensitive = false,
            WriteIndented = false,
            NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals
        };
        options.Converters.Add(new JsonStringEnumConverter(JsonNamingPolicy.CamelCase));
        return options;
    }

    private static string Sha256(byte[] bytes) =>
        Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
}

internal static class UniversalVtfManifestStore
{
    internal static UniversalVtfManifest Load(string path)
    {
        byte[] bytes = File.ReadAllBytes(Path.GetFullPath(path));
        string hash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        return Load(path, hash);
    }

    internal static UniversalVtfManifest Load(string path, string expectedSha256)
    {
        Bc3RawBridgeStore.RequireSha256(expectedSha256, nameof(expectedSha256));
        byte[] bytes = File.ReadAllBytes(Path.GetFullPath(path));
        string actual = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        if (!actual.Equals(expectedSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException(
                $"manifest_sha256_mismatch:{actual}!={expectedSha256.ToLowerInvariant()}");
        }

        UniversalVtfManifest manifest;
        try
        {
            manifest = JsonSerializer.Deserialize<UniversalVtfManifest>(
                           bytes,
                           UniversalVtfManifestWriter.SerializerOptions)
                       ?? throw new InvalidDataException("manifest_null_document");
        }
        catch (JsonException error)
        {
            throw new InvalidDataException("manifest_invalid_json", error);
        }

        UniversalVtfManifest validated = UniversalVtfManifestWriter.ValidateManifest(manifest);
        byte[] canonical = UniversalVtfManifestWriter.SerializeCanonical(validated);
        if (!bytes.AsSpan().SequenceEqual(canonical))
        {
            throw new InvalidDataException("manifest_noncanonical_json");
        }

        return validated;
    }
}
