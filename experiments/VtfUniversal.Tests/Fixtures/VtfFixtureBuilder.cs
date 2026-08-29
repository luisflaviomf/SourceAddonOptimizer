using System.Text;

namespace VtfUniversal.Tests.Fixtures;

internal sealed class VtfFixtureFile : IDisposable
{
    internal VtfFixtureFile(string path)
    {
        Path = path;
    }

    internal string Path { get; }

    public void Dispose()
    {
        if (File.Exists(Path))
        {
            File.Delete(Path);
        }

        string? directory = System.IO.Path.GetDirectoryName(Path);
        if (directory is not null && Directory.Exists(directory))
        {
            Directory.Delete(directory);
        }
    }
}

internal static class VtfFixtureBuilder
{
    internal const int UnknownOffsetResourceTag = 0x005A5A;

    private const int TextureFlagsEnvMap = 0x00004000;
    private const int TextureFlagsNoMip = 0x00000100;
    private const int HighResolutionImageTag = 0x000030;
    private const int InlineResourceFlag = 0x02;
    private const int CrcTag = 0x435243;

    internal static VtfFixtureFile WriteCubemap74(int width, int height, int mips) =>
        Write(
            minorVersion: 4,
            width,
            height,
            flags: TextureFlagsEnvMap,
            frames: 1,
            firstFrame: 0,
            depth: 1,
            mips,
            resourceFactory: headerSize => new[]
            {
                new ResourceSpec(HighResolutionImageTag, 0, checked((uint)headerSize))
            });

    internal static VtfFixtureFile WriteResource74WithInlineCrc(uint crc, int mips = 1) =>
        Write(
            minorVersion: 4,
            width: 16,
            height: 16,
            flags: 0,
            frames: 1,
            firstFrame: 0,
            depth: 1,
            mips,
            resourceFactory: headerSize => new[]
            {
                new ResourceSpec(HighResolutionImageTag, 0, checked((uint)headerSize)),
                new ResourceSpec(CrcTag, InlineResourceFlag, crc)
            });

    internal static VtfFixtureFile WriteResource75WithMips(
        int width,
        int height,
        int mips)
    {
        const int minorVersion = 5;
        const int highResolutionFormat = 13;
        const int lowResolutionFormat = 13;
        const int lowWidth = 4;
        const int lowHeight = 4;
        const int resourceCount = 3;
        const int headerSize = 80 + resourceCount * 8;

        int lowBytes = ImageBytes(lowWidth, lowHeight);
        int highBytes = Enumerable.Range(0, mips)
            .Sum(level => ImageBytes(
                Math.Max(1, width >> level),
                Math.Max(1, height >> level)));
        uint lowOffset = headerSize;
        uint highOffset = checked((uint)(headerSize + lowBytes));
        uint unknownOffset = checked((uint)(headerSize + lowBytes + highBytes));
        byte[] unknownPayload = Enumerable.Range(0, 23)
            .Select(index => checked((byte)(0xA0 + index)))
            .ToArray();

        using var output = new MemoryStream();
        using (var writer = new BinaryWriter(output, Encoding.ASCII, leaveOpen: true))
        {
            WriteHeaderPrefix(
                writer,
                minorVersion,
                headerSize,
                width,
                height,
                flags: 0,
                frames: 1,
                firstFrame: ushort.MaxValue,
                highResolutionFormat,
                mips,
                lowResolutionFormat,
                lowWidth,
                lowHeight,
                depth: 1);
            writer.Write(resourceCount);
            while (output.Position < 80)
            {
                writer.Write((byte)0);
            }

            // Deliberately keep dictionary order different from payload order.
            WriteResource(writer, UnknownOffsetResourceTag, 0, unknownOffset);
            WriteResource(writer, HighResolutionImageTag, 0, highOffset);
            WriteResource(writer, 0x000001, 0, lowOffset);
            AssertPosition(output, headerSize);

            writer.Write(Enumerable.Repeat((byte)0xE1, lowBytes).ToArray());
            WriteHighResolutionImages(writer, width, height, depth: 1, frames: 1,
                faces: 1, mips);
            writer.Write(unknownPayload);
        }

        return Persist(output);
    }

    internal static VtfFixtureFile WriteNomip72(
        int width,
        int height,
        int highResolutionFormat,
        int frames = 1,
        int depth = 1) =>
        Write(
            minorVersion: 2,
            width,
            height,
            flags: TextureFlagsNoMip,
            frames,
            firstFrame: 0,
            depth,
            mips: 1,
            resourceFactory: null,
            highResolutionFormat: highResolutionFormat);

