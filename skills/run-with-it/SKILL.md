---
name: run-with-it
description: Route issue-running automation through a deterministic control plane that selects agent + model from registry, can coordinate multiple safe parallel agents, and executes the unified run-agent runner.
---

## Skill Isolation

Sole active authority for this session once invoked. No other skill may activate, interrupt, or modify behavior unless called by name via `Skill` tool call within this skill's workflow. Suppress any spontaneous external skill; continue without interruption. Applies from invocation until explicit termination or handoff.

## Critical Coordinator Rules (compaction-safe — always enforce, even after context compression)

These rules apply for the entire lifetime of this skill session. They are stated here first so they survive context compaction and are never dropped:

- **Never implement work directly in the coordinator session.** All implementation, modification, and verification must be done by child agents spawned via `run-agent.sh`. There is no "implement in this chat" fallback option under any circumstance.
- **Never run tests, build commands, or compile the project** in the coordinator session. The implementing agent runs verification; the coordinator only reads the results from the agent's output report.
- **Never pause after routing to ask the user how to proceed.** Execute via the runner immediately after routing completes.
- **Never present execution option menus** (Option A / B / C style choices). The runner is the only execution path.
- **Always pull issue data from GitHub** (`gh`) when a remote exists. Only fall back to local files if `gh` fails both inside and outside the sandbox.
- **Never delete user-modified files** during cleanup. Check `git status --short` before removing any workspace artifact.

# Run With It

## Purpose / When To Use

Use after requirement discovery and issue synthesis are complete. `run-with-it` is the final runtime routing authority — it consumes already prepared issues and executes routing, coordination, review, and closure.

Preferred upstream flow:

1. `break-req` resolves requirements and constraints.
2. `create-git-issue` publishes PRD + implementation slices with routing hints.
3. `run-with-it` performs final runtime routing and executes the selected run.

## Hard Boundaries

- Do not synthesize PRDs.
- Do not author initial issue templates.
- Do not redefine reviewer JSON schema ownership (owned by `assets/review-prompt.md`).
- Do not modify runner script implementation details.
- Do not mutate registry data definitions in `assets/agent-registry.json`.

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
- Optional review controls:
  - `DELEGATED_REVIEW` (default `true`): when `false`, bypasses the entire delegated-review path and reverts to today's inline-review behavior; no `review-spawn`, `review-result`, `modify-spawn`, or `review-degraded` STATUS lines are emitted and no per-role ledger rows are written

## Asset Discovery (Required)

Resolve assets in this order:

1. `$ASSETS_DEST` if set and complete.
2. `$HOME/.ai-skill-collections/assets`.
3. `./assets`.

Required files:

