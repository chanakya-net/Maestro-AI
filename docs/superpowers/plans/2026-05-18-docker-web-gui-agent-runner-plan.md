# Docker Web GUI Agent Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Docker-backed web GUI that lets a user select a code folder, install/authenticate coding agents, choose supported models, and run the AI-Skills workflow (`break-req` -> `create-git-issue` -> `run-with-it`) interactively from the browser.

**Architecture:** Use a control-plane web app to manage projects, Docker runner containers, auth flows, model discovery, and live agent sessions. Each selected project gets a runner container with the code folder mounted at `/workspace` and a persistent Docker volume mounted at `/home/agent` so agent CLI auth survives restarts. Interactive skills are bridged through a PTY/session stream so agent questions can be shown in the UI and user answers can be sent back to the running process.

**Tech Stack:** Docker, Node.js, TypeScript, React/Vite or Next.js, Express/Fastify, WebSocket or Server-Sent Events, `node-pty`, Docker Engine API, existing AI-Skills assets (`assets/run-agent.sh`, `assets/agent-registry.json`, skill markdown files).

---

## Current Findings

This repository is currently a skill collection and shared runner package, not a web app.

Existing useful surfaces:

- `assets/run-agent.sh`: common runner wrapper for invoking agents with prepared context and prompt files.
- `assets/agent-registry.json`: supported agent/model registry and runner invocation templates.
- `install.sh`: installs assets and skill registrations for detected agents.
- `skills/break-req/SKILL.md`: requirements discovery flow; interactive question/answer behavior.
- `skills/create-git-issue/SKILL.md`: turns requirements into PRD and implementation slices.
- `skills/run-with-it/SKILL.md`: execution coordinator for ready issues.

Docker auth experiments already completed:

- Codex worked in Docker when `~/.codex/auth.json`, `config.toml`, and `version.json` were copied into the container home.
- Claude did not work by mounting host macOS auth files because host auth uses macOS Keychain entries:
  - `Claude Safe Storage`
  - `Claude Code-credentials`
- Claude did work in Docker after running `claude auth login --claudeai` inside the container and persisting `/home/agent` in a Docker volume.
- The successful Docker Claude test returned:

```text
TOOL_OK_CLAUDE_DOCKER
STATUS|type=telemetry|agent=claude|model=claude-sonnet-4-6|status=success
```

Conclusion:

- Container-native auth with persistent `/home/agent` is the portable approach.
- Host auth migration can be an optimization for some agents, but must not be the primary design.

## Target User Flow

1. User starts the control app.
2. User opens the web UI.
3. User enters or selects a code folder path.
4. User chooses agents to enable:
   - Codex
   - Claude
   - GitHub Copilot
   - later: Gemini, OpenCode, others
5. Control server creates a per-project runner container.
6. Runner installs selected agent CLIs.
7. UI guides user through auth for each selected agent.
8. Control server asks runner for detected agents and supported models.
9. UI lets user select allowed agent/model pairs.
10. User chooses which agent/model should run `break-req`.
11. `break-req` asks questions in the UI.
12. User answers in the UI.
13. When requirements are complete, `technical_requirements.md` is written in the mounted project folder.
14. User chooses to run `create-git-issue`.
15. The system creates local `prd.md` and `issues.md`, or GitHub issues if GitHub auth/config is enabled.
16. User chooses to run `run-with-it`.
17. The system runs implementation slices and streams progress/status back to the UI.

## Runtime Architecture

```text
Host Machine
  selected code folder
  Docker / OrbStack / Docker Desktop
  optional host helper
        |
        v
Control App
  Web UI
  API server
  Docker manager
  session manager
  auth adapters
  log streamer
        |
        v
Runner Container per project
  /workspace                 selected code folder
  /home/agent                persistent auth/config volume
  /opt/ai-skills/assets      AI-Skills runner assets
  installed CLIs             codex, claude, gh/copilot, etc.
```

## Control App Responsibilities

The control app owns orchestration, not agent execution.

Responsibilities:

