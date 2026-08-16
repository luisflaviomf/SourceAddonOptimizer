using GmodAddonCompressor.Models;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;

namespace GmodAddonCompressor.Systems.Companion
{
    internal static class CompanionPackageInstaller
    {
        internal const string PackageManifestName = "companion-package-manifest.json";
        internal const string OwnershipMarkerName = ".sourceaddonoptimizer-owner.json";
        internal const string StagingOwnershipMarkerName = ".sourceaddonoptimizer-staging-owner";
        internal const string StagingOwnershipMarkerContent = "source-addon-optimizer-companion-staging-v1";
        private const string PayloadRoot = "sourceaddonoptimizer_companion/";
        private const int MaximumPackageBytes = 20_000_000;
        private const int MaximumManifestBytes = 1_000_000;
        private const int MaximumFiles = 512;
        private const string StagingPrefix = ".sourceaddonoptimizer_companion.";
        private const string StagingSuffix = ".staging";

        internal static CompanionInstallStatus Inspect(byte[] packageBytes, string garrysModRoot)
        {
            ParsedPackage package = ParsePackage(packageBytes);
            string destination = CompanionPathPolicy.ResolveAddonDestination(garrysModRoot);
            if (File.Exists(destination) && !Directory.Exists(destination))
            {
                return new CompanionInstallStatus(
                    CompanionInstallState.UnknownFolder,
                    destination,
                    package.Manifest.PackageVersion,
                    string.Empty,
                    "A file already occupies the Companion addon path. No changes are allowed."
                );
            }
            if (!Directory.Exists(destination))
            {
                return new CompanionInstallStatus(
                    CompanionInstallState.NotInstalled,
                    destination,
                    package.Manifest.PackageVersion,
                    string.Empty,
                    "The optional in-game Companion is not installed."
                );
            }

            CompanionPathPolicy.AssertNotReparsePoint(destination, "Companion destination");
            string markerPath = ResolveSafePath(destination, OwnershipMarkerName);
            if (!File.Exists(markerPath))
            {
                return new CompanionInstallStatus(
                    CompanionInstallState.UnknownFolder,
                    destination,
                    package.Manifest.PackageVersion,
                    string.Empty,
                    "The destination exists without a Source Addon Optimizer ownership marker. It will not be modified."
                );
            }

            try
            {
                CompanionPathPolicy.AssertNotReparsePoint(markerPath, "Companion ownership marker");
                byte[] markerBytes = ReadOwnershipMarkerBytes(markerPath);
                using JsonDocument document = ParseClosedJson(markerBytes, "ownership marker");
                if (!document.RootElement.TryGetProperty("schemaVersion", out JsonElement schema)
                    || schema.ValueKind != JsonValueKind.Number
                    || !schema.TryGetInt32(out int schemaVersion))
                {
                    return new CompanionInstallStatus(
                        CompanionInstallState.UnknownFolder,
                        destination,
                        package.Manifest.PackageVersion,
                        string.Empty,
                        "The ownership marker is malformed. No changes are allowed."
                    );
                }
                if (schemaVersion != 1)
                {
                    string installedVersion = document.RootElement.TryGetProperty("packageVersion", out JsonElement version)
                        && version.ValueKind == JsonValueKind.String
                        ? version.GetString() ?? string.Empty
                        : string.Empty;
                    return new CompanionInstallStatus(
                        CompanionInstallState.Incompatible,
                        destination,
                        package.Manifest.PackageVersion,
                        installedVersion,
                        $"Ownership schema {schemaVersion} is not supported by this application."
                    );
                }

                CompanionOwnershipMarker ownership = ReadOwnership(destination);
                bool current = ownership.PackageSha256.Equals(package.PackageSha256, StringComparison.Ordinal)
                    && InstalledPayloadMatches(destination, ownership.Files);
                return new CompanionInstallStatus(
                    current ? CompanionInstallState.Compatible : CompanionInstallState.UpdateAvailable,
                    destination,
                    package.Manifest.PackageVersion,
                    ownership.PackageVersion,
                    current
                        ? "The installed Companion matches the packaged version."
                        : "An owned Companion install differs from the packaged version and can be updated safely."
                );
            }
            catch (Exception ex) when (ex is InvalidOperationException || ex is IOException || ex is UnauthorizedAccessException)
            {
                return new CompanionInstallStatus(
                    CompanionInstallState.UnknownFolder,
                    destination,
                    package.Manifest.PackageVersion,
                    string.Empty,
                    "The ownership marker could not be verified. No changes are allowed."
                );
            }
        }

