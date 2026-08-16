using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems.Companion
{
    internal static class CompanionBundleReader
    {
        private static readonly Regex ChunkFilePattern = new("^chunk-([0-9]{4})\\.json$", RegexOptions.CultureInvariant);
        private static readonly Regex LowerSha256Pattern = new("^[0-9a-f]{64}$", RegexOptions.CultureInvariant);
        private static readonly Regex WorkshopIdPattern = new("^[0-9]{1,20}$", RegexOptions.CultureInvariant);
        private static readonly HashSet<string> Realms = new(new[] { "server", "client", "singleplayer" }, StringComparer.Ordinal);

        public static CompanionBundleInspection Inspect(string indexPath, DateTimeOffset? now = null)
        {
            BundleMetadata metadata = ReadMetadata(indexPath, now ?? DateTimeOffset.UtcNow);
            bool compatible = metadata.Schema == CompanionContract.RuntimeProfileSchema
                && metadata.ContractMajor == CompanionContract.ContractMajor
                && metadata.ContractMinor == CompanionContract.ContractMinor;
            return new CompanionBundleInspection(
                metadata.Root,
                metadata.Schema,
                metadata.ContractMajor,
                metadata.ContractMinor,
                metadata.RunId,
                metadata.CreatedUtc,
                metadata.Producer.Name,
                metadata.Producer.Version,
                true,
                compatible,
                metadata.IndexSha256
            );
        }

        public static CompanionRuntimeProfile ReadRuntimeProfile(string indexPath, DateTimeOffset? now = null)
        {
            DateTimeOffset clock = now ?? DateTimeOffset.UtcNow;
            BundleMetadata metadata = ReadMetadata(indexPath, clock);
            if (metadata.Schema != CompanionContract.RuntimeProfileSchema)
                throw new InvalidOperationException($"Unexpected payload schema: {metadata.Schema}");
            if (metadata.ContractMajor != CompanionContract.ContractMajor || metadata.ContractMinor != CompanionContract.ContractMinor)
                throw new InvalidOperationException($"Unsupported contract {metadata.ContractMajor}.{metadata.ContractMinor} for {metadata.Schema}.");

            JsonElement index = metadata.Index;
            int chunkCount = ReadInteger(index.GetProperty("chunk_count"), "chunk_count", 1, 10_000);
            long totalBytes = ReadInteger64(index.GetProperty("total_bytes"), "total_bytes", 0, CompanionContract.MaxBundleBytes);
            JsonElement declarations = ReadArray(index.GetProperty("chunks"), "chunks", 10_000);
            if (declarations.GetArrayLength() != chunkCount)
                throw new InvalidOperationException("chunk_count does not match declarations.");

            var parsedDeclarations = new List<ChunkDeclaration>(chunkCount);
            var filenames = new HashSet<string>(StringComparer.Ordinal);
            int expectedSequence = 1;
            long declaredTotal = 0;
            foreach (JsonElement declaration in declarations.EnumerateArray())
            {
                RequireFields(declaration, "chunk declaration", "sequence", "file", "bytes", "sha256");
                int sequence = ReadInteger(declaration.GetProperty("sequence"), "chunk declaration sequence", 1, 10_000);
                if (sequence != expectedSequence)
                    throw new InvalidOperationException("Chunk declarations are reordered.");
                string filename = ReadString(declaration.GetProperty("file"), "chunk filename", 15, 15);
                Match match = ChunkFilePattern.Match(filename);
                if (!match.Success || int.Parse(match.Groups[1].Value, CultureInfo.InvariantCulture) != sequence || !filenames.Add(filename))
                    throw new InvalidOperationException("Chunk filename is invalid or repeated.");
                long bytes = ReadInteger64(declaration.GetProperty("bytes"), "chunk bytes", 1, long.MaxValue);
                if (bytes > CompanionContract.MaxChunkBytes)
                    throw new InvalidOperationException("Chunk exceeds its byte limit.");
                string hash = ReadSha256(declaration.GetProperty("sha256"), "chunk sha256");
                declaredTotal = checked(declaredTotal + bytes);
                if (declaredTotal > CompanionContract.MaxBundleBytes)
                    throw new InvalidOperationException("Bundle exceeds its byte limit.");
                parsedDeclarations.Add(new ChunkDeclaration(sequence, filename, bytes, hash));
                expectedSequence++;
            }
            if (declaredTotal != totalBytes)
                throw new InvalidOperationException("total_bytes does not match chunk declarations.");

            var models = new Dictionary<string, CompanionObservedModel>(StringComparer.Ordinal);
            var materials = new Dictionary<string, CompanionObservedMaterial>(StringComparer.Ordinal);
            var identities = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal);
            long truncatedModels = 0;
            long truncatedMaterials = 0;
            long truncatedLuaErrors = 0;
            long truncatedBytes = 0;
            foreach (ChunkDeclaration declaration in parsedDeclarations)
            {
                string chunkPath = Path.Combine(metadata.Root, declaration.Filename);
                byte[] raw = ReadBytes(chunkPath, CompanionContract.MaxChunkBytes, "chunk");
                if (raw.LongLength != declaration.Bytes)
                    throw new InvalidOperationException("Chunk size does not match its declaration.");
                if (!Sha256(raw).Equals(declaration.Sha256, StringComparison.Ordinal))
                    throw new InvalidOperationException("Chunk hash does not match its declaration.");
                using JsonDocument chunkDocument = ParseObject(raw, "chunk");
                JsonElement chunk = chunkDocument.RootElement;
                RequireFields(chunk, "chunk envelope", "schema", "contract", "run_id", "created_utc", "producer", "environment", "chunk", "payload");
                if (ReadString(chunk.GetProperty("schema"), "chunk schema", 1, 64) != metadata.Schema)
                    throw new InvalidOperationException("Chunk schema does not match the index.");
                if (ReadGuid(chunk.GetProperty("run_id"), "chunk run_id") != metadata.RunId)
                    throw new InvalidOperationException("Chunk run_id does not match the index.");
                (int major, int minor) = ReadContract(chunk.GetProperty("contract"));
                if (major != metadata.ContractMajor || minor != metadata.ContractMinor)
                    throw new InvalidOperationException("Chunk contract does not match the index.");
                if (ReadTimestamp(chunk.GetProperty("created_utc"), clock) != metadata.CreatedUtc)
                    throw new InvalidOperationException("Chunk timestamp does not match the index.");
                if (ReadProducer(chunk.GetProperty("producer")) != metadata.Producer)
                    throw new InvalidOperationException("Chunk producer does not match the index.");
                if (ReadEnvironment(chunk.GetProperty("environment")) != metadata.Environment)
                    throw new InvalidOperationException("Chunk environment does not match the index.");
                ReadChunkMetadata(chunk.GetProperty("chunk"), declaration.Sequence, chunkCount);
                ReadRuntimePayload(
                    chunk.GetProperty("payload"),
                    models,
                    materials,
                    identities,
                    ref truncatedModels,
                    ref truncatedMaterials,
                    ref truncatedLuaErrors,
                    ref truncatedBytes
                );
            }

            if (identities.GetValueOrDefault("models")?.Count > CompanionContract.MaxModels
                || identities.GetValueOrDefault("materials")?.Count > CompanionContract.MaxMaterials)
                throw new InvalidOperationException("Profile exceeds aggregate item limits.");
            return new CompanionRuntimeProfile(
                metadata.IndexPath,
                metadata.RunId,
                metadata.CreatedUtc,
                metadata.Producer.Name,
                metadata.Producer.Version,
                models,
                materials,
                truncatedModels,
                truncatedMaterials,
                truncatedLuaErrors,
                truncatedBytes
            );
        }

        public static CompanionValidationBundle ReadValidationResult(string indexPath, DateTimeOffset? now = null)
        {
            DateTimeOffset clock = now ?? DateTimeOffset.UtcNow;
            BundleMetadata metadata = ReadMetadata(indexPath, clock);
            if (metadata.Schema != CompanionResultService.StructuralSchema
                && metadata.Schema != CompanionResultService.VisualSchema)
                throw new InvalidOperationException($"Unexpected payload schema: {metadata.Schema}");
            if (metadata.ContractMajor != CompanionContract.ContractMajor || metadata.ContractMinor != CompanionContract.ContractMinor)
                throw new InvalidOperationException($"Unsupported contract {metadata.ContractMajor}.{metadata.ContractMinor} for {metadata.Schema}.");

            JsonElement index = metadata.Index;
            int chunkCount = ReadInteger(index.GetProperty("chunk_count"), "chunk_count", 1, 10_000);
            long totalBytes = ReadInteger64(index.GetProperty("total_bytes"), "total_bytes", 0, CompanionContract.MaxBundleBytes);
            JsonElement declarations = ReadArray(index.GetProperty("chunks"), "chunks", 10_000);
            if (declarations.GetArrayLength() != chunkCount)
                throw new InvalidOperationException("chunk_count does not match declarations.");

            var parsedDeclarations = new List<ChunkDeclaration>(chunkCount);
            var filenames = new HashSet<string>(StringComparer.Ordinal);
            int expectedSequence = 1;
            long declaredTotal = 0;
            foreach (JsonElement declaration in declarations.EnumerateArray())
            {
                RequireFields(declaration, "chunk declaration", "sequence", "file", "bytes", "sha256");
                int sequence = ReadInteger(declaration.GetProperty("sequence"), "chunk declaration sequence", 1, 10_000);
                if (sequence != expectedSequence)
                    throw new InvalidOperationException("Chunk declarations are reordered.");
                string filename = ReadString(declaration.GetProperty("file"), "chunk filename", 15, 15);
                Match match = ChunkFilePattern.Match(filename);
                if (!match.Success || int.Parse(match.Groups[1].Value, CultureInfo.InvariantCulture) != sequence || !filenames.Add(filename))
                    throw new InvalidOperationException("Chunk filename is invalid or repeated.");
                long bytes = ReadInteger64(declaration.GetProperty("bytes"), "chunk bytes", 1, CompanionContract.MaxChunkBytes);
                string hash = ReadSha256(declaration.GetProperty("sha256"), "chunk sha256");
                declaredTotal = checked(declaredTotal + bytes);
                if (declaredTotal > CompanionContract.MaxBundleBytes)
                    throw new InvalidOperationException("Bundle exceeds its byte limit.");
                parsedDeclarations.Add(new ChunkDeclaration(sequence, filename, bytes, hash));
                expectedSequence++;
            }
            if (declaredTotal != totalBytes)
                throw new InvalidOperationException("total_bytes does not match chunk declarations.");

            var resources = new List<CompanionValidationResource>();
            var screenshots = new List<CompanionValidationScreenshot>();
            var identities = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal);
            string? planSha256 = null;
            CompanionValidationStatus overall = CompanionValidationStatus.Pass;
            long primaryCount = 0;
            long secondaryCount = 0;
            double wallSeconds = 0;
            long truncatedPrimary = 0;
            long truncatedSecondary = 0;
            foreach (ChunkDeclaration declaration in parsedDeclarations)
            {
                string chunkPath = Path.Combine(metadata.Root, declaration.Filename);
                byte[] raw = ReadBytes(chunkPath, CompanionContract.MaxChunkBytes, "chunk");
                if (raw.LongLength != declaration.Bytes)
                    throw new InvalidOperationException("Chunk size does not match its declaration.");
                if (!Sha256(raw).Equals(declaration.Sha256, StringComparison.Ordinal))
                    throw new InvalidOperationException("Chunk hash does not match its declaration.");
                using JsonDocument chunkDocument = ParseObject(raw, "chunk");
                JsonElement chunk = chunkDocument.RootElement;
                RequireFields(chunk, "chunk envelope", "schema", "contract", "run_id", "created_utc", "producer", "environment", "chunk", "payload");
                if (ReadString(chunk.GetProperty("schema"), "chunk schema", 1, 64) != metadata.Schema)
                    throw new InvalidOperationException("Chunk schema does not match the index.");
                if (ReadGuid(chunk.GetProperty("run_id"), "chunk run_id") != metadata.RunId)
                    throw new InvalidOperationException("Chunk run_id does not match the index.");
                (int major, int minor) = ReadContract(chunk.GetProperty("contract"));
                if (major != metadata.ContractMajor || minor != metadata.ContractMinor)
                    throw new InvalidOperationException("Chunk contract does not match the index.");
                if (ReadTimestamp(chunk.GetProperty("created_utc"), clock) != metadata.CreatedUtc)
                    throw new InvalidOperationException("Chunk timestamp does not match the index.");
                if (ReadProducer(chunk.GetProperty("producer")) != metadata.Producer)
                    throw new InvalidOperationException("Chunk producer does not match the index.");
                if (ReadEnvironment(chunk.GetProperty("environment")) != metadata.Environment)
                    throw new InvalidOperationException("Chunk environment does not match the index.");
                ReadChunkMetadata(chunk.GetProperty("chunk"), declaration.Sequence, chunkCount);

                JsonElement payload = chunk.GetProperty("payload");
                string chunkPlanSha256;
                if (metadata.Schema == CompanionResultService.StructuralSchema)
                {
                    RequireFields(payload, "structural result payload", "plan_sha256", "overall_status", "resources", "performance", "truncation");
                    chunkPlanSha256 = ReadSha256(payload.GetProperty("plan_sha256"), "plan_sha256");
                    ReadResultResources(payload.GetProperty("resources"), resources, identities);
                    JsonElement performance = payload.GetProperty("performance");
                    RequireFields(performance, "structural performance", "tests", "wall_seconds");
                    primaryCount = checked(primaryCount + ReadInteger64(performance.GetProperty("tests"), "performance.tests", 0, long.MaxValue));
                    wallSeconds += ReadFiniteNumber(performance.GetProperty("wall_seconds"), "performance.wall_seconds", 0, 604_800);
                    JsonElement truncation = payload.GetProperty("truncation");
                    RequireFields(truncation, "structural truncation", "resources", "warnings");
                    truncatedPrimary = checked(truncatedPrimary + ReadInteger64(truncation.GetProperty("resources"), "truncation.resources", 0, long.MaxValue));
                    truncatedSecondary = checked(truncatedSecondary + ReadInteger64(truncation.GetProperty("warnings"), "truncation.warnings", 0, long.MaxValue));
                }
                else
                {
                    RequireFields(payload, "visual result payload", "plan_sha256", "overall_status", "resources", "screenshots", "performance", "truncation");
                    chunkPlanSha256 = ReadSha256(payload.GetProperty("plan_sha256"), "plan_sha256");
                    ReadResultResources(payload.GetProperty("resources"), resources, identities);
                    ReadResultScreenshots(payload.GetProperty("screenshots"), screenshots, identities);
                    JsonElement performance = payload.GetProperty("performance");
                    RequireFields(performance, "visual performance", "models", "screenshots", "wall_seconds");
                    primaryCount = checked(primaryCount + ReadInteger64(performance.GetProperty("models"), "performance.models", 0, long.MaxValue));
                    secondaryCount = checked(secondaryCount + ReadInteger64(performance.GetProperty("screenshots"), "performance.screenshots", 0, long.MaxValue));
                    wallSeconds += ReadFiniteNumber(performance.GetProperty("wall_seconds"), "performance.wall_seconds", 0, 604_800);
                    JsonElement truncation = payload.GetProperty("truncation");
                    RequireFields(truncation, "visual truncation", "models", "screenshots");
                    truncatedPrimary = checked(truncatedPrimary + ReadInteger64(truncation.GetProperty("models"), "truncation.models", 0, long.MaxValue));
                    truncatedSecondary = checked(truncatedSecondary + ReadInteger64(truncation.GetProperty("screenshots"), "truncation.screenshots", 0, long.MaxValue));
                }
                if (planSha256 != null && !planSha256.Equals(chunkPlanSha256, StringComparison.Ordinal))
                    throw new InvalidOperationException("plan_sha256 differs across result chunks.");
                planSha256 = chunkPlanSha256;
                overall = CompanionResultService.CombineStatusForContract(
                    overall,
                    ReadValidationStatus(payload.GetProperty("overall_status"), "overall_status")
                );
            }
            if (resources.Count > CompanionContract.MaxResultResources)
                throw new InvalidOperationException("Result exceeds the aggregate resource limit.");
            if (screenshots.Count > CompanionContract.MaxScreenshots)
                throw new InvalidOperationException("Result exceeds the aggregate screenshot limit.");
            CompanionValidationStatus observedStatus = CompanionValidationStatus.Pass;
            foreach (CompanionValidationStatus status in resources.Select(item => item.Status).Concat(screenshots.Select(item => item.Status)))
                observedStatus = CompanionResultService.CombineStatusForContract(observedStatus, status);
            if (CompanionResultService.CombineStatusForContract(overall, observedStatus) != overall)
                throw new InvalidOperationException("Declared overall_status understates a resource or screenshot result.");

            return new CompanionValidationBundle(
                metadata.Root,
                metadata.IndexPath,
                metadata.Schema,
                metadata.RunId,
                metadata.CreatedUtc,
                metadata.IndexSha256,
                planSha256 ?? throw new InvalidOperationException("Result plan hash is missing."),
                overall,
                resources,
                screenshots,
                primaryCount,
                secondaryCount,
                wallSeconds,
                truncatedPrimary,
                truncatedSecondary
            );
        }

        private static void ReadResultResources(
            JsonElement value,
            List<CompanionValidationResource> output,
            Dictionary<string, HashSet<string>> identities)
        {
            foreach (JsonElement resource in ReadArray(value, "resources", CompanionContract.MaxResultResources).EnumerateArray())
            {
                RequireFields(resource, "result resource", "id", "test_id", "path", "status", "reason", "warnings");
                string id = ReadString(resource.GetProperty("id"), "resource.id", 1, 64);
                RegisterId(identities, "resources", id);
                string testId = ReadString(resource.GetProperty("test_id"), "resource.test_id", 1, 64);
                string path = CompanionContract.NormalizeVirtualPath(ReadString(resource.GetProperty("path"), "resource.path", 1, CompanionContract.MaxPathBytes));
                var warnings = new List<string>();
                foreach (JsonElement warning in ReadArray(resource.GetProperty("warnings"), "resource.warnings", 64).EnumerateArray())
                    warnings.Add(ReadString(warning, "resource.warning", 0, 512));
                output.Add(new CompanionValidationResource(
                    id,
                    testId,
                    path,
                    ReadValidationStatus(resource.GetProperty("status"), "resource.status"),
                    ReadString(resource.GetProperty("reason"), "resource.reason", 0, 1_024),
                    warnings
                ));
            }
        }

        private static void ReadResultScreenshots(
            JsonElement value,
            List<CompanionValidationScreenshot> output,
            Dictionary<string, HashSet<string>> identities)
        {
            foreach (JsonElement screenshot in ReadArray(value, "screenshots", CompanionContract.MaxScreenshots).EnumerateArray())
            {
                RequireFields(screenshot, "screenshot", "id", "test_id", "path", "sha256", "bytes", "view", "retry_count", "status", "camera", "state");
                string id = ReadString(screenshot.GetProperty("id"), "screenshot.id", 1, 64);
                RegisterId(identities, "screenshots", id);
                string view = ReadString(screenshot.GetProperty("view"), "screenshot.view", 1, 16);
                if (view != "front" && view != "side" && view != "rear" && view != "isometric" && view != "closeup")
                    throw new InvalidOperationException("Invalid screenshot view.");
                JsonElement camera = screenshot.GetProperty("camera");
                RequireFields(camera, "screenshot.camera", "fov", "distance", "crop_width", "crop_height");
                JsonElement state = screenshot.GetProperty("state");
                RequireFields(state, "screenshot.state", "skin", "bodygroups");
                var bodygroups = new List<string>();
                foreach (JsonElement bodygroup in ReadArray(state.GetProperty("bodygroups"), "screenshot.state.bodygroups", 1_024).EnumerateArray())
                {
                    RequireFields(bodygroup, "screenshot bodygroup", "name", "value");
                    string name = ReadString(bodygroup.GetProperty("name"), "screenshot bodygroup name", 1, 128);
                    int bodygroupValue = ReadInteger(bodygroup.GetProperty("value"), "screenshot bodygroup value", 0, 255);
                    bodygroups.Add(name + "=" + bodygroupValue.ToString(CultureInfo.InvariantCulture));
                }
                output.Add(new CompanionValidationScreenshot(
                    id,
                    ReadString(screenshot.GetProperty("test_id"), "screenshot.test_id", 1, 64),
                    ReadString(screenshot.GetProperty("path"), "screenshot.path", 1, CompanionContract.MaxPathBytes),
                    ReadSha256(screenshot.GetProperty("sha256"), "screenshot.sha256"),
                    ReadInteger64(screenshot.GetProperty("bytes"), "screenshot.bytes", 0, CompanionContract.MaxScreenshotBytes),
                    view,
                    ReadInteger(screenshot.GetProperty("retry_count"), "screenshot.retry_count", 0, 1),
                    ReadValidationStatus(screenshot.GetProperty("status"), "screenshot.status"),
                    ReadFiniteNumber(camera.GetProperty("fov"), "screenshot.camera.fov", 1, 120),
                    ReadFiniteNumber(camera.GetProperty("distance"), "screenshot.camera.distance", 0, 1_000_000),
                    ReadInteger(camera.GetProperty("crop_width"), "screenshot.camera.crop_width", 1, 4_096),
                    ReadInteger(camera.GetProperty("crop_height"), "screenshot.camera.crop_height", 1, 4_096),
                    ReadInteger(state.GetProperty("skin"), "screenshot.state.skin", 0, 255),
                    bodygroups,
                    string.Empty
                ));
            }
        }

        private static CompanionValidationStatus ReadValidationStatus(JsonElement value, string label) =>
            ReadString(value, label, 4, 10) switch
            {
                "PASS" => CompanionValidationStatus.Pass,
                "WARNING" => CompanionValidationStatus.Warning,
                "FAILED" => CompanionValidationStatus.Failed,
                "INCOMPLETE" => CompanionValidationStatus.Incomplete,
                _ => throw new InvalidOperationException($"Invalid {label}."),
            };

        private static BundleMetadata ReadMetadata(string indexPath, DateTimeOffset now)
        {
            string fullInput = Path.GetFullPath(indexPath ?? throw new InvalidOperationException("Bundle path is missing."));
            string root = Directory.Exists(fullInput) ? fullInput : Path.GetDirectoryName(fullInput) ?? string.Empty;
            string resolvedIndex = Directory.Exists(fullInput) ? Path.Combine(root, "run-index.json") : fullInput;
            if (!Path.GetFileName(resolvedIndex).Equals("run-index.json", StringComparison.OrdinalIgnoreCase) || !Directory.Exists(root))
                throw new InvalidOperationException("Bundle root is missing or index path is invalid.");
            string readyPath = Path.Combine(root, "complete.ready.json");
            if (!File.Exists(readyPath))
                throw new InvalidOperationException("Bundle is incomplete: complete.ready.json is missing.");

            byte[] indexRaw = ReadBytes(resolvedIndex, CompanionContract.MaxIndexBytes, "run index");
            byte[] readyRaw = ReadBytes(readyPath, CompanionContract.MaxCompletionBytes, "completion marker");
            using JsonDocument indexDocument = ParseObject(indexRaw, "run index");
            using JsonDocument readyDocument = ParseObject(readyRaw, "completion marker");
            JsonElement index = indexDocument.RootElement;
            JsonElement ready = readyDocument.RootElement;
            RequireFields(ready, "completion marker", "schema", "run_id", "index_sha256");
            if (ReadString(ready.GetProperty("schema"), "completion schema", 1, 64) != "sao-completion-v1")
                throw new InvalidOperationException("Invalid completion schema.");
            string indexSha256 = Sha256(indexRaw);
            if (ReadSha256(ready.GetProperty("index_sha256"), "completion index_sha256") != indexSha256)
                throw new InvalidOperationException("Completion index hash does not match.");

            RequireFields(index, "run index", "schema", "contract", "run_id", "payload_schema", "created_utc", "producer", "environment", "chunk_count", "total_bytes", "chunks");
            if (ReadString(index.GetProperty("schema"), "run index schema", 1, 64) != "sao-run-index-v1")
                throw new InvalidOperationException("Invalid run index schema.");
            Guid runId = ReadGuid(index.GetProperty("run_id"), "run_id");
            if (ReadGuid(ready.GetProperty("run_id"), "completion run_id") != runId)
                throw new InvalidOperationException("Completion run_id does not match the index.");
            (int major, int minor) = ReadContract(index.GetProperty("contract"));
            DateTimeOffset created = ReadTimestamp(index.GetProperty("created_utc"), now);
            ProducerData producer = ReadProducer(index.GetProperty("producer"));
            EnvironmentData environment = ReadEnvironment(index.GetProperty("environment"));
            string schema = ReadString(index.GetProperty("payload_schema"), "payload_schema", 1, 64);
            return new BundleMetadata(
                root,
                resolvedIndex,
                schema,
                major,
                minor,
                runId,
                created,
                producer,
                environment,
                indexSha256,
                index.Clone()
            );
        }

        private static void ReadRuntimePayload(
            JsonElement payload,
            Dictionary<string, CompanionObservedModel> models,
            Dictionary<string, CompanionObservedMaterial> materials,
            Dictionary<string, HashSet<string>> identities,
            ref long truncatedModels,
            ref long truncatedMaterials,
            ref long truncatedLuaErrors,
            ref long truncatedBytes)
        {
            RequireFields(payload, "payload", "models", "materials", "lua_errors", "performance", "truncation");
            JsonElement modelArray = ReadArray(payload.GetProperty("models"), "models", CompanionContract.MaxModels);
            JsonElement materialArray = ReadArray(payload.GetProperty("materials"), "materials", CompanionContract.MaxMaterials);
            JsonElement errorArray = ReadArray(payload.GetProperty("lua_errors"), "lua_errors", 1_024);
            foreach (JsonElement modelElement in modelArray.EnumerateArray())
            {
                RequireFields(modelElement, "model", "id", "path", "evidence", "skins", "bodygroups", "submaterials", "materials");
                RegisterId(identities, "models", ReadString(modelElement.GetProperty("id"), "models.id", 1, 64));
                string displayPath = ReadString(modelElement.GetProperty("path"), "model.path", 1, CompanionContract.MaxPathBytes);
                string path = CompanionContract.NormalizeVirtualPath(displayPath);
                if (!path.EndsWith(".mdl", StringComparison.Ordinal))
                    throw new InvalidOperationException("Model path must identify an MDL.");
                CompanionEvidenceTier evidence = CompanionContract.ParseEvidence(ReadString(modelElement.GetProperty("evidence"), "evidence", 1, 32));
                if (!models.TryGetValue(path, out CompanionObservedModel? model))
                {
                    model = new CompanionObservedModel(path, displayPath.Replace('\\', '/'), evidence);
                    models.Add(path, model);
                }
                model.Evidence = Max(model.Evidence, evidence);

                JsonElement skins = ReadArray(modelElement.GetProperty("skins"), "model.skins", 256);
                var seenSkins = new HashSet<int>();
                foreach (JsonElement skinElement in skins.EnumerateArray())
                {
                    int skin = ReadInteger(skinElement, "model skin", 0, 255);
                    if (!seenSkins.Add(skin))
                        throw new InvalidOperationException("model.skins must contain unique values.");
                    model.Skins.Add(skin);
                }
                foreach (JsonElement bodygroup in ReadArray(modelElement.GetProperty("bodygroups"), "model.bodygroups", 1_024).EnumerateArray())
                {
                    RequireFields(bodygroup, "bodygroup", "name", "value", "evidence");
                    string name = ReadString(bodygroup.GetProperty("name"), "bodygroup.name", 1, 128);
                    int value = ReadInteger(bodygroup.GetProperty("value"), "bodygroup.value", 0, 255);
                    CompanionEvidenceTier bodyEvidence = CompanionContract.ParseEvidence(ReadString(bodygroup.GetProperty("evidence"), "evidence", 1, 32));
                    MergeEvidence(model.Bodygroups, $"{name}={value}", bodyEvidence);
                }
                foreach (JsonElement submaterial in ReadArray(modelElement.GetProperty("submaterials"), "model.submaterials", 256).EnumerateArray())
                {
                    RequireFields(submaterial, "submaterial", "slot", "material", "evidence");
                    int slot = ReadInteger(submaterial.GetProperty("slot"), "submaterial.slot", 0, 255);
                    string material = CompanionContract.NormalizeVirtualPath(ReadString(submaterial.GetProperty("material"), "submaterial.material", 1, CompanionContract.MaxPathBytes));
                    if (!material.EndsWith(".vmt", StringComparison.Ordinal))
                        throw new InvalidOperationException("Submaterial must identify a VMT.");
                    CompanionEvidenceTier subEvidence = CompanionContract.ParseEvidence(ReadString(submaterial.GetProperty("evidence"), "evidence", 1, 32));
                    MergeEvidence(model.Submaterials, $"{slot}={material}", subEvidence);
                }
                var seenModelMaterials = new HashSet<string>(StringComparer.Ordinal);
                foreach (JsonElement materialElement in ReadArray(modelElement.GetProperty("materials"), "model.materials", 4_096).EnumerateArray())
                {
                    string material = CompanionContract.NormalizeVirtualPath(ReadString(materialElement, "model material", 1, CompanionContract.MaxPathBytes));
                    if (!material.EndsWith(".vmt", StringComparison.Ordinal))
                        throw new InvalidOperationException("Model material must identify a VMT.");
                    if (!seenModelMaterials.Add(material))
                        throw new InvalidOperationException("model.materials must contain unique values.");
                    model.Materials.Add(material);
                }
            }

            foreach (JsonElement materialElement in materialArray.EnumerateArray())
            {
                RequireFields(materialElement, "material", "id", "path", "evidence");
                RegisterId(identities, "materials", ReadString(materialElement.GetProperty("id"), "materials.id", 1, 64));
                string displayPath = ReadString(materialElement.GetProperty("path"), "material.path", 1, CompanionContract.MaxPathBytes);
                string path = CompanionContract.NormalizeVirtualPath(displayPath);
                if (!path.EndsWith(".vmt", StringComparison.Ordinal))
                    throw new InvalidOperationException("Material path must identify a VMT.");
                CompanionEvidenceTier evidence = CompanionContract.ParseEvidence(ReadString(materialElement.GetProperty("evidence"), "evidence", 1, 32));
                if (!materials.TryGetValue(path, out CompanionObservedMaterial? material))
                    materials.Add(path, new CompanionObservedMaterial(path, displayPath.Replace('\\', '/'), evidence));
                else
                    material.Evidence = Max(material.Evidence, evidence);
            }

            foreach (JsonElement error in errorArray.EnumerateArray())
            {
                RequireFields(error, "lua_error", "id", "realm", "message", "source", "count");
                RegisterId(identities, "lua_errors", ReadString(error.GetProperty("id"), "lua_errors.id", 1, 64));
                string realm = ReadString(error.GetProperty("realm"), "lua_error.realm", 1, 16);
                if (realm is not ("server" or "client" or "menu"))
                    throw new InvalidOperationException("Invalid lua error realm.");
                ReadString(error.GetProperty("message"), "lua_error.message", 0, 1_024);
                string source = ReadString(error.GetProperty("source"), "lua_error.source", 0, CompanionContract.MaxPathBytes);
                if (source.Length > 0)
                    CompanionContract.NormalizeVirtualPath(source);
                ReadInteger(error.GetProperty("count"), "lua_error.count", 1, 1_000_000);
            }

            JsonElement performance = payload.GetProperty("performance");
            RequireFields(performance, "performance", "samples", "average_ms", "p95_ms", "max_ms", "queue_high_water");
            ReadInteger64(performance.GetProperty("samples"), "performance.samples", 0, long.MaxValue);
            ReadFiniteNumber(performance.GetProperty("average_ms"), "performance.average_ms", 0, double.MaxValue);
            ReadFiniteNumber(performance.GetProperty("p95_ms"), "performance.p95_ms", 0, double.MaxValue);
            ReadFiniteNumber(performance.GetProperty("max_ms"), "performance.max_ms", 0, double.MaxValue);
            ReadInteger64(performance.GetProperty("queue_high_water"), "performance.queue_high_water", 0, long.MaxValue);

            JsonElement truncation = payload.GetProperty("truncation");
            RequireFields(truncation, "truncation", "models", "materials", "lua_errors", "bytes");
            truncatedModels = checked(truncatedModels + ReadInteger64(truncation.GetProperty("models"), "truncation.models", 0, long.MaxValue));
            truncatedMaterials = checked(truncatedMaterials + ReadInteger64(truncation.GetProperty("materials"), "truncation.materials", 0, long.MaxValue));
            truncatedLuaErrors = checked(truncatedLuaErrors + ReadInteger64(truncation.GetProperty("lua_errors"), "truncation.lua_errors", 0, long.MaxValue));
            truncatedBytes = checked(truncatedBytes + ReadInteger64(truncation.GetProperty("bytes"), "truncation.bytes", 0, long.MaxValue));
        }

        private static byte[] ReadBytes(string path, int maximum, string label)
        {
            if (!File.Exists(path))
                throw new InvalidOperationException($"{label} is missing.");
            var info = new FileInfo(path);
            if (info.Length > maximum)
                throw new InvalidOperationException($"{label} exceeds its byte limit.");
            byte[] bytes = File.ReadAllBytes(path);
            if (bytes.LongLength != info.Length || bytes.LongLength > maximum)
                throw new InvalidOperationException($"{label} changed or exceeds its byte limit.");
            return bytes;
        }

        private static JsonDocument ParseObject(byte[] raw, string label)
        {
            ValidateJsonTokens(raw);
            JsonDocument document;
            try
            {
                document = JsonDocument.Parse(raw, new JsonDocumentOptions
                {
                    AllowTrailingCommas = false,
                    CommentHandling = JsonCommentHandling.Disallow,
                    MaxDepth = CompanionContract.MaxJsonDepth,
                });
            }
            catch (JsonException ex)
            {
                throw new InvalidOperationException($"{label} is not valid JSON: {ex.Message}", ex);
            }
            if (document.RootElement.ValueKind != JsonValueKind.Object)
            {
                document.Dispose();
                throw new InvalidOperationException($"{label} root must be an object.");
            }
            return document;
        }

        private static void ValidateJsonTokens(byte[] raw)
        {
            try
            {
                var reader = new Utf8JsonReader(raw, new JsonReaderOptions
                {
                    AllowTrailingCommas = false,
                    CommentHandling = JsonCommentHandling.Disallow,
                    MaxDepth = CompanionContract.MaxJsonDepth,
                });
                var objectKeys = new Stack<HashSet<string>>();
                int totalKeys = 0;
                while (reader.Read())
                {
                    if (reader.TokenType == JsonTokenType.StartObject)
                        objectKeys.Push(new HashSet<string>(StringComparer.Ordinal));
                    else if (reader.TokenType == JsonTokenType.EndObject)
                        objectKeys.Pop();
                    else if (reader.TokenType == JsonTokenType.PropertyName)
                    {
                        string key = reader.GetString() ?? string.Empty;
                        if (objectKeys.Count == 0 || !objectKeys.Peek().Add(key))
                            throw new InvalidOperationException($"Duplicate JSON key: {key}.");
                        totalKeys++;
                        if (totalKeys > CompanionContract.MaxTotalKeys)
                            throw new InvalidOperationException("JSON exceeds maximum key count.");
                    }
                    else if (reader.TokenType == JsonTokenType.Number
                        && (!reader.TryGetDouble(out double number) || !double.IsFinite(number)))
                        throw new InvalidOperationException("JSON numbers must be finite.");
                }
            }
            catch (JsonException ex)
            {
                throw new InvalidOperationException($"JSON is not valid: {ex.Message}", ex);
            }
        }

        private static void RequireFields(JsonElement value, string label, params string[] expected)
        {
            if (value.ValueKind != JsonValueKind.Object)
                throw new InvalidOperationException($"{label} must be an object.");
            var required = new HashSet<string>(expected, StringComparer.Ordinal);
            var actual = new HashSet<string>(StringComparer.Ordinal);
            foreach (JsonProperty property in value.EnumerateObject())
                actual.Add(property.Name);
            string[] unknown = actual.Except(required, StringComparer.Ordinal).OrderBy(value => value, StringComparer.Ordinal).ToArray();
            if (unknown.Length > 0)
                throw new InvalidOperationException($"{label} has unknown fields: {string.Join(", ", unknown)}.");
            string[] missing = required.Except(actual, StringComparer.Ordinal).OrderBy(value => value, StringComparer.Ordinal).ToArray();
            if (missing.Length > 0)
                throw new InvalidOperationException($"{label} is missing fields: {string.Join(", ", missing)}.");
        }

        private static JsonElement ReadArray(JsonElement value, string label, int maximum)
        {
            if (value.ValueKind != JsonValueKind.Array)
                throw new InvalidOperationException($"{label} must be an array.");
            if (value.GetArrayLength() > maximum)
                throw new InvalidOperationException($"{label} exceeds its item limit.");
            return value;
        }

        private static string ReadString(JsonElement value, string label, int minimum, int maximum)
        {
            if (value.ValueKind != JsonValueKind.String)
                throw new InvalidOperationException($"{label} must be a bounded string.");
            string result = value.GetString() ?? string.Empty;
            if (result.Length < minimum || result.Length > maximum)
                throw new InvalidOperationException($"{label} must be a bounded string.");
            return result;
        }

        private static int ReadInteger(JsonElement value, string label, int minimum, int maximum)
        {
            long result = ReadInteger64(value, label, minimum, maximum);
            return checked((int)result);
        }

        private static long ReadInteger64(JsonElement value, string label, long minimum, long maximum)
        {
            if (value.ValueKind != JsonValueKind.Number || !value.TryGetInt64(out long result) || result < minimum || result > maximum)
                throw new InvalidOperationException($"{label} must be a bounded integer.");
            return result;
        }

        private static double ReadFiniteNumber(JsonElement value, string label, double minimum, double maximum)
        {
            if (value.ValueKind != JsonValueKind.Number || !value.TryGetDouble(out double result) || !double.IsFinite(result) || result < minimum || result > maximum)
                throw new InvalidOperationException($"{label} must be finite and bounded.");
            return result;
        }

        private static string ReadSha256(JsonElement value, string label)
        {
            string result = ReadString(value, label, 64, 64);
            if (!LowerSha256Pattern.IsMatch(result))
                throw new InvalidOperationException($"{label} must be lowercase SHA-256.");
            return result;
        }

        private static Guid ReadGuid(JsonElement value, string label)
        {
            string raw = ReadString(value, label, 36, 36);
            if (!Guid.TryParseExact(raw, "D", out Guid result) || raw != result.ToString("D"))
                throw new InvalidOperationException($"Invalid {label} UUID.");
            return result;
        }

        private static (int Major, int Minor) ReadContract(JsonElement value)
        {
            RequireFields(value, "contract", "major", "minor");
            return (
                ReadInteger(value.GetProperty("major"), "contract.major", 0, int.MaxValue),
                ReadInteger(value.GetProperty("minor"), "contract.minor", 0, int.MaxValue)
            );
        }

        private static DateTimeOffset ReadTimestamp(JsonElement value, DateTimeOffset now)
        {
            string raw = ReadString(value, "created_utc", 20, 32);
            if (!raw.EndsWith('Z')
                || !DateTimeOffset.TryParseExact(
                    raw,
                    new[] { "yyyy-MM-dd'T'HH:mm:ss'Z'", "yyyy-MM-dd'T'HH:mm:ss.FFFFFFF'Z'" },
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                    out DateTimeOffset parsed))
                throw new InvalidOperationException("created_utc must be RFC 3339 UTC.");
            if (parsed > now.ToUniversalTime().AddMinutes(5))
                throw new InvalidOperationException("created_utc is in the future.");
            return parsed;
        }

        private static ProducerData ReadProducer(JsonElement value)
        {
            RequireFields(value, "producer", "name", "version");
            return new ProducerData(
                ReadString(value.GetProperty("name"), "producer.name", 1, 64),
                ReadString(value.GetProperty("version"), "producer.version", 1, 32)
            );
        }

        private static EnvironmentData ReadEnvironment(JsonElement value)
        {
            RequireFields(value, "environment", "game_build", "map", "gamemode", "realm", "session_seconds", "mounted_addons");
            string realm = ReadString(value.GetProperty("realm"), "realm", 1, 32);
            if (!Realms.Contains(realm))
                throw new InvalidOperationException($"Invalid realm: {realm}.");
            JsonElement addons = ReadArray(value.GetProperty("mounted_addons"), "environment.mounted_addons", 4_096);
            var mounted = new List<MountedAddonData>();
            foreach (JsonElement addon in addons.EnumerateArray())
            {
                RequireFields(addon, "mounted addon", "workshop_id", "title");
                string workshopId = ReadString(addon.GetProperty("workshop_id"), "mounted addon workshop_id", 1, 20);
                if (!WorkshopIdPattern.IsMatch(workshopId))
                    throw new InvalidOperationException("Invalid mounted addon workshop_id.");
                mounted.Add(new MountedAddonData(workshopId, ReadString(addon.GetProperty("title"), "mounted addon title", 0, 256)));
            }
            return new EnvironmentData(
                ReadString(value.GetProperty("game_build"), "environment.game_build", 0, 64),
                ReadString(value.GetProperty("map"), "environment.map", 0, 128),
                ReadString(value.GetProperty("gamemode"), "environment.gamemode", 0, 128),
                realm,
                ReadFiniteNumber(value.GetProperty("session_seconds"), "environment.session_seconds", 0, 604_800),
                string.Join("\n", mounted.Select(item => item.WorkshopId + "\0" + item.Title))
            );
        }

        private static void ReadChunkMetadata(JsonElement value, int sequence, int total)
        {
            RequireFields(value, "chunk", "sequence", "total");
            if (ReadInteger(value.GetProperty("sequence"), "chunk.sequence", 1, 10_000) != sequence)
                throw new InvalidOperationException("Chunk sequence is invalid.");
            if (ReadInteger(value.GetProperty("total"), "chunk.total", 1, 10_000) != total)
                throw new InvalidOperationException("Chunk total is invalid.");
        }

        private static void RegisterId(Dictionary<string, HashSet<string>> identities, string collection, string id)
        {
            if (!identities.TryGetValue(collection, out HashSet<string>? seen))
            {
                seen = new HashSet<string>(StringComparer.Ordinal);
                identities.Add(collection, seen);
            }
            if (!seen.Add(id))
                throw new InvalidOperationException($"Duplicate id across chunks in {collection}: {id}.");
        }

        private static void MergeEvidence(Dictionary<string, CompanionEvidenceTier> values, string key, CompanionEvidenceTier evidence)
        {
            if (!values.TryGetValue(key, out CompanionEvidenceTier current) || evidence > current)
                values[key] = evidence;
        }

        private static CompanionEvidenceTier Max(CompanionEvidenceTier left, CompanionEvidenceTier right) => left >= right ? left : right;
        private static string Sha256(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

        private sealed record ChunkDeclaration(int Sequence, string Filename, long Bytes, string Sha256);
        private sealed record ProducerData(string Name, string Version);
        private sealed record MountedAddonData(string WorkshopId, string Title);
        private sealed record EnvironmentData(string GameBuild, string Map, string Gamemode, string Realm, double SessionSeconds, string MountedAddons);
        private sealed record BundleMetadata(
            string Root,
            string IndexPath,
            string Schema,
            int ContractMajor,
            int ContractMinor,
            Guid RunId,
            DateTimeOffset CreatedUtc,
            ProducerData Producer,
            EnvironmentData Environment,
            string IndexSha256,
            JsonElement Index
        );
    }
}
