using GmodAddonCompressor.Systems.Optimizer;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace GmodAddonCompressor.Tests;

[TestClass]
public sealed class SourceAddonOptimizerProgressParserTests
{
    private readonly SourceAddonOptimizerProgressParser _parser = new();

    [TestMethod]
    public void SchedulerStatusParsesAggregateProgress()
    {
        var update = _parser.Parse(
            "MAXIMUM_EVENT {\"schema\":1,\"kind\":\"scheduler_status\",\"active_families\":8,\"completed_families\":24,\"family_total\":163,\"effective_jobs\":10,\"memory_throttled\":false}");

        Assert.IsNotNull(update);
        Assert.AreEqual(8, update.ActiveFamilies);
        Assert.AreEqual(24, update.CompletedFamilies);
        Assert.AreEqual(10, update.EffectiveMaximumJobs);
        Assert.AreEqual(false, update.MemoryThrottled);
    }

    [TestMethod]
    [DataRow("active_families", "-1")]
    [DataRow("completed_families", "1.5")]
    [DataRow("effective_jobs", "0")]
    [DataRow("memory_throttled", "\"false\"")]
    public void RejectsMalformedSchedulerStatusFields(string field, string value)
    {
        var line = $"MAXIMUM_EVENT {{\"schema\":1,\"kind\":\"scheduler_status\",\"{field}\":{value}}}";

        Assert.IsNull(_parser.Parse(line));
    }

    [TestMethod]
    public void ParsesEveryMaximumFieldWithExactTypes()
    {
        var update = _parser.Parse(
            "MAXIMUM_EVENT {\"schema\":1,\"kind\":\"best_updated\",\"family_id\":\"abc\",\"family_index\":2,\"family_total\":7,\"candidate_id\":\"r035\",\"candidate_index\":3,\"candidate_total\":19,\"stage\":\"visual\",\"best_bytes\":600,\"reduction_percent\":40.25,\"gate_status\":\"PASS\",\"report_path\":\"logs/maximum_report.json\"}");

        Assert.IsNotNull(update);
        Assert.AreEqual("best_updated", update.MaximumKind);
        Assert.AreEqual("abc", update.FamilyId);
        Assert.AreEqual(2, update.FamilyIndex);
        Assert.AreEqual(7, update.FamilyTotal);
        Assert.AreEqual("r035", update.CandidateId);
        Assert.AreEqual(3, update.CandidateIndex);
        Assert.AreEqual(19, update.CandidateTotal);
        Assert.AreEqual("visual", update.Stage);
        Assert.AreEqual(600L, update.BestBytes);
        Assert.AreEqual(40.25, update.ReductionPercent);
        Assert.AreEqual("PASS", update.GateStatus);
        Assert.AreEqual("logs/maximum_report.json", update.ReportPath);
    }

    [TestMethod]
    [DataRow("run_started")]
    [DataRow("family_started")]
    [DataRow("candidate_started")]
    [DataRow("stage")]
    [DataRow("candidate_finished")]
    [DataRow("best_updated")]
    [DataRow("family_finished")]
    [DataRow("run_finished")]
    [DataRow("run_cancelled")]
    public void ParsesEveryRequiredMaximumEventKind(string kind)
    {
        var update = _parser.Parse($"MAXIMUM_EVENT {{\"schema\":1,\"kind\":\"{kind}\"}}");

        Assert.IsNotNull(update);
        Assert.AreEqual(kind, update.MaximumKind);
    }

    [TestMethod]
    public void MissingOptionalFieldsRemainNull()
    {
        var update = _parser.Parse("MAXIMUM_EVENT {\"schema\":1,\"kind\":\"stage\"}");

        Assert.IsNotNull(update);
        Assert.IsNull(update.FamilyId);
        Assert.IsNull(update.FamilyIndex);
        Assert.IsNull(update.FamilyTotal);
        Assert.IsNull(update.CandidateId);
        Assert.IsNull(update.CandidateIndex);
        Assert.IsNull(update.CandidateTotal);
        Assert.IsNull(update.Stage);
        Assert.IsNull(update.BestBytes);
        Assert.IsNull(update.ReductionPercent);
        Assert.IsNull(update.GateStatus);
        Assert.IsNull(update.ReportPath);
    }