- `prompt.md`
- `run-agent.sh`
- `run-agent.ps1`
- `agent-registry.json`
- `review-prompt.md`
- `complexity-prompt.md`

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
New-Item -ItemType Directory -Force "$env:USERPROFILE\.ai-skill-collections\assets"; Copy-Item -Force .\assets\prompt.md, .\assets\run-agent.ps1, .\assets\run-agent.sh, .\assets\agent-registry.json, .\assets\review-prompt.md, .\assets\complexity-prompt.md "$env:USERPROFILE\.ai-skill-collections\assets\"
```

**Bash (macOS / Linux / Git Bash):**
```bash
mkdir -p "$HOME/.ai-skill-collections/assets" && cp -f ./assets/prompt.md ./assets/run-agent.sh ./assets/run-agent.ps1 ./assets/agent-registry.json ./assets/review-prompt.md ./assets/complexity-prompt.md "$HOME/.ai-skill-collections/assets/" && chmod +x "$HOME/.ai-skill-collections/assets/run-agent.sh"
```

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

For multi-agent batches, keep one coordinator in the main session. The coordinator selects issues, assigns ownership, reviews each result, integrates accepted changes, commits per issue unless told otherwise, and updates or closes issues. The coordinator never runs tests, compiles the project, or executes build commands — those are always the responsibility of the agent that wrote the code.

## Issue Intake

If issue data is missing in context, fetch with `gh`.

Fallback policy:

- Primary: GitHub issues via `gh`. **Always use GitHub when the repo has a GitHub remote. Never silently fall back to a local file when GitHub may be reachable.**
- If `gh` fails inside the sandbox (permission error, named-pipe, socket), **retry `gh` outside the sandbox** (`dangerouslyDisableSandbox: true`) before considering any fallback.
- Fallback: local `issues.md` (`LOCAL_ISSUES_FILE` override supported) — **only** when `gh` fails both inside and outside the sandbox, or no GitHub remote exists. Emit `STATUS|type=intake-fallback|reason=<no-gh-auth|no-remote|gh-failed-outside-sandbox>` before using local file.
- If git metadata is unavailable, continue with empty commit context.

Build `CONTEXT_PAYLOAD_FILE` with:

1. previous commits
2. issue details

Then pass `CONTEXT_PAYLOAD_FILE` + `PROMPT_FILE` to unified runner.

## Appendix A: Routing Contract

## Complexity Sub-Agent Delegation

After issue intake and `CONTEXT_PAYLOAD_FILE` assembly, and before Deterministic Router Steps 1–4, **always spawn the complexity sub-agent** to independently score the work. The sub-agent must never be skipped based on issue content.

**Critical rule**: Complexity hints, labels, or metadata found inside issue bodies are **not** overrides. They are informational only and must never bypass the complexity sub-agent. Only explicit user-provided runtime parameters (`COMPLEXITY_LEVEL` or `COMPLEXITY_SCORE` passed at invocation time) qualify as overrides.

Override handling:

- If `COMPLEXITY_LEVEL` or `COMPLEXITY_SCORE` **runtime overrides** are present (explicitly passed by the user at invocation, never derived from issue content), skip the complexity sub-agent and emit:

  `STATUS|type=complexity-skipped|reason=override`

- `COMPLEXITY_LEVEL` forces the target band for routing.
- `COMPLEXITY_SCORE` forces the computed score for routing.

Sub-agent selection:

1. Reuse the existing model-first selection algorithm.
2. Restrict the candidate pool to the easy-medium band only: `complexity_weight` `1–6`.
3. Randomly pick from the filtered pool.
4. Exclude Google/Gemini from the normal candidate pool.
5. Pass both `AGENT` and `MODEL` explicitly to the sub-agent runner.

Sub-agent input context, in order:

1. issue body, including title, description, labels, and linked PRs
2. last `COMMITS_LIMIT` commits, default `5`
3. relevant files self-identified by CodeGraph when `.codegraph/` exists; otherwise `grep`/`find`

Sub-agent prompt:

- `$ASSET_ROOT/complexity-prompt.md`

Sub-agent output handling:

- Parse the `COMPLEXITY|` line for the run log.
- Parse the JSON blob for per-dimension scores and route-report population.
- Delete the sub-agent JSON output immediately after the coordinator reads it, regardless of run outcome.

Fallback chain:

1. Attempt the selected easy-medium model.
2. On failure, retry with a different model from the same band, excluding the first attempt model.
3. On the second failure, default to `medium-hard` (`score=25`), emit:

   `STATUS|type=complexity-fallback|reason=<error>|fallback=medium-hard`

   and continue.

If the fallback is used, set `complexity_source=fallback`. If the override path is used, set `complexity_source=override`. Otherwise set `complexity_source=sub-agent`.

## Deterministic Router

The complexity score comes from the complexity sub-agent, a forced override, or the bounded fallback path above.

### Model-First Selection (model chosen first; agent defaults ignored)

#### Step 1 — Map Score to Target Weight Range

Look up the score in `model_routing.score_to_weight` from `agent-registry.json`:

| Score | Label | Weight range |
|-------|-------|-------------|
| 8–12  | `quite-easy`  | 1–3 |
| 13–17 | `easy`        | 2–4 |
| 18–22 | `medium`      | 4–6 |
| 23–27 | `medium-hard` | 6–7 |
| 28–32 | `complex`     | 7–9 |
| 33–45 | `holy-fuck`   | 9–10 |

Canonical score labels: `8-12` => `quite-easy`, `13-17` => `easy`, `18-22` => `medium`, `23-27` => `medium-hard`, `28-32` => `complex`, `33-45` => `holy-fuck`.

#### Step 2 — Apply Hard Minimum Overrides

Before proceeding, raise `weight_min` if any condition is true:

- dependency state unknown or conflicting → `weight_min = 9`
- heavy shared-file ownership conflict risk → `weight_min = 9`
- broad cross-module integration change → `weight_min = 7`
- explicit user request for deep/complex orchestration → `weight_min = 9`
- ambiguous requirements with high risk of misinterpretation → `weight_min = 9`
- large blast radius with limited rollback options → `weight_min = 9`

Use the higher of the table `weight_min` and any override.

#### Step 3 — Build Pool and Randomly Select Model

From `model_catalog` in `agent-registry.json`:

1. Collect all models where `complexity_weight` is within `[weight_min, weight_max]`.
2. Keep only models available on at least one detected, non-filtered agent (check each agent's `known_models` list).
3. Apply `model_routing.provider_routing_rules`:
   - remove any model whose `provider` exceeds its `max_band` for the current complexity level
   - exclude any provider with `automatic_routing = "last_resort_only"` from the normal automatic model pool
4. Google/Gemini is last-resort-only for automatic routing. Do not include Google/Gemini models in the normal candidate pool for any complexity band.
5. Sort remaining candidates by `(complexity_weight ASC, price_tier ASC, price_output_per_1m ASC)`, prioritizing the lowest `complexity_weight` before cost tie-breakers.
6. Take the top `selection_pool_size` (default 4 from `model_routing`) — this is the **base pool**.
7. Append any models listed in `model_routing.band_required_models[current_level]` that are not already in the base pool. These are always available regardless of price ranking.
8. **Randomly pick one model** from the final pool. This distributes load across non-Google agents and providers on every run.
9. If fewer than 2 candidates exist after filtering, expand `weight_max` by 1 and retry (up to 3 expansions) until pool reaches at least 2, then continue to bounded fallback diagnostics before considering last-resort providers.

#### Step 4 — Select Agent

1. Find all agents that list the chosen model in their `known_models`.
2. Apply `AGENT_ALLOWLIST` and `AGENT_DENYLIST`.
3. Keep only detected (installed) agents.
4. **Interchangeable group rule**: `codex` and `github-copilot` are identical runners for GPT models. If both are in the candidate set, pick one at random. Do not prefer either.
5. If one agent remains, use it.
6. If multiple non-interchangeable agents remain, prefer: `claude` for Claude models, random pick for GPT models.
7. If no agent remains, fail with clear filter diagnostics before attempting fallback.

#### Step 5 — Pass to Runner

Always pass both `AGENT` and `MODEL` explicitly. Never rely on the agent's registry default.

```bash
run-agent.sh --agent "$AGENT" --model "$MODEL" --unattended
```

### Reviewer Band Selection

For the happy-path review pass, bump the implementer's band up exactly one level and then reuse the same model-first selection logic.

| Implementer band | Reviewer band | Rule |
|-------------------|---------------|------|
| `quite-easy` | `easy` | bump one band |
| `easy` | `medium` | bump one band |
| `medium` | `medium-hard` | bump one band |
| `medium-hard` | `complex` | bump one band |
| `complex` | `holy-fuck` | bump one band |
| `holy-fuck` | `holy-fuck` | stay at top band, but select a different model |

Reviewer model selection rules:

1. Set `current_level` to the bumped reviewer band.
2. Reuse the main model-first algorithm.
3. Exclude the implementer's exact `model_id` from the candidate pool before selection.
4. If the implementer is already at `holy-fuck`, keep `current_level=holy-fuck` and pick a different model from that band instead of upgrading beyond the top band.
5. Pass the chosen reviewer through the same unified runner contract as any other child agent.

### Degraded Fallback

If no installed agent supports any model in the bumped reviewer band after applying `AGENT_ALLOWLIST`, `AGENT_DENYLIST`, and bounded fallback:

1. Fall back to the **implementer's original band** (`current_level` = implementer band).
2. Exclude the implementer's exact `model_id` from the candidate pool.
3. Select a different model from the implementer band using the normal model-first algorithm.
4. Emit `STATUS|type=review-degraded|task=<n>|reason=no-higher-band-agent` before spawning the reviewer.
5. Continue with the normal per-cycle review steps. The run still completes.

One `review-degraded` STATUS line is emitted per task that triggers this path, not per cycle.

### Override Precedence (highest first)

1. `AGENT` + `MODEL` forced together (both must be valid and installed)
2. `MODEL` forced alone → skip Steps 1–3, run Step 4 with forced model
3. `AGENT` forced alone → skip Step 4, run Steps 1–3 restricted to that agent's `known_models`
4. `COMPLEXITY_LEVEL` forced → use corresponding weight range from table, skip score computation
5. `COMPLEXITY_SCORE` forced → use as computed score, run full Steps 1–4
6. Computed score from nine dimensions → full Steps 1–4

Validation rules:

- Forced `AGENT` not installed → fail fast.
- Forced `MODEL` not in chosen agent's `known_models` → fail fast.
- Forced Gemini via `AGENT`, Gemini `MODEL`, or an `AGENT_ALLOWLIST` that only leaves Gemini is an explicit user constraint and may select Gemini after validation.
- `COMPLEXITY_LEVEL` must be one of the documented labels.
- `COMPLEXITY_SCORE` must be integer in `9–45`.

### Allowlist and Denylist

Apply filters before final selection:

- Start from detected installed agents.
- If `AGENT_ALLOWLIST` is set, keep only listed agents.
- Then remove any in `AGENT_DENYLIST`.
- If result is empty, fail with clear filter diagnostics.

Denylist wins on conflicts.

### Bounded Fallback

If selected agent fails preflight or execution start:

- Attempt next compatible non-Google agent from registry fallback order.
- Stop after `MAX_AGENT_FALLBACKS` attempts.
- Do not spend `MAX_AGENT_FALLBACKS` on Google/Gemini last-resort attempts.
- Only after all eligible non-Google candidates are unavailable or fail preflight/execution start, evaluate Google/Gemini as a separate last-resort phase.
- Emit final bounded-fallback diagnostics with the normal attempted chain and any separate last-resort Google/Gemini attempt.

## Coordinator Rules File

At the very start of execution (before preflight), copy `$ASSET_ROOT/coordinator-rules.md` to `.run-with-it/coordinator-rules.md` in the working directory:

```bash
mkdir -p .run-with-it
cp "$ASSET_ROOT/coordinator-rules.md" .run-with-it/coordinator-rules.md
```

**Read `.run-with-it/coordinator-rules.md` exactly twice per run:**

1. **At startup** — once, immediately after copying it, before any other action.
2. **At resume** — once at the top of the Resume Flow, before rehydrating state.

Do not re-read it at any other point. Mid-run re-reads add the file content to conversation history on every phase, which accelerates context fill — the opposite of the goal. If compaction occurs mid-run (not at a resume boundary), the rules in the Critical Coordinator Rules section at the top of this skill file are the recovery path, since that section is front-loaded and summarized first by the compactor.

`.run-with-it/coordinator-rules.md` (the working copy) is deleted as part of normal cleanup alongside the rest of `.run-with-it/`.

## Preflight Checks

Before execution verify:

1. resolved asset root exists
2. `prompt.md` exists
3. `review-prompt.md` exists
4. runner exists and is executable (`run-agent.sh` on Bash; `run-agent.ps1` on Windows)
5. `agent-registry.json` exists
6. `complexity-prompt.md` exists
7. `coordinator-rules.md` exists
7. `gh` auth when GitHub intake is required
8. unified runner supports selected agent/model
10. **review-band reachability** (when `DELEGATED_REVIEW=true`): confirm `agent-registry.json` contains at least one model in the bumped reviewer band whose supporting agent is also detected and installed. If not, log degraded mode at preflight rather than mid-run:

   ```
   PREFLIGHT|review-band=<bumped-band>|status=degraded|reason=no-higher-band-agent|fallback-band=<implementer-band>
   ```

   This does not block execution; it signals that the run will use the degraded same-band different-model reviewer for all tasks in this run.

11. **Existing-state detection** (resume vs. discard prompt): before any issue intake or fresh task selection, check whether `.run-with-it/state.json` exists in the current working directory.

   - If it exists, pause and present exactly this prompt to the user:

     ```
     Existing run state found at .run-with-it/state.json.
     Type "resume" to continue the previous run, or "discard" to delete it and start fresh.
     ```

   - **`resume`**: do not delete the file. Proceed to the Resume Flow section.
   - **`discard`**: apply the Cleanup `Discard` policy, then continue with normal preflight and fresh issue intake as if no prior state existed.
   - Do not start any new task, fetch any issue, or spawn any agent until the user responds.

If `review-prompt.md` or `complexity-prompt.md` is missing at the resolved asset root, fail fast with the same platform-appropriate one-line fix message used in asset discovery.

## Execution

**Execute immediately and unconditionally via `run-agent.sh`.** After routing completes, invoke the runner. Never pause, never present execution options, never ask the user how they want to proceed, never implement work directly in the coordinator session. The runner is the only execution path. If it cannot be found or is not executable, fail fast — do not fall back to in-chat implementation.

### `run-agent.sh` — Full Syntax Reference

```
run-agent.sh --agent <agent> [--model <model>] --context-file <file> [--prompt-file <file>]
             [--permission-mode <mode>] [--extra-arg <arg>] [--unattended] [--dry-run]
