using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;

namespace VtfMaximumLab.Inventory;

internal static class VtfInventoryScanner
{
    internal static IReadOnlyList<VtfInventoryEntry> Scan(string addonRoot)
    {
        string root = Path.GetFullPath(addonRoot);
        if (!Directory.Exists(root))
            throw new DirectoryNotFoundException($"Addon root was not found: {root}");

        AddonVtfCompressionAnalysis analysis = AddonVtfCompressionPlanner.Analyze(root);
        var entries = new List<VtfInventoryEntry>();
        foreach (string path in Directory.EnumerateFiles(root, "*.vtf", SearchOption.AllDirectories)
                     .OrderBy(path => path, StringComparer.OrdinalIgnoreCase))
        {
            if (!VtfDocumentReader.TryRead(path, out VtfDocumentInfo document, out string error))
                throw new InvalidDataException($"Cannot inventory '{path}': {error}");

            string relativePath = Normalize(Path.GetRelativePath(root, path));
            string textureKey = GetTextureKey(relativePath);
            VtfSemanticProfile profile = analysis.GetSemanticProfile(textureKey, relativePath);
            VtfAlphaClass alphaClass = ClassifyAlpha(path, document);
            IReadOnlyList<string> relatedVmts = ResolveRelatedVmts(root, relativePath, profile);
            IReadOnlySet<string> tags = BuildTags(relativePath, document, alphaClass, profile);

            entries.Add(new VtfInventoryEntry(
                path,
                relativePath,
                new FileInfo(path).Length,
                document.Width,
                document.Height,
                document.HighResFormat,
                document.MajorVersion,
                document.MinorVersion,
                document.MipCount,
                document.Frames,
                document.Faces,
                document.Depth,
                document.Flags,
                alphaClass,
                profile,
                relatedVmts,
                tags));
        }

        return entries;
    }

    private static VtfAlphaClass ClassifyAlpha(string path, VtfDocumentInfo document)
    {
        if (!AddonVtfCompressionPlanner.TryReadMetadata(path, out VtfFileModel metadata) ||
            !VtfHighResImageDecoder.TryDecodeHighResRgba(path, metadata, out byte[] rgba))
        {
            return VtfAlphaClass.Unknown;
        }

        bool sawTransparent = false;
        bool sawPartial = false;
        for (int offset = 3; offset < rgba.Length; offset += 4)
        {
            byte alpha = rgba[offset];
            sawTransparent |= alpha == 0;
            sawPartial |= alpha is > 0 and < 255;
            if (sawPartial)
                return VtfAlphaClass.Gradual;
        }

        return sawTransparent ? VtfAlphaClass.Cutout : VtfAlphaClass.Opaque;
    }

    private static IReadOnlyList<string> ResolveRelatedVmts(
        string root,
        string vtfRelativePath,
        VtfSemanticProfile profile)
    {
        var relativePaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        string sameNameVmt = Path.ChangeExtension(vtfRelativePath, ".vmt");
        if (File.Exists(Path.Combine(root, sameNameVmt.Replace('/', Path.DirectorySeparatorChar))))
            relativePaths.Add(Normalize(sameNameVmt));

        foreach (string evidence in profile.Evidence)
        {
            if (!evidence.StartsWith("vmt:", StringComparison.OrdinalIgnoreCase))
                continue;

            string candidate = $"materials/{evidence.Substring(4).Trim('/')}.vmt";
            if (File.Exists(Path.Combine(root, candidate.Replace('/', Path.DirectorySeparatorChar))))
                relativePaths.Add(candidate);
        }

        return relativePaths.OrderBy(path => path, StringComparer.OrdinalIgnoreCase).ToArray();
    }

    private static IReadOnlySet<string> BuildTags(
        string relativePath,
        VtfDocumentInfo document,
        VtfAlphaClass alphaClass,
        VtfSemanticProfile profile)
    {
        var tags = new HashSet<string>(StringComparer.Ordinal)
        {
            $"format:{GetFormatTag(document.HighResFormat)}",
            $"version:{document.MajorVersion}.{document.MinorVersion}",
            document.MipCount > 1 ? "mipmaps:present" : "mipmaps:none",
            $"alpha:{alphaClass.ToString().ToLowerInvariant()}"
        };

        int maximumSide = Math.Max(document.Width, document.Height);
        tags.Add(maximumSide >= 2048 ? "size:large" : maximumSide <= 256 ? "size:small" : "size:medium");
        if (document.Frames > 1)
            tags.Add("structure:animated");
        if (document.Faces > 1)
            tags.Add("structure:cubemap");
        if (document.Depth > 1)
            tags.Add("structure:volume");
        if (document.LowResWidth > 0 && document.LowResHeight > 0)
            tags.Add("resource:thumbnail");
        if (VtfInventorySemantics.IsNormalMap(relativePath, document.Flags, profile.IsNormalMap))
            tags.Add("semantic:normal");
        if (profile.IsVehicleGlass)
            tags.Add("semantic:glass");
        if (profile.UsesBaseTextureAsPhongMask)
            tags.Add("semantic:phong-mask");
        if (profile.UsesBaseTextureAsEnvMapMask)
            tags.Add("semantic:envmap-mask");
        if (profile.HasPhong)
            tags.Add("semantic:phong");
        if (profile.UsesNormalAlpha)
            tags.Add("semantic:normal-alpha-mask");
        if (profile.IsEmissive)
            tags.Add("semantic:emissive");
        if (profile.IsDecal)
            tags.Add("semantic:decal");
        if (profile.IsEffect)
            tags.Add("semantic:effect");
        if (profile.IsCutout)
            tags.Add("semantic:cutout");
        if (profile.IsTranslucent)
            tags.Add("semantic:translucent");

        return tags;
    }

    private static string GetFormatTag(int format) => format switch
    {
        13 => "DXT1",
        15 => "DXT5",
        20 => "DXT1_ONEBITALPHA",
        _ => format.ToString()
    };

    private static string GetTextureKey(string relativePath)
    {
        string key = relativePath;
        if (key.StartsWith("materials/", StringComparison.OrdinalIgnoreCase))
            key = key.Substring("materials/".Length);
        if (key.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
            key = key.Substring(0, key.Length - 4);
        return key.Trim('/').ToLowerInvariant();
    }

    private static string Normalize(string path) => path.Replace('\\', '/');
}
