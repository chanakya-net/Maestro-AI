# Why I Built AI-Skills and How the Workflow Works

> Working title direction: Why I Built AI-Skills and How the Workflow Works

This scaffold establishes the canonical document shape and labeled screenshot slots so later slices can fill content without changing the outline.

---

## 1. Intro / why I built this

I pay for Claude Opus. I also subscribe to Gemini, Copilot, and access other models for specific tasks. The problem wasn't the subscriptions—it was what I did with them.

I'd start most work on Opus. The model is powerful and useful for thinking through hard problems, exploring unfamiliar domains, and planning projects. That's exactly what I should be using an expensive model for. But here's what actually happened: once a session was productive, inertia took over. I'd keep using the same model for implementation, testing, and follow-up tasks—work that a cheaper or more specialized model could handle just fine. The result was the opposite of efficiency: I was burning premium tokens on straightforward implementation because the exploration happened to start in that session.

Sometimes I'd try to fix it manually. I'd export conversation history, copy context into a different tool or session, and start over. This felt worse. Workflow continuity broke. I'd lose thread-of-thought. I'd re-explain context. I'd end up wasting time re-doing work the first model had already scoped out. The manual handoff usually cost more effort than the token savings were worth.

This is the inefficiency I built AI-Skills to solve. The repo exists to make model and agent choice a deliberate routing decision, not a session accident. Once you've explored a problem and understood what needs to happen, the system should route you to the right tool for execution—and that tool might be cheaper, faster, or better-fit than the one you started with.

> [SCREENSHOT 1 — Problem exploration context (placeholder)]

---

## 2. The problem with one-session, one-model workflows

Single-session, single-model work creates a false economy. Here's what goes wrong:

**The lock-in pattern.** Once you've invested exploration and context-building time in a session, switching feels expensive. The premium model now "knows" the problem. Starting over means re-explaining. Re-explaining feels inefficient relative to just staying put. So you stay put—even when the remaining work doesn't warrant the cost.

**The three inefficiency outcomes.** First, you keep the expensive model and accept the waste. Second, you manually copy context to another tool or session, lose continuity, and often redo work. Third, you use a tool that's sub-optimal for the implementation phase because it's where the conversation happened to live. All three outcomes are worse than the alternative: knowing upfront which tool to use for each phase.

**Context fragmentation.** A single session isn't designed to be the permanent store for a complex project. Long conversations become hard to reference. You can't easily branch from a decision point. You can't hand off context to someone else without copying it manually. The session becomes brittle.

**Tooling mismatch.** The model that's best for exploration isn't always best for execution. Some models excel at asking clarifying questions and synthesizing messy input. Others are stronger at writing specific code patterns or following strict implementation specs. A single-session workflow forces you to choose one model for both—which means you're suboptimal at one task or the other, or both.

---

## 3. The repo idea: separate thinking, planning, and execution

The repo turns model selection into a deliberate choice by separating concerns. Instead of one session doing exploration and implementation, the workflow breaks into distinct phases with explicit handoffs.

**Thinking phase.** Explore the problem, understand constraints, ask clarifying questions. This phase benefits from a powerful model that thinks through complexity. Preserve the output as structured requirements, not as a conversation thread.

**Planning phase.** Take the requirements and break them into work: small, vertically-sliced tasks with clear success criteria, routing hints, and implementation notes. This phase is about organization and clarity, not just raw reasoning power. A good planner can work at different scales.

**Execution phase.** Implement the sliced tasks with the model and tool best suited to each one. A straightforward UI task might route to a faster, cheaper model. A subtle auth refactor might route to a more capable one. The system makes that choice based on task complexity and available tools, not based on convenience.

This separation means each phase gets a tool optimized for its job. It also means you can reinvest savings from cheap implementation tasks into harder discovery phases where premium reasoning is worth the cost. The workflow becomes economical by default instead of economical by accident.

The repo implements this vision through a set of skills—tools that guide the workflow—and a routing system that selects the right model and agent for each task. The rest of this article explains how that works in practice.

---

## 4. The canonical sequence: `break-req` -> `create-git-issue` -> `run-with-it`

The canonical path is intentionally simple:

1. `break-req` turns messy input into structured requirements.
2. `create-git-issue` turns those requirements into an issue-ready execution plan.
3. `run-with-it` executes the sliced work through runtime-capable skills and enforces final routing.

Think of it as a pipeline where every stage has one job and one explicit output:

