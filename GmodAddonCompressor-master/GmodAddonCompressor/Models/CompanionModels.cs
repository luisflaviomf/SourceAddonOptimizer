using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace GmodAddonCompressor.Models
{
    internal sealed class CompanionPackageManifest
    {
        [JsonPropertyName("schemaVersion")]
        public int SchemaVersion { get; set; }

        [JsonPropertyName("packageVersion")]
        public string PackageVersion { get; set; } = string.Empty;

        [JsonPropertyName("addonDirectory")]
        public string AddonDirectory { get; set; } = string.Empty;

        [JsonPropertyName("files")]
        public List<CompanionPackageFile> Files { get; set; } = new();
    }

    internal sealed class CompanionPackageFile
    {
        [JsonPropertyName("path")]
        public string Path { get; set; } = string.Empty;

        [JsonPropertyName("bytes")]
        public long Bytes { get; set; }

        [JsonPropertyName("sha256")]
        public string Sha256 { get; set; } = string.Empty;
    }

    internal sealed class CompanionOwnershipMarker
    {
        [JsonPropertyName("schemaVersion")]
        public int SchemaVersion { get; set; }

        [JsonPropertyName("packageVersion")]
        public string PackageVersion { get; set; } = string.Empty;

        [JsonPropertyName("packageSha256")]
        public string PackageSha256 { get; set; } = string.Empty;

        [JsonPropertyName("files")]
        public List<CompanionPackageFile> Files { get; set; } = new();
    }

    internal enum CompanionInstallDisposition
    {
        Installed,
        Updated,
        AlreadyInstalled
    }

    internal sealed class CompanionInstallResult
    {
        internal CompanionInstallResult(
            string destination,
            string packageVersion,
            string packageSha256,
            CompanionInstallDisposition disposition)
        {
            Destination = destination;
            PackageVersion = packageVersion;
            PackageSha256 = packageSha256;
            Disposition = disposition;
        }

        public string Destination { get; }
        public string PackageVersion { get; }
        public string PackageSha256 { get; }
        public CompanionInstallDisposition Disposition { get; }
    }

    internal sealed class CompanionUninstallResult
    {
        internal CompanionUninstallResult(
            string destination,
            bool addonDirectoryRemoved,
            IReadOnlyList<string> preservedUnknownPaths)
        {
            Destination = destination;
            AddonDirectoryRemoved = addonDirectoryRemoved;
            PreservedUnknownPaths = preservedUnknownPaths;
            Warning = preservedUnknownPaths.Count == 0
                ? string.Empty
                : "Unknown files were preserved: " + string.Join(", ", preservedUnknownPaths);
        }

        public string Destination { get; }
        public bool AddonDirectoryRemoved { get; }
        public IReadOnlyList<string> PreservedUnknownPaths { get; }
        public string Warning { get; }
    }
}
