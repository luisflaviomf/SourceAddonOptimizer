using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading;

namespace GmodAddonCompressor.Systems.Companion
{
    internal sealed record CompanionRuntimeProfileSnapshot(
        string BundleRoot,
        CompanionProfileImportResult ImportResult,
        string IndexSha256,
        Guid RunId
    );

    internal static class CompanionRuntimeProfileSnapshotService
    {
        private const int MaximumChunks = 10_000;

        public static CompanionRuntimeProfileSnapshot Create(
            IEnumerable<string> indexPaths,
            string addonRoot,
            string workDir,
            DateTimeOffset? now = null,
            CancellationToken cancellationToken = default)
        {
            DateTimeOffset clock = now ?? DateTimeOffset.UtcNow;
            string[] sources = indexPaths
                .Where(path => !string.IsNullOrWhiteSpace(path))
                .Select(Path.GetFullPath)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();
            if (sources.Length == 0)
                throw new InvalidOperationException("No imported runtime profile is available at run start.");

            CompanionProfileImportResult imported = CompanionProfileService.Import(
                sources,
                addonRoot,
                clock,
                cancellationToken
            );
            if (!imported.IsEligible)
                throw new InvalidOperationException("The runtime profile is no longer eligible for the selected addon.");

            cancellationToken.ThrowIfCancellationRequested();
            IReadOnlyList<Dictionary<string, object?>> modelRecords = BuildModelRecords(imported.Profile);
            IReadOnlyList<Dictionary<string, object?>> materialRecords = BuildMaterialRecords(imported.Profile);
            Guid runId = sources.Length == 1
                ? imported.Profile.RunId
                : DeterministicMergedRunId(modelRecords, materialRecords);
            List<SnapshotPayload> payloads = BuildPayloads(imported.Profile, modelRecords, materialRecords);

            string fullWorkDir = Path.GetFullPath(workDir ?? string.Empty);
            if (string.IsNullOrWhiteSpace(fullWorkDir))
                throw new InvalidOperationException("The Models work directory is missing.");
            string snapshotsRoot = Path.Combine(fullWorkDir, "runtime-profile-snapshots");
            Directory.CreateDirectory(snapshotsRoot);
            RejectReparsePoint(snapshotsRoot);
            string partialRoot = Path.Combine(snapshotsRoot, $".{runId:N}.{Guid.NewGuid():N}.partial");
            Directory.CreateDirectory(partialRoot);

            try
            {
                string createdUtc = imported.Profile.CreatedUtc
                    .ToUniversalTime()
                    .ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
                var producer = new Dictionary<string, object?>
                {
                    ["name"] = "GmodAddonOptimizer WPF",
                    ["version"] = $"{CompanionContract.ContractMajor}.{CompanionContract.ContractMinor}.0",
                };
                var environment = new Dictionary<string, object?>
                {
                    ["game_build"] = string.Empty,
                    ["map"] = string.Empty,
                    ["gamemode"] = string.Empty,
                    ["realm"] = "singleplayer",
                    ["session_seconds"] = 0.0,
                    ["mounted_addons"] = Array.Empty<object>(),
                };

                var declarations = new List<Dictionary<string, object?>>(payloads.Count);
                long totalBytes = 0;
                for (int index = 0; index < payloads.Count; index++)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    byte[] bytes = EnvelopeBytes(
                        payloads[index],
                        imported.Profile,
                        runId,
                        createdUtc,
                        producer,
                        environment,
                        index + 1,
                        payloads.Count
                    );
                    if (bytes.LongLength > CompanionContract.MaxChunkBytes)
                        throw new InvalidOperationException("Generated runtime profile chunk exceeds its byte limit.");
                    totalBytes = checked(totalBytes + bytes.LongLength);
                    if (totalBytes > CompanionContract.MaxBundleBytes)
                        throw new InvalidOperationException("Generated runtime profile exceeds its byte limit.");
                    string filename = $"chunk-{index + 1:0000}.json";
                    WriteNewFile(Path.Combine(partialRoot, filename), bytes);
                    declarations.Add(new Dictionary<string, object?>
                    {
                        ["sequence"] = index + 1,
                        ["file"] = filename,
                        ["bytes"] = bytes.LongLength,
                        ["sha256"] = Sha256(bytes),
                    });
                }

                byte[] indexBytes = JsonBytes(new Dictionary<string, object?>
                {
                    ["schema"] = "sao-run-index-v1",
                    ["contract"] = Contract(),
                    ["run_id"] = runId.ToString("D"),
                    ["payload_schema"] = CompanionContract.RuntimeProfileSchema,
                    ["created_utc"] = createdUtc,
                    ["producer"] = producer,
                    ["environment"] = environment,
                    ["chunk_count"] = payloads.Count,
                    ["total_bytes"] = totalBytes,
                    ["chunks"] = declarations,
                });
                if (indexBytes.LongLength > CompanionContract.MaxIndexBytes)
                    throw new InvalidOperationException("Generated runtime profile index exceeds its byte limit.");
                WriteNewFile(Path.Combine(partialRoot, "run-index.json"), indexBytes);
                WriteNewFile(Path.Combine(partialRoot, "complete.ready.json"), JsonBytes(new Dictionary<string, object?>
                {
                    ["schema"] = "sao-completion-v1",
                    ["run_id"] = runId.ToString("D"),
                    ["index_sha256"] = Sha256(indexBytes),
                }));

                CompanionRuntimeProfile verified = CompanionBundleReader.ReadRuntimeProfile(partialRoot, clock);
                string expectedSemantics = SemanticFingerprint(imported.Profile);
                if (!SemanticFingerprint(verified).Equals(expectedSemantics, StringComparison.Ordinal))
                    throw new InvalidOperationException("Generated runtime profile failed semantic revalidation.");
                CompanionBundleInspection inspection = CompanionBundleReader.Inspect(partialRoot, clock);
                string destination = Path.Combine(
                    snapshotsRoot,
                    $"{runId:N}-{inspection.IndexSha256[..16]}"
                );
                if (Directory.Exists(destination))
                {
                    bool reusable = false;
                    try
                    {
                        CompanionBundleInspection existing = CompanionBundleReader.Inspect(destination, clock);
                        CompanionRuntimeProfile existingProfile = CompanionBundleReader.ReadRuntimeProfile(destination, clock);
                        reusable = existing.IndexSha256.Equals(inspection.IndexSha256, StringComparison.Ordinal)
                            && SemanticFingerprint(existingProfile).Equals(expectedSemantics, StringComparison.Ordinal);
                    }
                    catch (InvalidOperationException)
                    {
                        // A damaged prior snapshot is never selected and is not modified in place.
                    }
                    if (reusable)
                        Directory.Delete(partialRoot, recursive: true);
                    else
                    {
                        destination += "-" + Guid.NewGuid().ToString("N");
                        Directory.Move(partialRoot, destination);
                    }
                }
                else
                {
                    Directory.Move(partialRoot, destination);
                }

                CompanionRuntimeProfile finalProfile = CompanionBundleReader.ReadRuntimeProfile(destination, clock);
                if (!SemanticFingerprint(finalProfile).Equals(expectedSemantics, StringComparison.Ordinal))
                    throw new InvalidOperationException("Final runtime profile snapshot failed revalidation.");
                return new CompanionRuntimeProfileSnapshot(
                    destination,
                    imported,
                    inspection.IndexSha256,
                    runId
                );
            }
            catch
            {
                CleanupPartial(partialRoot);
                throw;
            }
        }

