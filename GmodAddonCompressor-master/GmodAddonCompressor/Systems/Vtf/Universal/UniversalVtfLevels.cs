namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal static class UniversalVtfLevels
{
    internal static UniversalVtfDimensions At(int width, int height, int level)
    {
        if (width < 1)
        {
            throw new ArgumentOutOfRangeException(nameof(width));
        }

        if (height < 1)
        {
            throw new ArgumentOutOfRangeException(nameof(height));
        }

        if (level < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(level));
        }

        int divisor = level >= 30 ? int.MaxValue : 1 << level;
        return new UniversalVtfDimensions(
            Math.Max(1, width / divisor),
            Math.Max(1, height / divisor));
    }

    internal static bool IsReducible(int width, int height)
    {
        if (width < 1)
        {
            throw new ArgumentOutOfRangeException(nameof(width));
        }

        if (height < 1)
        {
            throw new ArgumentOutOfRangeException(nameof(height));
        }

        return width > 1 || height > 1;
    }

    internal static IReadOnlyList<int> Enumerate(int width, int height)
    {
        var levels = new List<int>();
        UniversalVtfDimensions current = At(width, height, 0);
        for (int level = 1; IsReducible(current.Width, current.Height); level++)
        {
            levels.Add(level);
            current = At(width, height, level);
        }

        return levels;
    }
}
