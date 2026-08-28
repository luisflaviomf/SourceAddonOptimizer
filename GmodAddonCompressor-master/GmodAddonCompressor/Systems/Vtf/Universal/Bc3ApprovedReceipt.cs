using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal static class Bc3ApprovedReceipt
{
    internal const string RequiredPriorManifestSha256 =
        "6eda827ee1fb1bddb85a1aabeea1a0181722947139bc4d58776a529c15d14b15";

    private const string PriorSchema = "gmod-corpus-bc3-to-bc1-candidate-v1";
    private const int ExpectedRows = 193;
    private const int ExpectedPhysicalOpaqueBc3 = 155;
    private const int ExpectedRawDxt1 = 32;
    private const int ExpectedSemanticExceptions = 6;

    private static readonly HashSet<string> SemanticExceptionPaths = new(
        new[]
        {
            "materials/models/diggercars/lambo_countach/int_seat_occ.vtf",
            "materials/models/diggercars/lambo_countach/skin.vtf",
            "materials/models/diggercars/mazda_miata/skin.vtf",
            "materials/models/diggercars/toyota_supra/skin.vtf",
            "materials/models/diggercars/toyota_supra/skin2.vtf",
            "materials/models/diggercars/toyota_supra/skin3.vtf"
        },
        StringComparer.Ordinal);

    internal static Bc3RawBridge Build(
        UniversalVtfInventory rawInventory,
        string priorManifest,
        UniversalVtfTreeSnapshot safeTree)
    {
        UniversalVtfTreeSnapshot rawTree = UniversalVtfTreeInventory.Capture(rawInventory.Root);
        return Build(rawInventory, priorManifest, safeTree, rawTree);
    }

    internal static Bc3RawBridge Build(
        UniversalVtfInventory rawInventory,
        string priorManifest,
        UniversalVtfTreeSnapshot safeTree,
        UniversalVtfTreeSnapshot rawTree)
    {
        ArgumentNullException.ThrowIfNull(rawInventory);
        ArgumentNullException.ThrowIfNull(safeTree);
        ArgumentNullException.ThrowIfNull(rawTree);
        if (string.IsNullOrWhiteSpace(priorManifest))
        {
            throw new ArgumentException("Prior manifest path cannot be empty.", nameof(priorManifest));
        }

        UniversalVtfCorpusExpectations.LvsV1.Verify(rawInventory, rawTree);
        byte[] manifestBytes = File.ReadAllBytes(Path.GetFullPath(priorManifest));
        string manifestSha256 = Sha256(manifestBytes);
        if (!manifestSha256.Equals(RequiredPriorManifestSha256, StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                $"prior_bc3_manifest_sha256:{manifestSha256}!={RequiredPriorManifestSha256}");
        }

        using JsonDocument document = JsonDocument.Parse(manifestBytes);
        JsonElement root = document.RootElement;
        RequireString(root, "schema", PriorSchema);
        JsonElement publicationCandidate = RequireObject(
            RequireObject(root, "publication"),
            "candidate");
        RequireTreeMatchesManifest(safeTree, publicationCandidate);

        JsonElement closure = RequireObject(root, "consumer_closure_scope");
        if (!RequireBoolean(closure, "complete_within_frozen_addon_and_audited_lvs_framework"))
        {
            throw new InvalidDataException("prior_consumer_closure_incomplete");
        }

        JsonElement selected = RequireArray(RequireObject(root, "classification"), "selected");
        JsonElement audits = RequireArray(root, "texture_audits");
        if (selected.GetArrayLength() != ExpectedRows || audits.GetArrayLength() != ExpectedRows)
        {
            throw new InvalidDataException(
                $"prior_bc3_receipt_count:{selected.GetArrayLength()}/{audits.GetArrayLength()}!={ExpectedRows}");
        }

        Dictionary<string, JsonElement> auditByPath = IndexAudits(audits);
        Dictionary<string, UniversalVtfInventoryEntry> rawByPath = rawInventory.Entries
            .ToDictionary(entry => entry.RelativePath, StringComparer.OrdinalIgnoreCase);
        Dictionary<string, UniversalVtfTreeFile> safeByPath = safeTree.Files
            .ToDictionary(entry => entry.RelativePath, StringComparer.OrdinalIgnoreCase);

        var rows = new List<Bc3RawBridgeRow>(ExpectedRows);
        var selectedPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (JsonElement priorRow in selected.EnumerateArray())
        {
            string path = RequireString(priorRow, "path").ToLowerInvariant();
            if (!selectedPaths.Add(path))
            {
                throw new InvalidDataException($"prior_bc3_duplicate_path:{path}");
            }

            if (!rawByPath.TryGetValue(path, out UniversalVtfInventoryEntry? rawEntry))
            {
                throw new InvalidDataException($"prior_bc3_path_missing_from_raw:{path}");
            }

            if (!safeByPath.TryGetValue(path, out UniversalVtfTreeFile? approvedSafe))
            {
                throw new InvalidDataException($"prior_bc3_path_missing_from_safe:{path}");
            }

            if (!auditByPath.TryGetValue(path, out JsonElement audit))
            {
                throw new InvalidDataException($"prior_bc3_path_missing_audit:{path}");
            }

            VerifySelectedAuditAgreement(path, priorRow, audit);
            long priorSourceBytes = RequireInt64(priorRow, "source_bytes");
            string priorSourceSha = RequireSha(priorRow, "source_sha256");
            long approvedBytes = RequireInt64(priorRow, "candidate_bytes");
            string approvedSha = RequireSha(priorRow, "candidate_sha256");
            if (approvedSafe.Length != approvedBytes ||
                !approvedSafe.Sha256.Equals(approvedSha, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException($"approved_safe_file_drift:{path}");
            }

            Bc3EligibilityMode mode = ResolveMode(path, rawEntry, priorRow);
            IReadOnlyList<Bc3RawMipReceipt> rawMips = BuildRawMipReceipts(rawInventory.Root, rawEntry);
            VerifyAlphaEligibility(path, mode, rawMips);
            string consumerReceiptSha = mode == Bc3EligibilityMode.SemanticDetailRgbOnly
                ? VerifyAndHashSemanticReceipt(path, priorSourceSha, audit, safeByPath)
                : HashPhysicalReceipt(path, closure);
            bool equivalent = mode == Bc3EligibilityMode.PhysicalOpaque &&
                              HasEquivalentPriorSafeColorChain(
                                  rawInventory.Root,
                                  rawEntry,
                                  safeTree.Root,
                                  path);

            rows.Add(new Bc3RawBridgeRow(
                path,
                rawEntry.Bytes,
                rawEntry.Sha256,
                priorSourceBytes,
                priorSourceSha,
                approvedBytes,
                approvedSha,
                consumerReceiptSha,
                rawEntry.HighResFormat,
                mode,
                path,
                equivalent,
                rawMips));
        }

        if (auditByPath.Count != selectedPaths.Count ||
            auditByPath.Keys.Any(path => !selectedPaths.Contains(path)))
        {
            throw new InvalidDataException("prior_bc3_selected_audit_path_set_mismatch");
        }

        rows.Sort((left, right) => StringComparer.Ordinal.Compare(left.RelativePath, right.RelativePath));
        var bridge = new Bc3RawBridge(
            Bc3RawBridgeStore.Schema,
            manifestSha256,
            rawTree.TreeSha256,
            rawTree.TotalBytes,
            rawTree.Files.Count,
            safeTree.TreeSha256,
            safeTree.TotalBytes,
            safeTree.Files.Count,
            rows.Count(row => row.EligibilityMode == Bc3EligibilityMode.PhysicalOpaque),
            rows.Count(row => row.EligibilityMode == Bc3EligibilityMode.RawDxt1),
            rows.Count(row => row.EligibilityMode == Bc3EligibilityMode.SemanticDetailRgbOnly),
            rows.Count(row => row.PriorSafeColorBlocksEquivalent),
            rows.AsReadOnly());

        ValidateFrozenCohort(bridge);
        ValidateRawAuthority(
            bridge,
            rawByPath.ToDictionary(
                pair => pair.Key,
                pair => (pair.Value.Bytes, pair.Value.Sha256),
                StringComparer.OrdinalIgnoreCase));
        Bc3RawBridgeStore.Validate(bridge);
        return bridge;
    }

    internal static void ValidateRawAuthority(
        Bc3RawBridge bridge,
        IReadOnlyDictionary<string, (long Bytes, string Sha256)> rawFiles)
    {
        ArgumentNullException.ThrowIfNull(bridge);
        ArgumentNullException.ThrowIfNull(rawFiles);
        foreach (Bc3RawBridgeRow row in bridge.Rows)
        {
            if (!rawFiles.TryGetValue(row.RelativePath, out var actual) ||
                actual.Bytes != row.RawBytes ||
                !actual.Sha256.Equals(row.RawSha256, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException($"bc3_bridge_raw_authority_mismatch:{row.RelativePath}");
            }
        }
    }

    private static void ValidateFrozenCohort(Bc3RawBridge bridge)
    {
        if (bridge.Rows.Count != ExpectedRows ||
            bridge.PhysicalOpaqueBc3Count != ExpectedPhysicalOpaqueBc3 ||
            bridge.RawDxt1Count != ExpectedRawDxt1 ||
            bridge.SemanticExceptionBc3Count != ExpectedSemanticExceptions ||
            bridge.EquivalentPriorSafeColorChainCount != 0)
        {
            throw new InvalidDataException(
                "bc3_bridge_frozen_partition:" +
                $"{bridge.Rows.Count}/" +
                $"{bridge.PhysicalOpaqueBc3Count}/" +
                $"{bridge.RawDxt1Count}/" +
                $"{bridge.SemanticExceptionBc3Count}/" +
                $"{bridge.EquivalentPriorSafeColorChainCount}");
        }

        string[] semantic = bridge.Rows
            .Where(row => row.EligibilityMode == Bc3EligibilityMode.SemanticDetailRgbOnly)
            .Select(row => row.RelativePath)
            .OrderBy(path => path, StringComparer.Ordinal)
            .ToArray();
        if (!semantic.SequenceEqual(
                SemanticExceptionPaths.OrderBy(path => path, StringComparer.Ordinal),
                StringComparer.Ordinal))
        {
            throw new InvalidDataException("bc3_bridge_semantic_exception_set_mismatch");
        }
    }

    private static Bc3EligibilityMode ResolveMode(
        string path,
        UniversalVtfInventoryEntry rawEntry,
        JsonElement priorRow)
    {
        string priorMode = RequireString(priorRow, "eligibility_mode");
        if (SemanticExceptionPaths.Contains(path))
        {
            if (priorMode != "semantic_detail_rgb_only" || rawEntry.HighResFormat != 15)
            {
                throw new InvalidDataException($"semantic_exception_receipt_drift:{path}");
            }

            return Bc3EligibilityMode.SemanticDetailRgbOnly;
        }

        if (priorMode != "physical_opaque")
        {
            throw new InvalidDataException($"physical_receipt_mode_drift:{path}:{priorMode}");
        }

        return rawEntry.HighResFormat switch
        {
            15 => Bc3EligibilityMode.PhysicalOpaque,
            13 => Bc3EligibilityMode.RawDxt1,
            _ => throw new InvalidDataException($"bc3_bridge_unsupported_raw_format:{path}:{rawEntry.HighResFormat}")
        };
    }

    private static IReadOnlyList<Bc3RawMipReceipt> BuildRawMipReceipts(
        string rawRoot,
        UniversalVtfInventoryEntry entry)
    {
        if (entry.MipCount <= 1)
        {
            throw new InvalidDataException($"bc3_bridge_missing_level1_plus:{entry.RelativePath}");
        }

        string path = CombineAuthority(rawRoot, entry.RelativePath);
        var results = new List<Bc3RawMipReceipt>(entry.MipCount - 1);
        using var stream = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read,
            1024 * 1024,
            FileOptions.SequentialScan);
        for (int level = 1; level < entry.MipCount; level++)
        {
            VtfSubresourceRange[] ranges = entry.Subresources
                .Where(range => range.MipLevel == level)
                .OrderBy(range => range.Frame)
                .ThenBy(range => range.Face)
                .ThenBy(range => range.DepthSlice)
                .ToArray();
            if (ranges.Length == 0)
            {
                throw new InvalidDataException($"bc3_bridge_empty_mip:{entry.RelativePath}:{level}");
            }

            using IncrementalHash hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            var alphaCounts = new long[256];
            long payloadBytes = 0;
            foreach (VtfSubresourceRange range in ranges)
            {
                byte[] payload = ReadRange(stream, range.Range);
                hash.AppendData(payload);
                payloadBytes = checked(payloadBytes + payload.Length);
                AccumulateAlpha(entry.HighResFormat, payload, alphaCounts);
            }

            long samples = alphaCounts.Sum();
            int minimum = Array.FindIndex(alphaCounts, count => count != 0);
            int maximum = Array.FindLastIndex(alphaCounts, count => count != 0);
            int unique = alphaCounts.Count(count => count != 0);
            results.Add(new Bc3RawMipReceipt(
                level,
                Math.Max(1, entry.Width >> level),
                Math.Max(1, entry.Height >> level),
                Math.Max(1, entry.Depth >> level),
                ranges.Length,
                payloadBytes,
                Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant(),
                new Bc3AlphaReceipt(
                    samples,
                    minimum,
                    maximum,
                    unique,
                    minimum == 255 && maximum == 255)));
        }

        return results.AsReadOnly();
    }

    private static void VerifyAlphaEligibility(
        string path,
        Bc3EligibilityMode mode,
        IReadOnlyList<Bc3RawMipReceipt> mips)
    {
        if (mode is Bc3EligibilityMode.PhysicalOpaque or Bc3EligibilityMode.RawDxt1)
        {
            if (mips.Any(mip => !mip.Alpha.FullyOpaque))
            {
                throw new InvalidDataException($"bc3_bridge_raw_alpha_not_opaque:{path}");
            }

            return;
        }

        if (mips.Any(mip => mip.Alpha.FullyOpaque || mip.Alpha.UniqueValues != 1))
        {
            throw new InvalidDataException($"bc3_bridge_semantic_alpha_not_constant_nonopaque:{path}");
        }
    }

    private static bool HasEquivalentPriorSafeColorChain(
        string rawRoot,
        UniversalVtfInventoryEntry rawEntry,
        string safeRoot,
        string path)
    {
        string rawPath = CombineAuthority(rawRoot, path);
        string safePath = CombineAuthority(safeRoot, path);
        VtfContainerLayout safe = VtfContainerLayoutReader.Read(safePath);
        if (safe.Document.HighResFormat != 13 || safe.Document.MipCount != rawEntry.MipCount - 1)
        {
            throw new InvalidDataException($"prior_safe_color_chain_shape:{path}");
        }

        using var rawStream = File.OpenRead(rawPath);
        using var safeStream = File.OpenRead(safePath);
        for (int rawLevel = 1; rawLevel < rawEntry.MipCount; rawLevel++)
        {
            int safeLevel = rawLevel - 1;
            VtfSubresourceRange[] rawRanges = OrderedRanges(rawEntry.Subresources, rawLevel);
            VtfSubresourceRange[] safeRanges = OrderedRanges(safe.Subresources, safeLevel);
            if (rawRanges.Length != safeRanges.Length)
            {
                throw new InvalidDataException($"prior_safe_color_chain_planes:{path}:{rawLevel}");
            }

            for (int plane = 0; plane < rawRanges.Length; plane++)
            {
                byte[] rawPayload = ReadRange(rawStream, rawRanges[plane].Range);
                byte[] safePayload = ReadRange(safeStream, safeRanges[plane].Range);
                if (rawPayload.Length != safePayload.Length * 2)
                {
                    throw new InvalidDataException($"prior_safe_color_chain_bytes:{path}:{rawLevel}");
                }

                for (int block = 0; block < safePayload.Length / 8; block++)
                {
                    if (!rawPayload.AsSpan(block * 16 + 8, 8)
                            .SequenceEqual(safePayload.AsSpan(block * 8, 8)))
                    {
                        return false;
                    }
                }
            }
        }

        return true;
    }

    private static VtfSubresourceRange[] OrderedRanges(
        IReadOnlyList<VtfSubresourceRange> ranges,
        int level) => ranges
        .Where(range => range.MipLevel == level)
        .OrderBy(range => range.Frame)
        .ThenBy(range => range.Face)
        .ThenBy(range => range.DepthSlice)
        .ToArray();

    private static void AccumulateAlpha(int format, byte[] payload, long[] counts)
    {
        if (format == 13)
        {
            if (payload.Length == 0 || payload.Length % 8 != 0)
            {
                throw new InvalidDataException("invalid_dxt1_payload");
            }

            counts[255] = checked(counts[255] + payload.Length / 8 * 16L);
            return;
        }

        if (format != 15 || payload.Length == 0 || payload.Length % 16 != 0)
        {
            throw new InvalidDataException("invalid_dxt5_payload");
        }

        for (int offset = 0; offset < payload.Length; offset += 16)
        {
            ReadOnlySpan<byte> block = payload.AsSpan(offset, 16);
            var palette = new byte[8];
            palette[0] = block[0];
            palette[1] = block[1];
            if (palette[0] > palette[1])
            {
                for (int index = 1; index <= 6; index++)
                {
                    palette[index + 1] = (byte)(((7 - index) * palette[0] + index * palette[1]) / 7);
                }
            }
            else
            {
                for (int index = 1; index <= 4; index++)
                {
                    palette[index + 1] = (byte)(((5 - index) * palette[0] + index * palette[1]) / 5);
                }

                palette[6] = 0;
                palette[7] = 255;
            }

            ulong indices = 0;
            for (int index = 0; index < 6; index++)
            {
                indices |= (ulong)block[index + 2] << (index * 8);
            }

            for (int pixel = 0; pixel < 16; pixel++)
            {
                counts[palette[(int)((indices >> (pixel * 3)) & 7)]]++;
            }
        }
    }

    private static string VerifyAndHashSemanticReceipt(
        string path,
        string priorSourceSha,
        JsonElement audit,
        IReadOnlyDictionary<string, UniversalVtfTreeFile> safeByPath)
    {
        if (!audit.TryGetProperty("semantic_receipt", out JsonElement receipt) ||
            receipt.ValueKind != JsonValueKind.Object ||
            !RequireBoolean(receipt, "consumer_closure_complete") ||
            !RequireString(receipt, "relative_path").Equals(path, StringComparison.Ordinal) ||
            !RequireSha(receipt, "source_sha256").Equals(priorSourceSha, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"semantic_receipt_drift:{path}");
        }

        JsonElement consumers = RequireArray(receipt, "consumers");
        if (consumers.GetArrayLength() == 0)
        {
            throw new InvalidDataException($"semantic_receipt_without_consumers:{path}");
        }

        foreach (JsonElement consumer in consumers.EnumerateArray())
        {
            if (!RequireString(consumer, "role").Equals("$detail", StringComparison.OrdinalIgnoreCase) ||
                RequireBoolean(consumer, "unknown_role") ||
                RequireArray(consumer, "alpha_semantics").GetArrayLength() != 0)
            {
                throw new InvalidDataException($"semantic_receipt_alpha_consumer:{path}");
            }

            string vmt = RequireString(consumer, "vmt").ToLowerInvariant();
            string vmtSha = RequireSha(consumer, "vmt_sha256");
            if (!safeByPath.TryGetValue(vmt, out UniversalVtfTreeFile? file) ||
                !file.Sha256.Equals(vmtSha, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException($"semantic_receipt_vmt_drift:{path}:{vmt}");
            }
        }

        return HashCanonical(receipt);
    }

    private static string HashPhysicalReceipt(string path, JsonElement closure)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream))
        {
            writer.WriteStartObject();
            writer.WritePropertyName("consumerClosureScope");
            WriteCanonical(writer, closure);
            writer.WriteString("eligibilityMode", "physical_opaque");
            writer.WriteString("relativePath", path);
            writer.WriteString("schema", "bc3-physical-consumer-receipt-v1");
            writer.WriteEndObject();
        }

        return Sha256(stream.ToArray());
    }

    private static Dictionary<string, JsonElement> IndexAudits(JsonElement audits)
    {
        var result = new Dictionary<string, JsonElement>(StringComparer.OrdinalIgnoreCase);
        foreach (JsonElement audit in audits.EnumerateArray())
        {
            string path = RequireString(audit, "relative_path").ToLowerInvariant();
            if (!result.TryAdd(path, audit))
            {
                throw new InvalidDataException($"prior_bc3_duplicate_audit:{path}");
            }
        }

        return result;
    }

    private static void VerifySelectedAuditAgreement(
        string path,
        JsonElement selected,
        JsonElement audit)
    {
        foreach (string property in new[]
                 {
                     "source_bytes", "candidate_bytes", "saved_bytes"
                 })
        {
            if (RequireInt64(selected, property) != RequireInt64(audit, property))
            {
                throw new InvalidDataException($"prior_bc3_audit_disagrees:{path}:{property}");
            }
        }

        foreach (string property in new[]
                 {
                     "source_sha256", "candidate_sha256", "eligibility_mode"
                 })
        {
            if (!RequireString(selected, property).Equals(
                    RequireString(audit, property),
                    StringComparison.Ordinal))
            {
                throw new InvalidDataException($"prior_bc3_audit_disagrees:{path}:{property}");
            }
        }
    }

    private static void RequireTreeMatchesManifest(
        UniversalVtfTreeSnapshot tree,
        JsonElement manifestTree)
    {
        long bytes = RequireInt64(manifestTree, "bytes");
        int files = checked((int)RequireInt64(manifestTree, "file_count"));
        string sha = RequireSha(manifestTree, "tree_sha256");
        if (tree.TotalBytes != bytes || tree.Files.Count != files ||
            !tree.TreeSha256.Equals(sha, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException("approved_safe_tree_anchor_mismatch");
        }
    }

    private static byte[] ReadRange(Stream stream, VtfByteRange range)
    {
        var bytes = new byte[checked((int)range.Length)];
        stream.Position = range.Offset;
        int total = 0;
        while (total < bytes.Length)
        {
            int read = stream.Read(bytes, total, bytes.Length - total);
            if (read == 0)
            {
                throw new EndOfStreamException(
                    $"Unexpected EOF while reading VTF range at {range.Offset}.");
            }

            total += read;
        }

        return bytes;
    }

    private static string CombineAuthority(string root, string relativePath)
    {
        string fullRoot = Path.GetFullPath(root).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        string fullPath = Path.GetFullPath(Path.Combine(
            fullRoot,
            relativePath.Replace('/', Path.DirectorySeparatorChar)));
        string prefix = fullRoot + Path.DirectorySeparatorChar;
        if (!fullPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"bc3_bridge_path_escape:{relativePath}");
        }

        return fullPath;
    }

    private static string HashCanonical(JsonElement element)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream))
        {
            WriteCanonical(writer, element);
        }

        return Sha256(stream.ToArray());
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
                throw new InvalidDataException($"unsupported_json_kind:{element.ValueKind}");
        }
    }

    private static JsonElement RequireObject(JsonElement parent, string name)
    {
        if (!parent.TryGetProperty(name, out JsonElement value) || value.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidDataException($"prior_manifest_missing_object:{name}");
        }

        return value;
    }

    private static JsonElement RequireArray(JsonElement parent, string name)
    {
        if (!parent.TryGetProperty(name, out JsonElement value) || value.ValueKind != JsonValueKind.Array)
        {
            throw new InvalidDataException($"prior_manifest_missing_array:{name}");
        }

        return value;
    }

    private static string RequireString(JsonElement parent, string name)
    {
        if (!parent.TryGetProperty(name, out JsonElement value) || value.ValueKind != JsonValueKind.String ||
            string.IsNullOrWhiteSpace(value.GetString()))
        {
            throw new InvalidDataException($"prior_manifest_missing_string:{name}");
        }

        return value.GetString()!;
    }

    private static void RequireString(JsonElement parent, string name, string expected)
    {
        string actual = RequireString(parent, name);
        if (!actual.Equals(expected, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"prior_manifest_value:{name}:{actual}!={expected}");
        }
    }

    private static string RequireSha(JsonElement parent, string name)
    {
        string sha = RequireString(parent, name).ToLowerInvariant();
        Bc3RawBridgeStore.RequireSha256(sha, name);
        return sha;
    }

    private static long RequireInt64(JsonElement parent, string name)
    {
        if (!parent.TryGetProperty(name, out JsonElement value) ||
            value.ValueKind != JsonValueKind.Number || !value.TryGetInt64(out long result))
        {
            throw new InvalidDataException($"prior_manifest_missing_int64:{name}");
        }

        return result;
    }

    private static bool RequireBoolean(JsonElement parent, string name)
    {
        if (!parent.TryGetProperty(name, out JsonElement value) ||
            value.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
        {
            throw new InvalidDataException($"prior_manifest_missing_boolean:{name}");
        }

        return value.GetBoolean();
    }

    private static string Sha256(byte[] bytes) =>
        Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
}