run-agent.sh --list-agents [--detected-only]
run-agent.sh --list-models <agent>
```

| Flag | Env var equivalent | Required | Description |
|------|--------------------|----------|-------------|
| `--agent <agent>` | `AGENT` | Yes | Agent slug (e.g. `codex`, `github-copilot`, `claude`, `gemini`) |
| `--model <model>` | `MODEL` | Yes (always pass explicitly) | Model id to use |
| `--context-file <file>` | `CONTEXT_PAYLOAD_FILE` | Yes | Path to the assembled context payload file |
| `--prompt-file <file>` | `PROMPT_FILE` | No (defaults to `<script-dir>/prompt.md`) | Path to the prompt file |
| `--permission-mode <mode>` | `AGENT_PERMISSION_MODE` | No | Override agent permission mode |
| `--extra-arg <arg>` | `AGENT_EXTRA_ARGS` | No | Repeatable; appended to agent invocation |
| `--unattended` | `UNATTENDED=1` | Yes (always pass) | Required when any permission mode is set |
| `--dry-run` | — | No | Print the resolved command without executing |
| `--list-agents` | — | — | List all agents and detection status; add `--detected-only` to filter |
| `--list-models <agent>` | — | — | List known models for an agent |

Additional env vars (no flag equivalents):

| Env var | Default | Description |
|---------|---------|-------------|
| `AGENT_REGISTRY_FILE` | `<script-dir>/agent-registry.json` | Path to agent registry |
| `GUI_MODE` | `auto` | `auto` detects GUI env vars; `1` forces GUI-safe noninteractive mode; `0` forces CLI/CI mode |
| `REPO_ROOT` | `$(pwd)` | Working directory passed to agent |
| `PRINT_PROMPT` | `0` | Set to `1` to print assembled prompt without running |

`GUI_MODE` behavior:

- `auto` — detects `VSCODE_PID`, `TERM_PROGRAM=vscode`, `ELECTRON_RUN_AS_NODE`, `ANTIGRAVITY_APP`, `CURSOR_TRACE_ID`, `CLAUDE_CODE_ENTRYPOINT`; sets `GUI_MODE=1` if any match.
- `1` — forces `UNATTENDED=1` and downgrades dangerous full-bypass permission flags to safer per-agent equivalents (`codex`: `--sandbox=workspace-write`; `claude`: `--permission-mode=acceptEdits`; `github-copilot`: `--allow-all-tools`; `gemini`: `--approval-mode=auto_edit`).
- `0` — preserves CLI/CI behavior with no permission downgrades.

### Canonical Invocation

Bash (macOS / Linux / Git Bash):

```bash
GUI_MODE="${GUI_MODE:-1}" \
AGENT_REGISTRY_FILE="$ASSET_ROOT/agent-registry.json" \
"$ASSET_ROOT/run-agent.sh" \
  --agent "$AGENT" \
  --model "$MODEL" \
  --context-file "$CONTEXT_PAYLOAD_FILE" \
  --prompt-file "$ASSET_ROOT/prompt.md" \
  --unattended
