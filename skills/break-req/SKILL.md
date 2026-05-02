name: break-req
description: Recursive tech-audit & decision-tree mapping.
rules:
  - Mode: Relentless interrogation. 1 question at a time.
  - Format: Question + [Recommended Answer].
  - Logic: Map Decision Tree -> Identify Blockers -> Resolve Dependencies.
  - Audit: 
      - UI: Frameworks, 3rd-party libs, component state.
      - Backend: NuGet packages, API contracts, service arch.
  - Strategy: Search codebase first. Query user for unknowns.
  - Success: Compile all resolutions into `technical_requirements.md`.