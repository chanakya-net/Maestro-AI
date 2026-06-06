#!/usr/bin/env dotnet
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

class Program
{
    static int Main(string[] args)
    {
        args = PreprocessArgs(args);
        var options = ParseArgs(args);
    if (options is null)
    {
        PrintUsage();
        return 2;
    }

    if (options.Help)
    {
        PrintUsage();
        return 0;
    }

    try
    {
        return options.Command switch
        {
            "failure-reason" => FailureReason(options),
            "synthesize" => Synthesize(options),
            _ => 2,
        };
    }
    catch (CommandError ex)
    {
        Console.Error.WriteLine(ex.Message);
        return 2;
    }
    catch (Exception ex)
    {
        Console.Error.WriteLine(ex.Message);
        return 1;
    }
}

static readonly HashSet<string> ComplexityLevels = new(StringComparer.Ordinal)
{
    "quite-easy",
    "easy",
    "medium",
    "medium-hard",
    "complex",
    "holy-fuck",
};

static readonly HashSet<string> ComplexityScoreKeys = new(StringComparer.Ordinal)
{
    "dependency_complexity",
    "ownership_overlap_risk",
    "architecture_risk",
    "orchestration_burden",
    "verification_risk",
    "ambiguity_of_requirements",
    "integration_surface_breadth",
    "rollback_recovery_risk",
    "blast_radius",
};

static readonly HashSet<string> ReviewVerdicts = new(StringComparer.Ordinal)
{
    "approve",
    "revise",
    "reject",
};

static readonly Regex CanonicalComplexityRetryPattern = new("^(.*cycle-[0-9]+)-attempt-[0-9]+-result\\.json$", RegexOptions.CultureInvariant);
static readonly Regex CanonicalReviewRetryPattern = new("^(.*cycle-[0-9]+)-attempt-[0-9]+-status\\.json$", RegexOptions.CultureInvariant);

static ParsedArgs? ParseArgs(string[] args)
{
    if (args.Length == 0)
    {
        return null;
    }

    var parsed = new ParsedArgs();

    if (args.Contains("--help") || args.Contains("-h"))
    {
        parsed.Help = true;
        return parsed;
    }

    if (args.Length < 1)
    {
        return null;
    }

    parsed.Command = args[0];
    for (int i = 1; i < args.Length; i++)
    {
        var arg = args[i];
        if (!arg.StartsWith("--", StringComparison.Ordinal) || i + 1 >= args.Length)
        {
            if (arg == "--issue")
            {
                continue;
            }

            return null;
        }

        var value = args[i + 1];
        i += 1;

        switch (arg)
        {
            case "--role":
                parsed.Role = value;
                break;
            case "--issue":
                parsed.Issue = value;
                break;
            case "--result-file":
                parsed.ResultFile = value;
                break;
            case "--done-file":
                parsed.DoneFile = value;
                break;
            case "--log-file":
                parsed.LogFile = value;
                break;
            case "--issue-dir":
                parsed.IssueDir = value;
                break;
            case "--repo-root":
                parsed.RepoRoot = value;
                break;
            case "--pre-spawn-head":
                parsed.PreSpawnHead = value;
                break;
            default:
                return null;
        }
    }

    if (string.IsNullOrWhiteSpace(parsed.Command)
        || string.IsNullOrWhiteSpace(parsed.Role)
        || string.IsNullOrWhiteSpace(parsed.Issue)
        || string.IsNullOrWhiteSpace(parsed.ResultFile))
    {
        return null;
    }

    if (parsed.Command != "failure-reason" && parsed.Command != "synthesize")
    {
        return null;
    }

    return parsed;
}

static int FailureReason(ParsedArgs args)
{
    Console.WriteLine(ResultFailureReason(args));
    return 0;
}

static int Synthesize(ParsedArgs args)
{
    return (SynthesizeImplementation(args) || SynthesizeReview(args) || SynthesizeComplexity(args)) ? 0 : 1;
}

static string ResultFailureReason(ParsedArgs args)
{
    if (ResultFileIsIssueReport(args))
    {
        return "worker-result-path-is-sub-coordinator-report";
    }

    var (payload, error) = LoadJson(args.ResultFile);
    if (error == "missing")
    {
        return "missing-result-artifact";
    }

    if (args.Role is "impl" or "modify")
    {
        if (error == "invalid")
        {
            return "invalid-result-artifact";
        }

        return ImplementationResultReason(args, payload!);
    }

    if (args.Role == "complexity")
    {
        if (error != "missing" && !(payload is JsonObject obj && ValidComplexityPayload(obj)))
        {
            return "invalid-complexity-result-artifact";
        }

        return string.Empty;
    }

    if (args.Role == "review")
    {
        return ReviewResultReason(args, payload, error);
    }

    if (error is not null || payload is not JsonObject)
    {
        return "invalid-result-artifact";
    }

    return string.Empty;
}

static string ImplementationResultReason(ParsedArgs args, JsonObject payload)
{
    if (AsScalarString(payload["issue"]) != args.Issue || AsString(payload["role"]) != args.Role || AsString(payload["status"]) != "success")
    {
        return "invalid-result-artifact";
    }

    var commitSha = AsString(payload["commit_sha"]);
    if (string.IsNullOrWhiteSpace(commitSha) || commitSha == "NONE")
    {
        return "invalid-result-artifact";
    }

    if (payload["files_committed"] is not JsonArray files || files.Count == 0)
    {
        return "invalid-result-artifact";
    }

    if (payload["verification"] is not JsonObject)
    {
        return "invalid-result-artifact";
    }

    if (string.IsNullOrWhiteSpace(args.RepoRoot) || !RepoAvailable(args.RepoRoot))
    {
        return "implementation-repo-unavailable";
    }

    var head = CurrentHead(args.RepoRoot);
    if (head != commitSha)
    {
        if (RecoverWrongWorktreeCommit(args, payload, head, commitSha))
        {
            return string.Empty;
        }

        return "commit-outside-issue-worktree";
    }

    if (!string.IsNullOrWhiteSpace(args.PreSpawnHead) && commitSha == args.PreSpawnHead)
    {
        return "missing-implementation-commit";
    }

    return string.Empty;
}

static string ReviewResultReason(ParsedArgs args, JsonObject? payload, string? statusError)
{
    if (statusError == "missing")
    {
        return "missing-result-artifact";
    }

    if (statusError == "invalid" || !ValidReviewStatus(payload))
    {
        return "invalid-review-status-artifact";
    }

    var instructionsFile = ReviewInstructionsFile(args.ResultFile);
    if (string.IsNullOrWhiteSpace(instructionsFile))
    {
        return "missing-review-instructions-artifact";
    }

    var (instructions, instructionsError) = LoadJson(instructionsFile);
    if (instructionsError == "missing")
    {
        return "missing-review-instructions-artifact";
    }

    if (instructionsError == "invalid" || !ValidReviewInstructions(instructions))
    {
        return "invalid-review-instructions-artifact";
    }

    if (AsString(payload!["verdict"]) != AsString(instructions!["verdict"]))
    {
        return "review-artifact-verdict-mismatch";
    }

    return string.Empty;
}

static bool SynthesizeImplementation(ParsedArgs args)
{
    if (args.Role is not "impl" and not "modify")
    {
        return false;
    }

    if (ResultFileIsIssueReport(args))
    {
        return false;
    }

    if (File.Exists(args.ResultFile) && new FileInfo(args.ResultFile).Length > 0)
    {
        return false;
    }

    if (WorkerPayloadWrittenToIssueReport(args))
    {
        return false;
    }

    if (string.IsNullOrWhiteSpace(args.DoneFile) || !File.Exists(args.DoneFile) || new FileInfo(args.DoneFile).Length == 0)
    {
        return false;
    }

    if (string.IsNullOrWhiteSpace(args.RepoRoot) || !RepoAvailable(args.RepoRoot))
    {
        return false;
    }

    var head = CurrentHead(args.RepoRoot);
    if (string.IsNullOrWhiteSpace(head) || head == args.PreSpawnHead)
    {
        return false;
    }

    var files = GetCommittedFiles(args.RepoRoot, head);
    if (files.Count == 0)
    {
        return false;
    }

    var payload = new JsonObject
    {
        ["schema_version"] = 1,
        ["issue"] = args.Issue,
        ["role"] = args.Role,
        ["status"] = "success",
        ["commit_sha"] = head,
        ["files_committed"] = files,
        ["verification"] = new JsonObject
        {
            ["passed"] = false,
            ["commands"] = new JsonArray(),
            ["source"] = "dispatcher-synthesized",
            ["note"] = "Worker exited successfully and advanced HEAD but did not write RUN_WITH_IT_RESULT_FILE; verification evidence was not machine-readable.",
        },
        ["source"] = "dispatcher-synthesized",
    };

    WriteJsonAtomic(args.ResultFile, payload);

    return ResultFailureReason(args) == string.Empty;
}

static bool SynthesizeReview(ParsedArgs args)
{
    if (args.Role != "review")
    {
        return false;
    }

    var canonicalStatusFile = CanonicalReviewRetryStatusFile(args.ResultFile);
    if (!string.IsNullOrWhiteSpace(canonicalStatusFile) && File.Exists(canonicalStatusFile))
    {
        var (canonicalPayload, canonicalStatusError) = LoadJson(canonicalStatusFile);
        var canonicalInstructionsFile = ReviewInstructionsFile(canonicalStatusFile);
        var (canonicalInstructionsPayload, canonicalInstructionsError) = LoadJson(canonicalInstructionsFile);

        if (canonicalStatusError == null
            && canonicalPayload is JsonObject canonical
            && ValidReviewStatus(canonical)
            && canonicalInstructionsError == null
            && canonicalInstructionsPayload is JsonObject canonicalInstructions
            && AsString(canonicalInstructions["verdict"]) == AsString(canonical["verdict"]))
        {
            var statusCopy = canonical.DeepClone() as JsonObject ?? new JsonObject();
            if (!statusCopy.ContainsKey("source"))
            {
                statusCopy["source"] = "dispatcher-copied-from-canonical-retry";
            }

            WriteJsonAtomic(args.ResultFile, statusCopy);

            var instructionsCopy = canonicalInstructions.DeepClone() as JsonObject ?? new JsonObject();
            instructionsCopy["source"] = "dispatcher-copied-from-canonical-retry";
            var instructionsFile = ReviewInstructionsFile(args.ResultFile);
            if (!string.IsNullOrWhiteSpace(instructionsFile))
            {
                WriteJsonAtomic(instructionsFile, instructionsCopy);
            }

            return ResultFailureReason(args) == string.Empty;
        }
    }

    var (statusPayload, statusError) = LoadJson(args.ResultFile);
    var instructionsFilePath = ReviewInstructionsFile(args.ResultFile);
    var (instructionsPayload, instructionsError) = LoadJson(instructionsFilePath);

    if ((statusError is not null || statusPayload is not JsonObject statusObj || !ValidReviewStatus(statusObj)) && instructionsError is null && instructionsPayload is JsonObject instructions && ValidReviewInstructions(instructions))
    {
        var comments = instructions["comments"] as JsonArray ?? new JsonArray();
        WriteJsonAtomic(args.ResultFile, new JsonObject
        {
            ["verdict"] = AsString(instructions["verdict"]) ?? "revise",
            ["comment_count"] = comments.Count,
            ["nitpick_only"] = NitpickOnly(comments),
            ["source"] = "dispatcher-synthesized",
        });

        return ResultFailureReason(args) == string.Empty;
    }

    if (statusError is null
        && statusPayload is JsonObject status
        && AsString(status["verdict"]) == "approve"
        && !string.IsNullOrWhiteSpace(instructionsFilePath)
        && (instructionsError is not null || !ValidReviewInstructions(instructionsPayload)))
    {
        WriteJsonAtomic(instructionsFilePath, new JsonObject
        {
            ["verdict"] = "approve",
            ["summary"] = "Dispatcher synthesized approve instructions because the reviewer wrote a valid approve status artifact but omitted REVIEWER_INSTRUCTIONS_FILE.",
            ["comments"] = new JsonArray(),
            ["blocking_reasons"] = new JsonArray(),
            ["source"] = "dispatcher-synthesized",
        });

        return ResultFailureReason(args) == string.Empty;
    }

    return false;
}

static bool SynthesizeComplexity(ParsedArgs args)
{
    if (args.Role != "complexity")
    {
        return false;
    }

    if (File.Exists(args.ResultFile) && new FileInfo(args.ResultFile).Length > 0)
    {
        return false;
    }

    var canonicalResult = CanonicalComplexityRetryResultFile(args.ResultFile);
    if (!string.IsNullOrWhiteSpace(canonicalResult) && File.Exists(canonicalResult))
    {
        var (payload, error) = LoadJson(canonicalResult);
        if (error is null && payload is JsonObject complexity && ValidComplexityPayload(complexity))
        {
            var copied = complexity.DeepClone() as JsonObject ?? new JsonObject();
            copied["source"] = "dispatcher-copied-from-canonical-retry";
            WriteJsonAtomic(args.ResultFile, copied);
            return ResultFailureReason(args) == string.Empty;
        }
    }

    if (string.IsNullOrWhiteSpace(args.DoneFile) || !File.Exists(args.DoneFile) || new FileInfo(args.DoneFile).Length == 0)
    {
        return false;
    }

    var payloadFromLog = ComplexityPayloadFromLog(args.LogFile);
    if (payloadFromLog is null)
    {
        return false;
    }

    payloadFromLog["source"] = "dispatcher-synthesized-from-log";
    WriteJsonAtomic(args.ResultFile, payloadFromLog);
    return ResultFailureReason(args) == string.Empty;
}

static JsonObject? ComplexityPayloadFromLog(string logFile)
{
    if (string.IsNullOrWhiteSpace(logFile) || !File.Exists(logFile) || new FileInfo(logFile).Length == 0)
    {
        return null;
    }

    var bytes = File.ReadAllText(logFile);
    var utf8 = Encoding.UTF8.GetBytes(bytes);
    for (var index = 0; index < utf8.Length; index++)
    {
        if (utf8[index] != (byte)'{')
        {
            continue;
        }

        var reader = new Utf8JsonReader(utf8.AsSpan(index), true, default);
        try
        {
            var doc = JsonDocument.ParseValue(ref reader);
            if (doc.RootElement.ValueKind == JsonValueKind.Object)
            {
                var payload = JsonNode.Parse(doc.RootElement.GetRawText()) as JsonObject;
                if (payload is not null && ValidComplexityPayload(payload))
                {
                    return payload;
                }
            }
        }
        catch
        {
            continue;
        }
    }

    return null;
}

static bool ResultFileIsIssueReport(ParsedArgs args)
{
    if (args.Role != "impl" && args.Role != "modify")
    {
        return false;
    }

    var reportPath = IssueReportPath(args);
    if (string.IsNullOrWhiteSpace(reportPath))
    {
        return false;
    }

    return IsIssueReportPath(args.ResultFile, reportPath);
}

static bool WorkerPayloadWrittenToIssueReport(ParsedArgs args)
{
    if (args.Role != "impl" && args.Role != "modify")
    {
        return false;
    }

    var reportPath = IssueReportPath(args);
    if (string.IsNullOrWhiteSpace(reportPath))
    {
        return false;
    }

    var (payload, error) = LoadJson(reportPath);
    if (error is not null || payload is not JsonObject report)
    {
        return false;
    }

    return AsScalarString(report["issue"]) == args.Issue && AsString(report["role"]) == args.Role;
}

static string IssueReportPath(ParsedArgs args)
{
    if (string.IsNullOrWhiteSpace(args.IssueDir))
    {
        return string.Empty;
    }

    return Path.Combine(args.IssueDir, "report.json");
}

static bool IsIssueReportPath(string resultFile, string issueReportPath)
{
    try
    {
        return Path.GetFullPath(resultFile).Equals(Path.GetFullPath(issueReportPath), StringComparison.Ordinal);
    }
    catch
    {
        return false;
    }
}

static bool RecoverWrongWorktreeCommit(ParsedArgs args, JsonObject payload, string head, string commitSha)
{
    if (string.IsNullOrWhiteSpace(args.PreSpawnHead))
    {
        return false;
    }

    if (head != args.PreSpawnHead || string.IsNullOrWhiteSpace(args.RepoRoot) || !RepoAvailable(args.RepoRoot))
    {
        return false;
    }

    if (!CommitExists(args.RepoRoot, commitSha) || !IsAncestor(args.RepoRoot, args.PreSpawnHead, commitSha))
    {
        return false;
    }

    if (!string.IsNullOrWhiteSpace(GitOutput(args.RepoRoot, "status", "--porcelain")) && GitOutput(args.RepoRoot, "status", "--porcelain") != string.Empty)
    {
        return false;
    }

    if (!GitSuccess(args.RepoRoot, "cherry-pick", "--no-edit", commitSha))
    {
        GitSuccess(args.RepoRoot, "cherry-pick", "--abort");
        return false;
    }

    var recoveredHead = CurrentHead(args.RepoRoot);
    if (recoveredHead == head)
    {
        return false;
    }

    payload["commit_sha"] = recoveredHead;
    payload["recovered_from_commit"] = commitSha;
    payload["recovery"] = "cherry-picked commit from outside issue worktree into issue worktree";
    WriteJsonAtomic(args.ResultFile, payload);
    return true;
}

static bool ValidComplexityPayload(JsonObject payload)
{
    if (AsInt(payload["total"]).GetValueOrDefault() == 0 && AsInt(payload["total"]) != 0)
    {
        return false;
    }

    if (AsString(payload["level"]) is not string level || !ComplexityLevels.Contains(level))
    {
        return false;
    }

    if (payload["scores"] is not JsonObject scores || payload["rationale"] is not JsonObject rationale)
    {
        return false;
    }

    if (!new HashSet<string>(scores.Select(k => k.Key), StringComparer.Ordinal).SetEquals(ComplexityScoreKeys))
    {
        return false;
    }

    if (!new HashSet<string>(rationale.Select(k => k.Key), StringComparer.Ordinal).SetEquals(ComplexityScoreKeys))
    {
        return false;
    }

    foreach (var key in ComplexityScoreKeys)
    {
        var score = AsInt(scores[key]);
        if (!score.HasValue || score < 1 || score > 5)
        {
            return false;
        }
    }

    return true;
}

static bool ValidReviewStatus(JsonObject payload)
{
    if (payload["verdict"] is not JsonValue verdict || !ReviewVerdicts.Contains(verdict.GetValue<string>()))
    {
        return false;
    }

    var commentCount = AsInt(payload["comment_count"]);
    if (!commentCount.HasValue || commentCount.Value < 0)
    {
        return false;
    }

    if (!payload["nitpick_only"].GetValueKind().Equals(JsonValueKind.True)
        && !payload["nitpick_only"].GetValueKind().Equals(JsonValueKind.False))
    {
        return false;
    }

    return true;
}

static bool ValidReviewInstructions(JsonObject payload)
{
    if (payload["verdict"] is not JsonValue verdict || !ReviewVerdicts.Contains(verdict.GetValue<string>()))
    {
        return false;
    }

    if (payload["summary"] is not JsonValue summary || summary.GetValueKind() != JsonValueKind.String)
    {
        return false;
    }

    if (payload["comments"] is not JsonArray)
    {
        return false;
    }

    if (payload["blocking_reasons"] is not JsonArray)
    {
        return false;
    }

    return true;
}

static bool NitpickOnly(JsonArray comments)
{
    if (comments.Count == 0)
    {
        return false;
    }

    foreach (var node in comments)
    {
        if (node is not JsonObject item)
        {
            return false;
        }

        if (!"info".Equals(AsString(item["severity"]), StringComparison.Ordinal))
        {
            return false;
        }

        var fix = AsString(item["fix"]);
        if (string.IsNullOrWhiteSpace(fix) || !fix.StartsWith("[nitpick]", StringComparison.Ordinal))
        {
            return false;
        }
    }

    return true;
}

static string ReviewInstructionsFile(string resultFile)
{
    if (resultFile.EndsWith("-status.json", StringComparison.Ordinal))
    {
        return resultFile[..^"-status.json".Length] + "-instructions.json";
    }

    return string.Empty;
}

static string CanonicalReviewRetryStatusFile(string resultFile)
{
    var match = CanonicalReviewRetryPattern.Match(resultFile);
    return match.Success ? match.Groups[1].Value + "-status.json" : string.Empty;
}

static string CanonicalComplexityRetryResultFile(string resultFile)
{
    var match = CanonicalComplexityRetryPattern.Match(resultFile);
    return match.Success ? match.Groups[1].Value + "-result.json" : string.Empty;
}

static bool RepoAvailable(string repoRoot)
{
    return GitOutput(repoRoot, "rev-parse", "--is-inside-work-tree") == "true";
}

static bool WorkingTreeClean(string repoRoot)
{
    return GitOutput(repoRoot, "status", "--porcelain") == string.Empty;
}

static bool CommitExists(string repoRoot, string commitSha)
{
    return GitSuccess(repoRoot, "cat-file", "-e", $"{commitSha}^{{commit}}");
}

static bool IsAncestor(string repoRoot, string ancestor, string descendant)
{
    return GitSuccess(repoRoot, "merge-base", "--is-ancestor", ancestor, descendant);
}

static bool GitSuccess(string repoRoot, params string[] args)
{
    var process = new Process
    {
        StartInfo = new ProcessStartInfo("git")
        {
            Arguments = $"-C \"{repoRoot}\" {string.Join(" ", args.Select(a => EscapeArg(a)))}",
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
        },
    };

    process.Start();
    process.WaitForExit();
    return process.ExitCode == 0;
}

static string GitOutput(string repoRoot, params string[] args)
{
    var process = new Process
    {
        StartInfo = new ProcessStartInfo("git")
        {
            Arguments = $"-C \"{repoRoot}\" {string.Join(" ", args.Select(a => EscapeArg(a)))}",
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
        },
    };

    process.Start();
    var output = process.StandardOutput.ReadToEnd();
    process.WaitForExit();
    return process.ExitCode == 0 ? output.Trim() : string.Empty;
}

static string CurrentHead(string repoRoot)
{
    return GitOutput(repoRoot, "rev-parse", "HEAD");
}

static JsonArray GetCommittedFiles(string repoRoot, string commitSha)
{
    try
    {
        var output = GitOutput(repoRoot, "show", "--name-only", "--pretty=format:", commitSha);
        var files = new JsonArray();
        foreach (var line in output.Split('\n'))
        {
            var name = line.Trim();
            if (!string.IsNullOrWhiteSpace(name))
            {
                files.Add(name);
            }
        }

        return files;
    }
    catch
    {
        return new JsonArray();
    }
}

static void WriteJsonAtomic(string path, JsonObject payload)
{
    var parent = Path.GetDirectoryName(path);
    if (!string.IsNullOrWhiteSpace(parent))
    {
        Directory.CreateDirectory(parent);
    }

    var temp = $"{path}.tmp.{Environment.ProcessId}";
    File.WriteAllText(temp, payload.ToJsonString(new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }) + "\n");
    File.Move(temp, path, overwrite: true);
}