- Serve the browser UI.
- Store project records.
- Validate selected folder paths.
- Start and stop runner containers.
- Create and reuse persistent auth volumes.
- Install selected agent CLIs in the runner container.
- Run agent auth commands and stream login URLs/codes.
- Ask the runner to list detected agents and supported models.
- Start interactive agent sessions.
- Stream stdout/stderr/status events to the UI.
- Send user input from UI back to running agent processes.
- Expose workflow actions for `break-req`, `create-git-issue`, and `run-with-it`.

Non-responsibilities:

- The control app should not parse or rewrite skill behavior.
- The control app should not store raw access tokens.
- The control app should not decide model routing beyond user-selected allowlists/defaults.

## Runner Container Responsibilities

The runner container owns isolated execution.

Responsibilities:

- Mount selected code folder at `/workspace`.
- Mount persistent agent home at `/home/agent`.
- Install selected CLIs.
- Store agent auth/config under `/home/agent`.
- Run AI-Skills scripts.
- Produce logs, status lines, and artifacts.

Non-responsibilities:

- Runner should not start sibling containers.
- Runner should not expose secrets to the UI.
- Runner should not browse arbitrary host paths.

## Docker Control Options

### Option A: Control Container With Docker Socket

Run control app container with:

```bash
docker run \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /Users/chanakya/projects:/host-projects \
  -p 3000:3000 \
  ai-skills-control
```

Pros:

- Fastest prototype.
- Control app can start runner containers directly.
- Easy to test locally.

Cons:

- Docker socket access is equivalent to host-level power.
- Needs careful local-only binding and warnings.
- Not ideal for untrusted/multi-user deployment.

### Option B: Native Host Helper

Ship a small host process that:

- Opens native folder picker.
- Starts/stops Docker containers.
- Owns Docker socket access.
- Talks to the web UI/control server over localhost.

Pros:

- Better UX for folder selection.
- Safer than exposing Docker socket to a web app container.
- More product-ready.

Cons:

- More work.
- Requires platform-specific packaging.

Recommendation:

- Use Option A for prototype.
- Move to Option B before distributing broadly.

## Agent Auth Design

Auth must be adapter-based. Do not build a generic "paste credentials" box.

### Common Auth Contract

Each agent adapter should implement:

```ts
type AgentId = "codex" | "claude" | "github-copilot";

interface AgentAuthAdapter {
  agent: AgentId;
  install(): Promise<CommandResult>;
  authStatus(): Promise<AuthStatus>;
  beginLogin(): AsyncIterable<AuthEvent>;
  logout(): Promise<CommandResult>;
}

type AuthStatus = {
  installed: boolean;
  authenticated: boolean;
  displayName?: string;
  details?: Record<string, string>;
};

type AuthEvent =
  | { type: "url"; url: string }
  | { type: "code"; code: string }
  | { type: "prompt"; text: string }
  | { type: "log"; text: string }
  | { type: "success" }
  | { type: "error"; message: string };
```

### Claude Auth

Install:

```bash
npm install -g @anthropic-ai/claude-code
```

Status:

```bash
claude auth status --text
```

Login:

```bash
claude auth login --claudeai
```

Observed login behavior:

- CLI prints an auth URL.
- User opens URL in browser.
- Claude returns a code.
- UI sends code back to the container process.
- Auth persists in `/home/agent` volume.

Important:

- Do not try to decrypt or migrate macOS Keychain credentials.
- Do not rely on mounting host `~/.claude*`.

### Codex Auth

Install:

```bash
npm install -g @openai/codex
```

Status smoke check:

```bash
codex --version
```

Runner smoke check:

```bash
assets/run-agent.sh \
  --agent codex \
  --model gpt-5.3-codex-spark \
  --context-file /tmp/context.md \
  --prompt-file /tmp/prompt.md \
  --permission-mode "--sandbox=read-only" \
  --unattended
```

Auth options:

- Preferred: container-native login into persistent `/home/agent`.
- Optional: import host `~/.codex/auth.json`, `config.toml`, and `version.json` after explicit user consent.

### GitHub Copilot Auth

Install path must be verified separately.

Likely dependencies:

