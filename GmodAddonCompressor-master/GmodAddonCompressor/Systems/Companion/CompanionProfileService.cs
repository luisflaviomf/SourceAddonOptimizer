using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;

namespace GmodAddonCompressor.Systems.Companion
{
    internal static class CompanionProfileService
    {
        private const int MaxInventoryFiles = 250_000;
        private static readonly TimeSpan StaleAfter = TimeSpan.FromDays(180);

        public static CompanionProfileImportResult Import(
            IEnumerable<string> indexPaths,
            string addonRoot,
            DateTimeOffset? now = null,
            CancellationToken cancellationToken = default)
        {
            DateTimeOffset clock = now ?? DateTimeOffset.UtcNow;
            string[] origins = indexPaths
                .Where(path => !string.IsNullOrWhiteSpace(path))
                .Select(Path.GetFullPath)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .ToArray();
            if (origins.Length == 0)
                throw new InvalidOperationException("Select at least one runtime profile index.");
            string fullAddonRoot = Path.GetFullPath(addonRoot ?? string.Empty);
            if (!Directory.Exists(fullAddonRoot))
                throw new InvalidOperationException("The selected addon folder does not exist.");

            var profiles = new List<CompanionRuntimeProfile>(origins.Length);
            foreach (string origin in origins)
            {
                cancellationToken.ThrowIfCancellationRequested();
                profiles.Add(CompanionBundleReader.ReadRuntimeProfile(origin, clock));
            }

            CompanionRuntimeProfile merged = Merge(profiles);
            Dictionary<string, string> inventory = BuildAddonInventory(fullAddonRoot, cancellationToken);
            var matchedModels = new Dictionary<string, CompanionObservedModel>(StringComparer.Ordinal);
            var matchedMaterials = new Dictionary<string, CompanionObservedMaterial>(StringComparer.Ordinal);
            var observedMaterialPaths = new HashSet<string>(merged.Materials.Keys, StringComparer.Ordinal);
            foreach (CompanionObservedModel model in merged.Models.Values)
            {
                observedMaterialPaths.UnionWith(model.Materials);
                foreach (string key in model.Submaterials.Keys)
                    observedMaterialPaths.Add(key[(key.IndexOf('=') + 1)..]);
            }

            foreach ((string path, CompanionObservedModel model) in merged.Models)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (!inventory.TryGetValue(path, out string? displayPath))
                    continue;
                var copy = new CompanionObservedModel(path, displayPath, model.Evidence);
                copy.Skins.UnionWith(model.Skins);
                foreach ((string key, CompanionEvidenceTier evidence) in model.Bodygroups)
                    copy.Bodygroups[key] = evidence;
                foreach ((string key, CompanionEvidenceTier evidence) in model.Submaterials)
                {
                    string materialPath = key[(key.IndexOf('=') + 1)..];
                    if (inventory.ContainsKey(materialPath))
                        copy.Submaterials[key] = evidence;
                }
                foreach (string materialPath in model.Materials)
                {
                    if (inventory.ContainsKey(materialPath))
                        copy.Materials.Add(materialPath);
                }
                matchedModels.Add(path, copy);
            }

            foreach (string path in observedMaterialPaths)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (!inventory.TryGetValue(path, out string? displayPath))
                    continue;
                if (merged.Materials.TryGetValue(path, out CompanionObservedMaterial? observed))
                    matchedMaterials[path] = new CompanionObservedMaterial(path, displayPath, observed.Evidence);
                else
                {
                    CompanionEvidenceTier evidence = merged.Models.Values
                        .Where(model => model.Materials.Contains(path) || model.Submaterials.Keys.Any(key => key.EndsWith("=" + path, StringComparison.Ordinal)))
                        .Select(model => model.Evidence)
                        .DefaultIfEmpty(CompanionEvidenceTier.Registered)
                        .Max();
                    matchedMaterials[path] = new CompanionObservedMaterial(path, displayPath, evidence);
                }
            }

