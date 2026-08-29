using System.Security.Cryptography;
using System.Text;

namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal static class UniversalVtfSemanticProof
{
    internal static string Compute(UniversalVtfSemanticDecision semantics)
    {
        ArgumentNullException.ThrowIfNull(semantics);
        var lines = new List<string>
        {
            $"route={semantics.Route}",
            $"unknown={semantics.HasUnknownConsumer}",
            $"alpha={semantics.AlphaTestReference:R}"
        };
        lines.AddRange(semantics.Classes.OrderBy(value => value).Select(value => $"class={value}"));
        foreach (VtfConsumerEvidence consumer in semantics.Consumers
                     .OrderBy(value => value.ConsumerPath, StringComparer.Ordinal)
                     .ThenBy(value => value.Key, StringComparer.Ordinal))
        {
            lines.Add(
                $"consumer={consumer.ConsumerPath}|{consumer.Shader}|{consumer.Key}|" +
                $"{consumer.TextureKey}|{consumer.Dynamic}|{consumer.ResolutionTrace}|" +
                string.Join(',', consumer.ReadChannels.OrderBy(value => value)));
            foreach ((string key, IReadOnlyList<string> values) in consumer.Parameters
                         .OrderBy(pair => pair.Key, StringComparer.OrdinalIgnoreCase))
            {
                lines.Add($"parameter={key.ToLowerInvariant()}={string.Join('|', values)}");
            }
        }

        lines.AddRange(semantics.Uncertainties
            .OrderBy(value => value, StringComparer.Ordinal)
            .Select(value => $"uncertainty={value}"));
        return Convert.ToHexString(SHA256.HashData(
                Encoding.UTF8.GetBytes(string.Join('\n', lines))))
            .ToLowerInvariant();
    }
}