```bash
gh auth login
```

Possible checks:

```bash
gh auth status
copilot --version
```

Implementation should treat Copilot as a separate adapter because `gh` auth and Copilot CLI availability may differ by machine/version.

## Model Discovery

Initial implementation can use this repo's existing registry-backed listing.

Detected agents:

```bash
assets/run-agent.sh --list-agents --detected-only
```

Models for agent:

```bash
assets/run-agent.sh --list-models claude
assets/run-agent.sh --list-models codex
assets/run-agent.sh --list-models github-copilot
```

UI should display:

```text
Agent           Installed    Authenticated    Models
Claude          yes          yes              claude-sonnet-4-6, claude-opus-4-7
Codex           yes          yes              gpt-5.3-codex-spark, gpt-5.3-codex, ...
GitHub Copilot  pending      pending          pending
```

Future improvement:

- Add real CLI model probing where supported.
- Compare live models to `assets/agent-registry.json`.
- Warn when registry models are unavailable in the user's plan.

## Interactive Session Design

`break-req` needs back-and-forth interaction. The control server must run agent commands through a PTY or equivalent stream.

Recommended session API:

```http
POST /api/sessions
POST /api/sessions/:id/input
GET  /api/sessions/:id/events
POST /api/sessions/:id/stop
GET  /api/sessions/:id/artifacts
```

Session state:

```ts
type SessionStatus =
  | "starting"
  | "running"
  | "waiting_for_user"
  | "completed"
  | "failed"
  | "stopped";

type SessionRecord = {
  id: string;
  projectId: string;
  agent: string;
  model: string;
  workflow: "break-req" | "create-git-issue" | "run-with-it" | "smoke";
  status: SessionStatus;
  startedAt: string;
  completedAt?: string;
};
```

Event stream:

```ts
type SessionEvent =
  | { type: "stdout"; text: string }
  | { type: "stderr"; text: string }
  | { type: "status"; text: string }
  | { type: "question"; text: string }
  | { type: "artifact"; path: string }
  | { type: "exit"; code: number };
```

Question detection can start simple:

- Treat all stdout as transcript.
- UI always provides an input box while the process is running.
- Later add structured question extraction.

## Workflow Commands

### Smoke Test

Create `/tmp/context.md`:

```text
This is a Docker auth smoke test. Do not edit files.
Reply with exactly TOOL_OK if you can run.
```

Create `/tmp/prompt.md`:

```text
Return only the requested marker. Do not include explanation.
```

Run:

```bash
assets/run-agent.sh \
  --agent <agent> \
  --model <model> \
  --context-file /tmp/context.md \
  --prompt-file /tmp/prompt.md \
  --permission-mode safe \
  --unattended
```

Expected:

```text
TOOL_OK
STATUS|type=telemetry|agent=<agent>|model=<model>|status=success|source=runner-default
```

### Run `break-req`

Create a context file:

```text
User wants requirements discovery for the project mounted at /workspace.
Use the break-req skill.
Ask one question at a time.
Write the final requirements artifact to /workspace/technical_requirements.md.
Do not implement code.
```

Run:

```bash
REPO_ROOT=/workspace \
assets/run-agent.sh \
  --agent <agent> \
  --model <model> \
  --context-file /tmp/break-req-context.md \
  --prompt-file /opt/ai-skills/assets/prompt.md \
  --permission-mode safe \
  --unattended
```

Note:

- Depending on agent skill discovery, the prompt may need to include the contents or path of `skills/break-req/SKILL.md`.
- The first implementation can invoke the installed skill naturally and verify behavior.
- If skill discovery is inconsistent, create explicit workflow prompts that embed the skill text.

### Run `create-git-issue`

Context:

```text
The requirements artifact exists at /workspace/technical_requirements.md.
Use create-git-issue to create PRD and implementation issue slices.
Prefer local files prd.md and issues.md unless GitHub auth is configured and the user selected GitHub publishing.
Do not execute implementation.
```

Expected artifacts:

- `/workspace/prd.md`
- `/workspace/issues.md`

### Run `run-with-it`

Context:

```text
The implementation issues have been prepared.
Use run-with-it to execute ready local issues from /workspace/issues.md or GitHub if configured.
Stream progress.
Do not ask for an execution option menu after planning.
```

Expected artifacts:

- `/workspace/.run-with-it/main-state.json`
- `/workspace/.run-with-it/reports/*`
- status/log files under `/workspace/.run-with-it/`

## UI Screens

### Project Setup

Fields:

- Project name
- Code folder path
- Mount mode:
  - read/write
  - read-only smoke test

Actions:

- Validate folder
- Create runner
- Open project dashboard

### Agent Selection

Controls:

- Checkboxes for Codex, Claude, GitHub Copilot.
- Install selected agents button.
- Auth status indicators.
- Login buttons per agent.

### Auth Flow

Display:

- Current auth command.
- Login URL as clickable link.
- Code entry field when CLI asks for a code.
- Streaming logs.
- Success/failure state.

### Model Selection

Display:

- Agent table.
- Model checkboxes.
- Default model selector per workflow.

### Workflow Dashboard

Actions:

- Run smoke test.
- Run `break-req`.
- Run `create-git-issue`.
- Run `run-with-it`.

Panels:

- Transcript.
- User input box.
- Status events.
- Artifact list.
- Runner container status.

## File Structure

Recommended additions:

```text
apps/control/
  package.json
  src/
    server.ts
    docker/
      dockerClient.ts
      runnerContainers.ts
      volumes.ts
    agents/
      types.ts
      claude.ts
      codex.ts
      copilot.ts
      registry.ts
    sessions/
      sessionManager.ts
      ptySession.ts
      eventTypes.ts
    workflows/
      smoke.ts
      breakReq.ts
      createGitIssue.ts
      runWithIt.ts
    web/
      main.tsx
      App.tsx
      pages/
        ProjectSetup.tsx
        Agents.tsx
        Auth.tsx
        Models.tsx
        Workflow.tsx
      components/
        Transcript.tsx
        AgentStatusTable.tsx
        ModelPicker.tsx
        ArtifactList.tsx

docker/
  control.Dockerfile
  runner.Dockerfile
  docker-compose.dev.yml

docs/
  docker-web-gui-agent-runner.md
```

This plan document can be split later into implementation issues once the direction is approved.

## API Sketch

```http
POST /api/projects
GET  /api/projects
GET  /api/projects/:id
POST /api/projects/:id/runner/start
POST /api/projects/:id/runner/stop
GET  /api/projects/:id/runner/status

POST /api/projects/:id/agents/install
GET  /api/projects/:id/agents/status
POST /api/projects/:id/agents/:agent/login
POST /api/projects/:id/agents/:agent/logout
GET  /api/projects/:id/agents/:agent/models

POST /api/projects/:id/sessions
POST /api/sessions/:id/input
GET  /api/sessions/:id/events
POST /api/sessions/:id/stop
GET  /api/sessions/:id/artifacts
```

## Data Model Sketch

```ts
type Project = {
  id: string;
  name: string;
  hostPath: string;
  workspacePath: "/workspace";
  runnerContainerName: string;
  homeVolumeName: string;
  createdAt: string;
};

type AgentConfig = {
  projectId: string;
  agent: "codex" | "claude" | "github-copilot";
  enabled: boolean;
  installed: boolean;
  authenticated: boolean;
  selectedModels: string[];
  defaultModel?: string;
};

type WorkflowRun = {
  id: string;
  projectId: string;
  workflow: "smoke" | "break-req" | "create-git-issue" | "run-with-it";
  agent: string;
  model: string;
  status: SessionStatus;
  createdAt: string;
  completedAt?: string;
};
```

For prototype, store this in a local JSON file. For product, move to SQLite.

## Security Requirements

Must-have for MVP:

- Bind web UI to localhost by default.
- Never log raw auth tokens.
- Redact known token patterns from command output before storing logs.
- Do not mount user home directories wholesale.
- Use per-project Docker volumes for `/home/agent`.
- Add a button to delete a project's auth volume.
- Make code-folder path explicit and visible before starting runner.
- Default smoke tests to read-only permission mode.

