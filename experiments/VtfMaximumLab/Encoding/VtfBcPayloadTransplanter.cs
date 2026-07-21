using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;

namespace VtfMaximumLab.Encoding;

internal static class VtfBcPayloadTransplanter
{
    internal static void Replace(string vtfPath, DdsBcDocument dds)
    {
        if (!VtfDocumentReader.TryRead(vtfPath, out VtfDocumentInfo document, out string error))
            throw new InvalidDataException($"Canonical VTF is invalid: {error}");
        if (document.Frames != 1 || document.Faces != 1 || document.Depth != 1)
            throw new InvalidDataException("Payload transplant only supports one frame, one face, and depth one.");
        if (document.Width != dds.Width || document.Height != dds.Height)
            throw new InvalidDataException("DDS dimensions do not match the canonical VTF.");
        if (document.MipCount != dds.Mips.Count)
            throw new InvalidDataException("DDS mip count does not match the canonical VTF.");

        bool formatMatches = dds.Format switch
        {
            DdsBcFormat.Bc1 => document.HighResFormat is 13 or 20,
            DdsBcFormat.Bc3 => document.HighResFormat == 15,
            _ => false
        };
        if (!formatMatches)
            throw new InvalidDataException("DDS BC format does not match the canonical VTF.");

        using FileStream stream = File.Open(vtfPath, FileMode.Open, FileAccess.ReadWrite, FileShare.None);
        foreach (VtfMipLevelInfo vtfMip in document.MipLevels)
        {
            DdsBcMip ddsMip = dds.Mips[vtfMip.Level];
            if (ddsMip.Width != vtfMip.Width || ddsMip.Height != vtfMip.Height || ddsMip.Bytes.Length != vtfMip.ByteCount)
                throw new InvalidDataException($"DDS mip {vtfMip.Level} does not match the VTF byte layout.");
            stream.Position = vtfMip.Offset;
            stream.Write(ddsMip.Bytes, 0, ddsMip.Bytes.Length);
        }
        stream.Flush(flushToDisk: true);
    }
}
