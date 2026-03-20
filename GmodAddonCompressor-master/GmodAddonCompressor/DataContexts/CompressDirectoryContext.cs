namespace GmodAddonCompressor.DataContexts
{
    internal class CompressDirectoryContext
    {
        private static string _directoryPath = string.Empty;

        internal static string DirectoryPath
        {
            set
            {
                _directoryPath = value;

                if (!_directoryPath.EndsWith("\\"))
                    _directoryPath += "\\";
            }
            get { return _directoryPath; }
        }

        internal static string ToLocal(string fullPath)
        {
            if (string.IsNullOrWhiteSpace(fullPath))
                return string.Empty;

            if (string.IsNullOrWhiteSpace(_directoryPath))
                return fullPath;

            return fullPath.Replace(_directoryPath, string.Empty);
        }
    }
}
