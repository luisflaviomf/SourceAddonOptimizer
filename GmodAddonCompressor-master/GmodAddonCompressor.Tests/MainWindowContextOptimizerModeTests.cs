using System.Collections.Generic;
using GmodAddonCompressor.DataContexts;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace GmodAddonCompressor.Tests;

[TestClass]
public sealed class MainWindowContextOptimizerModeTests
{
    [TestMethod]
    public void MaximumModeMapsToIndexTwoAndDisablesManualTuning()
    {
        var context = new MainWindowContext { OptimizerModeMaximumChecked = true };

        Assert.AreEqual(2, context.OptimizerModeIndex);
        Assert.IsTrue(context.OptimizerModeIsMaximum);
        Assert.IsFalse(context.OptimizerModeIsNormal);
        Assert.IsFalse(context.OptimizerModeIsFidelity);
        Assert.IsFalse(context.OptimizerManualTuningEnabled);
        CollectionAssert.AreEqual(
            new[] { "Normal", "Fidelity", "Maximum (experimental)" },
            context.OptimizerModeList);
        StringAssert.Contains(context.OptimizerModeDescriptionText, "compiled Source bytes");
        Assert.AreEqual("normal", MainWindow.GetOptimizerModeArgument(0));
        Assert.AreEqual("fidelity", MainWindow.GetOptimizerModeArgument(1));
        Assert.AreEqual("maximum", MainWindow.GetOptimizerModeArgument(2));
    }

    [TestMethod]
    public void OnlyMaximumModeRequiresTheVtfRenderDependency()
    {
        Assert.IsFalse(MainWindow.OptimizerModeNeedsVtfTool(0));
        Assert.IsFalse(MainWindow.OptimizerModeNeedsVtfTool(1));
        Assert.IsTrue(MainWindow.OptimizerModeNeedsVtfTool(2));
    }

    [TestMethod]
    public void MaximumModeRaisesEveryDependentPropertyNotification()
    {
        var context = new MainWindowContext();
        var changed = new HashSet<string>();
        context.PropertyChanged += (_, args) =>
        {
            if (args.PropertyName is not null)
                changed.Add(args.PropertyName);
        };

        context.OptimizerModeMaximumChecked = true;

        foreach (var property in new[]
        {
            nameof(context.OptimizerModeIsNormal),
            nameof(context.OptimizerModeIsFidelity),
            nameof(context.OptimizerModeIsMaximum),
            nameof(context.OptimizerModeNormalChecked),
            nameof(context.OptimizerModeFidelityChecked),
            nameof(context.OptimizerModeMaximumChecked),
            nameof(context.OptimizerManualTuningEnabled),
            nameof(context.OptimizerMaximumVisibility),
            nameof(context.OptimizerModeDescriptionText),
        })
        {
            Assert.IsTrue(changed.Contains(property), $"Missing notification for {property}");
        }
    }

    [TestMethod]
    public void MaximumStatusPropertiesAreObservable()
    {
        var context = new MainWindowContext();
        var changed = new List<string>();
        context.PropertyChanged += (_, args) => changed.Add(args.PropertyName ?? string.Empty);

        context.MaximumProgressText = "Family 2/5 | candidate 3/7 | visual";
        context.MaximumBestText = "Best: 42.84% | PASS";

        Assert.AreEqual("Family 2/5 | candidate 3/7 | visual", context.MaximumProgressText);
        Assert.AreEqual("Best: 42.84% | PASS", context.MaximumBestText);
        CollectionAssert.Contains(changed, nameof(context.MaximumProgressText));
        CollectionAssert.Contains(changed, nameof(context.MaximumBestText));
    }

    [TestMethod]
    public void MaximumJobsDefaultsToAutoAndIsObservable()
    {
        var context = new MainWindowContext();
        var changed = new List<string>();
        context.PropertyChanged += (_, args) => changed.Add(args.PropertyName ?? string.Empty);

        Assert.AreEqual(0, context.OptimizerMaximumJobs);
        context.OptimizerMaximumJobs = 4;

        Assert.AreEqual(4, context.OptimizerMaximumJobs);
        CollectionAssert.Contains(changed, nameof(context.OptimizerMaximumJobs));
    }
}
