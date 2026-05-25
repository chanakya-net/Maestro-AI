---
name: help-me-debug
description: Diagnose code issues through deep repository analysis and targeted clarification. Use when a user reports a bug, failure, regression, or unknown root cause and needs evidence-backed diagnosis artifacts for humans and LLM fix implementation.
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

## When To Use

- The user reports a bug, failure, or regression and root cause is unclear.
- The user asks for codebase-level debugging analysis before implementing a fix.
- The team needs a structured diagnosis package (human report + LLM context) before coding.

## Hard Stop

Never implement code, edit product files, create issues, publish to GitHub, run `create-git-issue`, run `run-with-it`, spawn implementation agents, or invoke external coding agents.

The only files this skill may create or update are `debug_human_report.md` and `debug_llm_context.md`.

Do read-only exploration aggressively. Default to deep analysis.

## Workflow

1. Frame the problem from user input: symptoms, expected behavior, actual behavior, frequency, scope, and urgency.
2. Run a deep codebase investigation before asking questions:
   - trace likely call paths and integration boundaries;
   - inspect configuration, dependency declarations, runtime assumptions, and environment-sensitive code;
   - identify recent or likely fault surfaces;
   - gather concrete evidence (file paths, symbols, logs/tests/error text when available).
3. Walk this diagnosis decision tree in order:
    - **Reproducibility branch**: Can the issue be reproduced from existing tests/logs/scripts?
       - If yes, capture deterministic repro steps and failure signature.
       - If no, identify missing reproduction inputs and ask one focused question.
    - **Scope branch**: Is the issue isolated or cross-cutting?
       - If isolated, trace nearest module boundaries and local invariants.
       - If cross-cutting, trace upstream/downstream dependencies and contract mismatches.
    - **Change-surface branch**: Is this likely caused by behavior change, configuration drift, or dependency/version shift?
       - Prioritize evidence from configs, env assumptions, and dependency edges.
    - **Failure-mode branch**: Does this present as logic error, state/async race, data/contract mismatch, or runtime integration fault?
       - Expand only the matching branch; avoid broad unfocused questioning.
    - **Confidence branch**: Do we have high-confidence root cause evidence?
       - If no, continue branch-specific exploration and ask one targeted question.
       - If yes, finalize outputs.
4. If gaps remain, ask exactly one targeted question at a time.
   - Every question must directly reduce diagnosis uncertainty.
   - After each answer, continue deep exploration before asking another question.
   - After asking a targeted question, pause and wait for the user's answer before proceeding.
   - Do not continue investigation finalization or report generation while the question is pending.
   - If any unresolved unknown is answerable by the user (policy, UX intent, business rule, expected output), you must ask at least one targeted question before finalizing.
   - Do not finalize while human-answerable unknowns remain unasked.
5. Resolve diagnosis branches across layers:
   - **UI/runtime behavior**: state, rendering, user flow, client-side dependencies.
   - **Backend/service behavior**: API contracts, service boundaries, retries/timeouts, error handling.
   - **Data/contracts**: schema assumptions, serialization, nullability, migrations, cross-layer contracts.
   - **Dependency/configuration**: package or library interactions, version constraints, feature flags, env vars.
   - **Architecture interactions**: module boundaries, side effects, async boundaries, orchestration flow.
6. For every candidate cause, assign a confidence level (`high`, `medium`, `low`) and cite evidence.
7. Before finalization, run a completion gate:
   - List unresolved unknowns.
   - For each unknown, classify as `human-answerable` or `not-currently-answerable`.
   - Ask one targeted question for the highest-impact `human-answerable` unknown.
   - If a question is unanswered (`pending`), stop and wait; do not write final artifacts yet.
   - Continue until no high-impact `human-answerable` unknowns remain.
8. Stop only after diagnosis is sufficiently complete to support a safe implementation handoff.

## Outputs

Output precondition:

- No targeted clarification question is in `pending` state.
- If a targeted question is pending, respond with `Awaiting user answer: <question>` and stop.

Once branches are resolved, produce both files at workspace root:

1. `debug_human_report.md`
   - Issue summary in plain language.
   - Observed symptoms and impact.
   - Why this issue is happening (confirmed causes first, then likely causes).
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
   - Question log: each targeted question asked, answer received, and how it changed the diagnosis.

2. `debug_llm_context.md`
   - Include these section headings exactly:
     - `Architecture Map`
          - What it should contain: relevant modules/components/services and how they connect for this issue.
          - Can include: a compact tree, boundaries (UI/backend/data), and ownership notes.
     - `Critical Call Paths`
          - What it should contain: trigger-to-symptom execution paths with key condition checks.
          - Can include: branch variants (online/offline, feature flags, error paths).
     - `Fault Surface Inventory`
          - What it should contain: likely files/functions/state points that must change.
          - Can include: per-item rationale, risk level, and expected blast radius.
     - `Implementation Approach`
          - What it should contain: practical fix options, preferred path, and why.
          - Can include: minimal-diff option, safer alternative, and rollout notes.
     - `What NOT to change`
          - What it should contain: nearby code/contracts that must remain intact.
          - Can include: i18n keys, shared flows, APIs, schema assumptions, and invariants.
     - `Constraints`
          - What it should contain: technical/product constraints that bound the fix.
          - Can include: compatibility limits, migration constraints, performance/security rules.
     - `Dependencies & Libraries`
          - What it should contain: frameworks/packages involved in this issue context.
          - Can include: versions (if known), affected tooling, and non-affected dependencies.
     - `Test files to update`
          - What it should contain: concrete test files and assertions to change/add.
          - Can include: unit/integration/e2e scope and regression cases.
   - In `Test files to update`, if no tests exist in the project or scope, write exactly: `No tests present.`
   - Use concrete file paths/functions and line anchors when available.
   - Keep recommendations implementation-ready so another LLM can apply a safe fix directly.

After writing both files:

1. Stop.
2. Inform the user the diagnosis package is ready and they can pass `debug_llm_context.md` to an implementation LLM.
3. Do not proceed beyond report generation, even if a fix is obvious.