- `break-req` = discovery and scoping. It translates ambiguous requests into a `technical_requirements.md` artifact. It is requirements-only by design and **stops there**: it should not commit to issue IDs, slice assignment, code changes, or runtime routing.
- `create-git-issue` = planning and decomposition. It consumes `technical_requirements.md`, publishes a PRD, and adds thin dependency-aware slices. It does routing suggestions, but it is advisory only and does not make final model/agent selection decisions.
- `run-with-it` = execution and final dispatch. It reads the PRD + slices, selects the right model and runtime skill for each slice, and drives implementation. For this workflow, it is the final routing authority.

The flow is intentionally gated:

- If `break-req` cannot produce stable requirements, the path should pause before `create-git-issue`.
- If planning shows unclear ownership or invalid sequencing, `create-git-issue` should pause with a clear dependency fixup request.
- If a slice is not suitable for the current runtime context, `run-with-it` adjusts routing rather than bypassing boundaries.

This is not a rigid one-size-fits-all pipeline; it is a default route to prevent accidental model lock-in and preserve a clean separation between thinking, planning, and execution.

> [SCREENSHOT 2 — Canonical workflow: staged flow (placeholder)]

---

## 5. What each current skill does

The shipped skills are the current execution surface, each with a narrow scope:

### `break-req`
**Role:** requirements extraction and decomposition.

`break-req` is for requirement discovery when a problem is still ambiguous. It asks questions when needed, resolves constraints, and outputs a `technical_requirements.md`-style structure. It exists so high-cost reasoning is spent where it has best return: understanding the work before execution starts. Its stop point is hard and explicit: when requirements are stable, hand off.

### `create-git-issue`
**Role:** PRD generation and slice materialization.

`create-git-issue` converts validated requirements into a PRD and emits thin, dependency-aware slices that are directly runnable. It provides routing hints for each slice, but it cannot finalize model/agent choices; that authority intentionally stays with `run-with-it`.

### `run-with-it`
**Role:** runtime orchestration and final routing authority.

`run-with-it` is the execution gate. It owns final dispatch decisions, enforces slice sequencing, coordinates tools/subagents, and executes using the most fitting runtime model for each slice. In this architecture, it is where the workflow becomes operational.

### `save-tokens`
**Role:** context compression and token efficiency.

`save-tokens` is a supporting skill for continuity and cost control. It helps compress prompts and state before/while passing between phases. It is not a planning or routing authority; it improves handoff quality and runtime efficiency.

### `tdd-implementation`
**Role:** implementation quality enforcement.

`tdd-implementation` is the quality-focused execution skill. It applies red-green-refactor discipline and drives work through thin vertical slices, so each implementation increment has behavior covered at the right level before moving on.

> [SCREENSHOT 3 — Skill overview: repo structure / skills directory (placeholder)]

---

## 6. The supporting mechanics behind the scenes

The routing logic lives in `assets/agent-registry.json`. It is not a hardcoded switch—it is a data-driven table the runner reads at execution time. Understanding its structure explains why routing produces the choices it does.

### The registry: what it contains

The registry has four main sections:

1. **`model_catalog`** — every model the system knows about, with `complexity_weight` (1–10), `price_tier`, `price_input_per_1m`, `price_output_per_1m`, `context_window`, and a `strengths` list.
2. **`model_routing`** — the score-to-weight band table, selection strategy, provider routing rules, hard minimum overrides, and band-required pinned models.
3. **`agents`** — five agents (`claude`, `codex`, `github-copilot`, `gemini`, `opencode`), each with detection commands, invocation templates, permission modes, default models, and fallback ordering.
4. **`aliases`** — human-friendly shorthands that map to canonical agent names (e.g., `claude-code` → `claude`).

### Complexity scoring and score-to-weight mapping

Every slice that reaches `run-with-it` is scored for complexity. The score is a holistic estimate of scope, cross-file impact, ambiguity, and risk—not a single measurement. The `score_to_weight` table translates that score into a model weight range:

| Score range | Label | Weight range |
|-------------|-------|-------------|
| 8–12 | quite-easy | 1–3 |
| 13–17 | easy | 2–4 |
| 18–22 | medium | 4–6 |
| 23–27 | medium-hard | 6–7 |
| 28–32 | complex | 7–9 |
| 33–40 | holy-fuck | 9–10 |

The weight range then filters the model catalog: only models whose `complexity_weight` falls within the band are eligible candidates.

