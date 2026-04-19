using GmodAddonCompressor.Models;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems.Vtf
{
    internal enum AddonVtfSourceRoutePreference
    {
        RawSplitFirst,
        ExportSplitFirst
    }

    internal readonly struct AddonVtfFxProfile
    {
        public static AddonVtfFxProfile None { get; } = new(false, "none", 0, 0, "none", AddonVtfSourceRoutePreference.RawSplitFirst);

        public AddonVtfFxProfile(
            bool isSensitive,
            string group,
            int score,
            int minimumShortSide,
            string signalSummary,
            AddonVtfSourceRoutePreference preferredSourceRoute)
        {
            IsSensitive = isSensitive;
            Group = string.IsNullOrWhiteSpace(group) ? "none" : group;
            Score = score;
            MinimumShortSide = minimumShortSide;
            SignalSummary = string.IsNullOrWhiteSpace(signalSummary) ? "none" : signalSummary;
            PreferredSourceRoute = preferredSourceRoute;
        }

        public bool IsSensitive { get; }
        public string Group { get; }
        public int Score { get; }
        public int MinimumShortSide { get; }
        public string SignalSummary { get; }
        public AddonVtfSourceRoutePreference PreferredSourceRoute { get; }
    }

    internal sealed class AddonVtfTextureSignals
    {
        public AddonVtfTextureSignals(string textureKey)
        {
            TextureKey = textureKey;
            VmtKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        }

        public string TextureKey { get; }
        public HashSet<string> VmtKeys { get; }
        public string NamespaceRoot { get; set; } = string.Empty;
        public bool UsesBaseTextureAlpha { get; set; }
        public bool IsNormalLike { get; set; }
        public bool UsesNormalAlpha { get; set; }
        public bool HasSpriteShader { get; set; }
        public bool HasEffectLuaReference { get; set; }
        public bool HasPcfReference { get; set; }
        public bool HasWeakNameHint { get; set; }
        public bool InParticleNamespace { get; set; }
        public bool InEffectsNamespace { get; set; }
        public bool InTrailNamespace { get; set; }
        public bool InCustomEffectNamespace { get; set; }
        public bool IsExcludedNamespace { get; set; }
        public bool HasTranslucent { get; set; }
        public bool HasVertexAlpha { get; set; }
        public bool HasAdditive { get; set; }
        public bool HasIgnoreZ { get; set; }
        public bool HasDepthBlend { get; set; }

        public int FxFlagCount
        {
            get
            {
                int count = 0;
                if (HasTranslucent)
                    count++;
                if (HasVertexAlpha)
                    count++;
                if (HasAdditive)
                    count++;
                if (HasIgnoreZ)
                    count++;
                if (HasDepthBlend)
                    count++;
                return count;
            }
        }

        public AddonVtfTextureSignals Clone()
        {
            var clone = new AddonVtfTextureSignals(TextureKey)
            {
                NamespaceRoot = NamespaceRoot,
                UsesBaseTextureAlpha = UsesBaseTextureAlpha,
                IsNormalLike = IsNormalLike,
                UsesNormalAlpha = UsesNormalAlpha,
                HasSpriteShader = HasSpriteShader,
                HasEffectLuaReference = HasEffectLuaReference,
                HasPcfReference = HasPcfReference,
                HasWeakNameHint = HasWeakNameHint,
                InParticleNamespace = InParticleNamespace,
                InEffectsNamespace = InEffectsNamespace,
                InTrailNamespace = InTrailNamespace,
                InCustomEffectNamespace = InCustomEffectNamespace,
                IsExcludedNamespace = IsExcludedNamespace,
                HasTranslucent = HasTranslucent,
                HasVertexAlpha = HasVertexAlpha,
                HasAdditive = HasAdditive,
                HasIgnoreZ = HasIgnoreZ,
                HasDepthBlend = HasDepthBlend
            };

            foreach (string vmtKey in VmtKeys)
                clone.VmtKeys.Add(vmtKey);

            return clone;
        }
    }

    internal sealed class AddonVtfCompressionAnalysis
    {
        public static AddonVtfCompressionAnalysis Empty { get; } = new(
            new Dictionary<string, AddonVtfTextureSignals>(StringComparer.OrdinalIgnoreCase),
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase),
            new HashSet<string>(StringComparer.OrdinalIgnoreCase),
            new HashSet<string>(StringComparer.OrdinalIgnoreCase),
            new HashSet<string>(StringComparer.OrdinalIgnoreCase));

        public AddonVtfCompressionAnalysis(
            Dictionary<string, AddonVtfTextureSignals> textures,
            Dictionary<string, string> vmtTextureMap,
            HashSet<string> effectLuaTextureKeys,
            HashSet<string> pcfTextureKeys,
            HashSet<string> effectNamespaces)
        {
            Textures = textures;
            VmtTextureMap = vmtTextureMap;
            EffectLuaTextureKeys = effectLuaTextureKeys;
            PcfTextureKeys = pcfTextureKeys;
            EffectNamespaces = effectNamespaces;
        }

        private Dictionary<string, AddonVtfTextureSignals> Textures { get; }
        private Dictionary<string, string> VmtTextureMap { get; }
        private HashSet<string> EffectLuaTextureKeys { get; }
        private HashSet<string> PcfTextureKeys { get; }
        private HashSet<string> EffectNamespaces { get; }

        public bool UsesBaseTextureAlpha(string? textureKey)
        {
            return TryGetSignals(textureKey, out AddonVtfTextureSignals? signals) &&
                   signals != null &&
                   signals.UsesBaseTextureAlpha;
        }

        public bool IsNormalLike(string? textureKey)
        {
            return TryGetSignals(textureKey, out AddonVtfTextureSignals? signals) &&
                   signals != null &&
                   signals.IsNormalLike;
        }

        public bool UsesNormalAlpha(string? textureKey)
        {
            return TryGetSignals(textureKey, out AddonVtfTextureSignals? signals) &&
                   signals != null &&
                   signals.UsesNormalAlpha;
        }

        public AddonVtfFxProfile GetFxProfile(string? textureKey, string? relativePath)
        {
            string normalizedTextureKey = AddonVtfCompressionPlanner.NormalizeTextureReference(textureKey ?? string.Empty);
            AddonVtfTextureSignals signals = TryGetSignals(normalizedTextureKey, out AddonVtfTextureSignals? existing)
                ? existing!.Clone()
                : new AddonVtfTextureSignals(normalizedTextureKey);

            AddonVtfCompressionPlanner.ApplyPathSignals(signals, normalizedTextureKey, relativePath, EffectNamespaces);

            if (!string.IsNullOrWhiteSpace(normalizedTextureKey))
            {
                if (EffectLuaTextureKeys.Contains(normalizedTextureKey))
                    signals.HasEffectLuaReference = true;

                if (PcfTextureKeys.Contains(normalizedTextureKey))
                    signals.HasPcfReference = true;
            }

            return EvaluateFxProfile(signals);
        }

        private bool TryGetSignals(string? textureKey, out AddonVtfTextureSignals? signals)
        {
            signals = null;
            if (string.IsNullOrWhiteSpace(textureKey))
                return false;

            return Textures.TryGetValue(textureKey, out signals);
        }

        private static AddonVtfFxProfile EvaluateFxProfile(AddonVtfTextureSignals signals)
        {
            if (signals.IsExcludedNamespace)
                return AddonVtfFxProfile.None;

            int score = 0;
            var tags = new List<string>();

            if (signals.InParticleNamespace)
            {
                score += 2;
                tags.Add("ns_particle");
            }
            else if (signals.InEffectsNamespace)
            {
                score += 2;
                tags.Add("ns_effects");
            }
            else if (signals.InTrailNamespace)
            {
                score += 1;
                tags.Add("ns_trails");
            }
            else if (signals.InCustomEffectNamespace)
            {
                score += 1;
                tags.Add($"ns_custom_{signals.NamespaceRoot}");
            }

            if (signals.HasSpriteShader)
            {
                score += 2;
                tags.Add("shader_sprite");
            }

            if (signals.HasPcfReference)
            {
                score += 2;
                tags.Add("pcf_ref");
            }

            if (signals.HasEffectLuaReference)
            {
                score += 2;
                tags.Add("lua_fx_ref");
            }

            if (signals.FxFlagCount >= 2)
            {
                score += 2;
                tags.Add($"flags_{signals.FxFlagCount}");
            }
            else if (signals.FxFlagCount == 1)
            {
                score += 1;
                tags.Add("flags_1");
            }

            if (signals.HasWeakNameHint)
            {
                score += 1;
                tags.Add("semantic_name");
            }

            bool hasUsageSignal = signals.HasPcfReference || signals.HasEffectLuaReference;
            bool hasVisualSignal = signals.HasSpriteShader || signals.FxFlagCount >= 2;

            bool isSensitive;
            if (signals.InParticleNamespace || signals.InEffectsNamespace)
            {
                isSensitive = score >= 4 &&
                              (hasVisualSignal || hasUsageSignal || signals.HasWeakNameHint);
            }
            else if (signals.InTrailNamespace || signals.InCustomEffectNamespace)
            {
                isSensitive = score >= 5 &&
                              (hasUsageSignal || (signals.HasWeakNameHint && hasVisualSignal));
            }
            else
            {
                isSensitive = score >= 6 && hasUsageSignal && hasVisualSignal;
            }

            if (!isSensitive)
            {
                return new AddonVtfFxProfile(
                    false,
                    "none",
                    score,
                    0,
                    tags.Count == 0 ? "none" : string.Join("+", tags),
                    AddonVtfSourceRoutePreference.RawSplitFirst);
            }

            string group = signals.InParticleNamespace
                ? "particle"
                : signals.InEffectsNamespace
                    ? "effects"
                    : signals.InTrailNamespace
                        ? "trails"
                        : !string.IsNullOrWhiteSpace(signals.NamespaceRoot)
                            ? $"custom:{signals.NamespaceRoot}"
                            : "custom";

            return new AddonVtfFxProfile(
                true,
                group,
                score,
                32,
                tags.Count == 0 ? "none" : string.Join("+", tags),
                DeterminePreferredSourceRoute(signals));
        }

        private static AddonVtfSourceRoutePreference DeterminePreferredSourceRoute(AddonVtfTextureSignals signals)
        {
            bool isTranslucentNonAdditiveSprite =
                signals.HasTranslucent &&
                signals.HasVertexAlpha &&
                !signals.HasAdditive;

            if (isTranslucentNonAdditiveSprite)
                return AddonVtfSourceRoutePreference.RawSplitFirst;

            bool prefersExportSplit =
                (signals.InEffectsNamespace || signals.HasPcfReference) &&
                (signals.HasAdditive || signals.HasSpriteShader);

            return prefersExportSplit
                ? AddonVtfSourceRoutePreference.ExportSplitFirst
                : AddonVtfSourceRoutePreference.RawSplitFirst;
        }
    }

    internal readonly struct AddonVtfCompressionPlan
    {
        public AddonVtfCompressionPlan(bool preserveWithoutCompression, string targetFormat, string reason, AddonVtfFxProfile fxProfile)
        {
            PreserveWithoutCompression = preserveWithoutCompression;
            TargetFormat = targetFormat;
            Reason = reason;
            FxProfile = fxProfile;
        }

        public bool PreserveWithoutCompression { get; }
        public string TargetFormat { get; }
        public string Reason { get; }
        public AddonVtfFxProfile FxProfile { get; }
    }

    internal static class AddonVtfCompressionPlanner
    {
        private static readonly Regex VmtTextureAssignmentRegex = new Regex(
            "^\\s*\"?(?<key>\\$[^\\s\"]+)\"?\\s+(?:\"(?<value>[^\"]+)\"|(?<value>[^\\s\\{\\}]+))",
            RegexOptions.Compiled | RegexOptions.IgnoreCase | RegexOptions.Multiline);

        private static readonly Regex LuaMaterialReferenceRegex = new Regex(
            "(?:Material|emitter:Add)\\s*\\(\\s*[\"'](?<value>[^\"']+)[\"']",
            RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static readonly Regex PcfMaterialReferenceRegex = new Regex(
            "(?<value>[A-Za-z0-9_./\\\\-]+\\.(?:vmt|vtf))",
            RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static readonly string[] EffectLuaMarkers =
        {
            "CreateParticleSystem",
            "ParticleEmitter(",
            "emitter:Add(",
            "PrecacheParticleSystem(",
            "game.AddParticles(",
            "render.DrawBeam(",
            "render.DrawSprite(",
            "util.Effect("
        };

        private static readonly HashSet<string> BumpmapKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$bumpmap",
            "$normalmap"
        };

        private static readonly HashSet<string> BaseTextureKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$basetexture"
        };

        private static readonly HashSet<string> BumpmapAlphaSemanticKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$normalmapalphaenvmapmask"
        };

        private static readonly HashSet<string> BaseTextureAlphaSemanticKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "$basemapalphaphongmask",
            "$basealphaenvmapmask",
            "$translucent",
            "$alphatest",
            "$additive"
        };

        private static readonly HashSet<string> SpriteShaders = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "SpriteCard",
            "Sprite"
        };

        private static readonly HashSet<string> ExcludedFxNamespaces = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "vgui",
            "scope"
        };

        private static readonly HashSet<string> FxSemanticTokens = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "smoke",
            "muzzle",
            "tracer",
            "trace",
            "shell",
            "flare",
            "beam",
            "spark",
            "fire",
            "dust",
            "heatwave",
            "flash",
            "glow"
        };

        private static readonly HashSet<int> AlphaCapableFormats = new HashSet<int>
        {
            0, 1, 6, 8, 11, 12, 14, 15, 19, 20, 21, 24, 25
        };

        private static readonly HashSet<int> OpaqueCompatibleFormats = new HashSet<int>
        {
            2, 3, 4, 13, 16, 17, 18
        };

        internal static AddonVtfCompressionAnalysis Analyze(string addonRoot)
        {
            if (string.IsNullOrWhiteSpace(addonRoot) || !Directory.Exists(addonRoot))
                return AddonVtfCompressionAnalysis.Empty;

            var textures = new Dictionary<string, AddonVtfTextureSignals>(StringComparer.OrdinalIgnoreCase);
            var vmtTextureMap = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var effectLuaTextureKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var pcfTextureKeys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var effectNamespaces = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            foreach (string vmtPath in Directory.EnumerateFiles(addonRoot, "*.vmt", SearchOption.AllDirectories))
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

                string materialKey = GetMaterialKeyFromVmtPath(addonRoot, vmtPath);
                string shaderName = GetShaderName(content);
                string? baseTexture = null;
                bool usesBaseTextureAlpha = false;
                string? bumpTexture = null;
                bool usesBumpAlpha = false;
                bool hasTranslucent = false;
                bool hasVertexAlpha = false;
                bool hasAdditive = false;
                bool hasIgnoreZ = false;
                bool hasDepthBlend = false;

                foreach (Match match in VmtTextureAssignmentRegex.Matches(content))
                {
                    string key = match.Groups["key"].Value.Trim();
                    string rawValue = match.Groups["value"].Value;
                    string value = NormalizeTextureReference(rawValue);

                    if (BaseTextureKeys.Contains(key) && !string.IsNullOrWhiteSpace(value))
                        baseTexture = value;

                    if (BumpmapKeys.Contains(key) && !string.IsNullOrWhiteSpace(value))
                        bumpTexture = value;

                    if (BaseTextureAlphaSemanticKeys.Contains(key) && IsTruthyMaterialValue(rawValue))
                        usesBaseTextureAlpha = true;

                    if (BumpmapAlphaSemanticKeys.Contains(key) && IsTruthyMaterialValue(rawValue))
                        usesBumpAlpha = true;

                    if (key.Equals("$translucent", StringComparison.OrdinalIgnoreCase) && IsTruthyMaterialValue(rawValue))
                        hasTranslucent = true;

                    if (key.Equals("$vertexalpha", StringComparison.OrdinalIgnoreCase) && IsTruthyMaterialValue(rawValue))
                        hasVertexAlpha = true;

                    if (key.Equals("$additive", StringComparison.OrdinalIgnoreCase) && IsTruthyMaterialValue(rawValue))
                        hasAdditive = true;

                    if (key.Equals("$ignorez", StringComparison.OrdinalIgnoreCase) && IsTruthyMaterialValue(rawValue))
                        hasIgnoreZ = true;

                    if (key.Equals("$depthblend", StringComparison.OrdinalIgnoreCase) && IsTruthyMaterialValue(rawValue))
                        hasDepthBlend = true;
                }

                if (!string.IsNullOrWhiteSpace(baseTexture))
                {
                    AddonVtfTextureSignals signals = GetOrCreateTextureSignals(textures, baseTexture);
                    signals.VmtKeys.Add(materialKey);
                    signals.UsesBaseTextureAlpha |= usesBaseTextureAlpha;
                    signals.HasSpriteShader |= SpriteShaders.Contains(shaderName);
                    signals.HasTranslucent |= hasTranslucent;
                    signals.HasVertexAlpha |= hasVertexAlpha;
                    signals.HasAdditive |= hasAdditive;
                    signals.HasIgnoreZ |= hasIgnoreZ;
                    signals.HasDepthBlend |= hasDepthBlend;
                    ApplyPathSignals(signals, baseTexture, null, effectNamespaces: null);
                    vmtTextureMap[materialKey] = baseTexture;
                }

                if (!string.IsNullOrWhiteSpace(bumpTexture))
                {
                    AddonVtfTextureSignals signals = GetOrCreateTextureSignals(textures, bumpTexture);
                    signals.IsNormalLike = true;
                    signals.UsesNormalAlpha |= usesBumpAlpha;
                    ApplyPathSignals(signals, bumpTexture, null, effectNamespaces: null);
                }
            }

            foreach (string luaPath in Directory.EnumerateFiles(addonRoot, "*.lua", SearchOption.AllDirectories))
            {
                string content;
                try
                {
                    content = File.ReadAllText(luaPath);
                }
                catch
                {
                    continue;
                }

                string normalizedLuaPath = luaPath.Replace('\\', '/');
                bool isEffectLua = normalizedLuaPath.IndexOf("/lua/effects/", StringComparison.OrdinalIgnoreCase) >= 0 ||
                                   EffectLuaMarkers.Any(marker => content.IndexOf(marker, StringComparison.OrdinalIgnoreCase) >= 0);
                if (!isEffectLua)
                    continue;

                foreach (Match match in LuaMaterialReferenceRegex.Matches(content))
                {
                    string rawReference = match.Groups["value"].Value;
                    MarkReferencedTexture(
                        rawReference,
                        textures,
                        vmtTextureMap,
                        effectLuaTextureKeys,
                        effectNamespaces,
                        isLuaReference: true);
                }
            }

            foreach (string pcfPath in Directory.EnumerateFiles(addonRoot, "*.pcf", SearchOption.AllDirectories))
            {
                string content;
                try
                {
                    content = File.ReadAllText(pcfPath, System.Text.Encoding.Latin1);
                }
                catch
                {
                    continue;
                }

                foreach (Match match in PcfMaterialReferenceRegex.Matches(content))
                {
                    string rawReference = match.Groups["value"].Value;
                    MarkReferencedTexture(
                        rawReference,
                        textures,
                        vmtTextureMap,
                        pcfTextureKeys,
                        effectNamespaces,
                        isLuaReference: false);
                }
            }

            return new AddonVtfCompressionAnalysis(
                textures,
                vmtTextureMap,
                effectLuaTextureKeys,
                pcfTextureKeys,
                effectNamespaces);
        }

        internal static bool TryCreatePlan(
            string vtfFilePath,
            VtfFileModel vtfInfo,
            AddonVtfCompressionAnalysis analysis,
            bool fullyOpaqueAlpha,
            out AddonVtfCompressionPlan plan)
        {
            if (vtfInfo.Frames > 1)
            {
                plan = new AddonVtfCompressionPlan(true, string.Empty, "animated_frames", AddonVtfFxProfile.None);
                return false;
            }

            if (vtfInfo.Depth > 1)
            {
                plan = new AddonVtfCompressionPlan(true, string.Empty, "volume_depth", AddonVtfFxProfile.None);
                return false;
            }

            string? relativePath = null;
            if (TryGetMaterialRelativePath(vtfFilePath, out string? parsedRelativePath))
            {
                relativePath = parsedRelativePath;
                if (string.IsNullOrWhiteSpace(relativePath))
                {
                    plan = new AddonVtfCompressionPlan(true, string.Empty, "material_relative_path_missing", AddonVtfFxProfile.None);
                    return false;
                }

                string fileName = Path.GetFileName(relativePath) ?? string.Empty;
                string fileNameNoExt = Path.GetFileNameWithoutExtension(fileName) ?? string.Empty;

                if (relativePath.StartsWith("materials/maps/", StringComparison.OrdinalIgnoreCase))
                {
                    plan = new AddonVtfCompressionPlan(true, string.Empty, "map_material_namespace", AddonVtfFxProfile.None);
                    return false;
                }

                if (relativePath.StartsWith("materials/skybox/", StringComparison.OrdinalIgnoreCase))
                {
                    plan = new AddonVtfCompressionPlan(true, string.Empty, "skybox_namespace", AddonVtfFxProfile.None);
                    return false;
                }

                if (fileName.EndsWith(".hdr.vtf", StringComparison.OrdinalIgnoreCase) ||
                    fileNameNoExt.IndexOf("_hdr", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    plan = new AddonVtfCompressionPlan(true, string.Empty, "hdr_variant", AddonVtfFxProfile.None);
                    return false;
                }

                if (fileNameNoExt.StartsWith("c-", StringComparison.OrdinalIgnoreCase) ||
                    fileNameNoExt.StartsWith("cubemapdefault", StringComparison.OrdinalIgnoreCase))
                {
                    plan = new AddonVtfCompressionPlan(true, string.Empty, "cubemap_name", AddonVtfFxProfile.None);
                    return false;
                }
            }

            string? textureKey = GetTextureKey(vtfFilePath);
            bool usesBaseTextureAlpha = analysis.UsesBaseTextureAlpha(textureKey);
            bool normalLike = analysis.IsNormalLike(textureKey) || (vtfInfo.Flags & 0x00000080) != 0;
            bool normalAlpha = analysis.UsesNormalAlpha(textureKey);
            AddonVtfFxProfile fxProfile = analysis.GetFxProfile(textureKey, relativePath);

            string targetFormat;
            string reason;

            if (normalLike)
            {
                targetFormat = "DXT5";
                reason = "normal_like_dxt5";
            }
            else if (AlphaCapableFormats.Contains(vtfInfo.HighResImageFormat))
            {
                bool preserveAlpha = normalAlpha ||
                                     (!fullyOpaqueAlpha && (usesBaseTextureAlpha || fxProfile.IsSensitive));
                targetFormat = preserveAlpha ? "DXT5" : "DXT1";
                if (targetFormat == "DXT5")
                {
                    reason = normalAlpha
                        ? "normal_alpha_dxt5"
                        : fxProfile.IsSensitive
                            ? "fx_sensitive_alpha_dxt5"
                            : "base_alpha_semantic_dxt5";
                }
                else
                {
                    reason = fxProfile.IsSensitive ? "fx_sensitive_opaque_dxt1" : "alpha_capable_dxt1";
                }
            }
            else if (OpaqueCompatibleFormats.Contains(vtfInfo.HighResImageFormat))
            {
                targetFormat = fullyOpaqueAlpha ? "DXT1" : "DXT5";
                if (targetFormat == "DXT5")
                {
                    reason = fxProfile.IsSensitive ? "fx_sensitive_opaquefmt_dxt5" : "opaque_compatible_dxt5";
                }
                else
                {
                    reason = fxProfile.IsSensitive ? "fx_sensitive_opaque_dxt1" : "opaque_compatible_dxt1";
                }
            }
            else
            {
                string formatName = TryGetFormatName(vtfInfo.HighResImageFormat, out string? knownFormatName)
                    ? knownFormatName!
                    : vtfInfo.HighResImageFormat.ToString();
                plan = new AddonVtfCompressionPlan(true, string.Empty, $"unsupported_format_{formatName}", fxProfile);
                return false;
            }

            plan = new AddonVtfCompressionPlan(false, targetFormat, reason, fxProfile);
            return true;
        }

        internal static bool TryReadMetadata(string vtfPath, out VtfFileModel metadata)
        {
            metadata = default;

            try
            {
                using FileStream stream = File.OpenRead(vtfPath);
                using BinaryReader reader = new BinaryReader(stream);

                byte[] signature = reader.ReadBytes(4);
                if (signature.Length != 4 ||
                    signature[0] != (byte)'V' ||
                    signature[1] != (byte)'T' ||
                    signature[2] != (byte)'F')
                {
                    return false;
                }

                int majorVersion = reader.ReadInt32();
                int minorVersion = reader.ReadInt32();
                int headerSize = reader.ReadInt32();
                int width = reader.ReadUInt16();
                int height = reader.ReadUInt16();
                int flags = reader.ReadInt32();
                int frames = reader.ReadUInt16();
                int firstFrame = reader.ReadUInt16();
                _ = reader.ReadBytes(4);
                _ = reader.ReadSingle();
                _ = reader.ReadSingle();
                _ = reader.ReadSingle();
                _ = reader.ReadBytes(4);
                _ = reader.ReadSingle();
                int highResImageFormat = reader.ReadInt32();
                int lowMipCount = reader.ReadByte();
                int lowResImageFormat = reader.ReadInt32();
                int lowResWidth = reader.ReadByte();
                int lowResHeight = reader.ReadByte();
                int depth = (majorVersion > 7 || (majorVersion == 7 && minorVersion >= 2))
                    ? reader.ReadUInt16()
                    : 1;

                metadata = new VtfFileModel
                {
                    MajorVersion = majorVersion,
                    MinorVersion = minorVersion,
                    HeaderSize = headerSize,
                    Width = width,
                    Height = height,
                    Flags = flags,
                    Frames = frames,
                    FirstFrame = firstFrame,
                    Depth = depth,
                    HighResImageFormat = highResImageFormat,
                    LowMipCount = lowMipCount,
                    LowResImageFormat = lowResImageFormat,
                    LowResWidth = lowResWidth,
                    LowResHeight = lowResHeight
                };

                return true;
            }
            catch
            {
                return false;
            }
        }

        internal static void PatchFlags(string vtfFilePath, int originalFlags)
        {
            using FileStream stream = new FileStream(vtfFilePath, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
            using BinaryWriter writer = new BinaryWriter(stream);
            writer.Seek(20, SeekOrigin.Begin);
            writer.Write(originalFlags);
        }

        internal static bool TryGetFormatName(int formatId, out string? formatName)
        {
            formatName = formatId switch
            {
                0 => "RGBA8888",
                1 => "ABGR8888",
                2 => "RGB888",
                3 => "BGR888",
                4 => "RGB565",
                5 => "I8",
                6 => "IA88",
                8 => "A8",
                11 => "ARGB8888",
                12 => "BGRA8888",
                13 => "DXT1",
                14 => "DXT3",
                15 => "DXT5",
                16 => "BGRX8888",
                17 => "BGR565",
                18 => "BGRX5551",
                19 => "BGRA4444",
                20 => "DXT1_ONEBITALPHA",
                21 => "BGRA5551",
                22 => "UV88",
                23 => "UVWQ8888",
                24 => "RGBA16161616F",
                25 => "RGBA16161616",
                26 => "UVLX8888",
                _ => null
            };

            return !string.IsNullOrWhiteSpace(formatName);
        }

        private static AddonVtfTextureSignals GetOrCreateTextureSignals(
            Dictionary<string, AddonVtfTextureSignals> textures,
            string textureKey)
        {
            if (!textures.TryGetValue(textureKey, out AddonVtfTextureSignals? signals))
            {
                signals = new AddonVtfTextureSignals(textureKey);
                textures[textureKey] = signals;
            }

            return signals;
        }

        private static void MarkReferencedTexture(
            string rawReference,
            Dictionary<string, AddonVtfTextureSignals> textures,
            Dictionary<string, string> vmtTextureMap,
            HashSet<string> referenceSet,
            HashSet<string> effectNamespaces,
            bool isLuaReference)
        {
            string referenceKey = NormalizeTextureReference(rawReference);
            if (string.IsNullOrWhiteSpace(referenceKey))
                return;

            referenceSet.Add(referenceKey);
            AddNamespace(effectNamespaces, referenceKey);

            string resolvedTextureKey = vmtTextureMap.TryGetValue(referenceKey, out string? mappedTextureKey)
                ? mappedTextureKey
                : referenceKey;

            referenceSet.Add(resolvedTextureKey);
            AddNamespace(effectNamespaces, resolvedTextureKey);

            if (textures.TryGetValue(resolvedTextureKey, out AddonVtfTextureSignals? signals))
            {
                if (isLuaReference)
                    signals.HasEffectLuaReference = true;
                else
                    signals.HasPcfReference = true;

                ApplyPathSignals(signals, resolvedTextureKey, null, effectNamespaces: null);
            }
        }

        private static string GetShaderName(string content)
        {
            foreach (string rawLine in content.Split(new[] { "\r\n", "\n" }, StringSplitOptions.None))
            {
                string line = rawLine.Trim();
                if (string.IsNullOrWhiteSpace(line) || line.StartsWith("//", StringComparison.Ordinal))
                    continue;

                return line.Trim().Trim('"').Trim();
            }

            return string.Empty;
        }

        private static string GetMaterialKeyFromVmtPath(string addonRoot, string vmtPath)
        {
            string relativePath = Path.GetRelativePath(addonRoot, vmtPath).Replace('\\', '/');
            int index = relativePath.IndexOf("materials/", StringComparison.OrdinalIgnoreCase);
            if (index >= 0)
                relativePath = relativePath.Substring(index + "materials/".Length);

            if (relativePath.EndsWith(".vmt", StringComparison.OrdinalIgnoreCase))
                relativePath = relativePath.Substring(0, relativePath.Length - 4);

            return relativePath.Trim('/').ToLowerInvariant();
        }

        internal static void ApplyPathSignals(
            AddonVtfTextureSignals signals,
            string? textureKey,
            string? relativePath,
            ISet<string>? effectNamespaces)
        {
            string resolvedKey = !string.IsNullOrWhiteSpace(textureKey)
                ? NormalizeTextureReference(textureKey)
                : GetTextureKeyFromRelativePath(relativePath);
            if (string.IsNullOrWhiteSpace(resolvedKey))
                return;

            string namespaceRoot = GetNamespaceRoot(resolvedKey);
            if (string.IsNullOrWhiteSpace(signals.NamespaceRoot))
                signals.NamespaceRoot = namespaceRoot;

            if (ExcludedFxNamespaces.Contains(namespaceRoot))
                signals.IsExcludedNamespace = true;

            if (namespaceRoot.Equals("particle", StringComparison.OrdinalIgnoreCase))
                signals.InParticleNamespace = true;

            if (namespaceRoot.Equals("effects", StringComparison.OrdinalIgnoreCase))
                signals.InEffectsNamespace = true;

            if (namespaceRoot.Equals("trails", StringComparison.OrdinalIgnoreCase))
                signals.InTrailNamespace = true;

            if (!signals.IsExcludedNamespace &&
                !signals.InParticleNamespace &&
                !signals.InEffectsNamespace &&
                !signals.InTrailNamespace &&
                effectNamespaces != null &&
                effectNamespaces.Contains(namespaceRoot))
            {
                signals.InCustomEffectNamespace = true;
            }

            if (HasFxSemanticHint(resolvedKey))
                signals.HasWeakNameHint = true;
        }

        private static bool HasFxSemanticHint(string textureKey)
        {
            if (string.IsNullOrWhiteSpace(textureKey))
                return false;

            string hintSource = textureKey;
            int slashIndex = textureKey.IndexOf('/');
            if (slashIndex >= 0 && slashIndex + 1 < textureKey.Length)
                hintSource = textureKey.Substring(slashIndex + 1);

            foreach (string token in Regex.Split(hintSource, "[^A-Za-z0-9]+"))
            {
                if (string.IsNullOrWhiteSpace(token))
                    continue;

                if (FxSemanticTokens.Contains(token))
                    return true;
            }

            return false;
        }

        private static void AddNamespace(ISet<string> namespaces, string textureKey)
        {
            string namespaceRoot = GetNamespaceRoot(textureKey);
            if (!string.IsNullOrWhiteSpace(namespaceRoot))
                namespaces.Add(namespaceRoot);
        }

        private static string GetNamespaceRoot(string textureKey)
        {
            if (string.IsNullOrWhiteSpace(textureKey))
                return string.Empty;

            string normalized = NormalizeTextureReference(textureKey);
            int slashIndex = normalized.IndexOf('/');
            return slashIndex >= 0
                ? normalized.Substring(0, slashIndex)
                : normalized;
        }

        private static bool TryGetMaterialRelativePath(string fullPath, out string? relativePath)
        {
            relativePath = null;

            string normalized = Path.GetFullPath(fullPath).Replace('\\', '/');
            int index = normalized.LastIndexOf("/materials/", StringComparison.OrdinalIgnoreCase);
            if (index < 0)
                return false;

            relativePath = normalized.Substring(index + 1);
            return true;
        }

        private static string? GetTextureKey(string fullPath)
        {
            if (!TryGetMaterialRelativePath(fullPath, out string? relativePath) ||
                string.IsNullOrWhiteSpace(relativePath) ||
                !relativePath.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase))
            {
                return null;
            }

            string key = relativePath.Substring("materials/".Length, relativePath.Length - "materials/".Length - 4);
            return key.Trim('/').ToLowerInvariant();
        }

        private static string GetTextureKeyFromRelativePath(string? relativePath)
        {
            if (string.IsNullOrWhiteSpace(relativePath))
                return string.Empty;

            string normalized = relativePath.Replace('\\', '/').Trim();
            if (normalized.StartsWith("materials/", StringComparison.OrdinalIgnoreCase))
                normalized = normalized.Substring("materials/".Length);

            if (normalized.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase) ||
                normalized.EndsWith(".vmt", StringComparison.OrdinalIgnoreCase))
            {
                normalized = normalized.Substring(0, normalized.Length - 4);
            }

            return normalized.Trim('/').ToLowerInvariant();
        }

        internal static string NormalizeTextureReference(string rawValue)
        {
            if (string.IsNullOrWhiteSpace(rawValue))
                return string.Empty;

            string value = rawValue.Trim().Replace('\\', '/');
            if (value.StartsWith("materials/", StringComparison.OrdinalIgnoreCase))
                value = value.Substring("materials/".Length);

            if (value.EndsWith(".vtf", StringComparison.OrdinalIgnoreCase) ||
                value.EndsWith(".vmt", StringComparison.OrdinalIgnoreCase))
            {
                value = value.Substring(0, value.Length - 4);
            }

            return value.Trim().Trim('/').ToLowerInvariant();
        }

        private static bool IsTruthyMaterialValue(string rawValue)
        {
            if (string.IsNullOrWhiteSpace(rawValue))
                return false;

            string value = rawValue.Trim().Trim('"').Trim();
            return !value.Equals("0", StringComparison.OrdinalIgnoreCase) &&
                   !value.Equals("false", StringComparison.OrdinalIgnoreCase);
        }
    }
}
