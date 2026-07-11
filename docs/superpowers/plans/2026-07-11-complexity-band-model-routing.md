# Complexity-Band Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce exact complexity-band model sets for every non-complexity worker and propagate band-specific Codex and Claude effort settings into real CLI invocations.

**Architecture:** `assets/agent-registry.json` owns the automatic band policy, provider targets, and effort matrix. `assets/run-with-it-router.py` converts scores to effective role bands, filters automatic candidates, balances compatible pairs by usage debt, and returns effort. The Sub-Coordinator and dispatchers carry that effort to `run-agent`, which renders provider-specific settings.

**Tech Stack:** JSON registry policy, Python 3 router, Bash, PowerShell, shell contract tests.

## Global Constraints

- `complexity` workers retain the existing independent weight-based routing path.
- Every other role uses the exact model set for its effective routing band.
- Review retains its one-band increase; planning retains its two-band increase.
- Explicit `FORCED_AGENT` and `FORCED_MODEL` overrides remain higher precedence than automatic band policy.
- Non-complexity provider preference is Codex, then Claude, then Agy; usage debt still governs distribution.
- GPT-5.6 Sol uses `high` at medium-hard and `xhigh` at complex/`holy-fuck`.
- Claude Sonnet 5 uses `low`, `medium`, `medium`, and `high` from quite-easy through medium-hard.
- Claude Opus 4.8 uses `xhigh` at complex and `max` at `holy-fuck`.
- Bash and PowerShell behavior must remain equivalent.
- The authoritative helper is `assets/run-with-it-router.py`; the stale CodeGraph `assets/python/` path is absent from the worktree and is not installed or tested.

## File Responsibility Map

| File | Responsibility |
|---|---|
| `assets/agent-registry.json` | Model inventory, band policy, targets, effort matrix, invocation templates |
| `assets/run-with-it-router.py` | Score conversion, role bumping, candidate filtering, usage-debt selection, route JSON |
| `assets/run-agent.sh`, `assets/run-agent.ps1` | Provider command rendering |
| `assets/run-with-it-dispatch.sh`, `assets/run-with-it-dispatch.ps1` | Worker lifecycle and effort forwarding |
| `assets/sub-coordinator-prompt.md` | Route extraction and dispatcher calls |
| `README.md`, `skills/run-with-it/SKILL.md`, `.agents/skills/run-with-it/SKILL.md` | User and agent contracts |
| `tests/run-with-it-router.test.sh`, `tests/run-agent*.test.sh` | Registry, router, and runner behavior |
| `tests/run-with-it-dispatch*.test.sh`, `tests/run-with-it-routing*.test.sh` | Platform propagation and orchestration contracts |

---

### Task 1: Lock and implement the exact automatic model matrix

**Files:**
- Modify: `tests/run-with-it-router.test.sh`
- Modify: `tests/run-agent.test.sh`
- Modify: `assets/agent-registry.json`
- Modify: `assets/run-with-it-router.py`

**Interfaces:**
- Consumes: `candidate_model_ids(registry, role, level, forced_model, exclude_models) -> list[str]`
- Produces: `model_routing.non_complexity_band_policy`, `usage_distribution.non_complexity_band_target_percent`, and exact non-complexity candidate lists.

- [ ] **Step 1: Write failing exact-policy tests**

Add this expected policy to both registry-contract Python blocks:

```python
expected_policy = {
    "quite-easy": {
        "models": ["gpt-5.4", "gpt-5.3-codex-spark", "gpt-5.6-luna", "claude-sonnet-5", "claude-haiku-4-5"],
        "providers": ["google"],
    },
    "easy": {
        "models": ["gpt-5.4", "gpt-5.3-codex-spark", "gpt-5.6-luna", "claude-sonnet-5", "claude-haiku-4-5"],
        "providers": ["google"],
    },
    "medium": {"models": ["gpt-5.6-terra", "gpt-5.3-codex-spark", "claude-sonnet-5"], "providers": []},
    "medium-hard": {"models": ["gpt-5.5", "gpt-5.6-sol", "gpt-5.3-codex-spark", "claude-sonnet-5"], "providers": []},
    "complex": {"models": ["gpt-5.6-sol", "claude-opus-4-8"], "providers": []},
    "holy-fuck": {"models": ["gpt-5.6-sol", "claude-opus-4-8"], "providers": []},
}
assert registry["model_routing"]["non_complexity_band_policy"] == expected_policy

expected_targets = {
    "quite-easy": {"codex": 55, "claude": 35, "agy": 10},
    "easy": {"codex": 55, "claude": 40, "agy": 5},
    "medium": {"codex": 70, "claude": 30, "agy": 0},
    "medium-hard": {"codex": 70, "claude": 30, "agy": 0},
    "complex": {"codex": 70, "claude": 30, "agy": 0},
    "holy-fuck": {"codex": 60, "claude": 40, "agy": 0},
}
assert distribution["non_complexity_band_target_percent"] == expected_targets
```

