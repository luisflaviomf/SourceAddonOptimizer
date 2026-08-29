namespace GmodAddonCompressor.Systems.Vtf.Universal;

internal sealed record UniversalVtfMonotonicOption(
    string Name,
    long Bytes,
    string Sha256,
    bool Eligible,
    string? IneligibleReason);

internal sealed record UniversalVtfMonotonicAlternativeDecision(
    UniversalVtfMonotonicOption Option,
    string Reason);

internal sealed record UniversalVtfMonotonicDecision(
    UniversalVtfMonotonicOption Incumbent,
    UniversalVtfMonotonicOption Winner,
    IReadOnlyList<UniversalVtfMonotonicAlternativeDecision> Alternatives)
{
    internal long SavedBytes => checked(Incumbent.Bytes - Winner.Bytes);
}

internal static class UniversalVtfMonotonicSelector
{
    internal static UniversalVtfMonotonicDecision Select(
        UniversalVtfMonotonicOption incumbent,
        IReadOnlyList<UniversalVtfMonotonicOption> alternatives)
    {
        ArgumentNullException.ThrowIfNull(incumbent);
        ArgumentNullException.ThrowIfNull(alternatives);
        Validate(incumbent, requireEligible: true);

        var names = new HashSet<string>(StringComparer.Ordinal);
        foreach (UniversalVtfMonotonicOption alternative in alternatives)
        {
            Validate(alternative, requireEligible: false);
            if (!names.Add(alternative.Name) ||
                alternative.Name.Equals(incumbent.Name, StringComparison.Ordinal))
            {
                throw new InvalidDataException(
                    $"monotonic_duplicate_option:{alternative.Name}");
            }
        }

        UniversalVtfMonotonicOption winner = alternatives
            .Where(option => option.Eligible && option.Bytes < incumbent.Bytes)
            .OrderBy(option => option.Bytes)
            .ThenBy(option => option.Name, StringComparer.Ordinal)
            .FirstOrDefault() ?? incumbent;

        UniversalVtfMonotonicAlternativeDecision[] decisions = alternatives
            .Select(option => new UniversalVtfMonotonicAlternativeDecision(
                option,
                Reason(option, incumbent, winner)))
            .ToArray();
        return new UniversalVtfMonotonicDecision(incumbent, winner, decisions);
    }

    private static string Reason(
        UniversalVtfMonotonicOption option,
        UniversalVtfMonotonicOption incumbent,
        UniversalVtfMonotonicOption winner)
    {
        if (!option.Eligible)
        {
            return option.IneligibleReason!;
        }

        if (ReferenceEquals(option, winner) || option == winner)
        {
            return "selected_smallest_safe";
        }

        return option.Bytes >= incumbent.Bytes
            ? "not_smaller_than_incumbent"
            : "larger_than_selected_alternative";
    }

    private static void Validate(
        UniversalVtfMonotonicOption option,
        bool requireEligible)
    {
        if (string.IsNullOrWhiteSpace(option.Name) || option.Bytes <= 0 ||
            option.Sha256 is null || option.Sha256.Length != 64 ||
            option.Sha256.Any(character => !Uri.IsHexDigit(character)) ||
            requireEligible && !option.Eligible ||
            option.Eligible && option.IneligibleReason is not null ||
            !option.Eligible && string.IsNullOrWhiteSpace(option.IneligibleReason))
        {
            throw new InvalidDataException($"monotonic_invalid_option:{option.Name}");
        }
    }
}
