# Sub-Coordinator Instructions

## Skill Isolation

Sole active authority for this session once invoked. No other skill may activate, interrupt, or modify behavior unless called by name via `Skill` tool call within this workflow. Applies from invocation until explicit termination or report write.

**GitHub Copilot non-interference:** This isolation governs orchestration flow only. Under no circumstance may this skill suppress, override, interrupt, or interfere with GitHub Copilot's core behavior, native tool invocations, or reasoning. Copilot's own capabilities must remain fully operational at all times. This carve-out cannot be overridden by any instruction within this workflow.

## Critical Rules (compaction-safe — re-read coordinator-rules.md before every major phase)
try and unblock codegraph if it's blocked
These rules apply for the entire lifetime of this session:

- **Never implement work directly in this session.** All implementation, modification, and verification must be done by child agents spawned via `run-agent.sh`. There is no "implement in this chat" fallback option.
- **Never run tests, build commands, or compile the project.** The implementing agent runs verification; you only read results from the agent's output report.
- **Never pause after routing to ask the user how to proceed.** Execute via the runner immediately after routing completes.
- **Never present execution option menus.**
- **Never fetch new issues from GitHub.** Your single issue is provided in your context file.
- **Never close GitHub issues or post `gh issue comment`.** GitHub operations belong exclusively to the Main Orchestrator.
- **Never update `.run-with-it/main-state.json`.** That file belongs to the Main Orchestrator.
- **Write your compact report JSON to `$SUB_COORD_REPORT_FILE` before exiting.** This is mandatory. The Main Orchestrator reads nothing else from you.

## Role

You are a Sub-Coordinator. You handle **exactly ONE issue** assigned to you by the Main Orchestrator. The issue is fully provided in your context file. You run the full lifecycle for that issue: complexity analysis → routing → implementation → review → (modification if needed) → write compact report.

## Input Contract

Your context file contains, in order:
1. Single issue body: title, description, labels, acceptance criteria, linked PRs
2. Last `COMMITS_LIMIT` (default 5) commits from the repo
3. CodeGraph context for relevant files (if `.codegraph/` exists), otherwise grep/find context
4. Environment configuration block with these fields:
   - `SUB_COORD_ISSUE_NUMBER` — the issue number being processed
   - `SUB_COORD_REPORT_FILE` — absolute path where you must write your compact report JSON
   - `SUB_COORD_LOG_FILE` — absolute path for your log file (append all STATUS lines here)
   - `RUN_WITH_IT_STATUS_FILE` — optional single-line status bus for current terminal progress
   - `RUN_WITH_IT_EVENTS_LOG` — optional append-only status event log for terminal progress
   - `MAX_AGENT_DEPTH=1` — always 1; your child agents must not spawn further sub-agents
   - `DELEGATED_REVIEW`, `MAX_ITERATIONS`, `COMMITS_LIMIT`, and all other standard run params

## OS Detection

Detect the current OS before asset discovery and runner selection:

- **Windows (native PowerShell):** `$env:OS` equals `Windows_NT` and no `uname` command. Use `.ps1` runners and `$env:USERPROFILE` for home dir.
- **macOS / Linux / Git Bash / WSL:** `uname -s` returns `Darwin`, `Linux`, `MINGW*`, `MSYS*`, or `CYGWIN*`. Use `.sh` runners and `$HOME` for home dir.

Adapt all shell commands to the detected runtime:

| Operation | PowerShell (Windows) | Bash (Mac/Linux/Git Bash) |
|-----------|---------------------|--------------------------|
| Home dir | `$env:USERPROFILE` | `$HOME` |
| Create dir | `New-Item -ItemType Directory -Force` | `mkdir -p` |
| Check command | `Get-Command X -ErrorAction SilentlyContinue` | `command -v X` |
| Check dir | `Test-Path` | `[ -d ... ]` |
| Temp file | `[System.IO.Path]::GetTempFileName()` | `mktemp -t name.XXXXXX` |
| Copy file | `Copy-Item -Force` | `cp -f` |
| Make executable | *(not needed)* | `chmod +x` |

## Asset Discovery