Assert candidate sets and complexity boundaries:

```python
for level, policy in expected_policy.items():
    expected = set(policy["models"])
    if "google" in policy["providers"]:
        expected.update(
            model_id for model_id, entry in registry["model_catalog"].items()
            if entry.get("provider") == "google"
        )
    assert set(automatic(level, "impl")) == expected
    assert set(automatic(level, "modify")) == expected

assert "gpt-5.4" not in automatic("complex", "impl")
assert "gpt-5.4" not in automatic("holy-fuck", "impl")
assert router.candidate_model_ids(registry, "impl", "complex", "gpt-5.4", None) == ["gpt-5.4"]

expected_score_levels = {
    8: "quite-easy", 12: "quite-easy", 13: "easy", 17: "easy",
    18: "medium", 22: "medium", 23: "medium-hard", 27: "medium-hard",
    28: "complex", 32: "complex", 33: "holy-fuck", 40: "holy-fuck",
}
for score, level in expected_score_levels.items():
    assert router.score_to_level(registry, score) == level
assert router.routing_level("review", "medium") == "medium-hard"
assert router.routing_level("plan", "medium") == "complex"
```

Require `claude-sonnet-5` in Claude's known models and require GPT-5.5 to no longer be explicit-only.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
bash tests/run-with-it-router.test.sh
bash tests/run-agent.test.sh
```

Expected: both fail because the new policy, targets, and Sonnet 5 do not exist.

- [ ] **Step 3: Add registry policy and inventory**

Add `non_complexity_band_policy` and `non_complexity_band_target_percent` using the exact structures from Step 1. Remove old non-complexity entries from `role_band_target_percent` so the registry has one target authority; retain its `complexity` entry. Set every non-complexity role preference to `["codex", "claude", "agy"]`, leaving `complexity` unchanged. Add Sonnet 5 after Opus 4.8 in Claude's `known_models` and add:

```json
"claude-sonnet-5": {
  "provider": "anthropic",
  "ability": "advanced",
  "complexity_weight": 7,
  "context_window": 1000000,
  "strengths": ["coding", "agentic", "adaptive-thinking", "speed-intelligence-balance"]
}
```

Remove `"explicit_only": true` from GPT-5.5. Do not add broad `routing_bands` to GPT-5.5 or GPT-5.4.

- [ ] **Step 4: Implement exact policy resolution**

Add:

```python
def automatic_policy_model_ids(registry: dict[str, Any], level: str) -> list[str]:
    catalog = registry.get("model_catalog", {})
    policy = registry.get("model_routing", {}).get("non_complexity_band_policy", {}).get(level)
    if not isinstance(policy, dict):
        fail(f"missing non-complexity automatic model policy for band: {level}")
    selected: list[str] = []
    for model_id in [str(value) for value in policy.get("models", [])]:
        if model_id not in catalog:
            fail(f"automatic model policy references unknown model: {model_id}")
        if model_id not in selected:
            selected.append(model_id)
    providers = {str(value) for value in policy.get("providers", [])}
    for model_id, entry in catalog.items():
        if str(entry.get("provider", "")) in providers and model_id not in selected:
            selected.append(model_id)
    return selected
```

In `candidate_model_ids`, preserve the forced-model return first, then add:

```python
if role != "complexity":
    return [
        model_id for model_id in automatic_policy_model_ids(registry, level)
        if model_id not in exclude_models
    ]
