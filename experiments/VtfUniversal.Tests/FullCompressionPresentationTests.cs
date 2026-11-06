using GmodAddonCompressor.Models;
using Xunit;

public class FullCompressionPresentationTests
{
    [Fact] public void NewSessionCannotExposeAnApprovedCandidate()
    {
        var view = new FullCompressionPresentation();
        Assert.True(view.IsEmpty); Assert.True(view.CanEdit); Assert.False(view.IsResult);
    }
    [Fact] public void RunningLocksEditsAndCancelDoesNotBecomeSuccess()
    {
        var view = new FullCompressionPresentation(); view.Prepare(true); view.Start();
        Assert.True(view.IsRunning); Assert.False(view.CanEdit);
        view.Cancel(); Assert.False(view.IsRunning); Assert.False(view.IsResult); Assert.True(view.CanEdit);
    }
    [Fact] public void FailureDoesNotExposeSuccessfulResult()
    {
        var view = new FullCompressionPresentation(); view.Prepare(true); view.Start(); view.Fail("disk full");
        Assert.False(view.IsResult); Assert.Contains("disk full", view.Hint); Assert.True(view.CanEdit);
    }
    [Fact] public void EditingInputInvalidatesPreviousResultAndEconomy()
    {
        var view = new FullCompressionPresentation(); view.Prepare(true); view.Start(); view.Complete("100 B");
        Assert.True(view.IsResult); Assert.Contains("GMod", view.Hint);
        view.Prepare(true); Assert.False(view.IsResult); Assert.DoesNotContain("100 B", view.Economy);
    }
}
