using System.Globalization;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfSemanticDecision(
    IReadOnlySet<UniversalVtfSemanticClass> Classes,
    UniversalVtfFilterRoute Route,
    bool HasUnknownConsumer,
    double AlphaTestReference,
    IReadOnlyList<VtfConsumerEvidence> Consumers,
    IReadOnlyList<string> Uncertainties);

internal static class UniversalVtfSemanticResolver
{
    private const int TextureFlagsSrgb = 0x00000040;
    private const int TextureFlagsNormal = 0x00000080;
    private const int TextureFlagsEnvmap = 0x00004000;
    private const int TextureFlagsSsbump = 0x08000000;

    private static readonly HashSet<string> NormalRoles = new(
        new[] { "$bumpmap", "$bumpmap2", "$normalmap", "$dudvmap" },
        StringComparer.OrdinalIgnoreCase);

    private static readonly HashSet<string> ScalarRoles = new(
        new[]
        {
            "$envmapmask", "$phongexponenttexture", "$selfillummask",
            "$blendmodulatetexture", "$lightwarptexture", "$phongwarptexture",
            "$flowmap", "$compress", "$stretch"
        },
        StringComparer.OrdinalIgnoreCase);

    private static readonly HashSet<string> EmissiveRoles = new(
        new[]
        {
            "$emissiveblendtexture", "$emissiveblendbasetexture",
            "$emissiveblendflowtexture", "$selfillummask"
        },
        StringComparer.OrdinalIgnoreCase);

    private static readonly string[] TextTokens =
    {
        "decal", "logo", "text", "gauge", "plate", "sign", "badge",
        "speedo", "tach", "number", "letter", "font"
    };

    internal static UniversalVtfSemanticDecision Resolve(
        UniversalVtfInventoryEntry entry,
        VtfConsumerGraph graph)
    {
        ArgumentNullException.ThrowIfNull(entry);
        ArgumentNullException.ThrowIfNull(graph);

        string textureKey = VtfConsumerGraphBuilder.NormalizeTextureName(entry.RelativePath);
        IReadOnlyList<VtfConsumerEvidence> consumers = graph.ConsumersFor(textureKey);
        var classes = new HashSet<UniversalVtfSemanticClass>();
        var uncertainties = new SortedSet<string>(StringComparer.Ordinal);

        foreach (VtfConsumerFailure failure in graph.Failures)
        {
            uncertainties.Add(
                $"graph_failure:{failure.Code}:{failure.ConsumerPath}:{failure.Detail}");
        }

        foreach (string uncertainty in graph.GlobalUncertainties)
        {
            uncertainties.Add(uncertainty);
        }

        foreach (VtfConsumerEvidence consumer in consumers)
        {
            ClassifyConsumer(consumer, classes);
            foreach (string uncertainty in consumer.Uncertainties)
            {
                uncertainties.Add(uncertainty);
            }

            if (consumer.Dynamic)
            {
                uncertainties.Add($"dynamic_consumer:{consumer.ConsumerPath}:{consumer.Key}");
            }
        }

        ApplyContainerFallbacks(entry, classes);
        ApplyPathHints(textureKey, classes);

        if (entry.Frames > 1)
        {
            classes.Add(UniversalVtfSemanticClass.Animated);
        }

        if (entry.Faces is 6 or 7)
        {
            classes.Add(UniversalVtfSemanticClass.Cubemap);
        }

        bool missingConsumer = consumers.Count == 0;
        if (missingConsumer)
        {
            uncertainties.Add($"missing_consumer:{textureKey}");
        }

        double alphaTestReference = ResolveAlphaTestReference(consumers, uncertainties);
        bool hasUnknown = missingConsumer || uncertainties.Count > 0;
        if (hasUnknown)
        {
            classes.Add(UniversalVtfSemanticClass.Unknown);
        }

        if (classes.Count == 0)
        {
            classes.Add(UniversalVtfSemanticClass.Unknown);
            uncertainties.Add($"unclassified:{textureKey}");
            hasUnknown = true;
        }

        UniversalVtfFilterRoute route = SelectRoute(classes, consumers, entry, hasUnknown);
        return new UniversalVtfSemanticDecision(
            classes,
            route,
            hasUnknown,
            alphaTestReference,
            consumers,
            uncertainties.ToArray());
    }

