using GmodAddonCompressor.DataContexts;
using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems;
using GmodAddonCompressor.Systems.Tools;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class CompressMaximumIntegrationTests
{
    [Fact]
    public void MaximumIsAThirdCompressModeWithoutChangingModelsMode()
    {
        var context = new MainWindowContext();
        int modelsMode = context.OptimizerModeIndex;

        context.CompressModeIndex = 2;

        Assert.Equal(new[] { "Padrao", "Magick", "Maximum" }, context.CompressModeList);
        Assert.True(context.CompressModeIsMaximum);
        Assert.False(context.CompressModeIsStandard);
        Assert.False(context.CompressModeIsMagick);
        Assert.Equal(modelsMode, context.OptimizerModeIndex);
    }

    [Fact]
    public void MaximumOptionsDescribeAdaptiveVtfRouting()
    {
        var options = new CompressPipelineOptions { Mode = CompressPipelineMode.Maximum };

        Assert.True(options.IsMaximumMode);
        Assert.Equal("Maximum", options.ModeLabel);
        Assert.Contains("original, 2x and 4x", options.BuildRoutingSummary());
    }

    [Fact]
    public void PublishedAssemblyContainsPinnedMaximumEncoders()
    {
        string[] resources = typeof(MaximumVtfToolSystem).Assembly.GetManifestResourceNames();

        Assert.Contains("GmodAddonCompressor.Resources.VtfMaximumTools.zip", resources);
    }

    [Fact]
    public void MaximumUsesTenParallelVtfJobs()
    {
        var options = new CompressPipelineOptions
        {
            Mode = CompressPipelineMode.Maximum,
            MaximumVtfParallelism = 10
        };
        var compressor = new CompressAddonSystem(Path.GetTempPath(), pipelineOptions: options);

        Assert.Equal(10, compressor.GetMaxDegreeOfParallelism("vtf"));
    }
}