        internal static CompanionInstallResult Install(
            byte[] packageBytes,
            string garrysModRoot,
            TimeSpan? lockTimeout = null)
        {
            ParsedPackage package = ParsePackage(packageBytes);
            string destination = CompanionPathPolicy.ResolveAddonDestination(garrysModRoot);
            string addonsRoot = Path.GetDirectoryName(destination)
                ?? throw new InvalidOperationException("The Companion addons root is invalid.");
            Directory.CreateDirectory(addonsRoot);
            CompanionPathPolicy.AssertNotReparsePoint(addonsRoot, "addons directory");
            string lockPath = Path.Combine(addonsRoot, ".sourceaddonoptimizer_companion.install.lock");

            FileStream? installLock = null;
            try
            {
                installLock = AcquireLock(lockPath, lockTimeout ?? TimeSpan.FromSeconds(30));
                CleanupInterruptedStaging(addonsRoot);
                if (File.Exists(destination) && !Directory.Exists(destination))
                    throw new InvalidOperationException("The Companion destination is an unknown file.");

                if (!Directory.Exists(destination))
                    return FreshInstall(package, destination, addonsRoot);

                CompanionPathPolicy.AssertNotReparsePoint(destination, "Companion destination");
                CompanionOwnershipMarker ownership = ReadOwnership(destination);
                if (ownership.PackageSha256.Equals(package.PackageSha256, StringComparison.Ordinal)
                    && InstalledPayloadMatches(destination, ownership.Files))
                {
                    return new CompanionInstallResult(
                        destination,
                        package.Manifest.PackageVersion,
                        package.PackageSha256,
                        CompanionInstallDisposition.AlreadyInstalled
                    );
                }

                return UpdateOwnedInstall(package, ownership, destination, addonsRoot);
            }
            finally
            {
                installLock?.Dispose();
                TryDeleteFile(lockPath);
            }
        }

        internal static CompanionUninstallResult Uninstall(
            string garrysModRoot,
            TimeSpan? lockTimeout = null)
        {
            string destination = CompanionPathPolicy.ResolveAddonDestination(garrysModRoot);
            string addonsRoot = Path.GetDirectoryName(destination)
                ?? throw new InvalidOperationException("The Companion addons root is invalid.");
            if (!Directory.Exists(destination))
                return new CompanionUninstallResult(destination, addonDirectoryRemoved: true, Array.Empty<string>());

            Directory.CreateDirectory(addonsRoot);
            CompanionPathPolicy.AssertNotReparsePoint(addonsRoot, "addons directory");
            string lockPath = Path.Combine(addonsRoot, ".sourceaddonoptimizer_companion.install.lock");
            FileStream? installLock = null;
            try
            {
                installLock = AcquireLock(lockPath, lockTimeout ?? TimeSpan.FromSeconds(30));
                CompanionPathPolicy.AssertNotReparsePoint(destination, "Companion destination");
                CompanionOwnershipMarker ownership = ReadOwnership(destination);
                var candidateDirectories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

                foreach (CompanionPackageFile declared in ownership.Files)
                {
                    string path = ResolveSafePath(destination, declared.Path);
                    AssertNoReparseSegments(destination, path);
                    if (Directory.Exists(path))
                        throw new InvalidOperationException($"Owned Companion file became a directory: {declared.Path}");
                    if (File.Exists(path))
                    {
                        CompanionPathPolicy.AssertNotReparsePoint(path, "owned Companion file");
                        File.Delete(path);
                    }
                    AddParentDirectories(destination, path, candidateDirectories);
                }

                string marker = ResolveSafePath(destination, OwnershipMarkerName);
                CompanionPathPolicy.AssertNotReparsePoint(marker, "Companion ownership marker");
                File.Delete(marker);
                RemoveKnownEmptyDirectories(destination, candidateDirectories);

                IReadOnlyList<string> unknown = EnumerateUnknownPaths(destination);
                bool removed = false;
                if (unknown.Count == 0 && !Directory.EnumerateFileSystemEntries(destination).Any())
                {
                    Directory.Delete(destination, recursive: false);
                    removed = true;
                }

                return new CompanionUninstallResult(destination, removed, unknown);
            }
            finally
            {
                installLock?.Dispose();
                TryDeleteFile(lockPath);
            }
        }

