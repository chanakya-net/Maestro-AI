---
name: run-with-it
description: Route issue-running automation through a deterministic control plane that selects agent + model from registry, can coordinate multiple safe parallel agents, and executes the unified run-agent runner.
---

# Run With It

Use this skill to process ready-for-agent issues without manually selecting a runner.

Preferred upstream flow:

1. `break-req` resolves requirements and constraints.
2. `create-git-issue` publishes PRD + implementation slices with routing hints.
3. `run-with-it` performs final runtime routing and executes the selected run.

## OS Detection

Detect the current OS before asset discovery and runner selection:

- **Windows (native PowerShell):** `$env:OS` equals `Windows_NT` and no `uname` command. Use `.ps1` runners and `$env:USERPROFILE` for home dir.
- **macOS / Linux / Git Bash / WSL:** `uname -s` returns `Darwin`, `Linux`, `MINGW*`, `MSYS*`, or `CYGWIN*`. Use `.sh` runners and `$HOME` for home dir.

Adapt all shell commands in this skill to the detected runtime:

| Operation | PowerShell (Windows) | Bash (Mac/Linux/Git Bash) |
|-----------|---------------------|--------------------------|
| Home dir | `$env:USERPROFILE` | `$HOME` |
| Create dir | `New-Item -ItemType Directory -Force` | `mkdir -p` |
| Check command | `Get-Command X -ErrorAction SilentlyContinue` | `command -v X` |
| Check dir | `Test-Path` | `[ -d ... ]` |
| Temp file | `[System.IO.Path]::GetTempFileName()` | `mktemp -t name.XXXXXX` |
| Copy file | `Copy-Item -Force` | `cp -f` |
| Make executable | *(not needed)* | `chmod +x` |

## Goal

Resolve required assets, score complexity deterministically, choose required capability, select installed agent/model targets from registry, emit parseable routing and status reports, coordinate one or more agents when work can safely run in parallel, and execute `run-agent.sh`.

## Inputs

Collect these values before execution:

- Task summary
- Optional pre-fetched issue context (otherwise fetch with `gh`)
- Optional asset root override: `ASSETS_DEST`
- Optional registry override: `AGENT_REGISTRY_FILE`
- Optional intake overrides:
  - `ISSUE_LABEL` (default `ready-for-agent`)
  - `ISSUE_LIMIT` (default `10`)
  - `ISSUE_STATE` (default `open`)
  - `COMMITS_LIMIT` (default `5`)
  - `MAX_ITERATIONS` (default `20`)
- Optional routing overrides:
  - `AGENT`
  - `MODEL`
  - `COMPLEXITY_LEVEL`
  - `COMPLEXITY_SCORE`
- Optional routing filters:
  - `AGENT_ALLOWLIST` (comma-separated)
  - `AGENT_DENYLIST` (comma-separated)
- Optional fallback bound:
  - `MAX_AGENT_FALLBACKS` (default `2`)
- Optional multi-agent bound:
  - `MAX_PARALLEL_AGENTS` (default `3`)
  - `ALLOW_PARALLEL_AGENTS` (default `true`)

## Asset Discovery (Required)

Resolve assets in this order:

1. `$ASSETS_DEST` if set and complete.
2. `$HOME/.ai-skill-collections/assets`.
3. `./assets`.

Required files:

- `prompt.md`
- `run-agent.sh`
- `agent-registry.json`

Selection rules:

- Use first path that contains all required files.
- If none are complete, stop and report missing files.
- Do not require git to resolve assets.
- Resolved asset root is the single source for that run.

### Fresh/No-Git Project Notes

- This skill must work in folders that are not initialized with git.
- Asset discovery is filesystem-based, not git-root-based.
- If assets are missing, report the platform-appropriate one-command fix:

