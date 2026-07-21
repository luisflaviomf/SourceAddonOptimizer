using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Vtf;

namespace VtfMaximumLab.Encoding;

internal static class VtfBcFileBuilder
{
    private const int TextureFlagsOneBitAlpha = 0x00001000;
    private const int TextureFlagsEightBitAlpha = 0x00002000;
    private const int LowResolutionImageResourceTag = 0x000001;
    private const int HighResolutionImageResourceTag = 0x000030;

    internal static void Build(
        string originalVtfPath,
        DdsBcDocument dds,
        string outputVtfPath,
        bool oneBitAlpha,
        bool preserveAlpha)
    {
        if (!VtfDocumentReader.TryRead(originalVtfPath, out VtfDocumentInfo original, out string error))
            throw new InvalidDataException($"Original VTF is invalid: {error}");
        if (original.MajorVersion != 7)
            throw new InvalidDataException("Exact-version rebuild only supports VTF major version 7.");
        if (original.Frames != 1 || original.Faces != 1 || original.Depth != 1)
            throw new InvalidDataException("Exact-version rebuild only supports one frame, one face, and depth one.");

        long originalPayloadEnd = original.MipLevels.Max(mip => mip.Offset + mip.ByteCount);
        if (originalPayloadEnd != original.FileLength)
            throw new InvalidDataException("Original VTF has trailing data that cannot be moved safely.");
        if (original.MinorVersion >= 3)
            ValidateSimpleResourceLayout(original);

        byte[] prefix = new byte[checked((int)original.HighResDataOffset)];
        using (FileStream source = File.OpenRead(originalVtfPath))
        {
            int read = 0;
            while (read < prefix.Length)
            {
                int count = source.Read(prefix, read, prefix.Length - read);
                if (count <= 0)
                    throw new InvalidDataException("Original VTF prefix is truncated.");
                read += count;
            }
        }

        WriteUInt16(prefix, 16, checked((ushort)dds.Width));
        WriteUInt16(prefix, 18, checked((ushort)dds.Height));
        int flags = original.Flags & ~(TextureFlagsOneBitAlpha | TextureFlagsEightBitAlpha);
        if (oneBitAlpha)
            flags |= TextureFlagsOneBitAlpha;
        else if (preserveAlpha)
            flags |= TextureFlagsEightBitAlpha;
        WriteInt32(prefix, 20, flags);
        int format = dds.Format switch
        {
            DdsBcFormat.Bc1 when oneBitAlpha => 20,
            DdsBcFormat.Bc1 => 13,
            DdsBcFormat.Bc3 => 15,
            _ => throw new InvalidDataException("Unsupported DDS format.")
        };
        WriteInt32(prefix, 52, format);
        prefix[56] = checked((byte)dds.Mips.Count);

        string fullOutputPath = Path.GetFullPath(outputVtfPath);
        Directory.CreateDirectory(Path.GetDirectoryName(fullOutputPath)!);
        using (FileStream output = File.Create(fullOutputPath))
        {
            output.Write(prefix, 0, prefix.Length);
            for (int index = dds.Mips.Count - 1; index >= 0; index--)
            {
                byte[] bytes = dds.Mips[index].Bytes;
                output.Write(bytes, 0, bytes.Length);
            }
            output.Flush(flushToDisk: true);
        }

        if (!VtfDocumentReader.TryRead(fullOutputPath, out VtfDocumentInfo rebuilt, out error))
            throw new InvalidDataException($"Rebuilt VTF failed structural validation: {error}");
        if (rebuilt.MajorVersion != original.MajorVersion || rebuilt.MinorVersion != original.MinorVersion)
            throw new InvalidDataException("Rebuilt VTF version changed unexpectedly.");
        if (rebuilt.HighResDataOffset != original.HighResDataOffset)
            throw new InvalidDataException("Rebuilt VTF high-resolution resource offset changed unexpectedly.");
    }

    private static void ValidateSimpleResourceLayout(VtfDocumentInfo original)
    {
        VtfResourceInfo[] highResolutionResources = original.Resources
            .Where(resource => resource.Tag == HighResolutionImageResourceTag)
            .ToArray();
        if (highResolutionResources.Length != 1 ||
            highResolutionResources[0].Flags != 0 ||
            highResolutionResources[0].Data != original.HighResDataOffset)
        {
            throw new InvalidDataException("VTF resource dictionary does not contain one movable high-resolution image resource.");
        }

        if (original.Resources.Any(resource =>
                resource.Flags != 0 ||
                resource.Tag is not LowResolutionImageResourceTag and not HighResolutionImageResourceTag ||
                resource.Data > original.HighResDataOffset))
        {
            throw new InvalidDataException("VTF contains extra or inline resources that cannot be preserved safely.");
        }
    }

    private static void WriteUInt16(byte[] target, int offset, ushort value)
    {
        byte[] bytes = BitConverter.GetBytes(value);
        Buffer.BlockCopy(bytes, 0, target, offset, bytes.Length);
    }

    private static void WriteInt32(byte[] target, int offset, int value)
    {
        byte[] bytes = BitConverter.GetBytes(value);
        Buffer.BlockCopy(bytes, 0, target, offset, bytes.Length);
    }
}