static (JsonObject?, string?) LoadJson(string path)
{
    if (string.IsNullOrWhiteSpace(path) || !File.Exists(path) || new FileInfo(path).Length == 0)
    {
        return (null, "missing");
    }

    try
    {
        var node = JsonNode.Parse(File.ReadAllText(path));
        if (node is JsonObject obj)
        {
            return (obj, null);
        }

        return (null, "invalid");
    }
    catch
    {
        return (null, "invalid");
    }
}

static string EscapeArg(string value)
{
    if (string.IsNullOrWhiteSpace(value))
    {
        return "\"\"";
    }

    if (value.Any(char.IsWhiteSpace) || value.Contains('"') || value.Contains('\''))
    {
        return '"' + value.Replace("\"", "\\\"") + '"';
    }

    return value;
}

static int? AsInt(JsonNode? node)
{
    if (node is JsonValue value && value.GetValueKind() == JsonValueKind.Number && value.TryGetValue<int>(out var result))
    {
        return result;
    }

    return null;
}

static string? AsString(JsonNode? node)
{
    return node is JsonValue value && value.GetValueKind() == JsonValueKind.String
        ? value.GetValue<string>()
        : null;
}

static string? AsScalarString(JsonNode? node)
{
    if (node is JsonValue value)
    {
        var kind = value.GetValueKind();
        if (kind == JsonValueKind.String)
        {
            return value.GetValue<string>();
        }
        return value.ToString();
    }
    return null;
}

