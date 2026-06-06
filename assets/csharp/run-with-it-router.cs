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
    static int Main(string[] args)
    {
        args = PreprocessArgs(args);
        var parsed = ParseArguments(args);
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
        var registry = ReadJsonFile(parsed.RegistryFile, defaultValue: null);
        var baseLevel = parsed.ComplexityLevel is not null
            ? parsed.ComplexityLevel
            : ScoreToLevel(registry, parsed.ComplexityScore!.Value);

        if (parsed.Record)
        {
            using var _ = DirectoryLock.Enter(parsed.LedgerFile);
            var ledger = NormalizeLedger(ReadJsonFile(parsed.LedgerFile, defaultValue: new JsonObject()));
            var selected = SelectPair(
                registry,
                ledger,
                parsed.Role,
                baseLevel,
                parsed.DetectedAgents,
                parsed.Allowlist,
                parsed.Denylist,
                parsed.ForcedAgent,
                parsed.ForcedModel,
                parsed.ExcludeModel
            );
            AppendDecision(ledger, selected);
            WriteJsonAtomic(parsed.LedgerFile, ledger);
            var outputRecord = BuildOutput(registry, ledger, parsed.LedgerFile, selected, updated: true);
            Console.WriteLine(outputRecord.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }

        var snapshot = NormalizeLedger(ReadJsonFile(parsed.LedgerFile, defaultValue: new JsonObject()));
        var selection = SelectPair(
            registry,
            snapshot,
            parsed.Role,
            baseLevel,
            parsed.DetectedAgents,
            parsed.Allowlist,
            parsed.Denylist,
            parsed.ForcedAgent,
            parsed.ForcedModel,
            parsed.ExcludeModel
        );

        var output = BuildOutput(registry, snapshot, parsed.LedgerFile, selection, updated: false);
        Console.WriteLine(output.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        return 0;
    }
    catch (CommandError error)
    {
        Console.Error.WriteLine($"run-with-it-router: {error.Message}");
        return 2;
    }
    catch (Exception error)
    {
        Console.Error.WriteLine($"run-with-it-router: {error.Message}");
        return 2;
    }
}

static ParsedArguments? ParseArguments(string[] args)
{
    if (args.Length == 0)
    {
        return null;
    }

    var parsed = new ParsedArguments
    {
        RegistryFile = string.Empty,
        LedgerFile = string.Empty,
        Role = string.Empty,
        ComplexityLevel = null,
        ComplexityScore = null,
        DetectedAgents = new HashSet<string>(StringComparer.Ordinal),
        Allowlist = new HashSet<string>(StringComparer.Ordinal),
        Denylist = new HashSet<string>(StringComparer.Ordinal),
        Record = false,
        Help = false,
    };

    if (args.Contains("--help") || args.Contains("-h"))
    {
        parsed.Help = true;
        return parsed;
    }

    bool detectedAgentsSpecified = false;

    for (int i = 0; i < args.Length; i++)
    {
        var arg = args[i];
        if (arg == "--record")
        {
            parsed.Record = true;
            continue;
        }

        if (!arg.StartsWith("--", StringComparison.Ordinal))
        {
            return null;
        }

        if (i + 1 >= args.Length)
        {
            return null;
        }

        var value = args[i + 1];
        i += 1;

        switch (arg)
        {
            case "--registry-file":
                parsed.RegistryFile = value;
                break;
            case "--ledger-file":
                parsed.LedgerFile = value;
                break;
            case "--role":
                parsed.Role = value;
                break;
            case "--complexity-level":
                parsed.ComplexityLevel = value;
                break;
            case "--complexity-score":
                if (!int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var score))
                {
                    throw new CommandError("invalid complexity score");
                }

                parsed.ComplexityScore = score;
                break;
            case "--detected-agents":
                parsed.DetectedAgents = SplitCsv(value);
                detectedAgentsSpecified = true;
                break;
            case "--allowlist":
                parsed.Allowlist = SplitCsv(value);
                break;
            case "--denylist":
                parsed.Denylist = SplitCsv(value);
                break;
            case "--forced-agent":
                parsed.ForcedAgent = string.IsNullOrWhiteSpace(value) ? null : value;
                break;
            case "--forced-model":
                parsed.ForcedModel = string.IsNullOrWhiteSpace(value) ? null : value;
                break;
            case "--exclude-model":
                parsed.ExcludeModel = string.IsNullOrWhiteSpace(value) ? null : value;
                break;
            default:
                return null;
        }
    }

    if (string.IsNullOrWhiteSpace(parsed.RegistryFile)
        || string.IsNullOrWhiteSpace(parsed.LedgerFile)
        || string.IsNullOrWhiteSpace(parsed.Role)
        || (string.IsNullOrWhiteSpace(parsed.ComplexityLevel) && parsed.ComplexityScore is null))
    {
        return null;
    }

    if (!new HashSet<string>(StringComparer.Ordinal) { "complexity", "impl", "review", "modify", "merge-recovery" }.Contains(parsed.Role))
    {
        throw new CommandError($"invalid role: {parsed.Role}");
    }

    if (!detectedAgentsSpecified)
    {
        parsed.DetectedAgents = new HashSet<string>(DefaultAgents, StringComparer.Ordinal);
    }

    return parsed;
}

