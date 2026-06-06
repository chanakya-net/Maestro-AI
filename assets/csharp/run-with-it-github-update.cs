#!/usr/bin/env dotnet
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;

class Program
{
    static readonly HashSet<string> TerminalOpenOutcomes = new(StringComparer.Ordinal)
    {
        "blocked", "failed-review", "failed-merge"
    };

    static int Main(string[] args)
    {
        var parsed = ParseArgs(args);
        if (parsed == null)
        {
            PrintUsage();
            return 2;
        }

        if (parsed.Help)
        {
            PrintUsage();
            return 0;
        }

        try
        {
            if (parsed.Command == "render-comment")
            {
                Console.Write(RenderTerminalComment(parsed.ReportFile, parsed.Outcome));
                return 0;
            }

            if (parsed.Command == "update")
            {
                return UpdateGithub(parsed);
            }

            return 2;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.Message);
            return 2;
        }
    }

    static int UpdateGithub(ParsedArgs args)
    {
        bool closeIssue = args.Outcome == "completed";
        if (args.Outcome != "completed" && !TerminalOpenOutcomes.Contains(args.Outcome))
        {
            return 0;
        }

        if (Environment.GetEnvironmentVariable("RUN_WITH_IT_GITHUB_UPDATES") == "0")
        {
            SetGithubUpdateState(args.StateFile, args.Issue, "skipped", "disabled");
            PrintStatus(args.Issue, args.Outcome, "skipped", new Dictionary<string, string?> { ["reason"] = "disabled" });
            return 0;
        }

        if (!GhExecutableExists())
        {
            SetGithubUpdateState(args.StateFile, args.Issue, "skipped", "gh-not-found");
            PrintStatus(args.Issue, args.Outcome, "skipped", new Dictionary<string, string?> { ["reason"] = "gh-not-found" });
            return 0;
        }

        if (!HasGithubRemote(args.RunRoot))
        {
            SetGithubUpdateState(args.StateFile, args.Issue, "skipped", "no-github-remote");
            PrintStatus(args.Issue, args.Outcome, "skipped", new Dictionary<string, string?> { ["reason"] = "no-github-remote" });
            return 0;
        }

        var comment = RenderTerminalComment(args.ReportFile, args.Outcome);
        var commentFile = Path.Combine(Path.GetTempPath(), $"github-comment-{Guid.NewGuid()}.md");
        File.WriteAllText(commentFile, comment);

        try
        {
            var commentResult = RunCommand("gh", new[] { "issue", "comment", args.Issue, "--body-file", commentFile }, args.RunRoot);
            if (commentResult.ExitCode != 0)
            {
                SetGithubUpdateState(args.StateFile, args.Issue, "failed", "comment-failed");
                PrintStatus(args.Issue, args.Outcome, "failed", new Dictionary<string, string?> { ["reason"] = "comment-failed" });
                return 0;
            }

            if (closeIssue)
            {
                var closeResult = RunCommand("gh", new[] { "issue", "close", args.Issue }, args.RunRoot);
                if (closeResult.ExitCode != 0)
                {
                    SetGithubUpdateState(args.StateFile, args.Issue, "failed", "close-failed");
                    PrintStatus(args.Issue, args.Outcome, "commented", new Dictionary<string, string?> { ["closed"] = "false", ["reason"] = "close-failed" });
                    return 0;
                }
            }

            string closedVal = closeIssue ? "true" : "false";
            SetGithubUpdateState(args.StateFile, args.Issue, "updated", $"commented;closed={closedVal}");
            PrintStatus(args.Issue, args.Outcome, "commented", new Dictionary<string, string?> { ["closed"] = closedVal });
            return 0;
        }
        finally
        {
            if (File.Exists(commentFile))
            {
                try { File.Delete(commentFile); } catch {}
            }
        }
    }

    static string RenderTerminalComment(string reportFile, string fallbackOutcome)
    {
        var report = LoadJson(reportFile);
        var outcome = AsString(report["outcome"]) ?? fallbackOutcome ?? "blocked";
        var summary = AsString(report["summary"]) ?? "No summary provided.";

        var verificationLines = new List<string>();
        if (report.TryGetPropertyValue("verification", out var verNode) && verNode is JsonObject verification)
        {
            var commands = new List<string>();
            if (verification.TryGetPropertyValue("commands_run", out var cmdsNode) && cmdsNode is JsonArray cmdsArray)
            {
                foreach (var cmd in cmdsArray)
                {
                    if (cmd != null) commands.Add(cmd.ToString());
                }
            }
            var evidence = AsString(verification["evidence"]) ?? "";

            string state = "unknown";
            if (verification.TryGetPropertyValue("passed", out var passedNode) && passedNode is JsonValue passedVal)
            {
                if (passedVal.GetValueKind() == JsonValueKind.True) state = "passed";
                else if (passedVal.GetValueKind() == JsonValueKind.False) state = "failed";
            }

            var commandText = commands.Count > 0 ? string.Join(", ", commands) : "unknown";
            verificationLines.Add($"State: {state}");
            verificationLines.Add($"Commands: {commandText}");
            verificationLines.Add($"Evidence: {(string.IsNullOrEmpty(evidence) ? "unknown" : evidence)}");
        }
        else
        {
            var verStr = verNode?.ToString();
            verificationLines.Add(!string.IsNullOrEmpty(verStr) ? verStr : "unknown");
        }

        var review = report["review_summary"] as JsonObject ?? new JsonObject();
        int? cycles = AsInt(review["cycles_used"]);
        var final = AsString(review["final_verdict"]) ?? "unknown";
        var reviewer = AsString(review["reviewer_model"]) ?? "unknown";

        string reviewLine;
        if (cycles == null)
        {
            reviewLine = $"Review: unknown, final verdict: {final}, reviewer model: {reviewer}";
        }
        else if (cycles.Value <= 1 && final == "approve")
        {
            reviewLine = $"Review: approve (1 cycle), final verdict: {final}, reviewer model: {reviewer}";
        }
        else
        {
            reviewLine = $"Review: revise ({cycles} cycles), final verdict: {final}, reviewer model: {reviewer}";
        }

        var tokens = report["token_usage"] as JsonObject;

        var lines = new List<string>
        {
            "## Status",
            outcome,
            "",
            "## Summary",
            summary,
            "",
            "## Verification"
        };
        lines.AddRange(verificationLines);
        lines.Add("");
        lines.Add("## Token Usage");
        lines.Add($"- Input tokens: {format_token(TokenTotal(tokens, "input"))}");
        lines.Add($"- Output tokens: {format_token(TokenTotal(tokens, "output"))}");
        lines.Add($"- Cache hit tokens: {format_token(TokenTotal(tokens, "cache"))}");
        lines.Add("");
        lines.Add("## Notes");
        lines.Add(reviewLine);

        var commitSha = AsString(report["commit_sha"]);
        if (!string.IsNullOrEmpty(commitSha))
        {
            lines.Add($"Commit: {commitSha}");
        }

        if (report.TryGetPropertyValue("merge", out var mergeNode) && mergeNode is JsonObject merge)
        {
            var mergeSha = AsString(merge["merge_sha"]);
            if (!string.IsNullOrEmpty(mergeSha))
            {
                lines.Add($"Merge: {mergeSha}");
            }
        }

        if (report.TryGetPropertyValue("blocking_reasons", out var blockNode) && blockNode is JsonArray blocking && blocking.Count > 0)
        {
            lines.Add("");
            lines.Add("## Blocking Reasons");
            foreach (var reason in blocking)
            {
                if (reason != null)
                {
                    lines.Add($"- {reason}");
                }
            }
        }

        return string.Join("\n", lines) + "\n";
    }

    static bool GhExecutableExists()
    {
        var pathEnv = Environment.GetEnvironmentVariable("PATH");
        if (string.IsNullOrEmpty(pathEnv)) return false;

        var separator = Path.PathSeparator;
        var paths = pathEnv.Split(separator, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        foreach (var p in paths)
        {
            try
            {
                var fullPath = Path.Combine(p, "gh");
                if (File.Exists(fullPath))
                {
                    return true;
                }
            }
            catch {}
        }
        return false;
    }

    static bool HasGithubRemote(string runRoot)
    {
        var res = RunCommand("git", new[] { "-C", runRoot, "remote", "-v" });
        return res.ExitCode == 0 && res.Stdout.ToLowerInvariant().Contains("github.com");
    }

    static void PrintStatus(string issue, string outcome, string action, Dictionary<string, string?> fields)
    {
        var suffix = string.Join("", fields.Where(f => f.Value != null).Select(f => $"|{f.Key}={f.Value}"));
        Console.WriteLine($"STATUS|type=github-update|issue={issue}|outcome={outcome}|action={action}{suffix}");
    }

    static void SetGithubUpdateState(string stateFile, string issue, string status, string detail)
    {
        var state = LoadJson(stateFile);
        if (!state.TryGetPropertyValue("issue_registry", out var regNode) || regNode is not JsonObject registry)
        {
            registry = new JsonObject();
            state["issue_registry"] = registry;
        }
        if (!registry.TryGetPropertyValue(issue, out var entryNode) || entryNode is not JsonObject entry)
        {
            entry = new JsonObject();
            registry[issue] = entry;
        }
        entry["github_update_status"] = status;
        entry["github_update_detail"] = detail;
        entry["github_updated_at"] = DateTimeOffset.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss'Z'", CultureInfo.InvariantCulture);
        SaveJson(stateFile, state);
    }

    static JsonObject LoadJson(string path)
    {
        var text = File.ReadAllText(path);
        return JsonNode.Parse(text) as JsonObject ?? new JsonObject();
    }

    static void SaveJson(string path, JsonObject content)
    {
        var parent = Path.GetDirectoryName(path);
        if (!string.IsNullOrWhiteSpace(parent))
        {
            Directory.CreateDirectory(parent);
        }

        var temp = $"{path}.tmp.{Environment.ProcessId}";
        File.WriteAllText(temp, content.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) + "\n");
        File.Move(temp, path, overwrite: true);
    }

    static ParsedArgs? ParseArgs(string[] args)
    {
        if (args.Length == 0) return null;
        var parsed = new ParsedArgs();
        if (args.Contains("--help") || args.Contains("-h"))
        {
            parsed.Help = true;
            return parsed;
        }

        parsed.Command = args[0];
        if (parsed.Command != "update" && parsed.Command != "render-comment")
        {
            return null;
        }

        for (int i = 1; i < args.Length; i++)
        {
            var arg = args[i];
            if (!arg.StartsWith("--", StringComparison.Ordinal) || i + 1 >= args.Length)
            {
                return null;
            }
            var value = args[i + 1];
            i++;

            switch (arg)
            {
                case "--state-file":
                    parsed.StateFile = value;
                    break;
                case "--run-root":
                    parsed.RunRoot = value;
                    break;
                case "--issue":
                    parsed.Issue = value;
                    break;
                case "--outcome":
                    parsed.Outcome = value;
                    break;
                case "--report-file":
                    parsed.ReportFile = value;
                    break;
                default:
                    return null;
            }
        }

        if (parsed.Command == "update")
        {
            if (string.IsNullOrEmpty(parsed.StateFile) ||
                string.IsNullOrEmpty(parsed.RunRoot) ||
                string.IsNullOrEmpty(parsed.Issue) ||
                string.IsNullOrEmpty(parsed.Outcome) ||
                string.IsNullOrEmpty(parsed.ReportFile))
            {
                return null;
            }
        }
        else if (parsed.Command == "render-comment")
        {
            if (string.IsNullOrEmpty(parsed.Outcome) ||
                string.IsNullOrEmpty(parsed.ReportFile))
            {
                return null;
            }
        }

        return parsed;
    }

    static int? TokenTotal(JsonObject? tokens, string kind)
    {
        if (tokens == null) return null;
        int total = 0;
        bool found = false;
        foreach (var pair in tokens)
        {
            var keyL = pair.Key.ToLowerInvariant();
            if (kind == "input" && !keyL.Contains("input")) continue;
            if (kind == "output" && !keyL.Contains("output")) continue;
            if (kind == "cache" && !keyL.Contains("cache")) continue;

            if (pair.Value is JsonValue val && val.GetValueKind() == JsonValueKind.Number)
            {
                if (val.TryGetValue<int>(out var intVal))
                {
                    total += intVal;
                    found = true;
                }
                else if (val.TryGetValue<double>(out var dblVal))
                {
                    total += (int)dblVal;
                    found = true;
                }
            }
        }
        return found ? total : null;
    }

    static string format_token(int? value)
    {
        return value.HasValue ? value.Value.ToString(CultureInfo.InvariantCulture) : "unknown";
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

    static void PrintUsage()
    {
        Console.WriteLine("Usage: run-with-it-github-update <command> ...");
    }

    static ProcessResult RunCommand(string exe, string[] args, string? workingDir = null)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = exe,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        if (workingDir != null)
        {
            startInfo.WorkingDirectory = workingDir;
        }
        foreach (var arg in args)
        {
            startInfo.ArgumentList.Add(arg);
        }

        try
        {
            using var process = Process.Start(startInfo);
            if (process == null) return new ProcessResult { ExitCode = -1, Stdout = "", Stderr = "" };
            var stdout = process.StandardOutput.ReadToEnd();
            var stderr = process.StandardError.ReadToEnd();
            process.WaitForExit();
            return new ProcessResult { ExitCode = process.ExitCode, Stdout = stdout, Stderr = stderr };
        }
        catch (Exception ex)
        {
            return new ProcessResult { ExitCode = -1, Stdout = "", Stderr = ex.Message };
        }
    }
}

class ParsedArgs
{
    public string Command { get; set; } = string.Empty;
    public string StateFile { get; set; } = string.Empty;
    public string RunRoot { get; set; } = string.Empty;
    public string Issue { get; set; } = string.Empty;
    public string Outcome { get; set; } = string.Empty;
    public string ReportFile { get; set; } = string.Empty;
    public bool Help { get; set; }
}

class ProcessResult
{
    public int ExitCode { get; set; }
    public string Stdout { get; set; } = string.Empty;
    public string Stderr { get; set; } = string.Empty;
}

static class JsonExtensions
{
    public static JsonValueKind GetValueKind(this JsonNode? node)
    {
        return node is JsonValue value ? value.GetValueKind() : JsonValueKind.Undefined;
    }
}
