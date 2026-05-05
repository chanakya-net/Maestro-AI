---
name: break-req
description: Recursive tech-audit & decision-tree mapping. Use when the user wants to break down a requirement, audit a tech stack, map dependencies, or plan an implementation.
---

Interrogate me relentlessly about every aspect of this requirement (both functional and non functional) until we have a complete technical picture. Walk down each branch of the decision tree, resolving blockers and dependencies one at a time. For each question, provide your recommended answer.

Ask the questions one at a time.

If a question can be answered by exploring the codebase, explore the codebase instead of asking.

Cover all relevant layers as you go:
- **UI**: frameworks, third-party libraries, component state management.
- **Backend**: packages, API contracts, service architecture.
- **Data**: schemas, migrations, contracts between layers.

Once all branches are resolved, compile every decision and resolution into `technical_requirements.md`.