using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems.Companion
{
    internal sealed record CompanionValidationBodygroup(string Name, int Value);

    internal sealed record CompanionValidationTest(
        string Id,
        string Kind,
        string Path,
        IReadOnlyList<string> RequiredFiles,
        bool ExpectedPhy,
        IReadOnlyList<int> Skins,
        IReadOnlyList<CompanionValidationBodygroup> Bodygroups,
        IReadOnlyList<string> Materials,
        IReadOnlyList<string> Views,
        int Priority,
        long ExpectedBytes
    );

    internal sealed record CompanionValidationPlanResult(
        string BundleRoot,
        Guid RunId,
        string IndexSha256,
        IReadOnlyList<CompanionValidationTest> Tests,
        int ModelCount,
        int ScreenshotCount,
        int TruncatedTests,
        int TruncatedModels,
        int TruncatedScreenshots,
        bool Dx80Excluded,
        int MaximumOrdinaryViews,
        int ScreenshotRetries
    );

    internal static class CompanionValidationPlanService
    {
        private const int MaximumModels = 250;
        private const int MaximumScreenshots = 1_000;
        private const int MaximumViews = 4;
        private const int ScreenshotRetries = 1;
        private const int MaximumTests = 5_000;
        private const int MaximumReportBytes = 20_000_000;
        private static readonly HashSet<string> AllowedKinds = new(
            new[] { "model_structure", "model_state", "material_resolve", "model_render" },
            StringComparer.Ordinal
        );
        private static readonly HashSet<string> AllowedViews = new(
            new[] { "front", "side", "rear", "isometric", "closeup" },
            StringComparer.Ordinal
        );
        private static readonly HashSet<string> TextureKeys = new(
            new[]
            {
                "$basetexture", "$bumpmap", "$normalmap", "$detail", "$envmapmask",
                "$phongexponenttexture", "$selfillummask", "$blendmodulatetexture",
                "$refracttexture", "$dudvmap", "$flowmap",
            },
            StringComparer.OrdinalIgnoreCase
        );
        private static readonly Regex TextureReferencePattern = new(
            "(?im)(?:\\\"?(?<key>\\$[a-z0-9_]+)\\\"?)\\s+(?:\\\"(?<quoted>[^\\\"\\r\\n]+)\\\"|(?<plain>[^\\s{}]+))",
            RegexOptions.CultureInvariant
        );
        private static readonly Regex IdentifierPattern = new("^[a-z0-9][a-z0-9._-]{0,63}$", RegexOptions.CultureInvariant);
        private static readonly Regex BodygroupNamePattern = new("^[A-Za-z0-9_. -]{1,128}$", RegexOptions.CultureInvariant);
        private static readonly Regex ChunkFilenamePattern = new("^chunk-[0-9]{4}\\.json$", RegexOptions.CultureInvariant);
        private static readonly Regex Sha256Pattern = new("^[a-f0-9]{64}$", RegexOptions.CultureInvariant);

        public static CompanionValidationPlanResult Generate(
            string optimizedAddonRoot,
            string maximumReportPath,
            string destinationParent,
            DateTimeOffset? now = null,
            string? originalAddonRoot = null)
        {
            string addonRoot = Path.GetFullPath(optimizedAddonRoot ?? string.Empty);
            if (!Directory.Exists(addonRoot))
                throw new InvalidOperationException("Optimized addon folder is missing.");
            string originalRoot = string.IsNullOrWhiteSpace(originalAddonRoot)
                ? addonRoot
                : Path.GetFullPath(originalAddonRoot);
            if (!Directory.Exists(originalRoot))
                throw new InvalidOperationException("Original addon folder is missing.");
            IReadOnlyList<ModelInput> models = ReadModelInputs(maximumReportPath);
            models = models.Where(model => model.Changed || model.StructuralFailure).ToArray();
            if (models.Count == 0)
                throw new InvalidOperationException("Maximum report contains no changed model metadata.");

            List<ModelPlan> rankedModels = models
                .Select(model => BuildModelPlan(addonRoot, originalRoot, model))
                .OrderByDescending(model => model.Priority)
                .ThenBy(model => model.Input.ModelPath, StringComparer.Ordinal)
                .ToList();
            int truncatedModels = Math.Max(0, rankedModels.Count - MaximumModels);
            rankedModels = rankedModels.Take(MaximumModels).ToList();

            var candidates = new List<CompanionValidationTest>();
            int screenshotCount = 0;
            int truncatedScreenshots = 0;
            foreach (ModelPlan model in rankedModels)
            {
                string[] views = ViewsFor(model.Input);
                if (screenshotCount + views.Length > MaximumScreenshots)
                {
                    truncatedScreenshots += views.Length;
                    views = Array.Empty<string>();
                }
                else
                {
                    screenshotCount += views.Length;
                }
                candidates.Add(ModelStructure(model));
                candidates.Add(ModelState(model));
                if (views.Length > 0)
                    candidates.Add(ModelRender(model, views));
                foreach (MaterialPlan material in model.Materials)
                    candidates.Add(MaterialResolve(model, material));
            }

            candidates = candidates
                .OrderByDescending(test => test.Priority)
                .ThenBy(test => test.Path, StringComparer.Ordinal)
                .ThenBy(test => test.Kind, StringComparer.Ordinal)
                .ToList();
            int truncatedTests = Math.Max(0, candidates.Count - MaximumTests);
            candidates = candidates.Take(MaximumTests).ToList();
            var tests = candidates
                .Select((test, index) => test with { Id = $"test-{index + 1:000000}" })
                .ToArray();
            ValidateTestsForContract(tests);
            screenshotCount = tests
                .Where(test => test.Kind == "model_render")
                .Sum(test => test.Views.Count);

            DateTimeOffset created = (now ?? DateTimeOffset.UtcNow).ToUniversalTime();
            string createdUtc = created.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
            Guid runId = DeterministicRunId(tests, createdUtc);
            var limits = new Dictionary<string, object?>
            {
                ["max_models"] = MaximumModels,
                ["max_screenshots"] = MaximumScreenshots,
                ["max_ordinary_views"] = MaximumViews,
                ["screenshot_retries"] = ScreenshotRetries,
            };
            var truncation = new Dictionary<string, object?>
            {
                ["tests"] = truncatedTests,
                ["models"] = truncatedModels,
                ["screenshots"] = truncatedScreenshots,
            };
            List<PlanPayload> payloads = BuildPayloads(tests, limits, truncation);
            string destinationRoot = WriteBundle(
                destinationParent,
                runId,
                createdUtc,
                payloads,
                out string indexSha256
            );
            return new CompanionValidationPlanResult(
                destinationRoot,
                runId,
                indexSha256,
                tests,
                rankedModels.Count,
                screenshotCount,
                truncatedTests,
                truncatedModels,
                truncatedScreenshots,
                true,
                MaximumViews,
                ScreenshotRetries
            );
        }

        public static void ValidateTestsForContract(IEnumerable<CompanionValidationTest> source)
        {
            CompanionValidationTest[] tests = source?.ToArray()
                ?? throw new InvalidOperationException("Validation tests are missing.");
            if (tests.Length > MaximumTests)
                throw new InvalidOperationException("Validation plan exceeds its test limit.");
            var ids = new HashSet<string>(StringComparer.Ordinal);
            var modelPaths = new HashSet<string>(StringComparer.Ordinal);
            int screenshots = 0;
            foreach (CompanionValidationTest test in tests)
            {
                if (!IdentifierPattern.IsMatch(test.Id) || !ids.Add(test.Id))
                    throw new InvalidOperationException("Validation test IDs must be unique bounded identifiers.");
                if (!AllowedKinds.Contains(test.Kind))
                    throw new InvalidOperationException("Validation test kind is not in the closed dispatch table.");
                string path = NormalizePlanPath(test.Path);
                bool materialTest = test.Kind == "material_resolve";
                if (materialTest ? !path.EndsWith(".vmt", StringComparison.Ordinal) : !path.EndsWith(".mdl", StringComparison.Ordinal))
                    throw new InvalidOperationException("Validation test path does not match its kind.");
                if (!materialTest)
                    modelPaths.Add(path);
                ValidateUniquePaths(test.RequiredFiles, 64, "required files");
                ValidateUniquePaths(test.Materials, 4_096, "materials", requireVmt: true);
                if (test.Skins.Count > 256
                    || test.Skins.Any(value => value < 0 || value > 255)
                    || test.Skins.Distinct().Count() != test.Skins.Count)
                    throw new InvalidOperationException("Validation test skins are invalid.");
                if (test.Bodygroups.Count > 1_024
                    || test.Bodygroups.Any(value => !BodygroupNamePattern.IsMatch(value.Name) || value.Value < 0 || value.Value > 255)
                    || test.Bodygroups.Select(value => (value.Name, value.Value)).Distinct().Count() != test.Bodygroups.Count)
                    throw new InvalidOperationException("Validation test bodygroups are invalid.");
                if (test.Views.Count > MaximumViews
                    || test.Views.Any(view => !AllowedViews.Contains(view))
                    || test.Views.Distinct(StringComparer.Ordinal).Count() != test.Views.Count)
                    throw new InvalidOperationException("Validation test views are invalid.");
                if (test.Kind != "model_render" && test.Views.Count != 0)
                    throw new InvalidOperationException("Only model_render tests may request views.");
                screenshots += test.Views.Count;
                if (test.Priority < 0 || test.Priority > 1_000_000 || test.ExpectedBytes < 0)
                    throw new InvalidOperationException("Validation test priority/expected bytes are invalid.");
            }
            if (modelPaths.Count > MaximumModels)
                throw new InvalidOperationException("Validation plan exceeds the 250 model limit.");
            if (screenshots > MaximumScreenshots)
                throw new InvalidOperationException("Validation plan exceeds the 1000 screenshot limit.");
        }

        public static string CopyBundle(string bundleRoot, string destinationParent)
        {
            string source = Path.GetFullPath(bundleRoot ?? string.Empty);
            string destinationBase = Path.GetFullPath(destinationParent ?? string.Empty);
            VerifyGeneratedBundle(source);
            Directory.CreateDirectory(destinationBase);
            string destination = Path.Combine(destinationBase, Path.GetFileName(source));
            if (Directory.Exists(destination) || File.Exists(destination))
                throw new InvalidOperationException("Validation plan destination already exists.");
            Directory.CreateDirectory(destination);
            try
            {
                foreach (string file in DeclaredBundleFiles(source))
                    File.Copy(Path.Combine(source, file), Path.Combine(destination, file), overwrite: false);
                VerifyGeneratedBundle(destination);
                return destination;
            }
            catch
            {
                if (Directory.Exists(destination))
                    Directory.Delete(destination, recursive: true);
                throw;
            }
        }

        private static IReadOnlyList<ModelInput> ReadModelInputs(string reportPath)
        {
            string path = Path.GetFullPath(reportPath ?? string.Empty);
            if (!File.Exists(path))
                throw new InvalidOperationException("Maximum adaptive report is missing.");
            var info = new FileInfo(path);
            if (info.Length <= 0 || info.Length > MaximumReportBytes)
                throw new InvalidOperationException("Maximum adaptive report exceeds its byte limit.");
            byte[] raw = File.ReadAllBytes(path);
            if (raw.LongLength != info.Length)
                throw new InvalidOperationException("Maximum adaptive report changed while reading.");
            using JsonDocument document = JsonDocument.Parse(raw, new JsonDocumentOptions
            {
                AllowTrailingCommas = false,
                CommentHandling = JsonCommentHandling.Disallow,
                MaxDepth = 16,
            });
            JsonElement root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("schema", out JsonElement schema)
                || !schema.TryGetInt32(out int schemaValue)
                || schemaValue != 1
                || !root.TryGetProperty("model_details", out JsonElement models)
                || models.ValueKind != JsonValueKind.Array
                || models.GetArrayLength() > 50_000)
                throw new InvalidOperationException("Maximum adaptive report model metadata is invalid.");
            var result = new List<ModelInput>(models.GetArrayLength());
            foreach (JsonElement model in models.EnumerateArray())
            {
                RequireFields(
                    model,
                    "model detail",
                    "model_path", "expected_phy", "changed", "structural_failure",
                    "expected_bytes", "original_bytes", "original_triangles", "final_triangles",
                    "geometry_reduction", "maximum_risk", "maximum_runtime_importance",
                    "visual_sensitive", "materials", "skins", "bodygroups"
                );
                string modelPath = NormalizePlanPath(ReadString(model, "model_path", 1, CompanionContract.MaxPathBytes));
                if (!modelPath.StartsWith("models/", StringComparison.Ordinal) || !modelPath.EndsWith(".mdl", StringComparison.Ordinal))
                    throw new InvalidOperationException("Model detail path must be a virtual MDL path.");
                string[] materials = ReadStringArray(model, "materials", 4_096)
                    .Select(NormalizePlanPath)
                    .ToArray();
                if (materials.Any(value => !value.EndsWith(".vmt", StringComparison.Ordinal)))
                    throw new InvalidOperationException("Model detail materials must be VMT paths.");
                int[] skins = ReadIntegerArray(model, "skins", 256, 0, 255);
                if (!skins.Contains(0))
                    skins = new[] { 0 }.Concat(skins).ToArray();
                JsonElement bodygroupArray = RequiredArray(model, "bodygroups", 1_024);
                var bodygroups = new List<CompanionValidationBodygroup>();
                foreach (JsonElement bodygroup in bodygroupArray.EnumerateArray())
                {
                    RequireFields(bodygroup, "bodygroup", "name", "value");
                    string name = ReadString(bodygroup, "name", 1, 128);
                    if (!BodygroupNamePattern.IsMatch(name))
                        throw new InvalidOperationException("Bodygroup name is unsafe.");
                    bodygroups.Add(new CompanionValidationBodygroup(name, ReadInteger(bodygroup, "value", 0, 255)));
                }
                result.Add(new ModelInput(
                    modelPath,
                    ReadBoolean(model, "expected_phy"),
                    ReadBoolean(model, "changed"),
                    ReadBoolean(model, "structural_failure"),
                    ReadInteger64(model, "expected_bytes", 0),
                    ReadInteger64(model, "original_bytes", 0),
                    ReadInteger64(model, "original_triangles", 0),
                    ReadInteger64(model, "final_triangles", 0),
                    ReadFinite(model, "geometry_reduction", 0.0, 1.0),
                    ReadFinite(model, "maximum_risk", 0.0, 1.0),
                    ReadFinite(model, "maximum_runtime_importance", 0.0, 1.0),
                    ReadBoolean(model, "visual_sensitive"),
                    materials.Distinct(StringComparer.Ordinal).OrderBy(value => value, StringComparer.Ordinal).ToArray(),
                    skins.Distinct().OrderBy(value => value).ToArray(),
                    bodygroups.Distinct().OrderBy(value => value.Name, StringComparer.Ordinal).ThenBy(value => value.Value).ToArray()
                ));
            }
            return result;
        }

        private static ModelPlan BuildModelPlan(string addonRoot, string originalRoot, ModelInput input)
        {
            var required = new List<string>
            {
                input.ModelPath,
                ChangeVirtualExtension(input.ModelPath, ".vvd"),
                ChangeVirtualExtension(input.ModelPath, ".dx90.vtx"),
            };
            if (input.ExpectedPhy)
                required.Add(ChangeVirtualExtension(input.ModelPath, ".phy"));
            required.Sort(StringComparer.Ordinal);
            var materials = input.Materials
                .Select(path => BuildMaterialPlan(addonRoot, originalRoot, path, input))
                .ToArray();
            int priority = PriorityFor(input, materials.Select(value => value.Reduction).DefaultIfEmpty(0.0).Max());
            long expectedBytes = required.Sum(path => ExistingBytes(addonRoot, path));
            return new ModelPlan(input, required.ToArray(), materials, priority, expectedBytes);
        }

        private static MaterialPlan BuildMaterialPlan(string addonRoot, string originalRoot, string materialPath, ModelInput model)
        {
            string diskPath = Path.Combine(addonRoot, materialPath.Replace('/', Path.DirectorySeparatorChar));
            var required = new SortedSet<string>(StringComparer.Ordinal) { materialPath };
            if (File.Exists(diskPath))
            {
                var info = new FileInfo(diskPath);
                if (info.Length <= 2_000_000)
                {
                    string text = File.ReadAllText(diskPath, Encoding.UTF8);
                    foreach (Match match in TextureReferencePattern.Matches(text))
                    {
                        if (!TextureKeys.Contains(match.Groups["key"].Value))
                            continue;
                        string token = match.Groups["quoted"].Success
                            ? match.Groups["quoted"].Value
                            : match.Groups["plain"].Value;
                        string? texture = NormalizeTextureReference(token);
                        if (texture != null)
                            required.Add(texture);
                        if (required.Count >= 64)
                            break;
                    }
                }
            }
            long bytes = required.Sum(path => ExistingBytes(addonRoot, path));
            long originalBytes = required.Sum(path => ExistingBytes(originalRoot, path));
            double reduction = originalBytes <= 0
                ? 0.0
                : Math.Max(0.0, Math.Min(1.0, 1.0 - bytes / (double)originalBytes));
            int priority = model.MaximumRuntimeImportance > 0.0
                ? 800_000
                : model.VisualSensitive
                    ? 700_000
                    : reduction >= 0.25
                        ? 500_000 + (int)(reduction * 10_000)
                        : 100_000;
            return new MaterialPlan(materialPath, required.ToArray(), priority, bytes, reduction);
        }

        private static CompanionValidationTest ModelStructure(ModelPlan model) => new(
            string.Empty, "model_structure", model.Input.ModelPath, model.RequiredFiles,
            model.Input.ExpectedPhy, new[] { 0 }, Array.Empty<CompanionValidationBodygroup>(),
            model.Input.Materials, Array.Empty<string>(), model.Priority + 30, model.ExpectedBytes
        );

        private static CompanionValidationTest ModelState(ModelPlan model) => new(
            string.Empty, "model_state", model.Input.ModelPath, new[] { model.Input.ModelPath },
            model.Input.ExpectedPhy, model.Input.Skins.Contains(0) ? model.Input.Skins : new[] { 0 }.Concat(model.Input.Skins).ToArray(),
            model.Input.Bodygroups, model.Input.Materials, Array.Empty<string>(), model.Priority + 20, model.ExpectedBytes
        );

        private static CompanionValidationTest ModelRender(ModelPlan model, string[] views) => new(
            string.Empty, "model_render", model.Input.ModelPath, model.RequiredFiles,
            model.Input.ExpectedPhy, model.Input.Skins.Contains(0) ? model.Input.Skins : new[] { 0 }.Concat(model.Input.Skins).ToArray(), model.Input.Bodygroups,
            model.Input.Materials, views, model.Priority + 10, model.ExpectedBytes
        );

        private static CompanionValidationTest MaterialResolve(ModelPlan model, MaterialPlan material) => new(
            string.Empty, "material_resolve", material.Path, material.RequiredFiles,
            false, Array.Empty<int>(), Array.Empty<CompanionValidationBodygroup>(),
            new[] { material.Path }, Array.Empty<string>(), Math.Max(model.Priority, material.Priority), material.ExpectedBytes
        );

        private static int PriorityFor(ModelInput model, double maximumTextureReduction)
        {
            if (model.StructuralFailure)
                return 900_000;
            if (model.MaximumRuntimeImportance > 0.0)
                return 800_000 + (int)(model.MaximumRuntimeImportance * 10_000);
            if (model.VisualSensitive)
                return 700_000 + (int)(model.MaximumRisk * 10_000);
            if (model.GeometryReduction >= 0.25)
                return 600_000 + (int)(model.GeometryReduction * 10_000);
            if (maximumTextureReduction >= 0.25)
                return 500_000 + (int)(maximumTextureReduction * 10_000);
            return 100_000 + (int)(model.MaximumRisk * 10_000);
        }

        private static string[] ViewsFor(ModelInput model)
            => SelectViewsForContract(model.MaximumRisk, model.MaximumRuntimeImportance).ToArray();

        internal static IReadOnlyList<string> SelectViewsForContract(double maximumRisk, double maximumRuntimeImportance)
        {
            if (!double.IsFinite(maximumRisk) || maximumRisk < 0.0 || maximumRisk > 1.0
                || !double.IsFinite(maximumRuntimeImportance) || maximumRuntimeImportance < 0.0 || maximumRuntimeImportance > 1.0)
                throw new InvalidOperationException("Validation view risk inputs must be finite and bounded.");
            if (maximumRisk >= 0.70 || maximumRuntimeImportance >= 0.75)
                return new[] { "front", "side", "rear", "isometric" };
            if (maximumRisk >= 0.40)
                return new[] { "front", "isometric" };
            return new[] { "isometric" };
        }

        private static List<PlanPayload> BuildPayloads(
            CompanionValidationTest[] tests,
            Dictionary<string, object?> limits,
            Dictionary<string, object?> truncation)
        {
            var result = new List<PlanPayload>();
            var current = new List<CompanionValidationTest>();
            foreach (CompanionValidationTest test in tests)
            {
                current.Add(test);
                var candidate = new PlanPayload(current.ToArray(), limits, result.Count == 0 ? truncation : ZeroTruncation());
                if (JsonBytes(PayloadRecord(candidate)).Length <= CompanionContract.MaxChunkBytes - 4_096)
                    continue;
                current.RemoveAt(current.Count - 1);
                if (current.Count == 0)
                    throw new InvalidOperationException("A validation test exceeds the chunk byte limit.");
                result.Add(new PlanPayload(current.ToArray(), limits, result.Count == 0 ? truncation : ZeroTruncation()));
                current.Clear();
                current.Add(test);
            }
            if (current.Count > 0)
                result.Add(new PlanPayload(current.ToArray(), limits, result.Count == 0 ? truncation : ZeroTruncation()));
            return result;
        }

        private static string WriteBundle(
            string destinationParent,
            Guid runId,
            string createdUtc,
            IReadOnlyList<PlanPayload> payloads,
            out string indexSha256)
        {
            string parent = Path.GetFullPath(destinationParent ?? string.Empty);
            Directory.CreateDirectory(parent);
            string destination = Path.Combine(parent, runId.ToString("D"));
            if (Directory.Exists(destination) || File.Exists(destination))
                throw new InvalidOperationException("Validation plan destination already exists.");
            string partial = Path.Combine(parent, $".{runId:N}.{Guid.NewGuid():N}.partial");
            Directory.CreateDirectory(partial);
            try
            {
                var producer = new Dictionary<string, object?> { ["name"] = "GMod Addon Optimizer", ["version"] = "1.0.0" };
                var environment = new Dictionary<string, object?>
                {
                    ["game_build"] = string.Empty, ["map"] = string.Empty, ["gamemode"] = string.Empty,
                    ["realm"] = "client", ["session_seconds"] = 0.0, ["mounted_addons"] = Array.Empty<object>(),
                };
                var declarations = new List<Dictionary<string, object?>>();
                long totalBytes = 0;
                for (int index = 0; index < payloads.Count; index++)
                {
                    byte[] chunk = JsonBytes(new Dictionary<string, object?>
                    {
                        ["schema"] = "sao-validation-plan-v1",
                        ["contract"] = Contract(), ["run_id"] = runId.ToString("D"), ["created_utc"] = createdUtc,
                        ["producer"] = producer, ["environment"] = environment,
                        ["chunk"] = new Dictionary<string, object?> { ["sequence"] = index + 1, ["total"] = payloads.Count },
                        ["payload"] = PayloadRecord(payloads[index]),
                    });
                    if (chunk.Length > CompanionContract.MaxChunkBytes)
                        throw new InvalidOperationException("Validation plan chunk exceeds its byte limit.");
                    totalBytes = checked(totalBytes + chunk.Length);
                    if (totalBytes > CompanionContract.MaxBundleBytes)
                        throw new InvalidOperationException("Validation plan exceeds its bundle byte limit.");
                    string filename = $"chunk-{index + 1:0000}.json";
                    WriteNew(Path.Combine(partial, filename), chunk);
                    declarations.Add(new Dictionary<string, object?>
                    {
                        ["sequence"] = index + 1, ["file"] = filename, ["bytes"] = chunk.Length, ["sha256"] = Sha256(chunk),
                    });
                }
                byte[] indexBytes = JsonBytes(new Dictionary<string, object?>
                {
                    ["schema"] = "sao-run-index-v1", ["contract"] = Contract(), ["run_id"] = runId.ToString("D"),
                    ["payload_schema"] = "sao-validation-plan-v1", ["created_utc"] = createdUtc,
                    ["producer"] = producer, ["environment"] = environment,
                    ["chunk_count"] = payloads.Count, ["total_bytes"] = totalBytes, ["chunks"] = declarations,
                });
                indexSha256 = Sha256(indexBytes);
                WriteNew(Path.Combine(partial, "run-index.json"), indexBytes);
                WriteNew(Path.Combine(partial, "complete.ready.json"), JsonBytes(new Dictionary<string, object?>
                {
                    ["schema"] = "sao-completion-v1", ["run_id"] = runId.ToString("D"), ["index_sha256"] = indexSha256,
                }));
                VerifyGeneratedBundle(partial);
                Directory.Move(partial, destination);
                VerifyGeneratedBundle(destination);
                return destination;
            }
            catch
            {
                if (Directory.Exists(partial))
                    Directory.Delete(partial, recursive: true);
                throw;
            }
        }

        private static void VerifyGeneratedBundle(string root)
        {
            string bundleRoot = Path.GetFullPath(root ?? string.Empty);
            byte[] indexBytes = ReadBoundedFile(Path.Combine(bundleRoot, "run-index.json"), CompanionContract.MaxIndexBytes, "index");
            byte[] readyBytes = ReadBoundedFile(Path.Combine(bundleRoot, "complete.ready.json"), CompanionContract.MaxCompletionBytes, "completion marker");
            using JsonDocument indexDocument = JsonDocument.Parse(indexBytes, StrictJsonOptions());
            JsonElement index = indexDocument.RootElement;
            RequireFields(index, "validation index", "schema", "contract", "run_id", "payload_schema", "created_utc", "producer", "environment", "chunk_count", "total_bytes", "chunks");
            if (index.GetProperty("schema").GetString() != "sao-run-index-v1"
                || index.GetProperty("payload_schema").GetString() != "sao-validation-plan-v1")
                throw new InvalidOperationException("Validation plan index schema is invalid.");
            string runId = index.GetProperty("run_id").GetString() ?? string.Empty;
            if (!Guid.TryParseExact(runId, "D", out _))
                throw new InvalidOperationException("Validation plan run ID is invalid.");
            ChunkDeclaration[] declarations = ReadChunkDeclarations(index);
            if (!index.GetProperty("chunk_count").TryGetInt32(out int chunkCount) || chunkCount != declarations.Length)
                throw new InvalidOperationException("Validation plan chunk count is invalid.");
            if (!index.GetProperty("total_bytes").TryGetInt64(out long declaredTotal) || declaredTotal < 0 || declaredTotal > CompanionContract.MaxBundleBytes)
                throw new InvalidOperationException("Validation plan total bytes are invalid.");

            using JsonDocument readyDocument = JsonDocument.Parse(readyBytes, StrictJsonOptions());
            RequireFields(readyDocument.RootElement, "validation completion", "schema", "run_id", "index_sha256");
            if (readyDocument.RootElement.GetProperty("schema").GetString() != "sao-completion-v1"
                || readyDocument.RootElement.GetProperty("run_id").GetString() != runId
                || readyDocument.RootElement.GetProperty("index_sha256").GetString() != Sha256(indexBytes))
                throw new InvalidOperationException("Validation plan completion hash is invalid.");
            var allTests = new List<CompanionValidationTest>();
            long actualTotal = 0;
            foreach (ChunkDeclaration declaration in declarations)
            {
                byte[] bytes = ReadBoundedFile(Path.Combine(bundleRoot, declaration.Filename), CompanionContract.MaxChunkBytes, "chunk");
                actualTotal = checked(actualTotal + bytes.LongLength);
                if (bytes.LongLength != declaration.Bytes || Sha256(bytes) != declaration.Sha256)
                    throw new InvalidOperationException("Validation plan chunk hash/size is invalid.");
                using JsonDocument chunk = JsonDocument.Parse(bytes, StrictJsonOptions());
                JsonElement chunkRoot = chunk.RootElement;
                RequireFields(chunkRoot, "validation chunk", "schema", "contract", "run_id", "created_utc", "producer", "environment", "chunk", "payload");
                if (chunkRoot.GetProperty("schema").GetString() != "sao-validation-plan-v1"
                    || chunkRoot.GetProperty("run_id").GetString() != runId)
                    throw new InvalidOperationException("Validation plan chunk identity is invalid.");
                JsonElement payload = chunkRoot.GetProperty("payload");
                RequireFields(payload, "validation payload", "tests", "limits", "dx80_excluded", "truncation");
                ValidateGeneratedLimits(payload);
                CompanionValidationTest[] tests = ReadGeneratedTests(payload.GetProperty("tests"));
                ValidateTestsForContract(tests);
                allTests.AddRange(tests);
            }
            if (actualTotal != declaredTotal)
                throw new InvalidOperationException("Validation plan total bytes do not match the index.");
            ValidateTestsForContract(allTests);
        }

        private static IEnumerable<string> DeclaredBundleFiles(string root)
        {
            yield return "run-index.json";
            yield return "complete.ready.json";
            byte[] bytes = ReadBoundedFile(Path.Combine(Path.GetFullPath(root), "run-index.json"), CompanionContract.MaxIndexBytes, "index");
            using JsonDocument index = JsonDocument.Parse(bytes, StrictJsonOptions());
            foreach (ChunkDeclaration declaration in ReadChunkDeclarations(index.RootElement))
                yield return declaration.Filename;
        }

        private static CompanionValidationTest[] ReadGeneratedTests(JsonElement values)
        {
            if (values.ValueKind != JsonValueKind.Array || values.GetArrayLength() > MaximumTests)
                throw new InvalidOperationException("Validation plan tests must be a bounded array.");
            return values.EnumerateArray().Select(test =>
            {
                RequireFields(test, "validation test", "id", "kind", "path", "required_files", "expected_phy", "skins", "bodygroups", "materials", "views", "priority", "expected_bytes");
                return new CompanionValidationTest(
                    test.GetProperty("id").GetString()!, test.GetProperty("kind").GetString()!, test.GetProperty("path").GetString()!,
                    test.GetProperty("required_files").EnumerateArray().Select(value => value.GetString()!).ToArray(),
                    test.GetProperty("expected_phy").GetBoolean(), test.GetProperty("skins").EnumerateArray().Select(value => value.GetInt32()).ToArray(),
                    test.GetProperty("bodygroups").EnumerateArray().Select(value =>
                    {
                        RequireFields(value, "validation bodygroup", "name", "value");
                        return new CompanionValidationBodygroup(value.GetProperty("name").GetString()!, value.GetProperty("value").GetInt32());
                    }).ToArray(),
                    test.GetProperty("materials").EnumerateArray().Select(value => value.GetString()!).ToArray(),
                    test.GetProperty("views").EnumerateArray().Select(value => value.GetString()!).ToArray(),
                    test.GetProperty("priority").GetInt32(), test.GetProperty("expected_bytes").GetInt64()
                );
            }).ToArray();
        }

        private static void ValidateGeneratedLimits(JsonElement payload)
        {
            if (payload.GetProperty("dx80_excluded").ValueKind != JsonValueKind.True)
                throw new InvalidOperationException("Validation plans must exclude DX80.");
            JsonElement limits = payload.GetProperty("limits");
            RequireFields(limits, "validation limits", "max_models", "max_screenshots", "max_ordinary_views", "screenshot_retries");
            if (limits.GetProperty("max_models").GetInt32() != MaximumModels
                || limits.GetProperty("max_screenshots").GetInt32() != MaximumScreenshots
                || limits.GetProperty("max_ordinary_views").GetInt32() != MaximumViews
                || limits.GetProperty("screenshot_retries").GetInt32() != ScreenshotRetries)
                throw new InvalidOperationException("Validation plan limits are invalid.");
            RequireFields(payload.GetProperty("truncation"), "validation truncation", "tests", "models", "screenshots");
        }

        private static ChunkDeclaration[] ReadChunkDeclarations(JsonElement index)
        {
            JsonElement chunks = index.GetProperty("chunks");
            if (chunks.ValueKind != JsonValueKind.Array || chunks.GetArrayLength() > MaximumTests || chunks.GetArrayLength() == 0)
                throw new InvalidOperationException("Validation plan chunk declarations are invalid.");
            var names = new HashSet<string>(StringComparer.Ordinal);
            return chunks.EnumerateArray().Select((declaration, offset) =>
            {
                RequireFields(declaration, "chunk declaration", "sequence", "file", "bytes", "sha256");
                string filename = declaration.GetProperty("file").GetString() ?? string.Empty;
                string digest = declaration.GetProperty("sha256").GetString() ?? string.Empty;
                if (!declaration.GetProperty("sequence").TryGetInt32(out int sequence) || sequence != offset + 1
                    || !ChunkFilenamePattern.IsMatch(filename) || !names.Add(filename)
                    || !declaration.GetProperty("bytes").TryGetInt64(out long length) || length <= 0 || length > CompanionContract.MaxChunkBytes
                    || !Sha256Pattern.IsMatch(digest))
                    throw new InvalidOperationException("Validation plan chunk declaration is unsafe.");
                return new ChunkDeclaration(filename, length, digest);
            }).ToArray();
        }

        private static byte[] ReadBoundedFile(string path, int maximumBytes, string label)
        {
            var info = new FileInfo(path);
            if (!info.Exists || info.Length <= 0 || info.Length > maximumBytes)
                throw new InvalidOperationException($"Validation plan {label} is missing or exceeds its byte limit.");
            byte[] bytes = File.ReadAllBytes(path);
            if (bytes.LongLength != info.Length)
                throw new InvalidOperationException($"Validation plan {label} changed while reading.");
            return bytes;
        }

        private static JsonDocumentOptions StrictJsonOptions() => new()
        {
            AllowTrailingCommas = false,
            CommentHandling = JsonCommentHandling.Disallow,
            MaxDepth = 24,
        };

        private static Dictionary<string, object?> PayloadRecord(PlanPayload payload) => new()
        {
            ["tests"] = payload.Tests.Select(TestRecord).ToArray(),
            ["limits"] = payload.Limits,
            ["dx80_excluded"] = true,
            ["truncation"] = payload.Truncation,
        };

        private static Dictionary<string, object?> TestRecord(CompanionValidationTest test) => new()
        {
            ["id"] = test.Id, ["kind"] = test.Kind, ["path"] = test.Path,
            ["required_files"] = test.RequiredFiles, ["expected_phy"] = test.ExpectedPhy,
            ["skins"] = test.Skins,
            ["bodygroups"] = test.Bodygroups.Select(value => new Dictionary<string, object?> { ["name"] = value.Name, ["value"] = value.Value }).ToArray(),
            ["materials"] = test.Materials, ["views"] = test.Views,
            ["priority"] = test.Priority, ["expected_bytes"] = test.ExpectedBytes,
        };

        private static Dictionary<string, object?> ZeroTruncation() => new() { ["tests"] = 0, ["models"] = 0, ["screenshots"] = 0 };
        private static Dictionary<string, object?> Contract() => new() { ["major"] = 1, ["minor"] = 0 };
        private static byte[] JsonBytes(object value) => Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value) + "\n");
        private static string Sha256(byte[] value) => Convert.ToHexString(SHA256.HashData(value)).ToLowerInvariant();
        private static void WriteNew(string path, byte[] bytes)
        {
            using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            stream.Write(bytes);
            stream.Flush(flushToDisk: true);
        }

        private static Guid DeterministicRunId(CompanionValidationTest[] tests, string createdUtc)
        {
            byte[] hash = SHA256.HashData(JsonBytes(new Dictionary<string, object?>
            {
                ["created_utc"] = createdUtc,
                ["tests"] = tests.Select(TestRecord).ToArray(),
            }));
            return new Guid(hash[..16]);
        }

        private static string NormalizePlanPath(string value)
        {
            string normalized = CompanionContract.NormalizeVirtualPath(value);
            if (normalized.StartsWith("lua/", StringComparison.Ordinal)
                || normalized.StartsWith("sound/", StringComparison.Ordinal)
                || normalized.StartsWith("maps/", StringComparison.Ordinal))
                throw new InvalidOperationException("Validation plans contain a forbidden path category.");
            if (!normalized.StartsWith("models/", StringComparison.Ordinal)
                && !normalized.StartsWith("materials/", StringComparison.Ordinal))
                throw new InvalidOperationException("Validation plan path must be a model/material virtual path.");
            return normalized;
        }

        private static string? NormalizeTextureReference(string token)
        {
            string value = token.Trim().Replace('\\', '/');
            if (value.Length == 0 || value.Contains(':') || value.StartsWith("_rt_", StringComparison.OrdinalIgnoreCase) || value.Equals("env_cubemap", StringComparison.OrdinalIgnoreCase))
                return null;
            if (value.StartsWith("materials/", StringComparison.OrdinalIgnoreCase))
                value = value["materials/".Length..];
            if (!value.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
                value += ".vtf";
            try
            {
                string normalized = NormalizePlanPath("materials/" + value);
                return normalized.EndsWith(".vtf", StringComparison.Ordinal) ? normalized : null;
            }
            catch (InvalidOperationException)
            {
                return null;
            }
        }

        private static string ChangeVirtualExtension(string path, string extension)
        {
            int dot = path.LastIndexOf('.');
            return path[..dot] + extension;
        }

        private static long ExistingBytes(string root, string virtualPath)
        {
            string path = Path.Combine(root, virtualPath.Replace('/', Path.DirectorySeparatorChar));
            return File.Exists(path) ? new FileInfo(path).Length : 0;
        }

        private static void ValidateUniquePaths(IEnumerable<string> values, int maximum, string label, bool requireVmt = false)
        {
            string[] paths = values?.ToArray() ?? throw new InvalidOperationException($"Validation test {label} are missing.");
            if (paths.Length > maximum || paths.Distinct(StringComparer.Ordinal).Count() != paths.Length)
                throw new InvalidOperationException($"Validation test {label} exceed their unique item limit.");
            foreach (string raw in paths)
            {
                string path = NormalizePlanPath(raw);
                if (requireVmt && !path.EndsWith(".vmt", StringComparison.Ordinal))
                    throw new InvalidOperationException("Validation test materials must identify VMT files.");
            }
        }

        private static void RequireFields(JsonElement value, string label, params string[] expected)
        {
            if (value.ValueKind != JsonValueKind.Object)
                throw new InvalidOperationException($"Input {label} must be an object.");
            var required = new HashSet<string>(expected, StringComparer.Ordinal);
            var actual = value.EnumerateObject().Select(property => property.Name).ToArray();
            if (actual.Length != actual.Distinct(StringComparer.Ordinal).Count()
                || !required.SetEquals(actual))
                throw new InvalidOperationException($"Input {label} has unknown, duplicate, or missing fields.");
        }

        private static string ReadString(JsonElement parent, string name, int minimum, int maximum)
        {
            if (!parent.TryGetProperty(name, out JsonElement value) || value.ValueKind != JsonValueKind.String)
                throw new InvalidOperationException($"Maximum report {name} must be a string.");
            string result = value.GetString() ?? string.Empty;
            if (result.Length < minimum || result.Length > maximum)
                throw new InvalidOperationException($"Maximum report {name} is out of bounds.");
            return result;
        }

        private static JsonElement RequiredArray(JsonElement parent, string name, int maximum)
        {
            if (!parent.TryGetProperty(name, out JsonElement value) || value.ValueKind != JsonValueKind.Array || value.GetArrayLength() > maximum)
                throw new InvalidOperationException($"Maximum report {name} must be a bounded array.");
            return value;
        }

        private static string[] ReadStringArray(JsonElement parent, string name, int maximum) =>
            RequiredArray(parent, name, maximum).EnumerateArray().Select(value =>
            {
                if (value.ValueKind != JsonValueKind.String)
                    throw new InvalidOperationException($"Maximum report {name} must contain strings.");
                return value.GetString()!;
            }).ToArray();

        private static int[] ReadIntegerArray(JsonElement parent, string name, int maximum, int minimum, int upper) =>
            RequiredArray(parent, name, maximum).EnumerateArray().Select(value =>
            {
                if (!value.TryGetInt32(out int result) || result < minimum || result > upper)
                    throw new InvalidOperationException($"Maximum report {name} must contain bounded integers.");
                return result;
            }).ToArray();

        private static bool ReadBoolean(JsonElement parent, string name)
        {
            if (!parent.TryGetProperty(name, out JsonElement value) || value.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                throw new InvalidOperationException($"Maximum report {name} must be boolean.");
            return value.GetBoolean();
        }

        private static int ReadInteger(JsonElement parent, string name, int minimum, int maximum)
        {
            long result = ReadInteger64(parent, name, minimum);
            if (result > maximum)
                throw new InvalidOperationException($"Maximum report {name} is out of bounds.");
            return (int)result;
        }

        private static long ReadInteger64(JsonElement parent, string name, long minimum)
        {
            if (!parent.TryGetProperty(name, out JsonElement value) || !value.TryGetInt64(out long result) || result < minimum)
                throw new InvalidOperationException($"Maximum report {name} must be a bounded integer.");
            return result;
        }

        private static double ReadFinite(JsonElement parent, string name, double minimum, double maximum)
        {
            if (!parent.TryGetProperty(name, out JsonElement value)
                || !value.TryGetDouble(out double result) || !double.IsFinite(result)
                || result < minimum || result > maximum)
                throw new InvalidOperationException($"Maximum report {name} must be finite and bounded.");
            return result;
        }

        private sealed record ModelInput(
            string ModelPath, bool ExpectedPhy, bool Changed, bool StructuralFailure,
            long ExpectedBytes, long OriginalBytes, long OriginalTriangles, long FinalTriangles,
            double GeometryReduction, double MaximumRisk, double MaximumRuntimeImportance,
            bool VisualSensitive, string[] Materials, int[] Skins,
            CompanionValidationBodygroup[] Bodygroups
        );
        private sealed record MaterialPlan(string Path, string[] RequiredFiles, int Priority, long ExpectedBytes, double Reduction);
        private sealed record ModelPlan(ModelInput Input, string[] RequiredFiles, MaterialPlan[] Materials, int Priority, long ExpectedBytes);
        private sealed record PlanPayload(CompanionValidationTest[] Tests, Dictionary<string, object?> Limits, Dictionary<string, object?> Truncation);
        private sealed record ChunkDeclaration(string Filename, long Bytes, string Sha256);
    }
}
