using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal enum Bc3EligibilityMode
{
    PhysicalOpaque,
    RawDxt1,
    SemanticDetailRgbOnly
}

internal sealed record Bc3AlphaReceipt(
    long DecodedSamples,
    int Minimum,
    int Maximum,
    int UniqueValues,
    bool FullyOpaque);

internal sealed record Bc3RawMipReceipt(
    int Level,
    int Width,
    int Height,
    int Depth,
    int PlaneCount,
    long PayloadBytes,
    string PayloadSha256,
    Bc3AlphaReceipt Alpha);

internal sealed record Bc3RawBridgeRow(
    string RelativePath,
    long RawBytes,
    string RawSha256,
    long PriorSafeSourceBytes,
    string PriorSafeSourceSha256,
    long ApprovedSafeBytes,
    string ApprovedSafeSha256,
    string ConsumerReceiptSha256,
    int RawFormat,
    Bc3EligibilityMode EligibilityMode,
    string ApprovedOutputPath,
    bool PriorSafeColorBlocksEquivalent,
    IReadOnlyList<Bc3RawMipReceipt> RawMips);

internal sealed record Bc3RawBridge(
    string Schema,
    string PriorManifestSha256,
    string RawTreeSha256,
    long RawTreeBytes,
    int RawTreeFileCount,
    string ApprovedSafeTreeSha256,
    long ApprovedSafeTreeBytes,
    int ApprovedSafeTreeFileCount,
    int PhysicalOpaqueBc3Count,
    int RawDxt1Count,
    int SemanticExceptionBc3Count,
    int EquivalentPriorSafeColorChainCount,
    IReadOnlyList<Bc3RawBridgeRow> Rows);

internal sealed record Bc3RawBridgeFrozen(string Path, string Sha256, Bc3RawBridge Bridge);

internal static class Bc3RawBridgeStore
{
    internal const string Schema = "bc3-raw-to-approved-v1";

    private static readonly JsonSerializerOptions JsonOptions = CreateJsonOptions();

    internal static Bc3RawBridgeFrozen WriteCanonical(string output, Bc3RawBridge bridge)
    {
        if (string.IsNullOrWhiteSpace(output))
        {
            throw new ArgumentException("Output path cannot be empty.", nameof(output));
        }

        Validate(bridge);
        string fullOutput = Path.GetFullPath(output);
        string? parent = Path.GetDirectoryName(fullOutput);
        if (parent is null || !Directory.Exists(parent))
        {
            throw new DirectoryNotFoundException(parent ?? fullOutput);
        }

        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(bridge, JsonOptions);
        using (var stream = new FileStream(
                   fullOutput,
                   FileMode.CreateNew,
                   FileAccess.Write,
                   FileShare.None))
        {
            stream.Write(bytes);
            stream.Flush(flushToDisk: true);
        }

        string sha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        return new Bc3RawBridgeFrozen(fullOutput, sha256, bridge);
    }