Hard minimum overrides can raise the floor. If a slice has unknown dependency state, heavy shared-file conflicts, or a broad cross-module integration change, the minimum weight is raised to 9 regardless of the scored band. This prevents cheap models from receiving work they structurally cannot complete safely.

### Model selection strategy

Once the eligible model pool is established, the selection strategy is `top-n-cheapest-random`:

1. Sort all eligible models by `complexity_weight ASC`, then `price_tier ASC`, then `price_output_per_1m ASC`.
2. Take the top 4 candidates.
3. Pick one at random from that pool.

The sort puts cheapest-for-the-required-capability first. The random pick prevents every task from routing to a single model, spreading load across providers and reducing dependency on any one endpoint. The pool cap keeps the random draw bounded and economical.

At the `complex` and `holy-fuck` bands, `gpt-5.5` is pinned into the pool regardless of price rank. This ensures frontier GPT options remain available at the highest complexity even when cheaper models dominate the cost-sorted positions.

### Installed-agent constraints

Before model selection, the runner detects which agents are actually installed by running each agent's detection command (`codex --version`, `claude --version`, etc.). If an agent is not detected, all models that depend on it are filtered from the pool entirely. You cannot be routed to `claude-opus-4-7` through the `claude` agent if `claude` is not installed.

This makes routing environment-aware rather than environment-agnostic. The registry describes what is possible; the runner trims it to what is available.

### Provider routing rules and Gemini last-resort handling

Google/Gemini models have a special rule in `provider_routing_rules`:

```json
"google": {
  "max_band": "medium",
  "automatic_routing": "last_resort_only"
}
```

This means Gemini models are excluded from normal automatic routing. The runner will not place them in the candidate pool unless all eligible non-Google routes are unavailable or have failed. They can still be used if the user explicitly sets an `AGENT` or `MODEL` override, but the system will not route to Gemini automatically when alternatives exist.

### Interchangeable agent groups

`codex` and `github-copilot` run the same underlying GPT models through different CLI surfaces. The registry marks them as interchangeable: when both are installed and eligible, one is picked at random. The router does not treat the agent surface as a quality differentiator between these two—it treats them as equivalent delivery mechanisms for the same models.

### The runner's role

`assets/run-agent.sh` is the execution layer. It reads the registry, applies the selection logic above, builds an execution prompt by merging the issue context payload with `assets/prompt.md` (implementation-only guidance), and invokes the selected agent. It supports `--list-agents` and `--list-models` for introspection, resolves GUI-safe permission modes when not running in a terminal, and emits token telemetry after each run.

The key principle: `run-agent.sh` is a deterministic control plane. Given the same registry, the same installed agents, and the same slice score, it will make the same class of routing decisions—though the final model pick within the pool is random.

> [SCREENSHOT 4 — Routing: registry/routing content or output example (placeholder)]

---

## 7. Example: small task vs complex task

Two concrete examples show how the routing economics work in practice.

### Small task: fix a broken doc comment

**Slice description:** A doc comment in a single utility function references a parameter that was renamed in a recent refactor. The comment needs updating to match the current parameter name.

**Routing walk-through:**

1. `run-with-it` scores the slice. Single file, no logic change, no cross-module dependency, unambiguous scope. Score: ~10 (quite-easy band, weight 1–3).
2. No hard minimum overrides apply—no shared-file conflicts, no unknown dependencies.
3. Eligible models from the catalog with `complexity_weight` 1–3: `claude-haiku-4-5` (w=2), `gpt-5-mini` (w=2), `gemini-3.1-flash-lite-preview` (w=3).
4. Gemini is excluded from automatic routing (last-resort-only rule), so `gemini-3.1-flash-lite-preview` is filtered out unless no other candidates exist.
5. Top 4 from remaining: `claude-haiku-4-5` ($0.001/1K input, $0.005/1K output), `gpt-5-mini` ($0.00025/1K input, $0.002/1K output).
6. Runner picks randomly from that pool. The task runs, the comment is fixed, telemetry is emitted.

**What this achieves:** A trivial maintenance task routes to the cheapest appropriate model. Using Claude Opus for a doc fix would cost roughly 5–25× more than using Haiku or GPT-5-mini with no quality difference for this scope.

### Complex task: refactor auth middleware across multiple modules

**Slice description:** The existing session token storage in auth middleware does not meet updated compliance requirements. The refactor touches the middleware, session management, and the integration tests that verify token behavior. The change must not break any callers and must satisfy the compliance constraint.

