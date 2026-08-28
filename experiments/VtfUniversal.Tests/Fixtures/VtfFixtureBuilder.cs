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
    private const int TextureFlagsEnvMap = 0x00004000;
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

    internal static VtfFixtureFile WriteResource74WithInlineCrc(uint crc) =>
        Write(
            minorVersion: 4,
            width: 4,
            height: 4,
            flags: 0,
            frames: 1,
            firstFrame: 0,
            depth: 1,
            mips: 1,
            resourceFactory: headerSize => new[]
            {
                new ResourceSpec(HighResolutionImageTag, 0, checked((uint)headerSize)),
                new ResourceSpec(CrcTag, InlineResourceFlag, crc)
            });

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
        Func<int, IReadOnlyList<ResourceSpec>>? resourceFactory)
    {
        const int highResolutionFormat = 13;
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
            writer.Write(-1);
            writer.Write((byte)0);
            writer.Write((byte)0);
            if (minorVersion >= 2)
            {
                writer.Write(checked((ushort)depth));
            }

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
                    writer.Write((byte)(resource.Tag & 0xff));
                    writer.Write((byte)((resource.Tag >> 8) & 0xff));
                    writer.Write((byte)((resource.Tag >> 16) & 0xff));
                    writer.Write(resource.Flags);
                    writer.Write(resource.Data);
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
            byte marker = 1;
            for (int level = mips - 1; level >= 0; level--)
            {
                int mipWidth = Math.Max(1, width >> level);
                int mipHeight = Math.Max(1, height >> level);
                int mipDepth = Math.Max(1, depth >> level);
                int bytesPerImage = Math.Max(1, (mipWidth + 3) / 4) *
                                    Math.Max(1, (mipHeight + 3) / 4) * 8;
                for (int frame = 0; frame < frames; frame++)
                for (int face = 0; face < faces; face++)
                for (int slice = 0; slice < mipDepth; slice++)
                {
                    writer.Write(Enumerable.Repeat(marker++, bytesPerImage).ToArray());
                }
            }
        }

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
