#!/usr/bin/env python3
"""Immediate GitHub issue update helper for run-with-it pool runners."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


TERMINAL_OPEN_OUTCOMES = {"blocked", "failed-review", "failed-merge"}


def load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def save_json(path: str, value: dict[str, Any]) -> None:
    tmp_path = f"{path}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
    os.replace(tmp_path, path)


def load_report(path: str) -> dict[str, Any]:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return {}
    try:
        return load_json(path)
    except Exception:
        return {}


def set_github_update_state(state_file: str, issue: str, status: str, detail: str) -> None:
    state = load_json(state_file)
    entry = state.setdefault("issue_registry", {}).setdefault(str(issue), {})
    entry["github_update_status"] = status
    entry["github_update_detail"] = detail
    entry["github_updated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    save_json(state_file, state)


def token_total(tokens: Any, kind: str) -> int | None:
    if not isinstance(tokens, dict):
        return None
    total = 0
    found = False
    for key, value in tokens.items():
        key_l = str(key).lower()
        if kind == "input" and "input" not in key_l:
            continue
        if kind == "output" and "output" not in key_l:
            continue
        if kind == "cache" and "cache" not in key_l:
            continue
        if isinstance(value, (int, float)):
            total += int(value)
            found = True
    return total if found else None


def format_token(value: int | None) -> str:
    return str(value) if value is not None else "unknown"


def render_terminal_comment(report_file: str, fallback_outcome: str) -> str:
    report = load_report(report_file)
    # Merge-recovery reports carry no token/review telemetry; fall back to the
    # sibling original issue report for those fields only (never for outcome,
    # summary, verification, or blocking reasons — those describe the recovery).
    if not report.get("token_usage") or not report.get("review_summary"):
        sibling = os.path.join(os.path.dirname(os.path.abspath(report_file)), "report.json")
        if sibling != os.path.abspath(report_file):
            original = load_report(sibling)
            if original:
                if not report.get("token_usage"):
                    report["token_usage"] = original.get("token_usage") or {}
                if not report.get("review_summary"):
                    report["review_summary"] = original.get("review_summary") or {}
                if report.get("review_skipped") is None and original.get("review_skipped") is not None:
                    report["review_skipped"] = original.get("review_skipped")
                    report["review_skip_reason"] = original.get("review_skip_reason")
    outcome = report.get("outcome") or fallback_outcome or "blocked"
    summary = report.get("summary") or "No summary provided."
    verification = report.get("verification") or {}
    if isinstance(verification, dict):
        commands = verification.get("commands_run") or []
        evidence = verification.get("evidence") or ""
        passed = verification.get("passed")
        state = "passed" if passed is True else "failed" if passed is False else "unknown"
        command_text = ", ".join(str(command) for command in commands) if commands else "unknown"
        verification_lines = [
            f"State: {state}",
            f"Commands: {command_text}",
            f"Evidence: {evidence or 'unknown'}",
        ]
    else:
        verification_lines = [str(verification) if verification else "unknown"]

    review = report.get("review_summary") or {}
    cycles = review.get("cycles_used")
    final = review.get("final_verdict") or "unknown"
    reviewer = review.get("reviewer_model") or "unknown"
    if report.get("review_skipped") is True:
        skip_reason = report.get("review_skip_reason") or "trivial-change"
        review_line = f"Review: skipped ({skip_reason})"
    elif cycles is None:
        review_line = f"Review: unknown, final verdict: {final}, reviewer model: {reviewer}"
    elif int(cycles) <= 1 and final == "approve":
        review_line = f"Review: approve (1 cycle), final verdict: {final}, reviewer model: {reviewer}"
    else:
        review_line = f"Review: revise ({cycles} cycles), final verdict: {final}, reviewer model: {reviewer}"

    tokens = report.get("token_usage") or {}
    lines = [
        "## Status",
        str(outcome),
        "",
        "## Summary",
        str(summary),
        "",
        "## Verification",
        *verification_lines,
        "",
        "## Token Usage",
        f"- Input tokens: {format_token(token_total(tokens, 'input'))}",
        f"- Output tokens: {format_token(token_total(tokens, 'output'))}",
        f"- Cache hit tokens: {format_token(token_total(tokens, 'cache'))}",
        "",
        "## Notes",
        review_line,
    ]
    if report.get("commit_sha"):
        lines.append(f"Commit: {report['commit_sha']}")
    merge = report.get("merge") or {}
    if isinstance(merge, dict) and merge.get("merge_sha"):
        lines.append(f"Merge: {merge['merge_sha']}")

    blocking = report.get("blocking_reasons") or []
    if blocking:
        lines.extend(["", "## Blocking Reasons"])
        lines.extend(f"- {reason}" for reason in blocking)

    return "\n".join(lines) + "\n"


def has_github_remote(run_root: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", run_root, "remote", "-v"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0 and "github.com" in result.stdout.lower()


def print_status(issue: str, outcome: str, action: str, **fields: Any) -> None:
    suffix = "".join(f"|{key}={value}" for key, value in fields.items() if value is not None)
    print(f"STATUS|type=github-update|issue={issue}|outcome={outcome}|action={action}{suffix}")


def update_github(args: argparse.Namespace) -> int:
    close_issue = args.outcome == "completed"
    if args.outcome != "completed" and args.outcome not in TERMINAL_OPEN_OUTCOMES:
        return 0

    if os.environ.get("RUN_WITH_IT_GITHUB_UPDATES", "1") == "0":
        set_github_update_state(args.state_file, args.issue, "skipped", "disabled")
        print_status(args.issue, args.outcome, "skipped", reason="disabled")
        return 0

    if shutil.which("gh") is None:
        set_github_update_state(args.state_file, args.issue, "skipped", "gh-not-found")
        print_status(args.issue, args.outcome, "skipped", reason="gh-not-found")
        return 0

    if not has_github_remote(args.run_root):
        set_github_update_state(args.state_file, args.issue, "skipped", "no-github-remote")
        print_status(args.issue, args.outcome, "skipped", reason="no-github-remote")
        return 0

    comment = render_terminal_comment(args.report_file, args.outcome)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(comment)
        comment_file = handle.name

    try:
        comment_result = subprocess.run(
            ["gh", "issue", "comment", str(args.issue), "--body-file", comment_file],
            cwd=args.run_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if comment_result.returncode != 0:
            set_github_update_state(args.state_file, args.issue, "failed", "comment-failed")
            print_status(args.issue, args.outcome, "failed", reason="comment-failed")
            return 0

        if close_issue:
            close_result = subprocess.run(
                ["gh", "issue", "close", str(args.issue)],
                cwd=args.run_root,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if close_result.returncode != 0:
                set_github_update_state(args.state_file, args.issue, "failed", "close-failed")
                print_status(args.issue, args.outcome, "commented", closed="false", reason="close-failed")
                return 0

        closed = "true" if close_issue else "false"
        set_github_update_state(args.state_file, args.issue, "updated", f"commented;closed={closed}")
        print_status(args.issue, args.outcome, "commented", closed=closed)
        return 0
    finally:
        Path(comment_file).unlink(missing_ok=True)


def render_comment(args: argparse.Namespace) -> int:
    sys.stdout.write(render_terminal_comment(args.report_file, args.outcome))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="run-with-it GitHub terminal update helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update = subparsers.add_parser("update")
    update.add_argument("--state-file", required=True)
    update.add_argument("--run-root", required=True)
    update.add_argument("--issue", required=True)
    update.add_argument("--outcome", required=True)
    update.add_argument("--report-file", required=True)
    update.set_defaults(func=update_github)

    render = subparsers.add_parser("render-comment")
    render.add_argument("--outcome", required=True)
    render.add_argument("--report-file", required=True)
    render.set_defaults(func=render_comment)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
