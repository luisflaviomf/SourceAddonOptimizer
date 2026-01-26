using System;
using System.Text;
using System.Windows;

namespace GmodAddonCompressor
{
    public partial class DebugConsoleWindow : Window
    {
        private const int MaxChars = 300_000;

        public event EventHandler? HideRequested;

        public DebugConsoleWindow()
        {
            InitializeComponent();

            Button_Clear.Click += (_, _) => TextBox_Log.Clear();
            Button_Copy.Click += (_, _) =>
            {
                try
                {
                    Clipboard.SetText(TextBox_Log.Text ?? string.Empty);
                }
                catch
                {
                }
            };
            Button_Hide.Click += (_, _) => HideRequested?.Invoke(this, EventArgs.Empty);

            Closing += (_, e) =>
            {
                e.Cancel = true;
                HideRequested?.Invoke(this, EventArgs.Empty);
            };
        }

        public void Append(string text)
        {
            if (string.IsNullOrEmpty(text))
                return;

            if (!Dispatcher.CheckAccess())
            {
                Dispatcher.BeginInvoke(new Action(() => Append(text)));
                return;
            }

            TextBox_Log.AppendText(text);

            if (TextBox_Log.Text.Length > MaxChars)
            {
                var keep = TextBox_Log.Text[^MaxChars..];
                TextBox_Log.Text = keep;
                TextBox_Log.CaretIndex = TextBox_Log.Text.Length;
            }

            TextBox_Log.ScrollToEnd();
        }
    }
}

