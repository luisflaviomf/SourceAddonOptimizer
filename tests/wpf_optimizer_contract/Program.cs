using GmodAddonCompressor.Systems.Optimizer;

static void Assert(bool condition, string message)
{
    if (!condition)
        throw new InvalidOperationException(message);
}

var options = new SourceAddonOptimizerRunOptions
{
    AddonPath = @"C:\addon",
    WorkDir = @"C:\work",
    OptimizerMode = "maximum",
    Ratio = 0.75,
    RestoreSkins = true
};
var arguments = SourceAddonOptimizerCommandBuilder.BuildArguments(options);
Assert(arguments.Contains("maximum"), "Maximum mode missing from worker arguments.");
Assert(arguments.Contains("0.75"), "Invariant ratio missing from worker arguments.");

var parser = new SourceAddonOptimizerProgressParser();
var update = parser.Parse("[MAXIMUM] stage=adaptive-simplification current=3 total=6 detail=wheel")
    ?? throw new InvalidOperationException("Maximum stage was not parsed.");
Assert(update.MaximumStage == "adaptive-simplification", "Maximum stage was not parsed.");
Assert(update.ItemIndex == 3 && update.ItemTotal == 6, "Maximum counts were not parsed.");
Assert(update.ItemPath == "wheel", "Maximum detail was not parsed.");
Assert(parser.Parse("[MAXIMUM] stage=unknown current=1 total=6 detail=x") == null, "Unknown Maximum stage must fail closed.");
Assert(parser.Parse("[MAXIMUM] stage=packaging current=x total=6 detail=x") == null, "Malformed Maximum counts must fail closed.");

Console.WriteLine("WPF optimizer contract tests passed.");
