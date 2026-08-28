using System.Diagnostics;
using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfTreeInventoryTests
{
    [Fact]
    public void ComputesCanonicalDigest()
    {
        using var tree = TestTree.Create(
            ("a.txt", new byte[] { 0x41 }),
            ("sub/b.bin", new byte[] { 0x00, 0xff }));

        UniversalVtfTreeSnapshot actual = UniversalVtfTreeInventory.Capture(tree.Root);

        Assert.Equal(2, actual.Files.Count);
        Assert.Equal(3, actual.TotalBytes);
        Assert.Equal(
            "75e21d8d75cff49a5579a92b2f881167a5cb57ee7fa08ba2602ee84458b424eb",
            actual.TreeSha256);
    }

    [Fact]
    public void SortsCanonicalPathsByCaseFoldedOrdinalKey()
    {
        using var tree = TestTree.Create(
            ("a_u.txt", new byte[] { 0x01 }),
            ("au.txt", new byte[] { 0x02 }));

        UniversalVtfTreeSnapshot actual = UniversalVtfTreeInventory.Capture(tree.Root);

        Assert.Equal(
            "883402f20663ecb974334b45c4b34cf924de75fd5c76202774998402a6fa5009",
            actual.TreeSha256);
    }

    [Fact]
    public void RejectsWrongAnchor()
    {
        using var tree = TestTree.Create(("a.txt", new byte[] { 0x41 }));
        UniversalVtfTreeSnapshot snapshot = UniversalVtfTreeInventory.Capture(tree.Root);
        var wrong = new UniversalVtfTreeAnchor(
            "fixture",
            FileCount: 2,
            TotalBytes: 1,
            TreeSha256: snapshot.TreeSha256);

        InvalidDataException error = Assert.Throws<InvalidDataException>(
            () => UniversalVtfTreeInventory.RequireAnchor(snapshot, wrong));

        Assert.Contains("file_count", error.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void RejectsCaseFoldCollision()
    {
        Assert.Throws<InvalidDataException>(() =>
            UniversalVtfTreeInventory.ValidateRelativePaths(new[]
            {
                "Materials/A.vtf",
                "materials/a.vtf"
            }));
    }

    [Fact]
    public void RejectsReparsePoint()
    {
        using var tree = TestTree.Create(("target/a.txt", new byte[] { 0x41 }));
        tree.CreateDirectoryJunction("linked", "target");

        InvalidDataException error = Assert.Throws<InvalidDataException>(
            () => UniversalVtfTreeInventory.Capture(tree.Root));

        Assert.Contains("reparse", error.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void RejectsTraversalAndExistingDestination()
    {
        using var tree = TestTree.Create();
        Directory.CreateDirectory(Path.Combine(tree.Root, "already-exists"));

        Assert.Throws<InvalidDataException>(() =>
            UniversalVtfTreeInventory.ResolveNewDestination(tree.Root, "../escape"));
        Assert.Throws<IOException>(() =>
            UniversalVtfTreeInventory.ResolveNewDestination(tree.Root, "already-exists"));
    }

    private sealed class TestTree : IDisposable
    {
        private readonly List<string> _junctions = new();

        private TestTree(string root)
        {
            Root = root;
        }

        internal string Root { get; }

        internal static TestTree Create(params (string RelativePath, byte[] Bytes)[] files)
        {
            string root = Path.Combine(
                Path.GetTempPath(),
                "VtfUniversal.Tests",
                Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            var tree = new TestTree(root);

            foreach ((string relativePath, byte[] bytes) in files)
            {
                string path = tree.Resolve(relativePath);
                Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                File.WriteAllBytes(path, bytes);
            }

            return tree;
        }

        internal void CreateDirectoryJunction(string linkRelativePath, string targetRelativePath)
        {
            string link = Resolve(linkRelativePath);
            string target = Resolve(targetRelativePath);
            var start = new ProcessStartInfo
            {
                FileName = "cmd.exe",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };
            start.ArgumentList.Add("/d");
            start.ArgumentList.Add("/c");
            start.ArgumentList.Add("mklink");
            start.ArgumentList.Add("/J");
            start.ArgumentList.Add(link);
            start.ArgumentList.Add(target);

            using Process process = Process.Start(start)
                ?? throw new InvalidOperationException("Could not start mklink.");
            process.WaitForExit();
            if (process.ExitCode != 0)
            {
                throw new InvalidOperationException(process.StandardError.ReadToEnd());
            }

            _junctions.Add(link);
        }

        public void Dispose()
        {
            foreach (string junction in _junctions)
            {
                if (Directory.Exists(junction))
                {
                    Directory.Delete(junction);
                }
            }

            if (Directory.Exists(Root))
            {
                Directory.Delete(Root, recursive: true);
            }
        }

        private string Resolve(string relativePath)
        {
            string fullRoot = Path.GetFullPath(Root) + Path.DirectorySeparatorChar;
            string fullPath = Path.GetFullPath(Path.Combine(Root, relativePath));
            if (!fullPath.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException("Fixture path escaped its root.");
            }

            return fullPath;
        }
    }
}
