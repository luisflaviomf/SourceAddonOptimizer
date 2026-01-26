using System;
using System.Globalization;
using System.Windows.Data;

namespace GmodAddonCompressor.CustomExtensions
{
    public sealed class TextContainsConverter : IValueConverter
    {
        public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
        {
            var text = value as string;
            var needle = parameter as string;

            if (string.IsNullOrWhiteSpace(text) || string.IsNullOrWhiteSpace(needle))
                return false;

            return text.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        {
            throw new NotSupportedException();
        }
    }
}