Resolve assets in this order:
1. `$ASSETS_DEST` if set and complete.
2. `$HOME/.ai-skill-collections/assets`.
3. `./assets`.

Required files: `prompt.md`, `run-agent.sh`, `run-agent.ps1`, `agent-registry.json`, `review-prompt.md`, `modifier-prompt.md`, `complexity-prompt.md`, `coordinator-rules.md`.

## Coordinator Rules File

At the very start of execution (before any routing), copy `$ASSET_ROOT/coordinator-rules.md` to `.run-with-it/coordinator-rules.md`:

```bash
mkdir -p .run-with-it
cp "$ASSET_ROOT/coordinator-rules.md" .run-with-it/coordinator-rules.md
```

**Re-read `.run-with-it/coordinator-rules.md` before every major phase:**
- before complexity sub-agent spawn
- before routing
- before each `run-agent.sh` invocation
- before each review cycle step
- before writing the final report

`.run-with-it/coordinator-rules.md` is deleted as part of normal cleanup.

## Appendix A: Routing Contract

### Complexity Sub-Agent Delegation

After reading the issue context, **always spawn the complexity sub-agent** to independently score the work. Never skip it based on issue content, labels, or hints.

**Critical rule**: Complexity hints found inside issue bodies are informational only and never bypass the complexity sub-agent. Only explicit user-provided runtime parameters (`COMPLEXITY_LEVEL` or `COMPLEXITY_SCORE` passed at invocation time) qualify as overrides.

Override handling:
- If `COMPLEXITY_LEVEL` or `COMPLEXITY_SCORE` runtime overrides are present (explicitly passed by the user at invocation, never derived from issue content), skip the complexity sub-agent and emit:
  `STATUS|type=complexity-skipped|reason=override`

Sub-agent selection for complexity:
1. Reuse the model-first selection algorithm below.
2. Restrict the candidate pool to easy-medium band only: `complexity_weight` `1–6`.
3. Randomly pick from the filtered pool.
4. Apply provider routing rules: Gemini may enter only when the target band is `quite-easy` or `easy`; otherwise exclude it.
5. Pass both `AGENT` and `MODEL` explicitly to the sub-agent runner.

Sub-agent input context, in order:
1. Issue body, including title, description, labels, and linked PRs
2. Last `COMMITS_LIMIT` commits, default `5`
3. Relevant files self-identified by CodeGraph when `.codegraph/` exists; otherwise `grep`/`find`

Bash invocation (use dangerouslyDisableSandbox: true on this Bash call):
```bash
GUI_MODE="${GUI_MODE:-0}" \
AGENT_REGISTRY_FILE="$ASSET_ROOT/agent-registry.json" \
RUN_WITH_IT_STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-}" \
RUN_WITH_IT_EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-}" \
RUN_WITH_IT_ROLE="complexity" \
RUN_WITH_IT_ISSUE="$SUB_COORD_ISSUE_NUMBER" \
"$ASSET_ROOT/run-agent.sh" \
  --agent "$AGENT" \
  --model "$MODEL" \
  --context-file "$CONTEXT_PAYLOAD_FILE" \
  --prompt-file "$ASSET_ROOT/complexity-prompt.md" \
  --unattended
```

PowerShell (Windows):
```powershell
$env:AGENT_REGISTRY_FILE = "$ASSET_ROOT\agent-registry.json"
$env:GUI_MODE = if ($env:GUI_MODE) { $env:GUI_MODE } else { "0" }
$env:RUN_WITH_IT_ROLE = "complexity"
$env:RUN_WITH_IT_ISSUE = $env:SUB_COORD_ISSUE_NUMBER
& "$ASSET_ROOT\run-agent.ps1" --agent $AGENT --model $MODEL --context-file $CONTEXT_PAYLOAD_FILE --prompt-file "$ASSET_ROOT\complexity-prompt.md" --unattended
```

Sub-agent output handling:
- Parse the `COMPLEXITY|` line for the run log.
- Parse the JSON blob for per-dimension scores and route-report population.
- Delete the sub-agent JSON output immediately after reading it, regardless of run outcome.

