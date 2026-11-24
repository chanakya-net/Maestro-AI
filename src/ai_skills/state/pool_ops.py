"""Pool admission, issue finalization, dependent unblocking, and artifact quarantine."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import posixpath
import re
import sys
import time
from pathlib import Path
from typing import Any
from ai_skills.state.io import (
    DEFAULT_WORKER_STALE_SECONDS,
    FINISHED_WORKER_STATES,
    LIVE_WORKER_STATES,
    TERMINAL_OUTCOMES,
    compact_model_usage,
    compaction_handoffs,
    file_has_json,
    file_nonempty,
    issue_dir_for_report,
    issue_entry,
    load_json,
    load_optional_json,
    load_report,
    parse_epoch,
    path_mtime,
    process_alive,
    recovery_attempt,
    recovery_max_attempts,
    report_file_metrics,
    save_json,
    state_transaction,
    unique,
    worker_liveness,
)
from ai_skills.state.concurrency import (
    classify_ownership_scope,
    classify_parallel_safe,
    completed_issue_numbers,
    is_dependency_blocking_reason,
    is_stale_base_dependency_reason,
    issue_dependencies_completed,
    issues_can_run_together,
    ownership_scopes_overlap,
    scope_comparison_prefix,
    utc_stamp,
)

def quarantine_artifacts(issue_dir: str, paths: list[str], label: str) -> str | None:
    """Move stale terminal artifacts (report.json / done / sub-state) aside so a
    requeued issue is re-run fresh instead of reusing poisoned state. Returns the
    archive dir, or None if there was nothing to move."""
    if not issue_dir:
        return None
    seen: set[str] = set()
    existing: list[str] = []
    for path in paths:
        if not path:
            continue
        real = os.path.abspath(path)
        if real in seen:
            continue
        seen.add(real)
        if os.path.exists(path):
            existing.append(path)
    if not existing:
        return None
    archive_dir = os.path.join(issue_dir, "recovery", label)
    os.makedirs(archive_dir, exist_ok=True)
    for path in existing:
        try:
            os.replace(path, os.path.join(archive_dir, os.path.basename(path)))
        except OSError:
            pass
    return archive_dir


def stale_terminal_artifact_paths(entry: dict[str, Any], issue_dir: str) -> list[str]:
    paths = [entry.get("report_file", ""), entry.get("done_file", "")]
    if issue_dir:
        paths += [
            os.path.join(issue_dir, "report.json"),
            os.path.join(issue_dir, "sub-coordinator.done"),
            os.path.join(issue_dir, "sub-state.json"),
        ]
    return paths


# Sub-coordinator terminal markers whose *stale* presence makes a re-dispatched
# sub-coordinator no-op: the fresh runner finds a prior report/done/alive state,
# refuses to relaunch "duplicate" work, times out, and the control plane stamps a
# phantom "sub-coordinator recovery dispatcher failed". Deliberately EXCLUDES
# sub-state.json and workers/ -- those are resume state a recovery dispatch must
# keep so it continues from saved artifacts instead of restarting from scratch.
SUB_COORD_TERMINAL_MARKERS = (
    "report.json",
    "sub-coordinator.done",
    "sub-coordinator.state.json",
    "sub-coordinator.dispatch.out",
)


def sub_coord_terminal_marker_paths(issue_dir: str) -> list[str]:
    if not issue_dir:
        return []
    return [os.path.join(issue_dir, name) for name in SUB_COORD_TERMINAL_MARKERS]


def auto_unblock_dependents(
    state: dict[str, Any], completed_issue: str, completed: set[int], timestamp: str
) -> list[dict[str, Any]]:
    """When an issue completes, reset any dependent that is BLOCKED solely
    because of this (now-satisfied) dependency back to pending, quarantining its
    stale blocked artifacts so the pool re-dispatches it. Replaces the manual
    "reset-to-pending" + "durable-unblock" repairs seen for issue 631."""
    repairs: list[dict[str, Any]] = []
    registry = state.get("issue_registry", {})
    for key, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        deps = entry.get("deps", []) or []
        if int(completed_issue) not in [int(dep) for dep in deps]:
            continue
        if entry.get("status") != "blocked":
            continue
        if not issue_dependencies_completed(entry, completed):
            continue
        remaining = [r for r in entry.get("blocking_reasons", []) if not is_dependency_blocking_reason(r, deps)]
        if remaining:
            # Still blocked for a non-dependency reason — leave it alone.
            continue
        issue_dir = entry.get("issue_dir", "") or ""
        archived = quarantine_artifacts(
            issue_dir, stale_terminal_artifact_paths(entry, issue_dir), f"auto-unblock-{timestamp}"
        )
        entry["status"] = "pending"
        entry["blocking_reasons"] = []
        entry["auto_unblocked_at"] = timestamp
        entry["auto_unblocked_after_issue"] = int(completed_issue)
        if archived:
            entry["auto_unblock_archive_dir"] = archived
        repairs.append(
            {
                "at": timestamp,
                "kind": f"auto-unblock-issue-{key}-after-{completed_issue}-completed",
                "issue": int(key),
                "archive_dir": archived,
            }
        )
    return repairs


@state_transaction
def ready_issues(args: argparse.Namespace) -> int:
    from ai_skills.pool import select_ready
    state = load_json(args.state_file)
    decision = select_ready(state, limit=args.limit)
    state["last_admission_deferrals"] = decision.deferrals
    save_json(args.state_file, state)
    print(" ".join(decision.issues))
    return 0


def admission_deferrals(args: argparse.Namespace) -> int:
    """Print the deferrals recorded by the last ready-issues admission pass as
    compact `issue:reason` pairs, one line total, empty when nothing deferred."""
    state = load_json(args.state_file)
    deferrals = state.get("last_admission_deferrals", {})
    if not isinstance(deferrals, dict):
        deferrals = {}
    pairs = [f"{issue}:{reason}" for issue, reason in sorted(deferrals.items(), key=lambda item: str(item[0]))]
    print(",".join(pairs))
    return 0


def active_pool_entries(args: argparse.Namespace) -> int:
    """Print one tab-separated `issue pid report_file` line per active pool
    member still marked in_progress, so a restarted pool runner can re-attach."""
    state = load_json(args.state_file)
    registry = state.get("issue_registry", {})
    for issue in state.get("active_pool_issues", []):
        entry = registry.get(str(issue), {})
        if not isinstance(entry, dict) or entry.get("status") != "in_progress":
            continue
        pid = entry.get("pid") or 0
        report_file = entry.get("report_file") or ""
        print(f"{issue}\t{pid}\t{report_file}")
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
        if not context_file or not os.path.isfile(context_file):
            count += 1
    print(count)
    return 0


def status_counts(args: argparse.Namespace) -> int:
    """One compact pipe-joined counts line for the pool heartbeat: per-status
    totals plus how many dependency-ready issues are waiting on context files."""
    state = load_json(args.state_file)
    registry = state.get("issue_registry", {})
    completed = completed_issue_numbers(state)
    counts = {
        "total": 0,
        "completed": 0,
        "in_progress": 0,
        "pending": 0,
        "blocked": 0,
        "failed_review": 0,
        "merge_recovery": 0,
        "waiting_context": 0,
    }
    for key, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        counts["total"] += 1
        status = str(entry.get("status") or "unknown").replace("-", "_")
        if status in counts:
            counts[status] += 1
        if entry.get("status") == "pending" and issue_dependencies_completed(entry, completed):
            context_file = entry.get("context_file") or entry.get("sub_coord_context_file") or ""
            if not context_file or not os.path.isfile(context_file):
                counts["waiting_context"] += 1
    print("|".join(f"{key}={value}" for key, value in counts.items()))
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


@state_transaction
def mark_in_progress(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    entry = issue_entry(state, args.issue)
    payload = {
        "status": "in_progress",
        "context_file": args.context_file,
        "issue_dir": args.issue_dir,
        "pid": int(args.pid),
        "started_at": int(time.time()),
        "log_file": args.log_file,
        "done_file": args.done_file,
        "report_file": args.report_file,
    }
    if args.sub_coord_state_file:
        payload["sub_coord_state_file"] = args.sub_coord_state_file
    entry.update(payload)
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


def complete_pool_attempt(state: dict, args: argparse.Namespace) -> None:
    """Commit attempt and issue completion in one main-state transaction."""
    attempt_id = getattr(args, "pool_attempt_id", None)
    if attempt_id is None:
        return
    intent = state.get("pool_attempts", {}).get(str(args.issue), {})
    if intent.get("attempt_id") != attempt_id:
        raise ValueError("stale pool attempt cannot finalize the issue")
    intent["status"] = "finalized"
    state["active_pool_issues"] = [item for item in state.get("active_pool_issues", []) if str(item) != str(args.issue)]


@state_transaction
def finalize_issue(args: argparse.Namespace) -> int:
    report = load_report(args.report_file)
    outcome = report.get("outcome", "blocked")
    status = "merge_recovery" if outcome == "merge_failed" else outcome

    state = load_json(args.state_file)
    complete_pool_attempt(state, args)
    entry = issue_entry(state, args.issue)
    entry["status"] = status
    entry["report_file"] = args.report_file
    report_blocking_reasons = report.get("blocking_reasons", [])
    if not isinstance(report_blocking_reasons, list):
        report_blocking_reasons = []
    if status == "blocked":
        entry["blocking_reasons"] = unique(entry.get("blocking_reasons", []) + report_blocking_reasons)
        deps = entry.get("deps", []) or []
        completed = completed_issue_numbers(state)
        reasons = entry.get("blocking_reasons", [])
        stale_base_requeue_attempts = entry.get("stale_base_requeue_attempts", 0)
        if not isinstance(stale_base_requeue_attempts, int) or stale_base_requeue_attempts < 0:
            stale_base_requeue_attempts = 0
        if (
            deps
            and issue_dependencies_completed(entry, completed)
            and reasons
            and all(is_stale_base_dependency_reason(reason, deps) for reason in reasons)
            and stale_base_requeue_attempts < 1
        ):
            issue_dir = entry.get("issue_dir", "") or ""
            archived = quarantine_artifacts(
                issue_dir,
                stale_terminal_artifact_paths(entry, issue_dir),
                f"stale-base-{utc_stamp()}",
            )
            entry["status"] = "pending"
            entry["blocking_reasons"] = []
            entry["stale_base_requeued_at"] = utc_stamp()
            entry["stale_base_report_file"] = args.report_file
            entry["stale_base_requeue_attempts"] = stale_base_requeue_attempts + 1
            entry["sub_coord_recovery_attempts"] = 0
            entry["sub_coord_compaction_handoffs"] = 0
            if archived:
                entry["stale_base_archive_dir"] = archived
            state["active_pool_issues"] = [
                value for value in state.get("active_pool_issues", []) if str(value) != str(args.issue)
            ]
            state.setdefault("auto_repairs", []).append(
                {
                    "at": entry["stale_base_requeued_at"],
                    "kind": f"stale-base-requeue-issue-{args.issue}",
                    "issue": int(args.issue),
                    "report_file": args.report_file,
                    "archive_dir": archived,
                }
            )
            save_json(args.state_file, state)
            print("pending")
            return 0
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
    if status == "completed":
        # entry["status"] is already "completed" in-memory, so it is counted here.
        repairs = auto_unblock_dependents(state, args.issue, completed_issue_numbers(state), utc_stamp())
        if repairs:
            state.setdefault("auto_repairs", []).extend(repairs)
    save_json(args.state_file, state)
    if not getattr(args, "quiet", False):
        print(status)
    return 0


