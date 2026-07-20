using System;
using System.IO;
using System.IO.Compression;
using System.Text;
using GmodAddonCompressor.Systems.Tools;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace GmodAddonCompressor.Tests;

[TestClass]
public sealed class ToolExtractionSystemTests
{
    [TestMethod]
    public void EmbeddedSourcePackageExtractsToPackageScopedWorkerPath()
    {
        ToolExtractionSystem.EnsureSourceAddonOptimizerExtracted();

        Assert.IsTrue(File.Exists(ToolPaths.WorkerExePath));
        Assert.IsTrue(File.Exists(ToolPaths.ManifestPath));
        StringAssert.Contains(
            ToolPaths.ToolRoot,
            ToolExtractionSystem.ComputePackageHash(
                GmodAddonCompressor.Properties.Resources.SourceAddonOptimizer_win_x64));
    }

    [TestMethod]
    public void ChangedPackageUsesNewDirectoryWhilePriorExecutableIsRunning()
    {
        string toolName = $"ToolExtractionSystemTests-{Guid.NewGuid():N}";
        string toolParent = Path.Combine(ToolPaths.ToolsRoot, toolName);
        byte[] firstPackage = CreatePackage("first worker");
        byte[] secondPackage = CreatePackage("second worker");

        try
        {
            string firstRoot = ToolExtractionSystem.EnsureExtracted(
                toolName,
                "same-version",
                firstPackage,
                new[] { "SourceAddonOptimizerWorker.exe" });
            string firstWorker = Path.Combine(firstRoot, "SourceAddonOptimizerWorker.exe");

            string secondRoot;
            using (new FileStream(
                firstWorker,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read))
            {
                secondRoot = ToolExtractionSystem.EnsureExtracted(
                    toolName,
                    "same-version",
                    secondPackage,
                    new[] { "SourceAddonOptimizerWorker.exe" });
            }

            Assert.AreNotEqual(firstRoot, secondRoot);
            Assert.AreEqual(
                "first worker",
                File.ReadAllText(firstWorker, Encoding.UTF8));
            Assert.AreEqual(
                "second worker",
                File.ReadAllText(
                    Path.Combine(secondRoot, "SourceAddonOptimizerWorker.exe"),
                    Encoding.UTF8));
        }
        finally
        {
            if (Directory.Exists(toolParent))
                Directory.Delete(toolParent, recursive: true);
        }
    }

    private static byte[] CreatePackage(string workerContents)
    {
        using var stream = new MemoryStream();
        using (var archive = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: true))
        {
            ZipArchiveEntry worker = archive.CreateEntry("SourceAddonOptimizerWorker.exe");
            using Stream entryStream = worker.Open();
            using var writer = new StreamWriter(
                entryStream,
                new UTF8Encoding(encoderShouldEmitUTF8Identifier: false));
            writer.Write(workerContents);
        }
        return stream.ToArray();
    }
}