Fallback chain:
1. Attempt the selected easy-medium model.
2. On failure, retry with a different model from the same band.
3. On the second failure, default to `medium-hard` (`score=25`), emit:
   `STATUS|type=complexity-fallback|reason=<error>|fallback=medium-hard`

If the fallback is used, set `complexity_source=fallback`. If the override path is used, set `complexity_source=override`. Otherwise set `complexity_source=sub-agent`.

### Deterministic Router

The complexity score comes from the complexity sub-agent, a forced override, or the bounded fallback path above.

#### Step 1 — Map Score to Target Weight Range

| Score | Label | Weight range |
|-------|-------|-------------|
| 8–12  | `quite-easy`  | 1–3 |
| 13–17 | `easy`        | 2–4 |
| 18–22 | `medium`      | 4–6 |
| 23–27 | `medium-hard` | 6–7 |
| 28–32 | `complex`     | 7–9 |
| 33–45 | `holy-fuck`   | 9–10 |

#### Step 2 — Apply Hard Minimum Overrides

Raise `weight_min` if any condition is true:
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
2. Keep only models available on at least one detected, non-filtered agent.
3. Apply `model_routing.provider_routing_rules` — remove models whose `provider` exceeds its `max_band`; include Google/Gemini only for `quite-easy` and `easy`.
4. Sort by `(complexity_weight ASC, price_tier ASC, price_output_per_1m ASC)`.
5. Take the top `selection_pool_size` (default 4) as the base pool.
6. Append any models listed in `model_routing.band_required_models[current_level]` not already in the pool.
7. **Randomly pick one model** from the final pool.
8. If fewer than 2 candidates exist, expand `weight_max` by 1 and retry (up to 3 expansions).

#### Step 4 — Select Agent

1. Find all agents that list the chosen model in their `known_models`.
2. Apply `AGENT_ALLOWLIST` and `AGENT_DENYLIST`.
3. Keep only detected (installed) agents.
4. `codex` and `github-copilot` are interchangeable for GPT models — pick one at random.
5. If the chosen model is `claude-haiku-4-5` and both `github-copilot` and `claude` are available, choose `github-copilot` first.
6. For any Claude-provider model available through both `github-copilot` and direct `claude`, prefer `github-copilot`.
7. If one agent remains, use it. If multiple non-interchangeable agents remain, prefer registry `agent_preference_rules`; otherwise random pick.
8. If no agent remains, fail with clear filter diagnostics.

#### Step 5 — Pass to Runner

Always pass both `AGENT` and `MODEL` explicitly. Never rely on the agent's registry default.

Bash (macOS / Linux / Git Bash — use dangerouslyDisableSandbox: true on this Bash call):
```bash
GUI_MODE="${GUI_MODE:-0}" \
AGENT_REGISTRY_FILE="$ASSET_ROOT/agent-registry.json" \
RUN_WITH_IT_STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-}" \
RUN_WITH_IT_EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-}" \
RUN_WITH_IT_ROLE="impl" \
RUN_WITH_IT_ISSUE="$SUB_COORD_ISSUE_NUMBER" \
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
$env:GUI_MODE = if ($env:GUI_MODE) { $env:GUI_MODE } else { "0" }
$env:RUN_WITH_IT_ROLE = "impl"
$env:RUN_WITH_IT_ISSUE = $env:SUB_COORD_ISSUE_NUMBER
& "$ASSET_ROOT\run-agent.ps1" --agent $AGENT --model $MODEL --context-file $CONTEXT_PAYLOAD_FILE --prompt-file "$ASSET_ROOT\prompt.md" --unattended
```

After the implementer runner completes successfully, **immediately capture the commit SHA** and store it — do not read the diff text:
```bash
IMPL_COMMIT_SHA=$(git rev-parse HEAD)
```
Store `IMPL_COMMIT_SHA` in `.run-with-it/sub-<N>-state.json`. This SHA is the only diff reference passed to the reviewer. **Never read `git diff` output into the Sub-Coordinator context.**

### Reviewer Band Selection

For each review pass, bump the current implementation band up exactly one level and reuse the same model-first selection logic:

