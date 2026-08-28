using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record VtfConsumerFailure(
    string Code,
    string ConsumerPath,
    string Detail);

internal sealed record VtfConsumerEvidence(
    string ConsumerPath,
    string Shader,
    string Key,
    string TextureKey,
    IReadOnlySet<int> ReadChannels,
    bool Dynamic,
    string ResolutionTrace)
{
    internal IReadOnlyDictionary<string, IReadOnlyList<string>> Parameters { get; init; } =
        new Dictionary<string, IReadOnlyList<string>>(StringComparer.OrdinalIgnoreCase);

    internal IReadOnlyList<string> Uncertainties { get; init; } = Array.Empty<string>();
}

internal sealed record VtfConsumerGraph(
    IReadOnlyDictionary<string, IReadOnlyList<VtfConsumerEvidence>> ConsumersByTexture,
    IReadOnlyList<VtfConsumerFailure> Failures,
    IReadOnlyList<string> GlobalUncertainties,
    int RawVmtCount,
    int EffectiveVmtCount)
{
    internal bool IsClosed => Failures.Count == 0 && GlobalUncertainties.Count == 0;

    internal IReadOnlyList<VtfConsumerEvidence> ConsumersFor(string textureKey) =>
        ConsumersByTexture.TryGetValue(textureKey, out IReadOnlyList<VtfConsumerEvidence>? consumers)
            ? consumers
            : Array.Empty<VtfConsumerEvidence>();
}

internal static class VtfConsumerGraphBuilder
{
    private static readonly Regex LuaMaterialCall = new(
        @"(?<![A-Za-z0-9_])Material\s*\((?<argument>[^)\r\n]*)\)",
        RegexOptions.Compiled | RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);

    private static readonly Regex LuaLiteralArgument = new(
        @"^\s*(?<quote>['""])(?<value>.*)\k<quote>\s*(?:,.*)?$",
        RegexOptions.Compiled | RegexOptions.CultureInvariant | RegexOptions.Singleline);

    private static readonly Regex LuaCreateMaterialCall = new(
        @"(?<![A-Za-z0-9_])CreateMaterial\s*\(",
        RegexOptions.Compiled | RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);

