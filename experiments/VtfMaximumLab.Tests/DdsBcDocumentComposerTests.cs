using VtfMaximumLab.Encoding;
using Xunit;

namespace VtfMaximumLab.Tests;

public sealed class DdsBcDocumentComposerTests
{
    [Fact]
    public void CombinesCurrentTopWithDimensionMatchedOriginalTail()
    {
        var current = new DdsBcDocument(8, 8, DdsBcFormat.Bc3, new[]
        {
            new DdsBcMip(0, 8, 8, new byte[] { 1 }),
            new DdsBcMip(1, 4, 4, new byte[] { 2 })
        });
        var tail = new DdsBcDocument(4, 4, DdsBcFormat.Bc3, new[]
        {
            new DdsBcMip(0, 4, 4, new byte[] { 7 }),
            new DdsBcMip(1, 2, 2, new byte[] { 8 })
        });

        DdsBcDocument hybrid = DdsBcDocumentComposer.WithTopMipAndTail(current, tail);

        Assert.Equal((8, 8, 3), (hybrid.Width, hybrid.Height, hybrid.Mips.Count));
        Assert.Equal(new byte[] { 1, 7, 8 }, hybrid.Mips.SelectMany(mip => mip.Bytes));
        Assert.Equal(new[] { 0, 1, 2 }, hybrid.Mips.Select(mip => mip.Level));
    }
}