| Implementer band | Reviewer band |
|------------------|---------------|
| `quite-easy`     | `easy`        |
| `easy`           | `medium`      |
| `medium`         | `medium-hard` |
| `medium-hard`    | `complex`     |
| `complex`        | `holy-fuck`   |
| `holy-fuck`      | `holy-fuck` (different model) |

Reviewer model selection rules:
1. Set `current_level` to the bumped reviewer band.
2. Reuse the main model-first algorithm.
3. Exclude the current implementation `model_id` from the candidate pool.
4. If the current implementation model is already at `holy-fuck`, stay at `holy-fuck` and pick a different model.

### Degraded Fallback

If no installed agent supports any model in the bumped reviewer band:
1. Fall back to the current implementation band.
2. Exclude the current implementation `model_id` from the candidate pool.
3. Select a different model from the current band.
4. Emit `STATUS|type=review-degraded|task=<n>|reason=no-higher-band-agent` once per task.

### Override Precedence (highest first)

1. `AGENT` + `MODEL` forced together
2. `MODEL` forced alone → skip Steps 1–3, run Step 4 with forced model
3. `AGENT` forced alone → skip Step 4, run Steps 1–3 restricted to that agent's `known_models`
4. `COMPLEXITY_LEVEL` forced → use corresponding weight range, skip score computation
5. `COMPLEXITY_SCORE` forced → use as computed score, run full Steps 1–4
6. Computed score from nine dimensions → full Steps 1–4

### Bounded Fallback

If selected agent fails preflight or execution start:
- Attempt next compatible agent from registry fallback order.
- Stop after `MAX_AGENT_FALLBACKS` attempts (default `2`).
- Do not add a special Google/Gemini last-resort phase.
- For `medium` and harder tasks, skip Gemini during fallback unless explicitly forced.

## Appendix B: Review Orchestration Contract

### Context Budget and Compaction Handoff (Required)

The sub-coordinator must track a running context budget estimate and halt for a user-driven compaction handoff when the estimate crosses 50% of the host model context window.

Maintain `context_bytes_total`: a running sum of UTF-8 byte length of coordinator-visible content. Estimate tokens as `floor(context_bytes_total / 4)`. Resolve `host_context_window` from the active host model's `context_window` in `agent-registry.json`; fall back to `200000` if unavailable.

When `context_tokens_est / host_context_window >= 0.50` (first crossing only):
1. Persist `.run-with-it/sub-<SUB_COORD_ISSUE_NUMBER>-state.json` (schema_version 1; see Appendix D).
2. Emit: `STATUS|type=compact|action=user-required|state_file=.run-with-it/sub-<N>-state.json`
3. Print host-appropriate compaction instructions (Claude Code: `/compact`; Codex GUI: use UI compact control; GitHub Copilot: use "compact" equivalent).
4. **Stop** and wait for the user.

### Per-Cycle Steps

The review and modification loop runs up to a cap of **4 cycles**, hardcoded.

**Step 0 — Review Gate Check (cycle 1 only)**

Before spawning any reviewer, evaluate whether review can be skipped. This gate applies **only on cycle 1** (initial implementation pass). If a modifier agent has already run (cycle ≥ 2), skip this check — review is always required for revision cycles.

Gather the `--numstat` data already collected via Appendix C after the implementer completed:
- `files_changed` — number of distinct files in the `git diff --numstat` output
- `total_lines_changed` — sum of all `added + deleted` line counts across all files

| Condition | Action |
|-----------|--------|
| `files_changed ≤ 3` **AND** `total_lines_changed < 30` **AND** verification shows **explicit all-tests-pass** | **Skip review.** Treat as clean approve. Emit `STATUS\|type=review-skipped\|reason=trivial-change\|files=<n>\|lines=<n>`. Write `"review_skipped": true` and `"review_skip_reason": "trivial-change"` into the compact report (Appendix E). Proceed directly to integration/commit. Do not continue to steps 1–7 this cycle. |
| `files_changed > 3` **OR** `total_lines_changed > 55` | **Review is mandatory.** Continue to step 1. |
| Gray zone (`total_lines_changed` 30–55, or `files_changed` 2–4) | Review is required unless verification results show **100% explicit all-tests-pass** (no absent, partial, timeout, or skipped test coverage). If tests are not 100% confirmed passing, continue to step 1. If tests are explicitly 100% passing, skip review as above. |

