using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems
{
    internal static class AudioReferenceUpdater
    {
        private static readonly HashSet<string> _textExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            ".lua",
            ".txt",
            ".cfg",
            ".ini",
            ".json",
            ".vdf",
            ".kv",
            ".vmf",
            ".vmt",
            ".nut"
        };

        private static readonly Regex _audioRefRegex = new Regex(
            @"(?<path>[A-Za-z0-9_\-./\\]+?)\.(wav|ogg|mp3)(?![A-Za-z0-9])",
            RegexOptions.IgnoreCase | RegexOptions.Compiled);

        internal static int UpdateReferences(string addonDirectoryPath, bool forceReplace)
        {
            if (string.IsNullOrWhiteSpace(addonDirectoryPath) || !Directory.Exists(addonDirectoryPath))
                return 0;

            int updatedFiles = 0;

            foreach (string filePath in Directory.EnumerateFiles(addonDirectoryPath, "*.*", SearchOption.AllDirectories))
            {
                string extension = Path.GetExtension(filePath);
                if (!_textExtensions.Contains(extension))
                    continue;

                if (TryUpdateFile(filePath, addonDirectoryPath, forceReplace))
                    updatedFiles++;
            }

            return updatedFiles;
        }

        private static bool TryUpdateFile(string filePath, string rootDirectory, bool forceReplace)
        {
            byte[] data = File.ReadAllBytes(filePath);
            Encoding encoding = DetectEncoding(data, out int bomLength);
            string text = encoding.GetString(data, bomLength, data.Length - bomLength);

            string updated = _audioRefRegex.Replace(text, match =>
            {
                string pathValue = match.Groups["path"].Value;
                if (string.IsNullOrWhiteSpace(pathValue))
                    return match.Value;

                if (forceReplace)
                    return pathValue + ".ogg";

                string normalized = pathValue.Replace('/', Path.DirectorySeparatorChar).Replace('\\', Path.DirectorySeparatorChar);
                normalized = normalized.TrimStart(Path.DirectorySeparatorChar);

                if (OggExists(rootDirectory, normalized))
                    return pathValue + ".ogg";

                return match.Value;
            });

            if (updated == text)
                return false;

            byte[] preamble = bomLength > 0 ? encoding.GetPreamble() : Array.Empty<byte>();
            byte[] body = encoding.GetBytes(updated);

            using (var stream = new FileStream(filePath, FileMode.Create, FileAccess.Write, FileShare.None))
            {
                if (preamble.Length > 0)
                    stream.Write(preamble, 0, preamble.Length);
                stream.Write(body, 0, body.Length);
            }

            return true;
        }

        private static bool OggExists(string rootDirectory, string relativeNoExt)
        {
            string relativeOgg = relativeNoExt + ".ogg";

            if (Path.IsPathRooted(relativeOgg))
                return File.Exists(relativeOgg);

            string direct = Path.Combine(rootDirectory, relativeOgg);
            if (File.Exists(direct))
                return true;

            string underSound = Path.Combine(rootDirectory, "sound", relativeOgg);
            if (File.Exists(underSound))
                return true;

            return false;
        }

        private static Encoding DetectEncoding(byte[] data, out int bomLength)
        {
            if (data.Length >= 3 && data[0] == 0xEF && data[1] == 0xBB && data[2] == 0xBF)
            {
                bomLength = 3;
                return new UTF8Encoding(true);
            }

            if (data.Length >= 2 && data[0] == 0xFF && data[1] == 0xFE)
            {
                bomLength = 2;
                return Encoding.Unicode;
            }

            if (data.Length >= 2 && data[0] == 0xFE && data[1] == 0xFF)
            {
                bomLength = 2;
                return Encoding.BigEndianUnicode;
            }

            if (data.Length >= 4 && data[0] == 0x00 && data[1] == 0x00 && data[2] == 0xFE && data[3] == 0xFF)
            {
                bomLength = 4;
                return new UTF32Encoding(true, true);
            }

            if (data.Length >= 4 && data[0] == 0xFF && data[1] == 0xFE && data[2] == 0x00 && data[3] == 0x00)
            {
                bomLength = 4;
                return new UTF32Encoding(false, true);
            }

            bomLength = 0;
            return Encoding.Default;
        }
    }
}
