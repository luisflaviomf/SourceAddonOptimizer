using System.Diagnostics;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Encodings.Web;
using System.Text.Json;
using GmodAddonCompressor.Properties;
using ImageMagick;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfToolBinary(
    string Name,
    string Version,
    long Bytes,
    string Sha256);

internal sealed record UniversalVtfToolchainFingerprint(
    string Root,
    IReadOnlyList<UniversalVtfToolBinary> Binaries,
    string CanonicalArguments,
    string Sha256);

internal sealed partial record UniversalVtfToolchain
{
    private const long ExpectedVtfCmdBytes = 102_400;
    private const long ExpectedMagickBytes = 1_126_696;
    private const string ExpectedVtfCmdVersion = "1.0.10";
    private const string ExpectedMagickVersion = "14.10.4";
    private const string ExpectedVtfCmdSha256 =
        "30d185a6df81d0c5d09c7ecda91a3f49ff183c14a2db12aaa845dc88059ee11e";
    private const string ExpectedVtfEditArchiveSha256 =
        "87d0280223f60be947723cb67076ed47db55221ee69ca60a7f773e4926f11c3c";
    private const string ExpectedMagickSha256 =
        "5e2ff85094dfd8fa9e15c3f884c3e3ef483bc074538b8c4fc8e1e0aaa643dfd6";

    private static readonly string[] CanonicalArgumentTemplate =
    {
        "-file", "<input.png>",
        "-output", "<isolated-dir>",
        "-format", "<DXT1|DXT5>",
        "-alphaformat", "<same>",
        "-version", "<source-version-clamped-to-7.4>"
    };

    internal static UniversalVtfToolchainFingerprint Verify(string root)
    {
        string canonicalRoot = RequireDirectory(root);
        var binaries = new List<UniversalVtfToolBinary>();

        string vtfCmdPath = RequireContainedFile(
            canonicalRoot,
            Path.Combine("VTFEdit", "VTFCmd.exe"));
        string vtfCmdHash = HashFile(vtfCmdPath);
        if (!vtfCmdHash.Equals(ExpectedVtfCmdSha256, StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                $"vtfcmd_sha256:{vtfCmdHash}!={ExpectedVtfCmdSha256}");
        }

        FileInfo vtfCmdInfo = new(vtfCmdPath);
        string vtfCmdVersion = FileVersion(vtfCmdPath);
        if (vtfCmdInfo.Length != ExpectedVtfCmdBytes ||
            !vtfCmdVersion.Equals(ExpectedVtfCmdVersion, StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                $"vtfcmd_identity:{vtfCmdInfo.Length}:{vtfCmdVersion}");
        }

        binaries.Add(Binary(canonicalRoot, vtfCmdPath, vtfCmdVersion, vtfCmdHash));

        string archivePath = RequireContainedFile(canonicalRoot, "VTFEdit.zip");
        string archiveHash = HashFile(archivePath);
        if (!archiveHash.Equals(ExpectedVtfEditArchiveSha256, StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                $"vtfedit_archive_sha256:{archiveHash}!={ExpectedVtfEditArchiveSha256}");
        }

        binaries.Add(Binary(canonicalRoot, archivePath, "embedded-v1", archiveHash));

        string magickPath = RequireContainedFile(
            canonicalRoot,
            Path.Combine("magick", "Magick.NET-Q16-AnyCPU.dll"));
        string magickHash = HashFile(magickPath);
        FileInfo magickInfo = new(magickPath);
        string magickVersion = FileVersion(magickPath);
        if (!magickHash.Equals(ExpectedMagickSha256, StringComparison.Ordinal) ||
            magickInfo.Length != ExpectedMagickBytes ||
            !magickVersion.Equals(ExpectedMagickVersion, StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                $"magick_managed_identity:{magickInfo.Length}:{magickVersion}:{magickHash}");
        }

        binaries.Add(Binary(canonicalRoot, magickPath, magickVersion, magickHash));

        string corePath = RequireContainedFile(
            canonicalRoot,
            Path.Combine("magick", "Magick.NET.Core.dll"));
        binaries.Add(Binary(canonicalRoot, corePath, FileVersion(corePath), HashFile(corePath)));

        string nativeRoot = Path.Combine(canonicalRoot, "magick", "native");
        if (!Directory.Exists(nativeRoot))
        {
            throw new InvalidDataException("magick_native_directory_missing");
        }

        string[] nativeFiles = Directory.GetFiles(nativeRoot, "Magick.Native*", SearchOption.AllDirectories);
        if (nativeFiles.Length == 0)
        {
            throw new InvalidDataException("magick_native_binary_missing");
        }

        foreach (string nativePath in nativeFiles)
        {
            string contained = RequireContainedPath(canonicalRoot, nativePath);
            binaries.Add(Binary(
                canonicalRoot,
                contained,
                FileVersion(contained),
                HashFile(contained)));
        }

        UniversalVtfToolBinary[] ordered = binaries
            .OrderBy(binary => binary.Name, StringComparer.Ordinal)
            .ToArray();
        string canonicalArguments = JsonSerializer.Serialize(
            CanonicalArgumentTemplate,
            new JsonSerializerOptions
            {
                Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
            });
        byte[] canonicalFingerprint = WriteCanonicalFingerprint(ordered, CanonicalArgumentTemplate);
        return new UniversalVtfToolchainFingerprint(
            canonicalRoot,
            ordered,
            canonicalArguments,
            Convert.ToHexString(SHA256.HashData(canonicalFingerprint)).ToLowerInvariant());
    }

