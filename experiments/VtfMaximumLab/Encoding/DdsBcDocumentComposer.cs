namespace VtfMaximumLab.Encoding;

internal static class DdsBcDocumentComposer
{
    internal static DdsBcDocument WithTopMipAndTail(DdsBcDocument topSource, DdsBcDocument tailSource)
    {
        if (topSource.Format != tailSource.Format)
            throw new InvalidDataException("BC formats differ between the current top mip and original mip tail.");
        if (topSource.Mips.Count == 0 || tailSource.Mips.Count == 0)
            throw new InvalidDataException("A hybrid BC mip chain cannot be empty.");
        DdsBcMip top = topSource.Mips[0];
        if (Math.Max(1, top.Width / 2) != tailSource.Width ||
            Math.Max(1, top.Height / 2) != tailSource.Height)
        {
            throw new InvalidDataException("Original mip tail does not immediately follow the current top mip dimensions.");
        }

        var mips = new List<DdsBcMip>(1 + tailSource.Mips.Count)
        {
            new(0, top.Width, top.Height, (byte[])top.Bytes.Clone())
        };
        mips.AddRange(tailSource.Mips.Select(mip =>
            new DdsBcMip(mip.Level + 1, mip.Width, mip.Height, (byte[])mip.Bytes.Clone())));
        return new DdsBcDocument(top.Width, top.Height, topSource.Format, mips);
    }
}
