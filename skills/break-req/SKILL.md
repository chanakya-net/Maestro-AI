---
name: break-req
description: Recursive tech-audit & decision-tree mapping. Use when the user wants to break down a requirement, audit a tech stack, map dependencies, or capture implementation requirements.
---

# Break Req

This skill is requirements-only.

## Hard Stop

Never implement code, edit product files, create issues, publish to GitHub, run `create-git-issue`, run `run-with-it`, spawn implementation agents, or invoke external coding agents.

The only file this skill may create or update is `technical_requirements.md`.

Interrogate me relentlessly about every aspect of this requirement (both functional and non functional) until we have a complete technical picture. Walk down each branch of the decision tree, resolving blockers and dependencies one at a time. For each question, provide your recommended answer.

Ask the questions one at a time.

If a question can be answered by exploring the codebase, explore the codebase instead of asking.

Cover all relevant layers as you go:
- **UI**: frameworks, third-party libraries, component state management.
- **Backend**: packages, API contracts, service architecture.
- **Data**: schemas, migrations, contracts between layers.

Once all branches are resolved:

1. Compile every decision and resolution into `technical_requirements.md`.
2. Stop.
3. Inform the user that requirements are ready and they can now run the `create-git-issue` skill.

Do not proceed beyond `technical_requirements.md`, even if the next step is obvious.