    internal static void ExtractEmbeddedTo(string destination)
    {
        RequireText(destination, nameof(destination));
        string root = Path.GetFullPath(destination);
        Directory.CreateDirectory(root);
        if (Directory.EnumerateFileSystemEntries(root).Any())
        {
            throw new IOException($"Toolchain destination is not empty: {root}");
        }

        byte[] archiveBytes = Resources.VTFEdit;
        string archiveHash = Convert.ToHexString(SHA256.HashData(archiveBytes)).ToLowerInvariant();
        if (!archiveHash.Equals(ExpectedVtfEditArchiveSha256, StringComparison.Ordinal))
        {
            throw new InvalidDataException(
                $"embedded_vtfedit_archive_sha256:{archiveHash}!={ExpectedVtfEditArchiveSha256}");
        }

        string archivePath = Path.Combine(root, "VTFEdit.zip");
        File.WriteAllBytes(archivePath, archiveBytes);
        ExtractArchive(archiveBytes, root);

        string magickRoot = Path.Combine(root, "magick");
        Directory.CreateDirectory(magickRoot);
        string q16Assembly = typeof(MagickImage).Assembly.Location;
        CopyExact(q16Assembly, Path.Combine(magickRoot, "Magick.NET-Q16-AnyCPU.dll"));
        string assemblyDirectory = Path.GetDirectoryName(q16Assembly)
            ?? throw new InvalidDataException("magick_assembly_directory_missing");
        string coreAssembly = Path.Combine(assemblyDirectory, "Magick.NET.Core.dll");
        CopyExact(coreAssembly, Path.Combine(magickRoot, "Magick.NET.Core.dll"));

        string runtimeIdentifier = RuntimeIdentifier();
        string nativeSource = FindNativeBinary(assemblyDirectory, runtimeIdentifier);
        string nativeDestination = Path.Combine(
            magickRoot,
            "native",
            runtimeIdentifier,
            Path.GetFileName(nativeSource));
        Directory.CreateDirectory(Path.GetDirectoryName(nativeDestination)!);
        CopyExact(nativeSource, nativeDestination);
    }

