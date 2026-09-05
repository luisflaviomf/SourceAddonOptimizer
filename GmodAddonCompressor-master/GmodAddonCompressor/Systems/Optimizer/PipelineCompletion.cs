namespace GmodAddonCompressor.Systems.Optimizer
{
    internal enum PipelineOutcome { Failed, Canceled, ReadyForExact, Success }

    internal static class PipelineCompletion
    {
        internal static PipelineOutcome Evaluate(bool modelsOk, bool compressOk, bool canceled, bool exactRequested, ExactDistributionResult? exact)
        {
            if (canceled) return PipelineOutcome.Canceled;
            if (!modelsOk || !compressOk) return PipelineOutcome.Failed;
            if (!exactRequested) return PipelineOutcome.Success;
            if (exact == null) return PipelineOutcome.ReadyForExact;
            return exact.Success ? PipelineOutcome.Success : PipelineOutcome.Failed;
        }

        internal static string? FinalOutput(string? nativeOutput, ExactDistributionResult? exact) =>
            exact?.Success == true ? exact.OutputPath : nativeOutput;
    }
}
