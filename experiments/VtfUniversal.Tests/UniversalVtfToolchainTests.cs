using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfToolchainTests
{
    [Fact]
    public void WrongVtfCmdHashFailsBeforeAnyOtherToolIsInspected()
    {
        using var root = new ToolchainTempDirectory();
        string directory = Path.Combine(root.Path, "VTFEdit");
        Directory.CreateDirectory(directory);
        File.WriteAllBytes(Path.Combine(directory, "VTFCmd.exe"), new byte[] { 1, 2, 3 });

        InvalidDataException error = Assert.Throws<InvalidDataException>(() =>
            UniversalVtfToolchain.Verify(root.Path));

        Assert.Contains("vtfcmd", error.Message, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("sha256", error.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void EmbeddedToolchainFingerprintIsCanonicalAndStable()
    {
        using var root = new ToolchainTempDirectory();
        UniversalVtfToolchain.ExtractEmbeddedTo(root.Path);

        UniversalVtfToolchainFingerprint first = UniversalVtfToolchain.Verify(root.Path);
        UniversalVtfToolchainFingerprint second = UniversalVtfToolchain.Verify(root.Path);

        Assert.Equal(first.Sha256, second.Sha256);
        Assert.Equal(64, first.Sha256.Length);
        Assert.Equal(
            first.Binaries.Select(binary => binary.Name).OrderBy(name => name, StringComparer.Ordinal),
            first.Binaries.Select(binary => binary.Name));
        Assert.Equal(
            "[\"-file\",\"<input.png>\",\"-output\",\"<isolated-dir>\",\"-format\",\"<DXT1|DXT5>\",\"-alphaformat\",\"<same>\",\"-version\",\"<source-version-clamped-to-7.4>\"]",
            first.CanonicalArguments);
        Assert.Contains(first.Binaries, binary =>
            binary.Name == "VTFEdit/VTFCmd.exe" &&
            binary.Bytes == 102_400 &&
            binary.Version == "1.0.10" &&
            binary.Sha256 == "30d185a6df81d0c5d09c7ecda91a3f49ff183c14a2db12aaa845dc88059ee11e");
        Assert.Contains(first.Binaries, binary =>
            binary.Name == "VTFEdit.zip" &&
            binary.Sha256 == "87d0280223f60be947723cb67076ed47db55221ee69ca60a7f773e4926f11c3c");
        Assert.Contains(first.Binaries, binary =>
            binary.Name == "magick/Magick.NET-Q16-AnyCPU.dll" &&
            binary.Bytes == 1_126_696 &&
            binary.Version == "14.10.4" &&
            binary.Sha256 == "5e2ff85094dfd8fa9e15c3f884c3e3ef483bc074538b8c4fc8e1e0aaa643dfd6");
        Assert.Contains(first.Binaries, binary =>
            binary.Name.StartsWith("magick/native/", StringComparison.Ordinal) &&
            binary.Name.Contains("Magick.Native", StringComparison.Ordinal));
    }

    internal sealed class ToolchainTempDirectory : IDisposable
    {
        internal ToolchainTempDirectory()
        {
            Path = System.IO.Path.Combine(
                System.IO.Path.GetTempPath(),
                "VtfUniversal.Tests",
                "Toolchain",
                Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Path);
        }

        internal string Path { get; }

        public void Dispose()
        {
            if (Directory.Exists(Path))
            {
                Directory.Delete(Path, recursive: true);
            }
        }
    }
}
