#!/usr/bin/env dotnet
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;

class Program
{
    static int Main(string[] args)
    {
        args = PreprocessArgs(args);
        if (args.Length == 0)
    {
        PrintUsage();
        return 2;
    }

    if (HasHelp(args))
    {
        PrintUsage();
        return 0;
    }

    var command = args[0];
    var options = ParseNamedOptions(args.Skip(1).ToArray(), out var parseCode);
    if (parseCode != 0)
    {
        PrintUsage();
        return parseCode;
    }

    try
    {
        return command switch
        {
            "ready-issues" => ReadyIssues(options),
            "ready-missing-context-count" => ReadyMissingContextCount(options),
            "context-file-for" => ContextFileFor(options),
            "parallel-jobs" => ParallelJobs(options),
            "mark-in-progress" => MarkInProgress(options),
            "finalize-issue" => FinalizeIssue(options),
            "analyze-sub-coord-failure" => AnalyzeSubCoordFailure(options),
            "write-sub-coord-recovery-context" => WriteSubCoordRecoveryContext(options),
            "mark-sub-coord-recovery-started" => MarkSubCoordRecoveryStarted(options),
            "mark-sub-coord-recovery-dispatch-failed" => MarkSubCoordRecoveryDispatchFailed(options),
            "write-merge-recovery-context" => WriteMergeRecoveryContext(options),
            "finalize-merge-recovery" => FinalizeMergeRecovery(options),
            "mark-merge-recovery-dispatch-failed" => MarkMergeRecoveryDispatchFailed(options),
            _ => throw new FormatException($"run-with-it-state: unknown command: {command}"),
        };
    }
    catch (FormatException ex)
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

static bool HasHelp(string[] args)
{
    return args.Contains("--help", StringComparer.Ordinal) || args.Contains("-h", StringComparer.Ordinal);
}

static Dictionary<string, string> ParseNamedOptions(string[] args, out int exitCode)
{
    var values = new Dictionary<string, string>(StringComparer.Ordinal);
    for (int i = 0; i < args.Length; i++)
    {
        var arg = args[i];
        if (!arg.StartsWith("--", StringComparison.Ordinal))
        {
            Console.Error.WriteLine($"run-with-it-state: unexpected positional argument: {arg}");
            exitCode = 2;
            return values;
        }

        if (i + 1 >= args.Length)
        {
            Console.Error.WriteLine($"run-with-it-state: expected value after {arg}");
            exitCode = 2;
            return values;
        }

        values[arg] = args[i + 1];
        i += 1;
    }

    exitCode = 0;
    return values;
}

static int ReadyIssues(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var limit = StrictParseInt(Require(options, "--limit"), "--limit");

    var state = LoadJson(stateFile);
    var completed = CompletedIssueNumbers(state);

    var executionPlan = state["execution_plan"] as JsonObject;
    var topoOrder = executionPlan? ["topo_order"] as JsonArray;
    var registry = GetObject(state, "issue_registry");

    var ready = new List<string>();
    if (topoOrder is not null)
    {
        foreach (var issueNode in topoOrder)
        {
            if (ready.Count >= limit)
            {
                break;
            }

            var issue = AsString(issueNode) ?? AsIntText(issueNode);
            if (string.IsNullOrWhiteSpace(issue))
            {
                continue;
            }

            if (!registry.ContainsKey(issue))
            {
                continue;
            }

            var info = registry[issue] as JsonObject;
            if (info is null)
            {
                continue;
            }

            if (AsString(info["status"]) != "pending")
            {
                continue;
            }

            if (!IssueDependenciesCompleted(info, completed))
            {
                continue;
            }

            var context = AsString(info["context_file"]) ?? AsString(info["sub_coord_context_file"]);
            if (!string.IsNullOrWhiteSpace(context))
            {
                ready.Add(issue);
            }
        }
    }

    Console.WriteLine(string.Join(" ", ready));
    return 0;
}

static int ReadyMissingContextCount(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var state = LoadJson(stateFile);
    var completed = CompletedIssueNumbers(state);

    var executionPlan = state["execution_plan"] as JsonObject;
    var topoOrder = executionPlan? ["topo_order"] as JsonArray;
    var registry = GetObject(state, "issue_registry");

    var count = 0;
    if (topoOrder is not null)
    {
        foreach (var issueNode in topoOrder)
        {
            var issue = AsString(issueNode) ?? AsIntText(issueNode);
            if (string.IsNullOrWhiteSpace(issue))
            {
                continue;
            }

            if (!registry.ContainsKey(issue))
            {
                continue;
            }

            var info = registry[issue] as JsonObject;
            if (info is null)
            {
                continue;
            }

            if (AsString(info["status"]) != "pending")
            {
                continue;
            }

            if (!IssueDependenciesCompleted(info, completed))
            {
                continue;
            }

            var context = AsString(info["context_file"]) ?? AsString(info["sub_coord_context_file"]) ?? string.Empty;
            if (string.IsNullOrWhiteSpace(context))
            {
                count += 1;
            }
        }
    }

    Console.WriteLine(count.ToString(CultureInfo.InvariantCulture));
    return 0;
}

static int ContextFileFor(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var issue = Require(options, "--issue");
    var state = LoadJson(stateFile);
    var registry = GetObject(state, "issue_registry");

    if (registry.TryGetPropertyValue(issue, out var infoNode) && infoNode is JsonObject info)
    {
        Console.WriteLine(AsString(info["context_file"]) ?? AsString(info["sub_coord_context_file"]) ?? string.Empty);
    }
    else
    {
        Console.WriteLine(string.Empty);
    }

    return 0;
}

static int ParallelJobs(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var state = LoadJson(stateFile);
    var executionPlan = state["execution_plan"] as JsonObject;
    Console.WriteLine(AsInt(executionPlan? ["parallel_jobs"], 4).ToString(CultureInfo.InvariantCulture));
    return 0;
}

static int MarkInProgress(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var issue = Require(options, "--issue");

    var state = LoadJson(stateFile);
    var entry = IssueEntry(state, issue);

    entry["status"] = "in_progress";
    entry["context_file"] = Require(options, "--context-file");
    entry["issue_dir"] = Require(options, "--issue-dir");
    entry["pid"] = StrictParseInt(Require(options, "--pid"), "--pid");
    entry["started_at"] = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    entry["log_file"] = Require(options, "--log-file");
    entry["done_file"] = Require(options, "--done-file");
    entry["report_file"] = Require(options, "--report-file");

    var active = new List<string>();
    if (state["active_pool_issues"] is JsonArray activePool)
    {
        foreach (var item in activePool)
        {
            var existing = AsString(item) ?? AsIntText(item);
            if (existing is not null && !active.Contains(existing, StringComparer.Ordinal))
            {
                active.Add(existing);
            }
        }
    }

    if (!active.Contains(issue, StringComparer.Ordinal))
    {
        active.Add(issue);
    }

    var next = new JsonArray();
    foreach (var value in active)
    {
        next.Add((JsonNode?)JsonValue.Create(value));
    }

    state["active_pool_issues"] = next;
    SaveJson(stateFile, state);
    return 0;
}

static int FinalizeIssue(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var issue = Require(options, "--issue");
    var reportFile = Require(options, "--report-file");

    var report = LoadReport(reportFile);
    var outcome = AsString(report["outcome"]) ?? "blocked";
    var status = outcome == "merge_failed" ? "merge_recovery" : outcome;

    var state = LoadJson(stateFile);
    var entry = IssueEntry(state, issue);
    entry["status"] = status;

    if (outcome == "merge_failed")
    {
        entry["failed_merge_report_file"] = reportFile;
        entry["blocking_reasons"] = Unique(AsTextList(entry["blocking_reasons"]), new[] { "merge recovery required" }).ToJsonArray();
    }

    if (state["active_pool_issues"] is JsonArray activePool)
    {
        var next = new JsonArray();
        foreach (var item in activePool)
        {
            var value = AsString(item) ?? AsIntText(item);
            if (!string.IsNullOrWhiteSpace(value) && value != issue)
            {
                next.Add((JsonNode?)JsonValue.Create(value));
            }
        }

        state["active_pool_issues"] = next;
    }

    var summary = MakeSummary(issue, status, reportFile, report);
    if (status == "completed")
    {
        AppendSummary(state, "completed", summary);
    }
    else if (status == "merge_recovery")
    {
        AppendSummary(state, "merge_recovery", summary);
    }
    else
    {
        AppendSummary(state, "other", summary);
    }

    state.AppendToArray("ledger_rows", $"STATUS|type=ledger|task={issue}|outcome={status}|report={reportFile}");
    SaveJson(stateFile, state);

    Console.WriteLine(status);
    return 0;
}

static int AnalyzeSubCoordFailure(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var issue = Require(options, "--issue");
    var reportFile = Require(options, "--report-file");
    var maxAttempts = OptionInt(options, "--max-attempts", 2);

    var state = LoadJson(stateFile);
    var report = LoadReport(reportFile);
    var outcome = AsString(report["outcome"]);

    var entry = IssueEntry(state, issue);

    if (!string.IsNullOrWhiteSpace(outcome) && TERMINAL_OUTCOMES.Contains(outcome))
    {
        var terminal = CompactWorkerDecision(
            "finalize",
            "terminal-report-present",
            issue,
            IssueDirForReport(state, issue, reportFile),
            ResolveSubStateFile(state, issue, reportFile),
            recoveryAttempt: AsInt(entry["sub_coord_recovery_attempts"], 0) + 1,
            maxAttempts: maxAttempts);

        Console.WriteLine(terminal.ToJsonString(new JsonSerializerOptions { WriteIndented = false }));
        return 0;
    }

    var issueDir = IssueDirForReport(state, issue, reportFile);
    var subStateFile = ResolveSubStateFile(state, issue, reportFile);
    var attempt = AsInt(entry["sub_coord_recovery_attempts"], 0) + 1;

    if (attempt > maxAttempts)
    {
        var block = CompactWorkerDecision(
            "block",
            "sub-coordinator-recovery-attempts-exhausted",
            issue,
            issueDir,
            subStateFile,
            recoveryAttempt: attempt,
            maxAttempts: maxAttempts);

        Console.WriteLine(block.ToJsonString(new JsonSerializerOptions { WriteIndented = false }));
        return 0;
    }

    var subState = LoadOptionalJson(subStateFile);
    if (subState is null || subState.Count == 0)
    {
        var missing = CompactWorkerDecision(
            "block",
            "missing-sub-state",
            issue,
            issueDir,
            subStateFile,
            recoveryAttempt: attempt,
            maxAttempts: maxAttempts);

        Console.WriteLine(missing.ToJsonString(new JsonSerializerOptions { WriteIndented = false }));
        return 0;
    }

    var phase = AsString(subState["phase"]);
    var inFlight = subState["in_flight_agents"] as JsonArray;
    var workerDecisions = new List<(JsonObject Worker, JsonObject State, bool Done, bool Result)>();

    if (inFlight is not null)
    {
        foreach (var node in inFlight)
        {
            if (node is not JsonObject worker)
            {
                continue;
            }

            var stateFilePath = AsString(worker["state_file"]) ?? string.Empty;
            var doneFile = AsString(worker["done_file"]) ?? string.Empty;
            var resultFile = AsString(worker["result_file"]) ?? string.Empty;
            var workerState = LoadOptionalJson(stateFilePath) ?? new JsonObject();

            var donePresent = !string.IsNullOrWhiteSpace(doneFile) && File.Exists(doneFile) && new FileInfo(doneFile).Length > 0 || AsBool(workerState["done"]);
            var resultPresent = !string.IsNullOrWhiteSpace(resultFile) && FileHasJson(resultFile) || AsBool(workerState["result_present"]);

            workerDecisions.Add((worker, workerState, donePresent, resultPresent));
        }
    }

    foreach (var decision in workerDecisions)
    {
        var workerStateName = AsString(decision.State["state"]);
        if (!string.IsNullOrWhiteSpace(workerStateName)
            && LIVE_WORKER_STATES.Contains(workerStateName)
            && !(decision.Done && decision.Result))
        {
            var wait = CompactWorkerDecision(
                "wait_worker",
                "in-flight-worker-running",
                issue,
                issueDir,
                subStateFile,
                phase,
                decision.Worker,
                decision.State,
                attempt,
                maxAttempts);

            Console.WriteLine(wait.ToJsonString(new JsonSerializerOptions { WriteIndented = false }));
            return 0;
        }
    }

    foreach (var decision in workerDecisions)
    {
        var workerStateName = AsString(decision.State["state"]);
        if ((workerStateName is not null && FINISHED_WORKER_STATES.Contains(workerStateName)) || decision.Done || decision.Result)
        {
            var reason = workerStateName == "completed" || decision.Result ? "in-flight-worker-finished" : "in-flight-worker-failed";
            var spawn = CompactWorkerDecision(
                "spawn_recovery",
                reason,
                issue,
                issueDir,
                subStateFile,
                phase,
                decision.Worker,
                decision.State,
                attempt,
                maxAttempts);

            Console.WriteLine(spawn.ToJsonString(new JsonSerializerOptions { WriteIndented = false }));
            return 0;
        }
    }

    var fallback = CompactWorkerDecision(
        "spawn_recovery",
        "sub-state-present-no-in-flight-worker",
        issue,
        issueDir,
        subStateFile,
        phase,
        null,
        null,
        attempt,
        maxAttempts);

    Console.WriteLine(fallback.ToJsonString(new JsonSerializerOptions { WriteIndented = false }));
    return 0;
}

static int WriteSubCoordRecoveryContext(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var issue = Require(options, "--issue");
    var contextFile = Require(options, "--context-file");
    var attempt = StrictParseInt(Require(options, "--attempt"), "--attempt");
    var reason = Require(options, "--reason");

    var state = LoadJson(stateFile);
    var registry = GetObject(state, "issue_registry");

    string originalContext = string.Empty;
    JsonObject? entry = null;
    if (registry.TryGetPropertyValue(issue, out var entryNode) && entryNode is JsonObject e)
    {
        entry = e;
        originalContext = AsString(entry["sub_coord_original_context_file"]) ??
                          AsString(entry["context_file"]) ??
                          AsString(entry["sub_coord_context_file"]) ??
                          string.Empty;
    }

    var issueDir = GetIssueDir(state, issue);
    if (string.IsNullOrWhiteSpace(issueDir) && entry != null)
    {
        var reportFile = AsString(entry["report_file"]);
        if (!string.IsNullOrWhiteSpace(reportFile))
        {
            issueDir = Path.GetDirectoryName(reportFile) ?? string.Empty;
        }
    }
    var subStateFile = string.IsNullOrWhiteSpace(issueDir) ? string.Empty : Path.Combine(issueDir, "sub-state.json");

    var originalText = string.Empty;
    if (!string.IsNullOrWhiteSpace(originalContext) && File.Exists(originalContext))
    {
        originalText = File.ReadAllText(originalContext);
    }

    Directory.CreateDirectory(Path.GetDirectoryName(contextFile) ?? ".");
    using var writer = new StreamWriter(contextFile, false);
    writer.WriteLine("SUB_COORD_RECOVERY_MODE=1");
    writer.WriteLine($"SUB_COORD_RECOVERY_ATTEMPT={attempt}");
    writer.WriteLine($"SUB_COORD_RECOVERY_REASON={reason}");
    writer.WriteLine($"SUB_COORD_STATE_FILE={subStateFile}");
    writer.WriteLine($"SUB_COORD_ORIGINAL_CONTEXT_FILE={originalContext}");
    writer.WriteLine();
    writer.WriteLine("Recovery instructions:");
    writer.WriteLine("Do not restart from scratch.");
    writer.WriteLine("Read SUB_COORD_STATE_FILE before doing any phase work.");
    writer.WriteLine("Analyze in_flight_agents and their state_file, done_file, and result_file paths.");
    writer.WriteLine("If a worker result is valid, process it and continue from the next phase.");
    writer.WriteLine("Never rerun a phase that already has a valid result artifact.");
    writer.WriteLine("If a worker failed without a valid result, apply the existing worker artifact recovery contract.");
    writer.WriteLine();
    writer.WriteLine("Original sub-coordinator context follows:");
    writer.Write(originalText);
    if (!string.IsNullOrEmpty(originalText) && !originalText.EndsWith("\n", StringComparison.Ordinal))
    {
        writer.WriteLine();
    }

    return 0;
}

static int MarkSubCoordRecoveryStarted(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var issue = Require(options, "--issue");
    var attempt = StrictParseInt(Require(options, "--attempt"), "--attempt");
    var reason = Require(options, "--reason");
    var contextFile = Require(options, "--context-file");

    var state = LoadJson(stateFile);
    var entry = IssueEntry(state, issue);

    if (!entry.ContainsKey("sub_coord_original_context_file"))
    {
        var originalContext = AsString(entry["context_file"]) ?? AsString(entry["sub_coord_context_file"]) ?? string.Empty;
        if (!string.IsNullOrWhiteSpace(originalContext))
        {
            entry["sub_coord_original_context_file"] = originalContext;
        }
    }

    entry["sub_coord_recovery_attempts"] = attempt;
    entry["sub_coord_recovery_last_reason"] = reason;
    entry["sub_coord_recovery_context_file"] = contextFile;

    SaveJson(stateFile, state);
    return 0;
}

static int MarkSubCoordRecoveryDispatchFailed(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var issue = Require(options, "--issue");
    var reportFile = Require(options, "--report-file");

    var state = LoadJson(stateFile);
    var entry = IssueEntry(state, issue);

    entry["sub_coord_recovery_dispatch_failed"] = true;
    entry["sub_coord_recovery_last_report_file"] = reportFile;
    entry["blocking_reasons"] = Unique(
        AsTextList(entry["blocking_reasons"]),
        new[] { "sub-coordinator recovery dispatcher failed" }
    ).ToJsonArray();

    SaveJson(stateFile, state);
    return 0;
}

static int WriteMergeRecoveryContext(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var issue = Require(options, "--issue");
    var contextFile = Require(options, "--context-file");
    var recoveryReportFile = Require(options, "--recovery-report-file");

    var state = LoadJson(stateFile);
    var entry = IssueEntry(state, issue);

    var payload = new JsonObject
    {
        ["issue"] = new JsonObject
        {
            ["number"] = ParseInt(issue),
            ["title"] = AsString(entry["title"]) ?? string.Empty,
            ["deps"] = entry["deps"]?.DeepClone() ?? new JsonArray(),
            ["issue_branch"] = entry["issue_branch"]?.DeepClone(),
            ["worktree_path"] = entry["worktree_path"]?.DeepClone(),
        },
        ["run_branch"] = state["run_branch"]?.DeepClone() ?? new JsonObject(),
        ["failed_merge_report_file"] = AsString(entry["failed_merge_report_file"]) ?? AsString(entry["report_file"]) ?? string.Empty,
        ["failed_merge_summary"] = new JsonObject
        {
            ["blocking_reasons"] = entry["blocking_reasons"]?.DeepClone() ?? new JsonArray(),
            ["dependency_proof"] = entry["dependency_proof"]?.DeepClone(),
        },
        ["completed_summaries"] = state["completed_summaries"]?.DeepClone() ?? new JsonArray(),
    };

    Directory.CreateDirectory(Path.GetDirectoryName(contextFile) ?? ".");
    using var writer = new StreamWriter(contextFile, false);
    writer.WriteLine("You are receiving merge recovery task data only.");
    writer.WriteLine("Resolve only the failed merge for this issue. Do not select new issues, close GitHub issues, create a final PR, or modify main-state.json.");
    writer.WriteLine();
    writer.WriteLine($"MERGE_RECOVERY_REPORT_FILE={recoveryReportFile}");
    writer.WriteLine($"RUN_WITH_IT_RESULT_FILE={recoveryReportFile}");
    writer.WriteLine("OUTCOME=completed");
    writer.WriteLine();
    writer.WriteLine("MERGE_RECOVERY_CONTEXT_JSON:");
    writer.WriteLine(payload.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
    return 0;
}

static int FinalizeMergeRecovery(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var issue = Require(options, "--issue");
    var reportFile = Require(options, "--report-file");

    var report = LoadReport(reportFile);
    var outcome = AsString(report["outcome"]) ?? "blocked";
    var status = outcome == "completed" ? "completed" : outcome;

    if (status != "completed" && status != "failed-merge" && status != "blocked")
    {
        status = "blocked";
    }

    var state = LoadJson(stateFile);
    var entry = IssueEntry(state, issue);

    entry["status"] = status;
    entry["merge_recovery_report_file"] = reportFile;

    if (status == "completed")
    {
        var current = AsTextList(entry["blocking_reasons"]).Where(item => item != "merge recovery required");
        entry["blocking_reasons"] = current.ToJsonArray();
        entry["commit_sha"] = AsString(report["merge_sha"]) ?? AsString(report["commit_sha"]);
    }
    else
    {
        entry["blocking_reasons"] = Unique(AsTextList(entry["blocking_reasons"]), AsTextList(report["blocking_reasons"])).ToJsonArray();
    }

    var summary = MakeSummary(issue, status, reportFile, report);
    if (status == "completed")
    {
        summary["commit_sha"] = CloneNode(report["merge_sha"]) ?? CloneNode(report["commit_sha"]);
    }

    if (status == "completed")
    {
        AppendSummary(state, "completed", summary);
    }
    else
    {
        AppendSummary(state, "merge_recovery", summary);
    }

    state.AppendToArray("ledger_rows", $"STATUS|type=ledger|task={issue}|outcome={status}|report={reportFile}|role=merge-recovery");
    SaveJson(stateFile, state);

    Console.WriteLine(status);
    return 0;
}

static int MarkMergeRecoveryDispatchFailed(Dictionary<string, string> options)
{
    var stateFile = Require(options, "--state-file");
    var issue = Require(options, "--issue");
    var reportFile = Require(options, "--report-file");

    var state = LoadJson(stateFile);
    var entry = IssueEntry(state, issue);

    entry["status"] = "blocked";
    entry["merge_recovery_report_file"] = reportFile;
    entry["blocking_reasons"] = Unique(
        AsTextList(entry["blocking_reasons"]),
        new[] { "merge recovery dispatcher failed" }
    ).ToJsonArray();

    SaveJson(stateFile, state);
    return 0;
}

static void PrintUsage()
{
    Console.WriteLine("Usage: run-with-it-state <command> ...");
}

static JsonObject MakeSummary(string issue, string status, string reportFile, JsonObject report)
{
    var issueInt = ParseInt(issue);
    var metrics = ReportFileMetrics(report);

    return new JsonObject
    {
        ["issue"] = issueInt,
        ["outcome"] = status,
        ["summary"] = CloneNode(report["summary"]),
        ["verification"] = report["verification"] is JsonObject verification ? verification.DeepClone() : new JsonObject(),
        ["report_file"] = reportFile,
        ["model_usage"] = CompactModelUsage(report),
        ["files_modified_count"] = metrics["files_modified_count"],
        ["lines_added"] = metrics["lines_added"],
        ["lines_deleted"] = metrics["lines_deleted"],
        ["review_cycles"] = CloneNode(report["review_cycles"]) ?? JsonValue.Create(0),
        ["commit_sha"] = CloneNode(report["commit_sha"]),
    };
}

static readonly HashSet<string> TERMINAL_OUTCOMES = new(StringComparer.Ordinal)
{
    "completed",
    "failed-review",
    "blocked",
    "merge_failed",
    "failed-merge",
};

static readonly HashSet<string> LIVE_WORKER_STATES = new(StringComparer.Ordinal)
{
    "ready",
    "starting",
    "running",
    "quiet",
    "stalled",
};

static readonly HashSet<string> FINISHED_WORKER_STATES = new(StringComparer.Ordinal)
{
    "completed",
    "failed",
};

static JsonObject LoadJson(string path)
{
    var text = File.ReadAllText(path);
    var node = JsonNode.Parse(text);
    if (node is not JsonObject obj)
    {
        throw new InvalidOperationException($"invalid JSON object: {path}");
    }

    return obj;
}

static JsonObject LoadReport(string path)
{
    if (!File.Exists(path) || new FileInfo(path).Length == 0)
    {
        return new JsonObject();
    }

    try
    {
        return LoadJson(path);
    }
    catch
    {
        return new JsonObject();
    }
}

static JsonObject? LoadOptionalJson(string path)
{
    if (string.IsNullOrWhiteSpace(path) || !File.Exists(path) || new FileInfo(path).Length == 0)
    {
        return null;
    }

    try
    {
        var node = LoadJson(path);
        return node;
    }
    catch
    {
        return null;
    }
}

static bool FileHasJson(string path)
{
    if (string.IsNullOrWhiteSpace(path) || !File.Exists(path) || new FileInfo(path).Length == 0)
    {
        return false;
    }

    try
    {
        JsonNode.Parse(File.ReadAllText(path));
        return true;
    }
    catch
    {
        return false;
    }
}

static JsonObject IssueEntry(JsonObject state, string issue)
{
    var registry = state["issue_registry"] as JsonObject;
    if (registry is null)
    {
        registry = new JsonObject();
        state["issue_registry"] = registry;
    }

    if (!registry.ContainsKey(issue) || registry[issue] is not JsonObject existing)
    {
        var created = new JsonObject();
        registry[issue] = created;
        return created;
    }

    return existing;
}

static string GetIssueDir(JsonObject state, string issue)
{
    var registry = GetObject(state, "issue_registry");
    if (registry.TryGetPropertyValue(issue, out var entryNode) && entryNode is JsonObject entry)
    {
        var issueDir = AsString(entry["issue_dir"]);
        if (!string.IsNullOrWhiteSpace(issueDir))
        {
            return issueDir;
        }
    }

    return string.Empty;
}

static string IssueDirForReport(JsonObject state, string issue, string reportFile)
{
    var issueDir = GetIssueDir(state, issue);
    if (!string.IsNullOrWhiteSpace(issueDir))
    {
        return issueDir;
    }

    return string.IsNullOrWhiteSpace(reportFile) ? string.Empty : Path.GetDirectoryName(reportFile) ?? string.Empty;
}

static string ResolveSubStateFile(JsonObject state, string issue, string reportFile)
{
    var issueDir = IssueDirForReport(state, issue, reportFile);
    return string.IsNullOrWhiteSpace(issueDir) ? string.Empty : Path.Combine(issueDir, "sub-state.json");
}

static Dictionary<string, int> ReportFileMetrics(JsonObject report)
{
    var files = report["files_modified"] as JsonArray;
    var fileItems = files is null
        ? new List<JsonObject>()
        : files.OfType<JsonObject>().ToList();

    int filesModified;
    int linesAdded;
    int linesDeleted;

    var explicitCount = IntFromNode(report["files_modified_count"]);
    if (explicitCount.HasValue)
    {
        filesModified = explicitCount.Value;
    }
    else
    {
        filesModified = fileItems.Count;
    }

    var explicitAdded = IntFromNode(report["lines_added"]);
    if (explicitAdded.HasValue)
    {
        linesAdded = explicitAdded.Value;
    }
    else
    {
        linesAdded = 0;
        foreach (var item in fileItems)
        {
            linesAdded += IntFromNode(item["lines_added"]) ?? 0;
        }
    }

    var explicitDeleted = IntFromNode(report["lines_deleted"]);
    if (explicitDeleted.HasValue)
    {
        linesDeleted = explicitDeleted.Value;
    }
    else
    {
        linesDeleted = 0;
        foreach (var item in fileItems)
        {
            linesDeleted += IntFromNode(item["lines_deleted"]) ?? 0;
        }
    }

    return new Dictionary<string, int>
    {
        ["files_modified_count"] = filesModified,
        ["lines_added"] = linesAdded,
        ["lines_deleted"] = linesDeleted,
    };
}

static JsonArray CompactModelUsage(JsonObject report)
{
    var rows = new JsonArray();
    if (report["model_usage"] is not JsonArray usage)
    {
        return rows;
    }

    foreach (var item in usage)
    {
        if (item is not JsonObject usageRow)
        {
            continue;
        }

        var cycleVal = AsIntOrNullNode(usageRow["cycle"]);
        rows.Add((JsonNode)new JsonObject
        {
            ["role"] = AsString(usageRow["role"]) ?? "unknown",
            ["cycle"] = cycleVal.HasValue ? JsonValue.Create(cycleVal.Value) : null,
            ["agent"] = AsString(usageRow["agent"]) ?? "unknown",
            ["model"] = AsString(usageRow["model"]) ?? "unknown",
            ["selection_reason"] = AsString(usageRow["selection_reason"]) ?? AsString(usageRow["reason"]) ?? "unknown",
        });
    }

    return rows;
}

static void AppendSummary(JsonObject state, string status, JsonObject summary)
{
    var list = status switch
    {
        "merge_recovery" => GetArray(state, "merge_recovery_summaries"),
        _ => GetArray(state, "completed_summaries"),
    };

    if (status == "other" && !state.TryGetPropertyValue("completed_summaries", out _))
    {
        list = GetArray(state, "completed_summaries");
    }

    list.Add((JsonNode)summary);
    if (!state.ContainsKey(status == "merge_recovery" ? "merge_recovery_summaries" : "completed_summaries"))
    {
        if (status == "merge_recovery")
        {
            state["merge_recovery_summaries"] = list;
        }
        else
        {
            state["completed_summaries"] = list;
        }
    }
}

static JsonObject CompactWorkerDecision(
    string action,
    string reason,
    string issue,
    string issueDir,
    string subStateFile,
    string? phase = null,
    JsonObject? worker = null,
    JsonObject? workerState = null,
    int recoveryAttempt = 0,
    int maxAttempts = 2)
{
    worker ??= new JsonObject();
    workerState ??= new JsonObject();

    return new JsonObject
    {
        ["action"] = action,
        ["reason"] = reason,
        ["issue"] = issue,
        ["issue_dir"] = issueDir,
        ["sub_state_file"] = subStateFile,
        ["phase"] = phase,
        ["worker_role"] = AsString(worker["role"]),
        ["worker_cycle"] = worker["cycle"]?.DeepClone(),
        ["worker_state"] = AsString(workerState["state"]),
        ["worker_state_file"] = AsString(worker["state_file"]) ?? AsString(workerState["state_file"]),
        ["worker_done_file"] = AsString(worker["done_file"]) ?? AsString(workerState["done_file"]),
        ["worker_result_file"] = AsString(worker["result_file"]) ?? AsString(workerState["result_file"]),
        ["recovery_attempt"] = recoveryAttempt,
        ["max_recovery_attempts"] = maxAttempts,
    };
}

static int OptionInt(Dictionary<string, string> options, string key, int defaultValue)
{
    if (!options.TryGetValue(key, out var value))
    {
        return defaultValue;
    }

    return StrictParseInt(value, key);
}

static JsonObject GetObject(JsonObject source, string key)
{
    return source[key] as JsonObject ?? new JsonObject();
}

static JsonArray GetArray(JsonObject source, string key)
{
    return source[key] as JsonArray ?? new JsonArray();
}

static IEnumerable<string> AsTextList(JsonNode? node)
{
    if (node is not JsonArray array)
    {
        yield break;
    }

    foreach (var item in array)
    {
        var text = AsString(item);
        if (!string.IsNullOrWhiteSpace(text))
        {
            yield return text;
        }
    }
}

static IEnumerable<string> Unique(IEnumerable<string> first, IEnumerable<string> second)
{
    var seen = new HashSet<string>(StringComparer.Ordinal);
    foreach (var value in first)
    {
        if (seen.Add(value))
        {
            yield return value;
        }
    }

    foreach (var value in second)
    {
        if (seen.Add(value))
        {
            yield return value;
        }
    }
}

static HashSet<int> CompletedIssueNumbers(JsonObject state)
{
    var completed = new HashSet<int>();
    var registry = GetObject(state, "issue_registry");

    foreach (var pair in registry)
    {
        if (pair.Value is not JsonObject issueInfo)
        {
            continue;
        }

        if (AsString(issueInfo["status"]) != "completed")
        {
            continue;
        }

        if (int.TryParse(pair.Key, NumberStyles.Integer, CultureInfo.InvariantCulture, out var issue))
        {
            completed.Add(issue);
        }
    }

    return completed;
}

static bool IssueDependenciesCompleted(JsonObject issueInfo, HashSet<int> completed)
{
    var deps = issueInfo["deps"] as JsonArray;
    if (deps is null)
    {
        return true;
    }

    foreach (var depNode in deps)
    {
        int dep;
        if (depNode is JsonValue depValue && depValue.GetValueKind() == JsonValueKind.Number)
        {
            if (!depValue.TryGetValue<int>(out dep))
            {
                return false;
            }
        }
        else if (!int.TryParse(AsString(depNode), NumberStyles.Integer, CultureInfo.InvariantCulture, out dep))
        {
            return false;
        }

        if (!completed.Contains(dep))
        {
            return false;
        }
    }

    return true;
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

static string Require(Dictionary<string, string> values, string key)
{
    if (!values.TryGetValue(key, out var value) || value.Length == 0)
    {
        throw new FormatException($"run-with-it-state: missing required argument: {key}");
    }

    return value;
}

static int ParseInt(string value, int fallback = 0)
{
    return int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed) ? parsed : fallback;
}

static int StrictParseInt(string value, string argName)
{
    if (!int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsed))
    {
        throw new FormatException($"run-with-it-state: invalid integer value for {argName}: {value}");
    }
    return parsed;
}

static int AsInt(JsonNode? node, int fallback)
{
    var value = IntFromNode(node);
    return value ?? fallback;
}

static int? AsInt(JsonNode? node)
{
    return IntFromNode(node);
}

static int? AsIntOrNullNode(JsonNode? node)
{
    return IntFromNode(node);
}

static int? IntFromNode(JsonNode? node)
{
    if (node is JsonValue value && value.GetValueKind() == JsonValueKind.Number)
    {
        if (value.TryGetValue<int>(out var intValue))
        {
            return intValue;
        }

        if (value.TryGetValue<long>(out var longValue) && longValue is >= int.MinValue and <= int.MaxValue)
        {
            return (int)longValue;
        }
    }

    return null;
}

static JsonNode? CloneNode(JsonNode? node)
{
    return node?.DeepClone();
}


static string? AsString(JsonNode? node)
{
    return node is JsonValue value && value.GetValueKind() == JsonValueKind.String
        ? value.GetValue<string>()
        : null;
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

static bool AsBool(JsonNode? node)
{
    return node is JsonValue value && value.GetValueKind() == JsonValueKind.True;
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

static class JsonArrayExtensions
{
    public static void AppendToArray(this JsonObject obj, string key, string value)
    {
        var array = obj[key] as JsonArray;
        if (array is null)
        {
            array = new JsonArray();
            obj[key] = array;
        }

        array.Add((JsonNode?)JsonValue.Create(value));
    }

    public static JsonArray ToJsonArray(this IEnumerable<string> items)
    {
        var array = new JsonArray();
        foreach (var item in items)
        {
            array.Add((JsonNode?)JsonValue.Create(item));
        }
        return array;
    }
}
