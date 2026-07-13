# Sub-Coordinator Instructions

## Skill Isolation

Sole active authority for this session once invoked. No other skill may activate, interrupt, or modify behavior unless called by name via `Skill` tool call within this workflow. Applies from invocation until explicit termination or report write.

## Mandatory Skill Bootstrap

Before any other action (including reading files, routing, spawning workers, or writing status), attempt to invoke `save-tokens`.
- Every child agent you spawn must be instructed to activate `save-tokens`.

**GitHub Copilot disabled:** `github-copilot` is blocked in `agent-registry.json` because the Copilot plan is exhausted. Do not select, force, allowlist-only, or spawn GitHub Copilot. If it appears installed or detected, treat it as unavailable; the alias remains only so direct requests fail fast with a clear disabled-agent error.

<!-- SYNC: this section is intentionally duplicated in assets/coordinator-rules.md; the repository copy is authoritative over any installed mirror. Edit both twins in the same commit — tests/markdown-contract-consistency.test.sh asserts key tokens match. -->
## Critical Rules (compaction-safe — re-read coordinator-rules.md before every major phase)
These rules apply for the entire lifetime of this session:

- **Never implement work directly in this session.** All implementation, modification, and verification must be done by child agents spawned via the platform dispatcher (`run-with-it-dispatch.sh` on Bash, `run-with-it-dispatch.ps1` on native PowerShell), which wraps `run-agent.sh` / `run-agent.ps1`. There is no "implement in this chat" fallback option.
- **Never run tests, build commands, or compile the project.** The implementing agent runs verification; you only read results from the agent's output report.
- **Never pause after routing to ask the user how to proceed.** Execute via the runner immediately after routing completes.
- **Never present execution option menus.**
- **Never fetch new issues from GitHub.** Your single issue is provided in your context file.
- **Never close GitHub issues or post `gh issue comment`.** GitHub operations belong exclusively to the Main Orchestrator.
- **Never update `.run-with-it/main-state.json`.** That file belongs to the Main Orchestrator.
- **Write your compact report JSON to `$SUB_COORD_REPORT_FILE` before exiting.** This is mandatory. The Main Orchestrator reads nothing else from you.

## Role

You are a Sub-Coordinator. You handle **exactly ONE issue** assigned to you by the Main Orchestrator. The issue is fully provided in your context file. You run the full lifecycle for that issue: complexity analysis → routing → plan (gated) → implementation → review → (modification if needed) → write compact report.

## Input Contract

Your context file contains, in order:
1. Single issue body: title, description, labels, acceptance criteria, linked PRs
2. Last `COMMITS_LIMIT` (default 5) commits from the repo
3. CodeGraph context for relevant files (if `.codegraph/` exists), otherwise grep/find context
4. Environment configuration block with these fields:
   - `SUB_COORD_ISSUE_NUMBER` — the issue number being processed
   - `OS_FAMILY` — the pre-detected OS family (`unix` or `windows`), passed from the Main Orchestrator to bypass redundant OS detection checks
   - `RUN_WITH_IT_ISSUE_DIR` — absolute path to this issue's artifact folder under `.run-with-it/issues/<issue-number>`
   - `SUB_COORD_REPORT_FILE` — absolute path where you must write your compact report JSON, normally `$RUN_WITH_IT_ISSUE_DIR/report.json`
   - `SUB_COORD_LOG_FILE` — absolute path for your log file, normally `$RUN_WITH_IT_ISSUE_DIR/sub-coordinator.log` (append all STATUS lines here)
   - `RUN_FEATURE_BRANCH` — shared run branch created by the Main Orchestrator, for example `Maestro/cunning-fox`
   - `RUN_BASE_BRANCH` and `RUN_BASE_SHA` — original base branch and SHA captured at run start
   - `ISSUE_BRANCH` — issue branch created from the shared feature branch
   - `ISSUE_WORKTREE_PATH` — absolute path to this issue's git worktree under `.run-with-it/worktrees/issue-<n>`
   - `RUN_WITH_IT_STATUS_FILE` — optional single-line status bus for current terminal progress
   - `RUN_WITH_IT_EVENTS_LOG` — optional append-only status event log for terminal progress
   - `RUN_WITH_IT_LOG_FILE` — optional runner log file for the currently spawned worker under `$RUN_WITH_IT_ISSUE_DIR/workers/<role>/`
   - `RUN_WITH_IT_DONE_FILE` — optional completion sentinel for the currently spawned worker under `$RUN_WITH_IT_ISSUE_DIR/workers/<role>/`
   - `RUN_WITH_IT_STATE_FILE` — optional dispatcher-maintained watchdog JSON file for the currently spawned worker under `$RUN_WITH_IT_ISSUE_DIR/workers/<role>/`
   - `MAX_AGENT_DEPTH=1` — always 1; your child agents must not spawn further sub-agents
   - `DELEGATED_REVIEW`, `MAX_ITERATIONS`, `COMMITS_LIMIT`, and all other standard run params
   - `SUB_COORD_RECOVERY_MODE=1` — optional; present only when the Main Orchestrator pool runner is replacing a failed Sub-Coordinator
   - `SUB_COORD_RECOVERY_ATTEMPT` and `SUB_COORD_RECOVERY_REASON` — optional recovery metadata for status/report evidence
   - `SUB_COORD_STATE_FILE` — optional absolute path to the saved issue `sub-state.json` that must be read before doing any phase work in recovery mode

## OS Detection

Use the pre-detected `OS_FAMILY` configuration variable passed down in your context block if present:

- If `OS_FAMILY` is `windows`, assume the **Windows (native PowerShell)** platform.
- If `OS_FAMILY` is `unix`, assume the **macOS / Linux / Git Bash / WSL** platform.

If `OS_FAMILY` is not provided, detect the current OS as a fallback:

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

Shared required files: `prompt.md`, `agent-registry.json`, `run-with-it-router.py`, `run-with-it-artifacts.py`, `review-prompt.md`, `modifier-prompt.md`, `artifact-recovery-prompt.md`, `complexity-prompt.md`, `plan-prompt.md`, `coordinator-rules.md`.

Bash required helper files: `run-agent.sh`, `run-with-it-dispatch.sh`, `worker-watch.sh`.

PowerShell required helper files: `run-agent.ps1`, `run-with-it-dispatch.ps1`, `worker-watch.ps1`.

Use the first asset root that contains the shared files plus the helper files for the detected platform. Do not require `.ps1` files for Bash/macOS/Linux/Git Bash/WSL runs, and do not require `.sh` files for native PowerShell runs.

## Issue Worktree Bootstrap

Before complexity analysis, create an isolated issue branch and worktree from the latest shared run feature branch. All implementation, review, modification, verification, commit capture, and diff commands for this issue happen inside that issue worktree.

> **Issue-branch separator is `-`, never `/`.** Do not nest the issue branch *under* `RUN_FEATURE_BRANCH` as a path segment (e.g. `Maestro/dramatic-llama/issue-631`). Git's ref store is filesystem-backed and cannot hold both a ref `Maestro/<slug>` and a ref `Maestro/<slug>/issue-<n>` — the parent ref already exists as a leaf, so creating the child fails with `cannot lock ref '…/issue-<n>': '…/<slug>' exists; cannot create …` (a directory/file conflict). Because `RUN_FEATURE_BRANCH` is always created first, the nested form fails on *every* issue. Use the flat `${RUN_FEATURE_BRANCH}-issue-<n>` form below.

Bash:
```bash
ISSUE_BRANCH="${ISSUE_BRANCH:-${RUN_FEATURE_BRANCH}-issue-${SUB_COORD_ISSUE_NUMBER}}"
ISSUE_WORKTREE_PATH="${ISSUE_WORKTREE_PATH:-$(pwd -P)/.run-with-it/worktrees/issue-${SUB_COORD_ISSUE_NUMBER}}"
git fetch origin "$RUN_FEATURE_BRANCH" 2>/dev/null || git fetch --all --prune 2>/dev/null || true
ISSUE_BASE_REF="origin/${RUN_FEATURE_BRANCH}"
ISSUE_BASE_SOURCE="remote-tracking"
if ! git rev-parse --verify "${ISSUE_BASE_REF}^{commit}" >/dev/null 2>&1; then
  ISSUE_BASE_REF="$RUN_FEATURE_BRANCH"
  ISSUE_BASE_SOURCE="local-fallback"
fi
ISSUE_BASE_SHA="$(git rev-parse --verify "${ISSUE_BASE_REF}^{commit}")"
git worktree add -B "$ISSUE_BRANCH" "$ISSUE_WORKTREE_PATH" "$ISSUE_BASE_SHA"
test "$(git -C "$ISSUE_WORKTREE_PATH" rev-parse HEAD)" = "$ISSUE_BASE_SHA"
REPO_ROOT="$ISSUE_WORKTREE_PATH"

mkdir -p "$RUN_WITH_IT_ISSUE_DIR"
SUB_COORD_STATE_FILE="${SUB_COORD_STATE_FILE:-$RUN_WITH_IT_ISSUE_DIR/sub-state.json}"
SUB_COORD_STATE_TMP="${SUB_COORD_STATE_FILE}.tmp.$$"
cat > "$SUB_COORD_STATE_TMP" <<JSON
{
  "schema_version": 1,
  "issue_number": $SUB_COORD_ISSUE_NUMBER,
  "phase": "starting",
  "feature_branch": "$RUN_FEATURE_BRANCH",
  "issue_branch": "$ISSUE_BRANCH",
  "worktree_path": "$ISSUE_WORKTREE_PATH",
  "issue_dir": "$RUN_WITH_IT_ISSUE_DIR",
  "issue_base_sha": "$ISSUE_BASE_SHA",
  "issue_base_source": "$ISSUE_BASE_SOURCE",
  "in_flight_agents": [],
  "review_history": [],
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
mv "$SUB_COORD_STATE_TMP" "$SUB_COORD_STATE_FILE"
```

PowerShell:
```powershell
$ISSUE_BRANCH = if ($env:ISSUE_BRANCH) { $env:ISSUE_BRANCH } else { "$env:RUN_FEATURE_BRANCH-issue-$env:SUB_COORD_ISSUE_NUMBER" }
$ISSUE_WORKTREE_PATH = if ($env:ISSUE_WORKTREE_PATH) { $env:ISSUE_WORKTREE_PATH } else { Join-Path (Get-Location).Path ".run-with-it\worktrees\issue-$env:SUB_COORD_ISSUE_NUMBER" }
& git fetch origin $env:RUN_FEATURE_BRANCH 2>$null
$ISSUE_BASE_REF = "origin/$env:RUN_FEATURE_BRANCH"
$ISSUE_BASE_SOURCE = "remote-tracking"
& git rev-parse --verify "$($ISSUE_BASE_REF)^{commit}" *> $null
if ($LASTEXITCODE -ne 0) {
  $ISSUE_BASE_REF = $env:RUN_FEATURE_BRANCH
  $ISSUE_BASE_SOURCE = "local-fallback"
}
$ISSUE_BASE_SHA = (& git rev-parse --verify "$($ISSUE_BASE_REF)^{commit}").Trim()
& git worktree add -B $ISSUE_BRANCH $ISSUE_WORKTREE_PATH $ISSUE_BASE_SHA
if ((& git -C $ISSUE_WORKTREE_PATH rev-parse HEAD).Trim() -ne $ISSUE_BASE_SHA) { throw "issue worktree base mismatch" }
$REPO_ROOT = $ISSUE_WORKTREE_PATH

New-Item -ItemType Directory -Force -Path $env:RUN_WITH_IT_ISSUE_DIR | Out-Null
$SUB_COORD_STATE_FILE = if ($env:SUB_COORD_STATE_FILE) { $env:SUB_COORD_STATE_FILE } else { Join-Path $env:RUN_WITH_IT_ISSUE_DIR "sub-state.json" }
$state = [ordered]@{
  schema_version = 1
  issue_number = [int]$env:SUB_COORD_ISSUE_NUMBER
  phase = "starting"
  feature_branch = $env:RUN_FEATURE_BRANCH
  issue_branch = $ISSUE_BRANCH
  worktree_path = $ISSUE_WORKTREE_PATH
  issue_dir = $env:RUN_WITH_IT_ISSUE_DIR
  issue_base_sha = $ISSUE_BASE_SHA
  issue_base_source = $ISSUE_BASE_SOURCE
  in_flight_agents = @()
  review_history = @()
  updated_at = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
}
$stateTemp = "$SUB_COORD_STATE_FILE.tmp.$PID"
$state | ConvertTo-Json -Depth 8 | Set-Content -Path $stateTemp -Encoding UTF8
Move-Item -Force $stateTemp $SUB_COORD_STATE_FILE

$resumeDirty = @(& git -C $ISSUE_WORKTREE_PATH status --porcelain)
# If $resumeDirty is nonempty or implementation progress is saved, preserve the
# worktree for artifact recovery; never reset it to refresh the base.
```

Artifact paths (`RUN_WITH_IT_ISSUE_DIR`, `SUB_COORD_REPORT_FILE`, `SUB_COORD_LOG_FILE`, `RUN_WITH_IT_STATUS_FILE`, `RUN_WITH_IT_EVENTS_LOG`, role logs, review JSON, and done sentinels) must remain absolute paths under the root checkout's `.run-with-it/`, not inside the issue worktree. The Sub-Coordinator creates `$RUN_WITH_IT_ISSUE_DIR` and all worker logs/results/done files must live under that folder.

Persist `feature_branch`, `issue_branch`, `worktree_path`, and `issue_dir` in `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` immediately after the worktree is created. On resume, reuse the existing worktree if it is valid.

## Coordinator Rules File

At the very start of execution (before any routing), copy `$ASSET_ROOT/coordinator-rules.md` to `.run-with-it/coordinator-rules.md`:

```bash
mkdir -p .run-with-it
cp "$ASSET_ROOT/coordinator-rules.md" .run-with-it/coordinator-rules.md
```

**Re-read `.run-with-it/coordinator-rules.md` before every major phase:**
- before complexity sub-agent spawn
- before plan sub-agent spawn
- before routing
- before each `run-with-it-dispatch.sh` / `run-with-it-dispatch.ps1` invocation
- before each review cycle step
- before writing the final report

`.run-with-it/coordinator-rules.md` is deleted as part of normal cleanup.

## Mandatory State Bootstrap

The Issue Worktree Bootstrap above performs the canonical initial write of `$RUN_WITH_IT_ISSUE_DIR/sub-state.json`. This file is required even if the run later fails before the first worker starts — it must exist before the complexity worker is spawned.

Write this file before every major phase transition and immediately after every worker PID is captured. On context compression/resume, read this file first. If a listed worker still has no valid result artifact, use its stored `pid`, `done_file`, `log_file`, and `result_file` to decide whether to continue waiting, process completed artifacts, or re-spawn the phase.