        private static IReadOnlyList<Dictionary<string, object?>> BuildModelRecords(CompanionRuntimeProfile profile)
        {
            int identity = 0;
            return profile.Models.Values
                .OrderBy(model => model.CanonicalPath, StringComparer.Ordinal)
                .Select(model =>
                {
                    identity++;
                    return new Dictionary<string, object?>
                    {
                        ["id"] = $"model-{identity:000000}",
                        ["path"] = model.CanonicalPath,
                        ["evidence"] = Evidence(model.Evidence),
                        ["skins"] = model.Skins.OrderBy(value => value).ToArray(),
                        ["bodygroups"] = model.Bodygroups
                            .OrderBy(pair => pair.Key, StringComparer.Ordinal)
                            .Select(pair => BodygroupRecord(pair.Key, pair.Value))
                            .ToArray(),
                        ["submaterials"] = model.Submaterials
                            .OrderBy(pair => pair.Key, StringComparer.Ordinal)
                            .Select(pair => SubmaterialRecord(pair.Key, pair.Value))
                            .ToArray(),
                        ["materials"] = model.Materials.OrderBy(value => value, StringComparer.Ordinal).ToArray(),
                    };
                })
                .ToArray();
        }

        private static IReadOnlyList<Dictionary<string, object?>> BuildMaterialRecords(CompanionRuntimeProfile profile)
        {
            int identity = 0;
            return profile.Materials.Values
                .OrderBy(material => material.CanonicalPath, StringComparer.Ordinal)
                .Select(material =>
                {
                    identity++;
                    return new Dictionary<string, object?>
                    {
                        ["id"] = $"material-{identity:000000}",
                        ["path"] = material.CanonicalPath,
                        ["evidence"] = Evidence(material.Evidence),
                    };
                })
                .ToArray();
        }