class ParsedArguments
{
    public string RegistryFile { get; set; } = string.Empty;
    public string LedgerFile { get; set; } = string.Empty;
    public string Role { get; set; } = string.Empty;
    public string? ComplexityLevel { get; set; }
    public int? ComplexityScore { get; set; }
    public HashSet<string> DetectedAgents { get; set; } = new HashSet<string>(StringComparer.Ordinal);
    public HashSet<string> Allowlist { get; set; } = new HashSet<string>(StringComparer.Ordinal);
    public HashSet<string> Denylist { get; set; } = new HashSet<string>(StringComparer.Ordinal);
    public string? ForcedAgent { get; set; }
    public string? ForcedModel { get; set; }
    public string? ExcludeModel { get; set; }
    public bool Record { get; set; }
    public bool Help { get; set; }
}

sealed class CommandError : Exception
{
    public CommandError(string message)
        : base(message)
    {
    }
}

static HashSet<string> SplitCsv(string value)
{
    var values = new HashSet<string>(StringComparer.Ordinal);
    foreach (var item in value.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
    {
        values.Add(item);
    }

    return values;
}

static string FormatList(IEnumerable<string> items)
{
    return "[" + string.Join(", ", items.Select(x => $"'{x}'")) + "]";
}

static readonly string[] DefaultAgents = { "codex", "agy", "github-copilot", "claude" };
static readonly string[] BandOrder = { "quite-easy", "easy", "medium", "medium-hard", "complex", "holy-fuck" };
static readonly Dictionary<string, string> ReviewBump = new(StringComparer.Ordinal)
{
    ["quite-easy"] = "easy",
    ["easy"] = "medium",
    ["medium"] = "medium-hard",
    ["medium-hard"] = "complex",
    ["complex"] = "holy-fuck",
    ["holy-fuck"] = "holy-fuck",
};
const double GLOBAL_DEBT_WEIGHT = 1.5;

static string RoutingLevel(string role, string level)
{
    return role == "review" && ReviewBump.TryGetValue(level, out var bumped)
        ? bumped
        : level;
}


static JsonObject ReadJsonFile(string path, JsonObject? defaultValue)
{
    if (!File.Exists(path))
    {
        if (defaultValue is null)
        {
            throw new CommandError($"missing JSON file: {path}");
        }

        return new JsonObject(defaultValue);
    }

    try
    {
        var text = File.ReadAllText(path);
        var node = JsonNode.Parse(text);
        if (node is not JsonObject obj)
        {
            if (defaultValue is null)
            {
                throw new CommandError($"invalid JSON in {path}");
            }

            return new JsonObject(defaultValue);
        }

        return obj;
    }
    catch (JsonException ex)
    {
        if (defaultValue is null)
        {
            throw new CommandError($"invalid JSON in {path}: {ex.Message}");
        }

        return new JsonObject(defaultValue);
    }
}

static void WriteJsonAtomic(string path, JsonObject value)
{
    Directory.CreateDirectory(Path.GetDirectoryName(path) ?? ".");
    var temp = $"{path}.{Environment.ProcessId}.tmp";
    File.WriteAllText(temp, value.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) + "\n");
    File.Move(temp, path, overwrite: true);
}