static void PrintUsage()
{
    Console.WriteLine("Usage: run-with-it-artifacts <command> ...");
}

class ParsedArgs
{
    public string Command { get; set; } = string.Empty;
    public string Role { get; set; } = string.Empty;
    public string Issue { get; set; } = string.Empty;
    public string ResultFile { get; set; } = string.Empty;
    public string DoneFile { get; set; } = string.Empty;
    public string LogFile { get; set; } = string.Empty;
    public string IssueDir { get; set; } = string.Empty;
    public string RepoRoot { get; set; } = string.Empty;
    public string PreSpawnHead { get; set; } = string.Empty;
    public bool Help { get; set; }
}

sealed class CommandError : Exception
{
    public CommandError(string message)
        : base(message)
    {
    }
}

    static string[] PreprocessArgs(string[] args)
    {
        var list = new List<string>();
        foreach (var arg in args)
        {
            if (arg.StartsWith("--", StringComparison.Ordinal) && arg.Contains('='))
            {
                var idx = arg.IndexOf('=');
                list.Add(arg.Substring(0, idx));
                list.Add(arg.Substring(idx + 1));
            }
            else
            {
                list.Add(arg);
            }
        }
        return list.ToArray();
    }

}

static class JsonExtensions
{
    public static JsonValueKind GetValueKind(this JsonNode? node)
    {
        return node is JsonValue value ? value.GetValueKind() : JsonValueKind.Undefined;
    }
}
