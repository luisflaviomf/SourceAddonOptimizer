using System;
using System.IO;

namespace GmodAddonCompressor.Interfaces
{
    /// <summary>
    /// A service that takes its whole list only in some modes (WAV: the adaptive profile, where the ffmpeg
    /// work of many sounds goes in the same process) and reports its files one by one as they finish.
    /// </summary>
    internal interface ICompressBatchSelective : ICompressBatch
    {
        /// <summary>False: the files go one by one through ICompress.Compress, as for any other service.</summary>
        bool UseBatch { get; }

        /// <summary>Set by the compress system before the batch: called once per file when it is done.</summary>
        Action<FileInfo>? FileCompleted { get; set; }
    }
}
