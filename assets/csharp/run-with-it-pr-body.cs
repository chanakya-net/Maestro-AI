#!/usr/bin/env dotnet
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

class Program
{
    static readonly Regex AutoCloseRefPattern = new(
        @"\b(close(?:s|d)?|fix(?:es|ed)?|resolve(?:s|d)?)(\s+)((?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?)(#\d+)\b",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant
    );

    static int Main(string[] args)
    {
        if (args.Length == 0)
        {
            PrintUsage();
            return 2;
        }

        if (args.Contains("--help") || args.Contains("-h"))
        {
            PrintUsage();
            return 0;
        }

        var command = args[0];
        if (command != "render")
        {
            PrintUsage();
            return 2;
        }

        string stateFile = "";
        for (int i = 1; i < args.Length; i++)
        {
            if (args[i] == "--state-file" && i + 1 < args.Length)
            {
                stateFile = args[i + 1];
                i++;
            }
            else
            {
                PrintUsage();
                return 2;
            }
        }

        if (string.IsNullOrEmpty(stateFile))
        {
            PrintUsage();
            return 2;
        }

        try
        {
            Console.Write(RenderPrBody(stateFile));
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.Message);
            return 2;
        }
    }

    static string RenderPrBody(string stateFile)
    {
        var state = LoadState(stateFile);
        var runRoot = RunRootFor(stateFile);
        var counts = StatusCounts(state);
        var summaries = SummaryByIssue(state);
        var completed = CompletedIssueNumbers(state);

        int totalAdded = 0;
        int totalDeleted = 0;
        foreach (var issue in completed)
        {
            if (summaries.TryGetValue(issue, out var sum))
            {
                totalAdded += AsInt(sum["lines_added"]) ?? 0;
                totalDeleted += AsInt(sum["lines_deleted"]) ?? 0;
            }
        }

        var lines = new List<string>
        {
            "## Summary",
            $"- Total issues processed: {counts.Values.Sum()}",
            $"- Completed: {counts["completed"]}",
            $"- Failed review: {counts["failed-review"]}",
            $"- Failed merge: {counts["failed-merge"]}",
            $"- Blocked: {counts["blocked"]}",
            $"- Lines added: {totalAdded}",
            $"- Lines deleted: {totalDeleted}",
            "",
            "## Closed Issues"
        };

        if (completed.Count > 0)
        {
            foreach (var issue in completed)
            {
                lines.Add($"- #{issue}");
            }
        }
        else
        {
            lines.Add("None");
        }

        lines.Add("");
        lines.Add("## Models Used");
        lines.Add("| Issue | Task | Cycle | Agent | Model | Reason |");
        lines.Add("|---|---|---:|---|---|---|");

        foreach (var issue in completed)
        {
            var summary = summaries.TryGetValue(issue, out var sum) ? sum : new JsonObject();
            var report = ReportForIssue(state, issue, runRoot, summary);
            foreach (var row in ModelRows(issue, report, summary))
            {
                lines.Add(
                    $"| #{OneLine(issue)} | {row["role"]} | {row["cycle"]} | " +
                    $"{row["agent"]} | {row["model"]} | {row["selection_reason"]} |"
                );
            }
        }

        lines.Add("");
        lines.Add("## Verification");
        lines.Add("| Issue | State | Evidence |");
        lines.Add("|---|---|---|");

        foreach (var issue in completed)
        {
            var summary = summaries.TryGetValue(issue, out var sum) ? sum : new JsonObject();
            var report = ReportForIssue(state, issue, runRoot, summary);
            var (stateText, evidence) = VerificationState(report, summary);
            lines.Add($"| #{OneLine(issue)} | {stateText} | {evidence} |");
        }

        return string.Join("\n", lines) + "\n";
    }

    static JsonObject LoadState(string path)
    {
        try
        {
            if (!File.Exists(path))
            {
                Console.Error.WriteLine($"error: failed to load state file {path}: File not found");
                Environment.Exit(1);
            }
            var text = File.ReadAllText(path);
            var node = JsonNode.Parse(text);
            if (node is JsonObject obj)
            {
                return obj;
            }
            Console.Error.WriteLine($"error: state file {path} must contain a JSON object");
            Environment.Exit(1);
            return null!;
        }
        catch (Exception exc)
        {
            Console.Error.WriteLine($"error: failed to load state file {path}: {exc.Message}");
            Environment.Exit(1);
            return null!;
        }
    }

    static string RunRootFor(string stateFile)
    {
        var fullPath = Path.GetFullPath(stateFile);
        var parent = Path.GetDirectoryName(fullPath);
        if (parent != null && Path.GetFileName(parent) == ".run-with-it")
        {
            return Path.GetDirectoryName(parent) ?? parent;
        }
        return parent ?? "";
    }

    static string ResolvePath(string? path, string runRoot)
    {
        if (string.IsNullOrEmpty(path)) return "";
        if (Path.IsPathRooted(path)) return path;
        return Path.GetFullPath(Path.Combine(runRoot, path));
    }

    static string OneLine(string? value)
    {
        var text = string.IsNullOrEmpty(value) ? "unknown" : value;
        text = text.Replace("|", "\\|");
        var parts = text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        text = string.Join(" ", parts);
        return SanitizeAutoCloseRefs(text);
    }

    static string SanitizeAutoCloseRefs(string text)
    {
        return AutoCloseRefPattern.Replace(text, match => {
            return $"{match.Groups[1].Value}{match.Groups[2].Value}{match.Groups[3].Value}\\{match.Groups[4].Value}";
        });
    }

    static Dictionary<string, int> StatusCounts(JsonObject state)
    {
        var counts = new Dictionary<string, int>(StringComparer.Ordinal)
        {
            ["completed"] = 0,
            ["failed-review"] = 0,
            ["failed-merge"] = 0,
            ["blocked"] = 0
        };
        if (state.TryGetPropertyValue("issue_registry", out var regNode) && regNode is JsonObject registry)
        {
            foreach (var pair in registry)
            {
                if (pair.Value is JsonObject info)
                {
                    var status = AsString(info["status"]) ?? "";
                    if (counts.ContainsKey(status))
                    {
                        counts[status]++;
                    }
                }
            }
        }
        return counts;
    }

    static IEnumerable<string> SortIssues(IEnumerable<string> issues)
    {
        return issues.OrderBy(issue => {
            if (int.TryParse(issue, NumberStyles.Integer, CultureInfo.InvariantCulture, out var val))
            {
                return (0, val.ToString("D20", CultureInfo.InvariantCulture));
            }
            return (1, issue);
        }, Comparer<(int, string)>.Default);
    }

    static List<string> CompletedIssueNumbers(JsonObject state)
    {
        var list = new List<string>();
        if (state.TryGetPropertyValue("issue_registry", out var regNode) && regNode is JsonObject registry)
        {
            foreach (var pair in registry)
            {
                if (pair.Value is JsonObject info)
                {
                    if (AsString(info["status"]) == "completed")
                    {
                        list.Add(pair.Key);
                    }
                }
            }
        }
        return SortIssues(list).ToList();
    }

    static Dictionary<string, JsonObject> SummaryByIssue(JsonObject state)
    {
        var result = new Dictionary<string, JsonObject>(StringComparer.Ordinal);
        if (state.TryGetPropertyValue("completed_summaries", out var sumNode) && sumNode is JsonArray summaries)
        {
            foreach (var item in summaries)
            {
                if (item is JsonObject obj && obj.TryGetPropertyValue("issue", out var issueVal) && issueVal != null)
                {
                    var issueStr = AsIntText(issueVal) ?? issueVal.ToString();
                    result[issueStr] = obj;
                }
            }
        }
        return result;
    }

    static JsonObject ReportForIssue(JsonObject state, string issue, string runRoot, JsonObject summary)
    {
        var summaryReport = ResolvePath(AsString(summary["report_file"]), runRoot);
        if (!string.IsNullOrEmpty(summaryReport) && File.Exists(summaryReport))
        {
            return LoadJson(summaryReport);
        }

        if (state.TryGetPropertyValue("issue_registry", out var regNode) && regNode is JsonObject registry)
        {
            if (registry.TryGetPropertyValue(issue, out var infoNode) && infoNode is JsonObject info)
            {
                var refs = new[] { AsString(info["merge_recovery_report_file"]), AsString(info["report_file"]) };
                foreach (var r in refs)
                {
                    var reportPath = ResolvePath(r, runRoot);
                    if (!string.IsNullOrEmpty(reportPath) && File.Exists(reportPath))
                    {
                        var report = LoadJson(reportPath);
                        if (report.Count > 0) return report;
                    }
                }
            }
        }
        return new JsonObject();
    }

    static List<JsonObject> ModelRows(string issue, JsonObject report, JsonObject summary)
    {
        var usage = report["model_usage"] as JsonArray ?? summary["model_usage"] as JsonArray;
        if (usage == null || usage.Count == 0)
        {
            return new List<JsonObject>
            {
                new JsonObject
                {
                    ["issue"] = issue,
                    ["role"] = "unknown",
                    ["cycle"] = "-",
                    ["agent"] = "unknown",
                    ["model"] = "unknown",
                    ["selection_reason"] = "missing-model-usage",
                }
            };
        }

        var rows = new List<JsonObject>();
        foreach (var item in usage)
        {
            if (item is JsonObject row)
            {
                var cycleVal = row.TryGetPropertyValue("cycle", out var cVal) && cVal != null ? AsIntText(cVal) ?? cVal.ToString() : "-";
                rows.Add(new JsonObject
                {
                    ["issue"] = issue,
                    ["role"] = OneLine(AsString(row["role"])),
                    ["cycle"] = OneLine(cycleVal),
                    ["agent"] = OneLine(AsString(row["agent"])),
                    ["model"] = OneLine(AsString(row["model"])),
                    ["selection_reason"] = OneLine(AsString(row["selection_reason"]) ?? AsString(row["reason"])),
                });
            }
        }

        if (rows.Count == 0)
        {
            return ModelRows(issue, new JsonObject(), new JsonObject());
        }
        return rows;
    }

    static (string State, string Evidence) VerificationState(JsonObject report, JsonObject summary)
    {
        var verification = report["verification"] as JsonObject ?? summary["verification"] as JsonObject ?? new JsonObject();

        string state = "unknown";
        if (verification.TryGetPropertyValue("passed", out var passedNode) && passedNode is JsonValue passedVal)
        {
            if (passedVal.GetValueKind() == JsonValueKind.True) state = "passed";
            else if (passedVal.GetValueKind() == JsonValueKind.False) state = "failed";
        }

        var evidence = AsString(verification["evidence"]) ?? AsString(summary["summary"]) ?? "unknown";
        return (state, OneLine(evidence));
    }

    static JsonObject LoadJson(string path)
    {
        if (!File.Exists(path) || new FileInfo(path).Length == 0) return new JsonObject();
        try
        {
            var text = File.ReadAllText(path);
            return JsonNode.Parse(text) as JsonObject ?? new JsonObject();
        }
        catch
        {
            return new JsonObject();
        }
    }

    static string? AsString(JsonNode? node)
    {
        return node is JsonValue value && value.GetValueKind() == JsonValueKind.String
            ? value.GetValue<string>()
            : null;
    }

    static int? AsInt(JsonNode? node)
    {
        if (node is JsonValue value && value.GetValueKind() == JsonValueKind.Number)
        {
            if (value.TryGetValue<int>(out var parsed))
            {
                return parsed;
            }
            if (value.TryGetValue<double>(out var dbl))
            {
                return (int)dbl;
            }
        }
        return null;
    }

    static string? AsIntText(JsonNode? node)
    {
        if (node is JsonValue value)
        {
            if (value.GetValueKind() == JsonValueKind.Number)
            {
                if (value.TryGetValue<int>(out var intValue))
                {
                    return intValue.ToString(CultureInfo.InvariantCulture);
                }
                if (value.TryGetValue<double>(out var doubleValue))
                {
                    return doubleValue.ToString(CultureInfo.InvariantCulture);
                }
            }
            if (value.GetValueKind() == JsonValueKind.String)
            {
                return value.GetValue<string>();
            }
        }
        return null;
    }

    static void PrintUsage()
    {
        Console.WriteLine("Usage: run-with-it-pr-body render --state-file <file>");
    }
}
