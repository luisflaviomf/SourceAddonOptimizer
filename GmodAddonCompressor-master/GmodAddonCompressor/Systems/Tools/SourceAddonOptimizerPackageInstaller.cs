using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Threading;

namespace GmodAddonCompressor.Systems.Tools
{
    internal static class SourceAddonOptimizerPackageInstaller
    {
        internal static string Install(
            byte[] zipBytes,
            string toolsRoot,
            string toolName,
            string toolVersion,
            TimeSpan? lockTimeout = null)
        {
            if (zipBytes == null || zipBytes.Length == 0)
                throw new InvalidOperationException("Embedded optimizer tools package is empty.");
            string packageHash = Convert.ToHexString(SHA256.HashData(zipBytes)).ToLowerInvariant();
            ValidateZip(zipBytes, toolName, toolVersion);

            string versionRoot = Path.Combine(Path.GetFullPath(toolsRoot), toolName, toolVersion);
            string finalRoot = Path.Combine(versionRoot, packageHash);
            string lockPath = Path.Combine(Path.GetFullPath(toolsRoot), toolName, "extract.lock");
            Directory.CreateDirectory(versionRoot);
            using FileStream extractionLock = AcquireLock(lockPath, lockTimeout ?? TimeSpan.FromSeconds(30));
            CleanupPartialDirectories(versionRoot);

            if (ValidateDirectory(finalRoot, toolName, toolVersion, throwOnFailure: false))
                return finalRoot;

            if (Directory.Exists(finalRoot))
            {
                string quarantine = Path.Combine(
                    versionRoot,
                    $".{packageHash}.{Environment.ProcessId}.{Guid.NewGuid():N}.invalid"
                );
                Directory.Move(finalRoot, quarantine);
                TryDeleteDirectory(quarantine);
            }

            string partialRoot = Path.Combine(
                versionRoot,
                $".{packageHash}.{Environment.ProcessId}.{Guid.NewGuid():N}.partial"
            );
            try
            {
                Directory.CreateDirectory(partialRoot);
                Extract(zipBytes, partialRoot);
                ValidateDirectory(partialRoot, toolName, toolVersion, throwOnFailure: true);

                if (Directory.Exists(finalRoot))
                {
                    if (!ValidateDirectory(finalRoot, toolName, toolVersion, throwOnFailure: false))
                        throw new InvalidOperationException($"Concurrent optimizer extraction produced an invalid root: {finalRoot}");
                    TryDeleteDirectory(partialRoot);
                    return finalRoot;
                }

                Directory.Move(partialRoot, finalRoot);
                if (!ValidateDirectory(finalRoot, toolName, toolVersion, throwOnFailure: false))
                    throw new InvalidOperationException($"Atomically installed optimizer tools failed final validation: {finalRoot}");
                return finalRoot;
            }
            catch
            {
                TryDeleteDirectory(partialRoot);
                throw;
            }
        }

