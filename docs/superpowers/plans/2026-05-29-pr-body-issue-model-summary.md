# PR Body Issue Model Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate final `run-with-it` PR bodies that list completed issues as plain links and summarize the agent/model used for each task role.

**Architecture:** Add a deterministic PR body renderer under `assets/`, preserve compact `model_usage` in `main-state.json`, and update orchestration instructions so final PR creation uses the helper output. Keep issue closing separate from PR body rendering; the PR body must never use auto-closing keywords.

**Tech Stack:** Python 3 helper scripts, Bash/PowerShell installer contracts, Markdown prompt files, shell test harnesses.

---

## File Structure

- Create `assets/run-with-it-pr-body.py`: pure Python renderer that reads `.run-with-it/main-state.json`, resolves compact report files, and writes Markdown to stdout.
- Modify `assets/run-with-it-state.py`: preserve compact `model_usage`, report summary, verification state, and report path in `completed_summaries`.
- Modify `assets/sub-coordinator-prompt.md`: require compact reports to include task-level `model_usage`.
- Modify `assets/coordinator-rules.md`: require Sub-Coordinators to carry route selections into the compact report.
- Modify `assets/main-orchestrator-rules.md`: require final PR creation through `run-with-it-pr-body.py`.
- Modify `skills/run-with-it/SKILL.md`: document the renderer in asset discovery, final PR creation, state schema, and final summary behavior.
- Modify `install.sh` and `install.ps1`: install the new helper and mark it executable on Unix.
- Modify `README.md`: list the new helper and manual asset copy commands.
- Modify tests:
  - `tests/run-with-it-helpers.test.sh`
  - `tests/install-assets-contract.test.sh`
  - `tests/install-assets-powershell-contract.test.sh`
  - `tests/run-with-it-routing.test.sh`

## Task 1: Add PR Body Renderer Contract

**Files:**
- Test: `tests/run-with-it-helpers.test.sh`
- Create: `assets/run-with-it-pr-body.py`

- [ ] **Step 1: Write failing renderer assertions**

Add `PR_BODY_HELPER="${ROOT_DIR}/assets/run-with-it-pr-body.py"` near the existing helper variables.

Add this assertion helper:

```bash
assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  [[ "$haystack" != *"$needle"* ]] || fail "$message (forbidden: $needle)"
}
```

Extend the first report fixture with model usage:

```json
  "model_usage": [
    {
      "role": "complexity",
      "cycle": 1,
      "agent": "agy",
      "model": "gemini-3.5-flash-medium",
      "selection_reason": "complexity-scorer"
    },
    {
      "role": "impl",
      "cycle": 1,
      "agent": "codex",
      "model": "gpt-5.3-codex",
      "selection_reason": "under-target"
    },
    {
      "role": "review",
      "cycle": 1,
      "agent": "claude",
      "model": "claude-sonnet-4-6",
      "selection_reason": "independent-review"
    }
  ],
```

After the existing render-comment assertions, add:

```bash
pr_body="$(python3 "$PR_BODY_HELPER" render --state-file "$STATE_FILE")"
assert_contains "$pr_body" "## Closed Issues" "PR body includes closed issues section"
assert_contains "$pr_body" "- #2" "PR body links completed issue 2"
assert_contains "$pr_body" "- #3" "PR body links completed issue 3"
assert_not_contains "$pr_body" "Closes #" "PR body avoids auto-closing keyword Closes"
assert_not_contains "$pr_body" "Fixes #" "PR body avoids auto-closing keyword Fixes"
assert_not_contains "$pr_body" "Resolves #" "PR body avoids auto-closing keyword Resolves"
assert_contains "$pr_body" "| #2 | complexity | 1 | agy | gemini-3.5-flash-medium | complexity-scorer |" "PR body includes complexity model row"
assert_contains "$pr_body" "| #2 | impl | 1 | codex | gpt-5.3-codex | under-target |" "PR body includes implementation model row"
assert_contains "$pr_body" "| #2 | review | 1 | claude | claude-sonnet-4-6 | independent-review |" "PR body includes review model row"
assert_contains "$pr_body" "| #3 | unknown | - | unknown | unknown | missing-model-usage |" "PR body falls back when model usage is absent"
```