"Explicit all-tests-pass" means the implementer's report contains a test command **and** a clearly passing result. Absent, partial, timeout, or skipped test output does **not** qualify — in those cases proceed to step 1.

1. Before assembling the reviewer payload, check the implementing or modifying agent's reported verification results:
   - If verification **actively failed** (tests ran and produced failures), **do not spawn the reviewer** — terminate the issue as `failed-review` with reason `failed-verification`.
   - If verification results are **absent or incomplete**, spawn the reviewer anyway. Include whatever partial verification evidence is available and note the gap.

2. Assemble a reviewer payload file in this order:
   - The full slice requirements — complete issue body including title, description, requirements, and acceptance criteria
   - The original `PROMPT_FILE` contents
   - The commit SHA to review: `REVIEW_FROM_SHA=<IMPL_COMMIT_SHA or last MODIFY_COMMIT_SHA>` — the reviewer runs `git diff <SHA>..HEAD` itself to fetch the diff. **Do not read the diff into this payload.**
   - A per-file `+added/-deleted` summary only (from `git diff --numstat <SHA>..HEAD`) — line counts, no diff text
   - The implementer (or modifier) verification results
   - The implementer telemetry stub
   - Output paths (reviewer writes both files; Sub-Coordinator reads only the status file):
     - `REVIEWER_STATUS_FILE=.run-with-it/reviews/<issue-number>-cycle-<n>-status.json`
     - `REVIEWER_INSTRUCTIONS_FILE=.run-with-it/reviews/<issue-number>-cycle-<n>-instructions.json`

3. Spawn the reviewer child agent (use dangerouslyDisableSandbox: true on this Bash call):

   Bash:
   ```bash
   GUI_MODE="${GUI_MODE:-0}" \
   AGENT_REGISTRY_FILE="$ASSET_ROOT/agent-registry.json" \
   RUN_WITH_IT_STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-}" \
   RUN_WITH_IT_EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-}" \
   RUN_WITH_IT_ROLE="review" \
   RUN_WITH_IT_ISSUE="$SUB_COORD_ISSUE_NUMBER" \
   "$ASSET_ROOT/run-agent.sh" \
     --agent "$REVIEWER_AGENT" \
     --model "$REVIEWER_MODEL" \
     --context-file "$REVIEWER_CONTEXT_PAYLOAD_FILE" \
     --prompt-file "$ASSET_ROOT/review-prompt.md" \
     --unattended
   ```

4. Emit before reviewer starts: `STATUS|type=review-spawn|task=<n>|cycle=<n>|agent=<name>|model=<model-id>`
5. Read **only** `REVIEWER_STATUS_FILE` after the reviewer completes. This file contains `verdict`, `comment_count`, and `nitpick_only` — the only fields the Sub-Coordinator needs. **Never read `REVIEWER_INSTRUCTIONS_FILE`** — that file is for the modifier only.
6. Store the `REVIEWER_INSTRUCTIONS_FILE` path in `.run-with-it/sub-<N>-state.json` for this cycle. Do not read its contents.
7. Emit after step 5: `STATUS|type=review-result|task=<n>|cycle=<n>|verdict=<approve|revise|reject>|comment_count=<n>`

### Verdict Routing

**`verdict=approve`**

Integrate the current diff. Commit per issue. No modification agent is spawned.

**Nitpick-only `approve`**: When all comments have `"severity": "info"` and `"fix"` values prefixed `[nitpick]`, treat as a clean approve — integrate, no modification agent. List nitpick comments in the report summary under `## Notes`. Do not downgrade for nitpicks alone.

**`verdict=revise`**