    [TestMethod]
    [DataRow("MAXIMUM_EVENT not-json")]
    [DataRow("MAXIMUM_EVENT {\"schema\":1,\"kind\":\"stage\"} trailing")]
    [DataRow("MAXIMUM_EVENT {\"schema\":2,\"kind\":\"stage\"}")]
    [DataRow("MAXIMUM_EVENT {\"schema\":1.0,\"kind\":\"stage\"}")]
    [DataRow("MAXIMUM_EVENT {\"schema\":true,\"kind\":\"stage\"}")]
    [DataRow("MAXIMUM_EVENT {\"schema\":1}")]
    [DataRow("MAXIMUM_EVENT {\"schema\":1,\"kind\":null}")]
    [DataRow("MAXIMUM_EVENT {\"schema\":1,\"kind\":\"\"}")]
    [DataRow("MAXIMUM_EVENT {\"schema\":1,\"kind\":\"stage\",\"kind\":\"future\"}")]
    [DataRow("maximum_event {\"schema\":1,\"kind\":\"stage\"}")]
    public void RejectsMalformedUnknownOrWrongSchemaMaximumLines(string line)
    {
        Assert.IsNull(_parser.Parse(line));
    }

    [TestMethod]
    public void PreservesUnknownSchemaOneKindForForwardCompatibleLoggingAndUi()
    {
        var update = _parser.Parse(
            "MAXIMUM_EVENT {\"schema\":1,\"kind\":\"future_kind\",\"stage\":\"future_stage\"}");

        Assert.IsNotNull(update);
        Assert.AreEqual("future_kind", update.MaximumKind);
        Assert.AreEqual("future_stage", update.Stage);
    }

    [TestMethod]
    [DataRow("family_id", "7")]
    [DataRow("family_index", "1.5")]
    [DataRow("family_index", "2147483648")]
    [DataRow("family_total", "true")]
    [DataRow("candidate_id", "{}")]
    [DataRow("candidate_index", "\"3\"")]
    [DataRow("candidate_total", "-1e1")]
    [DataRow("stage", "[]")]
    [DataRow("best_bytes", "1.0")]
    [DataRow("best_bytes", "9223372036854775808")]
    [DataRow("reduction_percent", "\"40\"")]
    [DataRow("reduction_percent", "1e999")]
    [DataRow("gate_status", "false")]
    [DataRow("report_path", "42")]
    public void RejectsWrongMaximumFieldTypesAndUnsafeNumbers(string field, string value)
    {
        var line = $"MAXIMUM_EVENT {{\"schema\":1,\"kind\":\"stage\",\"{field}\":{value}}}";

        Assert.IsNull(_parser.Parse(line));
    }

    [TestMethod]
    [DataRow("family_index", "-1")]
    [DataRow("family_total", "-1")]
    [DataRow("candidate_index", "-1")]
    [DataRow("candidate_total", "-1")]
    [DataRow("best_bytes", "-1")]
    [DataRow("reduction_percent", "100.0001")]
    public void RejectsMaximumValuesOutsideSemanticRanges(string field, string value)
    {
        var line = $"MAXIMUM_EVENT {{\"schema\":1,\"kind\":\"stage\",\"{field}\":{value}}}";

        Assert.IsNull(_parser.Parse(line));
    }

    [TestMethod]
    [DataRow("family_index", 0, "family_total", 0)]
    [DataRow("family_index", 1, "family_total", 1)]
    [DataRow("candidate_index", 0, "candidate_total", 0)]
    [DataRow("candidate_index", 2, "candidate_total", 2)]
    public void RejectsInvalidZeroBasedIndexAndTotalPairs(
        string indexName,
        int index,
        string totalName,
        int total)
    {
        var line = $"MAXIMUM_EVENT {{\"schema\":1,\"kind\":\"stage\",\"{indexName}\":{index},\"{totalName}\":{total}}}";

        Assert.IsNull(_parser.Parse(line));
    }

    [TestMethod]
    public void ExplicitNullIsAcceptedForNullableFields()
    {
        var update = _parser.Parse(
            "MAXIMUM_EVENT {\"schema\":1,\"kind\":\"stage\",\"family_id\":null,\"family_index\":null,\"best_bytes\":null,\"reduction_percent\":null}");

        Assert.IsNotNull(update);
        Assert.IsNull(update.FamilyId);
        Assert.IsNull(update.FamilyIndex);
        Assert.IsNull(update.BestBytes);
        Assert.IsNull(update.ReductionPercent);
    }