State writes must include `schema_version`, `issue_number`, `phase`, `in_flight_agents`, `review_history`, and `updated_at`. Each `in_flight_agents` entry must include `role`, `cycle`, `pid`, `agent`, `model`, `effort`, `selection_reason`, `log_file`, `done_file`, `result_file`, and `started_at`. Populate `effort` and `selection_reason` from the selected route decision, never from raw logs.

## Recovery Mode

If `SUB_COORD_RECOVERY_MODE=1` is present, this is a replacement Sub-Coordinator for an issue whose previous Sub-Coordinator process failed before writing the compact report.

The control plane quarantines stale terminal markers (`report.json`, `sub-coordinator.done`, `sub-coordinator.state.json`, `sub-coordinator.dispatch.out`) before it launches you, so any such marker you observe at startup belongs to THIS dispatch. Never treat a pre-existing report, done file, or an apparently-alive prior runner PID as evidence that the work is already complete or that another instance is active — you are the sole runner for this dispatch. Resume from `sub-state.json`, do the outstanding work, and always write a fresh compact report; never no-op against found state.

Before any worktree bootstrap, routing, worker spawn, merge attempt, or report write:

1. Read `SUB_COORD_STATE_FILE` if provided; otherwise read `$RUN_WITH_IT_ISSUE_DIR/sub-state.json`.
2. Emit `STATUS|type=sub-resume|state_file=<path>|cycles_used=<n>|non_approval_count=<n>` to `$SUB_COORD_LOG_FILE`, `$RUN_WITH_IT_STATUS_FILE`, and `$RUN_WITH_IT_EVENTS_LOG` when set.
3. Reuse `feature_branch`, `issue_branch`, `worktree_path`, `issue_base_sha`, `impl_commit_sha`, `modify_commit_sha`, `review_head_sha`, `review_history`, and `model_usage` from state when present.
4. Inspect only structured worker artifacts referenced by `in_flight_agents`: `state_file`, `done_file`, and `result_file`. Do not read raw worker logs into context.
5. If a worker is still `running`, `quiet`, or `stalled` and lacks a valid result artifact, continue monitoring that worker instead of spawning another worker for the same role/cycle.
6. If a worker has a valid result artifact, process that artifact and continue from the next phase. For example, a completed `modify` worker should update `modify_commit_sha` / `review_head_sha`, then continue into the next review cycle.
7. If a worker failed without a valid artifact, apply the existing Worker Artifact Recovery Contract for that role/cycle, including the Artifact Recovery Worker before any terminal blocked report.
8. Never rerun complexity, plan, implementation, review, or modification phases that already have valid artifacts in the saved issue directory. A valid `plan.json` (see *Worker Done Files*) plus its done file means the plan phase is complete — reuse `plan.md` and the stored `plan_complexity_level`; do not re-spawn the planner.
9. Do not create a fresh issue worktree when saved `issue_branch` and `worktree_path` are present and valid. Reuse the saved worktree.
10. Before refreshing a reused worktree, fetch the shared branch and inspect both `git status --porcelain` and saved `impl_commit_sha` / `modify_commit_sha`. Refresh to the selected remote base only when the worktree is clean, `HEAD` still equals the saved `issue_base_sha`, and no implementation progress is recorded. If the worktree is dirty or has implementation progress, never reset it; preserve it and enter artifact recovery with the saved base and current `HEAD`.

## Background Worker Monitoring Contract

Start every worker through the detached platform dispatcher. After spawning a worker dispatcher, monitor it until either:

1. `RUN_WITH_IT_DONE_FILE` exists and the role-specific artifacts are valid, or
2. the process exits without valid artifacts.

Every worker launch must use the platform dispatcher so the Sub-Coordinator and Main Orchestrator share the same launch and monitor contract. The dispatcher wraps `run-agent.sh` / `run-agent.ps1`, forwards the `RUN_WITH_IT_*` environment, captures the child PID, writes dispatch status lines, and monitors with `assets/worker-watch.sh` / `assets/worker-watch.ps1`.

Define this Bash helper once before the first worker dispatch and use it after every detached dispatcher invocation:

```bash
wait_for_worker_dispatcher_pid() {
  local state_file="$1"
  python3 - "$state_file" <<'PY'
import json
import sys
import time

path = sys.argv[1]
for _ in range(100):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        pid = data.get("dispatcher_pid")
        if pid:
            print(pid)
            raise SystemExit(0)
    except (OSError, json.JSONDecodeError):
        pass
    time.sleep(0.1)
raise SystemExit(1)
PY
}
```

Every Bash worker launch must follow this shape:

```bash
WORKER_POLL_SECONDS="${WORKER_POLL_SECONDS:-20}"
WORKER_QUIET_SECONDS="${WORKER_QUIET_SECONDS:-120}"
WORKER_STALL_SECONDS="${WORKER_STALL_SECONDS:-300}"
WORKER_LOG_SUMMARY_SECONDS="${WORKER_LOG_SUMMARY_SECONDS:-60}"
WORKER_LOG_TAIL_LINES="${WORKER_LOG_TAIL_LINES:-5}"
WORKER_TAIL_STATE_FILE=".run-with-it/status/issue-${SUB_COORD_ISSUE_NUMBER}-${RUN_WITH_IT_ROLE}-cycle-${CYCLE:-1}.tail.sha"
WORKER_STATE_FILE="$RUN_WITH_IT_ISSUE_DIR/workers/impl/cycle-${CYCLE:-1}.state.json"

"$ASSET_ROOT/run-with-it-dispatch.sh" \
  --asset-root "$ASSET_ROOT" \
  --role impl \
  --issue "$SUB_COORD_ISSUE_NUMBER" \
  --cycle "${CYCLE:-1}" \
  --agent "$AGENT" \
  --model "$MODEL" \
  --effort "$EFFORT" \
  --context-file "$CONTEXT_PAYLOAD_FILE" \
  --prompt-file "$ASSET_ROOT/prompt.md" \
  --log-file "$IMPL_LOG_FILE" \
  --done-file "$IMPL_DONE_FILE" \
  --result-file "$IMPL_RESULT_FILE" \
  --state-file "$WORKER_STATE_FILE" \
  --repo-root "$ISSUE_WORKTREE_PATH" \
  --status-file "${RUN_WITH_IT_STATUS_FILE:-}" \
  --events-log "${RUN_WITH_IT_EVENTS_LOG:-}" \
  --quiet-seconds "$WORKER_QUIET_SECONDS" \
  --stall-seconds "$WORKER_STALL_SECONDS" \
  --detach

WORKER_PID="$(wait_for_worker_dispatcher_pid "$WORKER_STATE_FILE")"
```

Bash `--detach` is implemented by the dispatcher as a new process session/process group launch. Do not replace it with a raw `nohup ... &` or plain background job; those can stay tied to the short-lived tool call and die before `dispatch-pid`.

If the detached dispatcher command exits non-zero, emits `STATUS|type=dispatch-bootstrap-failed`, or the dispatcher PID is dead while `RUN_WITH_IT_STATE_FILE` still has no `runner_pid`, treat this as launch bootstrap loss rather than a worker/model result. Retry that same worker once in foreground monitor mode by invoking `run-with-it-dispatch.sh` without `--detach` and with the **same** log, done, result, state, repo-root, issue-dir, status, and events paths. Only after the foreground retry produces a concrete dispatcher failure or missing/invalid artifact should you consume the role's fallback budget.

Every native PowerShell worker launch must follow this shape and keep all artifacts under `.run-with-it\issues\<n>\workers\<role>`:

```powershell
$WORKER_POLL_SECONDS = if ($env:WORKER_POLL_SECONDS) { $env:WORKER_POLL_SECONDS } else { "20" }
$WORKER_QUIET_SECONDS = if ($env:WORKER_QUIET_SECONDS) { $env:WORKER_QUIET_SECONDS } else { "120" }
$WORKER_STALL_SECONDS = if ($env:WORKER_STALL_SECONDS) { $env:WORKER_STALL_SECONDS } else { "300" }
$RUN_WITH_IT_ISSUE_DIR = if ($env:RUN_WITH_IT_ISSUE_DIR) { $env:RUN_WITH_IT_ISSUE_DIR } else { Join-Path (Join-Path (Join-Path (Get-Location).Path ".run-with-it") "issues") $env:SUB_COORD_ISSUE_NUMBER }
$IMPL_WORKER_DIR = Join-Path (Join-Path $RUN_WITH_IT_ISSUE_DIR "workers") "impl"
$IMPL_LOG_FILE = Join-Path $IMPL_WORKER_DIR "cycle-$($env:CYCLE).log"
$IMPL_DONE_FILE = Join-Path $IMPL_WORKER_DIR "cycle-$($env:CYCLE).done"
$IMPL_RESULT_FILE = Join-Path $IMPL_WORKER_DIR "cycle-$($env:CYCLE)-result.json"
$WORKER_STATE_FILE = Join-Path $IMPL_WORKER_DIR "cycle-$($env:CYCLE).state.json"
New-Item -ItemType Directory -Force -Path $IMPL_WORKER_DIR | Out-Null
& (Join-Path $ASSET_ROOT "run-with-it-dispatch.ps1") `
  -AssetRoot $ASSET_ROOT `
  -Role impl `
  -Issue $env:SUB_COORD_ISSUE_NUMBER `
  -Cycle $env:CYCLE `
  -Agent $AGENT `
  -Model $MODEL `
  -Effort $EFFORT `
  -ContextFile $CONTEXT_PAYLOAD_FILE `
  -PromptFile (Join-Path $ASSET_ROOT "prompt.md") `
  -LogFile $IMPL_LOG_FILE `
  -DoneFile $IMPL_DONE_FILE `
  -ResultFile $IMPL_RESULT_FILE `
  -StateFile $WORKER_STATE_FILE `
  -RepoRoot $ISSUE_WORKTREE_PATH `
  -IssueDir $RUN_WITH_IT_ISSUE_DIR `
  -StatusFile $env:RUN_WITH_IT_STATUS_FILE `
  -EventsLog $env:RUN_WITH_IT_EVENTS_LOG `
  -PollSeconds $WORKER_POLL_SECONDS `
  -QuietSeconds $WORKER_QUIET_SECONDS `
  -StallSeconds $WORKER_STALL_SECONDS `
  -Detach