        private static CompanionInstallResult FreshInstall(
            ParsedPackage package,
            string destination,
            string addonsRoot)
        {
            string staging = NewStagingPath(addonsRoot);
            try
            {
                ExtractToStaging(package, staging);
                WriteOwnership(staging, package);
                ValidateStagedInstall(staging, package);
                File.Delete(Path.Combine(staging, StagingOwnershipMarkerName));
                if (Directory.Exists(destination) || File.Exists(destination))
                    throw new InvalidOperationException("The Companion destination appeared during installation.");
                Directory.Move(staging, destination);
                ValidateStagedInstall(destination, package);
                return new CompanionInstallResult(
                    destination,
                    package.Manifest.PackageVersion,
                    package.PackageSha256,
                    CompanionInstallDisposition.Installed
                );
            }
            catch
            {
                DeleteOwnedStaging(staging);
                throw;
            }
        }

        private static CompanionInstallResult UpdateOwnedInstall(
            ParsedPackage package,
            CompanionOwnershipMarker previous,
            string destination,
            string addonsRoot)
        {
            string staging = NewStagingPath(addonsRoot);
            try
            {
                ExtractToStaging(package, staging);
                WriteOwnership(staging, package);
                ValidateStagedInstall(staging, package);
                var newPaths = new HashSet<string>(package.Manifest.Files.Select(file => file.Path), StringComparer.Ordinal);
                var candidateDirectories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

                foreach (CompanionPackageFile declared in package.Manifest.Files)
                {
                    string source = ResolveSafePath(staging, declared.Path);
                    string target = ResolveSafePath(destination, declared.Path);
                    AssertNoReparseSegments(destination, target);
                    Directory.CreateDirectory(Path.GetDirectoryName(target) ?? destination);
                    AssertNoReparseSegments(destination, target);
                    ReplaceFileFromStaging(source, target, declared);
                }

                foreach (CompanionPackageFile retired in previous.Files.Where(file => !newPaths.Contains(file.Path)))
                {
                    string target = ResolveSafePath(destination, retired.Path);
                    AssertNoReparseSegments(destination, target);
                    if (Directory.Exists(target))
                        throw new InvalidOperationException($"Retired Companion file became a directory: {retired.Path}");
                    if (File.Exists(target))
                    {
                        CompanionPathPolicy.AssertNotReparsePoint(target, "retired Companion file");
                        File.Delete(target);
                    }
                    AddParentDirectories(destination, target, candidateDirectories);
                }

                string stagedMarker = ResolveSafePath(staging, OwnershipMarkerName);
                string finalMarker = ResolveSafePath(destination, OwnershipMarkerName);
                ReplaceFileFromStaging(stagedMarker, finalMarker, expected: null);
                RemoveKnownEmptyDirectories(destination, candidateDirectories);
                if (!InstalledPayloadMatches(destination, package.Manifest.Files))
                    throw new InvalidOperationException("Updated Companion payload failed final validation.");
                return new CompanionInstallResult(
                    destination,
                    package.Manifest.PackageVersion,
                    package.PackageSha256,
                    CompanionInstallDisposition.Updated
                );
            }
            finally
            {
                DeleteOwnedStaging(staging);
            }
        }

        private static ParsedPackage ParsePackage(byte[] packageBytes)
        {
            if (packageBytes == null || packageBytes.Length == 0 || packageBytes.Length > MaximumPackageBytes)
                throw new InvalidOperationException("The Companion package is empty or exceeds its byte limit.");
            string packageSha256 = Digest(packageBytes);
            using var memory = new MemoryStream(packageBytes, writable: false);
            using var archive = new ZipArchive(memory, ZipArchiveMode.Read, leaveOpen: false);
            var entries = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
            long totalUncompressedBytes = 0;

            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                if (string.IsNullOrEmpty(entry.Name))
                    throw new InvalidOperationException($"The Companion package contains an undeclared directory entry: {entry.FullName}");
                string path = NormalizePackagePath(entry.FullName);
                totalUncompressedBytes += entry.Length;
                if (totalUncompressedBytes > MaximumPackageBytes)
                    throw new InvalidOperationException("The Companion package exceeds its uncompressed byte limit.");
                if (!entries.TryAdd(path, ReadEntry(entry)))
                    throw new InvalidOperationException($"The Companion package contains a duplicate path: {path}");
                if (entries.Count > MaximumFiles + 1)
                    throw new InvalidOperationException("The Companion package contains too many files.");
            }

            if (!entries.TryGetValue(PackageManifestName, out byte[]? manifestBytes))
                throw new InvalidOperationException("The Companion package manifest is missing.");
            CompanionPackageManifest manifest = ParseManifest(manifestBytes);
            var payloads = new Dictionary<string, byte[]>(StringComparer.Ordinal);