    internal static VtfFixtureFile WriteNomip74(
        int width,
        int height,
        int highResolutionFormat,
        int frames = 1,
        bool envMap = false,
        int lowWidth = 0,
        int lowHeight = 0)
    {
        const int minorVersion = 4;
        int flags = TextureFlagsNoMip | (envMap ? TextureFlagsEnvMap : 0);
        int firstFrame = envMap ? 0 : ushort.MaxValue;
        int faces = envMap ? 7 : 1;
        bool hasLow = lowWidth > 0 || lowHeight > 0;
        if (hasLow && (lowWidth <= 0 || lowHeight <= 0))
        {
            throw new ArgumentException("Both low-resolution dimensions are required.");
        }

        int resourceCount = hasLow ? 2 : 1;
        int headerSize = checked(80 + resourceCount * 8);
        int lowBytes = hasLow ? ImageBytes(13, lowWidth, lowHeight) : 0;
        int highBytes = checked(
            ImageBytes(highResolutionFormat, width, height) * frames * faces);
        uint lowOffset = checked((uint)headerSize);
        uint highOffset = checked((uint)(headerSize + lowBytes));

        using var output = new MemoryStream();
        using (var writer = new BinaryWriter(output, Encoding.ASCII, leaveOpen: true))
        {
            WriteHeaderPrefix(
                writer,
                minorVersion,
                headerSize,
                width,
                height,
                flags,
                frames,
                firstFrame,
                highResolutionFormat,
                mips: 1,
                lowResolutionFormat: hasLow ? 13 : -1,
                lowWidth,
                lowHeight,
                depth: 1);
            writer.Write(resourceCount);
            while (output.Position < 80)
            {
                writer.Write((byte)0);
            }

            WriteResource(writer, HighResolutionImageTag, 0, highOffset);
            if (hasLow)
            {
                WriteResource(writer, 0x000001, 0, lowOffset);
            }

            AssertPosition(output, headerSize);
            if (hasLow)
            {
                writer.Write(Enumerable.Repeat((byte)0xD2, lowBytes).ToArray());
            }

            long highStart = output.Position;
            WriteHighResolutionImages(
                writer,
                width,
                height,
                depth: 1,
                frames,
                faces,
                mips: 1,
                highResolutionFormat: highResolutionFormat);
            if (output.Position - highStart != highBytes)
            {
                throw new InvalidOperationException("NOMIP fixture high-resolution size drifted.");
            }
        }

        return Persist(output);
    }

    internal static VtfFixtureFile WriteOverlappingResources() =>
        Write(
            minorVersion: 5,
            width: 4,
            height: 4,
            flags: 0,
            frames: 1,
            firstFrame: ushort.MaxValue,
            depth: 1,
            mips: 1,
            resourceFactory: headerSize => new[]
            {
                new ResourceSpec(HighResolutionImageTag, 0, checked((uint)headerSize)),
                new ResourceSpec(0x005A5A, 0, checked((uint)(headerSize + 4)))
            });

    internal static VtfFixtureFile WriteVolumeWithFrames(
        int width,
        int height,
        int depth,
        int frames,
        int mips) =>
        Write(
            minorVersion: 2,
            width,
            height,
            flags: 0,
            frames,
            firstFrame: 0,
            depth,
            mips,
            resourceFactory: null);

    private static VtfFixtureFile Write(
        int minorVersion,
        int width,
        int height,
        int flags,
        int frames,
        int firstFrame,
        int depth,
        int mips,
        Func<int, IReadOnlyList<ResourceSpec>>? resourceFactory,
        int highResolutionFormat = 13)
    {
        IReadOnlyList<ResourceSpec> resources = Array.Empty<ResourceSpec>();
        int resourceCount = minorVersion >= 3 && resourceFactory is not null
            ? resourceFactory(0).Count
            : 0;
        int headerSize = minorVersion >= 3 ? 80 + resourceCount * 8 : 80;
        if (resourceFactory is not null)
        {
            resources = resourceFactory(headerSize);
        }

        using var output = new MemoryStream();
        using (var writer = new BinaryWriter(output, Encoding.ASCII, leaveOpen: true))
        {
            WriteHeaderPrefix(
                writer,
                minorVersion,
                headerSize,
                width,
                height,
                flags,
                frames,
                firstFrame,
                highResolutionFormat,
                mips,
                lowResolutionFormat: -1,
                lowWidth: 0,
                lowHeight: 0,
                depth);

            while (output.Position < 68)
            {
                writer.Write((byte)0);
            }

            if (minorVersion >= 3)
            {
                writer.Write(resources.Count);
                while (output.Position < 80)
                {
                    writer.Write((byte)0);
                }

                foreach (ResourceSpec resource in resources)
                {
                    WriteResource(writer, resource.Tag, resource.Flags, resource.Data);
                }
            }

            while (output.Position < headerSize)
            {
                writer.Write((byte)0);
            }

            int faces = (flags & TextureFlagsEnvMap) != 0 && minorVersion < 5 &&
                        firstFrame != ushort.MaxValue
                ? 7
                : (flags & TextureFlagsEnvMap) != 0 ? 6 : 1;
            WriteHighResolutionImages(
                writer,
                width,
                height,
                depth,
                frames,
                faces,
                mips,
                highResolutionFormat);
        }

        return Persist(output);
    }