    private static readonly Regex PcfTexturePath = new(
        @"(?i)(?:materials[/\\])?[a-z0-9_./\\-]+\.vtf",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    private static readonly HashSet<string> KnownTextureRoles = new(
        new[]
        {
            "$basetexture", "$basetexture2", "$bumpmap", "$bumpmap2",
            "$normalmap", "$detail", "$envmapmask", "$phongexponenttexture",
            "$selfillummask", "$lightwarptexture", "$phongwarptexture",
            "$blendmodulatetexture", "$emissiveblendtexture",
            "$emissiveblendbasetexture", "$emissiveblendflowtexture",
            "$refracttinttexture", "$dudvmap", "$flowmap", "$compress",
            "$stretch", "$texture1", "$texture2", "$texture3", "$texture4"
        },
        StringComparer.OrdinalIgnoreCase);

    private static readonly string[] BaseAlphaParameters =
    {
        "$translucent", "$alphatest", "$selfillum", "$basealphaenvmapmask",
        "$basemapalphaphongmask", "$blendtintbybasealpha", "$distancealpha",
        "$detail_alpha_mask_base_texture", "$allowalphatocoverage"
    };

    private static readonly string[] NormalAlphaParameters =
    {
        "$normalmapalphaenvmapmask", "$normalmapalphaphongmask"
    };

    internal static VtfConsumerGraph Build(
        string rawRoot,
        string effectiveSafeRoot,
        IReadOnlyList<string> frameworkRoots)
    {
        string raw = RequireDirectory(rawRoot, nameof(rawRoot));
        string safe = RequireDirectory(effectiveSafeRoot, nameof(effectiveSafeRoot));
        ArgumentNullException.ThrowIfNull(frameworkRoots);

        var failures = new List<VtfConsumerFailure>();
        var failureKeys = new HashSet<string>(StringComparer.Ordinal);
        var uncertainties = new SortedSet<string>(StringComparer.Ordinal);

        IReadOnlyList<string> rawVmtFiles = EnumerateFiles(
            raw,
            ".vmt",
            failures,
            failureKeys,
            "raw");
        IReadOnlyList<string> safeVmtFiles = EnumerateFiles(
            safe,
            ".vmt",
            failures,
            failureKeys,
            "safe");

        var rawPaths = new HashSet<string>(
            rawVmtFiles.Select(path => Relative(raw, path)),
            StringComparer.OrdinalIgnoreCase);
        var safePaths = new HashSet<string>(
            safeVmtFiles.Select(path => Relative(safe, path)),
            StringComparer.OrdinalIgnoreCase);
        foreach (string missing in rawPaths.Except(safePaths, StringComparer.OrdinalIgnoreCase))
        {
            AddFailure(
                failures,
                failureKeys,
                "missing_effective_vmt",
                missing,
                "raw VMT has no matching effective safe-tree VMT");
        }

        var materials = new Dictionary<string, MaterialSource>(StringComparer.OrdinalIgnoreCase);
        foreach (string path in safeVmtFiles)
        {
            string relative = Relative(safe, path);
            AddMaterialSource(
                materials,
                new MaterialSource(
                    MaterialNameFromRelative(relative),
                    path,
                    relative,
                    IsEffective: true),
                failures,
                failureKeys);
        }

        for (int index = 0; index < frameworkRoots.Count; index++)
        {
            string requested = frameworkRoots[index];
            if (string.IsNullOrWhiteSpace(requested) || !Directory.Exists(requested))
            {
                AddFailure(
                    failures,
                    failureKeys,
                    "framework_root_missing",
                    requested ?? "(null)",
                    $"framework root {index} does not exist");
                continue;
            }

            string framework = Path.GetFullPath(requested);
            IReadOnlyList<string> frameworkVmts = EnumerateFiles(
                framework,
                ".vmt",
                failures,
                failureKeys,
                $"framework[{index}]");
            foreach (string path in frameworkVmts)
            {
                string relative = Relative(framework, path);
                string materialName = MaterialNameFromRelative(relative);
                if (materials.ContainsKey(materialName))
                {
                    continue;
                }

                AddMaterialSource(
                    materials,
                    new MaterialSource(
                        materialName,
                        path,
                        $"framework[{index}]/{relative}",
                        IsEffective: false),
                    failures,
                    failureKeys);
            }
        }

        var documents = new Dictionary<string, VmtDocument>(StringComparer.OrdinalIgnoreCase);
        var consumers = new Dictionary<string, List<VtfConsumerEvidence>>(
            StringComparer.OrdinalIgnoreCase);

        foreach (MaterialSource source in materials.Values
                     .Where(value => value.IsEffective)
                     .OrderBy(value => Fold(value.ConsumerPath), StringComparer.Ordinal)
                     .ThenBy(value => value.ConsumerPath, StringComparer.Ordinal))
        {
            ExpandedMaterial? expanded = ExpandMaterial(
                source,
                materials,
                documents,
                new List<string>(),
                failures,
                failureKeys);
            if (expanded is null)
            {
                continue;
            }

            RegisterMaterialConsumers(source, expanded, consumers);
        }

        ScanLuaAndPcf(
            safe,
            "safe",
            consumers,
            uncertainties,
            failures,
            failureKeys);
        for (int index = 0; index < frameworkRoots.Count; index++)
        {
            if (!string.IsNullOrWhiteSpace(frameworkRoots[index]) &&
                Directory.Exists(frameworkRoots[index]))
            {
                ScanLuaAndPcf(
                    Path.GetFullPath(frameworkRoots[index]),
                    $"framework[{index}]",
                    consumers,
                    uncertainties,
                    failures,
                    failureKeys);
            }
        }

        IReadOnlyDictionary<string, IReadOnlyList<VtfConsumerEvidence>> frozenConsumers =
            consumers
                .OrderBy(pair => Fold(pair.Key), StringComparer.Ordinal)
                .ThenBy(pair => pair.Key, StringComparer.Ordinal)
                .ToDictionary(
                    pair => pair.Key,
                    pair => (IReadOnlyList<VtfConsumerEvidence>)pair.Value
                        .OrderBy(value => Fold(value.ConsumerPath), StringComparer.Ordinal)
                        .ThenBy(value => value.ConsumerPath, StringComparer.Ordinal)
                        .ThenBy(value => value.Key, StringComparer.OrdinalIgnoreCase)
                        .ToArray(),
                    StringComparer.OrdinalIgnoreCase);

        return new VtfConsumerGraph(
            frozenConsumers,
            failures
                .OrderBy(value => value.Code, StringComparer.Ordinal)
                .ThenBy(value => Fold(value.ConsumerPath), StringComparer.Ordinal)
                .ThenBy(value => value.Detail, StringComparer.Ordinal)
                .ToArray(),
            uncertainties.ToArray(),
            rawVmtFiles.Count,
            safeVmtFiles.Count);
    }

    internal static string NormalizeTextureName(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return string.Empty;
        }

        string normalized = value.Trim().Trim('"', '\'').Replace('\\', '/');
        while (normalized.StartsWith("./", StringComparison.Ordinal))
        {
            normalized = normalized[2..];
        }

        if (normalized.StartsWith("materials/", StringComparison.OrdinalIgnoreCase))
        {
            normalized = normalized["materials/".Length..];
        }

        if (normalized.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
        {
            normalized = normalized[..^4];
        }

        string[] segments = normalized.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (segments.Any(segment => segment is "." or ".."))
        {
            return string.Empty;
        }

        return string.Join('/', segments).ToLowerInvariant();
    }

    private static ExpandedMaterial? ExpandMaterial(
        MaterialSource source,
        IReadOnlyDictionary<string, MaterialSource> materials,
        IDictionary<string, VmtDocument> documents,
        IList<string> stack,
        ICollection<VtfConsumerFailure> failures,
        ISet<string> failureKeys)
    {
        int cycleStart = stack.IndexOf(source.MaterialName);
        if (cycleStart >= 0)
        {
            string cycle = string.Join(" -> ", stack.Skip(cycleStart).Append(source.MaterialName));
            AddFailure(
                failures,
                failureKeys,
                "include_cycle",
                source.ConsumerPath,
                cycle);
            return null;
        }

        VmtDocument document;
        try
        {
            if (!documents.TryGetValue(source.MaterialName, out document!))
            {
                document = VmtKeyValuesParser.Parse(
                    File.ReadAllText(source.FullPath),
                    source.ConsumerPath);
                documents[source.MaterialName] = document;
            }
        }
        catch (Exception error) when (
            error is IOException or UnauthorizedAccessException or InvalidDataException)
        {
            AddFailure(
                failures,
                failureKeys,
                "vmt_parse_error",
                source.ConsumerPath,
                $"{error.GetType().Name}:{error.Message}");
            return null;
        }

        stack.Add(source.MaterialName);
        try
        {
            if (!document.Shader.Equals("Patch", StringComparison.OrdinalIgnoreCase))
            {
                return new ExpandedMaterial(
                    document.Shader,
                    FlattenMaterialEntries(document.Entries).ToArray(),
                    new[] { source.MaterialName });
            }

            VmtKeyValuesNode[] includes = document.Entries
                .Where(node =>
                    node.Value is not null &&
                    node.Key.Equals("include", StringComparison.OrdinalIgnoreCase))
                .ToArray();
            if (includes.Length == 0)
            {
                AddFailure(
                    failures,
                    failureKeys,
                    "include_missing",
                    source.ConsumerPath,
                    "Patch has no include key");
                return null;
            }

            var parameters = new List<VmtKeyValuesNode>();
            var trace = new List<string> { source.MaterialName };
            bool resolvedAny = false;
            string? resolvedShader = null;
            foreach (VmtKeyValuesNode include in includes)
            {
                string includeName = NormalizeMaterialName(include.Value!.Text);
                if (string.IsNullOrEmpty(includeName) ||
                    !materials.TryGetValue(includeName, out MaterialSource? includedSource))
                {
                    AddFailure(
                        failures,
                        failureKeys,
                        "include_missing",
                        source.ConsumerPath,
                        include.Value.Text);
                    continue;
                }

                ExpandedMaterial? expandedBase = ExpandMaterial(
                    includedSource,
                    materials,
                    documents,
                    stack,
                    failures,
                    failureKeys);
                if (expandedBase is null)
                {
                    continue;
                }

                resolvedAny = true;
                if (resolvedShader is null)
                {
                    resolvedShader = expandedBase.Shader;
                }
                else if (!resolvedShader.Equals(
                             expandedBase.Shader,
                             StringComparison.OrdinalIgnoreCase))
                {
                    AddFailure(
                        failures,
                        failureKeys,
                        "include_shader_conflict",
                        source.ConsumerPath,
                        $"{resolvedShader}!={expandedBase.Shader}");
                }

                parameters.AddRange(expandedBase.Parameters);
                trace.AddRange(expandedBase.Trace);
            }

            if (!resolvedAny)
            {
                return null;
            }

            foreach (VmtKeyValuesNode node in document.Entries)
            {
                if (node.Key.Equals("include", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                if (node.Key.Equals("insert", StringComparison.OrdinalIgnoreCase) ||
                    node.Key.Equals("replace", StringComparison.OrdinalIgnoreCase))
                {
                    parameters.AddRange(FlattenMaterialEntries(node.Children));
                }
                else
                {
                    parameters.AddRange(FlattenMaterialEntries(new[] { node }));
                }
            }

            return new ExpandedMaterial(
                resolvedShader ?? "Patch",
                parameters.ToArray(),
                trace.ToArray());
        }
        finally
        {
            stack.RemoveAt(stack.Count - 1);
        }
    }

    private static IEnumerable<VmtKeyValuesNode> FlattenMaterialEntries(
        IEnumerable<VmtKeyValuesNode> entries)
    {
        foreach (VmtKeyValuesNode node in entries)
        {
            if (node.Value is not null ||
                node.Key.Equals("Proxies", StringComparison.OrdinalIgnoreCase))
            {
                yield return node;
                continue;
            }

            foreach (VmtKeyValuesNode child in FlattenMaterialEntries(node.Children))
            {
                yield return child;
            }
        }
    }

    private static void RegisterMaterialConsumers(
        MaterialSource source,
        ExpandedMaterial material,
        IDictionary<string, List<VtfConsumerEvidence>> consumers)
    {
        IReadOnlyDictionary<string, IReadOnlyList<string>> parameters = BuildParameterMap(
            material.Parameters);
        bool hasProxy = material.Parameters.Any(node =>
            node.Key.Equals("Proxies", StringComparison.OrdinalIgnoreCase));
        string[] materialUncertainties = hasProxy
            ? new[] { $"proxy:{source.ConsumerPath}" }
            : Array.Empty<string>();

        foreach (VmtKeyValuesNode node in material.Parameters)
        {
            if (node.Value is null || !LooksLikeTextureRole(node.Key))
            {
                continue;
            }

            string texture = NormalizeTextureName(node.Value.Text);
            if (string.IsNullOrEmpty(texture) || IsSpecialTexture(texture))
            {
                continue;
            }

            var evidence = new VtfConsumerEvidence(
                source.ConsumerPath,
                material.Shader,
                node.Key.ToLowerInvariant(),
                texture,
                ReadChannels(node.Key, parameters),
                Dynamic: hasProxy,
                ResolutionTrace: string.Join(" -> ", material.Trace) +
                                 $" @ {node.Span.Line}:{node.Span.Column}")
            {
                Parameters = parameters,
                Uncertainties = materialUncertainties
            };
            if (!consumers.TryGetValue(texture, out List<VtfConsumerEvidence>? list))
            {
                list = new List<VtfConsumerEvidence>();
                consumers.Add(texture, list);
            }

            list.Add(evidence);
        }
    }

    private static IReadOnlyDictionary<string, IReadOnlyList<string>> BuildParameterMap(
        IEnumerable<VmtKeyValuesNode> entries)
    {
        var mutable = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);
        foreach (VmtKeyValuesNode node in entries.Where(node => node.Value is not null))
        {
            if (!mutable.TryGetValue(node.Key, out List<string>? values))
            {
                values = new List<string>();
                mutable.Add(node.Key, values);
            }

            values.Add(node.Value!.Text);
        }

        return mutable.ToDictionary(
            pair => pair.Key,
            pair => (IReadOnlyList<string>)pair.Value.ToArray(),
            StringComparer.OrdinalIgnoreCase);
    }

    private static IReadOnlySet<int> ReadChannels(
        string role,
        IReadOnlyDictionary<string, IReadOnlyList<string>> parameters)
    {
        string key = role.ToLowerInvariant();
        var channels = new HashSet<int>();
        if (key is "$envmapmask" or "$phongexponenttexture" or "$selfillummask" or
            "$blendmodulatetexture")
        {
            channels.Add(0);
            return channels;
        }

        channels.UnionWith(new[] { 0, 1, 2 });
        if (key is "$basetexture" or "$basetexture2")
        {
            if (BaseAlphaParameters.Any(parameter => IsEnabled(parameters, parameter)))
            {
                channels.Add(3);
            }
        }
        else if (key is "$bumpmap" or "$bumpmap2" or "$normalmap")
        {
            if (NormalAlphaParameters.Any(parameter => IsEnabled(parameters, parameter)))
            {
                channels.Add(3);
            }
        }
        else if (key == "$detail" && DetailReadsAlpha(parameters))
        {
            channels.Add(3);
        }

        return channels;
    }

    private static bool DetailReadsAlpha(
        IReadOnlyDictionary<string, IReadOnlyList<string>> parameters)
    {
        string? value = LastValue(parameters, "$detailblendmode");
        if (value is not null &&
            int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int mode) &&
            mode is 2 or 3 or 4 or 7 or 8 or 9)
        {
            return true;
        }

        return IsEnabled(parameters, "$distancealpha") &&
               IsEnabled(parameters, "$distancealphafromdetail");
    }

    private static bool IsEnabled(
        IReadOnlyDictionary<string, IReadOnlyList<string>> parameters,
        string key)
    {
        string? value = LastValue(parameters, key);
        if (value is null)
        {
            return false;
        }

        if (double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out double numeric))
        {
            return numeric != 0;
        }

        return !value.Trim().Equals("false", StringComparison.OrdinalIgnoreCase) &&
               !value.Trim().Equals("off", StringComparison.OrdinalIgnoreCase) &&
               !value.Trim().Equals("no", StringComparison.OrdinalIgnoreCase) &&
               value.Trim().Length > 0;
    }