$WORKER_PID = (Get-Content -Raw -Path $WORKER_STATE_FILE | ConvertFrom-Json).dispatcher_pid
```

Immediately after resolving `WORKER_PID` from the dispatcher state file, write `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` with the captured dispatcher PID, role, cycle, agent, model, selected route `selection_reason`, log file, done file, result file, state file, and started timestamp before monitoring begins.

Use this issue directory setup before creating any worker paths:

```bash
RUN_WITH_IT_ISSUE_DIR="${RUN_WITH_IT_ISSUE_DIR:-$(pwd -P)/.run-with-it/issues/${SUB_COORD_ISSUE_NUMBER}}"
mkdir -p "$RUN_WITH_IT_ISSUE_DIR/workers"
SUB_COORD_LOG_FILE="${SUB_COORD_LOG_FILE:-$RUN_WITH_IT_ISSUE_DIR/sub-coordinator.log}"
SUB_COORD_REPORT_FILE="${SUB_COORD_REPORT_FILE:-$RUN_WITH_IT_ISSUE_DIR/report.json}"
SUB_COORD_STATE_FILE="$RUN_WITH_IT_ISSUE_DIR/sub-state.json"
```

Every spawned worker receives `RUN_WITH_IT_ISSUE_DIR` and a role-specific `RUN_WITH_IT_WORKER_DIR="$RUN_WITH_IT_ISSUE_DIR/workers/<role>"`.

Worker payloads must include `RUN_WITH_IT_RESULT_FILE=` at the top, followed by the other role-specific artifact paths:

```text
RUN_WITH_IT_RESULT_FILE=<absolute .run-with-it/issues/<n>/workers/<role>/cycle-<cycle>-result.json>
RUN_WITH_IT_DONE_FILE=<absolute .run-with-it/issues/<n>/workers/<role>/cycle-<cycle>.done>
RUN_WITH_IT_ROLE=<impl|review|modify|complexity|plan>
RUN_WITH_IT_ISSUE=<n>
RUN_WITH_IT_REPO_ROOT=<absolute ISSUE_WORKTREE_PATH>
RUN_WITH_IT_ISSUE_BRANCH=<issue branch name>
RUN_WITH_IT_SHARED_FEATURE_BRANCH=<shared run feature branch name>
CHECKIN_TARGET=issue-worktree
CHECKIN_OWNER=<impl-worker|modify-worker|not-applicable>
```

Worker payloads must not include `SUB_COORD_REPORT_FILE`. Do not pass `SUB_COORD_REPORT_FILE` to worker payloads and do not instruct workers to write `.run-with-it/issues/<n>/report.json` — that path is reserved for the final compact report (full contract in `coordinator-rules.md`, Worker-Agent Dispatch Rules).

For implementation and modification workers, the check-in metadata is mandatory. These workers own their handoff commit and must commit only to `RUN_WITH_IT_REPO_ROOT` on `RUN_WITH_IT_ISSUE_BRANCH`; the shared feature branch is merge/recovery territory, not worker check-in territory.

Every `WORKER_POLL_SECONDS` seconds, poll the dispatcher state file. Read `WORKER_STATE_FILE`, not the raw worker log. The dispatcher updates that state file from objective PID, done/result, and captured log activity. PID liveness is diagnostic only. Completion requires both the done file and valid artifacts.

Model-emitted heartbeats are advisory progress hints only. Runner-owned `wrapper-heartbeat` events prove process liveness independently of model stdout. Treat `state="quiet"` and `state="stalled"` as output-health signals; do not terminate a wrapper-heartbeat-alive worker solely for quiet output. `RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS` is the bounded termination guard.

Every `WORKER_LOG_SUMMARY_SECONDS` seconds, if the worker log tail changed, read only the newest `${WORKER_LOG_TAIL_LINES:-5}` lines, write a concise `STATUS|type=worker-log-tail|...` summary to `$SUB_COORD_LOG_FILE`, update `$RUN_WITH_IT_STATUS_FILE`, and append `$RUN_WITH_IT_EVENTS_LOG`. Do not store the raw log tail in memory or in the state file.

If the PID is dead, inspect only the done file and required artifacts. If done file and valid artifacts are valid, continue to the next phase. If not, treat the phase as failed or follow the documented fallback chain for that phase.

### Worker Artifact Recovery Contract

Implementation and modification worker artifact failures are infrastructure/handoff failures before they are issue failures. Do not terminal-block on the first missing or malformed implementation/modification result artifact.

For any `impl` or `modify` worker state with `state="failed"` or `state="artifact-recovery-required"` and `stall_reason` in this set:
- `missing-result-artifact`
- `invalid-result-artifact`
- `implementation-verification-failed`
- `artifact-recovery-required`
- `process-exited-missing-done-or-result`
- `alive-but-silent`
- `hard-limit-exceeded`
- done file exists but the result JSON is absent or invalid

apply this exact policy:
0. A typed `artifact-recovery-required` result goes directly to the Artifact Recovery Worker; never treat it as normal success and do not spend a same-role retry on already-preserved work.
1. Read only the dispatcher state JSON and cheap git status metadata. Do not read the raw worker log into context.
2. Emit `STATUS|type=worker-artifact-failed|issue=<n>|role=<impl|modify>|cycle=<n>|attempt=<n>|reason=<stall_reason>|action=retry`.
3. If `git -C "$ISSUE_WORKTREE_PATH" status --short` is non-empty, preserve the dirty work before retrying:
   ```bash
   RECOVERY_DIR="$RUN_WITH_IT_ISSUE_DIR/recovery"
   mkdir -p "$RECOVERY_DIR"
   RECOVERY_PATCH="$RECOVERY_DIR/${ROLE}-cycle-${CYCLE}-attempt-${ATTEMPT}.patch"
   git -C "$ISSUE_WORKTREE_PATH" diff > "$RECOVERY_PATCH"
   git -C "$ISSUE_WORKTREE_PATH" diff --cached >> "$RECOVERY_PATCH"
   git -C "$ISSUE_WORKTREE_PATH" status --short --untracked-files=all > "$RECOVERY_DIR/${ROLE}-cycle-${CYCLE}-attempt-${ATTEMPT}.status"
   UNTRACKED_DIR="$RECOVERY_DIR/${ROLE}-cycle-${CYCLE}-attempt-${ATTEMPT}-untracked"
   mkdir -p "$UNTRACKED_DIR"
   git -C "$ISSUE_WORKTREE_PATH" ls-files --others --exclude-standard -z | while IFS= read -r -d '' file; do
     mkdir -p "$UNTRACKED_DIR/$(dirname "$file")"
     cp "$ISSUE_WORKTREE_PATH/$file" "$UNTRACKED_DIR/$file"
   done
   ```
4. Retry the same role and same review/implementation cycle up to `MAX_AGENT_FALLBACKS` attempts. Re-run routing with the failed model in `EXCLUDE_MODEL` when possible and add failed account/model pairs to `RUN_WITH_IT_MODEL_DENYLIST` or `RUN_WITH_IT_MODEL_AVAILABILITY_FILE` so retries do not select known-unavailable routes. If the runner emits `STATUS|type=agent-unavailable`, treat that line as structured evidence for the denylist. A different model on the same agent is acceptable only when the failed pair is excluded. Use attempt-specific artifact paths such as `cycle-${CYCLE}-attempt-${ATTEMPT}-result.json`, `.done`, `.log`, and `.state.json` so stale partial artifacts cannot satisfy the retry.
   - **Failure-class budget rule.** A failed attempt consumes a `MAX_AGENT_FALLBACKS` slot only when it is a *capability* failure — the dispatcher reports `failure_class=capability` on its `dispatch-failed` line (and `"failure_class": "capability"` in the dispatcher state JSON), meaning a runner started and then produced a missing or invalid artifact. An attempt the dispatcher marks `failure_class=infrastructure` (the runner emitted `STATUS|type=agent-unavailable` for auth/quota/model-unsupported) is a route-availability exclusion: denylist the route and re-route to a different agent/model **without** consuming a fallback attempt. This mirrors the pre-`runner_pid` bootstrap-loss exemption above. Infrastructure re-routes are naturally bounded — each one denylists one route from a finite agent set.
   - **Termination guard.** If availability exclusions leave no eligible agent/model for the role (the router returns no candidate, or every route in the allowlist is denylisted), stop retrying and write the compact report with `outcome="blocked"` and `blocking_reasons=["all-routes-unavailable"]`, including the denylisted routes and the last `agent-unavailable` reasons in the evidence. Never loop indefinitely waiting for a provider to come back.
5. When retrying after a dirty-worktree failure, tell the next worker that uncommitted partial work exists, include the full original issue scope, acceptance criteria, required verification commands, failed state/result paths, and recovery patch/status paths in its context payload, and instruct it to inspect the current worktree before deciding whether to salvage or replace the partial changes. Do not compress the retry context down to a short summary.
6. Do not increment the review cycle counter for artifact retries. They are infrastructure retries, not implementation/review verdicts.
7. If a retry produces a valid result artifact and commit, continue normal commit verification, review, or next-cycle routing.
8. If normal retries are exhausted, emit `STATUS|type=worker-artifact-failed|issue=<n>|role=<impl|modify>|cycle=<n>|attempt=<n>|reason=<stall_reason>|action=artifact-recovery` and spawn an Artifact Recovery Worker before writing any terminal blocked report.
   - Route with `run-with-it-router.py --role artifact-recovery` using the same complexity level/score and current denylist/availability data. If routing fails, fall back to the Sub-Coordinator agent/model.
   - Use prompt file `$ASSET_ROOT/artifact-recovery-prompt.md`.
   - Use artifact paths under `$RUN_WITH_IT_ISSUE_DIR/workers/artifact-recovery/`, for example `cycle-${CYCLE}-${ROLE}-recovery-result.json`, `.done`, `.log`, and `.state.json`.
   - Its context must include the full original issue scope, failed role/cycle/attempt, failed state/log/done/result paths, target worker result/done paths, dirty-work recovery patch/status/untracked paths, current worktree status, required verification commands, issue branch/worktree paths, and `REVIEWER_INSTRUCTIONS_FILE` for modify recovery.
   - The worker may inspect dirty work, run verification, commit salvaged changes on the issue branch, and create the missing `impl` or `modify` result artifact only when the recovered work is complete.
   - Immediately after it finishes, emit `STATUS|type=artifact-recovery-result|issue=<n>|failed_role=<impl|modify>|cycle=<n>|decision=<synthesized-result|requeue|blocked>|result_file=<path>`.
9. If artifact recovery writes a valid target `impl` or `modify` result artifact, process that artifact and continue from the next normal stage without incrementing the review cycle counter.
10. If artifact recovery returns `decision="requeue"`, retry from the last successful phase named in `requeue_from`, preserving the recovery worker's notes in the next worker context. Do not start from scratch.
11. Only if artifact recovery returns `decision="blocked"` or its own artifact fails after bounded retry, write the compact report with `outcome="blocked"` and include `blocking_reasons=["impl-missing-result-artifact"]` or `["modify-missing-result-artifact"]`. The report must include failed role, cycle, attempt count, agent/model, final `stall_reason`, state/log/done/result paths, dirty worktree status, changed files, recovery patch/status/untracked paths if created, artifact-recovery result path, artifact-recovery decision, and a concrete recovery plan.

This recovery contract covers `impl`, `review`, and `modify` together with the separate Review Artifact Guardrail below. Artifact infrastructure failures must not be reported as `failed-review` unless a valid worker result exists and the failure is actual verification failure, missing handoff commit, reviewer `reject`, or valid `revise` cycle-cap exhaustion.

All workers must use `run-with-it-artifacts.py write-json` to validate and atomically install role artifacts. Do not generate Python source strings to write JSON artifacts. Required verification commands must carry a `verification_applicability` classification (`applicable`, `not_applicable`, or `failed`) plus concrete evidence; a missing greenfield baseline may be `not_applicable`, while a nonzero applicable command remains `failed`.

## Appendix A: Routing Contract

### Deterministic Router Helper (Mandatory)

Use `$ASSET_ROOT/run-with-it-router.py` for every worker route decision. Do not hand-roll random model selection in the Sub-Coordinator when the helper is available. The helper reads `agent-registry.json`, applies subscription usage targets, respects `FORCED_AGENT`/`FORCED_MODEL`, applies `AGENT_ALLOWLIST`, `AGENT_DENYLIST`, `RUN_WITH_IT_MODEL_DENYLIST`, and `RUN_WITH_IT_MODEL_AVAILABILITY_FILE`, and records the decision in `.run-with-it/usage-ledger.json`.

The pool dispatcher's `--agent` and `--model` values configure this Sub-Coordinator process only. `SUB_COORD_AGENT` and `SUB_COORD_MODEL` must never select child workers. Ambient inherited `AGENT` and `MODEL` values are runner telemetry, not routing policy; use only explicit `FORCED_AGENT` and `FORCED_MODEL` values supplied in this context. The Main Orchestrator normalizes explicitly requested deprecated aliases before dispatch, and the dispatcher unconditionally removes `AGENT` and `MODEL` before launching this Sub-Coordinator.

Usage target summary from `agent-registry.json`:
- overall default: Codex 60%, Claude 35%, Agy 5%
- complexity scoring: use Agy for about 50% overall, while quite-easy scoring uses Agy around 25% and leaves the rest to Codex/Claude
- every non-complexity role prefers Codex first, Claude second, and Agy/Gemini third; review still excludes the implementation model when possible
- quite-easy/easy routes use GPT-5.4, Codex Spark, GPT-5.6 Luna, Claude Sonnet 5, Claude Haiku 4.5, or eligible Gemini models exposed by Agy
- medium routes use GPT-5.6 Terra, Codex Spark, or Claude Sonnet 5; medium-hard routes use GPT-5.5, GPT-5.6 Sol, Codex Spark, or Claude Sonnet 5
- complex/holy-fuck routes use GPT-5.6 Sol or Claude Opus 4.8; Sol uses high effort only at medium-hard and xhigh above it, while Opus uses xhigh at complex and max at holy-fuck

Bash helper shape:
```bash
ROUTER_FILE="$ASSET_ROOT/run-with-it-router.py"
ROUTER_LEDGER_FILE="${RUN_WITH_IT_USAGE_LEDGER_FILE:-.run-with-it/usage-ledger.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DETECTED_AGENTS="$("$ASSET_ROOT/run-agent.sh" --list-agents --detected-only | cut -f1 | paste -sd, -)"
ROUTER_EXCLUDE_ARGS=()
IFS=',' read -r -a ROUTER_EXCLUDES <<< "${REVIEW_EXCLUDE_MODELS:-${EXCLUDE_MODEL:-}}"
for excluded_model in "${ROUTER_EXCLUDES[@]}"; do
  [ -n "$excluded_model" ] && ROUTER_EXCLUDE_ARGS+=(--exclude-model "$excluded_model")
done
ROUTE_JSON="$("$PYTHON_BIN" "$ROUTER_FILE" \
  --registry-file "$ASSET_ROOT/agent-registry.json" \
  --ledger-file "$ROUTER_LEDGER_FILE" \
  --role "$ROUTE_ROLE" \
  --complexity-level "$ROUTE_COMPLEXITY_LEVEL" \
  --detected-agents "$DETECTED_AGENTS" \
  --allowlist "${AGENT_ALLOWLIST:-}" \
  --denylist "${AGENT_DENYLIST:-}" \
  --forced-agent "${FORCED_AGENT:-}" \
  --forced-model "${FORCED_MODEL:-}" \
  "${ROUTER_EXCLUDE_ARGS[@]}" \
  --prefer-model "${PREFER_MODEL:-}" \
  --model-denylist "${RUN_WITH_IT_MODEL_DENYLIST:-}" \
  --availability-file "${RUN_WITH_IT_MODEL_AVAILABILITY_FILE:-}" \
  --record)"
AGENT="$(printf '%s' "$ROUTE_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["agent"])')"
MODEL="$(printf '%s' "$ROUTE_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["model"])')"
EFFORT="$(printf '%s' "$ROUTE_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("effort", ""))')"
SELECTION_REASON="$(printf '%s' "$ROUTE_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["selection_reason"])')"
printf 'STATUS|type=route-selected|issue=%s|role=%s|agent=%s|model=%s|effort=%s|reason=%s\n' \
  "$SUB_COORD_ISSUE_NUMBER" "$ROUTE_ROLE" "$AGENT" "$MODEL" "$EFFORT" "$SELECTION_REASON" \
  >> "$SUB_COORD_LOG_FILE"
```

PowerShell helper shape:
```powershell
$routerFile = Join-Path $ASSET_ROOT "run-with-it-router.py"
$routerLedgerFile = if ($env:RUN_WITH_IT_USAGE_LEDGER_FILE) { $env:RUN_WITH_IT_USAGE_LEDGER_FILE } else { ".run-with-it/usage-ledger.json" }
$pythonBin = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python3" }
$detectedAgents = (& (Join-Path $ASSET_ROOT "run-agent.ps1") --list-agents --detected-only | ForEach-Object { ($_ -split "`t")[0] }) -join ","
$reviewExcludeModels = if ($env:REVIEW_EXCLUDE_MODELS) { $env:REVIEW_EXCLUDE_MODELS } else { $env:EXCLUDE_MODEL }
$routerExcludeArgs = @()
foreach ($excludedModel in ($reviewExcludeModels -split ',' | Where-Object { $_ })) {
  $routerExcludeArgs += @("--exclude-model", $excludedModel)
}
$routeJson = & $pythonBin $routerFile `
  --registry-file (Join-Path $ASSET_ROOT "agent-registry.json") `
  --ledger-file $routerLedgerFile `
  --role $env:ROUTE_ROLE `
  --complexity-level $env:ROUTE_COMPLEXITY_LEVEL `
  --detected-agents $detectedAgents `
  --allowlist $env:AGENT_ALLOWLIST `
  --denylist $env:AGENT_DENYLIST `
  --forced-agent $env:FORCED_AGENT `
  --forced-model $env:FORCED_MODEL `
  @routerExcludeArgs `
  --prefer-model $env:PREFER_MODEL `
  --model-denylist $env:RUN_WITH_IT_MODEL_DENYLIST `
  --availability-file $env:RUN_WITH_IT_MODEL_AVAILABILITY_FILE `
  --record
