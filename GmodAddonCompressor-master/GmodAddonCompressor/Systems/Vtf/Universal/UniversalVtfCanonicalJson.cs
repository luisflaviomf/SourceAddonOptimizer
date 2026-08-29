using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal static class UniversalVtfCanonicalJson
{
    private static readonly JsonSerializerOptions Options = CreateOptions();

    internal static JsonSerializerOptions SerializerOptions => Options;

    internal static byte[] Serialize<T>(T value)
    {
        byte[] serialized = JsonSerializer.SerializeToUtf8Bytes(value, Options);
        using JsonDocument document = JsonDocument.Parse(serialized);
        using var output = new MemoryStream();
        using (var writer = new Utf8JsonWriter(output))
        {
            WriteCanonical(writer, document.RootElement);
        }

        return output.ToArray();
    }

    internal static T Deserialize<T>(ReadOnlySpan<byte> bytes)
    {
        T value;
        try
        {
            value = JsonSerializer.Deserialize<T>(bytes, Options)
                ?? throw new InvalidDataException("universal_vtf_null_json");
        }
        catch (JsonException error)
        {
            throw new InvalidDataException("universal_vtf_invalid_json", error);
        }

        byte[] canonical = Serialize(value);
        if (!bytes.SequenceEqual(canonical))
        {
            throw new InvalidDataException("universal_vtf_noncanonical_json");
        }

        return value;
    }

    internal static string Sha256(ReadOnlySpan<byte> bytes) =>
        Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    private static JsonSerializerOptions CreateOptions()
    {
        var options = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            PropertyNameCaseInsensitive = false,
            WriteIndented = false,
            NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals
        };
        options.Converters.Add(new UniversalVtfManifestDoubleConverter());
        options.Converters.Add(new JsonStringEnumConverter(JsonNamingPolicy.CamelCase));
        return options;
    }

    private static void WriteCanonical(Utf8JsonWriter writer, JsonElement element)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                foreach (JsonProperty property in element.EnumerateObject()
                             .OrderBy(property => property.Name, StringComparer.Ordinal))
                {
                    writer.WritePropertyName(property.Name);
                    WriteCanonical(writer, property.Value);
                }

                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (JsonElement item in element.EnumerateArray())
                {
                    WriteCanonical(writer, item);
                }

                writer.WriteEndArray();
                break;
            case JsonValueKind.String:
                writer.WriteStringValue(element.GetString());
                break;
            case JsonValueKind.Number:
                element.WriteTo(writer);
                break;
            case JsonValueKind.True:
                writer.WriteBooleanValue(true);
                break;
            case JsonValueKind.False:
                writer.WriteBooleanValue(false);
                break;
            case JsonValueKind.Null:
                writer.WriteNullValue();
                break;
            default:
                throw new InvalidDataException(
                    $"universal_vtf_unsupported_json:{element.ValueKind}");
        }
    }
}

internal sealed class UniversalVtfManifestDoubleConverter : JsonConverter<double>
{
    public override bool HandleNull => true;

    public override double Read(
        ref Utf8JsonReader reader,
        Type typeToConvert,
        JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.Null)
        {
            return double.NaN;
        }

        if (reader.TokenType == JsonTokenType.Number)
        {
            return reader.GetDouble();
        }

        if (reader.TokenType == JsonTokenType.String)
        {
            return reader.GetString() switch
            {
                "Infinity" => double.PositiveInfinity,
                "-Infinity" => double.NegativeInfinity,
                _ => throw new JsonException("manifest_invalid_named_double")
            };
        }

        throw new JsonException($"manifest_invalid_double_token:{reader.TokenType}");
    }

    public override void Write(
        Utf8JsonWriter writer,
        double value,
        JsonSerializerOptions options)
    {
        if (double.IsNaN(value))
        {
            writer.WriteNullValue();
            return;
        }

        if (double.IsPositiveInfinity(value))
        {
            writer.WriteStringValue("Infinity");
            return;
        }

        if (double.IsNegativeInfinity(value))
        {
            writer.WriteStringValue("-Infinity");
            return;
        }

        writer.WriteNumberValue(value);
    }
}
