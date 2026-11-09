using System;
using System.IO;
using System.Threading;

namespace GmodAddonCompressor.Systems.Reporting
{
    internal enum AddonFileKind
    {
        Models,
        Textures,
        Sounds,
        Others
    }

    internal readonly record struct AddonKindStats(int Files, long Bytes);

    /// <summary>
    /// File count and bytes of one addon folder grouped the way the "Otimizar" assistant shows them:
    /// Modelos (models/ and the Lua-drawn geometry streams in data_static/gmo/), Texturas (VTF/VMT,
    /// images and the Lua-decoded texture streams in data_static/gmot/), Sons (WAV/MP3/OGG) and Outros.
    /// </summary>
    internal sealed class AddonInventory
    {
        private readonly AddonKindStats[] _kinds = new AddonKindStats[4];

        public string RootPath { get; private init; } = string.Empty;
        public bool HasModelsFolder { get; private init; }
        public int TotalFiles { get; private set; }
        public long TotalBytes { get; private set; }

        public AddonKindStats this[AddonFileKind kind] => _kinds[(int)kind];
        public AddonKindStats Models => _kinds[(int)AddonFileKind.Models];
        public AddonKindStats Textures => _kinds[(int)AddonFileKind.Textures];
        public AddonKindStats Sounds => _kinds[(int)AddonFileKind.Sounds];
        public AddonKindStats Others => _kinds[(int)AddonFileKind.Others];

        /// <summary>True when Models "Tela" can run: a models/ folder with at least one .mdl.</summary>
        public bool HasModels => HasModelsFolder && Models.Files > 0;
        public bool HasMedia => Textures.Files > 0 || Sounds.Files > 0;

        internal static AddonFileKind Classify(string relativePath)
        {
            string rel = relativePath.Replace('\\', '/').TrimStart('/');
            if (rel.StartsWith("models/", StringComparison.OrdinalIgnoreCase)
                || rel.StartsWith("data_static/gmo/", StringComparison.OrdinalIgnoreCase))
                return AddonFileKind.Models;
            if (rel.StartsWith("data_static/gmot/", StringComparison.OrdinalIgnoreCase))
                return AddonFileKind.Textures;

            switch (Path.GetExtension(rel).ToLowerInvariant())
            {
                case ".vtf":
                case ".vmt":
                case ".png":
                case ".jpg":
                case ".jpeg":
                case ".tga":
                    return AddonFileKind.Textures;
                case ".wav":
                case ".mp3":
                case ".ogg":
                    return AddonFileKind.Sounds;
                default:
                    return AddonFileKind.Others;
            }
        }

        internal static AddonInventory Scan(string rootPath, CancellationToken token)
        {
            string root = Path.GetFullPath(rootPath);
            bool hasModelsFolder = Directory.Exists(Path.Combine(root, "models"));
            var inventory = new AddonInventory { RootPath = root, HasModelsFolder = hasModelsFolder };
            var options = new EnumerationOptions
            {
                RecurseSubdirectories = true,
                IgnoreInaccessible = true,
                AttributesToSkip = FileAttributes.ReparsePoint
            };
            foreach (string file in Directory.EnumerateFiles(root, "*", options))
            {
                token.ThrowIfCancellationRequested();
                long length;
                try
                {
                    length = new FileInfo(file).Length;
                }
                catch (IOException)
                {
                    continue;
                }
                catch (UnauthorizedAccessException)
                {
                    continue;
                }

                var kind = Classify(Path.GetRelativePath(root, file));
                var current = inventory._kinds[(int)kind];
                inventory._kinds[(int)kind] = new AddonKindStats(current.Files + 1, current.Bytes + length);
                inventory.TotalFiles++;
                inventory.TotalBytes += length;
            }

            return inventory;
        }
    }
}
