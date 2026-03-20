using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems
{
    internal sealed class AudioWebSafetyReport
    {
        public AudioWebSafetyReport(HashSet<string> protectedWavPaths)
        {
            ProtectedWavPaths = protectedWavPaths;
        }

        public HashSet<string> ProtectedWavPaths { get; }

        public bool IsProtectedWebAudioWav(string fullPath)
        {
            return ProtectedWavPaths.Contains(Path.GetFullPath(fullPath));
        }
    }

    internal static class AudioWebSafetyClassifier
    {
        private static readonly Regex WavPathRegex = new Regex(
            "\"(?<path>[^\"]+?\\.wav)\"",
            RegexOptions.Compiled | RegexOptions.IgnoreCase);

        internal static AudioWebSafetyReport Analyze(string addonRoot)
        {
            if (string.IsNullOrWhiteSpace(addonRoot) || !Directory.Exists(addonRoot))
                return new AudioWebSafetyReport(new HashSet<string>(StringComparer.OrdinalIgnoreCase));

            string fullRoot = Path.GetFullPath(addonRoot);
            var protectedWavPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

            AddProtectedTree(fullRoot, protectedWavPaths, Path.Combine("sound", "glide", "streams"));
            AddProtectedTree(fullRoot, protectedWavPaths, Path.Combine("sound", "glide_experiments"));

            string presetRoot = Path.Combine(fullRoot, "data_static", "glide", "stream_presets");
            if (!Directory.Exists(presetRoot))
                return new AudioWebSafetyReport(protectedWavPaths);

            foreach (string presetPath in Directory.EnumerateFiles(presetRoot, "*.json", SearchOption.AllDirectories))
            {
                string content;
                try
                {
                    content = File.ReadAllText(presetPath);
                }
                catch
                {
                    continue;
                }

                foreach (Match match in WavPathRegex.Matches(content))
                {
                    string rawPath = match.Groups["path"].Value;
                    if (string.IsNullOrWhiteSpace(rawPath))
                        continue;

                    string normalized = rawPath.Trim().Replace('\\', '/').TrimStart('/');
                    string relativePath = normalized.Replace('/', Path.DirectorySeparatorChar);

                    string directCandidate = Path.Combine(fullRoot, relativePath);
                    if (File.Exists(directCandidate))
                    {
                        protectedWavPaths.Add(Path.GetFullPath(directCandidate));
                        continue;
                    }

                    string soundCandidate = Path.Combine(fullRoot, "sound", relativePath);
                    if (File.Exists(soundCandidate))
                        protectedWavPaths.Add(Path.GetFullPath(soundCandidate));
                }
            }

            return new AudioWebSafetyReport(protectedWavPaths);
        }

        private static void AddProtectedTree(string fullRoot, HashSet<string> protectedWavPaths, string relativeDirectory)
        {
            string directoryPath = Path.Combine(fullRoot, relativeDirectory);
            if (!Directory.Exists(directoryPath))
                return;

            foreach (string wavPath in Directory.EnumerateFiles(directoryPath, "*.wav", SearchOption.AllDirectories))
                protectedWavPaths.Add(Path.GetFullPath(wavPath));
        }
    }
}