```

Leave the existing weight/routing-band/expansion path for `complexity`. Change `target_policy` so non-complexity roles read `non_complexity_band_target_percent[level]`; complexity retains its current lookup.

- [ ] **Step 5: Run tests and verify GREEN**

Run the two Step 2 commands. Expected: both pass, including exact membership, forced-model compatibility, boundary scores, role bumping, and unchanged complexity routing.

- [ ] **Step 6: Commit**

```bash
git add assets/agent-registry.json assets/run-with-it-router.py tests/run-with-it-router.test.sh tests/run-agent.test.sh
git commit -m "feat(routing): enforce model bands"
```

---

### Task 2: Resolve and record band-specific effort

**Files:**
- Modify: `tests/run-with-it-router.test.sh`
- Modify: `assets/agent-registry.json`
- Modify: `assets/run-with-it-router.py`

**Interfaces:**
- Consumes: selected `model` and effective `routing_level`.
- Produces: `model_effort_for_level(registry, model_id, level) -> str`, route JSON `effort`, and ledger decision `effort`.

- [ ] **Step 1: Write failing effort tests**

Assert this exact registry value:

```python
expected_effort = {
    "gpt-5.6-sol": {"medium-hard": "high", "complex": "xhigh", "holy-fuck": "xhigh"},
    "claude-sonnet-5": {
        "quite-easy": "low", "easy": "medium", "medium": "medium", "medium-hard": "high",
    },
    "claude-opus-4-8": {"complex": "xhigh", "holy-fuck": "max"},
}
assert registry["model_routing"]["effort_by_model_and_band"] == expected_effort
```

Add deterministic CLI cases using forced models:

```bash
sol_medium_hard="$(${ROUTER_PATH} --registry-file "${REGISTRY_PATH}" --ledger-file "${WORK_DIR}/effort.json" --role impl --complexity-level medium-hard --detected-agents codex --forced-model gpt-5.6-sol)"
assert_json_field "${sol_medium_hard}" 'payload["effort"] == "high"' "Sol medium-hard uses high effort"

sol_complex="$(${ROUTER_PATH} --registry-file "${REGISTRY_PATH}" --ledger-file "${WORK_DIR}/effort.json" --role impl --complexity-level complex --detected-agents codex --forced-model gpt-5.6-sol)"
assert_json_field "${sol_complex}" 'payload["effort"] == "xhigh"' "Sol complex uses xhigh effort"

sonnet_easy="$(${ROUTER_PATH} --registry-file "${REGISTRY_PATH}" --ledger-file "${WORK_DIR}/effort.json" --role impl --complexity-level easy --detected-agents claude --forced-model claude-sonnet-5)"
assert_json_field "${sonnet_easy}" 'payload["effort"] == "medium"' "Sonnet easy uses medium effort"

opus_holy="$(${ROUTER_PATH} --registry-file "${REGISTRY_PATH}" --ledger-file "${WORK_DIR}/effort.json" --role impl --complexity-level holy-fuck --detected-agents claude --forced-model claude-opus-4-8)"
assert_json_field "${opus_holy}" 'payload["effort"] == "max"' "Opus holy-fuck uses max effort"
```

- [ ] **Step 2: Run test and verify RED**

Run `bash tests/run-with-it-router.test.sh`.

Expected: failure because the effort matrix and route field do not exist.

- [ ] **Step 3: Implement effort resolution**

Add `model_routing.effort_by_model_and_band` using Step 1. Add:

```python
def model_effort_for_level(registry: dict[str, Any], model_id: str, level: str) -> str:
    effort_map = registry.get("model_routing", {}).get("effort_by_model_and_band", {})
    band_effort = effort_map.get(model_id, {}).get(level)
    if band_effort:
        return str(band_effort)
    return str(registry.get("model_catalog", {}).get(model_id, {}).get("reasoning_effort", ""))
```

After `select_pair` finalizes the selected pair, set:

```python
selected["effort"] = model_effort_for_level(registry, selected["model"], level)
```

Add `"effort": selection.get("effort", "")` to `append_decision` and `build_output`. The catalog fallback keeps router telemetry aligned with direct runner behavior for forced GPT-5.6 routes that have no band override. Use an empty string only when neither policy nor catalog defines effort.

- [ ] **Step 4: Run test and verify GREEN**

Run `bash tests/run-with-it-router.test.sh`.

Expected: pass with all route and ledger effort fields correct.

- [ ] **Step 5: Commit**

```bash
git add assets/agent-registry.json assets/run-with-it-router.py tests/run-with-it-router.test.sh
git commit -m "feat(routing): resolve model effort"
```

---

### Task 3: Render generic effort for Codex and Claude

**Files:**
- Modify: `tests/run-agent.test.sh`
- Modify: `tests/run-agent-ps1-status-bus.test.sh`
- Modify: `assets/run-agent.sh`
- Modify: `assets/run-agent.ps1`
- Modify: `assets/agent-registry.json`

**Interfaces:**
- Consumes: optional `MODEL_EFFORT` environment value or `--effort <level>` argument.
- Produces: Codex `-c model_reasoning_effort=<level>` or Claude Code `--effort <level>`.

- [ ] **Step 1: Write failing Bash and PowerShell dry-run tests**

Add Bash cases:

```bash
codex_xhigh_output="$("${RUNNER_PATH}" --agent codex --model gpt-5.6-sol --effort xhigh --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${codex_xhigh_output}" "-c model_reasoning_effort=xhigh" "Codex renders routed xhigh effort"