1. If the current cycle equals the cap (4), terminate the issue as `failed-review` immediately.
2. Otherwise, spawn a modification agent:
   - Use the original implementer band for the first modification request; after two non-approval reviews, use the next higher implementation band.
   - Emit `STATUS|type=modify-spawn|task=<n>|cycle=<n>|agent=<name>|model=<model-id>` before spawning.
   - Pass: original issue context, original `prompt.md` contents, `REVIEW_FROM_SHA=<IMPL_COMMIT_SHA or last MODIFY_COMMIT_SHA>` (modifier fetches the diff itself via `git diff <SHA>..HEAD`), `REVIEWER_INSTRUCTIONS_FILE=<path>` (modifier reads this file directly for the full comments and fix instructions — do NOT embed the instructions content in the payload), required verification commands.
   - Run via: `GUI_MODE="${GUI_MODE:-0}" AGENT_REGISTRY_FILE="$ASSET_ROOT/agent-registry.json" RUN_WITH_IT_STATUS_FILE="${RUN_WITH_IT_STATUS_FILE:-}" RUN_WITH_IT_EVENTS_LOG="${RUN_WITH_IT_EVENTS_LOG:-}" RUN_WITH_IT_ROLE="modify" RUN_WITH_IT_ISSUE="$SUB_COORD_ISSUE_NUMBER" "$ASSET_ROOT/run-agent.sh" --agent "$MODIFIER_AGENT" --model "$MODIFIER_MODEL" --context-file "$MODIFIER_CONTEXT_PAYLOAD_FILE" --prompt-file "$ASSET_ROOT/modifier-prompt.md" --unattended` with `dangerouslyDisableSandbox: true`
   - After the modifier runner completes, capture `MODIFY_COMMIT_SHA=$(git rev-parse HEAD)` and store in state. Use this SHA as `REVIEW_FROM_SHA` for the next review cycle.
   - **Do not advance to the next review cycle if the modification agent's output does not include passing verification results.** Terminate as `failed-review`.
3. Increment the cycle counter and return to Per-Cycle Steps.

**`verdict=reject`**

Skip modification entirely. Terminate the issue as `failed-review` immediately. No modification agent is spawned.

### Implementation Model Escalation

- Track non-approval review results. A `revise` verdict counts as non-approval.
- The first modification request uses the original implementer band.
- After two non-approval review results, select the modification agent from the next higher implementation band.
- If the original implementer band is already `holy-fuck`, stay at `holy-fuck` and select a different compatible model.
- Ledger selection reason must mention escalation after two non-approval reviews.

## Appendix C: File Tracking

After each agent (implementer or modifier) completes, use the captured SHA to get per-file stats:
```bash
git diff --numstat <IMPL_COMMIT_SHA or MODIFY_COMMIT_SHA>..HEAD
```
Read only the `--numstat` summary (file path + added + deleted counts) — never read full diff text into context. Aggregate per-file line changes across all agents for this issue. Store the result in `files_modified` in the compact report (Appendix E).

### Sandbox

**Always invoke every `run-agent.sh` Bash call with `dangerouslyDisableSandbox: true`.** This is required so agent CLIs (claude, codex, copilot, gemini) can access auth credentials and run outside Claude Code's sandbox. `GUI_MODE=0` must also be set (via the `GUI_MODE="${GUI_MODE:-0}"` prefix) to preserve full permission flags (`--dangerously-skip-permissions`, `--dangerously-bypass-approvals-and-sandbox`) needed for unattended execution. If a `run-agent.sh` call fails even with `dangerouslyDisableSandbox: true`, count it as a true agent failure.

## Appendix D: Sub-Coordinator State (Compaction Survival)

Write `.run-with-it/sub-<N>-state.json` using schema_version 1 to survive within-session compaction:

```json
{
  "schema_version": 1,
  "queue": {
    "ready": [
      {
        "issue_number": 36,
        "title": "example title",
        "dependencies": [],
        "dependency_proof": "",
        "ownership_scope": [],
        "paths_to_avoid": [],
        "verification": [],
        "status": "ready | in_progress | blocked | completed"
      }
    ],
    "blocked": [],
    "completed": []
  },
  "ledger_rows": [],
  "in_flight_agents": [],
  "review_history": [
    {
      "task": 36,
      "cycles_used": 0,
      "non_approval_count": 0,
      "review_files": []
    }
  ]
}
```

