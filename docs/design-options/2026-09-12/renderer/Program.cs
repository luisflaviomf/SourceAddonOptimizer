using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Markup;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("DesignRenderer <dictionary.xaml> <new-output-folder> [width height]");
            return 2;
        }
        try
        {
            string input = Path.GetFullPath(args[0]), output = Path.GetFullPath(args[1]);
            if (Directory.Exists(output) || File.Exists(output)) throw new IOException("Use a new evidence folder.");
            int width = args.Length > 2 ? int.Parse(args[2]) : 1280;
            int height = args.Length > 3 ? int.Parse(args[3]) : 720;
            var app = new Application { ShutdownMode = ShutdownMode.OnExplicitShutdown };
            using var stream = File.OpenRead(input);
            var resources = (ResourceDictionary)XamlReader.Load(stream);
            app.Resources.MergedDictionaries.Add(resources);
            Directory.CreateDirectory(output);
            var reports = new List<object>();
            var states = new List<(string, string)> { ("setup", "SetupView"), ("running", "RunningView"), ("result", "ResultView") };
            if (resources.Contains("EmptyView")) states.Insert(0, ("empty", "EmptyView"));
            foreach (var pair in states)
            {
                if (resources[pair.Item2] is not DataTemplate template) throw new InvalidDataException("Missing " + pair.Item2);
                var control = new ContentControl
                {
                    Content = new { State = pair.Item1 }, ContentTemplate = template,
                    Width = width, Height = height, HorizontalContentAlignment = HorizontalAlignment.Stretch,
                    VerticalContentAlignment = VerticalAlignment.Stretch, FontFamily = new FontFamily("Segoe UI"),
                    UseLayoutRounding = true, SnapsToDevicePixels = true
                };
                TextOptions.SetTextFormattingMode(control, TextFormattingMode.Display);
                control.Measure(new Size(width, height));
                control.Arrange(new Rect(0, 0, width, height));
                control.UpdateLayout();
                app.Dispatcher.Invoke(() => { }, DispatcherPriority.ContextIdle);
                control.UpdateLayout();
                var bitmap = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
                bitmap.Render(control);
                var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(bitmap));
                using (var file = new FileStream(Path.Combine(output, pair.Item1 + ".png"), FileMode.CreateNew)) encoder.Save(file);
                var texts = new List<object>();
                void Visit(DependencyObject node)
                {
                    if (node is UIElement element && element.Visibility != Visibility.Visible) return;
                    if (node is TextBlock text && text.ActualWidth > 0 && text.ActualHeight > 0 && !string.IsNullOrWhiteSpace(text.Text))
                    {
                        var origin = text.TransformToAncestor(control).Transform(new Point());
                        texts.Add(new { text = text.Text, x = origin.X, y = origin.Y, width = text.ActualWidth, height = text.ActualHeight, font = text.FontSize });
                    }
                    for (int index = 0; index < VisualTreeHelper.GetChildrenCount(node); index++) Visit(VisualTreeHelper.GetChild(node, index));
                }
                Visit(control);
                if (texts.Count < 10) throw new InvalidDataException("Rendered screen unexpectedly empty: " + pair.Item1);
                reports.Add(new { state = pair.Item1, width, height, text_count = texts.Count, texts });
                Console.WriteLine($"Rendered {pair.Item1}: {width}x{height}, {texts.Count} text elements");
            }
            File.WriteAllText(Path.Combine(output, "render-report.json"), JsonSerializer.Serialize(reports, new JsonSerializerOptions { WriteIndented = true }));
            app.Shutdown();
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
