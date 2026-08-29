using System.Text;
using GmodAddonCompressor.Systems.Vtf.Universal;
using Xunit;

namespace VtfUniversal.Tests;

public sealed class UniversalVtfCanonicalJsonTests
{
    [Fact]
    public void SerializeOrdersPropertiesAndDictionaryKeysCanonically()
    {
        var value = new CanonicalFixture(
            new Dictionary<string, double>
            {
                ["z"] = 2,
                ["a"] = 1
            },
            0);

        string json = Encoding.UTF8.GetString(UniversalVtfCanonicalJson.Serialize(value));

        Assert.Equal("{\"margin\":0,\"values\":{\"a\":1,\"z\":2}}", json);
    }

    [Fact]
    public void NanRoundTripsAsCanonicalJsonNull()
    {
        var value = new CanonicalFixture(
            new Dictionary<string, double> { ["metric"] = double.NaN },
            0);

        byte[] first = UniversalVtfCanonicalJson.Serialize(value);
        CanonicalFixture loaded = UniversalVtfCanonicalJson.Deserialize<CanonicalFixture>(first);
        byte[] second = UniversalVtfCanonicalJson.Serialize(loaded);

        Assert.Equal("{\"margin\":0,\"values\":{\"metric\":null}}",
            Encoding.UTF8.GetString(first));
        Assert.True(double.IsNaN(loaded.Values["metric"]));
        Assert.Equal(first, second);
    }

    [Fact]
    public void InfinitiesRoundTripAsNamedValues()
    {
        var value = new CanonicalFixture(
            new Dictionary<string, double> { ["positive"] = double.PositiveInfinity },
            double.NegativeInfinity);

        byte[] bytes = UniversalVtfCanonicalJson.Serialize(value);
        CanonicalFixture loaded = UniversalVtfCanonicalJson.Deserialize<CanonicalFixture>(bytes);

        Assert.Equal(
            "{\"margin\":\"-Infinity\",\"values\":{\"positive\":\"Infinity\"}}",
            Encoding.UTF8.GetString(bytes));
        Assert.True(double.IsNegativeInfinity(loaded.Margin));
        Assert.True(double.IsPositiveInfinity(loaded.Values["positive"]));
    }

    [Fact]
    public void DeserializeRejectsNamedNan()
    {
        byte[] bytes = Encoding.UTF8.GetBytes(
            "{\"margin\":0,\"values\":{\"metric\":\"NaN\"}}");

        InvalidDataException error = Assert.Throws<InvalidDataException>(() =>
            UniversalVtfCanonicalJson.Deserialize<CanonicalFixture>(bytes));

        Assert.Equal("universal_vtf_invalid_json", error.Message);
    }

    private sealed record CanonicalFixture(
        IReadOnlyDictionary<string, double> Values,
        double Margin);
}