- [ ] **Step 2: Run the failing helper test**

Run:

```bash
bash tests/run-with-it-helpers.test.sh
```

Expected: fail because `assets/run-with-it-pr-body.py` does not exist.

- [ ] **Step 3: Create the renderer**

Create `assets/run-with-it-pr-body.py` with:

```python
#!/usr/bin/env python3
"""Render final run-with-it pull request bodies from compact state."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def run_root_for(state_file: str) -> Path:
    state_path = Path(state_file).resolve()
    if state_path.parent.name == ".run-with-it":
        return state_path.parent.parent
    return state_path.parent


def resolve_path(path: Any, run_root: Path) -> str:
    if not isinstance(path, str) or not path:
        return ""
    candidate = Path(path)
    if candidate.is_absolute():
        return str(candidate)
    return str(run_root / candidate)


def issue_sort_key(value: Any) -> tuple[int, str]:
    text = str(value)
    try:
        return (0, f"{int(text):020d}")
    except ValueError:
        return (1, text)


def one_line(value: Any) -> str:
    text = "unknown" if value is None or value == "" else str(value)
    return " ".join(text.replace("|", "\\|").split())


def status_counts(state: dict[str, Any]) -> dict[str, int]:
    counts = {"completed": 0, "failed-review": 0, "failed-merge": 0, "blocked": 0}
    for info in state.get("issue_registry", {}).values():
        if isinstance(info, dict):
            status = str(info.get("status", ""))
            if status in counts:
                counts[status] += 1
    return counts


def completed_issue_numbers(state: dict[str, Any]) -> list[str]:
    registry = state.get("issue_registry", {})
    issues = [
        str(issue)
        for issue, info in registry.items()
        if isinstance(info, dict) and info.get("status") == "completed"
    ]
    return sorted(issues, key=issue_sort_key)


def summary_by_issue(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in state.get("completed_summaries", []):
        if isinstance(item, dict) and item.get("issue") is not None:
            result[str(item["issue"])] = item
    return result


def report_for_issue(state: dict[str, Any], issue: str, run_root: Path) -> dict[str, Any]:
    info = state.get("issue_registry", {}).get(str(issue), {})
    if not isinstance(info, dict):
        info = {}
    report_path = resolve_path(info.get("report_file"), run_root)
    if report_path:
        return load_json(report_path)
    return {}


def model_rows(issue: str, report: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, Any]]:
    usage = report.get("model_usage")
    if not isinstance(usage, list):
        usage = summary.get("model_usage")
    if not isinstance(usage, list) or not usage:
        return [
            {
                "issue": issue,
                "role": "unknown",
                "cycle": "-",
                "agent": "unknown",
                "model": "unknown",
                "selection_reason": "missing-model-usage",
            }
        ]

    rows: list[dict[str, Any]] = []
    for item in usage:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "issue": issue,
                "role": one_line(item.get("role")),
                "cycle": one_line(item.get("cycle") if item.get("cycle") is not None else "-"),
                "agent": one_line(item.get("agent")),
                "model": one_line(item.get("model")),
                "selection_reason": one_line(item.get("selection_reason") or item.get("reason")),
            }
        )
    return rows or model_rows(issue, {}, {})


def verification_state(report: dict[str, Any], summary: dict[str, Any]) -> tuple[str, str]:
    verification = report.get("verification")
    if not isinstance(verification, dict):
        verification = summary.get("verification") if isinstance(summary.get("verification"), dict) else {}
    passed = verification.get("passed")
    state = "passed" if passed is True else "failed" if passed is False else "unknown"
    evidence = verification.get("evidence") or summary.get("summary") or "unknown"
    return state, one_line(evidence)


def render_pr_body(state_file: str) -> str:
    state = load_json(state_file)
    run_root = run_root_for(state_file)
    counts = status_counts(state)
    summaries = summary_by_issue(state)
    completed = completed_issue_numbers(state)
    total_added = sum(int(item.get("lines_added", 0) or 0) for item in summaries.values())
    total_deleted = sum(int(item.get("lines_deleted", 0) or 0) for item in summaries.values())

    lines = [
        "## Summary",
        f"- Total issues processed: {sum(counts.values())}",
        f"- Completed: {counts['completed']}",
        f"- Failed review: {counts['failed-review']}",
        f"- Failed merge: {counts['failed-merge']}",
        f"- Blocked: {counts['blocked']}",
        f"- Lines added: {total_added}",
        f"- Lines deleted: {total_deleted}",
        "",
        "## Closed Issues",
    ]

    if completed:
        lines.extend(f"- #{issue}" for issue in completed)
    else:
        lines.append("None")

    lines.extend(
        [
            "",
            "## Models Used",
            "| Issue | Task | Cycle | Agent | Model | Reason |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for issue in completed:
        report = report_for_issue(state, issue, run_root)
        summary = summaries.get(issue, {})
        for row in model_rows(issue, report, summary):
            lines.append(
                f"| #{issue} | {row['role']} | {row['cycle']} | {row['agent']} | {row['model']} | {row['selection_reason']} |"
            )

    lines.extend(["", "## Verification", "| Issue | State | Evidence |", "|---|---|---|"])
    for issue in completed:
        report = report_for_issue(state, issue, run_root)
        summary = summaries.get(issue, {})
        state_text, evidence = verification_state(report, summary)
        lines.append(f"| #{issue} | {state_text} | {evidence} |")

    return "\n".join(lines) + "\n"


def render(args: argparse.Namespace) -> int:
    sys.stdout.write(render_pr_body(args.state_file))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="render run-with-it final PR body")
    subparsers = parser.add_subparsers(dest="command", required=True)
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--state-file", required=True)
    render_parser.set_defaults(func=render)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the renderer test**

Run:

```bash
bash tests/run-with-it-helpers.test.sh
```

Expected: fail until state preservation is implemented in Task 2.

## Task 2: Preserve Model Usage In State

**Files:**
- Modify: `assets/run-with-it-state.py`
- Test: `tests/run-with-it-helpers.test.sh`

- [ ] **Step 1: Add failing state preservation assertion**

After the existing `commit_sha` assertion for issue 2, add:

```python
model_usage = state["completed_summaries"][-1]["model_usage"]
assert model_usage[0]["role"] == "complexity"
assert model_usage[0]["agent"] == "agy"
assert model_usage[1]["model"] == "gpt-5.3-codex"
```

- [ ] **Step 2: Run the failing helper test**

Run:

```bash
bash tests/run-with-it-helpers.test.sh
```

Expected: fail with missing `model_usage` in `completed_summaries`.

- [ ] **Step 3: Add compact model usage normalization**

In `assets/run-with-it-state.py`, add:

```python
def compact_model_usage(report: dict[str, Any]) -> list[dict[str, Any]]:
    usage = report.get("model_usage")
    if not isinstance(usage, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in usage:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "role": str(item.get("role") or "unknown"),
                "cycle": item.get("cycle") if isinstance(item.get("cycle"), int) else None,
                "agent": str(item.get("agent") or "unknown"),
                "model": str(item.get("model") or "unknown"),
                "selection_reason": str(item.get("selection_reason") or item.get("reason") or "unknown"),
            }
        )
    return rows