$route = $routeJson | ConvertFrom-Json
$AGENT = $route.agent
$MODEL = $route.model
$EFFORT = if ($route.effort) { [string]$route.effort } else { "" }
$SELECTION_REASON = $route.selection_reason
Add-Content -Path $env:SUB_COORD_LOG_FILE -Value "STATUS|type=route-selected|issue=$env:SUB_COORD_ISSUE_NUMBER|role=$env:ROUTE_ROLE|agent=$AGENT|model=$MODEL|effort=$EFFORT|reason=$SELECTION_REASON"
```

Route helper inputs by phase:
- complexity worker: `ROUTE_ROLE=complexity`, `ROUTE_COMPLEXITY_LEVEL=${COMPLEXITY_LEVEL:-medium}` unless an explicit runtime override skips complexity entirely
- plan worker: `ROUTE_ROLE=plan`, `ROUTE_COMPLEXITY_LEVEL=<blind scored complexity level>` — pass the blind band as-is; the router's `PLAN_BUMP` forces a strong band internally, so never pre-bump it here (see *Plan Sub-Agent Delegation*)
- implementer worker: `ROUTE_ROLE=impl`, `ROUTE_COMPLEXITY_LEVEL=$EFFECTIVE_COMPLEXITY` — the plan's grounded re-score when a plan ran, otherwise the blind scored complexity level (see *Plan Sub-Agent Delegation*)
- reviewer worker: `ROUTE_ROLE=review`, use the implementation complexity band (`$EFFECTIVE_COMPLEXITY`, the same band the implementer routed on) — the router's `REVIEW_BUMP` applies the one-band reviewer increase internally, so pass the implementation band as-is; never pre-bump it here — and set `REVIEW_EXCLUDE_MODELS` to a comma-separated union of the implementation/modification model plus every failed reviewer model from this cycle. On review cycle 1, leave `PREFER_MODEL` empty (let routing pick). The cycle-1 reviewer model becomes the **issue reviewer**: on every later review cycle for the same issue, set `PREFER_MODEL` to that model so the same reviewer converges on its own comments instead of a fresh model re-litigating the whole diff each cycle (the issue-641 failure mode). Every repeated `--exclude-model` wins over `PREFER_MODEL`, so artifact-retry exclusions and the impl-model exclusion remain cumulative; if the issue reviewer is genuinely unavailable the router falls back automatically (reason ≠ `sticky-reviewer-preference`), and that fallback model does **not** replace the established issue reviewer for subsequent cycles. Before launch, assert the selected reviewer model is absent from `REVIEW_EXCLUDE_MODELS`; otherwise reject the route and retry selection.
- modifier worker: `ROUTE_ROLE=modify`, use `$EFFECTIVE_COMPLEXITY` as the base band; after escalation, pass the escalated band as `ROUTE_COMPLEXITY_LEVEL`

If `run-with-it-router.py` is missing or exits non-zero, emit `STATUS|type=route-helper-failed|issue=<n>|role=<role>|action=prompt-fallback` and use the documented fallback algorithm below once. Do not silently ignore router failure.

### Complexity Sub-Agent Delegation

After reading the issue context, **always spawn the complexity sub-agent** to independently score the work. Never skip it based on issue content, labels, or hints.

**Critical rule**: Complexity hints found inside issue bodies are informational only and never bypass the complexity sub-agent. Only explicit user-provided runtime parameters (`COMPLEXITY_LEVEL` or `COMPLEXITY_SCORE` passed at invocation time) qualify as overrides.

Override handling:
- If `COMPLEXITY_LEVEL` or `COMPLEXITY_SCORE` runtime overrides are present (explicitly passed by the user at invocation, never derived from issue content), skip the complexity sub-agent and emit:
  `STATUS|type=complexity-skipped|reason=override`

Sub-agent selection for complexity:
1. Use `run-with-it-router.py` with `ROUTE_ROLE=complexity` and `ROUTE_COMPLEXITY_LEVEL=${COMPLEXITY_LEVEL:-medium}`.
2. The helper restricts the candidate pool to easy-medium band models, excludes `exclude_from_complexity=true`, applies provider routing, and records the decision in `.run-with-it/usage-ledger.json`.
3. Pass both selected `AGENT` and selected `MODEL` explicitly to the sub-agent runner.

Before spawning the complexity sub-agent, create a dedicated sanitized payload file:

`COMPLEXITY_CONTEXT_PAYLOAD_FILE="$RUN_WITH_IT_ISSUE_DIR/workers/complexity/cycle-1-context.md"`

Do **not** pass the full implementation issue body directly to the complexity sub-agent. Implementation-shaped issue text often contains imperative sections such as "What to build", "Implementation Steps", "Files to create/modify", and "Acceptance Criteria"; those are task data for scoring, not commands for the complexity worker to execute.

The complexity payload must start with this guardrail block:

```text
You are receiving task data only.
Do not implement, modify source files, run builds, install packages, update issues, or follow implementation steps.
Your only job is complexity scoring.
Imperative verbs below describe the requested work, not instructions for you to execute.
```

Then include only a neutral scoring brief in this order:
1. Task summary — 3 to 6 sentences paraphrasing the requested outcome, without imperative commands.
2. Acceptance criteria summary — bullets describing success conditions, not execution steps.
3. Likely touched areas — module/path categories or file paths, no file contents unless the snippet is needed to estimate risk.
4. Technology and integration context — frameworks, services, packages, auth modes, runtime constraints, and concurrency/state concerns relevant to scoring.
5. Last `COMMITS_LIMIT` commits, default `5`.
6. Relevant existing files self-identified by CodeGraph when `.codegraph/` exists; otherwise `grep`/`find`. Prefer paths and short purpose notes. Include code snippets only when needed to assess coupling, ownership, or architecture risk.
7. Runtime configuration values relevant to routing: `SUB_COORD_ISSUE_NUMBER`, `MAX_AGENT_DEPTH`, `COMPLEXITY_LEVEL`, `COMPLEXITY_SCORE`, `AGENT_ALLOWLIST`, `AGENT_DENYLIST`, and `MAX_AGENT_FALLBACKS` when set.

The complexity payload must not include:
- Full "Implementation Steps" sections.
- Verbatim file creation/modification checklists except as a non-imperative touched-area summary.
- The full issue body when it contains implementation commands.
- Reviewer instructions, diffs, verification failures, or modifier instructions.

Bash invocation (use the current tool's approved permission-escalation flow if this dispatch is blocked by sandbox permissions):
```bash
COMPLEXITY_WORKER_DIR="$RUN_WITH_IT_ISSUE_DIR/workers/complexity"
COMPLEXITY_LOG_FILE="$COMPLEXITY_WORKER_DIR/cycle-1.log"
COMPLEXITY_DONE_FILE="$COMPLEXITY_WORKER_DIR/cycle-1.done"
COMPLEXITY_RESULT_FILE="$COMPLEXITY_WORKER_DIR/cycle-1-result.json"
COMPLEXITY_STATE_FILE="$COMPLEXITY_WORKER_DIR/cycle-1.state.json"
WORKER_POLL_SECONDS="${WORKER_POLL_SECONDS:-20}"
WORKER_QUIET_SECONDS="${WORKER_QUIET_SECONDS:-120}"
WORKER_STALL_SECONDS="${WORKER_STALL_SECONDS:-300}"
WORKER_LOG_SUMMARY_SECONDS="${WORKER_LOG_SUMMARY_SECONDS:-60}"
WORKER_TAIL_STATE_FILE=".run-with-it/status/issue-${SUB_COORD_ISSUE_NUMBER}-complexity-cycle-1.tail.sha"
mkdir -p "$COMPLEXITY_WORKER_DIR"
"$ASSET_ROOT/run-with-it-dispatch.sh" \
  --asset-root "$ASSET_ROOT" \
  --role complexity \
  --issue "$SUB_COORD_ISSUE_NUMBER" \
  --cycle 1 \
  --agent "$AGENT" \
  --model "$MODEL" \
  --effort "$EFFORT" \
  --context-file "$COMPLEXITY_CONTEXT_PAYLOAD_FILE" \
  --prompt-file "$ASSET_ROOT/complexity-prompt.md" \
  --log-file "$COMPLEXITY_LOG_FILE" \
  --done-file "$COMPLEXITY_DONE_FILE" \
  --result-file "$COMPLEXITY_RESULT_FILE" \
  --state-file "$COMPLEXITY_STATE_FILE" \
  --issue-dir "$RUN_WITH_IT_ISSUE_DIR" \
  --status-file "${RUN_WITH_IT_STATUS_FILE:-}" \
  --events-log "${RUN_WITH_IT_EVENTS_LOG:-}" \
  --quiet-seconds "$WORKER_QUIET_SECONDS" \
  --stall-seconds "$WORKER_STALL_SECONDS" \
  --detach

WORKER_PID="$(wait_for_worker_dispatcher_pid "$COMPLEXITY_STATE_FILE")"
```

Immediately write `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` with this dispatcher `WORKER_PID`, `role=complexity`, `cycle=1`, selected `AGENT`, selected `MODEL`, selected route `selection_reason`, `COMPLEXITY_LOG_FILE`, `COMPLEXITY_DONE_FILE`, `COMPLEXITY_RESULT_FILE`, and `COMPLEXITY_STATE_FILE`. Monitor `COMPLEXITY_STATE_FILE`; complexity is complete only after the done file and valid artifacts include a valid `COMPLEXITY|` line and JSON blob.

PowerShell (Windows):
```powershell
$COMPLEXITY_WORKER_DIR = Join-Path (Join-Path $RUN_WITH_IT_ISSUE_DIR "workers") "complexity"
$COMPLEXITY_LOG_FILE = Join-Path $COMPLEXITY_WORKER_DIR "cycle-1.log"
$COMPLEXITY_DONE_FILE = Join-Path $COMPLEXITY_WORKER_DIR "cycle-1.done"
$COMPLEXITY_RESULT_FILE = Join-Path $COMPLEXITY_WORKER_DIR "cycle-1-result.json"
$COMPLEXITY_STATE_FILE = Join-Path $COMPLEXITY_WORKER_DIR "cycle-1.state.json"
New-Item -ItemType Directory -Force -Path $COMPLEXITY_WORKER_DIR | Out-Null
& (Join-Path $ASSET_ROOT "run-with-it-dispatch.ps1") `
  -AssetRoot $ASSET_ROOT `
  -Role complexity `
  -Issue $env:SUB_COORD_ISSUE_NUMBER `
  -Cycle 1 `
  -Agent $AGENT `
  -Model $MODEL `
  -Effort $EFFORT `
  -ContextFile $COMPLEXITY_CONTEXT_PAYLOAD_FILE `
  -PromptFile (Join-Path $ASSET_ROOT "complexity-prompt.md") `
  -LogFile $COMPLEXITY_LOG_FILE `
  -DoneFile $COMPLEXITY_DONE_FILE `
  -ResultFile $COMPLEXITY_RESULT_FILE `
  -StateFile $COMPLEXITY_STATE_FILE `
  -IssueDir $RUN_WITH_IT_ISSUE_DIR `
  -StatusFile $env:RUN_WITH_IT_STATUS_FILE `
  -EventsLog $env:RUN_WITH_IT_EVENTS_LOG `
  -Detach
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

### Prompt Fallback Router

Use this section only when `run-with-it-router.py` is unavailable or exits non-zero. The complexity score comes from the complexity sub-agent, a forced override, or the bounded fallback path above. The fallback is intentionally bounded to one phase so a helper failure is visible instead of silently changing routing behavior for the whole run.

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

#### Step 3 — Build Pool and Select Model

From `model_catalog` in `agent-registry.json`:
1. Collect all models where `complexity_weight` is within `[weight_min, weight_max]`.
2. Keep only models available on at least one detected, non-filtered agent.
3. Apply `model_routing.provider_routing_rules` — Google/Gemini models are eligible only through `agy`.
4. Sort by `(complexity_weight ASC, context_window DESC, ability fit)`.
5. Take the top `selection_pool_size` (default 4) as the base pool.
6. Append any models listed in `model_routing.band_required_models[current_level]` not already in the pool.
7. Prefer the agent/model pair that is furthest below its subscription usage target; if usage data is unavailable, pick from the highest target share for that role/band.
8. If fewer than 2 candidates exist, expand `weight_max` by 1 and retry (up to 3 expansions).

#### Step 4 — Select Agent

1. Find all agents that list the chosen model in their `known_models`.
2. Apply `AGENT_ALLOWLIST` and `AGENT_DENYLIST`.
3. Keep only detected (installed) agents.
4. Exclude any agent whose registry entry sets `routing_disabled=true`; this blocks `github-copilot` even when the CLI is installed or manually requested.
5. For GPT models, prefer Codex unless usage targets push work to Claude or Agy.
6. For Claude-provider models, prefer direct `claude`.
7. If one agent remains, use it. If multiple non-interchangeable agents remain, prefer registry `agent_preference_rules`; otherwise prefer whichever is furthest below its subscription usage target.
8. If no agent remains, fail with clear filter diagnostics.

#### Step 5 — Pass to Runner

Always pass both `AGENT` and `MODEL` explicitly. Never rely on the agent's registry default.

### Plan Sub-Agent Delegation

After complexity is scored (and before the baseline SHA is captured), optionally run a read-only **plan** sub-agent. A strong model reads the real code in the issue worktree and writes a concrete "how I'll build this" plan (`plan.md` + machine-readable `plan.json`) that the implementer, reviewer, and modifier all consume. The plan worker **never commits or edits** — it runs before `ISSUE_BASE_SHA` so it cannot corrupt the baseline diff.

**Gate (reads the BLIND complexity level only).** The gate decides whether a plan is worth its cost. Refinement does not exist until a plan runs, so it can never feed back into its own gate.

- `RUN_WITH_IT_PLAN_ENABLED` (default `1`) — master switch. When `0`, skip the phase entirely.
- `RUN_WITH_IT_PLAN_MIN_COMPLEXITY` (default `medium-hard`) — run a plan only when the blind complexity level is at or above this band.

Resolve `EFFECTIVE_COMPLEXITY` once, right after this phase. It is the routing band for the `impl` spawn and **every later `modify` spawn**:
- plan ran and `plan.json` has a valid `complexity_level` → `EFFECTIVE_COMPLEXITY = <plan complexity_level>` (the grounded re-score).
- plan skipped (gate / disabled) or plan failed / invalid → `EFFECTIVE_COMPLEXITY = <blind complexity level>` (today's behavior, unchanged).

Emit `STATUS|type=complexity-refined|issue=<n>|from=<blind>|to=<plan>` only when the override actually changes the band, so the routing decision is auditable.

**Plan failure is non-blocking.** If the plan worker cannot produce a valid `plan.json` after the normal bounded artifact retries, treat the plan as skipped: emit `STATUS|type=plan-skipped|issue=<n>|reason=failed|...`, proceed to implementation with `EFFECTIVE_COMPLEXITY = <blind>` and no `RUN_WITH_IT_PLAN_FILE`. The plan is additive and gated — a failed or absent plan must never block the issue or degrade it below today's behavior. Do **not** spawn the Artifact Recovery Worker for a failed plan (it never commits, so there is no dirty work to salvage).

Bash (gate + resolve; use the current tool's approved permission-escalation flow if dispatch is blocked by sandbox permissions):
```bash
band_rank() {
  case "$1" in
    quite-easy) echo 0 ;; easy) echo 1 ;; medium) echo 2 ;;
    medium-hard) echo 3 ;; complex) echo 4 ;; holy-fuck) echo 5 ;;
    *) echo -1 ;;
  esac
}
PLAN_ENABLED="${RUN_WITH_IT_PLAN_ENABLED:-1}"
PLAN_MIN_COMPLEXITY="${RUN_WITH_IT_PLAN_MIN_COMPLEXITY:-medium-hard}"
BLIND_COMPLEXITY_LEVEL="$COMPLEXITY_LEVEL"          # scored blind band from the complexity worker / fallback / override
EFFECTIVE_COMPLEXITY="$BLIND_COMPLEXITY_LEVEL"      # default; refined below only if a plan runs and re-scores
PLAN_FILE="$RUN_WITH_IT_ISSUE_DIR/plan.md"          # top-level, reused across cycles
PLAN_RAN=0

