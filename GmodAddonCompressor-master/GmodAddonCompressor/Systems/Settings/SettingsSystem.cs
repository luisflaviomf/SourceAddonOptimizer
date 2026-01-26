using GmodAddonCompressor.Models;
using GmodAddonCompressor.Systems.Tools;
using System;
using System.IO;
using System.Text.Json;

namespace GmodAddonCompressor.Systems.Settings
{
    internal static class SettingsSystem
    {
        private const int CurrentSchemaVersion = 1;
        internal static string SettingsPath => Path.Combine(ToolPaths.AppDataRoot, "settings.json");

        internal static AppSettingsModel Load()
        {
            try
            {
                if (!File.Exists(SettingsPath))
                    return new AppSettingsModel();

                var json = File.ReadAllText(SettingsPath);
                var settings = JsonSerializer.Deserialize<AppSettingsModel>(json);
                if (settings == null)
                    return new AppSettingsModel();
                if (settings.SchemaVersion <= 0)
                    settings.SchemaVersion = CurrentSchemaVersion;
                return settings;
            }
            catch
            {
                return new AppSettingsModel();
            }
        }

        internal static void Save(AppSettingsModel settings)
        {
            try
            {
                Directory.CreateDirectory(ToolPaths.AppDataRoot);
                settings.SchemaVersion = CurrentSchemaVersion;
                var json = JsonSerializer.Serialize(settings, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(SettingsPath, json);
            }
            catch
            {
                // Avoid crashing on settings save errors.
            }
        }
    }
}