            foreach ((string path, byte[] bytes) in entries)
            {
                if (path.Equals(PackageManifestName, StringComparison.Ordinal))
                    continue;
                if (!path.StartsWith(PayloadRoot, StringComparison.Ordinal))
                    throw new InvalidOperationException($"The Companion package has an unsafe payload root: {path}");
                string relative = NormalizeRelativePath(path.Substring(PayloadRoot.Length));
                if (!payloads.TryAdd(relative, bytes))
                    throw new InvalidOperationException($"The Companion package has a duplicate payload: {relative}");
            }

            string[] actual = payloads.Keys.OrderBy(path => path, StringComparer.Ordinal).ToArray();
            string[] declared = manifest.Files.Select(file => file.Path).ToArray();
            if (!actual.SequenceEqual(declared, StringComparer.Ordinal))
                throw new InvalidOperationException("The Companion package payload differs from its manifest.");

            foreach (CompanionPackageFile file in manifest.Files)
            {
                byte[] bytes = payloads[file.Path];
                if (bytes.LongLength != file.Bytes || !Digest(bytes).Equals(file.Sha256, StringComparison.Ordinal))
                    throw new InvalidOperationException($"The Companion package hash/size differs for {file.Path}.");
            }

            return new ParsedPackage(manifest, packageSha256, payloads);
        }

        private static CompanionPackageManifest ParseManifest(byte[] bytes)
        {
            if (bytes.Length == 0 || bytes.Length > MaximumManifestBytes)
                throw new InvalidOperationException("The Companion package manifest exceeds its byte limit.");
            using JsonDocument document = ParseClosedJson(bytes, "package manifest");
            RequireClosedObject(document.RootElement, new[] { "schemaVersion", "packageVersion", "addonDirectory", "files" }, "package manifest");
            if (!document.RootElement.TryGetProperty("files", out JsonElement filesElement)
                || filesElement.ValueKind != JsonValueKind.Array)
                throw new InvalidOperationException("The Companion package manifest files are invalid.");
            foreach (JsonElement item in filesElement.EnumerateArray())
                RequireClosedObject(item, new[] { "path", "bytes", "sha256" }, "package manifest file");

            CompanionPackageManifest manifest;
            try
            {
                manifest = JsonSerializer.Deserialize<CompanionPackageManifest>(bytes)
                    ?? throw new InvalidOperationException("The Companion package manifest is empty.");
            }
            catch (JsonException ex)
            {
                throw new InvalidOperationException("The Companion package manifest is invalid JSON.", ex);
            }

            if (manifest.SchemaVersion != 1
                || manifest.AddonDirectory != CompanionPathPolicy.AddonDirectoryName
                || string.IsNullOrWhiteSpace(manifest.PackageVersion)
                || manifest.PackageVersion.Length > 32
                || manifest.PackageVersion.Any(character => !(IsAsciiLetterOrDigit(character) || character == '.' || character == '-')))
                throw new InvalidOperationException("The Companion package manifest contract/version is invalid.");
            if (manifest.Files.Count == 0 || manifest.Files.Count > MaximumFiles)
                throw new InvalidOperationException("The Companion package manifest file count is invalid.");

            string? previous = null;
            var unique = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            long totalDeclaredBytes = 0;
            foreach (CompanionPackageFile file in manifest.Files)
            {
                file.Path = NormalizeRelativePath(file.Path);
                if (previous != null && StringComparer.Ordinal.Compare(previous, file.Path) >= 0)
                    throw new InvalidOperationException("The Companion package manifest files are not uniquely ordinal-sorted.");
                previous = file.Path;
                if (file.Bytes < 0 || file.Bytes > MaximumPackageBytes || !IsSha256(file.Sha256) || !unique.Add(file.Path))
                    throw new InvalidOperationException($"The Companion package file declaration is invalid: {file.Path}");
                totalDeclaredBytes += file.Bytes;
                if (totalDeclaredBytes > MaximumPackageBytes)
                    throw new InvalidOperationException("The Companion package manifest exceeds its total byte limit.");
                file.Sha256 = file.Sha256.ToLowerInvariant();
            }

            if (!unique.Contains("addon.json"))
                throw new InvalidOperationException("The Companion package is missing addon.json.");
            int autorunCount = unique.Count(path => path.StartsWith("lua/autorun/", StringComparison.Ordinal) && path.EndsWith(".lua", StringComparison.Ordinal));
            if (autorunCount != 1)
                throw new InvalidOperationException("The Companion package must contain exactly one autorun Lua file.");
            return manifest;
        }