if [ "$PLAN_ENABLED" != "1" ] || [ "$(band_rank "$BLIND_COMPLEXITY_LEVEL")" -lt "$(band_rank "$PLAN_MIN_COMPLEXITY")" ]; then
  SKIP_REASON="below-threshold"; [ "$PLAN_ENABLED" != "1" ] && SKIP_REASON="disabled"
  printf 'STATUS|type=plan-skipped|issue=%s|reason=%s|blind=%s|min=%s\n' \
    "$SUB_COORD_ISSUE_NUMBER" "$SKIP_REASON" "$BLIND_COMPLEXITY_LEVEL" "$PLAN_MIN_COMPLEXITY" >> "$SUB_COORD_LOG_FILE"
  # EFFECTIVE_COMPLEXITY stays = blind; proceed to baseline SHA capture + impl. Do NOT create workers/plan/.
else
  PLAN_RAN=1
  # Route the plan worker: ROUTE_ROLE=plan, ROUTE_COMPLEXITY_LEVEL=<blind band>. The router's PLAN_BUMP forces a
  # strong band internally, so pass the blind band as-is — never pre-bump it here.
  PLAN_WORKER_DIR="$RUN_WITH_IT_ISSUE_DIR/workers/plan"
  PLAN_LOG_FILE="$PLAN_WORKER_DIR/cycle-1.log"
  PLAN_DONE_FILE="$PLAN_WORKER_DIR/cycle-1.done"
  PLAN_RESULT_FILE="$PLAN_WORKER_DIR/cycle-1-result.json"   # == RUN_WITH_IT_PLAN_RESULT_FILE == RUN_WITH_IT_RESULT_FILE
  PLAN_STATE_FILE="$PLAN_WORKER_DIR/cycle-1.state.json"
  PLAN_CONTEXT_PAYLOAD_FILE="$PLAN_WORKER_DIR/cycle-1-context.md"
  mkdir -p "$PLAN_WORKER_DIR"

  # Assemble the plan payload: the real issue scope + acceptance criteria (the planner MUST know what to build),
  # the blind complexity result, and the plan artifact paths. The plan worker reads the worktree itself.
  {
    printf 'Issue scope and acceptance criteria for issue #%s:\n' "$SUB_COORD_ISSUE_NUMBER"
    # ... append the full slice requirements / acceptance criteria here ...
    printf '\nBlind complexity: level=%s\n' "$BLIND_COMPLEXITY_LEVEL"
    printf 'RUN_WITH_IT_PLAN_FILE=%s\n' "$PLAN_FILE"
    printf 'RUN_WITH_IT_PLAN_RESULT_FILE=%s\n' "$PLAN_RESULT_FILE"
  } > "$PLAN_CONTEXT_PAYLOAD_FILE"

  # (route via the Deterministic Router Helper with ROUTE_ROLE=plan, ROUTE_COMPLEXITY_LEVEL="$BLIND_COMPLEXITY_LEVEL")
  "$ASSET_ROOT/run-with-it-dispatch.sh" \
    --asset-root "$ASSET_ROOT" \
    --role plan \
    --issue "$SUB_COORD_ISSUE_NUMBER" \
    --cycle 1 \
    --agent "$AGENT" \
    --model "$MODEL" \
    --effort "$EFFORT" \
    --context-file "$PLAN_CONTEXT_PAYLOAD_FILE" \
    --prompt-file "$ASSET_ROOT/plan-prompt.md" \
    --log-file "$PLAN_LOG_FILE" \
    --done-file "$PLAN_DONE_FILE" \
    --result-file "$PLAN_RESULT_FILE" \
    --state-file "$PLAN_STATE_FILE" \
    --repo-root "$ISSUE_WORKTREE_PATH" \
    --issue-dir "$RUN_WITH_IT_ISSUE_DIR" \
    --status-file "${RUN_WITH_IT_STATUS_FILE:-}" \
    --events-log "${RUN_WITH_IT_EVENTS_LOG:-}" \
    --quiet-seconds "${WORKER_QUIET_SECONDS:-120}" \
    --stall-seconds "${WORKER_STALL_SECONDS:-300}" \
    --detach
  WORKER_PID="$(wait_for_worker_dispatcher_pid "$PLAN_STATE_FILE")"
fi
```

**Plan dispatch rules** (differences from the impl spawn):
- Pass `--repo-root "$ISSUE_WORKTREE_PATH"` so the planner can read real code. The plan is **not** an implementation role, so the dispatcher gives it read access without commit/check-in handling.
- Do **not** set `CHECKIN_OWNER` / `CHECKIN_TARGET` in the payload — the plan worker never commits.
- Pass `RUN_WITH_IT_PLAN_FILE` and `RUN_WITH_IT_PLAN_RESULT_FILE` into the context payload (the dispatcher also sets `RUN_WITH_IT_RESULT_FILE` from `--result-file`, which equals `PLAN_RESULT_FILE`).

After the plan worker's done file and a valid `plan.json` are present (see *Worker Done Files* for plan completeness):
1. Read `complexity_level` from `plan.json`. If `plan.json` is valid and its `complexity_level` is one of the router's bands, set `EFFECTIVE_COMPLEXITY` to it; otherwise keep `EFFECTIVE_COMPLEXITY = BLIND_COMPLEXITY_LEVEL`.
2. If `EFFECTIVE_COMPLEXITY` differs from `BLIND_COMPLEXITY_LEVEL`, emit `STATUS|type=complexity-refined|issue=<n>|from=$BLIND_COMPLEXITY_LEVEL|to=$EFFECTIVE_COMPLEXITY`.
3. Store `plan_file`, `plan_result_file`, a short `plan_summary` (the `approach` field), and `plan_complexity_level` into `$RUN_WITH_IT_ISSUE_DIR/sub-state.json`.

```bash
if [ "$PLAN_RAN" = "1" ]; then
  PLAN_COMPLEXITY_LEVEL="$("${PYTHON_BIN:-python3}" -c 'import json,sys
try:
    d=json.load(open(sys.argv[1]))
    print(d.get("complexity_level","") if isinstance(d,dict) else "")
except Exception:
    print("")' "$PLAN_RESULT_FILE" 2>/dev/null || true)"
  # Only refine when BOTH artifacts are complete: a valid band in plan.json AND a
  # non-empty plan.md. Refining from a plan.json whose plan.md never landed would
  # change routing while downstream workers get no plan to follow.
  if [ -s "$PLAN_FILE" ] && [ -n "$PLAN_COMPLEXITY_LEVEL" ] && [ "$(band_rank "$PLAN_COMPLEXITY_LEVEL")" -ge 0 ]; then
    EFFECTIVE_COMPLEXITY="$PLAN_COMPLEXITY_LEVEL"
  fi
  if [ "$EFFECTIVE_COMPLEXITY" != "$BLIND_COMPLEXITY_LEVEL" ]; then
    printf 'STATUS|type=complexity-refined|issue=%s|from=%s|to=%s\n' \
      "$SUB_COORD_ISSUE_NUMBER" "$BLIND_COMPLEXITY_LEVEL" "$EFFECTIVE_COMPLEXITY" >> "$SUB_COORD_LOG_FILE"
  fi
fi
```

PowerShell (dispatch shape — mirror the gate/resolve logic above using `band_rank` equivalents):
```powershell
$PLAN_WORKER_DIR = Join-Path (Join-Path $RUN_WITH_IT_ISSUE_DIR "workers") "plan"
$PLAN_LOG_FILE = Join-Path $PLAN_WORKER_DIR "cycle-1.log"
$PLAN_DONE_FILE = Join-Path $PLAN_WORKER_DIR "cycle-1.done"
$PLAN_RESULT_FILE = Join-Path $PLAN_WORKER_DIR "cycle-1-result.json"
$PLAN_STATE_FILE = Join-Path $PLAN_WORKER_DIR "cycle-1.state.json"
New-Item -ItemType Directory -Force -Path $PLAN_WORKER_DIR | Out-Null
& (Join-Path $ASSET_ROOT "run-with-it-dispatch.ps1") `
  -AssetRoot $ASSET_ROOT `
  -Role plan `
  -Issue $env:SUB_COORD_ISSUE_NUMBER `
  -Cycle 1 `
  -Agent $AGENT `
  -Model $MODEL `
  -Effort $EFFORT `
  -ContextFile $PLAN_CONTEXT_PAYLOAD_FILE `
  -PromptFile (Join-Path $ASSET_ROOT "plan-prompt.md") `
  -LogFile $PLAN_LOG_FILE `
  -DoneFile $PLAN_DONE_FILE `
  -ResultFile $PLAN_RESULT_FILE `
  -StateFile $PLAN_STATE_FILE `
  -RepoRoot $ISSUE_WORKTREE_PATH `
  -IssueDir $RUN_WITH_IT_ISSUE_DIR `
  -StatusFile $env:RUN_WITH_IT_STATUS_FILE `
  -EventsLog $env:RUN_WITH_IT_EVENTS_LOG `
  -Detach
```

**Capture the issue baseline SHA before spawning the implementer** — it is already set at worktree bootstrap, so this step confirms it rather than overwriting it. This SHA anchors the reviewer's diff range and must never be the root checkout's `HEAD` at review time (other issues may commit in parallel).

```bash
ISSUE_BASE_SHA="${ISSUE_BASE_SHA:-$(git -C "$ISSUE_WORKTREE_PATH" rev-parse HEAD)}"
```

Store `ISSUE_BASE_SHA` in `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` immediately. This value never changes for the lifetime of this issue.

Bash (macOS / Linux / Git Bash; use the current tool's approved permission-escalation flow if this dispatch is blocked by sandbox permissions):
```bash
cat >> "$CONTEXT_PAYLOAD_FILE" <<EOF
RUN_WITH_IT_REPO_ROOT=$ISSUE_WORKTREE_PATH
RUN_WITH_IT_ISSUE_BRANCH=$ISSUE_BRANCH
RUN_WITH_IT_SHARED_FEATURE_BRANCH=$RUN_FEATURE_BRANCH
CHECKIN_TARGET=issue-worktree
CHECKIN_OWNER=impl-worker
EOF
# When the plan phase produced an approach plan, hand it to the implementer.
[ -f "$PLAN_FILE" ] && printf 'RUN_WITH_IT_PLAN_FILE=%s\n' "$PLAN_FILE" >> "$CONTEXT_PAYLOAD_FILE"

IMPL_WORKER_DIR="$RUN_WITH_IT_ISSUE_DIR/workers/impl"
IMPL_LOG_FILE="$IMPL_WORKER_DIR/cycle-${CYCLE:-1}.log"
IMPL_DONE_FILE="$IMPL_WORKER_DIR/cycle-${CYCLE:-1}.done"
IMPL_RESULT_FILE="$IMPL_WORKER_DIR/cycle-${CYCLE:-1}-result.json"
IMPL_STATE_FILE="$IMPL_WORKER_DIR/cycle-${CYCLE:-1}.state.json"
WORKER_POLL_SECONDS="${WORKER_POLL_SECONDS:-20}"
WORKER_QUIET_SECONDS="${WORKER_QUIET_SECONDS:-120}"
WORKER_STALL_SECONDS="${WORKER_STALL_SECONDS:-300}"
WORKER_LOG_SUMMARY_SECONDS="${WORKER_LOG_SUMMARY_SECONDS:-60}"
WORKER_TAIL_STATE_FILE=".run-with-it/status/issue-${SUB_COORD_ISSUE_NUMBER}-impl-cycle-${CYCLE:-1}.tail.sha"
mkdir -p "$IMPL_WORKER_DIR"
"$ASSET_ROOT/run-with-it-dispatch.sh" \
  --asset-root "$ASSET_ROOT" \
  --role impl \
  --issue "$SUB_COORD_ISSUE_NUMBER" \
  --cycle "${CYCLE:-1}" \
  --agent "$AGENT" \
  --model "$MODEL" \
  --effort "$EFFORT" \
  --context-file "$CONTEXT_PAYLOAD_FILE" \
  --prompt-file "$ASSET_ROOT/prompt.md" \
  --log-file "$IMPL_LOG_FILE" \
  --done-file "$IMPL_DONE_FILE" \
  --result-file "$IMPL_RESULT_FILE" \
  --state-file "$IMPL_STATE_FILE" \
  --repo-root "$ISSUE_WORKTREE_PATH" \
  --issue-dir "$RUN_WITH_IT_ISSUE_DIR" \
  --status-file "${RUN_WITH_IT_STATUS_FILE:-}" \
  --events-log "${RUN_WITH_IT_EVENTS_LOG:-}" \
  --quiet-seconds "$WORKER_QUIET_SECONDS" \
  --stall-seconds "$WORKER_STALL_SECONDS" \
  --detach

WORKER_PID="$(wait_for_worker_dispatcher_pid "$IMPL_STATE_FILE")"
```

Immediately write `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` with this dispatcher `WORKER_PID`, `role=impl`, `cycle=${CYCLE:-1}`, selected `AGENT`, selected `MODEL`, selected route `selection_reason`, `IMPL_LOG_FILE`, `IMPL_DONE_FILE`, `IMPL_RESULT_FILE`, and `IMPL_STATE_FILE`. Monitor `IMPL_STATE_FILE`; implementation is complete only after the done file and valid artifacts include verification evidence and the implementer result report.

If `IMPL_STATE_FILE` ends with `state="failed"` for a handoff/artifact reason, apply the Worker Artifact Recovery Contract before writing any terminal report. The next implementer attempt must receive the failed state file path, expected result file path, recovery patch/status paths when present, and the instruction to salvage or replace dirty work in the same issue worktree. Only after `MAX_AGENT_FALLBACKS` implementation artifact retries and the Artifact Recovery Worker both fail or decline recovery may this issue become `blocked`.

PowerShell (Windows):
```powershell
$IMPL_WORKER_DIR = Join-Path (Join-Path $RUN_WITH_IT_ISSUE_DIR "workers") "impl"
$IMPL_LOG_FILE = Join-Path $IMPL_WORKER_DIR "cycle-$($env:CYCLE).log"
$IMPL_DONE_FILE = Join-Path $IMPL_WORKER_DIR "cycle-$($env:CYCLE).done"
$IMPL_RESULT_FILE = Join-Path $IMPL_WORKER_DIR "cycle-$($env:CYCLE)-result.json"
$IMPL_STATE_FILE = Join-Path $IMPL_WORKER_DIR "cycle-$($env:CYCLE).state.json"
New-Item -ItemType Directory -Force -Path $IMPL_WORKER_DIR | Out-Null
& (Join-Path $ASSET_ROOT "run-with-it-dispatch.ps1") `
  -AssetRoot $ASSET_ROOT `
  -Role impl `
  -Issue $env:SUB_COORD_ISSUE_NUMBER `
  -Cycle $env:CYCLE `
  -Agent $AGENT `
  -Model $MODEL `
  -Effort $EFFORT `
  -ContextFile $CONTEXT_PAYLOAD_FILE `
  -PromptFile (Join-Path $ASSET_ROOT "prompt.md") `
  -LogFile $IMPL_LOG_FILE `
  -DoneFile $IMPL_DONE_FILE `
  -ResultFile $IMPL_RESULT_FILE `
  -StateFile $IMPL_STATE_FILE `
  -RepoRoot $ISSUE_WORKTREE_PATH `
  -IssueDir $RUN_WITH_IT_ISSUE_DIR `
  -StatusFile $env:RUN_WITH_IT_STATUS_FILE `
  -EventsLog $env:RUN_WITH_IT_EVENTS_LOG `
  -Detach