    private static void ClassifyConsumer(
        VtfConsumerEvidence consumer,
        ISet<UniversalVtfSemanticClass> classes)
    {
        string role = consumer.Key.ToLowerInvariant();
        if (role is "$basetexture" or "$basetexture2" or "$texture1" or
            "$texture2" or "$texture3" or "$texture4")
        {
            classes.Add(UniversalVtfSemanticClass.Diffuse);
        }
        else if (role == "$detail")
        {
            classes.Add(UniversalVtfSemanticClass.Detail);
        }
        else if (NormalRoles.Contains(role))
        {
            classes.Add(UniversalVtfSemanticClass.Normal);
        }
        else if (ScalarRoles.Contains(role) || role.Contains("mask", StringComparison.Ordinal))
        {
            classes.Add(UniversalVtfSemanticClass.ScalarMask);
        }
        else if (EmissiveRoles.Contains(role))
        {
            classes.Add(UniversalVtfSemanticClass.Emissive);
        }
        else if (role == "$refracttinttexture")
        {
            classes.Add(UniversalVtfSemanticClass.Glass);
        }
        else
        {
            classes.Add(UniversalVtfSemanticClass.Unknown);
        }

        if (ParameterEnabled(consumer.Parameters, "$ssbump"))
        {
            classes.Add(UniversalVtfSemanticClass.Normal);
            classes.Add(UniversalVtfSemanticClass.Ssbump);
        }

        if (ParameterEnabled(consumer.Parameters, "$alphatest"))
        {
            classes.Add(UniversalVtfSemanticClass.Cutout);
        }

        if (ParameterEnabled(consumer.Parameters, "$translucent") ||
            consumer.Shader.Contains("refract", StringComparison.OrdinalIgnoreCase) ||
            consumer.Shader.Contains("water", StringComparison.OrdinalIgnoreCase) ||
            consumer.Shader.Contains("glass", StringComparison.OrdinalIgnoreCase))
        {
            classes.Add(UniversalVtfSemanticClass.Glass);
        }

        if (ParameterEnabled(consumer.Parameters, "$selfillum") ||
            EmissiveRoles.Contains(role))
        {
            classes.Add(UniversalVtfSemanticClass.Emissive);
        }
    }

    private static void ApplyContainerFallbacks(
        UniversalVtfInventoryEntry entry,
        ISet<UniversalVtfSemanticClass> classes)
    {
        if ((entry.Flags & TextureFlagsSsbump) != 0)
        {
            classes.Add(UniversalVtfSemanticClass.Normal);
            classes.Add(UniversalVtfSemanticClass.Ssbump);
        }
        else if ((entry.Flags & TextureFlagsNormal) != 0)
        {
            classes.Add(UniversalVtfSemanticClass.Normal);
        }

        if ((entry.Flags & TextureFlagsEnvmap) != 0)
        {
            classes.Add(UniversalVtfSemanticClass.Cubemap);
        }

        if (classes.Count == 0 && (entry.Flags & TextureFlagsSrgb) != 0)
        {
            classes.Add(UniversalVtfSemanticClass.Diffuse);
        }
    }

    private static void ApplyPathHints(
        string textureKey,
        ISet<UniversalVtfSemanticClass> classes)
    {
        string leaf = Path.GetFileName(textureKey);
        if (TextTokens.Any(token =>
                leaf.Contains(token, StringComparison.OrdinalIgnoreCase)))
        {
            classes.Add(UniversalVtfSemanticClass.Text);
        }
    }