```

PowerShell (Windows):

```powershell
$env:AGENT_REGISTRY_FILE = "$ASSET_ROOT\agent-registry.json"
$env:GUI_MODE = if ($env:GUI_MODE) { $env:GUI_MODE } else { "1" }
& "$ASSET_ROOT\run-agent.ps1" --agent $AGENT --model $MODEL --context-file $CONTEXT_PAYLOAD_FILE --prompt-file "$ASSET_ROOT\prompt.md" --unattended
```

Never invoke legacy per-agent runner scripts from this skill.

### Sandbox Retry

If `run-agent.sh` (or `run-agent.ps1`) fails due to a sandbox restriction — identified by permission errors, named-pipe failures, socket access denied, or state/app-server access errors — **retry the exact same invocation outside the sandbox** using `dangerouslyDisableSandbox: true` on the Bash tool call. Do not count a sandbox failure as an agent failure or advance the fallback budget. Only count it as a true agent failure if it also fails outside the sandbox. Emit:

```
STATUS|type=runner-sandbox-retry|agent=<agent>|model=<model>|reason=<error-summary>
```

before the retry, and:

```
STATUS|type=runner-sandbox-retry-result|outcome=<success|failed>
```

after.

## Cleanup

Cleanup runs only after a successful completion, failed run, interrupted run, or explicit `discard` command. Cleanup must not fire on `resume`.

### Successful Run Completion

On successful run completion:

- Delete `CONTEXT_PAYLOAD_FILE`.
- Delete `.run-with-it/state.json`, `.run-with-it/coordinator-rules.md`, and all `.run-with-it/reviews/` files; remove the directory if empty.
- For each of `technical_requirements.md`, `prd.md`, and `issues.md` present in the workspace root: run `git status --short <file>`. Delete the file **only if** it is untracked (`??`) or clean (not listed). If the file has user modifications (any other status), skip deletion and emit `STATUS|type=cleanup|action=skipped-dirty-file|file=<file>` — never delete user-modified workspace files.
- Ensure `.gitignore` contains entries for `.run-with-it/`, `technical_requirements.md`, `prd.md`, and `issues.md` using an idempotent append.
- If `.git/` exists, stage only the deleted files and `.gitignore`; commit with message `chore: remove skill-generated artifacts post-run`.
- Emit `STATUS|type=cleanup|action=completed|files_removed=<n>`.

### Failed or Interrupted Run

On failed or interrupted run:

- Keep all `.run-with-it/` files and `CONTEXT_PAYLOAD_FILE`.
- Print the paths of preserved files.
- Offer `discard` command to force-delete and restart.

### Discard

On `discard`:

- Delete all preserved files, including `CONTEXT_PAYLOAD_FILE`, `.run-with-it/state.json`, `.run-with-it/coordinator-rules.md`, `.run-with-it/reviews/`, `technical_requirements.md`, `prd.md`, and `issues.md`.
- Update `.gitignore` and commit with message `chore: remove skill-generated artifacts (discarded run)`.
- Emit `STATUS|type=cleanup|action=discarded|files_removed=<n>`.
- Proceed as a fresh run.

### Complexity Output Cleanup

The complexity sub-agent JSON output is always deleted immediately after the coordinator reads it, regardless of run outcome.

## Appendix B: Status and Ledger Contract

## Routing Report (Required)

Emit a parseable one-line route summary before execution:

```text
ROUTE|agent=<agent>|model=<model>|complexity_level=<level>|complexity_score=<score>|target_weight=<min>-<max>|model_weight=<n>|price_tier=<tier>|fallback_budget=<n>|allowlist=<value>|denylist=<value>|complexity_source=<sub-agent|fallback|override>
```

Also provide human-readable details:

1. per-dimension scores
2. final score and complexity level
3. target weight range and selected model (with `complexity_weight` and `price_tier`)
4. agent selection reason (sole match / interchangeable random pick / provider match)
5. runner command summary
6. fallback attempts used
7. completion status
8. complexity source

At the end of every run, include a final task execution ledger. The ledger must clearly show which role handled each completed task (impl, review, or modify), the cycle number, which agent and model handled it, how many lines that task changed, a short routing reason, and the child-agent token telemetry captured for that task.

Required final ledger columns:

- Task: issue number/title or local task name
- Role: `impl`, `review`, or `modify`
- Cycle: integer cycle number (`0` for the impl row, `1` for the first review/modify pair, `2` for the second review/modify pair if it occurs)
- Agent: selected agent slug/display name
- Model: selected model id
- Line changes: `+<added>/-<deleted> (<total> total)`
- Input tokens: child-agent prompt/input token count when available
- Output tokens: child-agent completion/output token count when available
- Cache hit tokens: child-agent cache-hit token count when available
- Telemetry source: telemetry origin, such as `runner-default`, provider-native, or coordinator-estimated
- Selection reasoning: one short sentence explaining why that agent/model was selected, such as score band match, forced override, sole compatible agent, interchangeable random pick, provider match, or fallback

Cycle-numbering rule: the implementer always receives `cycle=0`. The first reviewer run and the modification agent it triggers (if any) share `cycle=1`. The second reviewer run and its modification agent (if any) share `cycle=2`. This means a full revise→approve flow emits exactly four ledger rows: `role=impl|cycle=0`, `role=review|cycle=1`, `role=modify|cycle=1`, `role=review|cycle=2`.

Calculate line changes per task from the accepted diff for that task. Prefer `git diff --numstat` or commit stats after each task integration; sum added and deleted lines across files owned by that task. For binary files or unavailable stats, report `n/a` and explain why in the selection or notes text.

Preserve all existing parseable ledger fields and append token telemetry fields in this order for backward compatibility: `input_tokens`, `output_tokens`, `cache_hit_tokens`, `telemetry_source`.

Required parseable final ledger row format:

`STATUS|type=ledger|task=<task-id>|agent=<agent-name>|model=<model-id>|added=<n>|deleted=<n>|total=<n>|reason=<short-selection-reason>|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>|telemetry_source=<source>`

For multi-agent batches, emit one ledger row per child agent task and do not collapse rows by batch. For sequential runs, emit one row per completed issue/task. If a task is blocked or rejected and no diff is accepted, list `+0/-0 (0 total)` with the final status.

Normalize child-agent token telemetry from the selected runner's final telemetry contract. When a child agent does not expose token counts, emit `unknown` for the missing values and keep the original ledger row.

After the ledger, emit a final human-readable token summary aligned with the parseable rows. Include:

- Child-agent totals
- implementation-role aggregate totals across completed tasks: input, output, and cache-hit tokens
- review-role aggregate totals across all review passes
- modify-role aggregate totals across all modification passes
- separate coordinator totals when the coordinator used measurable tokens during planning, review, or integration
- a combined run total when both child-agent and coordinator totals are available
- `unknown` for any total that cannot be computed from captured telemetry

Also include explicit section labels for `Coordinator totals` and `Run totals`.

Human-readable final summary must explicitly label these five sections as `Impl totals`, `Review totals`, `Modify totals`, `Coordinator totals`, and `Run totals`. Sections with zero rows may still be emitted with `unknown` if no telemetry was captured for that role.

## Appendix C: Review Orchestration Contract

## Canonical Coordinator Contract (Required)

### Issues

- Treat issue data already present in the context payload as the source of truth for selection and planning.
- Use `gh` only when fresh issue data is needed or issue status must be updated.
- Work only on `ready-for-agent` issues.
- Continue selecting and completing ready tasks until no ready work remains for the run.
- If all ready work is complete, output `<promise>NO MORE TASKS</promise>`.

### Operating Mode

- Prefer a safe parallel batch when several ready issues have independent ownership.
- Use sequential execution when tasks are dependency-sensitive, concentrated in the same files, or share migrations, fixtures, or architecture decisions.
- Do not stop after one issue if other ready work remains.
- Reassess the queue after each completed issue or batch.

### Context Budget and Compaction Handoff (Required)

The coordinator must track a **running context budget estimate** and halt for a user-driven compaction handoff when the estimate crosses **50%** of the host model context window.

#### Estimator

- Maintain `context_bytes_total`: a running sum of the UTF-8 byte length of coordinator-visible content.
- Estimate tokens as `context_tokens_est = floor(context_bytes_total / 4)`. (Heuristic: `bytes/4`.)
- Increment the counter for coordinator-visible content including (non-exhaustive):
  - issue context payloads (including any fetched issue bodies/comments included in the payload)
  - any direct file reads (prompt files, registry reads, diffs, logs, etc.)
  - any re-reads of archived review JSON files under `.run-with-it/reviews/`
  - any ledger rows and STATUS lines the coordinator emits (count emitted text toward the estimate)

#### Denominator (context window)

- Resolve `host_context_window` by reading the active host model’s `context_window` from the resolved `agent-registry.json` `model_catalog`.
- If the host model id cannot be detected, or if it is not present in the registry catalog, fall back to `host_context_window = 200000`.

#### 50% Trigger Behavior

When `context_tokens_est / host_context_window >= 0.50` (crossing the threshold for the first time in a run):

1. Persist `.run-with-it/state.json` (schema version 1; see below).
2. Emit exactly one parseable status line:

   `STATUS|type=compact|action=user-required|state_file=.run-with-it/state.json`

3. Print a human-readable handoff instruction block (per host below).
4. **Stop**: halt new work (no new file reads, no new child-agent spawns, no new review cycles, no new integrations) and wait for the user.

The coordinator must never invoke any host compaction command itself; compaction is always user-driven.

#### User Instructions (human-readable)

After emitting the `STATUS|type=compact...` line, present per-host instructions:

- **Claude Code:** run `/compact`, then re-run the coordinator with the same run context payload, ensuring `.run-with-it/state.json` remains present.
- **Codex GUI:** use the UI’s compaction control (equivalent to “compact”), then restart the run using the same run context payload and the persisted `.run-with-it/state.json`.
- **GitHub Copilot (VS Code Chat):** use Copilot Chat’s “compact” / “summarize conversation” equivalent, then restart the run with the same run context payload and `.run-with-it/state.json`.

Do not claim equivalence between hosts beyond the intent: reduce chat context while preserving the persisted state file.

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
- Keep one coordinator responsible for final integration, review, commits, and issue updates. Tests and compilation are always run by the implementing agent, never the coordinator.

### Coordination

Each child agent receives a self-contained prompt with:

- issue number and goal
- exact ownership scope
- paths it must not edit
- relevant repo conventions copied directly into the prompt
- required verification commands (the agent must run these; the coordinator must not)
- instruction to keep changes minimal and compatible with other agents
- TDD requirement when the issue requests test-first implementation

Review handoff every child-agent result before accepting it.
Reject or revise work that violates ownership, skips required tests, duplicates domain logic, or makes unrelated edits.
The coordinator reads verification results from the agent's output report — it does not re-run tests or build commands itself.

Child agent lifecycle rules:

- close completed or blocked agents after their result is captured
- record the decision immediately: `integrate|revise|blocked`
- do not keep agents open only because more tasks might appear later

### Review Handoff

When `DELEGATED_REVIEW=false`, skip this entire section. The coordinator performs inline review using today's behavior with no child agents spawned for review or modification. No `review-spawn`, `review-result`, `modify-spawn`, or `review-degraded` STATUS lines are emitted. No per-role ledger rows for reviewer or modifier are written.

When `DELEGATED_REVIEW=true` (the default), after the implementer finishes and the diff is captured, the coordinator performs the review and modification loop:

#### Cycle Counter

- Cycle 1 = first reviewer run after implementation.
- Cycle 2 = reviewer run after first modification.
- The cap is **2 cycles**, hardcoded (no env override in this iteration).
- Cap exhaustion terminates the issue as `failed-review`.
- **Compaction survival**: the authoritative cycle counter for each task is `review_history[task].cycles_used` in `.run-with-it/state.json`. On resume, restore this value and enforce the cap against it — a task that consumed cycle 1 before compaction may use only one more cycle (cycle 2) after resume.

#### Per-Cycle Steps

1. Before assembling the reviewer payload, confirm the implementing or modifying agent reported passing verification results. If verification results are absent or report failures, **do not spawn the reviewer** — terminate the issue as `failed-review` with reason `missing-or-failed-verification`.

2. Assemble a reviewer payload file in this order:
   - issue context from the existing context payload
   - the original `PROMPT_FILE` contents
   - the captured implementer diff (or latest modification diff when cycling)
   - a per-file changed-file list with `+added/-deleted` counts for each file
   - the implementer (or modifier) verification results — commands run, pass/fail status, sandbox-retry notes if any
   - the implementer telemetry stub
3. Spawn the reviewer child agent with the selected reviewer band and selected reviewer model.
4. Emit `STATUS|type=review-spawn|task=<n>|cycle=<n>|agent=<agent-name>|model=<model-id>` before the reviewer starts.
5. Run the reviewer through the existing unified runner using the same `--agent`, `--model`, `--unattended` contract.
6. Parse the reviewer JSON output against the PRD contract below.
7. **Archive the reviewer JSON** to `.run-with-it/reviews/<issue-number>-cycle-<n>.json` immediately after parsing.
8. Emit `STATUS|type=review-result|task=<n>|cycle=<n>|verdict=<approve|revise|reject>|comment_count=<n>` after archival.

#### Verdict Routing

**`verdict=approve`**

Integrate the current diff using the existing per-issue commit policy. No modification agent is spawned.

**`verdict=revise`**

1. If the current cycle equals the cap (2), terminate the issue as `failed-review` immediately — do not spawn a modification agent.
2. Otherwise, spawn a modification agent for the current cycle:
   - Select the modification agent at the **original implementer band** using the same model-first selection algorithm.
   - Emit `STATUS|type=modify-spawn|task=<n>|cycle=<n>|agent=<agent-name>|model=<model-id>` before spawning.
   - Pass the modification agent a payload containing, in this order:
     1. the original issue context
     2. the original `prompt.md` contents
     3. the implementer's reviewed diff
     4. the complete reviewer JSON (from `.run-with-it/reviews/<issue-number>-cycle-<n>.json`)
     5. the required verification commands from `state.json` (`queue[task].verification`) — the modification agent **must** run these before reporting completion
   - The modification agent must run verification and report pass/fail results in its output, following the same sandbox-retry rule as the implementer (retry with `dangerouslyDisableSandbox: true` on permission/pipe errors before marking verification failed).
   - **Do not advance to the next review cycle if the modification agent's output does not include passing verification results.** If verification failed or was not reported, treat the modification as blocked and terminate the issue as `failed-review` — do not silently cycle to the reviewer with unverified changes.
   - Capture both the new diff and the verification results reported by the modification agent.
3. Increment the cycle counter and return to **Per-Cycle Steps** with the new diff and verification results.

**`verdict=reject`**

Skip modification entirely. Terminate the issue as `failed-review` immediately. The reviewer JSON for this cycle is already archived. No modification agent is spawned.

Reviewer JSON contract from the PRD:

```json
{
  "verdict": "approve | revise | reject",
  "summary": "one-paragraph rationale",
  "comments": [
    {
      "file": "path/to/file",
      "line": 42,
      "severity": "info | warning | critical",
      "fix": "concrete suggested change"
    }
  ],
  "blocking_reasons": ["list when verdict=reject"]
}
```

Authoritative reviewer JSON output shape is owned by `assets/review-prompt.md`. This section describes parse/validation expectations only.

## Appendix D: Resume and State Contract

### Persistent State (Required)

When the coordinator persists any file under `.run-with-it/` (including `.run-with-it/state.json` and any archived review JSON), it owns that directory namespace for the run.

#### `.run-with-it/state.json` (schema_version 1)

Write a single JSON file at `.run-with-it/state.json` using this schema:

```json
{
  "schema_version": 1,
  "queue": {
    "ready": [
      {
        "issue_number": 36,
        "title": "example title",
        "dependencies": [30],
        "dependency_proof": "freeform string",
        "ownership_scope": ["skills/run-with-it/SKILL.md"],
        "paths_to_avoid": ["assets/prompt.md"],
        "verification": ["freeform command list"],
        "status": "ready | in_progress | blocked | completed"
      }
    ],
    "blocked": [],
    "completed": []
  },
  "ledger_rows": [
    "STATUS|type=ledger|task=... (verbatim line as emitted)"
  ],
  "in_flight_agents": [
    {
      "task": 36,
      "role": "impl | review | modify | coordinator",
      "cycle": 0,
      "agent": "agent-name",
      "model": "model-id",
      "ownership_scope": ["path/owned"]
    }
  ],
  "review_history": [
    {
      "task": 36,
      "cycles_used": 1,
      "review_files": [".run-with-it/reviews/36-cycle-1.json"]
    }
  ]
}
```

State category requirements:

- `queue`: include dependencies, dependency proof, status, and ownership scope per task.
- `ledger_rows`: store the coordinator-emitted `STATUS|type=ledger...` lines verbatim.
- `in_flight_agents`: store role, cycle, and scope for any currently running or last-known active agents.
- `review_history`: store total cycles used per task and the archived review JSON file paths.

The coordinator may include additional fields, but must not omit these four categories when `schema_version` is 1.

### Resume Flow (Required)

#### Rehydration

Rebuild all four state categories in memory from `.run-with-it/state.json` (schema_version 1):

1. **`queue`** — restore all task entries. Tasks whose `status` is `"completed"` or `"done"` are skipped entirely; do not requeue them. Tasks with `status` `"ready"` or `"blocked"` are returned to their respective queues.
2. **`ledger_rows`** — restore verbatim ledger `STATUS` lines. Do not re-emit them; hold them in memory so the final ledger includes pre-compaction rows.
3. **`in_flight_agents`** — restore each entry. These represent agents that were active or last-known-active at compaction time.
4. **`review_history`** — restore `cycles_used` per task. The 2-cycle cap is enforced against these restored values for every subsequent review pass.

Emit one parseable line immediately after rehydration succeeds:

```
STATUS|type=resume|state_file=.run-with-it/state.json|tasks_restored=<n>|in_flight=<n>|skipped_done=<n>
```

If `.run-with-it/state.json` is missing or unparseable when the user types `resume`, emit:

```
STATUS|type=resume-error|reason=<missing|parse-error>|action=user-required
```

Then stop and ask the user whether to proceed as a fresh run.

#### In-Flight Agent Reattempt

For each entry in `in_flight_agents`, resume work from the phase recorded in `role`:

| Persisted `role` | Reattempt behavior |
|---|---|
| `impl` | Re-spawn the implementer for this task using the same band selection; treat as a fresh impl pass (cycle 0 stays 0). |
| `review` | Re-spawn the reviewer for this task at the recorded `cycle`. Read the existing archived review JSON for prior cycles from `review_history.review_files` before spawning. |
| `modify` | Re-spawn the modification agent for this task at the recorded `cycle`. Pass it the archived reviewer JSON for that cycle. |
| `coordinator` | No child agent to reattempt; the coordinator itself was interrupted. Resume from task selection normally. |

Emit a standard `STATUS|type=spawn` or `review-spawn`/`modify-spawn` line before each reattempted agent, identical to a first-run spawn.

#### Backward Compatibility

Runs with no prior `.run-with-it/state.json` bypass the resume/discard prompt entirely.

### `.gitignore` Auto-Append for `.run-with-it/` (Required)

On the first write of any file under `.run-with-it/` in a run:

- If a `.git/` directory exists in the current working directory, ensure `.run-with-it/` is present in the repo `.gitignore`:
  - create `.gitignore` if it does not exist
  - append `.run-with-it/` on its own line only if not already present (idempotent)
  - preserve existing `.gitignore` contents unchanged aside from the single appended entry when needed
- If `.git/` is absent, skip `.gitignore` creation or modification silently.

### Status Messages

Emit parseable one-line status messages for multi-agent runs:

- spawn: `STATUS|type=spawn|batch=<batch-id>|agent=<agent-name>|issue=#<n>|phase=assigned|scope=<owned-paths>|eta=<rough-eta>`
- heartbeat: `STATUS|type=heartbeat|batch=<batch-id>|agent=<agent-name>|issue=#<n>|phase=<exploring|implementing|testing|review>|progress=<short-text>|elapsed=<seconds>`
- completion: `STATUS|type=completion|batch=<batch-id>|agent=<agent-name>|issue=#<n>|result=<done|needs-revision|blocked>|verify=<pass|fail|partial>|next=<integrate|revise|blocked>`
- stall: `STATUS|type=stall|batch=<batch-id>|agent=<agent-name>|issue=#<n>|idle_for=<seconds>|action=<ping|replan|deparallelize|abort-agent>`
- batch summary: `STATUS|type=batch|batch=<batch-id>|running=<count>|completed=<count>|blocked=<count>|next=<text>`
- integration: `STATUS|type=integration|batch=<batch-id>|issue=#<n>|action=<merge|conflict-fix|follow-up-agent>|state=<in-progress|done>`
- close: `STATUS|type=close|batch=<batch-id>|agent=<agent-name>|issue=#<n>|reason=<completed|blocked|replaced|failed-review>`
- review spawn: `STATUS|type=review-spawn|task=<n>|cycle=<n>|agent=<agent-name>|model=<model-id>`
- review result: `STATUS|type=review-result|task=<n>|cycle=<n>|verdict=<approve|revise|reject>|comment_count=<n>`
- modify spawn: `STATUS|type=modify-spawn|task=<n>|cycle=<n>|agent=<agent-name>|model=<model-id>`
- review degraded: `STATUS|type=review-degraded|task=<n>|reason=no-higher-band-agent` — emitted once per task when degraded fallback activates; never emitted when `DELEGATED_REVIEW=false`
- complexity skipped: `STATUS|type=complexity-skipped|reason=override`
- complexity fallback: `STATUS|type=complexity-fallback|reason=<error>|fallback=medium-hard`
- compact handoff: `STATUS|type=compact|action=user-required|state_file=.run-with-it/state.json` — emitted exactly once when the context estimate crosses 50% of the host context window; the coordinator halts and waits for the user to compact
- cleanup completed: `STATUS|type=cleanup|action=completed|files_removed=<n>`
- cleanup discarded: `STATUS|type=cleanup|action=discarded|files_removed=<n>`
- final ledger row: `STATUS|type=ledger|task=<task-id>|role=<impl|review|modify>|cycle=<n>|agent=<agent-name>|model=<model-id>|added=<n>|deleted=<n>|total=<n>|reason=<short-selection-reason>|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>|telemetry_source=<source>`
- backward-compatible ledger row: `STATUS|type=ledger|task=<task-id>|agent=<agent-name>|model=<model-id>|added=<n>|deleted=<n>|total=<n>|reason=<short-selection-reason>|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>|telemetry_source=<source>`
- child-agent token totals: `STATUS|type=summary|scope=child-agents|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>`
- impl token totals: `STATUS|type=summary|scope=impl|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>`
- review token totals: `STATUS|type=summary|scope=review|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>`
- modify token totals: `STATUS|type=summary|scope=modify|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>`
- coordinator token totals: `STATUS|type=summary|scope=coordinator|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>`
- combined run token totals: `STATUS|type=summary|scope=run|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>`

