using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace GmodAddonCompressor.Systems.Companion
{
    internal static class CompanionPathPolicy
    {
        internal const string AddonDirectoryName = "sourceaddonoptimizer_companion";

        internal static string? DetectGarrysModRoot(string? explicitPath = null)
        {
            if (TryNormalizeGameRoot(explicitPath, out string explicitRoot))
                return explicitRoot;

            foreach (string steamRoot in SteamRoots())
            {
                foreach (string library in SteamLibraries(steamRoot).Prepend(steamRoot))
                {
                    string candidate = Path.Combine(library, "steamapps", "common", "GarrysMod");
                    if (TryNormalizeGameRoot(candidate, out string detected))
                        return detected;
                }
            }

            return null;
        }

        internal static string ResolveAddonDestination(string garrysModRoot)
        {
            if (!TryNormalizeGameRoot(garrysModRoot, out string normalizedRoot))
                throw new InvalidOperationException("The selected Garry's Mod root is invalid.");

            AssertNotReparsePoint(normalizedRoot, "Garry's Mod root");
            string garrysmod = Path.Combine(normalizedRoot, "garrysmod");
            AssertNotReparsePoint(garrysmod, "garrysmod directory");
            string addons = Path.Combine(garrysmod, "addons");
            if (Directory.Exists(addons))
                AssertNotReparsePoint(addons, "addons directory");

            string fullAddons = Path.GetFullPath(addons).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string destination = Path.GetFullPath(Path.Combine(fullAddons, AddonDirectoryName));
            string containment = fullAddons + Path.DirectorySeparatorChar;
            if (!destination.StartsWith(containment, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("The Companion destination escapes the Garry's Mod addons directory.");
            if (Directory.Exists(destination) || File.Exists(destination))
                AssertNotReparsePoint(destination, "Companion destination");

            return destination;
        }

        internal static void AssertNotReparsePoint(string path, string label)
        {
            if (!Directory.Exists(path) && !File.Exists(path))
                return;
            FileAttributes attributes = File.GetAttributes(path);
            if ((attributes & FileAttributes.ReparsePoint) != 0)
                throw new InvalidOperationException($"Refusing {label} because it is a reparse point.");
        }

        private static bool TryNormalizeGameRoot(string? value, out string normalized)
        {
            normalized = string.Empty;
            if (string.IsNullOrWhiteSpace(value))
                return false;

            try
            {
                string candidate = Path.GetFullPath(Environment.ExpandEnvironmentVariables(value.Trim().Trim('"')));
                if (File.Exists(candidate) && Path.GetFileName(candidate).Equals("gmod.exe", StringComparison.OrdinalIgnoreCase))
                    candidate = Path.GetDirectoryName(candidate) ?? candidate;
                if (Path.GetFileName(candidate.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar))
                        .Equals("garrysmod", StringComparison.OrdinalIgnoreCase))
                {
                    string? parent = Directory.GetParent(candidate)?.FullName;
                    if (parent != null && File.Exists(Path.Combine(parent, "gmod.exe")))
                        candidate = parent;
                }

                if (!File.Exists(Path.Combine(candidate, "gmod.exe"))
                    || !Directory.Exists(Path.Combine(candidate, "garrysmod")))
                    return false;
                normalized = Path.GetFullPath(candidate).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
                return true;
            }
            catch (Exception ex) when (ex is ArgumentException || ex is IOException || ex is UnauthorizedAccessException || ex is NotSupportedException)
            {
                return false;
            }
        }

        private static IEnumerable<string> SteamRoots()
        {
            var candidates = new List<string>();
            try
            {
                if (OperatingSystem.IsWindows())
                {
                    using RegistryKey? key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam");
                    if (key?.GetValue("SteamPath") is string steamPath)
                        candidates.Add(steamPath);
                }
            }
            catch (Exception ex) when (ex is UnauthorizedAccessException || ex is IOException)
            {
                // Registry discovery is optional; explicit selection remains available.
            }

            candidates.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Steam"));
            candidates.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Steam"));
            return candidates
                .Where(path => !string.IsNullOrWhiteSpace(path) && Directory.Exists(path))
                .Select(Path.GetFullPath)
                .Distinct(StringComparer.OrdinalIgnoreCase);
        }

        private static IEnumerable<string> SteamLibraries(string steamRoot)
        {
            string file = Path.Combine(steamRoot, "steamapps", "libraryfolders.vdf");
            if (!File.Exists(file))
                yield break;
            string raw;
            try
            {
                raw = File.ReadAllText(file);
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                yield break;
            }

            foreach (Match match in Regex.Matches(raw, "\\\"path\\\"\\s*\\\"(?<path>[^\\\"]+)\\\"", RegexOptions.CultureInvariant))
            {
                string candidate = match.Groups["path"].Value.Replace("\\\\", "\\");
                if (Directory.Exists(candidate))
                    yield return Path.GetFullPath(candidate);
            }
        }
    }
}
