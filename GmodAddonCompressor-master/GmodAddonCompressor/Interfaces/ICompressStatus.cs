using System;

namespace GmodAddonCompressor.Interfaces
{
    /// <summary>
    /// A batch service that says what it is doing while it runs (the texture engine works for minutes on
    /// the whole list and reports its files only at the end: without this the screen kept showing the last
    /// sound that finished).
    /// </summary>
    internal interface ICompressStatus
    {
        /// <summary>Set by the compress system before the batch: a short line for the screen.</summary>
        Action<string>? StatusChanged { get; set; }
    }
}
