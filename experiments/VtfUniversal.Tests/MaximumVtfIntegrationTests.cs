using GmodAddonCompressor.Interfaces;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Objects;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class MaximumVtfIntegrationTests
{
    [Fact]
    public async Task MaximumRunsOneBatchWithoutPerFileTimeout()
    {
        var service = new RecordingBatchService();
        FileInfo[] files = Enumerable.Range(0, 12)
            .Select(index => new FileInfo(Path.Combine(Path.GetTempPath(), $"{index:D2}.vtf")))
            .ToArray();

        await CompressAddonSystem.RunBatchServiceAsync(
            service,
            files,
            CancellationToken.None);

        IReadOnlyList<FileInfo> call = Assert.Single(service.Calls);
        Assert.Equal(12, call.Count);
        Assert.Equal(files.Select(file => file.FullName), call.Select(file => file.FullName));
    }

    [Fact]
    public void MaximumDefaultsToBalancedUniversalProfile()
    {
        var options = new CompressPipelineOptions
        {
            Mode = CompressPipelineMode.Maximum
        };

        Assert.Equal(UniversalVtfProfileKind.Balanced, options.MaximumVtfProfile);
        Assert.Contains("universal VTF", options.BuildRoutingSummary(),
            StringComparison.OrdinalIgnoreCase);
        Assert.True(typeof(ICompressBatch).IsAssignableFrom(typeof(MaximumVTFEdit)));
    }

    [Fact]
    public void ProductionHasNoLegacyLabReference()
    {
        string repository = FindRepositoryRoot();
        string project = File.ReadAllText(Path.Combine(
            repository,
            "GmodAddonCompressor-master",
            "GmodAddonCompressor",
            "GmodAddonCompressor.csproj"));
        string facade = File.ReadAllText(Path.Combine(
            repository,
            "GmodAddonCompressor-master",
            "GmodAddonCompressor",
            "Objects",
            "MaximumVTFEdit.cs"));

        // Magick+ reuses the lab codec pieces (exact BC3->BC1 repack); the July
        // Maximum runner itself must never be linked into production.
        foreach (string runnerFolder in new[] { "Experiment", "Baseline", "Sampling", "Selection", "Metrics", "Reporting" })
        {
            Assert.DoesNotContain($@"VtfMaximumLab\{runnerFolder}\", project, StringComparison.OrdinalIgnoreCase);
        }
        Assert.DoesNotContain("VtfMaximumLab", facade, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("new VTFEdit", facade, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("VtfMaximumExperimentRunner", facade,
            StringComparison.OrdinalIgnoreCase);
    }

    private static string FindRepositoryRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null)
        {
            if (Directory.Exists(Path.Combine(directory.FullName, "GmodAddonCompressor-master")) &&
                Directory.Exists(Path.Combine(directory.FullName, "experiments")))
            {
                return directory.FullName;
            }

            directory = directory.Parent;
        }

        throw new DirectoryNotFoundException("Could not locate repository root from test output.");
    }

    private sealed class RecordingBatchService : ICompressBatch
    {
        internal List<IReadOnlyList<FileInfo>> Calls { get; } = new();

        public Task CompressBatchAsync(
            IReadOnlyList<FileInfo> files,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            Calls.Add(files.ToArray());
            return Task.CompletedTask;
        }
    }
}
