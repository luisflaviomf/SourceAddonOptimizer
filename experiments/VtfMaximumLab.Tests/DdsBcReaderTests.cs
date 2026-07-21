using VtfMaximumLab.Encoding;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class DdsBcReaderTests
{
    [Theory]
    [InlineData("DXT1", 8)]
    [InlineData("DXT5", 16)]
    public void ReadsLegacyBcMipChain(string fourCc, int blockBytes)
    {
        string path = DdsFixtureBuilder.Write(8, 8, fourCc, 4);

        DdsBcDocument document = DdsBcReader.Read(path);

        Assert.Equal(8, document.Width);
        Assert.Equal(4, document.Mips.Count);
        Assert.Equal(blockBytes * 4, document.Mips[0].Bytes.Length);
        Assert.Equal(1, document.Mips[0].Bytes[0]);
    }
}