static void PrintUsage()
{
    Console.WriteLine("Usage: run-with-it-router --registry-file <file> --ledger-file <file> --role <role>");
}

sealed class DirectoryLock : IDisposable
{
    private readonly string _lockPath;
    private readonly FileStream? _fs;

    private DirectoryLock(string lockPath, FileStream? fs)
    {
        _lockPath = lockPath;
        _fs = fs;
    }

    public static DirectoryLock Enter(string ledgerPath)
    {
        var lockPath = $"{ledgerPath}.lock";
        var start = Stopwatch.GetTimestamp();
        var timeoutTicks = Stopwatch.Frequency * 10;

        var dir = Path.GetDirectoryName(lockPath);
        if (!string.IsNullOrEmpty(dir))
        {
            Directory.CreateDirectory(dir);
        }

        while (true)
        {
            try
            {
                var fs = new FileStream(lockPath, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
                return new DirectoryLock(lockPath, fs);
            }
            catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
            {
                if (Stopwatch.GetTimestamp() - start > timeoutTicks)
                {
                    throw new CommandError($"timed out waiting for ledger lock: {lockPath}");
                }

                Thread.Sleep(50);
            }
        }
    }

    public void Dispose()
    {
        try
        {
            _fs?.Dispose();
        }
        catch
        {
        }
        try
        {
            if (File.Exists(_lockPath))
            {
                File.Delete(_lockPath);
            }
            else if (Directory.Exists(_lockPath))
            {
                Directory.Delete(_lockPath);
            }
        }
        catch
        {
        }
    }
}

static JsonObject NormalizeLedger(JsonObject ledger)
{
    if (!ledger.ContainsKey("schema_version"))
    {
        ledger["schema_version"] = 1;
    }

    if (ledger["decisions"] is not JsonArray decisions)
    {
        decisions = new JsonArray();
        ledger["decisions"] = decisions;
    }

    var totals = ledger["totals"] as JsonObject;
    if (totals is null)
    {
        totals = new JsonObject();
        ledger["totals"] = totals;
    }

    var agents = totals["agents"] as JsonObject;
    if (agents is null)
    {
        agents = new JsonObject();
        totals["agents"] = agents;
    }

    var roles = totals["roles"] as JsonObject;
    if (roles is null)
    {
        roles = new JsonObject();
        totals["roles"] = roles;
    }

    var rebuildAgentTotals = agents.Count == 0;
    var rebuildRoleTotals = roles.Count == 0;
    if ((rebuildAgentTotals || rebuildRoleTotals) && decisions.Count > 0)
    {
        foreach (var item in decisions.OfType<JsonObject>())
        {
            var agent = AsString(item["agent"]);
            if (string.IsNullOrWhiteSpace(agent))
            {
                continue;
            }

            if (rebuildAgentTotals)
            {
                agents[agent] = AsIntOrDefault(agents[agent]) + 1;
            }

            if (!rebuildRoleTotals)
            {
                continue;
            }

            var role = AsString(item["role"]);
            if (string.IsNullOrWhiteSpace(role))
            {
                continue;
            }

            var roleTotals = roles[role] as JsonObject;
            if (roleTotals is null)
            {
                roleTotals = new JsonObject();
                roles[role] = roleTotals;
            }

            var roleAgents = roleTotals["agents"] as JsonObject;
            if (roleAgents is null)
            {
                roleAgents = new JsonObject();
                roleTotals["agents"] = roleAgents;
            }

            roleAgents[agent] = AsIntOrDefault(roleAgents[agent]) + 1;
        }
    }

    return ledger;
}

static JsonObject SelectPair(
    JsonObject registry,
    JsonObject ledger,
    string role,
    string baseLevel,
    HashSet<string> detectedAgents,
    HashSet<string> allowlist,
    HashSet<string> denylist,
    string? forcedAgent,
    string? forcedModel,
    string? excludeModel)
{
    var routingLevel = RoutingLevel(role, baseLevel);
    var reason = !string.IsNullOrWhiteSpace(forcedAgent) && !string.IsNullOrWhiteSpace(forcedModel)
        ? "forced-agent-and-model"
        : !string.IsNullOrWhiteSpace(forcedAgent)
            ? "forced-agent"
            : !string.IsNullOrWhiteSpace(forcedModel)
                ? "forced-model"
                : "usage-share-debt";

    var candidatePairs = CandidatePairs(registry, role, routingLevel, detectedAgents, allowlist, denylist, forcedAgent, forcedModel, excludeModel);
    if (candidatePairs.Count == 0)
    {
        throw new CommandError(
            "no compatible routing candidates " +
            $"role={role} level={routingLevel} detected={FormatList(detectedAgents.OrderBy(x => x))} " +
            $"allowlist={FormatList(allowlist.OrderBy(x => x))} denylist={FormatList(denylist.OrderBy(x => x))}"
        );
    }

    var distribution = GetObject(registry, "model_routing", "usage_distribution");
    var defaultPolicy = ToIntDictionary(GetObject(distribution, "default_target_percent"));
    var rolePolicy = TargetPolicy(registry, role, routingLevel);
    var preferences = RoleAgentPreference(registry, role);
    var preferenceRank = new Dictionary<string, int>(StringComparer.Ordinal);
    for (int i = 0; i < preferences.Count; i++)
    {
        preferenceRank[preferences[i]] = i;
    }

    var counts = CurrentAgentCounts(ledger);
    var roleCounts = RoleAgentCounts(ledger, role);
    var total = counts.Values.Sum();
    var roleTotal = roleCounts.Values.Sum();
    var (minWeight, maxWeight) = role == "complexity" ? (1, 6) : WeightRangeForLevel(registry, routingLevel);
    var weightCenter = (minWeight + maxWeight) / 2.0;

    var catalog = GetObject(registry, "model_catalog");

    var prepared = candidatePairs.Select(item =>
    {
        var policyTarget = rolePolicy.GetValueOrDefault(item.Agent, 0);
        var globalTarget = defaultPolicy.GetValueOrDefault(item.Agent, policyTarget);
        var current = total > 0 ? counts.GetValueOrDefault(item.Agent, 0) * 100.0 / total : 0.0;
        var roleCurrent = roleTotal > 0 ? roleCounts.GetValueOrDefault(item.Agent, 0) * 100.0 / roleTotal : 0.0;
        var projected = (counts.GetValueOrDefault(item.Agent, 0) + 1) * 100.0 / (total + 1);
        var roleProjected = (roleCounts.GetValueOrDefault(item.Agent, 0) + 1) * 100.0 / (roleTotal + 1);
        var combinedDebt = (globalTarget - current) * GLOBAL_DEBT_WEIGHT + (policyTarget - roleCurrent);
        var projectedError = Math.Abs(projected - globalTarget) + Math.Abs(roleProjected - policyTarget);
        var targetPenalty = (policyTarget <= 0 && string.IsNullOrWhiteSpace(forcedAgent)) ? 1000 : 0;

        return new
        {
            Pair = item,
            PolicyTarget = policyTarget,
            GlobalTarget = globalTarget,
            GlobalDebt = globalTarget - current,
            RoleDebt = policyTarget - roleCurrent,
            TargetPenalty = targetPenalty,
            CombinedDebt = -combinedDebt,
            ProjectedError = projectedError,
            Preference = preferenceRank.GetValueOrDefault(item.Agent, 999),
            WeightDelta = Math.Abs(item.ComplexityWeight - weightCenter),
            ContextWindow = item.ContextWindow,
            CurrentPercent = current,
        };
    })
    .OrderBy(x => x.TargetPenalty)
    .ThenBy(x => x.CombinedDebt)
    .ThenBy(x => x.ProjectedError)
    .ThenBy(x => -x.GlobalDebt)
    .ThenBy(x => -x.RoleDebt)
    .ThenBy(x => -x.GlobalTarget)
    .ThenBy(x => -x.PolicyTarget)
    .ThenBy(x => x.Preference)
    .ThenBy(x => x.WeightDelta)
    .ThenBy(x => -x.ContextWindow)
    .ThenBy(x => x.Pair.Model)
    .ThenBy(x => x.Pair.Agent)
    .ToList();

    var top = prepared[0];
    var model = GetObject(catalog, top.Pair.Model);

    var selected = new JsonObject
    {
        ["agent"] = top.Pair.Agent,
        ["model"] = top.Pair.Model,
        ["provider"] = AsString(model["provider"]) ?? "unknown",
        ["ability"] = AsString(model["ability"]) ?? "unknown",
        ["complexity_weight"] = top.Pair.ComplexityWeight,
        ["context_window"] = top.Pair.ContextWindow,
        ["role"] = role,
        ["complexity_level"] = baseLevel,
        ["routing_level"] = routingLevel,
        ["selection_reason"] = reason,
        ["target_percent"] = top.PolicyTarget,
        ["global_target_percent"] = top.GlobalTarget,
        ["current_percent"] = Math.Round(top.CurrentPercent, 2),
        ["evaluated_candidates"] = new JsonArray(),
    };

    var evaluated = new JsonArray();
    foreach (var item in prepared.Take(8))
    {
        evaluated.Add(new JsonObject
        {
            ["agent"] = item.Pair.Agent,
            ["model"] = item.Pair.Model,
            ["target_percent"] = item.PolicyTarget,
            ["complexity_weight"] = item.Pair.ComplexityWeight,
        });
    }

    selected["evaluated_candidates"] = evaluated;
    return selected;
}

static JsonObject BuildOutput(JsonObject registry, JsonObject ledger, string ledgerFile, JsonObject selection, bool updated)
{
    var distribution = GetObject(registry, "model_routing", "usage_distribution");
    var counts = CurrentAgentCounts(ledger);
    var agent = AsString(selection["agent"]) ?? string.Empty;

    return new JsonObject
    {
        ["schema_version"] = 1,
        ["agent"] = agent,
        ["model"] = AsString(selection["model"]) ?? string.Empty,
        ["role"] = AsString(selection["role"]),
        ["complexity_level"] = AsString(selection["complexity_level"]),
        ["routing_level"] = AsString(selection["routing_level"]),
        ["selection_reason"] = AsString(selection["selection_reason"]),
        ["target_percent"] = selection["target_percent"]?.DeepClone(),
        ["global_target_percent"] = selection["global_target_percent"]?.DeepClone(),
        ["current_percent"] = selection["current_percent"]?.DeepClone(),
        ["policy"] = new JsonObject
        {
            ["default_target_percent"] = GetObject(distribution, "default_target_percent").DeepClone(),
            ["role_target_percent"] = GetObject(distribution, "role_target_percent", AsString(selection["role"]) ?? string.Empty).DeepClone(),
        },
        ["ledger"] = new JsonObject
        {
            ["path"] = ledgerFile,
            ["updated"] = updated,
            ["total_decisions"] = counts.Values.Sum(),
            ["agent_counts"] = ToJson(counts),
            ["selected_agent_count"] = counts.GetValueOrDefault(agent, 0),
        },
        ["evaluated_candidates"] = selection["evaluated_candidates"]?.DeepClone() ?? new JsonArray(),
    };
}

static void AppendDecision(JsonObject ledger, JsonObject selection)
{
    var decisions = ledger["decisions"] as JsonArray;
    if (decisions is null)
    {
        decisions = new JsonArray();
        ledger["decisions"] = decisions;
    }

    decisions.Add(new JsonObject
    {
        ["selected_at"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture),
        ["role"] = AsString(selection["role"]),
        ["complexity_level"] = AsString(selection["complexity_level"]),
        ["routing_level"] = AsString(selection["routing_level"]),
        ["agent"] = AsString(selection["agent"]),
        ["model"] = AsString(selection["model"]),
        ["selection_reason"] = AsString(selection["selection_reason"]),
    });

    var totals = ledger["totals"] as JsonObject;
    if (totals is null)
    {
        totals = new JsonObject();
        ledger["totals"] = totals;
    }

    var agents = totals["agents"] as JsonObject;
    if (agents is null)
    {
        agents = new JsonObject();
        totals["agents"] = agents;
    }

    var roles = totals["roles"] as JsonObject;
    if (roles is null)
    {
        roles = new JsonObject();
        totals["roles"] = roles;
    }

    var role = AsString(selection["role"]) ?? string.Empty;
    var agent = AsString(selection["agent"]) ?? string.Empty;

    agents[agent] = AsIntOrDefault(agents[agent]) + 1;

    var roleTotals = roles[role] as JsonObject;
    if (roleTotals is null)
    {
        roleTotals = new JsonObject();
        roles[role] = roleTotals;
    }

    var roleAgents = roleTotals["agents"] as JsonObject;
    if (roleAgents is null)
    {
        roleAgents = new JsonObject();
        roleTotals["agents"] = roleAgents;
    }

    roleAgents[agent] = AsIntOrDefault(roleAgents[agent]) + 1;
}

static Dictionary<string, int> CurrentAgentCounts(JsonObject ledger)
{
    return ToIntDictionary(GetObject(ledger, "totals", "agents"));
}

static Dictionary<string, int> RoleAgentCounts(JsonObject ledger, string role)
{
    return ToIntDictionary(GetObject(ledger, "totals", "roles", role, "agents"));
}

static Dictionary<string, int> TargetPolicy(JsonObject registry, string role, string level)
{
    var roleBands = GetObject(registry, "model_routing", "usage_distribution", "role_band_target_percent");
    if (roleBands.TryGetPropertyValue(role, out var roleNode) && roleNode is JsonObject roleMap)
    {
        if (roleMap.TryGetPropertyValue(level, out var levelNode) && levelNode is JsonObject levelMap)
        {
            return ToIntDictionary(levelMap);
        }
    }

    var roleTarget = GetObject(registry, "model_routing", "usage_distribution", "role_target_percent");
    if (roleTarget.TryGetPropertyValue(role, out var explicitRole) && explicitRole is JsonObject explicitMap)
    {
        return ToIntDictionary(explicitMap);
    }

    return ToIntDictionary(GetObject(registry, "model_routing", "usage_distribution", "default_target_percent"));
}

static List<string> RoleAgentPreference(JsonObject registry, string role)
{
    var rolePrefs = GetArray(registry, "model_routing", "usage_distribution", "role_agent_preference", role);
    if (rolePrefs.Count > 0)
    {
        return rolePrefs.Select(item => AsString(item)).Where(item => !string.IsNullOrWhiteSpace(item)).Cast<string>().ToList();
    }

    var defaultPrefs = GetArray(registry, "model_routing", "usage_distribution", "role_agent_preference", "default");
    if (defaultPrefs.Count > 0)
    {
        return defaultPrefs.Select(item => AsString(item)).Where(item => !string.IsNullOrWhiteSpace(item)).Cast<string>().ToList();
    }

    return new List<string>(DefaultAgents);
}

static int BandIndex(string level)
{
    return Array.IndexOf(BandOrder, level);
}

static string ScoreToLevel(JsonObject registry, int score)
{
    foreach (var item in GetArray(registry, "model_routing", "score_to_weight"))
    {
        if (item is not JsonObject row)
        {
            continue;
        }

        var min = AsInt(row["score_min"]);
        var max = AsInt(row["score_max"]);
        if (min.HasValue && max.HasValue && min.Value <= score && score <= max.Value)
        {
            return AsString(row["label"]) ?? "easy";
        }
    }

    if (score > 40)
    {
        return "holy-fuck";
    }

    throw new CommandError($"complexity score does not map to a routing band: {score}");
}

static (int, int) WeightRangeForLevel(JsonObject registry, string level)
{
    foreach (var item in GetArray(registry, "model_routing", "score_to_weight"))
    {
        if (item is not JsonObject row)
        {
            continue;
        }

        if (AsString(row["label"]) == level)
        {
            return (AsInt(row["weight_min"]) ?? 1, AsInt(row["weight_max"]) ?? 6);
        }
    }

    throw new CommandError($"unknown complexity level: {level}");
}

static bool MinBandAllows(JsonObject registry, string modelId, string level)
{
    var model = GetObject(registry, "model_catalog", modelId);
    var minBand = AsString(model["min_band"]);
    if (string.IsNullOrWhiteSpace(minBand) || string.IsNullOrWhiteSpace(level))
    {
        return true;
    }

    var minIndex = BandIndex(minBand);
    var levelIndex = BandIndex(level);
    if (minIndex < 0 || levelIndex < 0)
    {
        return true;
    }

    return levelIndex >= minIndex;
}

class Candidate
{
    public required string Agent { get; set; }
    public required string Model { get; set; }
    public int ComplexityWeight { get; set; }
    public int ContextWindow { get; set; }
}

static List<Candidate> CandidatePairs(
    JsonObject registry,
    string role,
    string level,
    HashSet<string> detected,
    HashSet<string> allowlist,
    HashSet<string> denylist,
    string? forcedAgent,
    string? forcedModel,
    string? excludeModel)
{
    var catalog = GetObject(registry, "model_catalog");
    var modelIds = CandidateModelIds(registry, role, level, forcedModel, excludeModel);
    var pairs = new List<Candidate>();

    foreach (var modelId in modelIds)
    {
        foreach (var agentId in CompatibleAgentsForModel(registry, modelId, detected, allowlist, denylist, forcedAgent))
        {
            var model = GetObject(catalog, modelId);
            pairs.Add(new Candidate
            {
                Agent = agentId,
                Model = modelId,
                ComplexityWeight = AsInt(model["complexity_weight"]) ?? 99,
                ContextWindow = AsInt(model["context_window"]) ?? 0,
            });
        }
    }

    return pairs;
}

static List<string> CompatibleAgentsForModel(
    JsonObject registry,
    string modelId,
    HashSet<string> detected,
    HashSet<string> allowlist,
    HashSet<string> denylist,
    string? forcedAgent)
{
    var model = GetObject(registry, "model_catalog", modelId);
    var provider = AsString(model["provider"]);
    var agents = GetObject(registry, "agents");
    var output = new List<string>();

    foreach (var pair in agents)
    {
        var agentId = pair.Key;
        if (pair.Value is not JsonObject agent)
        {
            continue;
        }

        if (forcedAgent is not null && agentId != forcedAgent)
        {
            continue;
        }

        if (!detected.Contains(agentId))
        {
            continue;
        }

        if (allowlist.Count > 0 && !allowlist.Contains(agentId))
        {
            continue;
        }

        if (denylist.Contains(agentId))
        {
            continue;
        }

        var knownModels = GetArray(agent, "model", "known_models");
        if (!knownModels.Any(item => AsString(item) == modelId))
        {
            continue;
        }

        if (provider == "google" && agentId != "agy")
        {
            continue;
        }

        output.Add(agentId);
    }

    return output;
}

static List<string> CandidateModelIds(
    JsonObject registry,
    string role,
    string level,
    string? forcedModel,
    string? excludeModel)
{
    var catalog = GetObject(registry, "model_catalog");

    if (!string.IsNullOrWhiteSpace(forcedModel))
    {
        if (!catalog.ContainsKey(forcedModel))
        {
            throw new CommandError($"forced model is not in model_catalog: {forcedModel}");
        }

        return new List<string> { forcedModel };
    }

    var (minWeight, maxWeight) = role == "complexity" ? (1, 6) : WeightRangeForLevel(registry, level);
    var candidates = new List<string>();

    foreach (var pair in catalog)
    {
        if (pair.Key == excludeModel)
        {
            continue;
        }

        if (pair.Value is not JsonObject model)
        {
            continue;
        }

        if (role == "complexity" && AsBool(model["exclude_from_complexity"]))
        {
            continue;
        }

        if (!MinBandAllows(registry, pair.Key, level))
        {
            continue;
        }

        var weight = AsInt(model["complexity_weight"]);
        if (weight.HasValue && minWeight <= weight.Value && weight.Value <= maxWeight)
        {
            candidates.Add(pair.Key);
        }
    }

    var required = GetArray(registry, "model_routing", "band_required_models", level);
    foreach (var item in required)
    {
        var modelId = AsString(item);
        if (string.IsNullOrWhiteSpace(modelId) || modelId == excludeModel)
        {
            continue;
        }

        if (!catalog.ContainsKey(modelId))
        {
            continue;
        }

        if (role == "complexity" && AsBool(GetObject(catalog, modelId)["exclude_from_complexity"]))
        {
            continue;
        }

        if (!MinBandAllows(registry, modelId, level))
        {
            continue;
        }

        if (!candidates.Contains(modelId, StringComparer.Ordinal))
        {
            candidates.Add(modelId);
        }
    }

    if (candidates.Count > 0)
    {
        return candidates;
    }

    for (int expansion = 1; expansion <= 3; expansion++)
    {
        var expandedMax = maxWeight + expansion;
        foreach (var pair in catalog)
        {
            if (pair.Key == excludeModel)
            {
                continue;
            }

            if (pair.Value is not JsonObject model)
            {
                continue;
            }

            if (role == "complexity" && AsBool(model["exclude_from_complexity"]))
            {
                continue;
            }

            if (!MinBandAllows(registry, pair.Key, level))
            {
                continue;
            }

            var weight = AsInt(model["complexity_weight"]);
            if (weight.HasValue && minWeight <= weight.Value && weight.Value <= expandedMax)
            {
                candidates.Add(pair.Key);
            }
        }

        if (candidates.Count > 0)
        {
            return candidates;
        }
    }

    return candidates;
}

static JsonObject GetObject(JsonObject root, params string[] path)
{
    JsonNode? current = root;
    foreach (var segment in path)
    {
        if (current is JsonObject obj && obj.TryGetPropertyValue(segment, out var next))
        {
            current = next;
            continue;
        }

        return new JsonObject();
    }

    return current as JsonObject ?? new JsonObject();
}

static JsonArray GetArray(JsonObject root, params string[] path)
{
    JsonNode? current = root;
    foreach (var segment in path)
    {
        if (current is JsonObject obj && obj.TryGetPropertyValue(segment, out var next))
        {
            current = next;
            continue;
        }

        return new JsonArray();
    }

    return current as JsonArray ?? new JsonArray();
}

static JsonObject ToJson(Dictionary<string, int> source)
{
    var result = new JsonObject();
    foreach (var pair in source)
    {
        result[pair.Key] = pair.Value;
    }

    return result;
}

static Dictionary<string, int> ToIntDictionary(JsonObject source)
{
    var values = new Dictionary<string, int>(StringComparer.Ordinal);
    foreach (var pair in source)
    {
        if (pair.Value is JsonValue value && value.GetValueKind() == JsonValueKind.Number && value.TryGetValue<int>(out var parsed))
        {
            values[pair.Key] = parsed;
        }
    }

    return values;
}

static string? AsString(JsonNode? node)
{
    return node is JsonValue value && value.GetValueKind() == JsonValueKind.String
        ? value.GetValue<string>()
        : null;
}

static int? AsInt(JsonNode? node)
{
    if (node is JsonValue value)
    {
        if (value.GetValueKind() == JsonValueKind.Number && value.TryGetValue<int>(out var parsed))
        {
            return parsed;
        }
    }

    return null;
}

static int AsIntOrDefault(JsonNode? node)
{
    return AsInt(node) ?? 0;
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

static class Extensions
{
    public static int GetValueOrDefault(this Dictionary<string, int> source, string key)
    {
        return source.TryGetValue(key, out var value) ? value : 0;
    }
}