Keep `progress` values under 8 words and `next` values under 5 words.

### Quality and Closure Loop

- Review each agent diff individually, then review the combined batch diff.
- Read the verification results reported by the implementing agent; do not re-run tests, build commands, or compile the project.
- Commit per issue by default.
- Update each terminal-state issue with the standardized final comment before closing or leaving it blocked.
- Close completed issues with `gh issue close` unless explicitly left open.

## Appendix E: Terminal Issue Comment Contract

### Terminal Issue Comments

Post issue comments only for terminal outcomes: `completed`, `blocked`, or `failed-review`.
Do not post this final template for non-terminal progress updates.

Use the same markdown template for every terminal outcome, with this fixed section order:

1. `## Status`
2. `## Summary`
3. `## Verification`
4. `## Token Usage`
5. `## Notes`

Terminal comment template:

```md
## Status
<completed|blocked|failed-review>

## Summary
<task outcome summary>

## Verification
<task-specific verification results>

## Token Usage
- Input tokens: <n|unknown>
- Output tokens: <n|unknown>
- Cache hit tokens: <n|unknown>

## Notes
Review: <approve|revise (N cycles)>, final verdict: <approve|reject>, reviewer model: <model-id>
<follow-ups, blockers, or additional reviewer notes — omit line if none>
```