    private static string? LastValue(
        IReadOnlyDictionary<string, IReadOnlyList<string>> parameters,
        string key) =>
        parameters.TryGetValue(key, out IReadOnlyList<string>? values) && values.Count > 0
            ? values[^1]
            : null;

    private static void ScanLuaAndPcf(
        string root,
        string scope,
        IDictionary<string, List<VtfConsumerEvidence>> consumers,
        ISet<string> uncertainties,
        ICollection<VtfConsumerFailure> failures,
        ISet<string> failureKeys)
    {
        foreach (string luaPath in EnumerateFiles(
                     root,
                     ".lua",
                     failures,
                     failureKeys,
                     scope))
        {
            string relative = $"{scope}/{Relative(root, luaPath)}";
            string content;
            try
            {
                content = File.ReadAllText(luaPath);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            {
                AddFailure(failures, failureKeys, "lua_read_error", relative, error.Message);
                continue;
            }

            foreach (Match match in LuaMaterialCall.Matches(content))
            {
                string argument = match.Groups["argument"].Value;
                Match literal = LuaLiteralArgument.Match(argument);
                if (!literal.Success)
                {
                    uncertainties.Add($"dynamic_lua_material:{relative}:{LineAt(content, match.Index)}");
                }
            }

            if (LuaCreateMaterialCall.IsMatch(content))
            {
                uncertainties.Add($"dynamic_lua_create_material:{relative}");
            }
        }

        foreach (string pcfPath in EnumerateFiles(
                     root,
                     ".pcf",
                     failures,
                     failureKeys,
                     scope))
        {
            string relative = $"{scope}/{Relative(root, pcfPath)}";
            byte[] bytes;
            try
            {
                bytes = File.ReadAllBytes(pcfPath);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException)
            {
                AddFailure(failures, failureKeys, "pcf_read_error", relative, error.Message);
                continue;
            }

            string searchable = Encoding.Latin1.GetString(bytes);
            MatchCollection matches = PcfTexturePath.Matches(searchable);
            if (matches.Count == 0)
            {
                uncertainties.Add($"pcf_unresolved:{relative}");
                continue;
            }

            foreach (Match match in matches)
            {
                string texture = NormalizeTextureName(match.Value);
                if (string.IsNullOrEmpty(texture))
                {
                    continue;
                }

                if (!consumers.TryGetValue(texture, out List<VtfConsumerEvidence>? list))
                {
                    list = new List<VtfConsumerEvidence>();
                    consumers.Add(texture, list);
                }

                list.Add(new VtfConsumerEvidence(
                    relative,
                    "PCF",
                    "pcf:texture",
                    texture,
                    new HashSet<int> { 0, 1, 2, 3 },
                    Dynamic: true,
                    ResolutionTrace: $"pcf-string@{match.Index}")
                {
                    Uncertainties = new[] { $"pcf_consumer:{relative}" }
                });
            }
        }
    }

    private static int LineAt(string content, int offset)
    {
        int line = 1;
        for (int index = 0; index < offset && index < content.Length; index++)
        {
            if (content[index] == '\n')
            {
                line++;
            }
        }

        return line;
    }

    private static IReadOnlyList<string> EnumerateFiles(
        string root,
        string extension,
        ICollection<VtfConsumerFailure> failures,
        ISet<string> failureKeys,
        string scope)
    {
        var paths = new List<string>();
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
                AddFailure(
                    failures,
                    failureKeys,
                    "consumer_enumeration_error",
                    $"{scope}/{Relative(root, directory)}",
                    error.Message);
                continue;
            }

            foreach (string child in children)
            {
                FileAttributes attributes;
                try
                {
                    attributes = File.GetAttributes(child);
                }
                catch (Exception error) when (error is IOException or UnauthorizedAccessException)
                {
                    AddFailure(
                        failures,
                        failureKeys,
                        "consumer_enumeration_error",
                        $"{scope}/{Relative(root, child)}",
                        error.Message);
                    continue;
                }

                if ((attributes & FileAttributes.ReparsePoint) != 0)
                {
                    AddFailure(
                        failures,
                        failureKeys,
                        "consumer_reparse_point",
                        $"{scope}/{Relative(root, child)}",
                        "reparse points are not followed");
                    continue;
                }

                if ((attributes & FileAttributes.Directory) != 0)
                {
                    pending.Push(child);
                }
                else if (Path.GetExtension(child).Equals(extension, StringComparison.OrdinalIgnoreCase))
                {
                    paths.Add(child);
                }
            }
        }