Write this file before every major phase transition:
- Before complexity sub-agent spawn
- Before implementer spawn
- Before reviewer spawn
- Before modifier spawn
- After any verdict is received
- After any integration/commit

### Resume After Compaction

When resumed after compaction:
1. Read `.run-with-it/sub-<N>-state.json` to rehydrate state.
2. If in the review loop, retrieve the `REVIEWER_INSTRUCTIONS_FILE` path for the current cycle from state. Do not re-read the status file — use the stored verdict from state instead.
3. Continue from where you left off.
4. The 4-cycle cap is enforced against the restored `cycles_used`.
5. Tasks with a restored `non_approval_count` of 2 or more must resume with the escalated implementation band.

Emit: `STATUS|type=sub-resume|state_file=.run-with-it/sub-<N>-state.json|cycles_used=<n>|non_approval_count=<n>`

## Appendix E: Output Contract

### Log File

At startup, **immediately** create the log file and write a header line:

```bash
mkdir -p "$(dirname "$SUB_COORD_LOG_FILE")"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] sub-coordinator started issue=$SUB_COORD_ISSUE_NUMBER" >> "$SUB_COORD_LOG_FILE"
```

PowerShell:
```powershell
New-Item -ItemType Directory -Force -Path (Split-Path $env:SUB_COORD_LOG_FILE) | Out-Null
Add-Content -Path $env:SUB_COORD_LOG_FILE -Value "[$([datetime]::UtcNow.ToString('o'))] sub-coordinator started issue=$env:SUB_COORD_ISSUE_NUMBER"
```

**Every STATUS, ROUTE, COMPLEXITY, and heartbeat line MUST be written to the log file using an explicit shell command.** Printing to console or mentioning the line in your response text is NOT sufficient — you must run the write command:

```bash
# Use this pattern for every status line:
STATUS_LINE="STATUS|type=example|field=value"
echo "$STATUS_LINE" >> "$SUB_COORD_LOG_FILE"
echo "$STATUS_LINE"  # also print to console
```

PowerShell:
```powershell
$statusLine = "STATUS|type=example|field=value"
Add-Content -Path $env:SUB_COORD_LOG_FILE -Value $statusLine
Write-Host $statusLine
```

Write liberally — every line gives the user visibility. The Main Orchestrator never reads this file into its AI context; it only prints its path.

### Live Status Bus

If `$RUN_WITH_IT_STATUS_FILE` is set, overwrite it with the latest one-line status. If `$RUN_WITH_IT_EVENTS_LOG` is set, append the same line there. These files are for terminal visibility only; never read them into your context.

Bash:
```bash
write_live_status() {
  status_line="$1"
  if [ -n "${RUN_WITH_IT_STATUS_FILE:-}" ]; then
    mkdir -p "$(dirname "$RUN_WITH_IT_STATUS_FILE")"
    printf '%s\n' "$status_line" > "$RUN_WITH_IT_STATUS_FILE"
  fi
  if [ -n "${RUN_WITH_IT_EVENTS_LOG:-}" ]; then
    mkdir -p "$(dirname "$RUN_WITH_IT_EVENTS_LOG")"
    printf '%s\n' "$status_line" >> "$RUN_WITH_IT_EVENTS_LOG"
  fi
}
```

PowerShell:
```powershell
function Write-LiveStatus([string]$statusLine) {
  if ($env:RUN_WITH_IT_STATUS_FILE) {
    New-Item -ItemType Directory -Force -Path (Split-Path $env:RUN_WITH_IT_STATUS_FILE) | Out-Null
    Set-Content -Path $env:RUN_WITH_IT_STATUS_FILE -Value $statusLine
  }
  if ($env:RUN_WITH_IT_EVENTS_LOG) {
    New-Item -ItemType Directory -Force -Path (Split-Path $env:RUN_WITH_IT_EVENTS_LOG) | Out-Null
    Add-Content -Path $env:RUN_WITH_IT_EVENTS_LOG -Value $statusLine
  }
}
```