```

Then extend the `summary` dict in `finalize_issue`:

```python
        "summary": report.get("summary"),
        "verification": report.get("verification") if isinstance(report.get("verification"), dict) else {},
        "report_file": args.report_file,
        "model_usage": compact_model_usage(report),
```

- [ ] **Step 4: Run the helper test**

Run:

```bash
bash tests/run-with-it-helpers.test.sh
```

Expected: pass.

- [ ] **Step 5: Commit state and renderer**

Run:

```bash
git add assets/run-with-it-pr-body.py assets/run-with-it-state.py tests/run-with-it-helpers.test.sh
git commit -m "feat(run-with-it): render final PR summary"
```

## Task 3: Update Runtime Instructions And Schemas

**Files:**
- Modify: `assets/sub-coordinator-prompt.md`
- Modify: `assets/coordinator-rules.md`
- Modify: `assets/main-orchestrator-rules.md`
- Modify: `skills/run-with-it/SKILL.md`
- Test: `tests/run-with-it-routing.test.sh`

- [ ] **Step 1: Add failing instruction contract assertions**

In `tests/run-with-it-routing.test.sh`, add assertions:

```bash
assert_file_contains "$ORCHESTRATOR_RULES_FILE" 'run-with-it-pr-body.py' "runtime rules require PR body renderer"
assert_file_contains "$RUN_WITH_IT_SKILL_FILE" 'run-with-it-pr-body.py' "skill documents PR body renderer"
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" '"model_usage"' "sub-coordinator report schema includes model usage"
assert_file_contains "$SUB_COORDINATOR_PROMPT_FILE" 'selection_reason' "sub-coordinator model usage records selection reason"
assert_file_contains "$COORDINATOR_RULES_FILE" 'model_usage' "coordinator rules require model usage in compact report"
```

If `COORDINATOR_RULES_FILE` is not already declared, add:

```bash
COORDINATOR_RULES_FILE="${ROOT_DIR}/assets/coordinator-rules.md"
```

- [ ] **Step 2: Run the failing routing contract**

Run:

```bash
bash tests/run-with-it-routing.test.sh
```

Expected: fail on missing renderer/model usage instructions.

- [ ] **Step 3: Update final PR instructions**

In `assets/main-orchestrator-rules.md`, replace the final PR bullet with:

```markdown
- Main Orchestrator may create the final PR from `run-with-it/<run-id>` to the original base branch after all issues are terminal. Before `gh pr create`, render the body with `$ASSET_ROOT/run-with-it-pr-body.py render --state-file .run-with-it/main-state.json > .run-with-it/final-pr-body.md` and pass that file via `gh pr create --body-file .run-with-it/final-pr-body.md`. The rendered body must list closed issues as plain links like `#123` and must not use auto-closing keywords such as `Closes`, `Fixes`, or `Resolves`.
```

- [ ] **Step 4: Update compact report instructions**

In `assets/coordinator-rules.md`, add under report rules:

```markdown
- Include `model_usage` in the compact report. Add one entry for each routed task role (`complexity`, `impl`, `review`, `modify`, and `merge-recovery` when applicable) with `role`, `cycle`, `agent`, `model`, and `selection_reason`. Do not read raw logs to reconstruct this; use the route decisions already selected and persisted in Sub-Coordinator state.
```

In `assets/sub-coordinator-prompt.md`, extend the Appendix E JSON example with:

```json
  "model_usage": [
    {
      "role": "impl",
      "cycle": 1,
      "agent": "codex",
      "model": "gpt-5.3-codex",
      "selection_reason": "under-target"
    }
  ],
