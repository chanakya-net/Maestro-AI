# Codex GPT-5.6 Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add canonical Codex GPT-5.6 Luna, Terra, and Sol routing with an atomic high-reasoning invariant while making GPT-5.5 explicit-only.

**Architecture:** The shared registry remains the source of truth for model identity, complexity placement, context size, and reasoning effort. The routers filter `explicit_only` models only during automatic candidate construction, while the Bash and PowerShell runners translate per-model reasoning metadata into final Codex CLI arguments after caller-supplied extras.

**Tech Stack:** JSON registry, Python 3 router, Bash runner/tests, PowerShell runner/tests, Markdown documentation.

## Global Constraints

- Use canonical non-interactive model IDs: `gpt-5.6-luna`, `gpt-5.6-terra`, and `gpt-5.6-sol`.
- Every invocation of those three models must end with `-c model_reasoning_effort=high` after caller-provided extra arguments.
- Route Luna only at `easy`, Terra only at `medium`, and Sol at `medium-hard`, `complex`, and `holy-fuck`.
- Keep GPT-5.5 registered and directly runnable, but exclude it from all automatic routing.
- Preserve explicit GPT-5.5 paths: router `--forced-model`, runner `--model`, and `SUB_COORD_MODEL`.
- Modify the maintained router at `assets/run-with-it-router.py`; do not recreate the removed `assets/python` duplicate.
- Keep Bash and PowerShell runner behavior equivalent.
- Do not change provider usage percentages, subscription accounting, or reasoning behavior for older Codex models.

## File Structure

- `assets/agent-registry.json`: model metadata, Codex known-model list, Codex argument template, and high-band pins.
- `assets/run-with-it-router.py`: installed router's explicit-only automatic filtering.
- `assets/run-agent.sh`: Bash registry lookup and atomic model-settings argument rendering.
- `assets/run-agent.ps1`: PowerShell registry lookup and atomic model-settings argument rendering.
- `assets/run-with-it-pool.sh`: Bash Sub-Coordinator default model.
- `assets/run-with-it-pool.ps1`: PowerShell Sub-Coordinator default model.
- `tests/run-with-it-router.test.sh`: registry metadata, band eligibility, and GPT-5.5 explicit-only behavior.
- `tests/run-agent.test.sh`: Bash command construction and precedence.
- `tests/run-agent-ps1-status-bus.test.sh`: PowerShell command construction and precedence.
- `tests/run-with-it-pool.test.sh`: Bash default-model behavior and documentation contract.
- `tests/run-with-it-pool-ps1.test.sh`: PowerShell default-model contract.
- `skills/run-with-it/SKILL.md`: documented Sub-Coordinator default.
- `README.md`: public Sub-Coordinator default.

---

### Task 1: Register GPT-5.6 bands and make GPT-5.5 explicit-only

**Files:**
- Modify: `tests/run-with-it-router.test.sh`
- Modify: `tests/run-agent.test.sh`
- Modify: `assets/agent-registry.json`
- Modify: `assets/run-with-it-router.py`

**Interfaces:**
- Consumes: existing `candidate_model_ids(registry, role, level, forced_model, exclude_model) -> list[str]`.
- Produces: catalog fields `reasoning_effort: str`, `explicit_only: bool`, and the canonical Codex known-model list used by Task 2.

- [ ] **Step 1: Write failing registry and router tests**

Update both embedded registry-contract blocks to expect the canonical list:

```python
expected_codex_models = [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex-spark",
]
assert codex_model.get("known_models") == expected_codex_models

expected_gpt56 = {
    "gpt-5.6-luna": ("balanced", 3, "easy"),
    "gpt-5.6-terra": ("advanced", 5, "medium"),
    "gpt-5.6-sol": ("frontier", 7, "medium-hard"),
}
for model_id, (ability, weight, min_band) in expected_gpt56.items():
    entry = model_catalog[model_id]
    assert entry["ability"] == ability
    assert entry["complexity_weight"] == weight
    assert entry["min_band"] == min_band
    assert entry["context_window"] == 372000
    assert entry["reasoning_effort"] == "high"

assert model_catalog["gpt-5.5"]["explicit_only"] is True
assert "gpt-5.5" not in registry["model_routing"]["band_required_models"]["complex"]
assert "gpt-5.5" not in registry["model_routing"]["band_required_models"]["holy-fuck"]
assert "gpt-5.6-sol" in registry["model_routing"]["band_required_models"]["holy-fuck"]
```

