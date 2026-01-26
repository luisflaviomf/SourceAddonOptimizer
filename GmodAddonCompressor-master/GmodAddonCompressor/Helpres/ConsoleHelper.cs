using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows;

namespace GmodAddonCompressor.Helpres
{
    internal static class ConsoleHelper
    {
        private static readonly object Gate = new object();

        private static DebugConsoleWindow? _window;
        private static TextWriter? _originalOut;
        private static TextWriter? _originalErr;
        private static TextWriter? _redirectWriter;
        private static TraceListener? _traceListener;
        private static bool _enabled;

        public static bool TryAllocConsole(out string? error, Window? owner = null, Action? onHideRequested = null)
        {
            lock (Gate)
            {
                try
                {
                    EnsureWindow(owner, onHideRequested);
                    EnableRedirection();

                    _window!.Show();
                    _window.Activate();

                    error = null;
                    return true;
                }
                catch (Exception ex)
                {
                    error = ex.ToString();
                    TryWriteLog("open", ex);
                    return false;
                }
            }
        }

        public static bool TryFreeConsole(out string? error)
        {
            lock (Gate)
            {
                try
                {
                    DisableRedirection();
                    _window?.Hide();

                    error = null;
                    return true;
                }
                catch (Exception ex)
                {
                    error = ex.ToString();
                    TryWriteLog("close", ex);
                    return false;
                }
            }
        }

        private static void EnsureWindow(Window? owner, Action? onHideRequested)
        {
            if (_window != null)
            {
                if (owner != null)
                    _window.Owner = owner;

                return;
            }

            _window = new DebugConsoleWindow
            {
                Owner = owner
            };

            _window.HideRequested += (_, _) =>
            {
                _window.Hide();
                onHideRequested?.Invoke();
            };
        }

        private static void EnableRedirection()
        {
            if (_enabled)
                return;

            _originalOut = Console.Out;
            _originalErr = Console.Error;

            _redirectWriter = new DebugConsoleTextWriter(text =>
            {
                try
                {
                    _window?.Append(text);
                }
                catch
                {
                }
            });

            Console.SetOut(_redirectWriter);
            Console.SetError(_redirectWriter);

            Trace.AutoFlush = true;
            _traceListener = new TextWriterTraceListener(_redirectWriter) { Name = "DebugConsole" };
            Trace.Listeners.Add(_traceListener);

            _enabled = true;
        }

        private static void DisableRedirection()
        {
            if (!_enabled)
                return;

            try
            {
                if (_traceListener != null)
                {
                    Trace.Listeners.Remove(_traceListener);
                    _traceListener.Dispose();
                    _traceListener = null;
                }
            }
            catch
            {
            }

            try
            {
                Console.SetOut(_originalOut ?? TextWriter.Null);
            }
            catch
            {
            }

            try
            {
                Console.SetError(_originalErr ?? TextWriter.Null);
            }
            catch
            {
            }

            _redirectWriter = null;
            _originalOut = null;
            _originalErr = null;
            _enabled = false;
        }

        private static void TryWriteLog(string action, Exception ex)
        {
            try
            {
                var baseDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "GmodAddonCompressor",
                    "logs");
                Directory.CreateDirectory(baseDir);
                var logPath = Path.Combine(baseDir, "debug_console.log");
                var line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} [{action}] {ex}{Environment.NewLine}";
                File.AppendAllText(logPath, line);
            }
            catch
            {
            }
        }

        private sealed class DebugConsoleTextWriter : TextWriter
        {
            private readonly Action<string> _write;

            public DebugConsoleTextWriter(Action<string> write)
            {
                _write = write;
            }

            public override Encoding Encoding => Encoding.UTF8;

            public override void Write(char value) => _write(value.ToString());

            public override void Write(string? value)
            {
                if (!string.IsNullOrEmpty(value))
                    _write(value);
            }

            public override void WriteLine(string? value)
            {
                _write((value ?? string.Empty) + Environment.NewLine);
            }
        }
    }
}