        private static CompanionOwnershipMarker ReadOwnership(string destination)
        {
            string markerPath = ResolveSafePath(destination, OwnershipMarkerName);
            if (!File.Exists(markerPath))
                throw new InvalidOperationException("The Companion destination is unknown because its ownership marker is missing.");
            CompanionPathPolicy.AssertNotReparsePoint(markerPath, "Companion ownership marker");
            byte[] bytes = ReadOwnershipMarkerBytes(markerPath);
            try
            {
                using JsonDocument document = ParseClosedJson(bytes, "ownership marker");
                RequireClosedObject(document.RootElement, new[] { "schemaVersion", "packageVersion", "packageSha256", "files" }, "ownership marker");
                if (!document.RootElement.TryGetProperty("files", out JsonElement filesElement)
                    || filesElement.ValueKind != JsonValueKind.Array)
                    throw new InvalidOperationException("The Companion ownership marker files are invalid.");
                foreach (JsonElement item in filesElement.EnumerateArray())
                    RequireClosedObject(item, new[] { "path", "bytes", "sha256" }, "ownership marker file");
                CompanionOwnershipMarker marker = JsonSerializer.Deserialize<CompanionOwnershipMarker>(bytes)
                    ?? throw new InvalidOperationException("The Companion ownership marker is empty.");
                ValidateOwnership(marker);
                return marker;
            }
            catch (Exception ex) when (ex is JsonException || ex is InvalidOperationException || ex is IOException)
            {
                throw new InvalidOperationException("The Companion ownership marker is invalid or tampered. " + ex.Message, ex);
            }
        }

        private static byte[] ReadOwnershipMarkerBytes(string markerPath)
        {
            using var stream = new FileStream(markerPath, FileMode.Open, FileAccess.Read, FileShare.Read);
            if (stream.Length <= 0 || stream.Length > MaximumManifestBytes)
                throw new InvalidOperationException("The Companion ownership marker exceeds its byte limit.");
            byte[] bytes = new byte[(int)stream.Length];
            int offset = 0;
            while (offset < bytes.Length)
            {
                int read = stream.Read(bytes, offset, bytes.Length - offset);
                if (read == 0)
                    throw new InvalidOperationException("The Companion ownership marker changed while reading.");
                offset += read;
            }
            if (stream.ReadByte() != -1)
                throw new InvalidOperationException("The Companion ownership marker changed while reading.");
            return bytes;
        }

        private static void ValidateOwnership(CompanionOwnershipMarker marker)
        {
            if (marker.SchemaVersion != 1
                || string.IsNullOrWhiteSpace(marker.PackageVersion)
                || !IsSha256(marker.PackageSha256)
                || marker.Files.Count == 0
                || marker.Files.Count > MaximumFiles)
                throw new InvalidOperationException("Invalid ownership marker contract.");
            string? previous = null;
            var unique = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (CompanionPackageFile file in marker.Files)
            {
                file.Path = NormalizeRelativePath(file.Path);
                if (previous != null && StringComparer.Ordinal.Compare(previous, file.Path) >= 0)
                    throw new InvalidOperationException("Ownership files are not uniquely ordinal-sorted.");
                previous = file.Path;
                if (file.Bytes < 0 || !IsSha256(file.Sha256) || !unique.Add(file.Path))
                    throw new InvalidOperationException("Invalid owned file declaration.");
                file.Sha256 = file.Sha256.ToLowerInvariant();
            }
            marker.PackageSha256 = marker.PackageSha256.ToLowerInvariant();
        }

        private static void ExtractToStaging(ParsedPackage package, string staging)
        {
            Directory.CreateDirectory(staging);
            File.WriteAllText(
                Path.Combine(staging, StagingOwnershipMarkerName),
                StagingOwnershipMarkerContent,
                new UTF8Encoding(encoderShouldEmitUTF8Identifier: false)
            );
            foreach (CompanionPackageFile declared in package.Manifest.Files)
            {
                string path = ResolveSafePath(staging, declared.Path);
                Directory.CreateDirectory(Path.GetDirectoryName(path) ?? staging);
                using var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                byte[] payload = package.Payloads[declared.Path];
                output.Write(payload, 0, payload.Length);
                output.Flush(flushToDisk: true);
            }
        }

        private static void WriteOwnership(string root, ParsedPackage package)
        {
            var marker = new CompanionOwnershipMarker
            {
                SchemaVersion = 1,
                PackageVersion = package.Manifest.PackageVersion,
                PackageSha256 = package.PackageSha256,
                Files = package.Manifest.Files.Select(file => new CompanionPackageFile
                {
                    Path = file.Path,
                    Bytes = file.Bytes,
                    Sha256 = file.Sha256
                }).ToList()
            };
            byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(marker);
            string path = ResolveSafePath(root, OwnershipMarkerName);
            using var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            output.Write(bytes, 0, bytes.Length);
            output.Flush(flushToDisk: true);
        }