**PowerShell (Windows):**
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ai-skill-collections\assets"; Copy-Item -Force .\assets\prompt.md, .\assets\run-agent.ps1, .\assets\run-agent.sh, .\assets\agent-registry.json "$env:USERPROFILE\.ai-skill-collections\assets\"
```

**Bash (macOS / Linux / Git Bash):**
```bash
mkdir -p "$HOME/.ai-skill-collections/assets" && cp -f ./assets/prompt.md ./assets/run-agent.sh ./assets/agent-registry.json "$HOME/.ai-skill-collections/assets/" && chmod +x "$HOME/.ai-skill-collections/assets/run-agent.sh"
```

## Responsibility Boundary

This skill owns:

- issue intake and context payload assembly
- deterministic complexity scoring
- capability-band requirement resolution
- agent/model selection using `agent-registry.json`
- multi-agent batch planning for independent ready issues
- bounded fallback policy
- status and routing report output

`run-agent.sh` only executes selected parameters.
`prompt.md` is implementation-only guidance.

## Multi-Agent Capability

`run-with-it` may run a single issue or coordinate a batch of multiple agents.

Use multiple agents when all are true:

- two or more `ready-for-agent` issues are unblocked
- ownership scopes do not overlap, or one agent has explicit ownership of shared files
- verification can run independently before final integration
- `ALLOW_PARALLEL_AGENTS` is not `false`
- the batch size is within `MAX_PARALLEL_AGENTS`

Use sequential execution when any are true:

- issues depend on one another
- issues touch the same files without a clear owner
- migrations, fixtures, generated assets, or shared contracts are involved
- requirements are ambiguous enough that one result may change the next issue

For multi-agent batches, keep one coordinator in the main session. The coordinator selects issues, assigns ownership, reviews each result, integrates accepted changes, runs verification, commits per issue unless told otherwise, and updates or closes issues.

## Issue Intake

If issue data is missing in context, fetch with `gh`.

Fallback policy:

- Primary: GitHub issues via `gh`.
- Fallback: local `issues.md` (`LOCAL_ISSUES_FILE` override supported).
- If git metadata is unavailable, continue with empty commit context.

Build `CONTEXT_PAYLOAD_FILE` with:

1. previous commits
2. issue details

Then pass `CONTEXT_PAYLOAD_FILE` + `PROMPT_FILE` to unified runner.

## Deterministic Router

### Complexity Scoring (8 dimensions, each 1-5)

Score each dimension from `1` (lowest) to `5` (highest):

1. dependency complexity
2. ownership overlap risk
3. architecture risk
4. orchestration burden
5. verification risk
6. ambiguity of requirements
7. integration surface breadth
8. rollback/recovery risk

Total score range: `8-40`.

### Score to Complexity Level

- `8-12` => `quite-easy`
- `13-17` => `easy`
- `18-22` => `medium`
- `23-27` => `medium-hard`
- `28-32` => `complex`
- `33-40` => `holy-fuck`

### Model-First Selection

The orchestrator selects the **model first**, then the agent that supports it. Agent defaults are ignored.

#### Step 1 — Map Score to Target Weight Range

Look up the score in `model_routing.score_to_weight` from `agent-registry.json`:

| Score | Label | Weight range |
|-------|-------|-------------|
| 8–12  | `quite-easy`  | 1–3 |
| 13–17 | `easy`        | 2–4 |
| 18–22 | `medium`      | 4–6 |
| 23–27 | `medium-hard` | 6–7 |
| 28–32 | `complex`     | 7–9 |
| 33–40 | `holy-fuck`   | 9–10 |

#### Step 2 — Apply Hard Minimum Overrides

Before proceeding, raise `weight_min` if any condition is true:

- dependency state unknown or conflicting → `weight_min = 9`
- heavy shared-file ownership conflict risk → `weight_min = 9`
- broad cross-module integration change → `weight_min = 7`
- explicit user request for deep/complex orchestration → `weight_min = 9`

Use the higher of the table `weight_min` and any override.

#### Step 3 — Build Pool and Randomly Select Model

From `model_catalog` in `agent-registry.json`:

1. Collect all models where `complexity_weight` is within `[weight_min, weight_max]`.
2. Keep only models available on at least one detected, non-filtered agent (check each agent's `known_models` list).
3. Apply `model_routing.provider_routing_rules`: remove any model whose `provider` exceeds its `max_band` for the current complexity level. Example: `google` is excluded for `medium-hard` and above.
4. Sort remaining candidates by `(complexity_weight ASC, price_tier ASC, price_output_per_1m ASC)`.
5. Take the top `selection_pool_size` (default 4 from `model_routing`) — this is the **base pool**.
6. Append any models listed in `model_routing.band_required_models[current_level]` that are not already in the base pool. These are always available regardless of price ranking.
7. **Randomly pick one model** from the final pool. This distributes load across agents and providers on every run.
8. If fewer than 2 candidates exist after filtering, expand `weight_max` by 1 and retry (up to 3 expansions) until pool reaches at least 2, then fail with diagnostics.

#### Step 4 — Select Agent

1. Find all agents that list the chosen model in their `known_models`.
2. Apply `AGENT_ALLOWLIST` and `AGENT_DENYLIST`.
3. Keep only detected (installed) agents.
4. **Interchangeable group rule**: `codex` and `github-copilot` are identical runners for GPT models. If both are in the candidate set, pick one at random. Do not prefer either.
5. If one agent remains, use it.
6. If multiple non-interchangeable agents remain, prefer: `claude` for Claude models, `gemini` for Gemini models, random pick for GPT models.
7. If no agent remains, fail with clear filter diagnostics before attempting fallback.

#### Step 5 — Pass to Runner

Always pass both `AGENT` and `MODEL` explicitly. Never rely on the agent's registry default.

```bash
run-agent.sh --agent "$AGENT" --model "$MODEL" --unattended
```

### Override Precedence (highest first)

1. `AGENT` + `MODEL` forced together (both must be valid and installed)
2. `MODEL` forced alone → skip Steps 1–3, run Step 4 with forced model
3. `AGENT` forced alone → skip Step 4, run Steps 1–3 restricted to that agent's `known_models`
4. `COMPLEXITY_LEVEL` forced → use corresponding weight range from table, skip score computation
5. `COMPLEXITY_SCORE` forced → use as computed score, run full Steps 1–4
6. Computed score from eight dimensions → full Steps 1–4

Validation rules:

- Forced `AGENT` not installed → fail fast.
- Forced `MODEL` not in chosen agent's `known_models` → fail fast.
- `COMPLEXITY_LEVEL` must be one of the documented labels.
- `COMPLEXITY_SCORE` must be integer in `8–40`.

Validation rules:

- If forced `AGENT` is not installed/detected, fail fast.
- If forced `MODEL` is invalid for selected agent, fail fast.
- `COMPLEXITY_LEVEL` must be one of documented levels.
- `COMPLEXITY_SCORE` must be integer in `8-40`.

### Allowlist and Denylist

Apply filters before final selection:

- Start from detected installed agents.
- If `AGENT_ALLOWLIST` is set, keep only listed agents.
- Then remove any in `AGENT_DENYLIST`.
- If result is empty, fail with clear filter diagnostics.

Denylist wins on conflicts.

### Bounded Fallback

If selected agent fails preflight or execution start:

- Attempt next compatible agent from registry fallback order.
- Stop after `MAX_AGENT_FALLBACKS` attempts.
- Emit final bounded-fallback failure with attempted chain.

## Preflight Checks

Before execution verify:

1. resolved asset root exists
2. `prompt.md` exists
3. runner exists and is executable (`run-agent.sh` on Bash; `run-agent.ps1` on Windows)
4. `agent-registry.json` exists
5. `gh` auth when GitHub intake is required
6. unified runner supports selected agent/model

## Execution

Use unified runner only. Select runner based on detected OS (see OS Detection table above).

Required environment/flags:

- `AGENT_REGISTRY_FILE="$ASSET_ROOT/agent-registry.json"`
- `CONTEXT_PAYLOAD_FILE`
- `PROMPT_FILE="$ASSET_ROOT/prompt.md"` (or override)
- selected `AGENT`
- selected `MODEL` (optional; default from registry)

Bash (macOS / Linux / Git Bash):

```bash
AGENT_REGISTRY_FILE="$ASSET_ROOT/agent-registry.json" \
CONTEXT_PAYLOAD_FILE="$CONTEXT_PAYLOAD_FILE" \
PROMPT_FILE="$ASSET_ROOT/prompt.md" \
"$ASSET_ROOT/run-agent.sh" --agent "$AGENT" --model "$MODEL" --unattended
```

PowerShell (Windows):

```powershell
$env:AGENT_REGISTRY_FILE = "$ASSET_ROOT\agent-registry.json"
$env:CONTEXT_PAYLOAD_FILE = $CONTEXT_PAYLOAD_FILE
$env:PROMPT_FILE = "$ASSET_ROOT\prompt.md"
& "$ASSET_ROOT\run-agent.ps1" --agent $AGENT --model $MODEL --unattended
```

Never invoke legacy per-agent runner scripts from this skill.

## Routing Report (Required)

Emit a parseable one-line route summary before execution:

```text
ROUTE|agent=<agent>|model=<model>|complexity_level=<level>|complexity_score=<score>|target_weight=<min>-<max>|model_weight=<n>|price_tier=<tier>|fallback_budget=<n>|allowlist=<value>|denylist=<value>
```

Also provide human-readable details:

1. per-dimension scores
2. final score and complexity level
3. target weight range and selected model (with `complexity_weight` and `price_tier`)
4. agent selection reason (sole match / interchangeable random pick / provider match)
5. runner command summary
6. fallback attempts used
7. completion status

## Canonical Coordinator Contract (Required)

Apply these rules after routing and before invoking the selected agent.

### Issues

- Treat issue data already present in the context payload as the source of truth for selection and planning.
- Use `gh` only when fresh issue data is needed or issue status must be updated.
- Work only on `ready-for-agent` issues.
- Continue selecting and completing ready tasks until no ready work remains for the run.
- If all ready work is complete, output `<promise>NO MORE TASKS</promise>`.

### Operating Mode

You are the coordinator. Your job is to plan, delegate, review, integrate, commit, and update issues.
Implementation belongs to child agents or the selected external agent process.

- Prefer a safe parallel batch when several ready issues have independent ownership.
- Use sequential execution when tasks are dependency-sensitive, concentrated in the same files, or share migrations, fixtures, or architecture decisions.
- Do not stop after one issue if other ready work remains.
- Reassess the queue after each completed issue or batch.

### Task Selection

Before selecting a task, confirm dependencies are complete.
If a task depends on another open or in-progress task, run the dependency first or mark the task blocked.

Prioritize in this order:

1. critical fixes
2. development infrastructure
3. tracer-bullet feature slices
4. polish and quick wins
5. refactors

For each selected issue, define:

- GitHub issue number and title
- why it is ready now
- dependency proof
- expected file or module ownership
- verification steps

### Parallel Planning

When multiple ready issues are available, build the largest safe batch.

- Assign one issue per child agent.
- Group only issues with minimal file overlap and low coordination cost.
- Avoid batching issues that edit the same files unless one agent owns those files.
- Keep one coordinator responsible for final integration, review, tests, commits, and issue updates.

### Coordination

Each child agent receives a self-contained prompt with:

- issue number and goal
- exact ownership scope
- paths it must not edit
- relevant repo conventions copied directly into the prompt
- required verification commands
- instruction to keep changes minimal and compatible with other agents
- TDD requirement when the issue requests test-first implementation

Review every child-agent result before accepting it.
Reject or revise work that violates ownership, skips required tests, duplicates domain logic, or makes unrelated edits.

Child agent lifecycle rules:

- close completed or blocked agents after their result is captured
- record the decision immediately: `integrate|revise|blocked`
- do not keep agents open only because more tasks might appear later

### Status Messages

Emit parseable one-line status messages for multi-agent runs:

- spawn: `STATUS|type=spawn|batch=<batch-id>|agent=<agent-name>|issue=#<n>|phase=assigned|scope=<owned-paths>|eta=<rough-eta>`
- heartbeat: `STATUS|type=heartbeat|batch=<batch-id>|agent=<agent-name>|issue=#<n>|phase=<exploring|implementing|testing|review>|progress=<short-text>|elapsed=<seconds>`
- completion: `STATUS|type=completion|batch=<batch-id>|agent=<agent-name>|issue=#<n>|result=<done|needs-revision|blocked>|verify=<pass|fail|partial>|next=<integrate|revise|blocked>`
- stall: `STATUS|type=stall|batch=<batch-id>|agent=<agent-name>|issue=#<n>|idle_for=<seconds>|action=<ping|replan|deparallelize|abort-agent>`
- batch summary: `STATUS|type=batch|batch=<batch-id>|running=<count>|completed=<count>|blocked=<count>|next=<text>`
- integration: `STATUS|type=integration|batch=<batch-id>|issue=#<n>|action=<merge|conflict-fix|follow-up-agent>|state=<in-progress|done>`
- close: `STATUS|type=close|batch=<batch-id>|agent=<agent-name>|issue=#<n>|reason=<completed|blocked|replaced|failed-review>`

Keep `progress` values under 8 words and `next` values under 5 words.

### Quality and Closure Loop

- Review each agent diff individually, then review the combined batch diff.
- Run issue-specific checks before broader suites.
- Commit per issue by default.
- Update each issue with completion, verification, and follow-up notes.
- Close completed issues with `gh issue close` unless explicitly left open.

## Guardrails

- Keep prompt content implementation-only.
- Preserve local fallback behavior when GitHub or git is unavailable.
- Keep changes minimal and focused to routing/control-plane behavior.
