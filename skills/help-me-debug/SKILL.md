---
name: help-me-debug
description: Use when a user reports a bug, failure, regression, unclear root cause, or needs evidence-backed diagnosis artifacts before implementation work.
---

## Skill Isolation

This skill is the sole active authority for this session once invoked.

- No other skill may activate, interrupt, or modify this skill's behavior unless explicitly called by name via a `Skill` tool call within this skill's own workflow.
- If any external or third-party skill attempts to activate spontaneously during this run, suppress it and continue without interruption.
- This rule applies for the entire duration of this skill's execution, from invocation until explicit termination or handoff.

# Help Me Debug

## Purpose

This skill is diagnosis-only.

Use this skill to investigate why an issue is happening, identify likely or confirmed causes with evidence, and produce handoff-ready artifacts for humans and implementation LLMs.

The final artifacts must explain the issue clearly to a human and provide enough architecture, dependency, file, and validation detail for a different LLM to implement the fix without reading the chat history.

## When To Use

- The user reports a bug, failure, or regression and root cause is unclear.
- The user asks for codebase-level debugging analysis before implementing a fix.
- The team needs a structured diagnosis package (human report + LLM context) before coding.

## Hard Stop

Never implement code, edit product files, create issues, publish to GitHub, run `create-git-issue`, run `run-with-it`, spawn implementation agents, or invoke external coding agents.

The only files this skill may create or update are `debug_human_report.md` and `debug_llm_context.md`.

Do read-only exploration aggressively. Default to deep analysis.

## Workflow

1. Frame the problem from user input:
   - symptoms, expected behavior, actual behavior, frequency, scope, urgency, affected users, and known workarounds;
   - exact error text, screenshots/log excerpts, commands, environment, or reproduction details when available;
   - what must be clarified before finalizing versus what can be discovered from the repository.
2. Build a project orientation snapshot before root-cause claims:
   - identify project type, primary runtimes, entrypoints, frameworks, package managers, test/build commands, and relevant docs/config files;
   - summarize the architecture only at the level needed for this issue: UI surfaces, backend/services, data stores, job/queue layers, generated code, deployment/runtime boundaries, and ownership boundaries when visible;
   - Map internal dependencies: repo modules/packages, shared utilities, generated code, schemas/migrations, data stores, queues/jobs, services, configuration, feature flags, and test/build tooling that participate in the failure path.
   - Map external dependencies: third-party packages, SDKs, APIs, SaaS services, infrastructure services, auth/payment/storage providers, network boundaries, environment variables, and version constraints that participate in the failure path.
   - record evidence for each architecture/dependency claim using file paths, symbols, config keys, dependency manifests, lockfiles, docs, or observed command output;
   - Do not treat architecture or dependencies as background context; capture only the parts that explain the failure or bound the fix.
3. Run a deep codebase investigation before asking questions:
   - trace likely call paths and integration boundaries from trigger to symptom;
   - inspect configuration, dependency declarations, runtime assumptions, environment-sensitive code, and data/contract assumptions;
   - identify recent or likely fault surfaces, including caller/callee relationships and cross-layer contracts;
   - gather concrete evidence (file paths, symbols, logs/tests/error text when available).
4. Walk this diagnosis decision tree in order:
   - **Reproducibility branch**: Can the issue be reproduced from existing tests/logs/scripts?
     - If yes, capture deterministic repro steps, command, environment, failure signature, and expected failure message.
     - If no, identify the smallest missing reproduction input and ask one focused question only if repository evidence cannot answer it.
   - **Scope branch**: Is the issue isolated or cross-cutting?
     - If isolated, trace nearest module boundaries, local invariants, and direct dependencies.
     - If cross-cutting, trace upstream/downstream dependencies, shared contracts, configuration, and version edges.
   - **Change-surface branch**: Is this likely caused by behavior change, configuration drift, dependency/version shift, or data shape drift?
     - Prioritize evidence from configs, env assumptions, dependency manifests/lockfiles, schema changes, migrations, API contracts, and integration seams.
   - **Failure-mode branch**: Does this present as logic error, state/async race, data/contract mismatch, runtime integration fault, or missing/incorrect validation?
     - Expand only the matching branch; avoid broad unfocused questioning.
   - **Confidence branch**: Do we have high-confidence root cause evidence?
     - If no, continue branch-specific exploration and ask one targeted question only when a human answer is required.
     - If yes, finalize outputs.