### Compact Report JSON (MANDATORY)

When the sub-coordinator reaches any terminal state (completed / failed-review / blocked):

1. Populate and write the report JSON:

```json
{
  "schema_version": 1,
  "issue_number": 36,
  "issue_title": "Add login endpoint",
  "outcome": "completed | failed-review | blocked",
  "summary": "One paragraph describing what was done, why it passed/failed, key decisions.",
  "files_modified": [
    { "path": "src/auth/login.ts", "lines_added": 42, "lines_deleted": 7 }
  ],
  "verification": {
    "passed": true,
    "commands_run": ["bun test src/auth/"],
    "evidence": "15 tests passed, 0 failed"
  },
  "review_summary": {
    "cycles_used": 1,
    "final_verdict": "approve",
    "reviewer_model": "claude-sonnet-4-5",
    "non_approval_count": 0,
    "nitpick_only": false
  },
  "token_usage": {
    "impl_input": 0, "impl_output": 0,
    "review_input": 0, "review_output": 0,
    "modify_input": 0, "modify_output": 0,
    "complexity_input": 0, "complexity_output": 0
  },
  "commit_sha": "abc1234",
  "blocking_reasons": []
}
```

2. Write to `$SUB_COORD_REPORT_FILE` (provided in context).
3. If `$SUB_COORD_REPORT_FILE` is missing from context, write to `.run-with-it/reports/sub-<SUB_COORD_ISSUE_NUMBER>-report.json` as fallback.
4. Ensure the JSON is fully written and valid before exiting.

The report file is the sub-coordinator's only required artifact for the Main Orchestrator.

## Appendix F: Status Lines

Emit parseable status messages throughout execution. Every line below — and every `STATUS|type=heartbeat` line read from a worker agent's terminal output — MUST be written to `$SUB_COORD_LOG_FILE` using an explicit shell command. Also append to `$SUB_COORD_LOG_FILE`:

- `ROUTE|agent=<agent>|model=<model>|complexity_level=<level>|complexity_score=<score>|target_weight=<min>-<max>|model_weight=<n>|price_tier=<tier>|fallback_budget=<n>|allowlist=<value>|denylist=<value>|complexity_source=<sub-agent|fallback|override>`
- `STATUS|type=spawn|agent=<name>|issue=#<n>|phase=assigned|scope=<owned-paths>`
- `STATUS|type=heartbeat|issue=<n>|role=<complexity|impl|review|modify>|phase=<exploring|implementing|testing|review>|progress=<short-text>|elapsed=<seconds>`
- `STATUS|type=agent-start|issue=<n>|role=<complexity|impl|review|modify>|agent=<name>|model=<model-id>`
- `STATUS|type=agent-complete|issue=<n>|role=<complexity|impl|review|modify>|agent=<name>|model=<model-id>|status=<success|failed>`
- `STATUS|type=review-spawn|task=<n>|cycle=<n>|agent=<name>|model=<model-id>`
- `STATUS|type=review-result|task=<n>|cycle=<n>|verdict=<approve|revise|reject>|comment_count=<n>`
- `STATUS|type=modify-spawn|task=<n>|cycle=<n>|agent=<name>|model=<model-id>`
- `STATUS|type=review-degraded|task=<n>|reason=no-higher-band-agent`
- `STATUS|type=complexity-skipped|reason=override`
- `STATUS|type=complexity-fallback|reason=<error>|fallback=medium-hard`
- `STATUS|type=compact|action=user-required|state_file=<path>`
- `STATUS|type=ledger|task=<task-id>|role=<impl|review|modify>|cycle=<n>|agent=<name>|model=<model-id>|added=<n>|deleted=<n>|total=<n>|reason=<short-selection-reason>|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>|telemetry_source=<source>`

Keep `progress` values under 8 words.

## Guardrails

- Keep changes minimal and focused to the assigned issue.
- **Never pause after routing to ask the user how to proceed.** Execute via the runner immediately.
- **Never offer to implement work directly in this session.**
- **Never present execution option menus.**
- **Never run tests, build commands, or compile the project** in this session.
