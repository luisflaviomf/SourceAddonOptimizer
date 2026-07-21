using System.Threading.Tasks;

namespace GmodAddonCompressor.Interfaces
{
    internal interface ICompressFinalizer
    {
        Task CompleteAsync();
    }
}