Change the existing embedded Python command to receive the router path:

```bash
python3 - "${REGISTRY_PATH}" "${ROUTER_PATH}" <<'PY'
```

Then import the router beside the existing registry contract and assert automatic versus forced candidate behavior:

```python
import importlib.util

spec = importlib.util.spec_from_file_location("run_with_it_router", sys.argv[2])
router = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(router)

def automatic(level):
    return router.candidate_model_ids(registry, "impl", level, None, None)

assert "gpt-5.6-luna" not in automatic("quite-easy")
assert "gpt-5.6-luna" in automatic("easy")
assert "gpt-5.6-luna" not in automatic("medium")
assert "gpt-5.6-terra" in automatic("medium")
assert "gpt-5.6-terra" not in automatic("medium-hard")
assert "gpt-5.6-sol" in automatic("medium-hard")
assert "gpt-5.6-sol" in automatic("complex")
assert "gpt-5.6-sol" in automatic("holy-fuck")
for level in ("quite-easy", "easy", "medium", "medium-hard", "complex", "holy-fuck"):
    assert "gpt-5.5" not in automatic(level)
assert router.candidate_model_ids(registry, "impl", "complex", "gpt-5.5", None) == ["gpt-5.5"]
```

- [ ] **Step 2: Run focused tests and confirm the new expectations fail**

Run:

```bash
bash tests/run-with-it-router.test.sh
bash tests/run-agent.test.sh
```

Expected: FAIL because the GPT-5.6 entries, `explicit_only`, new band pin, and explicit-only filter do not exist.

- [ ] **Step 3: Add the registry metadata and routing policy**

Add these catalog entries before the existing GPT entries:

```json
"gpt-5.6-luna": {
  "provider": "openai",
  "ability": "balanced",
  "complexity_weight": 3,
  "min_band": "easy",
  "context_window": 372000,
  "reasoning_effort": "high",
  "strengths": ["clear-repeatable-work", "high-throughput", "cost-efficient"]
},
"gpt-5.6-terra": {
  "provider": "openai",
  "ability": "advanced",
  "complexity_weight": 5,
  "min_band": "medium",
  "context_window": 372000,
  "reasoning_effort": "high",
  "strengths": ["everyday-work", "reasoning", "tool-use"]
},
"gpt-5.6-sol": {
  "provider": "openai",
  "ability": "frontier",
  "complexity_weight": 7,
  "min_band": "medium-hard",
  "context_window": 372000,
  "reasoning_effort": "high",
  "strengths": ["complex-open-ended-work", "agentic-coding", "polish"]
}
```

Add `"explicit_only": true` to `gpt-5.5`, replace GPT-5.5 with Sol in `band_required_models["holy-fuck"]`, remove GPT-5.5 from `band_required_models["complex"]`, and update the Codex model list to:

```json
"known_models": [
  "gpt-5.6-sol",
  "gpt-5.6-terra",
  "gpt-5.6-luna",
  "gpt-5.5",
  "gpt-5.4",
  "gpt-5.4-mini",
  "gpt-5.3-codex-spark"
]
```

Update adjacent `_note` fields so they describe GPT-5.6 routing and GPT-5.5 explicit-only status.

- [ ] **Step 4: Implement explicit-only filtering in the maintained router**

In `assets/run-with-it-router.py`, keep the existing forced-model early return, then add this guard to the main catalog loop and expansion loop:

```python
if entry.get("explicit_only") is True:
    continue
```

Harden the required-model loop so an explicit-only model cannot re-enter automatic routing through configuration:

```python
for model_id in routing.get("band_required_models", {}).get(level, []):
    entry = catalog.get(model_id, {})
    if (
        model_id != exclude_model
        and model_id in catalog
        and entry.get("explicit_only") is not True
        and min_band_allows(entry, level)
    ):
        if model_id not in candidates:
            candidates.append(model_id)
```

