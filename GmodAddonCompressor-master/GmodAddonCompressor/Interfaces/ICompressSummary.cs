namespace GmodAddonCompressor.Interfaces
{
    /// <summary>A compress service that leaves one line about its result for the caller's log.</summary>
    internal interface ICompressSummary
    {
        /// <summary>Null until the service has run.</summary>
        string? Summary { get; }
    }
}