5. If gaps remain, ask exactly one targeted question at a time.
   - Every question must directly reduce diagnosis uncertainty.
   - Ask about expected behavior, business rules, UX intent, policy, data provenance, user environment, or reproduction inputs only when the answer cannot be inferred from the repository.
   - After each answer, continue deep exploration before asking another question.
   - After asking a targeted question, pause and wait for the user's answer before proceeding.
   - Do not continue investigation finalization or report generation while the question is pending.
   - If any unresolved unknown is answerable by the user (policy, UX intent, business rule, expected output), you must ask at least one targeted question before finalizing.
   - Do not finalize while human-answerable unknowns remain unasked.
6. Resolve diagnosis branches across layers:
   - **UI/runtime behavior**: state, rendering, user flow, client-side dependencies.
   - **Backend/service behavior**: API contracts, service boundaries, retries/timeouts, error handling.
   - **Data/contracts**: schema assumptions, serialization, nullability, migrations, cross-layer contracts.
   - **Dependency/configuration**: package or library interactions, version constraints, feature flags, env vars.
   - **Architecture interactions**: module boundaries, side effects, async boundaries, orchestration flow.
7. For every candidate cause, assign a confidence level (`high`, `medium`, `low`) and cite evidence.
   - Separate observations from inferences.
   - Mark assumptions explicitly and explain how to validate or remove them.
   - Prefer one confirmed cause over many speculative causes.
8. Before finalization, run a completion gate:
   - List unresolved unknowns.
   - For each unknown, classify as `human-answerable` or `not-currently-answerable`.
   - Ask one targeted question for the highest-impact `human-answerable` unknown.
   - If a question is unanswered (`pending`), stop and wait; do not write final artifacts yet.
   - Continue until no high-impact `human-answerable` unknowns remain.
9. Stop only after diagnosis is sufficiently complete to support a safe implementation handoff.

## Deterministic Handoff Rules

These rules exist so different LLMs can produce the same implementation from the same `debug_llm_context.md`.

- Any implementation LLM should be able to apply the fix using this file without reading the chat history.
- Use deterministic, model-independent instructions: write exact file paths, symbols, expected behavior, preconditions, postconditions, and validation commands; avoid vague phrases like "handle this better" unless immediately followed by the precise required behavior.
- Use `must`, `must not`, and concrete acceptance criteria for required behavior.
- When multiple fix options exist, name the preferred option and explain why it is preferred; keep alternatives clearly separated.
- Include line anchors as `path/to/file:line` when available. If exact lines are unavailable, use stable symbols and searchable strings.
- Include test expectations as observable behavior, not implementation taste.
- Do not rely on "see above", "same as earlier", or unstated chat context.
- If a dependency or architecture detail is irrelevant to the fix, omit it.

## Outputs

Output precondition:

- No targeted clarification question is in `pending` state.
- If a targeted question is pending, respond with `Awaiting user answer: <question>` and stop.

Once branches are resolved, produce both files at workspace root:

1. `debug_human_report.md`
   - Issue summary in plain language: one sentence plus a short paragraph that a non-implementer can understand.
   - Expected vs Actual: clear comparison of what should happen and what happens instead.
   - Observed symptoms and impact: who/what is affected, frequency if known, and why it matters.
   - Architecture and Dependency Summary: human-readable overview of the relevant architecture, internal dependencies, and external dependencies.
   - Why this issue is happening: confirmed causes first, then likely causes, with jargon explained.
   - Call Path Trace: ordered end-to-end execution path from trigger to symptom.
     - Include file/function hops, key condition checks, and state transitions.
     - Include path anchors in the form `path/to/file:line` wherever available.
     - Format requirements for this section (must match this style):
       - Use stage headers in plain language (for example: `User action`, `System handler`, `Page load`, `Render outcome`).
       - Under each stage, use arrow hops with one step per line using `->`.
       - Show branching conditions inline (for example: `isOfflineMode() = true -> onOfflineSubmit()`).
       - End with a final symptom line that makes the rendered outcome explicit.
       - Prefer a monospaced trace block so sequence is visually scannable.
   - Contributing factors and conditions that trigger the failure.
   - Evidence table (path/symbol/error snippet -> conclusion).
   - Confidence per cause and unresolved unknowns.
   - Human-readable fix direction: describe the safest fix shape without implementing it.
   - Question log: each targeted question asked, answer received, and how it changed the diagnosis.