    private static double ResolveAlphaTestReference(
        IEnumerable<VtfConsumerEvidence> consumers,
        ISet<string> uncertainties)
    {
        var references = new SortedSet<double>();
        bool hasCutout = false;
        foreach (VtfConsumerEvidence consumer in consumers)
        {
            if (!ParameterEnabled(consumer.Parameters, "$alphatest"))
            {
                continue;
            }

            hasCutout = true;
            string? value = LastParameter(consumer.Parameters, "$alphatestreference");
            if (value is null)
            {
                references.Add(0.5);
            }
            else if (double.TryParse(
                         value,
                         NumberStyles.Float,
                         CultureInfo.InvariantCulture,
                         out double parsed) &&
                     double.IsFinite(parsed) &&
                     parsed is >= 0 and <= 1)
            {
                references.Add(parsed);
            }
            else
            {
                uncertainties.Add(
                    $"invalid_alphatestreference:{consumer.ConsumerPath}:{value}");
                references.Add(0.5);
            }
        }

        if (!hasCutout)
        {
            return 0.5;
        }

        if (references.Count != 1)
        {
            uncertainties.Add(
                "conflicting_alphatestreference:" +
                string.Join(',', references.Select(value =>
                    value.ToString("R", CultureInfo.InvariantCulture))));
            return 0.5;
        }

        return references.Single();
    }

    private static UniversalVtfFilterRoute SelectRoute(
        IReadOnlySet<UniversalVtfSemanticClass> classes,
        IReadOnlyList<VtfConsumerEvidence> consumers,
        UniversalVtfInventoryEntry entry,
        bool hasUnknown)
    {
        if (hasUnknown || classes.Contains(UniversalVtfSemanticClass.Unknown))
        {
            return UniversalVtfFilterRoute.Composite;
        }

        bool normal = classes.Contains(UniversalVtfSemanticClass.Normal);
        bool scalar = classes.Contains(UniversalVtfSemanticClass.ScalarMask);
        bool color = classes.Any(value => value is
            UniversalVtfSemanticClass.Diffuse or
            UniversalVtfSemanticClass.Detail or
            UniversalVtfSemanticClass.Cutout or
            UniversalVtfSemanticClass.Glass or
            UniversalVtfSemanticClass.Emissive or
            UniversalVtfSemanticClass.Text or
            UniversalVtfSemanticClass.Cubemap);
        int domains = (normal ? 1 : 0) + (scalar ? 1 : 0) + (color ? 1 : 0);
        if (domains > 1)
        {
            return UniversalVtfFilterRoute.Composite;
        }

        if (classes.Contains(UniversalVtfSemanticClass.Ssbump))
        {
            return UniversalVtfFilterRoute.SsbumpScalars;
        }

        if (normal)
        {
            bool dxt5Nm = entry.HighResFormat == 15 && consumers.Any(consumer =>
                ParameterEnabled(consumer.Parameters, "$dxt5nm") ||
                consumer.ReadChannels.SetEquals(new[] { 1, 3 }));
            return dxt5Nm
                ? UniversalVtfFilterRoute.Dxt5NmAg
                : UniversalVtfFilterRoute.RgbNormal;
        }

        if (scalar)
        {
            return UniversalVtfFilterRoute.NumericLinear;
        }

        return UniversalVtfFilterRoute.SrgbColor;
    }

    private static bool ParameterEnabled(
        IReadOnlyDictionary<string, IReadOnlyList<string>> parameters,
        string key)
    {
        string? value = LastParameter(parameters, key);
        if (value is null)
        {
            return false;
        }

        if (double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out double numeric))
        {
            return numeric != 0;
        }

        string normalized = value.Trim();
        return normalized.Length > 0 &&
               !normalized.Equals("false", StringComparison.OrdinalIgnoreCase) &&
               !normalized.Equals("off", StringComparison.OrdinalIgnoreCase) &&
               !normalized.Equals("no", StringComparison.OrdinalIgnoreCase);
    }

    private static string? LastParameter(
        IReadOnlyDictionary<string, IReadOnlyList<string>> parameters,
        string key) =>
        parameters.TryGetValue(key, out IReadOnlyList<string>? values) && values.Count > 0
            ? values[^1]
            : null;
}