claude_medium_output="$("${RUNNER_PATH}" --agent claude --model claude-sonnet-5 --effort medium --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_contains "${claude_medium_output}" "--model claude-sonnet-5" "Claude renders Sonnet 5"
assert_contains "${claude_medium_output}" "--effort medium" "Claude renders routed medium effort"

claude_default_output="$("${RUNNER_PATH}" --agent claude --model claude-haiku-4-5 --context-file "${CONTEXT_FILE}" --prompt-file "${PROMPT_FILE}" --dry-run --unattended)"
assert_not_contains "${claude_default_output}" "--effort" "Haiku uses provider-default effort"
```

Mirror them in `tests/run-agent-ps1-status-bus.test.sh`, matching quoted output:

```bash
assert_contains "$output" "'--effort' 'xhigh'" "PowerShell Claude runner renders xhigh effort"
assert_contains "$output" "'model_reasoning_effort=xhigh'" "PowerShell Codex runner renders xhigh effort"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
bash tests/run-agent.test.sh
bash tests/run-agent-ps1-status-bus.test.sh
```

Expected: failures because `--effort` is unknown and Claude has no model-settings placeholder.

- [ ] **Step 3: Implement generic input and provider rendering**

In both runners, initialize from `MODEL_EFFORT`, parse `--effort` with CLI precedence, and fall back to the catalog's existing `reasoning_effort` only when no routed value exists.

Replace the Bash `{{model_settings}}` branch with:

```bash
"{{model_settings}}")
  if [[ -n "${model_effort}" ]]; then
    case "${AGENT}" in
      codex) cmd+=("-c" "model_reasoning_effort=${model_effort}") ;;
      claude) cmd+=("--effort" "${model_effort}") ;;
    esac
  fi
  ;;