2. `debug_llm_context.md`
   - Include these section headings exactly:
   - Use this order:
     - `Project Overview`
       - What it should contain: project type, runtime, frameworks, package manager, relevant entrypoints, important commands, and relevant docs/config files.
       - Include only facts that affect diagnosis or implementation.
     - `Architecture Map`
       - What it should contain: relevant modules/components/services and how they connect for this issue.
       - Include boundaries (UI/backend/data/jobs/infrastructure), ownership notes when visible, and trigger-to-symptom data/control flow.
     - `Internal Dependencies`
       - What it should contain: repo-local modules, shared packages, generated code, schemas/migrations, state stores, queues/jobs, services, config files, feature flags, and test/build tooling involved in the failure path.
       - For each item, state why it matters and whether it is a change target, invariant, or validation dependency.
     - `External Dependencies`
       - What it should contain: third-party packages, SDKs, APIs, SaaS/infrastructure services, auth/payment/storage providers, network boundaries, environment variables, and version constraints involved in the failure path.
       - For each item, state the observed version/config when known, the contract relied on, and whether the fix should or must not touch it.
     - `Critical Call Paths`
       - What it should contain: trigger-to-symptom execution paths with key condition checks.
       - Include branch variants that matter (online/offline, feature flags, auth state, error paths, retries, async timing).
     - `Fault Surface Inventory`
       - What it should contain: likely files/functions/state points that must change or must be protected.
       - Include per-item rationale, confidence, risk level, expected blast radius, and evidence anchors.
     - `Implementation Approach`
       - What it should contain: preferred fix path, why it is preferred, exact behavior to preserve, exact behavior to change, and rejected alternatives.
       - Include minimal-diff option, safer alternative when relevant, rollout notes, and migration/config considerations.
     - `File-by-File Change Guide`
       - What it should contain: each file an implementation LLM is expected to edit or inspect.
       - For each file, include target symbols, current behavior, required behavior, invariants, dependency interactions, and tests that should prove the change.
     - `What NOT to change`
       - What it should contain: nearby code/contracts that must remain intact.
       - Include i18n keys, shared flows, public APIs, schema assumptions, telemetry, auth/security behavior, backwards compatibility, and non-target dependencies.
     - `Constraints`
       - What it should contain: technical/product constraints that bound the fix.
       - Include compatibility limits, migration constraints, performance/security rules, data retention/privacy constraints, operational limits, and supported environments.
     - `Dependencies & Libraries`
       - What it should contain: frameworks/packages/tools involved in this issue context.
       - Include versions if known, affected tooling, lockfile/config evidence, and non-affected dependencies that might otherwise look relevant.
     - `Test files to update`
       - What it should contain: concrete test files and assertions to change/add.
       - Include unit/integration/e2e scope, regression cases, fixtures, mocks/stubs, and commands to run.
     - `Acceptance Criteria`
       - What it should contain: observable behavior that proves the bug is fixed, regression protection exists, and unrelated flows remain intact.
       - Include validation commands and expected pass/fail signals.
   - In `Test files to update`, if no tests exist in the project or scope, write exactly: `No tests present.`
   - Use concrete file paths/functions and line anchors when available.
   - Keep recommendations implementation-ready so another LLM can apply a safe fix directly.

After writing both files:

1. Stop.
2. Inform the user the diagnosis package is ready and they can pass `debug_llm_context.md` to an implementation LLM.
3. Do not proceed beyond report generation, even if a fix is obvious.
