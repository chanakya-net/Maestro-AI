---
name: break-req
description: Requirements discovery, dependency mapping, and technical constraint capture before implementation planning.
---

## Skill Isolation

This skill is the sole active authority for this session once invoked.

- No other skill may activate, interrupt, or modify this skill's behavior unless explicitly called by name via a `Skill` tool call — whether from this skill's own workflow or from the governing prompt/skill that activated this one (e.g. the `run-with-it` worker prompts, which bootstrap `save-tokens` and `tdd-implementation` together).
- If any external or third-party skill attempts to activate spontaneously during this run, suppress it and continue without interruption.
- This rule applies for the entire duration of this skill's execution, from invocation until explicit termination or handoff.

# Break Req

## Purpose

This skill is requirements-only.

Use this skill to capture complete implementation requirements, resolve decision branches, and produce a finalized requirements artifact.

## When To Use

- The user wants to break down a requirement into implementation-ready decisions.
- The team needs dependency mapping and technical constraints before planning or execution.
- The user needs a complete requirements artifact before issue creation.

## Hard Stop

Never implement code, edit product files, create issues, publish to GitHub, run `create-git-issue`, run `run-with-it`, spawn implementation agents, or invoke external coding agents.

The only file this skill may create or update is `technical_requirements.md`.

Do read-only exploration to answer questions from the existing codebase when possible.

## Workflow

Interrogate me about every aspect of this requirement (both functional and non functional) until we have a complete technical picture. Walk down each branch of the decision tree, resolving blockers and dependencies one at a time. For each question, provide your recommended answer.

Ask the questions one at a time.

If a question can be answered by exploring the codebase, explore the codebase instead of asking.

Cover all relevant layers as you go:
- **UI**: frameworks, third-party libraries, component state management.
- **Backend**: packages, API contracts, service architecture.
- **Data**: schemas, migrations, contracts between layers.
- **Implementation order**: for each decision area, identify the concrete steps an agent would take to implement it — specific files to create or modify, functions to add, migrations to write, tests to add. Capture these as an ordered list so they can feed directly into issue slices.

## Outputs

Once all branches are resolved:

1. Compile every decision and resolution into `technical_requirements.md`.
2. For each major functional area, include an **Implementation Steps** subsection: an ordered, numbered list of concrete tasks (file paths, function names, schema fields, test cases) an agent would execute. Flag any step that requires human input with `[HITL]`.
3. Stop.
4. Inform the user that requirements are ready and they can now run the `create-git-issue` skill.

Do not proceed beyond `technical_requirements.md`, even if the next step is obvious.