- [ ] **Step 5: Run focused tests and confirm they pass**

Run:

```bash
bash tests/run-with-it-router.test.sh
bash tests/run-agent.test.sh
```

Expected: both scripts print their PASS lines and exit 0.

- [ ] **Step 6: Commit the routing slice**

```bash
git add assets/agent-registry.json assets/run-with-it-router.py tests/run-with-it-router.test.sh tests/run-agent.test.sh
git commit -m "feat(router): add GPT-5.6 model bands"
```

### Task 2: Render high reasoning atomically in Bash and PowerShell

**Files:**
- Modify: `tests/run-agent.test.sh`
- Modify: `tests/run-agent-ps1-status-bus.test.sh`
- Modify: `assets/agent-registry.json`
- Modify: `assets/run-agent.sh`
- Modify: `assets/run-agent.ps1`

**Interfaces:**
- Consumes: `model_catalog[MODEL].reasoning_effort` from Task 1.
- Produces: `{{model_settings}}`, which renders zero arguments for ordinary models or `-c model_reasoning_effort=<effort>` for configured Codex models.

- [ ] **Step 1: Write failing Bash dry-run and precedence tests**

Add this loop after the existing Codex/Claude dry-run assertions in `tests/run-agent.test.sh`:

```bash
for model in gpt-5.6-luna gpt-5.6-terra gpt-5.6-sol; do
  output="$("${RUNNER_PATH}" \
    --agent codex \
    --model "${model}" \
    --context-file "${CONTEXT_FILE}" \
    --prompt-file "${PROMPT_FILE}" \
    --dry-run \
    --unattended)"
  assert_contains "${output}" "--model ${model}" "Codex dry-run uses canonical ${model} ID"
  assert_contains "${output}" "-c model_reasoning_effort=high" "Codex dry-run applies high reasoning to ${model}"
done

precedence_output="$(AGENT_EXTRA_ARGS='-c model_reasoning_effort=medium' \
  "${RUNNER_PATH}" \
  --agent codex \
  --model gpt-5.6-sol \
  --context-file "${CONTEXT_FILE}" \
  --prompt-file "${PROMPT_FILE}" \
  --dry-run \
  --unattended)"
case "${precedence_output}" in
  *"model_reasoning_effort=medium"*"model_reasoning_effort=high"*) ;;
  *) fail "registry high reasoning must follow caller extra arguments" ;;
esac

legacy_output="$("${RUNNER_PATH}" \
  --agent codex \
  --model gpt-5.4 \
  --context-file "${CONTEXT_FILE}" \
  --prompt-file "${PROMPT_FILE}" \
  --dry-run \
  --unattended)"
assert_not_contains "${legacy_output}" "model_reasoning_effort=high" "legacy Codex models do not inherit high reasoning"
```

- [ ] **Step 2: Write failing PowerShell dry-run and precedence tests**

Add real-registry dry-run coverage near the end of `tests/run-agent-ps1-status-bus.test.sh`:

```bash
for model in gpt-5.6-luna gpt-5.6-terra gpt-5.6-sol; do
  output="$(REPO_ROOT="${ROOT_DIR}" \
    "$PS_CMD" -NoProfile -File "$RUNNER_PATH" \
    --agent codex \
    --model "$model" \
    --context-file "$CONTEXT_FILE" \
    --prompt-file "$PROMPT_FILE" \
    --dry-run \
    --unattended)"
  assert_contains "$output" "'--model' '$model'" "PowerShell runner uses canonical $model ID"
  assert_contains "$output" "'-c' 'model_reasoning_effort=high'" "PowerShell runner applies high reasoning to $model"
done

precedence_output="$(AGENT_EXTRA_ARGS='-c model_reasoning_effort=medium' \
  REPO_ROOT="${ROOT_DIR}" \
  "$PS_CMD" -NoProfile -File "$RUNNER_PATH" \
  --agent codex \
  --model gpt-5.6-sol \
  --context-file "$CONTEXT_FILE" \
  --prompt-file "$PROMPT_FILE" \
  --dry-run \
  --unattended)"
case "$precedence_output" in
  *"model_reasoning_effort=medium"*"model_reasoning_effort=high"*) ;;
  *) fail "PowerShell registry high reasoning must follow caller extra arguments" ;;
esac
```