Must-have before broader release:

- Replace Docker socket mount with a native host helper or hardened local daemon.
- Add origin checks for browser requests.
- Add CSRF protection if cookie auth is introduced.
- Add resource limits on runner containers.
- Add audit log for container starts/stops and auth events.
- Add explicit warning when enabling broad agent permissions.

## Implementation Tasks

### Task 1: Create Runner Image

**Files:**

- Create: `docker/runner.Dockerfile`
- Create: `docker/README.md`

- [ ] Create a runner Dockerfile with Node, Bash, Git, Python, `jq`, `curl`, and CA certificates.
- [ ] Copy or mount AI-Skills assets at runtime.
- [ ] Install no agents by default; install agents via control app commands.
- [ ] Verify image builds:

```bash
docker build -t ai-skills-runner -f docker/runner.Dockerfile .
```

- [ ] Verify shell tools:

```bash
docker run --rm ai-skills-runner bash -lc 'node --version && git --version && jq --version'
```

### Task 2: Create Control App Skeleton

**Files:**

- Create: `apps/control/package.json`
- Create: `apps/control/src/server.ts`
- Create: `apps/control/src/web/App.tsx`
- Create: `docker/control.Dockerfile`
- Create: `docker/docker-compose.dev.yml`

- [ ] Scaffold TypeScript server.
- [ ] Scaffold web UI.
- [ ] Add health endpoint:

```http
GET /api/health
```

- [ ] Add dev compose file exposing the control app on `localhost:3000`.
- [ ] Verify:

```bash
docker compose -f docker/docker-compose.dev.yml up --build
curl http://localhost:3000/api/health
```

Expected:

```json
{"ok":true}
```

### Task 3: Add Docker Manager

**Files:**

- Create: `apps/control/src/docker/dockerClient.ts`
- Create: `apps/control/src/docker/runnerContainers.ts`
- Create: `apps/control/src/docker/volumes.ts`

- [ ] Implement Docker Engine client.
- [ ] Create per-project volume names:

```text
ai-skills-agent-home-<project-id>
```

- [ ] Start runner with:

```text
/workspace -> selected host path
/home/agent -> persistent Docker volume
```

- [ ] Add runner status endpoint.
- [ ] Verify container starts and can see `/workspace`.

### Task 4: Add Agent Install Adapters

**Files:**

- Create: `apps/control/src/agents/types.ts`
- Create: `apps/control/src/agents/claude.ts`
- Create: `apps/control/src/agents/codex.ts`
- Create: `apps/control/src/agents/copilot.ts`

- [ ] Implement `install()` for Claude:

```bash
npm install -g @anthropic-ai/claude-code
```

- [ ] Implement `install()` for Codex:

```bash
npm install -g @openai/codex
```

- [ ] Leave Copilot as experimental until install/auth is verified.
- [ ] Stream install output to UI.

### Task 5: Add Auth Adapters

**Files:**

- Modify: `apps/control/src/agents/claude.ts`
- Modify: `apps/control/src/agents/codex.ts`
- Modify: `apps/control/src/agents/copilot.ts`
- Create: `apps/control/src/sessions/ptySession.ts`

- [ ] Run auth commands in PTY sessions.
- [ ] Detect URLs in output and emit `AuthEvent { type: "url" }`.
- [ ] Detect code prompts and allow UI input.
- [ ] Persist auth by using `/home/agent` volume.
- [ ] Verify Claude login with:

```bash
claude auth login --claudeai
claude auth status --text
```

### Task 6: Add Model Discovery

**Files:**

- Create: `apps/control/src/agents/registry.ts`
- Add API endpoints for model listing.

- [ ] Run detected-agent command:

```bash
assets/run-agent.sh --list-agents --detected-only
```

- [ ] Run model listing:

```bash
assets/run-agent.sh --list-models <agent>
```

- [ ] Display models in UI with checkboxes.
- [ ] Persist selected models per project.

### Task 7: Add Smoke Test Workflow

**Files:**

