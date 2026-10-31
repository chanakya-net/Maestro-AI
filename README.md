# AI-Skills

AI-Skills coordinates coding agents through dependency-aware issue scheduling, isolated Git worktrees, model routing, review loops, artifact recovery, and merge recovery. Seven skills guide requirements discovery, issue creation, implementation, debugging, and paired design discussions.

The maintained runtime is Python on Windows, macOS, and Linux. The same commands and JSON configuration replace the former Bash and PowerShell entry points.

## Requirements

- Python 3.12 or newer and Git.
- A supported coding agent CLI for execution: Codex, Claude Code, Gemini/Antigravity, Agy, or OpenCode. Install and authenticate the providers you intend to use. A provider distributed through npm still requires Node.js.
- Authenticated GitHub CLI (`gh`) for GitHub issue and PR operations. Local planning and runtime diagnostics do not require GitHub access.

The registry defines supported models and availability. Copilot runtime selection remains disabled while its configured plan is exhausted. The installer does not install Python, Node.js, or providers, and does not broaden their permissions.

## Installation

Clone this repository and create a Python environment. On macOS/Linux, select an installed Python 3.12 or newer:

```text
python3.12 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m ai_skills install --source . --dry-run
.venv/bin/python -m ai_skills install --source .
```

On Windows, bootstrap with the Python launcher and use the environment's interpreter:

```text
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\python.exe -m ai_skills install --source . --dry-run
.venv\Scripts\python.exe -m ai_skills install --source .
```

After selecting your environment's Python, every runtime command below is identical on all three systems. `python -m ai_skills` and the installed `ai-skills` console command are equivalent.

Installation packages the maintained prompts, registry, and all seven skills into an immutable runtime under `~/.ai-skill-collections/assets/runtimes/`. It publishes `runtime.json` only after verification. Use `--assets-dest PATH` to select another destination and `--only codex` (or another listed profile) to limit registrations. `install --list` lists profiles. A built wheel can replace `.` in `--source`.

Existing unowned skill directories are protected. Inspect the dry-run output, then use `--replace-existing` to migrate an older installation explicitly; the installer preserves backups and records ownership. Registration uses local packaged content rather than fetching a changing branch.

```text
python -m ai_skills doctor --json
python -m ai_skills agent list
python -m ai_skills uninstall --dry-run
python -m ai_skills uninstall
```

Uninstall removes owned registrations and restores preserved originals when safe. Edited or unrecognized content is preserved. Runtime versions are retained because other runs may still pin them; inspect the reported retained paths before any manual cleanup.

## Skills

| Skill | Purpose |
| --- | --- |
| `break-req` | Discover requirements, constraints, and dependencies. |
| `create-git-issue` | Turn resolved requirements into a PRD and implementation issues. |
| `run-with-it` | Coordinate a rolling pool of issue workers and produce a final PR. |
| `pair-colleague` | Run two independent solution colleagues through bounded discussion. |
| `help-me-debug` | Collect evidence and diagnose failures before implementation. |
| `tdd-implementation` | Implement with behavior-focused red/green/refactor cycles. |
| `save-tokens` | Use compressed communication when requested. |

Invoke skills inside your coding agent using its normal skill mechanism. A typical workflow is `break-req` → `create-git-issue` → `run-with-it`. Existing ready-for-agent issues can enter directly at `run-with-it`. The skill instructions define the required artifacts, authority boundaries, verification, and final PR flow.

## Runtime commands

| Command | Responsibility |
| --- | --- |
| `agent run`, `agent list`, `agent models` | Provider invocation, detection, and model discovery. |
| `worker dispatch`, `worker inspect`, `worker emit` | Durable worker execution, inspection, and structured status events. |
| `run pool`, `run watch`, `run stop` | Dependency scheduling, event observation, and owned process shutdown. |
| `state` | Transactional issue and coordinator state operations. |
| `route` | Registry-driven model selection and budget policy. |
| `artifact` | Validate and recover role-specific artifacts against Git evidence. |
| `git prepare-issue`, `git merge-issue` | Prepare resumable issue worktrees and verify disposable integration worktrees. |
| `github update` | Apply authorized issue progress updates through `gh`. |
| `report pr` | Render the final PR description from persisted results. |
| `pair` | Start, continue, inspect, and finalize a paired discussion. |

Use `python -m ai_skills COMMAND --help` for exact arguments. There is no shell selection or PowerShell parameter variant.

Workers accept JSON configuration through `worker dispatch --config PATH`. Pools accept a versioned configuration through `run pool --config PATH`; configuration records use `schema_version: 1`. The orchestration skill creates the main state and context files before launching the pool. A typical configured run is observed and stopped with:

```text
python -m ai_skills run pool --config pool.json
python -m ai_skills run watch --events-log .run-with-it/status/events.log --pool-state-file .run-with-it/main/pool.state.json --wait-seconds 30
python -m ai_skills run stop --run-root .
python -m ai_skills state status-board --state-file .run-with-it/main-state.json
```

`git merge-issue` accepts `--verification-file` containing a JSON list of argument lists, for example `[["python", "-m", "pytest", "-q"]]`. Commands run without a shell. Supply the project's actual verification commands; the default Git whitespace check alone does not establish project correctness. Pushing requires explicit `--push`.

Provider selection follows explicit configuration, environment overrides, then registry defaults. `SUB_COORD_AGENT`/`SUB_COORD_MODEL` select coordinator execution; `FORCED_AGENT`/`FORCED_MODEL` constrain worker routing independently. Deprecated ambient `AGENT`/`MODEL` aliases remain available for direct agent execution, while dispatchers clear them before launching providers. Credentials are inherited at execution time and are not serialized into runtime records.

## Recovery and upgrades

A pool records its Python engine and interpreter in `.run-with-it/engine.json`; pair runs pin their engine in `config.json`. Commands select that interpreter before parsing version-specific arguments. Installing a newer default does not move an existing run to another runtime. Missing or incompatible pinned runtimes fail explicitly.

Finish or stop legacy runs before migrating. Active legacy state is refused rather than adopted into an incompatible engine. The Python runtime preserves existing state schema contracts and supports guarded imports of quiescent pair runs. Historical design documents describe earlier implementations; executable instructions in `skills/` and this README describe the current runtime.

State updates use atomic replacement and ownership-aware directory locks. Process records include creation identity, allowing a restarted controller to distinguish owned workers from recycled PIDs. On POSIX the runtime owns a process session; deliberately detached descendants that create a new session can escape that boundary. On Windows, suspended launch into a kill-on-close Job Object prevents the normal child-launch race. Consoleless Windows shutdown terminates the owned job. Use a local filesystem supporting atomic creation and rename for runtime state.

## Development

```text
python -m pip install -e ".[dev]"
python -m pytest -q
python -m build
```

CI runs the native Python suite on Windows, macOS, and Linux with Python 3.12 and 3.15. Tests use fixture providers and temporary repositories, including process recovery, scheduling, artifact policy, installation, and wheel resources. Provider credentials and paid requests are unnecessary for the suite. Live provider authentication and interactive registration remain environment-specific checks.

Generated logs, run state, virtual environments, build outputs, and local migration reports are ignored. The maintained source lives in `src/ai_skills/`; provider prompts and routing metadata live in `assets/`. The small numerical example is `examples/add_two_numbers.py`.

See [explainer.html](explainer.html) for the workflow walkthrough and [LICENSE](LICENSE) for the AGPL-3.0 license.
