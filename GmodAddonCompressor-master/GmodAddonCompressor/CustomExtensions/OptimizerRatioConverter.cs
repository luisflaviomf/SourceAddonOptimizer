using System;
using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace GmodAddonCompressor.CustomExtensions
{
    public sealed class OptimizerRatioConverter : IValueConverter
    {
        private const double MinRatio = 0.01;
        private const double MaxRatio = 1.00;

        public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
        {
            if (value is double ratio)
                return ratio.ToString("0.00", CultureInfo.InvariantCulture);

            return MinRatio.ToString("0.00", CultureInfo.InvariantCulture);
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        {
            var text = value as string;
            if (string.IsNullOrWhiteSpace(text))
                return MinRatio;

            var trimmed = text.Trim();
            var normalized = trimmed.Replace(',', '.');

            if (double.TryParse(normalized, NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed))
                return Math.Clamp(parsed, MinRatio, MaxRatio);

            if (double.TryParse(trimmed, NumberStyles.Float, culture, out parsed))
                return Math.Clamp(parsed, MinRatio, MaxRatio);

            return DependencyProperty.UnsetValue;
        }
    }
}