- [ ] **Step 3: Run both runner tests and verify the new assertions fail**

Run:

```bash
bash tests/run-agent.test.sh
bash tests/run-agent-ps1-status-bus.test.sh
```

Expected: FAIL because `{{model_settings}}` and model reasoning lookups are not implemented.

- [ ] **Step 4: Add the model-settings placeholder to the Codex template**

In `assets/agent-registry.json`, place the new placeholder after extras:

```json
"{{model_flag}}",
"{{extra_args}}",
"{{model_settings}}",
"{{prompt}}"
```

- [ ] **Step 5: Implement Bash reasoning metadata lookup and rendering**

In `json_py`, add:

```python
model_catalog = registry.get("model_catalog", {})

elif action == "model_reasoning_effort":
    print(model_catalog.get(arg, {}).get("reasoning_effort", ""))
```

In the jq action switch, add:

```bash
model_reasoning_effort) json_jq --arg m "${arg}" '.model_catalog[$m].reasoning_effort // ""' ;;
```

Before command construction, resolve the value:

```bash
model_reasoning_effort=""
if [[ -n "${MODEL}" ]]; then
  model_reasoning_effort="$(json_value model_reasoning_effort "${MODEL}")"
fi
```

Add this exact template case after `{{extra_args}}`:

```bash
"{{model_settings}}")
  if [[ -n "${model_reasoning_effort}" ]]; then
    cmd+=("-c" "model_reasoning_effort=${model_reasoning_effort}")
  fi
  ;;
```

- [ ] **Step 6: Implement PowerShell reasoning metadata lookup and rendering**

After resolving `$modelFlag`, resolve the catalog entry safely by model ID:

```powershell
$modelReasoningEffort = ""
if ($MODEL -and $registry.model_catalog -and $registry.model_catalog.PSObject.Properties[$MODEL]) {
    $modelEntry = $registry.model_catalog.PSObject.Properties[$MODEL].Value
    if ($modelEntry.reasoning_effort) {
        $modelReasoningEffort = [string]$modelEntry.reasoning_effort
    }
}
```

Add this template case after `{{extra_args}}`:

```powershell
"{{model_settings}}" {
    if ($modelReasoningEffort) {
        $cmdArgs.Add("-c")
        $cmdArgs.Add("model_reasoning_effort=$modelReasoningEffort")
    }
}
```

- [ ] **Step 7: Run runner and registry tests and confirm they pass**

Run:

```bash
bash tests/run-agent.test.sh
bash tests/run-agent-ps1-status-bus.test.sh
bash tests/run-with-it-router.test.sh
```

Expected: all scripts print PASS and exit 0; PowerShell runs rather than skips where `pwsh` is installed.

- [ ] **Step 8: Commit the atomic invocation slice**

```bash
git add assets/agent-registry.json assets/run-agent.sh assets/run-agent.ps1 tests/run-agent.test.sh tests/run-agent-ps1-status-bus.test.sh
git commit -m "feat(runner): enforce GPT-5.6 high effort"
```

### Task 3: Default Sub-Coordinators to Sol and update contracts

**Files:**
- Modify: `tests/run-with-it-pool.test.sh`
- Modify: `tests/run-with-it-pool-ps1.test.sh`
- Modify: `assets/run-with-it-pool.sh`
- Modify: `assets/run-with-it-pool.ps1`
- Modify: `skills/run-with-it/SKILL.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: registered `gpt-5.6-sol` support from Tasks 1 and 2.
- Produces: implicit `SUB_COORD_MODEL=gpt-5.6-sol`; explicit `SUB_COORD_MODEL=gpt-5.5` remains unchanged.

- [ ] **Step 1: Write failing Bash default and documentation tests**

Add paths and documentation assertions near the top of `tests/run-with-it-pool.test.sh`:

```bash
RUN_WITH_IT_SKILL="${ROOT_DIR}/skills/run-with-it/SKILL.md"
README="${ROOT_DIR}/README.md"