```

After the implementer runner completes, **immediately capture the commit SHA and validate a commit was actually made**:

```bash
cd "$ISSUE_WORKTREE_PATH"
IMPL_COMMIT_SHA=$(git rev-parse HEAD)
if [ "$IMPL_COMMIT_SHA" = "$ISSUE_BASE_SHA" ]; then
  # Implementer did not commit — treat as failure
  printf 'STATUS|type=impl-no-commit|issue=%s|action=fail\n' "$SUB_COORD_ISSUE_NUMBER"
  # Mark issue as failed-review with blocking_reason="implementer-no-commit"
  # Write compact report and exit
fi
```

**Exception — verified no-op:** if `IMPL_COMMIT_SHA` equals `ISSUE_BASE_SHA` but the implementer's result artifact is valid with `"no_op": true` and `verification.passed=true` (the verified no-op contract in `prompt.md`, validated by `run-with-it-artifacts.py`), treat the phase as success with `commit_sha=NONE`; do not mark `implementer-no-commit`.

Store both `ISSUE_BASE_SHA` and `IMPL_COMMIT_SHA` in `$RUN_WITH_IT_ISSUE_DIR/sub-state.json`. These two SHAs define the exact diff range for the reviewer. **Never read `git diff` output into the Sub-Coordinator context. Never pass `HEAD` to the reviewer — use the explicit `IMPL_COMMIT_SHA`.**

### Reviewer Band Selection (prompt fallback router only)

This manual bump applies only when `run-with-it-router.py` is unavailable and the Prompt Fallback Router is in use; the deterministic router performs this one-band bump itself via `REVIEW_BUMP`.

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
5. **Sticky reviewer across cycles.** See the reviewer entry under *Route helper inputs by phase* (Appendix A) — the cycle-1 reviewer model is the issue reviewer and is passed as `PREFER_MODEL` on later cycles; exclusions and availability still win, and a fallback selection does not overwrite the established issue reviewer.

### Degraded Fallback

If no installed agent supports any model in the bumped reviewer band:
1. Fall back to the current implementation band.
2. Exclude the current implementation `model_id` from the candidate pool.
3. Select a different model from the current band.
4. Emit `STATUS|type=review-degraded|task=<n>|reason=no-higher-band-agent` once per task.

### Override Precedence (highest first)

1. `FORCED_AGENT` + `FORCED_MODEL` forced together
2. `FORCED_MODEL` forced alone → skip Steps 1–3, run Step 4 with forced model
3. `FORCED_AGENT` forced alone → skip Step 4, run Steps 1–3 restricted to that agent's `known_models`
4. `COMPLEXITY_LEVEL` forced → use corresponding weight range, skip score computation
5. `COMPLEXITY_SCORE` forced → use as computed score, run full Steps 1–4
6. Computed score from nine dimensions → full Steps 1–4

### Bounded Fallback

If selected agent fails preflight or execution start:
- Attempt next compatible agent from registry fallback order.
- Stop after `MAX_AGENT_FALLBACKS` attempts (default `2`).
- Do not add a special Google/Gemini last-resort phase.
- For Google/Gemini fallback, use `agy` only; never route through the removed standalone `gemini` agent.

## Appendix B: Review Orchestration Contract

### Context Budget and Compaction Handoff (Required)

The sub-coordinator must track a running context budget estimate and halt for a user-driven compaction handoff when the estimate crosses 50% of the host model context window.

Maintain `context_bytes_total`: a running sum of UTF-8 byte length of coordinator-visible content. Estimate tokens as `floor(context_bytes_total / 4)`. Resolve `host_context_window` from the active host model's `context_window` in `agent-registry.json`; fall back to `200000` if unavailable.

When `context_tokens_est / host_context_window >= 0.50` (first crossing only):
1. Persist `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` (schema_version 1; see Appendix D).
2. Emit: `STATUS|type=compact|action=user-required|state_file=$RUN_WITH_IT_ISSUE_DIR/sub-state.json`
3. Print host-appropriate compaction instructions (Claude Code: `/compact`; Codex GUI: use UI compact control; GitHub Copilot: use "compact" equivalent).
4. **Stop** and wait for the user.

### Per-Cycle Steps

The review and modification loop runs up to a cap of **8 cycles**, hardcoded.

**Step 0 — Review Gate Check (cycle 1 only)**

Before spawning any reviewer, evaluate whether review can be skipped. This gate applies **only on cycle 1** (initial implementation pass). If a modifier agent has already run (cycle ≥ 2), skip this check — review is always required for revision cycles.

Gather the `--numstat` data already collected via Appendix C after the implementer completed:
- `files_changed` — number of distinct files in the `git diff --numstat` output
- `total_lines_changed` — sum of all `added + deleted` line counts across all files

Evaluate these rules **in order**; the first matching rule decides:

| Rule | Condition | Action |
|------|-----------|--------|
| 1 | `files_changed > 3` **OR** `total_lines_changed > 55` | **Review is mandatory.** Continue to step 1. |
| 2 | `files_changed ≤ 3` **AND** `total_lines_changed < 30` **AND** verification shows **explicit all-tests-pass** | **Skip review.** Treat as clean approve. Emit `STATUS\|type=review-skipped\|reason=trivial-change\|files=<n>\|lines=<n>`. Write `"review_skipped": true` and `"review_skip_reason": "trivial-change"` into the compact report (Appendix E). Proceed directly to compact report generation — the implementer already committed. Do not continue to steps 1–7 this cycle. |
| 3 | Everything else (`total_lines_changed` 30–55 with `files_changed ≤ 3`, or tests not confirmed passing) | Review is required unless verification results show **100% explicit all-tests-pass** (no absent, partial, timeout, or skipped test coverage) — then skip review as in rule 2. Otherwise continue to step 1. |

"Explicit all-tests-pass" means the implementer's report contains a test command **and** a clearly passing result. Absent, partial, timeout, or skipped test output does **not** qualify — in those cases proceed to step 1.

1. Before assembling the reviewer payload, check the implementing or modifying agent's reported verification results:
   - If verification **actively failed** (tests ran and produced failures), **do not spawn the reviewer** — terminate the issue as `failed-review` with reason `failed-verification`.
   - If verification results are **absent or incomplete**, spawn the reviewer anyway. Include whatever partial verification evidence is available and note the gap.

2. Assemble a reviewer payload file in this order:
   - The full slice requirements — complete issue body including title, description, requirements, and acceptance criteria
   - The original `PROMPT_FILE` contents
   - **Explicit SHA range** (both fields are required; never substitute `HEAD`):
     - `REVIEW_BASE_SHA=<ISSUE_BASE_SHA>` — the commit before any work on this issue
     - `REVIEW_HEAD_SHA=<IMPL_COMMIT_SHA or last MODIFY_COMMIT_SHA>` — the specific commit of the work under review
     - Instruction: `Run git diff <REVIEW_BASE_SHA>..<REVIEW_HEAD_SHA> to fetch the diff. Do NOT use HEAD — other issues may have committed since this SHA.`
   - A per-file `+added/-deleted` summary only: `git diff --numstat <REVIEW_BASE_SHA>..<REVIEW_HEAD_SHA>` — line counts, no diff text. **Do not read the full diff into this payload.**
   - The implementer (or modifier) verification results
   - The implementer telemetry stub
   - When the plan phase produced an artifact, `RUN_WITH_IT_PLAN_FILE=$PLAN_FILE` (only when the file exists) so the reviewer can check the diff against the intended approach and out-of-scope list
   - **Prior-review carry-forward (cycle ≥ 2 only).** Pass the prior review cycles' reviewer instructions and modifier results as a newline-delimited `PRIOR_REVIEW_LEDGER` of `cycle=<k> instructions=<path> modify_result=<path>` lines, one per completed cycle, pointing at `workers/review/cycle-<k>-instructions.json` and `workers/modify/cycle-<k>-result.json`. **Pass paths only — never read these files into the Sub-Coordinator context** (the budget rule in Appendix B forbids it; the reviewer reads them). This lets the reviewer see what was already raised and how the modifier closed each comment, so it confirms closure instead of re-deriving a fresh comment set or re-litigating severities it already accepted (issue 641).
   - Output paths (reviewer writes both files; Sub-Coordinator reads only the status file):
     - `REVIEWER_STATUS_FILE=$RUN_WITH_IT_ISSUE_DIR/workers/review/cycle-<n>-status.json`
     - `REVIEWER_INSTRUCTIONS_FILE=$RUN_WITH_IT_ISSUE_DIR/workers/review/cycle-<n>-instructions.json`

   **Always reinforce in the payload**: these SHA values are concrete commit hashes, not symbolic refs. The reviewer must not resolve `HEAD` or any branch name — the SHAs are the authority.

3. Spawn the reviewer child agent (use the current tool's approved permission-escalation flow if this dispatch is blocked by sandbox permissions):

   Bash:
   ```bash
   REVIEW_WORKER_DIR="$RUN_WITH_IT_ISSUE_DIR/workers/review"
   REVIEW_LOG_FILE="$REVIEW_WORKER_DIR/cycle-${CYCLE}.log"
   REVIEW_DONE_FILE="$REVIEW_WORKER_DIR/cycle-${CYCLE}.done"
   REVIEW_RESULT_FILE="$REVIEWER_STATUS_FILE"
   REVIEW_STATE_FILE="$REVIEW_WORKER_DIR/cycle-${CYCLE}.state.json"
   WORKER_POLL_SECONDS="${WORKER_POLL_SECONDS:-20}"
   WORKER_QUIET_SECONDS="${WORKER_QUIET_SECONDS:-120}"
   WORKER_STALL_SECONDS="${WORKER_STALL_SECONDS:-300}"
   WORKER_LOG_SUMMARY_SECONDS="${WORKER_LOG_SUMMARY_SECONDS:-60}"
   WORKER_TAIL_STATE_FILE=".run-with-it/status/issue-${SUB_COORD_ISSUE_NUMBER}-review-cycle-${CYCLE}.tail.sha"
   mkdir -p "$REVIEW_WORKER_DIR"
   "$ASSET_ROOT/run-with-it-dispatch.sh" \
     --asset-root "$ASSET_ROOT" \
     --role review \
     --issue "$SUB_COORD_ISSUE_NUMBER" \
     --cycle "$CYCLE" \
     --agent "$REVIEWER_AGENT" \
     --model "$REVIEWER_MODEL" \
     --context-file "$REVIEWER_CONTEXT_PAYLOAD_FILE" \
     --prompt-file "$ASSET_ROOT/review-prompt.md" \
     --log-file "$REVIEW_LOG_FILE" \
     --done-file "$REVIEW_DONE_FILE" \
     --result-file "$REVIEW_RESULT_FILE" \
     --state-file "$REVIEW_STATE_FILE" \
     --repo-root "$ISSUE_WORKTREE_PATH" \
     --issue-dir "$RUN_WITH_IT_ISSUE_DIR" \
     --status-file "${RUN_WITH_IT_STATUS_FILE:-}" \
     --events-log "${RUN_WITH_IT_EVENTS_LOG:-}" \
     --quiet-seconds "$WORKER_QUIET_SECONDS" \
     --stall-seconds "$WORKER_STALL_SECONDS" \
     --detach

   WORKER_PID="$(wait_for_worker_dispatcher_pid "$REVIEW_STATE_FILE")"
   ```

4. Emit before reviewer starts: `STATUS|type=review-spawn|task=<n>|cycle=<n>|agent=<name>|model=<model-id>`
5. Immediately write `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` with this dispatcher `WORKER_PID`, `role=review`, `cycle`, reviewer agent/model, selected route `selection_reason`, `REVIEW_LOG_FILE`, `REVIEW_DONE_FILE`, `REVIEW_RESULT_FILE`, and `REVIEW_STATE_FILE`. Monitor `REVIEW_STATE_FILE`; review is complete only after the done file and valid artifacts include both reviewer JSON files.
6. Read **only** `REVIEWER_STATUS_FILE` after the reviewer completes. This file contains `verdict`, `comment_count`, and `nitpick_only` — the only fields the Sub-Coordinator needs. **Never read `REVIEWER_INSTRUCTIONS_FILE`** — that file is for the modifier only.
7. Store the `REVIEWER_INSTRUCTIONS_FILE` path in `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` for this cycle. Do not read its contents.
8. Emit after step 6: `STATUS|type=review-result|task=<n>|cycle=<n>|verdict=<approve|revise|reject>|comment_count=<n>`

### Review Artifact Guardrail

The dispatcher validates review artifacts through `run-with-it-artifacts.py`. It may safely repair two partial review handoffs:
- valid `REVIEWER_INSTRUCTIONS_FILE` but missing/invalid status file → synthesize the minimal status JSON;
- valid status file with `verdict="approve"` **and `comment_count=0`** but missing/invalid instructions file → synthesize an empty approve instructions JSON. A status with `comment_count>0` and missing/invalid instructions is NOT auto-approved — synthesizing an empty approve there would silently drop the reviewer's comments, so it fails and retries instead.

For any review worker state with `state="failed"` and `stall_reason` in this set:
- `missing-result-artifact`
- `invalid-review-status-artifact`
- `missing-review-instructions-artifact`
- `invalid-review-instructions-artifact`
- `review-artifact-verdict-mismatch`
- `review-comment-count-mismatch`
- `review-nitpick-only-mismatch`
- `hard-limit-exceeded`

apply this exact policy:
1. Emit `STATUS|type=review-artifact-failed|issue=<n>|cycle=<n>|attempt=<n>|reason=<stall_reason>|action=retry`.
2. Retry the **same review cycle** up to `MAX_REVIEW_ARTIFACT_RETRIES` attempts (default `2`) with a different reviewer model when available. Append the failed reviewer model to `REVIEW_EXCLUDE_MODELS` without removing the implementation/modification model or earlier failed reviewers, then re-run routing with one repeated `--exclude-model` per value. If the same agent is selected with a different model, that is acceptable. Use attempt-specific artifact paths such as `cycle-${CYCLE}-attempt-${ATTEMPT}-status.json` and `cycle-${CYCLE}-attempt-${ATTEMPT}-instructions.json` so stale partial JSON cannot satisfy the retry. When you use attempt-specific paths, regenerate the reviewer context payload so its `RUN_WITH_IT_RESULT_FILE`, `REVIEWER_STATUS_FILE`, and `REVIEWER_INSTRUCTIONS_FILE` entries exactly match the dispatcher `--result-file` and sibling instructions path for that attempt.
3. Do not increment the review cycle counter for artifact retries. They are infrastructure retries, not reviewer verdicts.
4. If a retry produces valid review artifacts, continue normal verdict routing for the original cycle.
5. If retries are exhausted, write the compact report with `outcome="blocked"` and include `blocking_reasons=["reviewer-missing-result-artifact"]` plus the final dispatcher `stall_reason`, `REVIEW_STATE_FILE`, `REVIEW_RESULT_FILE`, and `REVIEWER_INSTRUCTIONS_FILE` paths in the summary/evidence. Do not report `failed-review`, do not spawn a modifier, and do not merge.

A `hard-limit-exceeded` dispatcher result is infrastructure/artifact loss, not a product review verdict. Artifact infrastructure failures must never be reported as `failed-review`. `failed-review` is reserved for actual review verdicts (`reject`), review-cycle cap exhaustion after valid `revise` verdicts, failed verification, or missing implementation/modification commits.

### Verdict Routing

**`verdict=approve`**

The implementer (or modifier) has already committed all changes as part of its mandatory handoff commit. On approve, proceed directly to compact report generation. **Do not create an additional commit** — the work is already committed. No modification agent is spawned.

**Nitpick-only `approve`**: When all comments have `"severity": "info"` and `"fix"` values prefixed `[nitpick]`, treat as a clean approve — proceed to report generation, no modification agent. List nitpick comments in the report summary under `## Notes`. Do not downgrade for nitpicks alone.

