using System;
using System.Globalization;
using System.Windows.Data;

namespace GmodAddonCompressor.CustomExtensions
{
    /// <summary>
    /// Binds a group of RadioButtons/chips to one property (string or int): checked when the value equals
    /// the ConverterParameter; checking a button writes its parameter back, converted to the property type.
    /// </summary>
    public sealed class ValueEqualsConverter : IValueConverter
    {
        public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
            string.Equals(System.Convert.ToString(value, CultureInfo.InvariantCulture), parameter?.ToString(), StringComparison.Ordinal);

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        {
            if (value is not true)
                return Binding.DoNothing;
            string text = parameter?.ToString() ?? string.Empty;
            if (targetType == typeof(int) || targetType == typeof(int?))
                return int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out int number) ? number : Binding.DoNothing;
            return text;
        }
    }
}