        private static FileStream AcquireLock(string lockPath, TimeSpan timeout)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(lockPath) ?? ".");
            DateTime deadline = DateTime.UtcNow + timeout;
            while (true)
            {
                try
                {
                    return new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
                }
                catch (Exception ex) when ((ex is IOException || ex is UnauthorizedAccessException) && DateTime.UtcNow < deadline)
                {
                    Thread.Sleep(100);
                }
            }
        }

        private static void ValidateZip(byte[] zipBytes, string toolName, string toolVersion)
        {
            using var memory = new MemoryStream(zipBytes, writable: false);
            using var archive = new ZipArchive(memory, ZipArchiveMode.Read, leaveOpen: false);
            var entries = new Dictionary<string, ZipArchiveEntry>(StringComparer.OrdinalIgnoreCase);
            foreach (ZipArchiveEntry entry in archive.Entries.Where(item => !string.IsNullOrEmpty(item.Name)))
            {
                string normalized = NormalizeRelativePath(entry.FullName);
                if (!entries.TryAdd(normalized, entry))
                    throw new InvalidOperationException($"Optimizer tools ZIP contains duplicate path: {normalized}");
            }
            if (!entries.TryGetValue(SourceAddonOptimizerPackageManifest.RelativePath, out ZipArchiveEntry? manifestEntry))
                throw new InvalidOperationException("Optimizer tools ZIP is missing its package manifest.");
            byte[] manifestBytes = ReadAll(manifestEntry);
            SourceAddonOptimizerPackageManifest manifest = SourceAddonOptimizerPackageManifest.Parse(manifestBytes);
            Dictionary<string, SourceAddonOptimizerPackageFile> declared = ValidateManifest(manifest, toolName, toolVersion);
            string[] actual = entries.Keys
                .Where(path => !path.Equals(SourceAddonOptimizerPackageManifest.RelativePath, StringComparison.OrdinalIgnoreCase))
                .OrderBy(path => path, StringComparer.Ordinal)
                .ToArray();
            if (!actual.SequenceEqual(declared.Keys.OrderBy(path => path, StringComparer.Ordinal), StringComparer.Ordinal))
                throw new InvalidOperationException("Optimizer tools ZIP contents differ from its package manifest.");
            foreach ((string relative, SourceAddonOptimizerPackageFile expected) in declared)
            {
                (string hash, long size) = Hash(entries[relative].Open());
                if (size != expected.Size || !hash.Equals(expected.Sha256, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException($"Optimizer tools ZIP hash/size mismatch: {relative}");
            }
            AssertAmd64(ReadAll(entries[SourceAddonOptimizerPackageManifest.NativeDllRelativePath]), "embedded native DLL");
        }

        private static bool ValidateDirectory(
            string root,
            string toolName,
            string toolVersion,
            bool throwOnFailure)
        {
            try
            {
                if (!Directory.Exists(root))
                    return false;
                string manifestPath = ResolveSafePath(root, SourceAddonOptimizerPackageManifest.RelativePath);
                if (!File.Exists(manifestPath))
                    return false;
                SourceAddonOptimizerPackageManifest manifest = SourceAddonOptimizerPackageManifest.Parse(File.ReadAllBytes(manifestPath));
                Dictionary<string, SourceAddonOptimizerPackageFile> declared = ValidateManifest(manifest, toolName, toolVersion);
                string[] actual = Directory.GetFiles(root, "*", SearchOption.AllDirectories)
                    .Select(path => Path.GetRelativePath(root, path).Replace('\\', '/'))
                    .Where(path => !path.Equals(SourceAddonOptimizerPackageManifest.RelativePath, StringComparison.OrdinalIgnoreCase))
                    .OrderBy(path => path, StringComparer.Ordinal)
                    .ToArray();
                if (!actual.SequenceEqual(declared.Keys.OrderBy(path => path, StringComparer.Ordinal), StringComparer.Ordinal))
                    throw new InvalidOperationException("Installed optimizer tools differ from their package manifest.");
                foreach ((string relative, SourceAddonOptimizerPackageFile expected) in declared)
                {
                    string path = ResolveSafePath(root, relative);
                    using FileStream stream = File.OpenRead(path);
                    (string hash, long size) = Hash(stream);
                    if (size != expected.Size || !hash.Equals(expected.Sha256, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidOperationException($"Installed optimizer tool hash/size mismatch: {relative}");
                }
                AssertAmd64(
                    File.ReadAllBytes(ResolveSafePath(root, SourceAddonOptimizerPackageManifest.NativeDllRelativePath)),
                    "installed native DLL"
                );
                return true;
            }
            catch when (!throwOnFailure)
            {
                return false;
            }
        }

        private static Dictionary<string, SourceAddonOptimizerPackageFile> ValidateManifest(
            SourceAddonOptimizerPackageManifest manifest,
            string toolName,
            string toolVersion)
        {
            if (manifest.SchemaVersion != 1
                || !manifest.ToolName.Equals(toolName, StringComparison.Ordinal)
                || !manifest.ToolVersion.Equals(toolVersion, StringComparison.Ordinal))
                throw new InvalidOperationException("Optimizer tool package contract/version mismatch.");
            var declared = new Dictionary<string, SourceAddonOptimizerPackageFile>(StringComparer.OrdinalIgnoreCase);
            string? previous = null;
            foreach (SourceAddonOptimizerPackageFile file in manifest.Files)
            {
                string path = NormalizeRelativePath(file.Path);
                if (previous != null && StringComparer.Ordinal.Compare(previous, path) >= 0)
                    throw new InvalidOperationException("Optimizer tool package files are not uniquely ordinal-sorted.");
                previous = path;
                if (file.Size < 0 || !IsSha256(file.Sha256) || !declared.TryAdd(path, file))
                    throw new InvalidOperationException($"Invalid optimizer tool package file declaration: {path}");
            }
            SourceAddonOptimizerSilhouetteContract silhouette = manifest.Silhouette;
            if (!silhouette.ApiVersion.Equals("1.0.0", StringComparison.Ordinal)
                || !silhouette.BuildId.Equals("maximum-silhouette-raw-v1-20260722", StringComparison.Ordinal)
                || !silhouette.Architecture.Equals("x64", StringComparison.Ordinal)
                || !NormalizeRelativePath(silhouette.DllPath).Equals(SourceAddonOptimizerPackageManifest.NativeDllRelativePath, StringComparison.Ordinal)
                || !silhouette.MinimumWorkerContract.Equals(toolVersion, StringComparison.Ordinal)
                || !silhouette.MinimumWpfContract.Equals(toolVersion, StringComparison.Ordinal)
                || !declared.TryGetValue(SourceAddonOptimizerPackageManifest.NativeDllRelativePath, out SourceAddonOptimizerPackageFile? dll)
                || dll.Size != silhouette.Size
                || !dll.Sha256.Equals(silhouette.Sha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Optimizer silhouette package contract mismatch.");
            return declared;
        }

        private static void Extract(byte[] zipBytes, string destination)
        {
            using var memory = new MemoryStream(zipBytes, writable: false);
            using var archive = new ZipArchive(memory, ZipArchiveMode.Read, leaveOpen: false);
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string normalized = NormalizeRelativePath(entry.FullName);
                string path = ResolveSafePath(destination, normalized);
                if (string.IsNullOrEmpty(entry.Name))
                {
                    Directory.CreateDirectory(path);
                    continue;
                }
                Directory.CreateDirectory(Path.GetDirectoryName(path) ?? destination);
                using Stream input = entry.Open();
                using FileStream output = new(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                input.CopyTo(output);
                output.Flush(flushToDisk: true);
            }
        }

        private static string NormalizeRelativePath(string raw)
        {
            string normalized = (raw ?? string.Empty).Replace('\\', '/').TrimEnd('/');
            string[] parts = normalized.Split('/');
            if (string.IsNullOrWhiteSpace(normalized)
                || normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.Contains(':')
                || parts.Any(part => string.IsNullOrEmpty(part) || part == "." || part == ".."))
                throw new InvalidOperationException($"Unsafe optimizer package path: {raw}");
            return string.Join('/', parts);
        }

        private static string ResolveSafePath(string root, string relative)
        {
            string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            string fullPath = Path.GetFullPath(Path.Combine(fullRoot, NormalizeRelativePath(relative).Replace('/', Path.DirectorySeparatorChar)));
            if (!fullPath.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException($"Optimizer package path escapes extraction root: {relative}");
            return fullPath;
        }

        private static (string Hash, long Size) Hash(Stream stream)
        {
            using (stream)
            using (SHA256 sha256 = SHA256.Create())
            {
                byte[] buffer = new byte[1024 * 1024];
                long size = 0;
                int read;
                while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    sha256.TransformBlock(buffer, 0, read, null, 0);
                    size += read;
                }
                sha256.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                return (Convert.ToHexString(sha256.Hash!).ToLowerInvariant(), size);
            }
        }

        private static byte[] ReadAll(ZipArchiveEntry entry)
        {
            using Stream input = entry.Open();
            using var output = new MemoryStream();
            input.CopyTo(output);
            return output.ToArray();
        }

        private static bool IsSha256(string value) =>
            value != null && value.Length == 64 && value.All(character => Uri.IsHexDigit(character));

        private static void AssertAmd64(byte[] bytes, string label)
        {
            if (bytes.Length < 64 || bytes[0] != (byte)'M' || bytes[1] != (byte)'Z')
                throw new InvalidOperationException($"{label} is not a PE file.");
            int peOffset = BitConverter.ToInt32(bytes, 0x3c);
            if (peOffset < 0 || peOffset + 6 > bytes.Length
                || bytes[peOffset] != (byte)'P' || bytes[peOffset + 1] != (byte)'E'
                || bytes[peOffset + 2] != 0 || bytes[peOffset + 3] != 0)
                throw new InvalidOperationException($"{label} has an invalid PE header.");
            ushort machine = BitConverter.ToUInt16(bytes, peOffset + 4);
            if (machine != 0x8664)
                throw new InvalidOperationException($"{label} is not AMD64 (0x{machine:x4}).");
        }

        private static void CleanupPartialDirectories(string versionRoot)
        {
            foreach (string path in Directory.GetDirectories(versionRoot, ".*.partial", SearchOption.TopDirectoryOnly))
                TryDeleteDirectory(path);
        }

        private static void TryDeleteDirectory(string path)
        {
            try
            {
                if (Directory.Exists(path))
                    Directory.Delete(path, recursive: true);
            }
            catch
            {
                // An old loaded DLL or antivirus may retain an immutable root; it is never selected.
            }
        }
    }
}