    private static void WriteHeaderPrefix(
        BinaryWriter writer,
        int minorVersion,
        int headerSize,
        int width,
        int height,
        int flags,
        int frames,
        int firstFrame,
        int highResolutionFormat,
        int mips,
        int lowResolutionFormat,
        int lowWidth,
        int lowHeight,
        int depth)
    {
        writer.Write(new byte[] { (byte)'V', (byte)'T', (byte)'F', 0 });
        writer.Write(7);
        writer.Write(minorVersion);
        writer.Write(headerSize);
        writer.Write(checked((ushort)width));
        writer.Write(checked((ushort)height));
        writer.Write(flags);
        writer.Write(checked((ushort)frames));
        writer.Write(checked((ushort)firstFrame));
        writer.Write(0);
        writer.Write(0.25f);
        writer.Write(0.5f);
        writer.Write(0.75f);
        writer.Write(0);
        writer.Write(1.0f);
        writer.Write(highResolutionFormat);
        writer.Write(checked((byte)mips));
        writer.Write(lowResolutionFormat);
        writer.Write(checked((byte)lowWidth));
        writer.Write(checked((byte)lowHeight));
        if (minorVersion >= 2)
        {
            writer.Write(checked((ushort)depth));
        }

        while (writer.BaseStream.Position < 68)
        {
            writer.Write((byte)0);
        }
    }

    private static void WriteResource(BinaryWriter writer, int tag, byte flags, uint data)
    {
        writer.Write((byte)(tag & 0xff));
        writer.Write((byte)((tag >> 8) & 0xff));
        writer.Write((byte)((tag >> 16) & 0xff));
        writer.Write(flags);
        writer.Write(data);
    }

    private static void WriteHighResolutionImages(
        BinaryWriter writer,
        int width,
        int height,
        int depth,
        int frames,
        int faces,
        int mips,
        int highResolutionFormat = 13)
    {
        byte marker = 1;
        for (int level = mips - 1; level >= 0; level--)
        {
            int mipWidth = Math.Max(1, width >> level);
            int mipHeight = Math.Max(1, height >> level);
            int mipDepth = Math.Max(1, depth >> level);
            int bytesPerImage = ImageBytes(highResolutionFormat, mipWidth, mipHeight);
            for (int frame = 0; frame < frames; frame++)
            for (int face = 0; face < faces; face++)
            for (int slice = 0; slice < mipDepth; slice++)
            {
                writer.Write(Enumerable.Repeat(marker++, bytesPerImage).ToArray());
            }
        }
    }

    private static int ImageBytes(int width, int height) => ImageBytes(13, width, height);

    private static int ImageBytes(int format, int width, int height) =>
        format switch
        {
            13 or 20 => Math.Max(1, (width + 3) / 4) *
                        Math.Max(1, (height + 3) / 4) * 8,
            15 => Math.Max(1, (width + 3) / 4) *
                  Math.Max(1, (height + 3) / 4) * 16,
            _ => throw new ArgumentOutOfRangeException(nameof(format))
        };

    private static void AssertPosition(Stream stream, int expected)
    {
        if (stream.Position != expected)
        {
            throw new InvalidOperationException(
                $"Fixture header position {stream.Position} differs from {expected}.");
        }
    }

    private static VtfFixtureFile Persist(MemoryStream output)
    {
        string directory = System.IO.Path.Combine(
            System.IO.Path.GetTempPath(),
            "VtfUniversal.Tests",
            "VtfFixtures",
            Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        string path = System.IO.Path.Combine(directory, "fixture.vtf");
        File.WriteAllBytes(path, output.ToArray());
        return new VtfFixtureFile(path);
    }

    private sealed record ResourceSpec(int Tag, byte Flags, uint Data);
}