    [TestMethod]
    public void AcceptsSafeAndSemanticNumericBoundaries()
    {
        var update = _parser.Parse(
            "MAXIMUM_EVENT {\"schema\":1,\"kind\":\"stage\",\"family_index\":0,\"family_total\":2147483647,\"candidate_index\":2147483646,\"candidate_total\":2147483647,\"best_bytes\":9223372036854775807,\"reduction_percent\":100}");

        Assert.IsNotNull(update);
        Assert.AreEqual(0, update.FamilyIndex);
        Assert.AreEqual(int.MaxValue, update.FamilyTotal);
        Assert.AreEqual(int.MaxValue - 1, update.CandidateIndex);
        Assert.AreEqual(int.MaxValue, update.CandidateTotal);
        Assert.AreEqual(long.MaxValue, update.BestBytes);
        Assert.AreEqual(100.0, update.ReductionPercent);
    }

    [TestMethod]
    public void AcceptsZeroBytesAndStandaloneZeroTotal()
    {
        var update = _parser.Parse(
            "MAXIMUM_EVENT {\"schema\":1,\"kind\":\"run_started\",\"family_total\":0,\"best_bytes\":0}");

        Assert.IsNotNull(update);
        Assert.AreEqual(0, update.FamilyTotal);
        Assert.AreEqual(0L, update.BestBytes);
    }

    [TestMethod]
    public void AllowsNegativeReductionToReportInflation()
    {
        var update = _parser.Parse(
            "MAXIMUM_EVENT {\"schema\":1,\"kind\":\"candidate_finished\",\"reduction_percent\":-25.5}");

        Assert.IsNotNull(update);
        Assert.AreEqual(-25.5, update.ReductionPercent);
    }

    [TestMethod]
    public void PreservesLegacyStepSemantics()
    {
        var update = _parser.Parse("== Step 2/5: Compile models ==");

        Assert.IsNotNull(update);
        Assert.AreEqual(2, update.StepIndex);
        Assert.AreEqual(5, update.StepTotal);
        Assert.AreEqual("Compile models", update.Phase);
    }

    [TestMethod]
    [DataRow("== Packaging: Build output ==", true, false, "Build output")]
    [DataRow("== Finalize: Complete ==", false, true, "Complete")]
    public void PreservesLegacyPhaseSemantics(string line, bool packaging, bool finalize, string phase)
    {
        var update = _parser.Parse(line);

        Assert.IsNotNull(update);
        Assert.AreEqual(packaging, update.IsPackaging);
        Assert.AreEqual(finalize, update.IsFinalize);
        Assert.AreEqual(phase, update.Phase);
    }

    [TestMethod]
    public void PreservesLegacyBatchItemOutputAndWorkDirSemantics()
    {
        var batch = _parser.Parse("== Batch addon 1/4: vehicle ==");
        var started = _parser.Parse("=== (2/9) MDL:  models/a.mdl");
        var completed = _parser.Parse(">>> DONE (2/9) QC:  source/a.qc");
        var output = _parser.Parse("Output addon: C:\\out\\addon");
        var work = _parser.Parse("Work dir: C:\\work\\addon");

        Assert.IsNotNull(batch);
        Assert.AreEqual(1, batch.BatchAddonIndex);
        Assert.AreEqual(4, batch.BatchAddonTotal);
        Assert.AreEqual("vehicle", batch.BatchAddonName);
        Assert.IsNotNull(started);
        Assert.AreEqual(2, started.ItemIndex);
        Assert.AreEqual(9, started.ItemTotal);
        Assert.AreEqual("MDL", started.ItemType);
        Assert.AreEqual("models/a.mdl", started.ItemPath);
        Assert.IsFalse(started.IsItemCompletion);
        Assert.IsNotNull(completed);
        Assert.AreEqual("QC", completed.ItemType);
        Assert.AreEqual("source/a.qc", completed.ItemPath);
        Assert.IsTrue(completed.IsItemCompletion);
        Assert.AreEqual("C:\\out\\addon", output!.OutputAddonPath);
        Assert.AreEqual("C:\\work\\addon", work!.WorkDirPath);
    }

    [TestMethod]
    [DataRow("== Step 999999999999999999999/2: Compile ==")]
    [DataRow("== Batch addon 1/999999999999999999999: addon ==")]
    [DataRow("=== (999999999999999999999/2) MDL: models/a.mdl")]
    [DataRow(">>> DONE (1/999999999999999999999) QC: source/a.qc")]
    [DataRow("== Step x/2: Compile ==")]
    public void LegacyLikeMalformedOrOverflowingLinesReturnNullWithoutThrowing(string line)
    {
        Assert.IsNull(_parser.Parse(line));
    }
}