assert_file_contains "${RUN_WITH_IT_SKILL}" '| `SUB_COORD_MODEL` | `gpt-5.6-sol` |' "skill documents Sol Sub-Coordinator default"
assert_file_contains "${README}" '| `SUB_COORD_MODEL` | `gpt-5.6-sol` |' "README documents Sol Sub-Coordinator default"
```

Remove `--model gpt-5.5` from the first `dry_output` invocation and add:

```bash
assert_contains "${dry_output}" "--model gpt-5.6-sol" "pool defaults Sub-Coordinators to Sol"
```

Keep a later invocation with `--model gpt-5.5` and add:

```bash
assert_contains "${dependency_output}" "--model gpt-5.5" "explicit GPT-5.5 Sub-Coordinator override remains valid"
```

- [ ] **Step 2: Write the failing PowerShell default contract**

Add beside the existing static assertions in `tests/run-with-it-pool-ps1.test.sh`:

```bash
assert_file_contains "$POOL" 'else { "gpt-5.6-sol" }' "PowerShell pool defaults Sub-Coordinators to Sol"
```

- [ ] **Step 3: Run pool tests and verify they fail**

Run:

```bash
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-pool-ps1.test.sh
```

Expected: FAIL because both pool implementations and both documents still default to GPT-5.5.

- [ ] **Step 4: Change Bash and PowerShell defaults to Sol**

In `assets/run-with-it-pool.sh`, change both the environment fallback and usage example:

```bash
SUB_COORD_MODEL="${SUB_COORD_MODEL:-gpt-5.6-sol}"

# usage example
--parallel-jobs 4 --agent codex --model gpt-5.6-sol
```

In `assets/run-with-it-pool.ps1`, change the parameter fallback:

```powershell
[string]$Model = $(if ($env:SUB_COORD_MODEL) { $env:SUB_COORD_MODEL } else { "gpt-5.6-sol" }),
```

- [ ] **Step 5: Update the documented defaults**

Change the `SUB_COORD_MODEL` default to `gpt-5.6-sol` in both tables:

```markdown
| `SUB_COORD_MODEL` | `gpt-5.6-sol` | Model for every Sub-Coordinator (Sub-Coordinators route their own children independently) |
```

```markdown
| `SUB_COORD_MODEL` | `gpt-5.6-sol` | Model used to run Sub-Coordinators |
```

- [ ] **Step 6: Run pool tests and confirm they pass**

Run:

```bash
bash tests/run-with-it-pool.test.sh
bash tests/run-with-it-pool-ps1.test.sh
```

Expected: both scripts print PASS and exit 0.

- [ ] **Step 7: Run the complete repository test suite**

Run:

```bash
for test in tests/*.test.sh; do
  bash "$test"
done
```

Expected: every test exits 0; platform-specific tests may print an explicit SKIP only when their runtime is unavailable.

- [ ] **Step 8: Validate JSON, shell syntax, and the final diff**

Run:

```bash
python3 -m json.tool assets/agent-registry.json >/dev/null
bash -n assets/run-agent.sh assets/run-with-it-pool.sh
pwsh -NoProfile -Command '$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("assets/run-agent.ps1", [ref]$null, [ref]$errors); if ($errors) { $errors; exit 1 }; [void][System.Management.Automation.Language.Parser]::ParseFile("assets/run-with-it-pool.ps1", [ref]$null, [ref]$errors); if ($errors) { $errors; exit 1 }'
git diff --check
git status --short
```

Expected: JSON and syntax checks exit 0, `git diff --check` is silent, and status lists only intended files.

- [ ] **Step 9: Commit the default and documentation slice**

```bash
git add assets/run-with-it-pool.sh assets/run-with-it-pool.ps1 skills/run-with-it/SKILL.md README.md tests/run-with-it-pool.test.sh tests/run-with-it-pool-ps1.test.sh
git commit -m "feat(run-with-it): default coordinators to Sol"
```