            int observedModels = merged.Models.Count;
            int observedMaterials = observedMaterialPaths.Count;
            int ignored = (observedModels - matchedModels.Count) + (observedMaterials - matchedMaterials.Count);
            bool stale = profiles.Any(profile => clock.ToUniversalTime() - profile.CreatedUtc > StaleAfter);
            int matchedAssets = matchedModels.Count + matchedMaterials.Count;
            bool eligible = !stale && matchedAssets > 0;
            DateTimeOffset newest = profiles.Max(profile => profile.CreatedUtc);
            DateTimeOffset oldest = profiles.Min(profile => profile.CreatedUtc);
            var intersected = new CompanionRuntimeProfile(
                string.Join(";", origins),
                profiles.Count == 1 ? profiles[0].RunId : Guid.Empty,
                newest,
                profiles.Count == 1 ? profiles[0].ProducerName : "Merged Companion profiles",
                profiles.Count == 1 ? profiles[0].ProducerVersion : CompanionContract.ContractMajor + "." + CompanionContract.ContractMinor,
                matchedModels,
                matchedMaterials,
                merged.TruncatedModels,
                merged.TruncatedMaterials,
                merged.TruncatedLuaErrors,
                merged.TruncatedBytes
            );
            string originText = profiles.Count == 1
                ? $"1 profile from {profiles[0].ProducerName} {profiles[0].ProducerVersion}"
                : $"{profiles.Count} profiles merged (maximum evidence + set union)";
            string ageText = profiles.Count == 1
                ? FormatAge(clock - newest)
                : $"Newest {FormatAge(clock - newest)}; oldest {FormatAge(clock - oldest)}";
            long totalTruncations = merged.TruncatedModels + merged.TruncatedMaterials + merged.TruncatedLuaErrors + merged.TruncatedBytes;
            string truncationText = totalTruncations == 0
                ? "No producer truncation reported"
                : $"Truncated: models {merged.TruncatedModels}, materials {merged.TruncatedMaterials}, Lua errors {merged.TruncatedLuaErrors}, bytes {merged.TruncatedBytes}";
            string compatibility = stale
                ? "Valid contract, but stale (>180 days); Maximum v2 opt-in is unavailable"
                : matchedAssets == 0
                    ? "Valid contract, but no observed assets match the selected addon"
                    : "Compatible runtime profile v1.0";
            return new CompanionProfileImportResult(
                intersected,
                profiles.Count,
                observedModels,
                observedMaterials,
                matchedModels.Count,
                matchedMaterials.Count,
                Math.Max(0, ignored),
                stale,
                eligible,
                originText,
                ageText,
                truncationText,
                compatibility
            );
        }

        private static CompanionRuntimeProfile Merge(IReadOnlyList<CompanionRuntimeProfile> profiles)
        {
            var models = new Dictionary<string, CompanionObservedModel>(StringComparer.Ordinal);
            var materials = new Dictionary<string, CompanionObservedMaterial>(StringComparer.Ordinal);
            long truncatedModels = 0;
            long truncatedMaterials = 0;
            long truncatedLuaErrors = 0;
            long truncatedBytes = 0;
            foreach (CompanionRuntimeProfile profile in profiles)
            {
                foreach ((string path, CompanionObservedModel source) in profile.Models)
                {
                    if (!models.TryGetValue(path, out CompanionObservedModel? destination))
                    {
                        destination = new CompanionObservedModel(path, source.DisplayPath, source.Evidence);
                        models.Add(path, destination);
                    }
                    destination.Evidence = Max(destination.Evidence, source.Evidence);
                    destination.Skins.UnionWith(source.Skins);
                    MergeEvidence(destination.Bodygroups, source.Bodygroups);
                    MergeEvidence(destination.Submaterials, source.Submaterials);
                    destination.Materials.UnionWith(source.Materials);
                }
                foreach ((string path, CompanionObservedMaterial source) in profile.Materials)
                {
                    if (!materials.TryGetValue(path, out CompanionObservedMaterial? destination))
                        materials.Add(path, new CompanionObservedMaterial(path, source.DisplayPath, source.Evidence));
                    else
                        destination.Evidence = Max(destination.Evidence, source.Evidence);
                }
                truncatedModels = checked(truncatedModels + profile.TruncatedModels);
                truncatedMaterials = checked(truncatedMaterials + profile.TruncatedMaterials);
                truncatedLuaErrors = checked(truncatedLuaErrors + profile.TruncatedLuaErrors);
                truncatedBytes = checked(truncatedBytes + profile.TruncatedBytes);
            }
            CompanionRuntimeProfile newest = profiles.OrderByDescending(profile => profile.CreatedUtc).First();
            return new CompanionRuntimeProfile(
                string.Join(";", profiles.Select(profile => profile.OriginPath)),
                profiles.Count == 1 ? newest.RunId : Guid.Empty,
                newest.CreatedUtc,
                newest.ProducerName,
                newest.ProducerVersion,
                models,
                materials,
                truncatedModels,
                truncatedMaterials,
                truncatedLuaErrors,
                truncatedBytes
            );
        }

        private static Dictionary<string, string> BuildAddonInventory(string addonRoot, CancellationToken cancellationToken)
        {
            var inventory = new Dictionary<string, string>(StringComparer.Ordinal);
            var directories = new Stack<string>();
            directories.Push(addonRoot);
            int fileCount = 0;
            while (directories.Count > 0)
            {
                cancellationToken.ThrowIfCancellationRequested();
                string current = directories.Pop();
                foreach (string directory in Directory.EnumerateDirectories(current))
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    if ((File.GetAttributes(directory) & FileAttributes.ReparsePoint) == 0)
                        directories.Push(directory);
                }
                foreach (string file in Directory.EnumerateFiles(current))
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    fileCount++;
                    if (fileCount > MaxInventoryFiles)
                        throw new InvalidOperationException("Selected addon exceeds the bounded inventory file limit.");
                    string relative = Path.GetRelativePath(addonRoot, file).Replace('\\', '/');
                    if (!(relative.StartsWith("models/", StringComparison.OrdinalIgnoreCase) && relative.EndsWith(".mdl", StringComparison.OrdinalIgnoreCase))
                        && !(relative.StartsWith("materials/", StringComparison.OrdinalIgnoreCase) && relative.EndsWith(".vmt", StringComparison.OrdinalIgnoreCase)))
                        continue;
                    try
                    {
                        string canonical = CompanionContract.NormalizeVirtualPath(relative);
                        inventory.TryAdd(canonical, relative);
                    }
                    catch (InvalidOperationException)
                    {
                        // v1 intentionally ignores non-ASCII or otherwise unsafe filesystem names.
                    }
                }
            }
            return inventory;
        }

        private static void MergeEvidence(
            Dictionary<string, CompanionEvidenceTier> destination,
            Dictionary<string, CompanionEvidenceTier> source)
        {
            foreach ((string key, CompanionEvidenceTier evidence) in source)
            {
                if (!destination.TryGetValue(key, out CompanionEvidenceTier current) || evidence > current)
                    destination[key] = evidence;
            }
        }

        private static CompanionEvidenceTier Max(CompanionEvidenceTier left, CompanionEvidenceTier right) => left >= right ? left : right;

        private static string FormatAge(TimeSpan age)
        {
            if (age < TimeSpan.Zero)
                age = TimeSpan.Zero;
            if (age.TotalDays >= 2)
                return $"{Math.Floor(age.TotalDays):0} days old";
            if (age.TotalHours >= 2)
                return $"{Math.Floor(age.TotalHours):0} hours old";
            return $"{Math.Max(0, Math.Floor(age.TotalMinutes)):0} minutes old";
        }
    }
}
