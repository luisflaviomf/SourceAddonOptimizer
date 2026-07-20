using System.Linq;
using GmodAddonCompressor.Systems.Optimizer;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace GmodAddonCompressor.Tests;

[TestClass]
public sealed class SourceAddonOptimizerRunnerArgumentTests
{
    [TestMethod]
    public void MaximumJobsArePassedAsAutoByDefault()
    {
        var options = new SourceAddonOptimizerRunOptions
        {
            WorkerExePath = @"C:\tools\worker.exe",
            AddonPath = @"C:\addon",
            WorkDir = @"C:\work",
            OptimizerMode = "maximum",
            MaximumJobs = 0,
        };

        var arguments = SourceAddonOptimizerRunner.BuildStartInfo(options)
            .ArgumentList.ToList();
        int index = arguments.IndexOf("--maximum-jobs");

        Assert.IsTrue(index >= 0);
        Assert.AreEqual("0", arguments[index + 1]);
    }

    [TestMethod]
    public void NonMaximumModeDoesNotReceiveMaximumJobs()
    {
        var options = new SourceAddonOptimizerRunOptions
        {
            WorkerExePath = @"C:\tools\worker.exe",
            AddonPath = @"C:\addon",
            WorkDir = @"C:\work",
            OptimizerMode = "normal",
            MaximumJobs = 4,
        };

        var arguments = SourceAddonOptimizerRunner.BuildStartInfo(options)
            .ArgumentList.ToList();

        CollectionAssert.DoesNotContain(arguments, "--maximum-jobs");
    }
}
