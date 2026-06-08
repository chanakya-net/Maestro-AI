#!/usr/bin/env python3
"""Render final run-with-it pull request bodies from compact state."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

AUTO_CLOSE_REF_PATTERN = re.compile(
    r"\b(close(?:s|d)?|fix(?:es|ed)?|resolve(?:s|d)?)(\s+)((?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?)(#\d+)\b",
    re.IGNORECASE,
)


def load_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def load_state(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except Exception as exc:
        raise SystemExit(f"error: failed to load state file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"error: state file {path} must contain a JSON object")
    return value


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
    text = " ".join(text.replace("|", "\\|").split())
    return sanitize_auto_close_refs(text)


def sanitize_auto_close_refs(text: str) -> str:
    return AUTO_CLOSE_REF_PATTERN.sub(lambda match: f"{match[1]}{match[2]}{match[3]}\\{match[4]}", text)


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


def report_for_issue(
    state: dict[str, Any], issue: str, run_root: Path, summary: dict[str, Any]
) -> dict[str, Any]:
    summary_report = resolve_path(summary.get("report_file"), run_root)
    if summary_report:
        return load_json(summary_report)

    info = state.get("issue_registry", {}).get(str(issue), {})
    if not isinstance(info, dict):
        info = {}
    for report_ref in (info.get("merge_recovery_report_file"), info.get("report_file")):
        report_path = resolve_path(report_ref, run_root)
        if report_path:
            report = load_json(report_path)
            if report:
                return report
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
        summary_verification = summary.get("verification")
        verification = summary_verification if isinstance(summary_verification, dict) else {}
    passed = verification.get("passed")
    state = "passed" if passed is True else "failed" if passed is False else "unknown"
    evidence = verification.get("evidence") or summary.get("summary") or "unknown"
    return state, one_line(evidence)


def render_pr_body(state_file: str) -> str:
    state = load_state(state_file)
    run_root = run_root_for(state_file)
    counts = status_counts(state)
    summaries = summary_by_issue(state)
    completed = completed_issue_numbers(state)
    total_added = sum(int(summaries.get(issue, {}).get("lines_added", 0) or 0) for issue in completed)
    total_deleted = sum(int(summaries.get(issue, {}).get("lines_deleted", 0) or 0) for issue in completed)

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
        summary = summaries.get(issue, {})
        report = report_for_issue(state, issue, run_root, summary)
        for row in model_rows(issue, report, summary):
            lines.append(
                f"| #{one_line(issue)} | {row['role']} | {row['cycle']} | "
                f"{row['agent']} | {row['model']} | {row['selection_reason']} |"
            )

    lines.extend(["", "## Verification", "| Issue | State | Evidence |", "|---|---|---|"])
    for issue in completed:
        summary = summaries.get(issue, {})
        report = report_for_issue(state, issue, run_root, summary)
        state_text, evidence = verification_state(report, summary)
        lines.append(f"| #{one_line(issue)} | {state_text} | {evidence} |")

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