```

Add a sentence below the schema:

```markdown
`model_usage` must include every worker route selected for the issue. If a role is skipped, omit that role; do not invent model names.
```

- [ ] **Step 5: Update skill documentation**

In `skills/run-with-it/SKILL.md`:

- Add `run-with-it-pr-body.py` to the shared required files list.
- Add it to Bash and PowerShell one-command asset copy examples.
- Add `model_usage` to Appendix A `completed_summaries`.
- Add final PR body rendering instructions near final PR creation.
- Update the final summary bullets to include task-level model usage.

- [ ] **Step 6: Run routing contract**

Run:

```bash
bash tests/run-with-it-routing.test.sh
```

Expected: pass.

- [ ] **Step 7: Commit instruction updates**

Run:

```bash
git add assets/main-orchestrator-rules.md assets/coordinator-rules.md assets/sub-coordinator-prompt.md skills/run-with-it/SKILL.md tests/run-with-it-routing.test.sh
git commit -m "docs(run-with-it): require PR body renderer"
```

## Task 4: Wire New Helper Into Installation And Docs

**Files:**
- Modify: `install.sh`
- Modify: `install.ps1`
- Modify: `README.md`
- Modify: `tests/install-assets-contract.test.sh`
- Modify: `tests/install-assets-powershell-contract.test.sh`

- [ ] **Step 1: Add failing installer assertions**

In both installer contract tests, add an assertion next to other shared Python helpers:

```bash
assert_contains "${dry_run_output}" "run-with-it-pr-body.py" "dry-run includes final PR body helper asset"
```

For PowerShell test, use:

```bash
assert_contains "${dry_run_output}" "run-with-it-pr-body.py" "PowerShell dry-run includes final PR body helper asset"
```

- [ ] **Step 2: Run failing installer contracts**

Run:

```bash
bash tests/install-assets-contract.test.sh
bash tests/install-assets-powershell-contract.test.sh
```

Expected: Bash contract fails until `install.sh` includes the asset; PowerShell contract fails if PowerShell is available.

- [ ] **Step 3: Update installers**

In `install.sh`, add `"run-with-it-pr-body.py"` to the `files` array after `run-with-it-github-update.py`. Add chmod lines:

```bash
note "  [dry-run] chmod +x ${ASSETS_DEST}/run-with-it-pr-body.py"
chmod +x "${ASSETS_DEST}/run-with-it-pr-body.py"
```

In `install.ps1`, add `"run-with-it-pr-body.py"` to `$files` after `run-with-it-github-update.py`.

- [ ] **Step 4: Update README asset references**

Add `run-with-it-pr-body.py` to:

- the `assets/` tree listing
- the asset description table
- Bash manual copy examples
- PowerShell manual copy examples

Use this table row:

```markdown
| [`assets/run-with-it-pr-body.py`](assets/run-with-it-pr-body.py) | Shared final PR body renderer for closed issue links and task-level model summaries. |
```

- [ ] **Step 5: Run installer contracts**

Run:

```bash
bash tests/install-assets-contract.test.sh
bash tests/install-assets-powershell-contract.test.sh
```

Expected: pass or PowerShell contract skips when PowerShell is unavailable.

- [ ] **Step 6: Commit install/docs wiring**

Run:

```bash
git add install.sh install.ps1 README.md tests/install-assets-contract.test.sh tests/install-assets-powershell-contract.test.sh
git commit -m "chore: install PR body helper asset"
```

## Task 5: Final Verification

**Files:**
- All changed files

- [ ] **Step 1: Make helper executable**

Run:

```bash
chmod +x assets/run-with-it-pr-body.py
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
bash tests/run-with-it-helpers.test.sh
bash tests/run-with-it-routing.test.sh
bash tests/install-assets-contract.test.sh
bash tests/install-assets-powershell-contract.test.sh
```

Expected: all pass, except PowerShell install contract may print `SKIP: PowerShell unavailable for install.ps1 asset contract`.

- [ ] **Step 3: Run full shell test suite**

Run:

```bash
for test_file in tests/*.test.sh; do bash "$test_file"; done
```

Expected: every test passes or platform-specific PowerShell tests skip due to unavailable PowerShell.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: only intended run-with-it PR body, state, docs, installer, and test files changed.

- [ ] **Step 5: Commit any final fixups**

If Task 5 changed file modes or minor docs after prior commits, run:

```bash
git add assets/run-with-it-pr-body.py README.md skills/run-with-it/SKILL.md assets/*.md tests/*.sh install.sh install.ps1
git commit -m "test(run-with-it): verify PR body summary"
```

If there are no remaining changes, do not create an empty commit.