    private static byte[] WriteCanonicalFingerprint(
        IReadOnlyList<UniversalVtfToolBinary> binaries,
        IReadOnlyList<string> arguments)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions
               {
                   Indented = false,
                   SkipValidation = false,
                   Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping
               }))
        {
            writer.WriteStartObject();
            writer.WriteString("schema", "universal-vtf-toolchain-fingerprint-v1");
            writer.WritePropertyName("arguments");
            writer.WriteStartArray();
            foreach (string argument in arguments)
            {
                writer.WriteStringValue(argument);
            }

            writer.WriteEndArray();
            writer.WritePropertyName("binaries");
            writer.WriteStartArray();
            foreach (UniversalVtfToolBinary binary in binaries)
            {
                writer.WriteStartObject();
                writer.WriteString("name", binary.Name);
                writer.WriteString("version", binary.Version);
                writer.WriteNumber("bytes", binary.Bytes);
                writer.WriteString("sha256", binary.Sha256);
                writer.WriteEndObject();
            }

            writer.WriteEndArray();
            writer.WriteEndObject();
        }

        return stream.ToArray();
    }

    private static UniversalVtfToolBinary Binary(
        string root,
        string path,
        string version,
        string sha256) =>
        new(
            Path.GetRelativePath(root, path).Replace('\\', '/'),
            version,
            new FileInfo(path).Length,
            sha256);

    private static string RequireDirectory(string requested)
    {
        RequireText(requested, nameof(requested));
        string root = Path.GetFullPath(requested);
        if (!Directory.Exists(root))
        {
            throw new DirectoryNotFoundException(root);
        }

        return root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }

    private static string RequireContainedFile(string root, string relativePath)
    {
        if (Path.IsPathRooted(relativePath))
        {
            throw new ArgumentException("A relative tool path is required.", nameof(relativePath));
        }

        string path = RequireContainedPath(root, Path.Combine(root, relativePath));
        if (!File.Exists(path))
        {
            throw new FileNotFoundException($"Required toolchain file is missing: {relativePath}", path);
        }

        return path;
    }

    private static string RequireContainedPath(string root, string requested)
    {
        string fullPath = Path.GetFullPath(requested);
        string prefix = root + Path.DirectorySeparatorChar;
        if (!fullPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidDataException($"toolchain_path_escape:{requested}");
        }

        return fullPath;
    }

    private static void ExtractArchive(byte[] archiveBytes, string root)
    {
        using var memory = new MemoryStream(archiveBytes, writable: false);
        using var archive = new ZipArchive(memory, ZipArchiveMode.Read, leaveOpen: false);
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            string normalized = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
            string destination = RequireContainedPath(root, Path.Combine(root, normalized));
            string relative = Path.GetRelativePath(root, destination).Replace('\\', '/');
            if (!seen.Add(relative))
            {
                throw new InvalidDataException($"toolchain_archive_collision:{relative}");
            }

            if (string.IsNullOrEmpty(entry.Name))
            {
                Directory.CreateDirectory(destination);
                continue;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            entry.ExtractToFile(destination, overwrite: false);
        }
    }

    private static string FindNativeBinary(string assemblyDirectory, string runtimeIdentifier)
    {
        string expectedName = RuntimeInformation.ProcessArchitecture switch
        {
            Architecture.X64 => "Magick.Native-Q16-x64.dll",
            Architecture.X86 => "Magick.Native-Q16-x86.dll",
            Architecture.Arm64 => "Magick.Native-Q16-arm64.dll",
            _ => throw new PlatformNotSupportedException(
                $"Unsupported ImageMagick architecture: {RuntimeInformation.ProcessArchitecture}")
        };
        string[] matches = Directory
            .GetFiles(assemblyDirectory, expectedName, SearchOption.AllDirectories)
            .Where(path => path.Replace('\\', '/').Contains(
                $"/{runtimeIdentifier}/",
                StringComparison.OrdinalIgnoreCase))
            .OrderBy(path => path.Length)
            .ThenBy(path => path, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        if (matches.Length == 0)
        {
            throw new FileNotFoundException(
                $"ImageMagick native binary for {runtimeIdentifier} was not found.",
                expectedName);
        }

        return matches[0];
    }

    private static string RuntimeIdentifier()
    {
        string os = RuntimeInformation.IsOSPlatform(OSPlatform.Windows)
            ? "win"
            : RuntimeInformation.IsOSPlatform(OSPlatform.Linux)
                ? "linux"
                : RuntimeInformation.IsOSPlatform(OSPlatform.OSX)
                    ? "osx"
                    : throw new PlatformNotSupportedException("Unsupported ImageMagick operating system.");
        string architecture = RuntimeInformation.ProcessArchitecture switch
        {
            Architecture.X64 => "x64",
            Architecture.X86 => "x86",
            Architecture.Arm64 => "arm64",
            _ => throw new PlatformNotSupportedException(
                $"Unsupported ImageMagick architecture: {RuntimeInformation.ProcessArchitecture}")
        };
        return $"{os}-{architecture}";
    }

    private static void CopyExact(string source, string destination)
    {
        if (!File.Exists(source))
        {
            throw new FileNotFoundException("Required toolchain source file is missing.", source);
        }

        File.Copy(source, destination, overwrite: false);
    }

    private static string FileVersion(string path)
    {
        string? version = FileVersionInfo.GetVersionInfo(path).FileVersion;
        return version?.Split('+', StringSplitOptions.TrimEntries)[0] ?? string.Empty;
    }

    private static string HashFile(string path)
    {
        using FileStream stream = new(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read,
            bufferSize: 1024 * 1024,
            FileOptions.SequentialScan);
        using SHA256 sha256 = SHA256.Create();
        return Convert.ToHexString(sha256.ComputeHash(stream)).ToLowerInvariant();
    }

    private static void RequireText(string? value, string parameterName)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new ArgumentException("A non-empty path is required.", parameterName);
        }
    }
}