        private static List<SnapshotPayload> BuildPayloads(
            CompanionRuntimeProfile profile,
            IReadOnlyList<Dictionary<string, object?>> models,
            IReadOnlyList<Dictionary<string, object?>> materials)
        {
            var result = new List<SnapshotPayload>();
            var currentModels = new List<Dictionary<string, object?>>();
            var currentMaterials = new List<Dictionary<string, object?>>();
            foreach (Dictionary<string, object?> model in models)
                AddBounded(result, currentModels, currentMaterials, model, isModel: true, profile);
            foreach (Dictionary<string, object?> material in materials)
                AddBounded(result, currentModels, currentMaterials, material, isModel: false, profile);
            if (currentModels.Count > 0 || currentMaterials.Count > 0 || result.Count == 0)
                result.Add(new SnapshotPayload(currentModels.ToArray(), currentMaterials.ToArray(), result.Count == 0));
            if (result.Count > MaximumChunks)
                throw new InvalidOperationException("Generated runtime profile exceeds its chunk limit.");
            return result;
        }

        private static void AddBounded(
            List<SnapshotPayload> completed,
            List<Dictionary<string, object?>> models,
            List<Dictionary<string, object?>> materials,
            Dictionary<string, object?> item,
            bool isModel,
            CompanionRuntimeProfile profile)
        {
            (isModel ? models : materials).Add(item);
            var candidate = new SnapshotPayload(models.ToArray(), materials.ToArray(), completed.Count == 0);
            if (PayloadBytes(candidate, profile).LongLength <= CompanionContract.MaxChunkBytes - 4_096)
                return;
            (isModel ? models : materials).RemoveAt((isModel ? models : materials).Count - 1);
            if (models.Count == 0 && materials.Count == 0)
                throw new InvalidOperationException("A runtime profile record exceeds the chunk byte limit.");
            completed.Add(new SnapshotPayload(models.ToArray(), materials.ToArray(), completed.Count == 0));
            models.Clear();
            materials.Clear();
            (isModel ? models : materials).Add(item);
            candidate = new SnapshotPayload(models.ToArray(), materials.ToArray(), false);
            if (PayloadBytes(candidate, profile).LongLength > CompanionContract.MaxChunkBytes - 4_096)
                throw new InvalidOperationException("A runtime profile record exceeds the chunk byte limit.");
        }

        private static Dictionary<string, object?> BodygroupRecord(string key, CompanionEvidenceTier evidence)
        {
            int separator = key.LastIndexOf('=');
            if (separator <= 0 || !int.TryParse(key[(separator + 1)..], NumberStyles.None, CultureInfo.InvariantCulture, out int value))
                throw new InvalidOperationException("Imported runtime profile contains an invalid bodygroup state.");
            return new Dictionary<string, object?>
            {
                ["name"] = key[..separator],
                ["value"] = value,
                ["evidence"] = Evidence(evidence),
            };
        }

        private static Dictionary<string, object?> SubmaterialRecord(string key, CompanionEvidenceTier evidence)
        {
            int separator = key.IndexOf('=');
            if (separator <= 0 || !int.TryParse(key[..separator], NumberStyles.None, CultureInfo.InvariantCulture, out int slot))
                throw new InvalidOperationException("Imported runtime profile contains an invalid submaterial state.");
            return new Dictionary<string, object?>
            {
                ["slot"] = slot,
                ["material"] = key[(separator + 1)..],
                ["evidence"] = Evidence(evidence),
            };
        }

        private static byte[] PayloadBytes(SnapshotPayload payload, CompanionRuntimeProfile profile) =>
            JsonBytes(PayloadRecord(payload, profile));