        paths.Sort((left, right) =>
        {
            string leftRelative = Relative(root, left);
            string rightRelative = Relative(root, right);
            int folded = StringComparer.Ordinal.Compare(Fold(leftRelative), Fold(rightRelative));
            return folded != 0
                ? folded
                : StringComparer.Ordinal.Compare(leftRelative, rightRelative);
        });
        return paths;
    }

    private static void AddMaterialSource(
        IDictionary<string, MaterialSource> materials,
        MaterialSource source,
        ICollection<VtfConsumerFailure> failures,
        ISet<string> failureKeys)
    {
        if (!materials.TryAdd(source.MaterialName, source))
        {
            AddFailure(
                failures,
                failureKeys,
                "material_path_collision",
                source.ConsumerPath,
                source.MaterialName);
        }
    }

    private static void AddFailure(
        ICollection<VtfConsumerFailure> failures,
        ISet<string> failureKeys,
        string code,
        string path,
        string detail)
    {
        string canonicalPath = path.Replace('\\', '/');
        string key = $"{code}\0{canonicalPath}\0{detail}";
        if (failureKeys.Add(key))
        {
            failures.Add(new VtfConsumerFailure(code, canonicalPath, detail));
        }
    }

    private static bool LooksLikeTextureRole(string key) =>
        KnownTextureRoles.Contains(key) ||
        key.Contains("texture", StringComparison.OrdinalIgnoreCase) ||
        key.EndsWith("map", StringComparison.OrdinalIgnoreCase);

    private static bool IsSpecialTexture(string normalized) =>
        normalized.StartsWith('_') ||
        normalized.Equals("env_cubemap", StringComparison.OrdinalIgnoreCase) ||
        normalized.StartsWith("rt_", StringComparison.OrdinalIgnoreCase);

    private static string MaterialNameFromRelative(string relativePath)
    {
        string normalized = relativePath.Replace('\\', '/').TrimStart('/');
        int materialsIndex = normalized.IndexOf("materials/", StringComparison.OrdinalIgnoreCase);
        if (materialsIndex >= 0)
        {
            normalized = normalized[(materialsIndex + "materials/".Length)..];
        }

        return NormalizeMaterialName(normalized);
    }

    private static string NormalizeMaterialName(string value)
    {
        string normalized = value.Trim().Trim('"', '\'').Replace('\\', '/');
        while (normalized.StartsWith("./", StringComparison.Ordinal))
        {
            normalized = normalized[2..];
        }

        if (normalized.StartsWith("materials/", StringComparison.OrdinalIgnoreCase))
        {
            normalized = normalized["materials/".Length..];
        }

        if (normalized.EndsWith(".vmt", StringComparison.OrdinalIgnoreCase))
        {
            normalized = normalized[..^4];
        }

        string[] segments = normalized.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (segments.Any(segment => segment is "." or ".."))
        {
            return string.Empty;
        }

        return string.Join('/', segments).ToLowerInvariant();
    }

    private static string RequireDirectory(string requested, string parameterName)
    {
        if (string.IsNullOrWhiteSpace(requested))
        {
            throw new ArgumentException("A consumer root is required.", parameterName);
        }

        string root = Path.GetFullPath(requested);
        if (!Directory.Exists(root))
        {
            throw new DirectoryNotFoundException(root);
        }

        if ((File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
        {
            throw new InvalidDataException($"consumer_root_reparse_point:{root}");
        }

        return root;
    }

    private static string Relative(string root, string path) =>
        Path.GetRelativePath(root, path).Replace('\\', '/');

    private static string Fold(string value) => value.ToLowerInvariant();

    private sealed record MaterialSource(
        string MaterialName,
        string FullPath,
        string ConsumerPath,
        bool IsEffective);

    private sealed record ExpandedMaterial(
        string Shader,
        IReadOnlyList<VmtKeyValuesNode> Parameters,
        IReadOnlyList<string> Trace);
}