**`verdict=revise`**

1. If the current cycle equals the cap (8), terminate the issue as `failed-review` immediately.
2. Otherwise, spawn a modification agent:
   - Use the original implementer band for the first modification request; after two non-approval reviews, use the next higher implementation band.
   - Emit `STATUS|type=modify-spawn|task=<n>|cycle=<n>|agent=<name>|model=<model-id>` before spawning.
   - Pass: original issue context, original `prompt.md` contents, `REVIEW_BASE_SHA=<ISSUE_BASE_SHA>` (never changes — baseline before any work on this issue), `REVIEW_HEAD_SHA=<IMPL_COMMIT_SHA or last MODIFY_COMMIT_SHA>` (the specific commit the reviewer assessed — modifier fetches accumulated diff via `git diff <REVIEW_BASE_SHA>..<REVIEW_HEAD_SHA>`, **never `..HEAD`**), `REVIEWER_INSTRUCTIONS_FILE=<path>` (modifier reads this file directly for the full comments and fix instructions — do NOT embed the instructions content in the payload), required verification commands, `RUN_WITH_IT_CYCLE=<current cycle number>`, `RUN_WITH_IT_REPO_ROOT=<ISSUE_WORKTREE_PATH>`, `RUN_WITH_IT_ISSUE_BRANCH=<ISSUE_BRANCH>`, `RUN_WITH_IT_SHARED_FEATURE_BRANCH=<RUN_FEATURE_BRANCH>`, `CHECKIN_TARGET=issue-worktree`, `CHECKIN_OWNER=modify-worker`, and `RUN_WITH_IT_PLAN_FILE=<PLAN_FILE>` when the plan artifact exists (the modifier reads it for original intent / cross-cycle continuity).
   - Run via this background-worker shape; use the current tool's approved permission-escalation flow if this dispatch is blocked by sandbox permissions:

     ```bash
     cat >> "$MODIFIER_CONTEXT_PAYLOAD_FILE" <<EOF
RUN_WITH_IT_REPO_ROOT=$ISSUE_WORKTREE_PATH
RUN_WITH_IT_ISSUE_BRANCH=$ISSUE_BRANCH
RUN_WITH_IT_SHARED_FEATURE_BRANCH=$RUN_FEATURE_BRANCH
CHECKIN_TARGET=issue-worktree
CHECKIN_OWNER=modify-worker
EOF
     # Hand the modifier the original approach plan for cross-cycle intent continuity.
     [ -f "$PLAN_FILE" ] && printf 'RUN_WITH_IT_PLAN_FILE=%s\n' "$PLAN_FILE" >> "$MODIFIER_CONTEXT_PAYLOAD_FILE"

     MODIFY_WORKER_DIR="$RUN_WITH_IT_ISSUE_DIR/workers/modify"
     MODIFY_LOG_FILE="$MODIFY_WORKER_DIR/cycle-${CYCLE}.log"
     MODIFY_DONE_FILE="$MODIFY_WORKER_DIR/cycle-${CYCLE}.done"
     MODIFY_RESULT_FILE="$MODIFY_WORKER_DIR/cycle-${CYCLE}-result.json"
     MODIFY_STATE_FILE="$MODIFY_WORKER_DIR/cycle-${CYCLE}.state.json"
     WORKER_POLL_SECONDS="${WORKER_POLL_SECONDS:-20}"
     WORKER_QUIET_SECONDS="${WORKER_QUIET_SECONDS:-120}"
     WORKER_STALL_SECONDS="${WORKER_STALL_SECONDS:-300}"
     WORKER_LOG_SUMMARY_SECONDS="${WORKER_LOG_SUMMARY_SECONDS:-60}"
     WORKER_TAIL_STATE_FILE=".run-with-it/status/issue-${SUB_COORD_ISSUE_NUMBER}-modify-cycle-${CYCLE}.tail.sha"
     mkdir -p "$MODIFY_WORKER_DIR"
     "$ASSET_ROOT/run-with-it-dispatch.sh" \
       --asset-root "$ASSET_ROOT" \
       --role modify \
       --issue "$SUB_COORD_ISSUE_NUMBER" \
       --cycle "$CYCLE" \
       --agent "$MODIFIER_AGENT" \
       --model "$MODIFIER_MODEL" \
       --context-file "$MODIFIER_CONTEXT_PAYLOAD_FILE" \
       --prompt-file "$ASSET_ROOT/modifier-prompt.md" \
       --log-file "$MODIFY_LOG_FILE" \
       --done-file "$MODIFY_DONE_FILE" \
       --result-file "$MODIFY_RESULT_FILE" \
       --state-file "$MODIFY_STATE_FILE" \
       --repo-root "$ISSUE_WORKTREE_PATH" \
       --issue-dir "$RUN_WITH_IT_ISSUE_DIR" \
       --status-file "${RUN_WITH_IT_STATUS_FILE:-}" \
       --events-log "${RUN_WITH_IT_EVENTS_LOG:-}" \
       --quiet-seconds "$WORKER_QUIET_SECONDS" \
       --stall-seconds "$WORKER_STALL_SECONDS" \
       --detach

     WORKER_PID="$(wait_for_worker_dispatcher_pid "$MODIFY_STATE_FILE")"
     ```

     Immediately write `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` with this dispatcher `WORKER_PID`, `role=modify`, `cycle`, modifier agent/model, selected route `selection_reason`, `MODIFY_LOG_FILE`, `MODIFY_DONE_FILE`, `MODIFY_RESULT_FILE`, and `MODIFY_STATE_FILE`. Monitor `MODIFY_STATE_FILE`; modification is complete only after the done file and valid artifacts include verification evidence and the modifier result report.
   - If `MODIFY_STATE_FILE` ends with `state="failed"` for a handoff/artifact reason, apply the Worker Artifact Recovery Contract before advancing the review cycle or writing any terminal report. The next modifier attempt must receive the failed state file path, expected result file path, reviewer instructions path, recovery patch/status paths when present, and the instruction to salvage or replace dirty work in the same issue worktree. Only after `MAX_AGENT_FALLBACKS` modification artifact retries and the Artifact Recovery Worker both fail or decline recovery may this issue become `blocked`.
   - After the modifier runner completes, **capture and validate the modifier commit**:
     ```bash
     cd "$ISSUE_WORKTREE_PATH"
     MODIFY_COMMIT_SHA=$(git rev-parse HEAD)
     if [ "$MODIFY_COMMIT_SHA" = "$REVIEW_HEAD_SHA" ]; then
       # Modifier did not commit — treat as failure
       printf 'STATUS|type=modify-no-commit|issue=%s|cycle=%s|action=fail\n' \
         "$SUB_COORD_ISSUE_NUMBER" "$CYCLE"
       # Terminate as failed-review with blocking_reason="modifier-no-commit"
     fi
     ```
     **Exception — verified no-op:** if `MODIFY_COMMIT_SHA` equals `REVIEW_HEAD_SHA` but the modifier's result artifact is valid with `"no_op": true` and `verification.passed=true` (the verified no-op contract in `modifier-prompt.md`, validated by `run-with-it-artifacts.py`), treat the modification as success; do not mark `modifier-no-commit`, and keep `REVIEW_HEAD_SHA` unchanged for the next review cycle.

     Store `MODIFY_COMMIT_SHA` in state. For the next review cycle: `REVIEW_HEAD_SHA=MODIFY_COMMIT_SHA` (and `REVIEW_BASE_SHA` stays as `ISSUE_BASE_SHA` — never changes).
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

After each agent (implementer or modifier) completes, use the issue baseline and the specific commit SHA to get per-file stats:
```bash
git diff --numstat <ISSUE_BASE_SHA>..<IMPL_COMMIT_SHA or latest MODIFY_COMMIT_SHA>
```
**Never use `HEAD` as the end of this range** — other issues may have committed since. Always use the explicit commit SHA captured immediately after the agent's mandatory commit.

Read only the `--numstat` summary (file path + added + deleted counts) — never read full diff text into context. Aggregate per-file line changes across all agents for this issue. Store the result in `files_modified` in the compact report (Appendix E).

## Appendix C2: Normal Merge Back to Shared Feature Branch

After review approval, attempt to merge this issue branch back into the shared run feature branch. The Main Orchestrator must never perform this merge; this Sub-Coordinator owns the normal merge attempt.

Acquire `.run-with-it/locks/merge.lock` before touching the shared feature branch:

```bash
mkdir -p .run-with-it/locks
while ! mkdir .run-with-it/locks/merge.lock 2>/dev/null; do
  sleep 5
done
trap 'rmdir .run-with-it/locks/merge.lock 2>/dev/null || true' EXIT
```

Then merge in a **fresh throwaway worktree** created from the latest `origin/$RUN_FEATURE_BRANCH`. **Never run `git checkout "$RUN_FEATURE_BRANCH"` or `git merge` in the shared root checkout** — a conflict there leaves the shared checkout with an unresolved index and a dangling `MERGE_HEAD` that breaks the merge for every later issue (the root cause of the `shared-worktree-dirty` / `pre-existing-unresolved-index` cascade). The throwaway worktree isolates the merge so a conflict only aborts locally and the shared checkout is never touched.

```bash
STATUS_LINE="STATUS|type=merge-start|issue=${SUB_COORD_ISSUE_NUMBER}|branch=${ISSUE_BRANCH}|target=${RUN_FEATURE_BRANCH}"
echo "$STATUS_LINE" >> "$SUB_COORD_LOG_FILE"
echo "$STATUS_LINE"

git fetch origin "$RUN_FEATURE_BRANCH" 2>/dev/null || true
MERGE_BASE_REF="origin/${RUN_FEATURE_BRANCH}"
git rev-parse --verify "$MERGE_BASE_REF" >/dev/null 2>&1 || MERGE_BASE_REF="$RUN_FEATURE_BRANCH"

MERGE_WT=".run-with-it/worktrees/merge-${SUB_COORD_ISSUE_NUMBER}"
MERGE_TMP_BRANCH="merge-tmp-${SUB_COORD_ISSUE_NUMBER}"
git worktree remove --force "$MERGE_WT" 2>/dev/null || true
git branch -D "$MERGE_TMP_BRANCH" 2>/dev/null || true
git worktree add --force -B "$MERGE_TMP_BRANCH" "$MERGE_WT" "$MERGE_BASE_REF"

if git -C "$MERGE_WT" merge --no-ff "$ISSUE_BRANCH" -m "merge(#${SUB_COORD_ISSUE_NUMBER}): integrate issue branch"; then
  MERGE_SHA="$(git -C "$MERGE_WT" rev-parse HEAD)"
  git -C "$MERGE_WT" push origin "HEAD:${RUN_FEATURE_BRANCH}" 2>/dev/null || true
  # Keep the local shared-branch ref current for future issue worktrees (best effort).
  git branch -f "$RUN_FEATURE_BRANCH" "$MERGE_SHA" 2>/dev/null || true
  STATUS_LINE="STATUS|type=merge-complete|issue=${SUB_COORD_ISSUE_NUMBER}|merge_sha=${MERGE_SHA}|pushed=true"
else
  # Capture conflicts BEFORE aborting (abort clears the index), then abort so the
  # throwaway worktree — and the shared checkout — are left clean.
  CONFLICT_FILES="$(git -C "$MERGE_WT" diff --name-only --diff-filter=U | tr '\n' ' ')"
  git -C "$MERGE_WT" merge --abort 2>/dev/null || true
  STATUS_LINE="STATUS|type=merge-failed|issue=${SUB_COORD_ISSUE_NUMBER}|reason=conflict|conflict_files=${CONFLICT_FILES}"
fi

# Always remove the throwaway worktree and temp branch. The shared root checkout is never touched.
git worktree remove --force "$MERGE_WT" 2>/dev/null || true
git branch -D "$MERGE_TMP_BRANCH" 2>/dev/null || true

echo "$STATUS_LINE" >> "$SUB_COORD_LOG_FILE"
echo "$STATUS_LINE"
```

On merge success, include `merge.status="completed"`, `merge.merge_sha`, `issue_branch`, `feature_branch`, and `worktree_path` in the compact report. On merge failure, write `outcome="merge_failed"` and include `merge.status="failed"`, `merge.failure_reason`, and `merge.conflict_files` so Main Orchestrator can move the issue to `merge_recovery`.

### Sandbox

**Invoke the platform dispatcher (`run-with-it-dispatch.sh` / `run-with-it-dispatch.ps1`) through the current tool's approved permission-escalation flow when sandbox restrictions block access to agent credentials or required project commands.** The dispatcher sets `GUI_MODE=0` by default before calling `run-agent.sh` / `run-agent.ps1`, preserving unattended runner flags configured in `agent-registry.json`. If permission escalation is unavailable or the dispatch still fails after an approved retry, count it as a true agent failure.

## Appendix D: Sub-Coordinator State (Compaction Survival)

Write `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` using schema_version 1 to survive within-session compaction:

```json
{
  "schema_version": 1,
  "issue_base_sha": "<SHA captured before any work — never changes>",
  "impl_commit_sha": "<SHA captured after implementer's mandatory commit — null until set>",
  "modify_commit_sha": "<SHA captured after latest modifier's mandatory commit — null until set>",
  "review_head_sha": "<current REVIEW_HEAD_SHA for next reviewer — equals impl_commit_sha or modify_commit_sha>",
  "feature_branch": "Maestro/cunning-fox",
  "issue_branch": "Maestro/cunning-fox-issue-36",
  "worktree_path": ".run-with-it/worktrees/issue-36",
  "plan_file": "<.run-with-it/issues/<n>/plan.md when the plan phase ran — null when skipped/disabled>",
  "plan_result_file": "<workers/plan/cycle-1-result.json when the plan phase ran — null otherwise>",
  "plan_summary": "<short approach from plan.json — null when no plan>",
  "plan_complexity_level": "<grounded re-score from plan.json — null when no plan or invalid; drives EFFECTIVE_COMPLEXITY>",
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
  "in_flight_agents": [
    {
      "role": "impl",
      "cycle": 1,
      "pid": 12345,
      "agent": "codex",
      "model": "gpt-5.3-codex",
      "selection_reason": "under-target",
      "log_file": ".run-with-it/issues/36/workers/impl/cycle-1.log",
      "done_file": ".run-with-it/issues/36/workers/impl/cycle-1.done",
      "result_file": ".run-with-it/issues/36/workers/impl/cycle-1-result.json",
      "state_file": ".run-with-it/issues/36/workers/impl/cycle-1.state.json",
      "started_at": "2026-05-15T00:00:00Z"
    }
  ],
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
- Before plan sub-agent spawn (only when the gate runs it)
- After plan done — store `plan_file`, `plan_result_file`, `plan_summary`, and `plan_complexity_level`, then resolve `EFFECTIVE_COMPLEXITY`
- Before implementer spawn (capture and store `issue_base_sha` here)
- After implementer done — store `impl_commit_sha` and set `review_head_sha = impl_commit_sha`
- Before reviewer spawn
- Before modifier spawn
- After modifier done — store `modify_commit_sha` and update `review_head_sha = modify_commit_sha`
- After any verdict is received

### Resume After Compaction

When resumed after compaction:
1. Read `$RUN_WITH_IT_ISSUE_DIR/sub-state.json` to rehydrate state.
2. If in the review loop, retrieve the `REVIEWER_INSTRUCTIONS_FILE` path for the current cycle from state. Do not re-read the status file — use the stored verdict from state instead.
3. Continue from where you left off.
4. The 8-cycle cap is enforced against the restored `cycles_used`.
5. Tasks with a restored `non_approval_count` of 2 or more must resume with the escalated implementation band.

Emit: `STATUS|type=sub-resume|state_file=$RUN_WITH_IT_ISSUE_DIR/sub-state.json|cycles_used=<n>|non_approval_count=<n>`

## Appendix E: Output Contract

### Log File

`$SUB_COORD_LOG_FILE` must live under the issue artifact folder, for example `.run-with-it/issues/<issue>/sub-coordinator.log`. Do not use the legacy scattered role directories for this issue's logs.

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

**Every STATUS, ROUTE, and COMPLEXITY line MUST be written to the log file using an explicit shell command.** Printing to console or mentioning the line in your response text is NOT sufficient — you must run the write command:

```bash
# Use this pattern for every status line:
STATUS_LINE="STATUS|type=example|field=value"
echo "$STATUS_LINE" >> "$SUB_COORD_LOG_FILE"
```

PowerShell:
```powershell
$statusLine = "STATUS|type=example|field=value"
Add-Content -Path $env:SUB_COORD_LOG_FILE -Value $statusLine
```

Write status only at phase boundaries or meaningful state changes. The Main Orchestrator never reads this file into its AI context; it only prints its path.

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

### Worker Log Files

Every worker-agent invocation must set `RUN_WITH_IT_LOG_FILE` to an issue-scoped role file:

- `.run-with-it/issues/<n>/workers/complexity/cycle-1.log`
- `.run-with-it/issues/<n>/workers/impl/cycle-<cycle>.log`
- `.run-with-it/issues/<n>/workers/review/cycle-<cycle>.log`
- `.run-with-it/issues/<n>/workers/modify/cycle-<cycle>.log`
- `.run-with-it/issues/<n>/workers/artifact-recovery/cycle-<cycle>-<role>-recovery.log`

The runner mirrors each worker's stdout/stderr into that file. Do not load raw worker logs into coordinator context. Forward only structured `STATUS|...`, `ROUTE|...`, and `COMPLEXITY|...` lines to `$SUB_COORD_LOG_FILE` and the live status bus.

### Worker State Files

Every worker-agent invocation must set `RUN_WITH_IT_STATE_FILE` to a role-specific watchdog JSON file under the issue folder:

- `.run-with-it/issues/<n>/workers/complexity/cycle-1.state.json`
- `.run-with-it/issues/<n>/workers/impl/cycle-<cycle>.state.json`
- `.run-with-it/issues/<n>/workers/review/cycle-<cycle>.state.json`
- `.run-with-it/issues/<n>/workers/modify/cycle-<cycle>.state.json`
- `.run-with-it/issues/<n>/workers/artifact-recovery/cycle-<cycle>-<role>-recovery.state.json`

The platform dispatcher (`run-with-it-dispatch.sh` / `run-with-it-dispatch.ps1`) owns this file. Read it to determine whether a worker is `running`, `quiet`, `stalled`, `artifact-recovery-required`, `failed`, or `completed`. A `stalled` state describes quiet model output, not process death. Runner-owned `wrapper-heartbeat` events are independent liveness evidence, and the hard elapsed limit is the final bound.

For roles listed in `RUN_WITH_IT_AUTO_FAIL_STALLED_ROLES` (Bash default: `complexity,impl,modify,plan`), the dispatcher terminates a stalled runner and emits `STATUS|type=worker-stall-timeout` followed by `dispatch-failed`. Auto-fail applies only when the runner emits no current wrapper heartbeat; a heartbeat-alive worker is never terminated for quiet stdout alone — `RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS` is the elapsed-time bound. Treat a stall termination as a concrete worker infrastructure failure and apply the role fallback rule. For complexity, retry/fallback; do not wait indefinitely for the terminated scorer.

### Worker Done Files

Every worker-agent invocation must set `RUN_WITH_IT_DONE_FILE` to a role-specific sentinel under the issue folder:

- `.run-with-it/issues/<n>/workers/complexity/cycle-1.done`
- `.run-with-it/issues/<n>/workers/plan/cycle-1.done`
- `.run-with-it/issues/<n>/workers/impl/cycle-<cycle>.done`
- `.run-with-it/issues/<n>/workers/review/cycle-<cycle>.done`
- `.run-with-it/issues/<n>/workers/modify/cycle-<cycle>.done`
- `.run-with-it/issues/<n>/workers/artifact-recovery/cycle-<cycle>-<role>-recovery.done`

The worker may write this file when its required artifacts are complete. The platform dispatcher delegates stale sentinel cleanup and fallback `DONE|...|source=runner-exit` writes to `run-agent.sh` / `run-agent.ps1`. Treat the done file as a phase-transition hint only after required output artifacts are valid:

- complexity: valid `COMPLEXITY|` line and JSON blob are available from the worker stream/log
- plan: the worker result JSON (`plan.json`) exists, parses as valid JSON, has `status="success"`, and includes `schema_version`, `issue`, `role`, `approach`, `complexity_level`, and `slices` (no `commit_sha` — the plan never commits); the human-readable `plan.md` (`RUN_WITH_IT_PLAN_FILE`, at `$RUN_WITH_IT_ISSUE_DIR/plan.md`) exists and is non-empty; **and** the plan done file exists. Both artifacts are required — a `plan.json` without a non-empty `plan.md` is incomplete (downstream workers consume `plan.md`), so treat it as a failed plan. `complexity_level` must be one of the router's accepted bands; if it is missing or invalid, the plan is still usable for its approach/slices but does **not** refine routing — fall back to the blind complexity score for `impl`/`modify` (per *Plan Sub-Agent Delegation*) rather than failing the whole plan. The dispatcher enforces this same shape via `run-with-it-artifacts.py failure-reason --role plan` (`invalid-plan-result-artifact` for a bad/failed `plan.json`, `missing-plan-file-artifact` for a missing/empty `plan.md`).
- impl/modify: the worker result JSON exists, parses as valid JSON, includes `schema_version`, `issue`, `role`, `status`, `commit_sha`, `files_committed`, and `verification`, and either the worker's mandatory commit was made in the issue worktree (captured SHA differs from the pre-spawn baseline and matches the issue worktree `HEAD`) **or** the result is a verified no-op (`"no_op": true` with `verification.passed=true`), which the dispatcher validates via `run-with-it-artifacts.py`
- review: both `REVIEWER_STATUS_FILE` and `REVIEWER_INSTRUCTIONS_FILE` exist and parse as valid JSON; dispatcher-synthesized review status is acceptable only when derived from a valid instructions JSON, and dispatcher-synthesized review instructions are acceptable only when the status verdict is `approve`

When a valid done file and valid artifacts are both present, emit `STATUS|type=worker-done|issue=<n>|role=<role>|phase=<phase>|source=<agent|runner-exit>` to `$SUB_COORD_LOG_FILE` and the live status bus, then proceed to the next phase. Do not wait for unrelated CLI cleanup once the role's required artifacts are valid.

### Compact Report JSON (MANDATORY)

When the sub-coordinator reaches any terminal state (completed / failed-review / merge_failed / blocked):

1. Populate and write the report JSON:

```json
{
  "schema_version": 1,
  "issue_number": 36,
  "issue_title": "Add login endpoint",
  "outcome": "completed | failed-review | merge_failed | blocked",
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
  "review_skipped": false,
  "review_skip_reason": null,
  "token_usage": {
    "impl_input": 0, "impl_output": 0,
    "review_input": 0, "review_output": 0,
    "modify_input": 0, "modify_output": 0,
    "complexity_input": 0, "complexity_output": 0
  },
  "model_usage": [
    {
      "role": "impl",
      "cycle": 1,
      "agent": "codex",
      "model": "gpt-5.3-codex",
      "effort": "high",
      "selection_reason": "under-target"
    }
  ],
  "commit_sha": "abc1234",
  "issue_branch": "Maestro/cunning-fox-issue-36",
  "feature_branch": "Maestro/cunning-fox",
  "worktree_path": ".run-with-it/worktrees/issue-36",
  "merge": {
    "status": "completed | failed | skipped",
    "merge_sha": "abc1234",
    "pushed": true,
    "failure_reason": null,
    "conflict_files": []
  },
  "blocking_reasons": [],
  "evidence": {
    "failed_role": "impl | review | modify | null",
    "failed_cycle": 1,
    "failed_attempts": 0,
    "final_stall_reason": null,
    "state_file": null,
    "log_file": null,
    "done_file": null,
    "result_file": null,
    "dirty_worktree": false,
    "changed_files": [],
    "recovery_patch": null,
    "recovery_status": null,
    "recovery_untracked_dir": null,
    "recovery_plan": null
  }
}
```

2. Write to `$SUB_COORD_REPORT_FILE` (provided in context).
3. If `$SUB_COORD_REPORT_FILE` is missing from context, write to `$RUN_WITH_IT_ISSUE_DIR/report.json` as fallback.
4. Ensure the JSON is fully written and valid before exiting.

`model_usage` must include every worker route selected for the issue, including `artifact-recovery` when it runs. If a role is skipped, omit that role; do not invent model names.

`review_skipped` / `review_skip_reason` are set only when Step 0 skipped review (`true` / `"trivial-change"`); in that case `review_summary.reviewer_model` is `null` and `final_verdict` is `"approve"` — a skip is treated as a clean approve.

The report file is the sub-coordinator's only required artifact for the Main Orchestrator.

For `blocked` outcomes caused by worker artifact failures, `evidence` is mandatory and must be populated with concrete paths and recovery details. `recovery_plan` must be specific enough for a later Sub-Coordinator or implementation agent to resume safely without reading the raw worker logs.

## Appendix F: Status Lines

Emit parseable status messages throughout execution. Every line below MUST be written to `$SUB_COORD_LOG_FILE` using an explicit shell command. Worker stdout/stderr is mirrored by the runner to `RUN_WITH_IT_LOG_FILE` under the matching `$RUN_WITH_IT_ISSUE_DIR/workers/<role>/` directory. Also append to `$SUB_COORD_LOG_FILE`:

- `ROUTE|agent=<agent>|model=<model>|complexity_level=<level>|complexity_score=<score>|target_weight=<min>-<max>|model_weight=<n>|fallback_budget=<n>|allowlist=<value>|denylist=<value>|complexity_source=<sub-agent|fallback|override>`
- `STATUS|type=spawn|agent=<name>|issue=#<n>|phase=assigned|scope=<owned-paths>`
- `STATUS|type=agent-start|issue=<n>|role=<complexity|plan|impl|review|modify|artifact-recovery>|agent=<name>|model=<model-id>`
- `STATUS|type=worker-artifact-failed|issue=<n>|role=<impl|modify>|cycle=<n>|attempt=<n>|reason=<missing-result-artifact|invalid-result-artifact|process-exited-missing-done-or-result|alive-but-silent>|action=<retry|artifact-recovery|blocked>`
- `STATUS|type=worker-artifact-recovery|issue=<n>|role=<impl|modify>|cycle=<n>|attempt=<n>|dirty=<true|false>|patch_file=<path-or-none>|status_file=<path-or-none>`
- `STATUS|type=artifact-recovery-result|issue=<n>|failed_role=<impl|modify>|cycle=<n>|decision=<synthesized-result|requeue|blocked>|result_file=<path>`
- `STATUS|type=agent-complete|issue=<n>|role=<complexity|plan|impl|review|modify|artifact-recovery>|agent=<name>|model=<model-id>|status=<success|failed>`
- `STATUS|type=review-spawn|task=<n>|cycle=<n>|agent=<name>|model=<model-id>`
- `STATUS|type=review-result|task=<n>|cycle=<n>|verdict=<approve|revise|reject>|comment_count=<n>`
- `STATUS|type=modify-spawn|task=<n>|cycle=<n>|agent=<name>|model=<model-id>`
- `STATUS|type=review-degraded|task=<n>|reason=no-higher-band-agent`
- `STATUS|type=complexity-skipped|reason=override`
- `STATUS|type=complexity-fallback|reason=<error>|fallback=medium-hard`
- `STATUS|type=route-selected|issue=<n>|role=<complexity|plan|impl|review|modify|artifact-recovery>|agent=<name>|model=<model-id>|reason=<selection-reason>`
- `STATUS|type=route-helper-failed|issue=<n>|role=<complexity|plan|impl|review|modify|artifact-recovery>|action=prompt-fallback`
- `STATUS|type=compact|action=user-required|state_file=<path>`
- `STATUS|type=ledger|task=<task-id>|role=<impl|review|modify|artifact-recovery>|cycle=<n>|agent=<name>|model=<model-id>|added=<n>|deleted=<n>|total=<n>|reason=<short-selection-reason>|input_tokens=<n-or-unknown>|output_tokens=<n-or-unknown>|cache_hit_tokens=<n-or-unknown>|telemetry_source=<source>`

Keep `progress` values under 8 words.

## Guardrails

- Keep changes minimal and focused to the assigned issue.
- **Never pause after routing to ask the user how to proceed.** Execute via the runner immediately.
- **Never offer to implement work directly in this session.**
- **Never present execution option menus.**
- **Never run tests, build commands, or compile the project** in this session.