Comment requirements:

- `Token Usage` must report task-specific telemetry only. Do not include coordinator totals or combined run totals in the issue comment.
- If any token value is unavailable, render that value explicitly as `unknown`.
- `Verification` must summarize the checks run for that issue and whether they passed, failed, or were blocked.
- `Notes` must include exactly one review summary line when `DELEGATED_REVIEW=true`. Format: `Review: <verdict-path>, final verdict: <approve|reject>, reviewer model: <model-id>`. For a straight approval write `approve (1 cycle)`; for a revise-then-approve write `revise (N cycles)` where N is the total cycle count. Omit the review summary line only when `DELEGATED_REVIEW=false`. Additional follow-up or blocker lines may follow after the review summary line.

## Guardrails

- Keep prompt content implementation-only.
- Preserve local fallback behavior when GitHub or git is unavailable.
- Keep changes minimal and focused to routing/control-plane behavior.
- **Never pause after routing to ask the user how to proceed.** Execute via the runner immediately.
- **Never offer to implement work directly in the coordinator session.** Implementation belongs to child agents via the runner. There is no "implement in this chat" option.
- **Never present execution option menus** (Option A / B / C style choices). The runner is the only execution path.
- **Never run tests, build commands, or compile the project** in the coordinator session. The implementing agent runs verification; the coordinator only reads the results from the agent's output report.