        private static void ValidateStagedInstall(string root, ParsedPackage package)
        {
            if (!InstalledPayloadMatches(root, package.Manifest.Files))
                throw new InvalidOperationException("The staged Companion payload failed hash validation.");
            CompanionOwnershipMarker marker = ReadOwnership(root);
            if (!marker.PackageSha256.Equals(package.PackageSha256, StringComparison.Ordinal)
                || !marker.PackageVersion.Equals(package.Manifest.PackageVersion, StringComparison.Ordinal))
                throw new InvalidOperationException("The staged Companion ownership marker differs from its package.");
            var allowed = new HashSet<string>(package.Manifest.Files.Select(file => file.Path), StringComparer.Ordinal)
            {
                OwnershipMarkerName
            };
            if (File.Exists(Path.Combine(root, StagingOwnershipMarkerName)))
                allowed.Add(StagingOwnershipMarkerName);
            string[] actual = Directory.GetFiles(root, "*", SearchOption.AllDirectories)
                .Select(path => Path.GetRelativePath(root, path).Replace('\\', '/'))
                .OrderBy(path => path, StringComparer.Ordinal)
                .ToArray();
            if (!actual.SequenceEqual(allowed.OrderBy(path => path, StringComparer.Ordinal), StringComparer.Ordinal))
                throw new InvalidOperationException("The staged Companion contains undeclared files.");
        }

        private static bool InstalledPayloadMatches(string root, IEnumerable<CompanionPackageFile> files)
        {
            try
            {
                foreach (CompanionPackageFile declared in files)
                {
                    string path = ResolveSafePath(root, declared.Path);
                    AssertNoReparseSegments(root, path);
                    if (!File.Exists(path) || Directory.Exists(path))
                        return false;
                    using FileStream input = File.OpenRead(path);
                    (string hash, long bytes) = Hash(input);
                    if (bytes != declared.Bytes || !hash.Equals(declared.Sha256, StringComparison.Ordinal))
                        return false;
                }
                return true;
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                return false;
            }
        }

