using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems.Maps
{
    internal sealed class MapVtfSkipReasonSummary
    {
        public MapVtfSkipReasonSummary(string reason, int fileCount, long totalBytes)
        {
            Reason = reason;
            FileCount = fileCount;
            TotalBytes = totalBytes;
        }

        public string Reason { get; }
        public int FileCount { get; }
        public long TotalBytes { get; }
    }

    internal sealed class MapVtfSkippedFile
    {
        public MapVtfSkippedFile(string fullPath, string relativePath, long totalBytes, IReadOnlyList<string> reasons)
        {
            FullPath = fullPath;
            RelativePath = relativePath;
            TotalBytes = totalBytes;
            Reasons = reasons;
        }

        public string FullPath { get; }
        public string RelativePath { get; }
        public long TotalBytes { get; }
        public IReadOnlyList<string> Reasons { get; }
        public string PrimaryReason => Reasons.Count > 0 ? Reasons[0] : "special_vtf";
    }

    internal sealed class MapVtfSafetyReport
    {
        public MapVtfSafetyReport(
            IReadOnlyCollection<string> safeVtfPaths,
            long safeVtfBytes,
            IReadOnlyList<MapVtfSkippedFile> skippedFiles,
            IReadOnlyList<MapVtfSkipReasonSummary> reasonSummaries)
        {
            SafeVtfPaths = safeVtfPaths;
            SafeVtfBytes = safeVtfBytes;
            SkippedFiles = skippedFiles;
            ReasonSummaries = reasonSummaries;
        }

        public IReadOnlyCollection<string> SafeVtfPaths { get; }
        public IReadOnlyList<MapVtfSkippedFile> SkippedFiles { get; }
        public IReadOnlyList<MapVtfSkipReasonSummary> ReasonSummaries { get; }
        public int SafeVtfCount => SafeVtfPaths.Count;
        public long SafeVtfBytes { get; }
        public int SkippedVtfCount => SkippedFiles.Count;
        public long SkippedVtfBytes => SkippedFiles.Sum(file => file.TotalBytes);

        public bool IsSafeVtf(string fullPath)
        {
            return SafeVtfPaths.Contains(Path.GetFullPath(fullPath), StringComparer.OrdinalIgnoreCase);
        }
    }

    internal static class MapVtfSafetyClassifier
    {
        private const int TextureFlagNormal = 0x00000080;
        private const int TextureFlagEnvmap = 0x00004000;

        private static readonly Regex VmtTextureAssignmentRegex = new Regex(
            "^\\s*\"?(?<key>\\$[^\\s\"]+)\"?\\s+(?:\"(?<value>[^\"]+)\"|(?<value>[^\\s\\{\\}]+))",
            RegexOptions.Compiled | RegexOptions.IgnoreCase | RegexOptions.Multiline);

        private static readonly HashSet<string> BumpmapKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$bumpmap",
            "$normalmap"
        };

        private static readonly HashSet<string> EnvmapKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$envmap"
        };

        private static readonly HashSet<string> EnvmapMaskKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$envmapmask"
        };

        private static readonly HashSet<string> SelfIllumMaskKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$selfillummask"
        };

        private static readonly HashSet<string> BaseTextureKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$basetexture",
            "$basetexture2"
        };

        private static readonly HashSet<string> BaseTextureAlphaSemanticKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$blendtintbybasealpha",
            "$basealphaenvmapmask"
        };

        private static readonly HashSet<string> BumpmapAlphaSemanticKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$normalmapalphaenvmapmask"
        };

        internal static MapVtfSafetyReport Analyze(IEnumerable<string> stageRoots)
        {
            var roots = stageRoots
                .Where(path => !string.IsNullOrWhiteSpace(path) && Directory.Exists(path))
                .Select(Path.GetFullPath)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();

            var bumpmapRefs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var envmapRefs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var envmapMaskRefs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var baseTextureAlphaRefs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var bumpmapAlphaRefs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var translucentBaseTextureRefs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var selfIllumBaseTextureRefs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var selfIllumMaskRefs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (string root in roots)
                ParseVmtReferences(
                    root,
                    bumpmapRefs,
                    envmapRefs,
                    envmapMaskRefs,
                    baseTextureAlphaRefs,
                    bumpmapAlphaRefs,
                    translucentBaseTextureRefs,
                    selfIllumBaseTextureRefs,
                    selfIllumMaskRefs);

            var safeVtfPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            long safeVtfBytes = 0;
            var skippedFiles = new List<MapVtfSkippedFile>();

            foreach (string root in roots)
            {
                foreach (string vtfPath in Directory.EnumerateFiles(root, "*.vtf", SearchOption.AllDirectories))
                {
                    string fullPath = Path.GetFullPath(vtfPath);
                    string relativePath = GetRelativeNormalized(root, fullPath);
                    string? textureKey = GetTextureKey(relativePath);
                    string fileName = Path.GetFileName(relativePath);
                    var reasons = new List<string>();

                    if (relativePath.StartsWith("materials/maps/", StringComparison.OrdinalIgnoreCase))
                        reasons.Add("map_material_namespace");

                    if (relativePath.StartsWith("materials/skybox/", StringComparison.OrdinalIgnoreCase))
                        reasons.Add("skybox_namespace");

                    if (fileName.EndsWith(".hdr.vtf", StringComparison.OrdinalIgnoreCase))
                        reasons.Add("hdr_variant");

                    string fileNameNoExt = Path.GetFileNameWithoutExtension(fileName);
                    if (fileNameNoExt.Contains("_hdr", StringComparison.OrdinalIgnoreCase))
                        reasons.Add("skybox_hdr_name");

                    if (fileNameNoExt.StartsWith("c-", StringComparison.OrdinalIgnoreCase) ||
                        fileNameNoExt.StartsWith("cubemapdefault", StringComparison.OrdinalIgnoreCase))
                    {
                        reasons.Add("cubemap_name");
                    }

                    if (textureKey != null)
                    {
                        if (translucentBaseTextureRefs.Contains(textureKey))
                            reasons.Add("translucent_basetexture_reference");

                        if (selfIllumBaseTextureRefs.Contains(textureKey))
                            reasons.Add("selfillum_basetexture_reference");

                        if (baseTextureAlphaRefs.Contains(textureKey))
                            reasons.Add("basealpha_semantic_reference");

                        if (bumpmapRefs.Contains(textureKey))
                            reasons.Add("bumpmap_reference");

                        if (bumpmapAlphaRefs.Contains(textureKey))
                            reasons.Add("normalmap_alpha_semantic_reference");

                        if (envmapRefs.Contains(textureKey))
                            reasons.Add("envmap_reference");

                        if (envmapMaskRefs.Contains(textureKey))
                            reasons.Add("envmap_mask_reference");

                        if (selfIllumMaskRefs.Contains(textureKey))
                            reasons.Add("selfillum_mask_reference");
                    }

                    if (!TryReadMetadata(fullPath, out var metadata))
                    {
                        reasons.Add("vtf_metadata_unreadable");
                    }
                    else
                    {
                        if (metadata.Frames > 1)
                            reasons.Add("animated_frames");

                        if (metadata.Depth > 1)
                            reasons.Add("volume_depth");

                        if ((metadata.Flags & TextureFlagEnvmap) != 0)
                            reasons.Add("envmap_flag");

                        if ((metadata.Flags & TextureFlagNormal) != 0)
                            reasons.Add("normal_map_flag");
                    }

                    reasons = reasons
                        .Distinct(StringComparer.OrdinalIgnoreCase)
                        .ToList();

                    if (reasons.Count > 0)
                    {
                        long totalBytes = 0;
                        try
                        {
                            totalBytes = new FileInfo(fullPath).Length;
                        }
                        catch
                        {
                        }

                        skippedFiles.Add(new MapVtfSkippedFile(fullPath, relativePath, totalBytes, reasons));
                    }
                    else
                    {
                        safeVtfPaths.Add(fullPath);
                        try
                        {
                            safeVtfBytes += new FileInfo(fullPath).Length;
                        }
                        catch
                        {
                        }
                    }
                }
            }

            var reasonSummaries = skippedFiles
                .GroupBy(file => file.PrimaryReason, StringComparer.OrdinalIgnoreCase)
                .Select(group => new MapVtfSkipReasonSummary(
                    group.Key,
                    group.Count(),
                    group.Sum(file => file.TotalBytes)))
                .OrderByDescending(entry => entry.TotalBytes)
                .ThenBy(entry => entry.Reason, StringComparer.OrdinalIgnoreCase)
                .ToArray();

            return new MapVtfSafetyReport(safeVtfPaths, safeVtfBytes, skippedFiles, reasonSummaries);
        }

        private static void ParseVmtReferences(
            string stageRoot,
            HashSet<string> bumpmapRefs,
            HashSet<string> envmapRefs,
            HashSet<string> envmapMaskRefs,
            HashSet<string> baseTextureAlphaRefs,
            HashSet<string> bumpmapAlphaRefs,
            HashSet<string> translucentBaseTextureRefs,
            HashSet<string> selfIllumBaseTextureRefs,
            HashSet<string> selfIllumMaskRefs)
        {
            foreach (string vmtPath in Directory.EnumerateFiles(stageRoot, "*.vmt", SearchOption.AllDirectories))
            {
                string content;
                try
                {
                    content = File.ReadAllText(vmtPath);
                }
                catch
                {
                    continue;
                }

                string? baseTexture = null;
                string? bumpmapTexture = null;
                bool hasTranslucent = false;
                bool hasSelfIllum = false;
                bool usesBaseTextureAlpha = false;
                bool usesBumpmapAlpha = false;

                foreach (Match match in VmtTextureAssignmentRegex.Matches(content))
                {
                    string key = match.Groups["key"].Value.Trim();
                    string rawValue = match.Groups["value"].Value;
                    string value = NormalizeTextureReference(rawValue);

                    if (BumpmapKeys.Contains(key))
                    {
                        if (!string.IsNullOrWhiteSpace(value))
                        {
                            bumpmapRefs.Add(value);
                            bumpmapTexture = value;
                        }
                    }
                    else if (EnvmapKeys.Contains(key))
                    {
                        if (!string.IsNullOrWhiteSpace(value))
                            envmapRefs.Add(value);
                    }
                    else if (EnvmapMaskKeys.Contains(key))
                    {
                        if (!string.IsNullOrWhiteSpace(value))
                            envmapMaskRefs.Add(value);
                    }
                    else if (SelfIllumMaskKeys.Contains(key))
                    {
                        if (!string.IsNullOrWhiteSpace(value))
                            selfIllumMaskRefs.Add(value);
                    }
                    else if (BaseTextureKeys.Contains(key))
                    {
                        if (!string.IsNullOrWhiteSpace(value))
                            baseTexture = value;
                    }

                    if (BaseTextureAlphaSemanticKeys.Contains(key) && IsTruthyMaterialValue(rawValue))
                        usesBaseTextureAlpha = true;

                    if (BumpmapAlphaSemanticKeys.Contains(key) && IsTruthyMaterialValue(rawValue))
                        usesBumpmapAlpha = true;

                    if ((key.Equals("$translucent", StringComparison.OrdinalIgnoreCase) ||
                         key.Equals("$alphatest", StringComparison.OrdinalIgnoreCase)) &&
                        IsTruthyMaterialValue(rawValue))
                    {
                        hasTranslucent = true;
                    }

                    if (key.Equals("$selfillum", StringComparison.OrdinalIgnoreCase) &&
                        IsTruthyMaterialValue(rawValue))
                    {
                        hasSelfIllum = true;
                    }
                }

                if (hasTranslucent && !string.IsNullOrWhiteSpace(baseTexture))
                    translucentBaseTextureRefs.Add(baseTexture);

                if (hasSelfIllum && !string.IsNullOrWhiteSpace(baseTexture))
                    selfIllumBaseTextureRefs.Add(baseTexture);

                if (usesBaseTextureAlpha && !string.IsNullOrWhiteSpace(baseTexture))
                    baseTextureAlphaRefs.Add(baseTexture);

                if (usesBumpmapAlpha && !string.IsNullOrWhiteSpace(bumpmapTexture))
                    bumpmapAlphaRefs.Add(bumpmapTexture);
            }
        }

        private static bool IsTruthyMaterialValue(string rawValue)
        {
            if (string.IsNullOrWhiteSpace(rawValue))
                return false;

            string value = rawValue.Trim().Trim('"').Trim();
            return !value.Equals("0", StringComparison.OrdinalIgnoreCase) &&
                   !value.Equals("false", StringComparison.OrdinalIgnoreCase);
        }

        private static string NormalizeTextureReference(string rawValue)
        {
            if (string.IsNullOrWhiteSpace(rawValue))
                return string.Empty;

            string value = rawValue.Trim().Replace('\\', '/');
            if (value.StartsWith("materials/", StringComparison.OrdinalIgnoreCase))
                value = value.Substring("materials/".Length);

            if (value.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
                value = value.Substring(0, value.Length - 4);

            return value.Trim().Trim('/').ToLowerInvariant();
        }

        private static string GetRelativeNormalized(string rootPath, string fullPath)
        {
            string relative = Path.GetRelativePath(rootPath, fullPath);
            return relative.Replace('\\', '/');
        }

        private static string? GetTextureKey(string relativePath)
        {
            string normalized = relativePath.Replace('\\', '/');
            if (!normalized.StartsWith("materials/", StringComparison.OrdinalIgnoreCase) ||
                !normalized.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            string key = normalized.Substring("materials/".Length, normalized.Length - "materials/".Length - 4);
            return key.Trim('/').ToLowerInvariant();
        }

        private static bool TryReadMetadata(string vtfPath, out MapVtfMetadata metadata)
        {
            metadata = default;

            try
            {
                using FileStream stream = File.OpenRead(vtfPath);
                using BinaryReader reader = new BinaryReader(stream);

                byte[] signature = reader.ReadBytes(4);
                if (signature.Length != 4 || signature[0] != (byte)'V' || signature[1] != (byte)'T' || signature[2] != (byte)'F')
                    return false;

                uint majorVersion = reader.ReadUInt32();
                uint minorVersion = reader.ReadUInt32();
                _ = reader.ReadUInt32(); // header size
                _ = reader.ReadUInt16(); // width
                _ = reader.ReadUInt16(); // height
                uint flags = reader.ReadUInt32();
                ushort frames = reader.ReadUInt16();
                _ = reader.ReadUInt16(); // first frame
                _ = reader.ReadBytes(4); // padding0
                _ = reader.ReadSingle();
                _ = reader.ReadSingle();
                _ = reader.ReadSingle();
                _ = reader.ReadBytes(4); // padding1
                _ = reader.ReadSingle(); // bumpmap scale
                _ = reader.ReadUInt32(); // high res image format
                _ = reader.ReadByte(); // mip count
                _ = reader.ReadUInt32(); // low res image format
                _ = reader.ReadByte(); // low res width
                _ = reader.ReadByte(); // low res height

                ushort depth = 1;
                if (majorVersion > 7 || (majorVersion == 7 && minorVersion >= 2))
                    depth = reader.ReadUInt16();

                metadata = new MapVtfMetadata((int)flags, frames, depth);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private readonly struct MapVtfMetadata
        {
            public MapVtfMetadata(int flags, int frames, int depth)
            {
                Flags = flags;
                Frames = frames;
                Depth = depth;
            }

            public int Flags { get; }
            public int Frames { get; }
            public int Depth { get; }
        }
    }
}