- Create: `apps/control/src/workflows/smoke.ts`
- Modify: workflow dashboard UI.

- [ ] Generate temporary context and prompt files inside runner.
- [ ] Run `assets/run-agent.sh`.
- [ ] Stream output to UI.
- [ ] Mark success when expected marker appears.

### Task 8: Add Interactive `break-req`

**Files:**

- Create: `apps/control/src/workflows/breakReq.ts`
- Modify: `apps/control/src/sessions/sessionManager.ts`
- Modify: workflow dashboard UI.

- [ ] Start selected agent/model in a PTY session.
- [ ] Provide context that instructs the agent to use `break-req`.
- [ ] Show transcript in UI.
- [ ] Allow user answers while session runs.
- [ ] Verify `technical_requirements.md` appears in `/workspace`.

### Task 9: Add `create-git-issue`

**Files:**

- Create: `apps/control/src/workflows/createGitIssue.ts`

- [ ] Start selected agent/model.
- [ ] Provide context pointing to `/workspace/technical_requirements.md`.
- [ ] Prefer local artifacts for MVP.
- [ ] Verify `/workspace/prd.md` and `/workspace/issues.md`.

### Task 10: Add `run-with-it`

**Files:**

- Create: `apps/control/src/workflows/runWithIt.ts`

- [ ] Start selected agent/model.
- [ ] Provide context pointing to local issues or GitHub issues.
- [ ] Stream status lines.
- [ ] Show `.run-with-it` artifacts in UI.
- [ ] Add stop button with clear warning.

### Task 11: Harden Security

**Files:**

- Modify: server middleware.
- Modify: Docker manager.
- Add: log redaction utility.

- [ ] Bind control server to `127.0.0.1` by default.
- [ ] Add token redaction before log persistence.
- [ ] Add runner resource limits.
- [ ] Add delete-auth-volume endpoint.
- [ ] Add visible warning for Docker socket mode.

## Testing Plan

Manual tests for MVP:

1. Start control app.
2. Add project path.
3. Start runner.
4. Install Claude.
5. Run Claude login.
6. Complete browser auth.
7. Confirm `claude auth status --text`.
8. Run smoke prompt and confirm marker.
9. Install Codex.
10. Login/import Codex auth.
11. Run smoke prompt and confirm marker.
12. List models.
13. Start `break-req`.
14. Answer at least two questions.
15. Confirm `technical_requirements.md`.

Automated tests:

- Unit test path validation.
- Unit test Docker volume naming.
- Unit test URL/code extraction from auth output.
- Unit test registry model parsing.
- Integration test runner start/stop with a temp workspace.
- Integration test smoke workflow using a fake agent command.

## Open Questions

- Should the prototype live inside this repo or a separate app repo?
- Should local artifact mode be the default even when GitHub auth exists?
- Should runner containers be one per project or one shared runner per user?
- Should `/workspace` be read/write by default, or should the UI require explicit write access?
- Should Codex auth import from host be allowed, or should all agents use container-native login?
- Which Copilot CLI package/path should be supported first?

## Recommended MVP Cut

Build the first version with only:

- Control app Docker socket mode.
- Manual folder path input.
- One runner container per project.
- Claude + Codex only.
- Container-native auth only.
- Model listing from `assets/agent-registry.json`.
- Smoke test.
- Interactive `break-req`.

Do not include in MVP:

- Native host helper.
- GitHub issue publishing.
- Copilot.
- Multi-user accounts.
- Cloud deployment.
- Automatic host credential import.

## Acceptance Criteria

The MVP is complete when:

- A user can start the control app.
- A user can enter a local project path.
- The app can start a runner container with that path mounted.
- The app can install Claude and Codex in the runner.
- The app can authenticate Claude inside the runner using browser-code login.
- The app can authenticate Codex inside the runner.
- The app can list detected agents.
- The app can list models for each detected agent.
- The app can run a smoke prompt successfully.
- The app can run `break-req` interactively through the UI.
- The app writes `technical_requirements.md` into the selected project folder.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-18-docker-web-gui-agent-runner-plan.md`.

Two execution options:

1. **Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.

