using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace GmodAddonCompressor.Interfaces
{
    internal interface ICompressBatch
    {
        Task CompressBatchAsync(
            IReadOnlyList<FileInfo> files,
            CancellationToken cancellationToken);
    }
}