```

Implement the equivalent PowerShell branch with `$AGENT`, `$modelEffort`, and `$cmdArgs.Add(...)`. Add `"{{model_settings}}"` immediately after `"{{model_flag}}"` in Claude's registry invocation template. Leave Agy unchanged.

- [ ] **Step 4: Run tests and verify GREEN**

Run the two Step 2 commands.

Expected: pass for Codex high/xhigh, Claude routed effort, Haiku without effort, and existing direct-call fallback behavior.

- [ ] **Step 5: Commit**

```bash
git add assets/agent-registry.json assets/run-agent.sh assets/run-agent.ps1 tests/run-agent.test.sh tests/run-agent-ps1-status-bus.test.sh
git commit -m "feat(runners): apply routed effort"
```

---

### Task 4: Propagate effort through Sub-Coordinator and dispatchers

**Files:**
- Modify: `tests/run-with-it-dispatch.test.sh`
- Modify: `tests/run-with-it-dispatch-ps1.test.sh`
- Modify: `tests/run-with-it-routing.test.sh`
- Modify: `tests/run-with-it-routing-windows.test.sh`
- Modify: `assets/run-with-it-dispatch.sh`
- Modify: `assets/run-with-it-dispatch.ps1`
- Modify: `assets/sub-coordinator-prompt.md`

**Interfaces:**
- Consumes: router JSON `effort` and dispatcher option `--effort` / `-Effort`.
- Produces: dispatcher state/status effort, detached argument preservation, and runner effort forwarding.

- [ ] **Step 1: Write failing propagation tests**

Extend Bash dispatcher dry-run and validation cases with `--effort xhigh`:

```bash
assert_contains "${dry_output}" "--effort xhigh" "dry-run forwards model effort"
assert_file_contains "${STATE_FILE}" '"effort": "xhigh"' "dispatcher state records effort"
```

Mirror them in the PowerShell suite with `-Effort xhigh`. Add orchestration assertions:

```bash
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" 'EFFORT="$(printf' "Bash routing extracts effort"
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" '$EFFORT = if ($route.effort)' "PowerShell routing extracts effort"
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" '--effort "$EFFORT"' "Bash dispatch passes effort"
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" '-Effort $EFFORT' "PowerShell dispatch passes effort"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-dispatch-ps1.test.sh
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-routing-windows.test.sh
```

Expected: failures because neither dispatcher accepts effort and the Sub-Coordinator does not extract it.

- [ ] **Step 3: Add Bash dispatcher effort support**

- Initialize `MODEL_EFFORT=""` and parse `--effort`.
- Add `"effort": $(json_string "$MODEL_EFFORT")` to worker state.
- Include `effort=${MODEL_EFFORT}` in route-visible ready/start status lines.
- Preserve the option in detached `ORIGINAL_ARGS`.
- Build one runner argument array; append `--effort "$MODEL_EFFORT"` only when non-empty.
- Use that array for dry-run output and real `run-agent.sh` execution.

The core optional-argument logic is:

```bash
runner_args=(
  --agent "$AGENT_NAME"
  --model "$MODEL_NAME"
  --context-file "$CONTEXT_FILE"
  --prompt-file "$PROMPT_FILE"
  --unattended
)
if [ -n "$MODEL_EFFORT" ]; then
  runner_args+=(--effort "$MODEL_EFFORT")
fi
```

- [ ] **Step 4: Add PowerShell dispatcher effort support**

Add `[string]$Effort = ""` to parameters, `effort = $Effort` to state JSON, and effort to status. Add `-Effort`, `$Effort` to detached arguments only when non-empty. Append `--effort`, `$Effort` to `$runnerArgs` only when non-empty and use `$runnerArgs` for dry-run and process launch:

```powershell
if ($Effort) {
    $runnerArgs += @("--effort", $Effort)
}
```

- [ ] **Step 5: Extract and forward effort in the Sub-Coordinator contract**

After route agent/model parsing, add:

```bash
EFFORT="$(printf '%s' "$ROUTE_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("effort", ""))')"
```

And:

```powershell
$EFFORT = if ($route.effort) { [string]$route.effort } else { "" }
```

Include effort in `STATUS|type=route-selected` and `model_usage`. Add `--effort "$EFFORT"` / `-Effort $EFFORT` to every dispatcher invocation for complexity, artifact recovery, plan, implementation, review/modification reuse, and retry paths. Empty effort remains valid.

- [ ] **Step 6: Run tests and verify GREEN**

Run the four Step 2 commands.

Expected: pass with effort visible in dry runs, state, status, detached re-entry, and both platform command examples.

- [ ] **Step 7: Commit**

```bash
git add assets/run-with-it-dispatch.sh assets/run-with-it-dispatch.ps1 assets/sub-coordinator-prompt.md tests/run-with-it-dispatch.test.sh tests/run-with-it-dispatch-ps1.test.sh tests/run-with-it-routing.test.sh tests/run-with-it-routing-windows.test.sh
git commit -m "feat(dispatch): forward model effort"
```

---

### Task 5: Document, verify, and synchronize policy

**Files:**
- Modify: `README.md`
- Modify: `skills/run-with-it/SKILL.md`
- Modify: `.agents/skills/run-with-it/SKILL.md`
- Modify: `tests/run-with-it-routing.test.sh`
- Verify: all Task 1-4 files
- Synchronize after verification: `~/.ai-skill-collections/assets/`

**Interfaces:**
- Consumes: completed registry, router, runner, and dispatcher contracts.
- Produces: matching documentation, full regression evidence, and verified local installed assets.

- [ ] **Step 1: Write failing documentation assertions**

Require README and skill text to name these sets:

```text
quite-easy / easy: GPT-5.4, Codex Spark, GPT-5.6 Luna, Claude Sonnet 5, Claude Haiku 4.5, and eligible Gemini models
medium: GPT-5.6 Terra, Codex Spark, and Claude Sonnet 5
medium-hard: GPT-5.5, GPT-5.6 Sol, Codex Spark, and Claude Sonnet 5
complex / holy-fuck: GPT-5.6 Sol and Claude Opus 4.8
```

Require the Sol and Claude effort mappings, complexity exemption, review +1, plan +2, and `cmp -s skills/run-with-it/SKILL.md .agents/skills/run-with-it/SKILL.md`.

- [ ] **Step 2: Run documentation test and verify RED**

Run `bash tests/run-with-it-routing.test.sh`.

Expected: failure because the new matrix and effort mapping are undocumented.

- [ ] **Step 3: Update documentation and mirrors**

Add a compact automatic-routing matrix to README and both skill copies. Explain effective role bands, provider preference, provider-native effort translation, and that forced models bypass automatic membership but not compatibility or availability checks. Keep both skill files byte-identical.

- [ ] **Step 4: Run focused verification**

Run:

```bash
bash tests/run-with-it-router.test.sh
bash tests/run-agent.test.sh
bash tests/run-agent-ps1-status-bus.test.sh
bash tests/run-with-it-dispatch.test.sh
bash tests/run-with-it-dispatch-ps1.test.sh
bash tests/run-with-it-routing.test.sh
bash tests/run-with-it-routing-windows.test.sh
bash tests/run-with-it-plan.test.sh
```

Expected: every command exits 0 and prints its PASS summary.

- [ ] **Step 5: Run full regression suite**

```bash
for test_file in tests/*.test.sh; do
  bash "$test_file"
done
```

Expected: every test script exits 0.

- [ ] **Step 6: Simulate numeric complexity routing**

```bash
work_dir="$(mktemp -d)"
for score in 8 13 18 23 28 33; do
  python3 assets/run-with-it-router.py \
    --registry-file assets/agent-registry.json \
    --ledger-file "$work_dir/ledger-$score.json" \
    --role impl \
    --complexity-score "$score" \
    --detected-agents codex,claude,agy \
    | jq -r '[.complexity_level,.routing_level,.agent,.model,.effort]|@tsv'
done
rm -rf "$work_dir"
```

Expected bands: `quite-easy`, `easy`, `medium`, `medium-hard`, `complex`, `holy-fuck`; every model and effort belongs to its registry policy.

- [ ] **Step 7: Check scope and formatting**

```bash
git diff --check
git status --short
git diff --stat HEAD~4..HEAD
```

Expected: no whitespace errors and only planned routing, runner, dispatcher, documentation, and test files changed.

- [ ] **Step 8: Commit documentation**

```bash
git add README.md skills/run-with-it/SKILL.md .agents/skills/run-with-it/SKILL.md tests/run-with-it-routing.test.sh
git commit -m "docs: explain model band routing"
```

- [ ] **Step 9: Synchronize verified local assets**

Only after all tests pass:

```bash
mkdir -p "$HOME/.ai-skill-collections/assets"
cp -f \
  assets/agent-registry.json \
  assets/run-with-it-router.py \
  assets/run-agent.sh \
  assets/run-agent.ps1 \
  assets/run-with-it-dispatch.sh \
  assets/run-with-it-dispatch.ps1 \
  assets/sub-coordinator-prompt.md \
  "$HOME/.ai-skill-collections/assets/"
chmod +x \
  "$HOME/.ai-skill-collections/assets/run-with-it-router.py" \
  "$HOME/.ai-skill-collections/assets/run-agent.sh" \
  "$HOME/.ai-skill-collections/assets/run-with-it-dispatch.sh"
```

Verify byte equality:

```bash
for file in \
  agent-registry.json run-with-it-router.py run-agent.sh run-agent.ps1 \
  run-with-it-dispatch.sh run-with-it-dispatch.ps1 sub-coordinator-prompt.md
do
  cmp -s "assets/$file" "$HOME/.ai-skill-collections/assets/$file"
done
```

Expected: exit 0.

---

## Completion Evidence

- Numeric scores map to existing bands and every non-complexity automatic route stays inside the approved matrix.
- Complexity routing remains unchanged.
- GPT-5.4 cannot route automatically at complex or `holy-fuck`.
- GPT-5.5 can route automatically only at medium-hard.
- Sonnet 5 is recognized by Claude Code.
- Sol, Sonnet 5, and Opus 4.8 receive approved band effort.
- Bash and PowerShell show equivalent routing and effort propagation.
- Forced-model, availability, exclusion, sticky-review, and ledger tests remain green.
- The full test suite passes.
- Installed local assets match verified repository assets.
