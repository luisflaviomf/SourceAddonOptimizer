namespace VtfUniversalCorpusDriver;

internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length == 0 || args.Contains("--help", StringComparer.OrdinalIgnoreCase))
        {
            Console.WriteLine("VtfUniversalCorpusDriver --help");
            return 0;
        }

        Console.Error.WriteLine($"Unknown command: {args[0]}");
        return 2;
    }
}