**Routing walk-through:**

1. `run-with-it` scores the slice. Multiple files, cross-module change, compliance constraint, high regression risk, shared-file ownership. Score: ~35 (holy-fuck band, weight 9–10).
2. Hard minimum override applies: broad cross-module integration change → `weight_min=9`. Score and override agree.
3. Eligible models with `complexity_weight` 9–10: `claude-opus-4-6`, `claude-opus-4-7`, `claude-opus-4-7[1m]`, `gpt-5.4`, `gpt-5.5`, `gemini-3.1-pro-preview`, plus several variants.
4. `gpt-5.5` is pinned into the pool for the `holy-fuck` band (band-required model).
5. Gemini is again filtered unless last-resort.
6. Top 4 cost-sorted from non-Google: `gpt-5.4` ($2.50/1M in, $15/1M out), `claude-opus-4-7` ($5/1M in, $25/1M out), `gpt-5.5` ($5/1M in, $30/1M out, pinned), `claude-opus-4-6` ($5/1M in, $25/1M out).
7. Runner picks randomly from those four. Task runs with full context payload.

**What this achieves:** A compliance-critical multi-module refactor gets a frontier model. The routing system does not try to save money by sending this to Haiku—the scoring and weight system prevent it. At the same time, it doesn't automatically use the single most expensive model; it picks randomly from a qualified frontier pool, which distributes cost and load across providers.

**The key contrast:** Both tasks receive models that are appropriate for their complexity. The small task avoids premium token spend on work that doesn't require it. The complex task avoids cheap models on work that could break production. The routing table enforces the economics that manual session choices tend to undermine.

---

## 8. What works today

These behaviors are shipped, stable, and documentable as reliable paths:

**Registry-driven routing.** `assets/agent-registry.json` is the authoritative source of truth for models, weights, agents, and routing rules. Any change to routing behavior is a change to this file.

**Score-to-weight band filtering.** Six bands from `quite-easy` (score 8–12, weight 1–3) through `holy-fuck` (score 33–40, weight 9–10). Each band narrows the eligible model pool to those capable of the work.

**Top-n-cheapest-random selection.** Sort candidates by cost within the eligible weight range, take the top 4, pick one randomly. Produces cost-efficient routing with provider spread.

**Hard minimum overrides.** Four override conditions (unknown dependency state, shared-file conflicts, broad cross-module change, explicit deep/complex request) floor the weight at 7 or 9, preventing under-routing of high-risk slices.

**Band-required pinning.** `gpt-5.5` is pinned into the candidate pool for `complex` and `holy-fuck` bands, guaranteeing a frontier GPT option is always available at the highest complexity levels.

**Google/Gemini last-resort constraint.** Gemini models are excluded from automatic routing unless all non-Google options are unavailable or have failed. They remain accessible via explicit `AGENT`/`MODEL` overrides.

**Installed-agent detection.** The runner probes each agent with its detection command before building the candidate pool. Models tied to uninstalled agents are filtered from routing candidates automatically.

**Interchangeable Codex/Copilot routing.** When both `codex` and `github-copilot` are installed and eligible, the runner picks between them randomly. They run the same GPT models through different surfaces and are treated as equivalent.

**GUI-safe permission mode resolution.** When not running in a terminal (e.g., invoked from a GUI), the runner selects permission modes that do not require interactive approval prompts.

**`--list-agents` and `--list-models` introspection flags.** The runner can report which agents are installed and which models are available without executing a task.

**Token telemetry emission.** The runner emits per-run token usage data after each execution, providing cost visibility across routing decisions.

**The canonical three-step workflow.** `break-req` → `create-git-issue` → `run-with-it` is the documented and tested path. Each step has a defined input, a defined output, and a hard stop at its phase boundary.

> [SCREENSHOT 5 — Practical usage: command/prompt flow or issue handoff (placeholder)]

---

## 9. Where this is going

The system has a working foundation. The directions below are real planned investments—but none of them are shipped yet, and this section describes intent, not commitments.

**More precise complexity scoring.** The current complexity score is a holistic estimate made by `run-with-it`. There is no formal rubric or automated measurement behind it. A future direction is to make scoring more systematic: looking at file count, dependency graph depth, test coverage gaps, and issue history, rather than leaving it entirely to per-run judgment.

