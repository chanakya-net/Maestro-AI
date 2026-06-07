#!/usr/bin/env python3
"""State helpers for run-with-it pool runners.

This file intentionally owns JSON state mutations that are shared by the Bash
and PowerShell pool supervisors. The platform runners still own process
spawning and monitoring.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


TERMINAL_OUTCOMES = {"completed", "failed-review", "blocked", "merge_failed", "failed-merge"}
LIVE_WORKER_STATES = {"ready", "starting", "running", "quiet", "stalled"}
FINISHED_WORKER_STATES = {"completed", "failed"}


def load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


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
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def load_optional_json(path: str | None) -> dict[str, Any]:
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def file_has_json(path: str | None) -> bool:
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            json.load(handle)
        return True
    except Exception:
        return False


def file_nonempty(path: str | None) -> bool:
    return bool(path and os.path.exists(path) and os.path.getsize(path) > 0)


def report_file_metrics(report: dict[str, Any]) -> dict[str, int]:
    files = report.get("files_modified", [])
    file_items = [item for item in files if isinstance(item, dict)] if isinstance(files, list) else []

    def explicit_int(name: str) -> int | None:
        value = report.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def item_int(item: dict[str, Any], name: str) -> int:
        value = item.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    return {
        "files_modified_count": explicit_int("files_modified_count")
        if explicit_int("files_modified_count") is not None
        else len(file_items),
        "lines_added": explicit_int("lines_added")
        if explicit_int("lines_added") is not None
        else sum(item_int(item, "lines_added") for item in file_items),
        "lines_deleted": explicit_int("lines_deleted")
        if explicit_int("lines_deleted") is not None
        else sum(item_int(item, "lines_deleted") for item in file_items),
    }


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


def unique(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = json.dumps(value, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def issue_entry(state: dict[str, Any], issue: str) -> dict[str, Any]:
    registry = state.setdefault("issue_registry", {})
    return registry.setdefault(str(issue), {})


def issue_dir_for_report(state: dict[str, Any], issue: str, report_file: str) -> str:
    entry = state.get("issue_registry", {}).get(str(issue), {})
    if isinstance(entry, dict) and entry.get("issue_dir"):
        return str(entry["issue_dir"])
    if report_file:
        return str(Path(report_file).parent)
    return ""


def recovery_attempt(entry: dict[str, Any]) -> int:
    value = entry.get("sub_coord_recovery_attempts", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def recovery_max_attempts(args: argparse.Namespace) -> int:
    value = getattr(args, "max_attempts", 2)
    if isinstance(value, bool) or not isinstance(value, int):
        return 2
    return max(value, 0)


def compact_worker_decision(
    *,
    action: str,
    reason: str,
    issue: str,
    issue_dir: str,
    sub_state_file: str,
    phase: str | None = None,
    worker: dict[str, Any] | None = None,
    worker_state: dict[str, Any] | None = None,
    attempt: int = 0,
    max_attempts: int = 2,
) -> dict[str, Any]:
    worker = worker if isinstance(worker, dict) else {}
    worker_state = worker_state if isinstance(worker_state, dict) else {}
    return {
        "action": action,
        "reason": reason,
        "issue": str(issue),
        "issue_dir": issue_dir,
        "sub_state_file": sub_state_file,
        "phase": phase,
        "worker_role": worker.get("role"),
        "worker_cycle": worker.get("cycle"),
        "worker_state": worker_state.get("state"),
        "worker_state_file": worker.get("state_file") or worker_state.get("state_file"),
        "worker_done_file": worker.get("done_file") or worker_state.get("done_file"),
        "worker_result_file": worker.get("result_file") or worker_state.get("result_file"),
        "recovery_attempt": attempt,
        "max_recovery_attempts": max_attempts,
    }


def completed_issue_numbers(state: dict[str, Any]) -> set[int]:
    completed: set[int] = set()
    for key, value in state.get("issue_registry", {}).items():
        if isinstance(value, dict) and value.get("status") == "completed":
            completed.add(int(key))
    return completed


def issue_dependencies_completed(info: dict[str, Any], completed: set[int]) -> bool:
    return all(int(dep) in completed for dep in info.get("deps", []))


def ready_issues(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    registry = state.get("issue_registry", {})
    completed = completed_issue_numbers(state)
    ready: list[str] = []
    for issue in state.get("execution_plan", {}).get("topo_order", []):
        if len(ready) >= args.limit:
            break
        info = registry.get(str(issue), {})
        if not isinstance(info, dict):
            continue
        if info.get("status") != "pending":
            continue
        if not issue_dependencies_completed(info, completed):
            continue
        context_file = info.get("context_file") or info.get("sub_coord_context_file")
        if context_file:
            ready.append(str(issue))
    print(" ".join(ready))
    return 0


def ready_missing_context_count(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    registry = state.get("issue_registry", {})
    completed = completed_issue_numbers(state)
    count = 0
    for issue in state.get("execution_plan", {}).get("topo_order", []):
        info = registry.get(str(issue), {})
        if not isinstance(info, dict):
            continue
        if info.get("status") != "pending":
            continue
        if not issue_dependencies_completed(info, completed):
            continue
        context_file = info.get("context_file") or info.get("sub_coord_context_file") or ""
        if not context_file:
            count += 1
    print(count)
    return 0


def context_file_for(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    info = state.get("issue_registry", {}).get(str(args.issue), {})
    if not isinstance(info, dict):
        info = {}
    print(info.get("context_file") or info.get("sub_coord_context_file") or "")
    return 0


def parallel_jobs(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    print(state.get("execution_plan", {}).get("parallel_jobs", 4))
    return 0


def mark_in_progress(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    entry = issue_entry(state, args.issue)
    entry.update(
        {
            "status": "in_progress",
            "context_file": args.context_file,
            "issue_dir": args.issue_dir,
            "pid": int(args.pid),
            "started_at": int(time.time()),
            "log_file": args.log_file,
            "done_file": args.done_file,
            "report_file": args.report_file,
        }
    )
    active = [str(value) for value in state.setdefault("active_pool_issues", [])]
    if str(args.issue) not in active:
        active.append(str(args.issue))
    state["active_pool_issues"] = active
    save_json(args.state_file, state)
    return 0


def append_summary(state: dict[str, Any], status: str, summary: dict[str, Any]) -> None:
    if status == "merge_recovery":
        state.setdefault("merge_recovery_summaries", []).append(summary)
    elif status == "completed":
        state.setdefault("completed_summaries", []).append(summary)
    else:
        state.setdefault("completed_summaries", []).append(summary)


def finalize_issue(args: argparse.Namespace) -> int:
    report = load_report(args.report_file)
    outcome = report.get("outcome", "blocked")
    status = "merge_recovery" if outcome == "merge_failed" else outcome

    state = load_json(args.state_file)
    entry = issue_entry(state, args.issue)
    entry["status"] = status
    if outcome == "merge_failed":
        entry["failed_merge_report_file"] = args.report_file
        entry["blocking_reasons"] = unique(entry.get("blocking_reasons", []) + ["merge recovery required"])

    state["active_pool_issues"] = [
        value for value in state.get("active_pool_issues", []) if str(value) != str(args.issue)
    ]
    metrics = report_file_metrics(report)
    summary = {
        "issue": int(args.issue),
        "outcome": status,
        "summary": report.get("summary"),
        "verification": report.get("verification") if isinstance(report.get("verification"), dict) else {},
        "report_file": args.report_file,
        "model_usage": compact_model_usage(report),
        "files_modified_count": metrics["files_modified_count"],
        "lines_added": metrics["lines_added"],
        "lines_deleted": metrics["lines_deleted"],
        "review_cycles": report.get("review_cycles", 0),
        "commit_sha": report.get("commit_sha"),
    }
    append_summary(state, status, summary)
    state.setdefault("ledger_rows", []).append(
        f"STATUS|type=ledger|task={args.issue}|outcome={status}|report={args.report_file}"
    )
    save_json(args.state_file, state)
    print(status)
    return 0


def analyze_sub_coord_failure(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    report = load_report(args.report_file)
    outcome = report.get("outcome")
    if isinstance(outcome, str) and outcome in TERMINAL_OUTCOMES:
        issue_dir = issue_dir_for_report(state, args.issue, args.report_file)
        decision = compact_worker_decision(
            action="finalize",
            reason="terminal-report-present",
            issue=args.issue,
            issue_dir=issue_dir,
            sub_state_file=str(Path(issue_dir) / "sub-state.json") if issue_dir else "",
            phase=None,
            attempt=recovery_attempt(issue_entry(state, args.issue)) + 1,
            max_attempts=recovery_max_attempts(args),
        )
        print(json.dumps(decision, sort_keys=True))
        return 0

    entry = issue_entry(state, args.issue)
    issue_dir = issue_dir_for_report(state, args.issue, args.report_file)
    sub_state_file = str(Path(issue_dir) / "sub-state.json") if issue_dir else ""
    attempt = recovery_attempt(entry) + 1
    max_attempts = recovery_max_attempts(args)

    if attempt > max_attempts:
        decision = compact_worker_decision(
            action="block",
            reason="sub-coordinator-recovery-attempts-exhausted",
            issue=args.issue,
            issue_dir=issue_dir,
            sub_state_file=sub_state_file,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        print(json.dumps(decision, sort_keys=True))
        return 0

    sub_state = load_optional_json(sub_state_file)
    if not sub_state:
        decision = compact_worker_decision(
            action="block",
            reason="missing-sub-state",
            issue=args.issue,
            issue_dir=issue_dir,
            sub_state_file=sub_state_file,
            attempt=attempt,
            max_attempts=max_attempts,
        )
        print(json.dumps(decision, sort_keys=True))
        return 0

    phase = sub_state.get("phase") if isinstance(sub_state.get("phase"), str) else None
    workers = sub_state.get("in_flight_agents")
    workers = workers if isinstance(workers, list) else []
    worker_decisions: list[tuple[dict[str, Any], dict[str, Any], bool, bool]] = []
    for item in workers:
        if not isinstance(item, dict):
            continue
        state_file = item.get("state_file") if isinstance(item.get("state_file"), str) else ""
        worker_state = load_optional_json(state_file)
        done_file = item.get("done_file") if isinstance(item.get("done_file"), str) else ""
        result_file = item.get("result_file") if isinstance(item.get("result_file"), str) else ""
        done_present = file_nonempty(done_file) or bool(worker_state.get("done") is True)
        result_present = file_has_json(result_file) or bool(worker_state.get("result_present") is True)
        worker_decisions.append((item, worker_state, done_present, result_present))

    for worker, worker_state, done_present, result_present in worker_decisions:
        state_name = worker_state.get("state")
        if state_name in LIVE_WORKER_STATES and not (done_present and result_present):
            decision = compact_worker_decision(
                action="wait_worker",
                reason="in-flight-worker-running",
                issue=args.issue,
                issue_dir=issue_dir,
                sub_state_file=sub_state_file,
                phase=phase,
                worker=worker,
                worker_state=worker_state,
                attempt=attempt,
                max_attempts=max_attempts,
            )
            print(json.dumps(decision, sort_keys=True))
            return 0

    for worker, worker_state, done_present, result_present in worker_decisions:
        state_name = worker_state.get("state")
        if state_name in FINISHED_WORKER_STATES or done_present or result_present:
            reason = "in-flight-worker-finished" if state_name == "completed" or result_present else "in-flight-worker-failed"
            decision = compact_worker_decision(
                action="spawn_recovery",
                reason=reason,
                issue=args.issue,
                issue_dir=issue_dir,
                sub_state_file=sub_state_file,
                phase=phase,
                worker=worker,
                worker_state=worker_state,
                attempt=attempt,
                max_attempts=max_attempts,
            )
            print(json.dumps(decision, sort_keys=True))
            return 0

    decision = compact_worker_decision(
        action="spawn_recovery",
        reason="sub-state-present-no-in-flight-worker",
        issue=args.issue,
        issue_dir=issue_dir,
        sub_state_file=sub_state_file,
        phase=phase,
        attempt=attempt,
        max_attempts=max_attempts,
    )
    print(json.dumps(decision, sort_keys=True))
    return 0


def write_sub_coord_recovery_context(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    entry = state.get("issue_registry", {}).get(str(args.issue), {})
    if not isinstance(entry, dict):
        entry = {}
    original_context = (
        entry.get("sub_coord_original_context_file")
        or entry.get("context_file")
        or entry.get("sub_coord_context_file")
        or ""
    )
    issue_dir = entry.get("issue_dir") or issue_dir_for_report(state, args.issue, entry.get("report_file", ""))
    sub_state_file = str(Path(str(issue_dir)) / "sub-state.json") if issue_dir else ""

    original_text = ""
    if original_context and os.path.exists(str(original_context)):
        with open(str(original_context), "r", encoding="utf-8") as handle:
            original_text = handle.read()

    Path(args.context_file).parent.mkdir(parents=True, exist_ok=True)
    with open(args.context_file, "w", encoding="utf-8") as handle:
        handle.write("SUB_COORD_RECOVERY_MODE=1\n")
        handle.write(f"SUB_COORD_RECOVERY_ATTEMPT={args.attempt}\n")
        handle.write(f"SUB_COORD_RECOVERY_REASON={args.reason}\n")
        handle.write(f"SUB_COORD_STATE_FILE={sub_state_file}\n")
        handle.write(f"SUB_COORD_ORIGINAL_CONTEXT_FILE={original_context}\n\n")
        handle.write("Recovery instructions:\n")
        handle.write("Do not restart from scratch.\n")
        handle.write("Read SUB_COORD_STATE_FILE before doing any phase work.\n")
        handle.write("Analyze in_flight_agents and their state_file, done_file, and result_file paths.\n")
        handle.write("If a worker result is valid, process it and continue from the next phase.\n")
        handle.write("Never rerun a phase that already has a valid result artifact.\n")
        handle.write("If a worker failed without a valid result, apply the existing worker artifact recovery contract.\n\n")
        handle.write(
            "Preserve the full original issue scope, acceptance criteria, verification commands, and recovery artifact paths in every worker retry payload.\n\n"
        )
        handle.write("Original sub-coordinator context follows:\n")
        handle.write(original_text)
        if original_text and not original_text.endswith("\n"):
            handle.write("\n")
    return 0


def mark_sub_coord_recovery_started(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    entry = issue_entry(state, args.issue)
    if not entry.get("sub_coord_original_context_file"):
        original_context = entry.get("context_file") or entry.get("sub_coord_context_file") or ""
        if original_context:
            entry["sub_coord_original_context_file"] = original_context
    entry["sub_coord_recovery_attempts"] = int(args.attempt)
    entry["sub_coord_recovery_last_reason"] = args.reason
    entry["sub_coord_recovery_context_file"] = args.context_file
    save_json(args.state_file, state)
    return 0


def mark_sub_coord_recovery_dispatch_failed(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    entry = issue_entry(state, args.issue)
    entry["sub_coord_recovery_dispatch_failed"] = True
    entry["sub_coord_recovery_last_report_file"] = args.report_file
    entry["blocking_reasons"] = unique(
        entry.get("blocking_reasons", []) + ["sub-coordinator recovery dispatcher failed"]
    )
    save_json(args.state_file, state)
    return 0


def write_merge_recovery_context(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    entry = state.get("issue_registry", {}).get(str(args.issue), {})
    if not isinstance(entry, dict):
        entry = {}
    payload = {
        "issue": {
            "number": int(args.issue),
            "title": entry.get("title", ""),
            "deps": entry.get("deps", []),
            "issue_branch": entry.get("issue_branch"),
            "worktree_path": entry.get("worktree_path"),
        },
        "run_branch": state.get("run_branch", {}),
        "failed_merge_report_file": entry.get("failed_merge_report_file") or entry.get("report_file"),
        "failed_merge_summary": {
            "blocking_reasons": entry.get("blocking_reasons", []),
            "dependency_proof": entry.get("dependency_proof"),
        },
        "completed_summaries": state.get("completed_summaries", []),
    }
    Path(args.context_file).parent.mkdir(parents=True, exist_ok=True)
    with open(args.context_file, "w", encoding="utf-8") as handle:
        handle.write("You are receiving merge recovery task data only.\n")
        handle.write(
            "Resolve only the failed merge for this issue. Do not select new issues, "
            "close GitHub issues, create a final PR, or modify main-state.json.\n\n"
        )
        handle.write(f"MERGE_RECOVERY_REPORT_FILE={args.recovery_report_file}\n")
        handle.write(f"RUN_WITH_IT_RESULT_FILE={args.recovery_report_file}\n")
        handle.write("OUTCOME=completed\n\n")
        handle.write("MERGE_RECOVERY_CONTEXT_JSON:\n")
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return 0


def finalize_merge_recovery(args: argparse.Namespace) -> int:
    report = load_report(args.report_file)
    outcome = report.get("outcome", "blocked")
    status = "completed" if outcome == "completed" else outcome
    if status not in {"completed", "failed-merge", "blocked"}:
        status = "blocked"

    state = load_json(args.state_file)
    entry = issue_entry(state, args.issue)
    entry["status"] = status
    entry["merge_recovery_report_file"] = args.report_file
    if status == "completed":
        entry["blocking_reasons"] = [
            reason for reason in entry.get("blocking_reasons", []) if reason != "merge recovery required"
        ]
        entry["commit_sha"] = report.get("merge_sha") or report.get("commit_sha")
    else:
        entry["blocking_reasons"] = unique(entry.get("blocking_reasons", []) + report.get("blocking_reasons", []))

    metrics = report_file_metrics(report)
    summary = {
        "issue": int(args.issue),
        "outcome": status,
        "summary": report.get("summary"),
        "verification": report.get("verification") if isinstance(report.get("verification"), dict) else {},
        "report_file": args.report_file,
        "model_usage": compact_model_usage(report),
        "files_modified_count": metrics["files_modified_count"],
        "lines_added": metrics["lines_added"],
        "lines_deleted": metrics["lines_deleted"],
        "review_cycles": report.get("review_cycles", 0),
        "commit_sha": report.get("merge_sha") or report.get("commit_sha"),
    }
    if status == "completed":
        state.setdefault("completed_summaries", []).append(summary)
    else:
        state.setdefault("merge_recovery_summaries", []).append(summary)
    state.setdefault("ledger_rows", []).append(
        f"STATUS|type=ledger|task={args.issue}|outcome={status}|report={args.report_file}|role=merge-recovery"
    )
    save_json(args.state_file, state)
    print(status)
    return 0


def mark_merge_recovery_dispatch_failed(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    entry = issue_entry(state, args.issue)
    entry["status"] = "blocked"
    entry["merge_recovery_report_file"] = args.report_file
    entry["blocking_reasons"] = unique(entry.get("blocking_reasons", []) + ["merge recovery dispatcher failed"])
    save_json(args.state_file, state)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="run-with-it shared state helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_state_file(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--state-file", required=True)

    ready = subparsers.add_parser("ready-issues")
    add_state_file(ready)
    ready.add_argument("--limit", type=int, required=True)
    ready.set_defaults(func=ready_issues)

    missing = subparsers.add_parser("ready-missing-context-count")
    add_state_file(missing)
    missing.set_defaults(func=ready_missing_context_count)

    context = subparsers.add_parser("context-file-for")
    add_state_file(context)
    context.add_argument("--issue", required=True)
    context.set_defaults(func=context_file_for)

    parallel = subparsers.add_parser("parallel-jobs")
    add_state_file(parallel)
    parallel.set_defaults(func=parallel_jobs)

    progress = subparsers.add_parser("mark-in-progress")
    add_state_file(progress)
    progress.add_argument("--issue", required=True)
    progress.add_argument("--pid", required=True)
    progress.add_argument("--context-file", required=True)
    progress.add_argument("--log-file", required=True)
    progress.add_argument("--done-file", required=True)
    progress.add_argument("--report-file", required=True)
    progress.add_argument("--issue-dir", required=True)
    progress.set_defaults(func=mark_in_progress)

    final = subparsers.add_parser("finalize-issue")
    add_state_file(final)
    final.add_argument("--issue", required=True)
    final.add_argument("--report-file", required=True)
    final.set_defaults(func=finalize_issue)

    sub_analysis = subparsers.add_parser("analyze-sub-coord-failure")
    add_state_file(sub_analysis)
    sub_analysis.add_argument("--issue", required=True)
    sub_analysis.add_argument("--report-file", required=True)
    sub_analysis.add_argument("--max-attempts", type=int, default=2)
    sub_analysis.set_defaults(func=analyze_sub_coord_failure)

    sub_recovery_context = subparsers.add_parser("write-sub-coord-recovery-context")
    add_state_file(sub_recovery_context)
    sub_recovery_context.add_argument("--issue", required=True)
    sub_recovery_context.add_argument("--context-file", required=True)
    sub_recovery_context.add_argument("--attempt", type=int, required=True)
    sub_recovery_context.add_argument("--reason", required=True)
    sub_recovery_context.set_defaults(func=write_sub_coord_recovery_context)

    sub_recovery_started = subparsers.add_parser("mark-sub-coord-recovery-started")
    add_state_file(sub_recovery_started)
    sub_recovery_started.add_argument("--issue", required=True)
    sub_recovery_started.add_argument("--attempt", type=int, required=True)
    sub_recovery_started.add_argument("--reason", required=True)
    sub_recovery_started.add_argument("--context-file", required=True)
    sub_recovery_started.set_defaults(func=mark_sub_coord_recovery_started)

    sub_recovery_failed = subparsers.add_parser("mark-sub-coord-recovery-dispatch-failed")
    add_state_file(sub_recovery_failed)
    sub_recovery_failed.add_argument("--issue", required=True)
    sub_recovery_failed.add_argument("--report-file", required=True)
    sub_recovery_failed.set_defaults(func=mark_sub_coord_recovery_dispatch_failed)

    recovery_context = subparsers.add_parser("write-merge-recovery-context")
    add_state_file(recovery_context)
    recovery_context.add_argument("--issue", required=True)
    recovery_context.add_argument("--context-file", required=True)
    recovery_context.add_argument("--recovery-report-file", required=True)
    recovery_context.set_defaults(func=write_merge_recovery_context)

    recovery_final = subparsers.add_parser("finalize-merge-recovery")
    add_state_file(recovery_final)
    recovery_final.add_argument("--issue", required=True)
    recovery_final.add_argument("--report-file", required=True)
    recovery_final.set_defaults(func=finalize_merge_recovery)

    recovery_failed = subparsers.add_parser("mark-merge-recovery-dispatch-failed")
    add_state_file(recovery_failed)
    recovery_failed.add_argument("--issue", required=True)
    recovery_failed.add_argument("--report-file", required=True)
    recovery_failed.set_defaults(func=mark_merge_recovery_dispatch_failed)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
