using System;
using System.Globalization;
using System.Windows.Data;

namespace GmodAddonCompressor.CustomExtensions
{
    /// <summary>
    /// Binds a group of RadioButtons/chips to one string property: checked when the value equals the
    /// ConverterParameter; checking a button writes its parameter back.
    /// </summary>
    public sealed class ValueEqualsConverter : IValueConverter
    {
        public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
            string.Equals(value?.ToString(), parameter?.ToString(), StringComparison.Ordinal);

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
            value is true ? parameter?.ToString() ?? string.Empty : Binding.DoNothing;
    }
}