        private static void ReplaceFileFromStaging(
            string source,
            string target,
            CompanionPackageFile? expected)
        {
            if (Directory.Exists(target))
                throw new InvalidOperationException($"The owned Companion path became a directory: {target}");
            if (File.Exists(target))
                CompanionPathPolicy.AssertNotReparsePoint(target, "owned Companion file");
            string temporary = target + $".sao-replacement-{Environment.ProcessId}-{Guid.NewGuid():N}.tmp";
            try
            {
                using (FileStream input = File.OpenRead(source))
                using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    input.CopyTo(output);
                    output.Flush(flushToDisk: true);
                }
                if (expected != null)
                {
                    using FileStream verify = File.OpenRead(temporary);
                    (string hash, long bytes) = Hash(verify);
                    if (bytes != expected.Bytes || !hash.Equals(expected.Sha256, StringComparison.Ordinal))
                        throw new InvalidOperationException($"The staged replacement differs for {expected.Path}.");
                }
                File.Move(temporary, target, overwrite: true);
            }
            finally
            {
                TryDeleteFile(temporary);
            }
        }

        private static FileStream AcquireLock(string path, TimeSpan timeout)
        {
            DateTime deadline = DateTime.UtcNow + timeout;
            while (true)
            {
                try
                {
                    return new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
                }
                catch (Exception ex) when ((ex is IOException || ex is UnauthorizedAccessException) && DateTime.UtcNow < deadline)
                {
                    Thread.Sleep(50);
                }
            }
        }

        private static void CleanupInterruptedStaging(string addonsRoot)
        {
            foreach (string path in Directory.GetDirectories(addonsRoot, StagingPrefix + "*" + StagingSuffix, SearchOption.TopDirectoryOnly))
            {
                if (!HasValidStagingOwnership(path))
                    continue;
                DeleteOwnedStaging(path);
            }
        }

        private static string NewStagingPath(string addonsRoot) =>
            Path.Combine(addonsRoot, $"{StagingPrefix}{Environment.ProcessId}.{Guid.NewGuid():N}{StagingSuffix}");

        private static void DeleteOwnedStaging(string path)
        {
            if (!Directory.Exists(path))
                return;
            string name = Path.GetFileName(path);
            if (!IsValidStagingName(name))
                throw new InvalidOperationException("Refusing to remove a non-Companion staging directory.");
            CompanionPathPolicy.AssertNotReparsePoint(path, "Companion staging directory");
            Directory.Delete(path, recursive: true);
        }

        private static bool HasValidStagingOwnership(string path)
        {
            string name = Path.GetFileName(path);
            if (!IsValidStagingName(name))
                return false;
            CompanionPathPolicy.AssertNotReparsePoint(path, "Companion staging directory");
            string marker = Path.Combine(path, StagingOwnershipMarkerName);
            if (!File.Exists(marker))
                return false;
            CompanionPathPolicy.AssertNotReparsePoint(marker, "Companion staging ownership marker");
            try
            {
                return File.ReadAllText(marker, Encoding.UTF8).Equals(StagingOwnershipMarkerContent, StringComparison.Ordinal);
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                return false;
            }
        }

        private static bool IsValidStagingName(string name)
        {
            if (!name.StartsWith(StagingPrefix, StringComparison.Ordinal)
                || !name.EndsWith(StagingSuffix, StringComparison.Ordinal))
                return false;
            string identity = name.Substring(
                StagingPrefix.Length,
                name.Length - StagingPrefix.Length - StagingSuffix.Length
            );
            string[] parts = identity.Split('.');
            return parts.Length == 2
                && int.TryParse(parts[0], out int processId)
                && processId > 0
                && parts[1].Length == 32
                && parts[1].All(character =>
                    (character >= '0' && character <= '9')
                    || (character >= 'a' && character <= 'f'));
        }

        private static string NormalizePackagePath(string raw)
        {
            if (raw == null || raw.Contains('\\') || raw != raw.ToLowerInvariant() || raw.Any(character => character > 127))
                throw new InvalidOperationException($"Unsafe Companion package path; paths must be lowercase ASCII: {raw}");
            return NormalizeRelativePath(raw);
        }

        private static string NormalizeRelativePath(string raw)
        {
            string normalized = (raw ?? string.Empty).Replace('\\', '/').TrimEnd('/');
            string[] parts = normalized.Split('/');
            if (string.IsNullOrWhiteSpace(normalized)
                || normalized.StartsWith("/", StringComparison.Ordinal)
                || normalized.Contains(':')
                || normalized.Any(character => character > 127)
                || normalized != normalized.ToLowerInvariant()
                || parts.Any(part => string.IsNullOrEmpty(part) || part == "." || part == ".." || part.Length > 128)
                || normalized.Length > 512)
                throw new InvalidOperationException($"Unsafe Companion relative path; paths must be lowercase ASCII: {raw}");
            string extension = Path.GetExtension(normalized);
            if (!extension.Equals(".lua", StringComparison.Ordinal) && !extension.Equals(".json", StringComparison.Ordinal))
                throw new InvalidOperationException($"The Companion package uses a forbidden extension: {raw}");
            return string.Join('/', parts);
        }

        private static string ResolveSafePath(string root, string relative)
        {
            string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string normalized = relative == OwnershipMarkerName
                ? OwnershipMarkerName
                : NormalizeRelativePath(relative);
            string fullPath = Path.GetFullPath(Path.Combine(fullRoot, normalized.Replace('/', Path.DirectorySeparatorChar)));
            string containment = fullRoot + Path.DirectorySeparatorChar;
            if (!fullPath.StartsWith(containment, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException($"The Companion path escapes its root: {relative}");
            return fullPath;
        }

        private static void AssertNoReparseSegments(string root, string target)
        {
            string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string fullTarget = Path.GetFullPath(target);
            string containment = fullRoot + Path.DirectorySeparatorChar;
            if (!fullTarget.StartsWith(containment, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("The Companion path escapes its owned root.");
            CompanionPathPolicy.AssertNotReparsePoint(fullRoot, "Companion root");
            string relative = Path.GetRelativePath(fullRoot, fullTarget);
            string current = fullRoot;
            foreach (string part in relative.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar))
            {
                current = Path.Combine(current, part);
                if (Directory.Exists(current) || File.Exists(current))
                    CompanionPathPolicy.AssertNotReparsePoint(current, "Companion path segment");
            }
        }

        private static void AddParentDirectories(string root, string file, HashSet<string> directories)
        {
            string fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string? current = Path.GetDirectoryName(file);
            while (current != null && !current.Equals(fullRoot, StringComparison.OrdinalIgnoreCase))
            {
                directories.Add(current);
                current = Path.GetDirectoryName(current);
            }
        }

        private static void RemoveKnownEmptyDirectories(string root, IEnumerable<string> directories)
        {
            foreach (string directory in directories.OrderByDescending(path => path.Length))
            {
                if (!Directory.Exists(directory))
                    continue;
                AssertNoReparseSegments(root, directory);
                if (!Directory.EnumerateFileSystemEntries(directory).Any())
                    Directory.Delete(directory, recursive: false);
            }
        }

        private static IReadOnlyList<string> EnumerateUnknownPaths(string root)
        {
            var result = new List<string>();
            if (!Directory.Exists(root))
                return result;
            var pending = new Stack<string>();
            pending.Push(root);
            while (pending.Count > 0 && result.Count < 256)
            {
                string directory = pending.Pop();
                foreach (string entry in Directory.EnumerateFileSystemEntries(directory).OrderBy(path => path, StringComparer.Ordinal))
                {
                    string relative = Path.GetRelativePath(root, entry).Replace('\\', '/');
                    if (Directory.Exists(entry))
                    {
                        result.Add(relative + "/");
                        if ((File.GetAttributes(entry) & FileAttributes.ReparsePoint) != 0)
                            continue;
                        else
                            pending.Push(entry);
                    }
                    else
                    {
                        result.Add(relative);
                    }
                    if (result.Count >= 256)
                        break;
                }
            }
            result.Sort(StringComparer.Ordinal);
            return result;
        }

        private static JsonDocument ParseClosedJson(byte[] bytes, string label)
        {
            try
            {
                return JsonDocument.Parse(bytes, new JsonDocumentOptions
                {
                    AllowTrailingCommas = false,
                    CommentHandling = JsonCommentHandling.Disallow,
                    MaxDepth = 12
                });
            }
            catch (JsonException ex)
            {
                throw new InvalidOperationException($"The Companion {label} is invalid JSON.", ex);
            }
        }

        private static void RequireClosedObject(JsonElement element, IEnumerable<string> expected, string label)
        {
            if (element.ValueKind != JsonValueKind.Object)
                throw new InvalidOperationException($"The Companion {label} must be an object.");
            var required = new HashSet<string>(expected, StringComparer.Ordinal);
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (JsonProperty property in element.EnumerateObject())
            {
                if (!required.Contains(property.Name) || !seen.Add(property.Name))
                    throw new InvalidOperationException($"The Companion {label} has unknown or duplicate fields.");
            }
            if (!seen.SetEquals(required))
                throw new InvalidOperationException($"The Companion {label} is missing required fields.");
        }

        private static byte[] ReadEntry(ZipArchiveEntry entry)
        {
            if (entry.Length < 0 || entry.Length > MaximumPackageBytes)
                throw new InvalidOperationException($"The Companion ZIP entry exceeds its byte limit: {entry.FullName}");
            using Stream input = entry.Open();
            using var output = new MemoryStream((int)entry.Length);
            input.CopyTo(output);
            if (output.Length != entry.Length)
                throw new InvalidOperationException($"The Companion ZIP entry length changed while reading: {entry.FullName}");
            return output.ToArray();
        }

        private static string Digest(byte[] bytes) =>
            Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

        private static (string Hash, long Bytes) Hash(Stream stream)
        {
            using (stream)
            using (SHA256 sha256 = SHA256.Create())
            {
                byte[] buffer = new byte[64 * 1024];
                long bytes = 0;
                int read;
                while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    sha256.TransformBlock(buffer, 0, read, null, 0);
                    bytes += read;
                }
                sha256.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                return (Convert.ToHexString(sha256.Hash!).ToLowerInvariant(), bytes);
            }
        }

        private static bool IsSha256(string? value) =>
            value != null
            && value.Length == 64
            && value == value.ToLowerInvariant()
            && value.All(character => (character >= '0' && character <= '9') || (character >= 'a' && character <= 'f'));

        private static bool IsAsciiLetterOrDigit(char value) =>
            (value >= 'a' && value <= 'z')
            || (value >= 'A' && value <= 'Z')
            || (value >= '0' && value <= '9');

        private static void TryDeleteFile(string path)
        {
            try
            {
                if (File.Exists(path))
                    File.Delete(path);
            }
            catch (IOException)
            {
                // A concurrent caller may already own the lock file.
            }
            catch (UnauthorizedAccessException)
            {
                // A concurrent caller may already own the lock file.
            }
        }

        private sealed class ParsedPackage
        {
            internal ParsedPackage(
                CompanionPackageManifest manifest,
                string packageSha256,
                IReadOnlyDictionary<string, byte[]> payloads)
            {
                Manifest = manifest;
                PackageSha256 = packageSha256;
                Payloads = payloads;
            }

            internal CompanionPackageManifest Manifest { get; }
            internal string PackageSha256 { get; }
            internal IReadOnlyDictionary<string, byte[]> Payloads { get; }
        }
    }
}