    internal static Bc3RawBridge LoadAndVerify(string path, string expectedSha256)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            throw new ArgumentException("Bridge path cannot be empty.", nameof(path));
        }

        RequireSha256(expectedSha256, nameof(expectedSha256));
        string fullPath = Path.GetFullPath(path);
        byte[] bytes = File.ReadAllBytes(fullPath);
        string actual = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        if (!actual.Equals(expectedSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException(
                $"bc3_bridge_sha256_mismatch:{actual}!={expectedSha256.ToLowerInvariant()}");
        }

        Bc3RawBridge bridge;
        try
        {
            bridge = JsonSerializer.Deserialize<Bc3RawBridge>(bytes, JsonOptions)
                ?? throw new InvalidDataException("bc3_bridge_null_document");
        }
        catch (JsonException error)
        {
            throw new InvalidDataException("bc3_bridge_invalid_json", error);
        }

        Validate(bridge);
        byte[] canonical = JsonSerializer.SerializeToUtf8Bytes(bridge, JsonOptions);
        if (!bytes.AsSpan().SequenceEqual(canonical))
        {
            throw new InvalidDataException("bc3_bridge_noncanonical_json");
        }

        return bridge;
    }

    internal static void Validate(Bc3RawBridge bridge)
    {
        ArgumentNullException.ThrowIfNull(bridge);
        if (!bridge.Schema.Equals(Schema, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"bc3_bridge_schema:{bridge.Schema}");
        }

        RequireSha256(bridge.PriorManifestSha256, nameof(bridge.PriorManifestSha256));
        RequireSha256(bridge.RawTreeSha256, nameof(bridge.RawTreeSha256));
        RequireSha256(bridge.ApprovedSafeTreeSha256, nameof(bridge.ApprovedSafeTreeSha256));
        if (bridge.RawTreeBytes < 0 || bridge.RawTreeFileCount < 0 ||
            bridge.ApprovedSafeTreeBytes < 0 || bridge.ApprovedSafeTreeFileCount < 0)
        {
            throw new InvalidDataException("bc3_bridge_negative_tree_accounting");
        }

        if (bridge.Rows is null)
        {
            throw new InvalidDataException("bc3_bridge_missing_rows");
        }

        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        string? previous = null;
        foreach (Bc3RawBridgeRow row in bridge.Rows)
        {
            ValidatePath(row.RelativePath);
            ValidatePath(row.ApprovedOutputPath);
            if (!row.RelativePath.Equals(row.ApprovedOutputPath, StringComparison.Ordinal))
            {
                throw new InvalidDataException($"bc3_bridge_output_path_mismatch:{row.RelativePath}");
            }

            if (!seen.Add(row.RelativePath))
            {
                throw new InvalidDataException($"bc3_bridge_duplicate_path:{row.RelativePath}");
            }

            if (previous is not null && CompareCanonical(previous, row.RelativePath) >= 0)
            {
                throw new InvalidDataException("bc3_bridge_rows_not_canonically_sorted");
            }

            previous = row.RelativePath;
            RequireSha256(row.RawSha256, nameof(row.RawSha256));
            RequireSha256(row.PriorSafeSourceSha256, nameof(row.PriorSafeSourceSha256));
            RequireSha256(row.ApprovedSafeSha256, nameof(row.ApprovedSafeSha256));
            RequireSha256(row.ConsumerReceiptSha256, nameof(row.ConsumerReceiptSha256));
            if (row.RawBytes <= 0 || row.PriorSafeSourceBytes <= 0 || row.ApprovedSafeBytes <= 0)
            {
                throw new InvalidDataException($"bc3_bridge_nonpositive_bytes:{row.RelativePath}");
            }

            if (row.RawFormat is not (13 or 15))
            {
                throw new InvalidDataException($"bc3_bridge_raw_format:{row.RelativePath}:{row.RawFormat}");
            }

            ValidateMode(row);
            ValidateMips(row);
        }

        int physical = bridge.Rows.Count(row =>
            row.RawFormat == 15 && row.EligibilityMode == Bc3EligibilityMode.PhysicalOpaque);
        int dxt1 = bridge.Rows.Count(row =>
            row.RawFormat == 13 && row.EligibilityMode == Bc3EligibilityMode.RawDxt1);
        int semantic = bridge.Rows.Count(row =>
            row.RawFormat == 15 && row.EligibilityMode == Bc3EligibilityMode.SemanticDetailRgbOnly);
        int equivalent = bridge.Rows.Count(row => row.PriorSafeColorBlocksEquivalent);
        if (physical != bridge.PhysicalOpaqueBc3Count ||
            dxt1 != bridge.RawDxt1Count ||
            semantic != bridge.SemanticExceptionBc3Count ||
            equivalent != bridge.EquivalentPriorSafeColorChainCount ||
            physical + dxt1 + semantic != bridge.Rows.Count)
        {
            throw new InvalidDataException("bc3_bridge_partition_mismatch");
        }
    }

    private static void ValidateMode(Bc3RawBridgeRow row)
    {
        bool valid = row.EligibilityMode switch
        {
            Bc3EligibilityMode.PhysicalOpaque => row.RawFormat == 15,
            Bc3EligibilityMode.RawDxt1 => row.RawFormat == 13,
            Bc3EligibilityMode.SemanticDetailRgbOnly => row.RawFormat == 15,
            _ => false
        };
        if (!valid)
        {
            throw new InvalidDataException($"bc3_bridge_mode_format_mismatch:{row.RelativePath}");
        }
    }

    private static void ValidateMips(Bc3RawBridgeRow row)
    {
        if (row.RawMips is null || row.RawMips.Count == 0)
        {
            throw new InvalidDataException($"bc3_bridge_missing_level1_plus:{row.RelativePath}");
        }

        for (int index = 0; index < row.RawMips.Count; index++)
        {
            Bc3RawMipReceipt mip = row.RawMips[index];
            if (mip.Level != index + 1 || mip.Width <= 0 || mip.Height <= 0 ||
                mip.Depth <= 0 || mip.PlaneCount <= 0 || mip.PayloadBytes <= 0)
            {
                throw new InvalidDataException($"bc3_bridge_invalid_mip:{row.RelativePath}:{mip.Level}");
            }

            RequireSha256(mip.PayloadSha256, nameof(mip.PayloadSha256));
            Bc3AlphaReceipt alpha = mip.Alpha ??
                throw new InvalidDataException($"bc3_bridge_missing_alpha:{row.RelativePath}:{mip.Level}");
            if (alpha.DecodedSamples <= 0 || alpha.Minimum is < 0 or > 255 ||
                alpha.Maximum is < 0 or > 255 || alpha.Minimum > alpha.Maximum ||
                alpha.UniqueValues is < 1 or > 256 ||
                alpha.FullyOpaque != (alpha.Minimum == 255 && alpha.Maximum == 255))
            {
                throw new InvalidDataException($"bc3_bridge_invalid_alpha:{row.RelativePath}:{mip.Level}");
            }
        }
    }

    private static void ValidatePath(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || Path.IsPathRooted(path) ||
            path.Contains('\\') || path != path.ToLowerInvariant() ||
            path.Split('/').Any(segment => string.IsNullOrEmpty(segment) || segment is "." or ".."))
        {
            throw new InvalidDataException($"bc3_bridge_invalid_path:{path}");
        }
    }

    private static int CompareCanonical(string left, string right)
    {
        int folded = StringComparer.Ordinal.Compare(left.ToLowerInvariant(), right.ToLowerInvariant());
        return folded != 0 ? folded : StringComparer.Ordinal.Compare(left, right);
    }

    internal static void RequireSha256(string value, string field)
    {
        if (value is null || value.Length != 64 ||
            value.Any(character => !Uri.IsHexDigit(character)))
        {
            throw new InvalidDataException($"invalid_sha256:{field}");
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
}