**Cross-agent context handoff.** Today, each run starts with a fresh context assembled from the issue payload and `prompt.md`. If a task runs on Codex and follow-up work routes to Claude, there is no automated handoff of the prior agent's reasoning or partial output. The roadmap includes a structured context bundle format that can survive the agent boundary.

**Budget tracking and cost estimation.** Token telemetry is emitted today but not aggregated or surfaced. A planned addition is a lightweight cost ledger: a per-project spend view that shows how much has been spent, which bands it came from, and what the routing breakdown looks like across agents and models.

**OpenCode as a first-class agent.** `opencode` is already in the registry with a detection stub and invocation template, but it requires a user-configured model to route and is marked `skip_when_unconfigured`. The intent is to bring it into regular rotation once the configuration path is documented and the model support is stable.

**Better multi-agent coordination.** The current runner runs one agent per slice. Coordinating parallel agents—for instance, running documentation generation in parallel with implementation—is a planned capability that the current runner architecture does not yet support.

**Routing feedback loop.** There is no mechanism today for a completed run to influence future routing decisions. If a task at a given score band consistently fails or requires re-runs, the system has no way to learn that. A longer-term direction is to record run outcomes and let them inform minimum weight floors for recurring slice patterns.

---

## 10. Current limitations and honest gaps

This section is intentionally honest about what works today, what is evolving, and what remains roadmap. The goal is to prevent confusion between shipped features and future intent.

### What works today
- Registry-driven routing using `assets/agent-registry.json` is authoritative for model/agent selection.
- Complexity scoring and band filtering are enforced for every slice.
- The canonical workflow (`break-req` → `create-git-issue` → `run-with-it`) is stable and tested.
- Hard minimum overrides and band-required pinning are active.
- Google/Gemini models are last-resort only unless explicitly selected.
- Installed-agent detection and interchangeable Codex/Copilot routing are enforced.
- Token telemetry is emitted per run.
- Screenshot placeholders are present, but no screenshots are shipped yet.

### What is evolving
- Complexity scoring is currently holistic and manual; a more systematic rubric is planned.
- No automated cross-agent context handoff exists; each run starts with a fresh context.
- Token telemetry is not yet aggregated into a project-level cost ledger.
- OpenCode agent is present in the registry but not yet a first-class, fully supported agent.
- Multi-agent parallel execution is not yet supported; all slices run sequentially.
- No routing feedback loop exists; run outcomes do not yet influence future routing decisions.

### What remains roadmap
- Automated, rubric-based complexity scoring.
- Structured context bundles for agent handoff.
- Project-level cost tracking and reporting.
- Full OpenCode agent support and documentation.
- Parallel agent coordination for multi-slice runs.
- Adaptive routing based on historical run outcomes.

> [SCREENSHOT 6 — Limitations/roadmap diagram or placeholder (optional)]

---

## 11. How to use this repo in practice

This section provides practical guidance for using the canonical workflow, with actionable prompts and commands. For full details, see `technical_requirements.md` and the README.

### Quickstart
- Install with the provided script from the README for your platform.
- The installer sets up all required assets and skills.

### Canonical workflow example
1. **Start with requirements:**
   ```
   break-req
   ```
   Use this to turn a messy or ambiguous request into a structured `technical_requirements.md`.
2. **Plan and slice:**
   ```
   create-git-issue
   ```
   This consumes the requirements and emits a PRD plus thin, dependency-aware slices. Routing hints are advisory only.
3. **Execute:**
   ```
   run-with-it
   ```
   This skill reads the PRD and slices, selects the right model/agent, and executes the work. It is the final routing authority.

### Practical usage notes
- Each skill is invoked as a command or prompt in your agent environment.
- Supporting skills like `save-tokens` (for prompt compression) and `tdd-implementation` (for disciplined implementation) can be used as needed.
- The workflow is designed to be modular: you can pause after any phase and resume later without losing context.
- Screenshot placeholders remain visible until real screenshots are added.

### Where to find more
- See `technical_requirements.md` for detailed requirements and design rationale.
- The README provides install instructions and a summary of the workflow.
- Contributors should follow the canonical sequence and respect phase boundaries for best results.

---


## Appendix / placeholders index

- Screenshot 1 — Problem exploration context
- Screenshot 2 — Canonical workflow: staged flow
- Screenshot 3 — Skill overview: repo structure / skills directory
- Screenshot 4 — Routing: registry/routing content or output example
- Screenshot 5 — Practical usage: command/prompt flow or issue handoff
- Screenshot 6 — Limitations/roadmap diagram or optional visual