        private static byte[] EnvelopeBytes(
            SnapshotPayload payload,
            CompanionRuntimeProfile profile,
            Guid runId,
            string createdUtc,
            Dictionary<string, object?> producer,
            Dictionary<string, object?> environment,
            int sequence,
            int total) => JsonBytes(new Dictionary<string, object?>
            {
                ["schema"] = CompanionContract.RuntimeProfileSchema,
                ["contract"] = Contract(),
                ["run_id"] = runId.ToString("D"),
                ["created_utc"] = createdUtc,
                ["producer"] = producer,
                ["environment"] = environment,
                ["chunk"] = new Dictionary<string, object?> { ["sequence"] = sequence, ["total"] = total },
                ["payload"] = PayloadRecord(payload, profile),
            });

        private static Dictionary<string, object?> PayloadRecord(
            SnapshotPayload payload,
            CompanionRuntimeProfile? profile) => new()
            {
                ["models"] = payload.Models,
                ["materials"] = payload.Materials,
                ["lua_errors"] = Array.Empty<object>(),
                ["performance"] = new Dictionary<string, object?>
                {
                    ["samples"] = 0,
                    ["average_ms"] = 0.0,
                    ["p95_ms"] = 0.0,
                    ["max_ms"] = 0.0,
                    ["queue_high_water"] = 0,
                },
                ["truncation"] = new Dictionary<string, object?>
                {
                    ["models"] = payload.CarriesTruncation && profile != null ? profile.TruncatedModels : 0,
                    ["materials"] = payload.CarriesTruncation && profile != null ? profile.TruncatedMaterials : 0,
                    ["lua_errors"] = payload.CarriesTruncation && profile != null ? profile.TruncatedLuaErrors : 0,
                    ["bytes"] = payload.CarriesTruncation && profile != null ? profile.TruncatedBytes : 0,
                },
            };

        private static Dictionary<string, object?> Contract() => new()
        {
            ["major"] = CompanionContract.ContractMajor,
            ["minor"] = CompanionContract.ContractMinor,
        };

        private static Guid DeterministicMergedRunId(
            IReadOnlyList<Dictionary<string, object?>> models,
            IReadOnlyList<Dictionary<string, object?>> materials)
        {
            byte[] hash = SHA256.HashData(JsonBytes(new Dictionary<string, object?>
            {
                ["models"] = models,
                ["materials"] = materials,
            }));
            byte[] guidBytes = hash[..16];
            return new Guid(guidBytes);
        }

        private static string SemanticFingerprint(CompanionRuntimeProfile profile) =>
            Sha256(JsonBytes(new Dictionary<string, object?>
            {
                ["models"] = BuildModelRecords(profile),
                ["materials"] = BuildMaterialRecords(profile),
                ["truncation"] = new long[]
                {
                    profile.TruncatedModels,
                    profile.TruncatedMaterials,
                    profile.TruncatedLuaErrors,
                    profile.TruncatedBytes,
                },
            }));

        private static string Evidence(CompanionEvidenceTier value) => value switch
        {
            CompanionEvidenceTier.Registered => "registered",
            CompanionEvidenceTier.Instantiated => "instantiated",
            CompanionEvidenceTier.Visible => "visible",
            CompanionEvidenceTier.RepeatedVisible => "repeated_visible",
            CompanionEvidenceTier.Focused => "focused",
            _ => throw new InvalidOperationException("Imported runtime profile has an invalid evidence tier."),
        };

        private static byte[] JsonBytes(object value)
        {
            byte[] encoded = JsonSerializer.SerializeToUtf8Bytes(value);
            byte[] result = new byte[encoded.Length + 1];
            Buffer.BlockCopy(encoded, 0, result, 0, encoded.Length);
            result[^1] = (byte)'\n';
            return result;
        }

        private static void WriteNewFile(string path, byte[] bytes)
        {
            using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            stream.Write(bytes);
            stream.Flush(flushToDisk: true);
        }

        private static string Sha256(byte[] bytes) =>
            Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

        private static void RejectReparsePoint(string path)
        {
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException("Runtime profile snapshot directory cannot be a reparse point.");
        }

        private static void CleanupPartial(string path)
        {
            if (!Directory.Exists(path) || !Path.GetFileName(path).EndsWith(".partial", StringComparison.Ordinal))
                return;
            Directory.Delete(path, recursive: true);
        }

        private sealed record SnapshotPayload(
            IReadOnlyList<Dictionary<string, object?>> Models,
            IReadOnlyList<Dictionary<string, object?>> Materials,
            bool CarriesTruncation
        );
    }
}
