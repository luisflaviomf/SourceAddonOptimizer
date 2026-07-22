using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace GmodAddonCompressor.Systems.Tools
{
    internal sealed class SourceAddonOptimizerPackageManifest
    {
        internal const string RelativePath = "_internal/maximum_optimizer/native/tool-package-manifest.json";
        internal const string NativeDllRelativePath = "_internal/maximum_optimizer/native/bin/win-x64/meshopt_bridge.dll";

        [JsonPropertyName("schemaVersion")]
        public int SchemaVersion { get; set; }

        [JsonPropertyName("toolName")]
        public string ToolName { get; set; } = string.Empty;

        [JsonPropertyName("toolVersion")]
        public string ToolVersion { get; set; } = string.Empty;

        [JsonPropertyName("silhouette")]
        public SourceAddonOptimizerSilhouetteContract Silhouette { get; set; } = new();

        [JsonPropertyName("files")]
        public List<SourceAddonOptimizerPackageFile> Files { get; set; } = new();

        internal static SourceAddonOptimizerPackageManifest Parse(byte[] utf8)
        {
            try
            {
                return JsonSerializer.Deserialize<SourceAddonOptimizerPackageManifest>(utf8)
                    ?? throw new InvalidOperationException("Tool package manifest is empty.");
            }
            catch (JsonException ex)
            {
                throw new InvalidOperationException("Tool package manifest is invalid JSON.", ex);
            }
        }
    }

    internal sealed class SourceAddonOptimizerSilhouetteContract
    {
        [JsonPropertyName("apiVersion")]
        public string ApiVersion { get; set; } = string.Empty;

        [JsonPropertyName("buildId")]
        public string BuildId { get; set; } = string.Empty;

        [JsonPropertyName("architecture")]
        public string Architecture { get; set; } = string.Empty;

        [JsonPropertyName("dllPath")]
        public string DllPath { get; set; } = string.Empty;

        [JsonPropertyName("sha256")]
        public string Sha256 { get; set; } = string.Empty;

        [JsonPropertyName("size")]
        public long Size { get; set; }

        [JsonPropertyName("minimumWorkerContract")]
        public string MinimumWorkerContract { get; set; } = string.Empty;

        [JsonPropertyName("minimumWpfContract")]
        public string MinimumWpfContract { get; set; } = string.Empty;
    }

    internal sealed class SourceAddonOptimizerPackageFile
    {
        [JsonPropertyName("path")]
        public string Path { get; set; } = string.Empty;

        [JsonPropertyName("size")]
        public long Size { get; set; }

        [JsonPropertyName("sha256")]
        public string Sha256 { get; set; } = string.Empty;
    }
}
