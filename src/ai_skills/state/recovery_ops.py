"""Sub-Coordinator failure analysis, recovery contexts, merge recovery, and requeue."""
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
    completed_issue_numbers,
    is_dependency_blocking_reason,
    is_stale_base_dependency_reason,
    issue_dependencies_completed,
    utc_stamp,
)
from ai_skills.state.pool_ops import (
    append_summary,
    auto_unblock_dependents,
    complete_pool_attempt,
    quarantine_artifacts,
    stale_terminal_artifact_paths,
    sub_coord_terminal_marker_paths,
)

def _has_native_child_evidence(items) -> bool:
    if not isinstance(items, list):
        return True
    for item in items:
        if not isinstance(item, dict):
            return True
        if item.get("run_id") or item.get("attempt_id"):
            return True
        state_file = item.get("state_file")
        if isinstance(state_file, str) and state_file:
            config_path = Path(state_file + ".config.json")
            if config_path.exists() or config_path.is_symlink():
                return True
            snapshot = load_optional_json(state_file)
            if any(snapshot.get(key) for key in ("run_id", "attempt_id", "launcher_identity", "provider_identity")):
                return True
    return False


def _recovery_workers(sub_state, issue, issue_dir, args, *, check_ownership):
    items = sub_state.get("in_flight_agents", [])
    if not isinstance(items, list):
        items = [None] if check_ownership else []
    now = time.time()
    for item in items:
        if not isinstance(item, dict) and not check_ownership:
            continue
        worker = item if isinstance(item, dict) else {}
        snapshot = load_optional_json(worker.get("state_file") if isinstance(worker.get("state_file"), str) else "")
        if check_ownership:
            liveness = _child_recovery_evidence(issue, issue_dir, worker, snapshot, args)
            yield worker, snapshot, False, False, liveness
            continue
        done = file_nonempty(worker.get("done_file")) or snapshot.get("done") is True
        result = file_has_json(worker.get("result_file")) or snapshot.get("result_present") is True
        liveness = worker_liveness(worker, snapshot, now=now,
            stale_seconds=max(int(getattr(args, "worker_stale_seconds", DEFAULT_WORKER_STALE_SECONDS) or 0), 60),
            dead_pid_grace_seconds=max(int(getattr(args, "worker_dead_pid_grace_seconds", 60) or 0), 0))
        yield worker, snapshot, done, result, liveness


def _child_recovery_evidence(issue, issue_dir, worker, snapshot, args):
    from ai_skills.dispatch import inspect_recovery_child

    result = inspect_recovery_child(issue, issue_dir, worker,
        reconcile=getattr(args, "reconcile_children", False))
    ownership = "live" if result.state == "running" else "unknown"
    if result.state in ("released", "completed", "failed", "interrupted", "artifact-recovery-required"):
        ownership = "released"
    return {"ownership": ownership, "reason": result.reason,
        "attempt_id": worker.get("attempt_id") or snapshot.get("attempt_id"),
        "handoff_state": result.state if ownership == "released" else None}


def sub_coord_recovery_decision(args: argparse.Namespace) -> dict:
    from ai_skills.recovery import RecoveryEvidence, RecoveryPolicy, decide_recovery

    state = load_json(args.state_file)
    report = load_report(args.report_file) if getattr(args, "accept_terminal_report", True) else {}
    entry = state.get("issue_registry", {}).get(str(args.issue), {})
    issue_dir = issue_dir_for_report(state, args.issue, args.report_file)
    sub_state_file = str(Path(issue_dir) / "sub-state.json") if issue_dir else ""
    sub_state = load_optional_json(sub_state_file)
    policy = RecoveryPolicy(max_attempts=recovery_max_attempts(args),
        max_compaction_handoffs=max(int(getattr(args, "max_compaction_handoffs", 6) or 0), 0),
        max_wait_seconds=max(int(getattr(args, "max_wait_seconds", 0) or 0), 0))
    check_ownership = policy.max_wait_seconds > 0 or _has_native_child_evidence(sub_state.get("in_flight_agents", []))
    workers = tuple(_recovery_workers(sub_state, str(args.issue), issue_dir, args,
        check_ownership=check_ownership))
    evidence = RecoveryEvidence(issue=str(args.issue), issue_dir=issue_dir, sub_state_file=sub_state_file,
        report=report, sub_state=sub_state, recovery_attempts=recovery_attempt(entry),
        compaction_handoffs=compaction_handoffs(entry), workers=workers,
        wait_elapsed_seconds=max(int(getattr(args, "wait_elapsed_seconds", 0) or 0), 0))
    return decide_recovery(evidence, policy).details


def analyze_sub_coord_failure(args: argparse.Namespace) -> int:
    print(json.dumps(sub_coord_recovery_decision(args), sort_keys=True))
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
        if args.reason == "in-flight-worker-orphaned":
            handle.write(
                "The in-flight worker recorded in sub-state.json was orphaned: its dispatcher died with your\n"
                "predecessor, so its state file is a frozen snapshot and its `state` field is not a live claim.\n"
                "Treat that worker as dead. Re-dispatch the phase unless a valid result artifact already exists.\n\n"
            )
        if args.reason == "sub-coordinator-context-exhausted":
            handle.write(
                "Your predecessor halted for a compaction handoff after reaching its context budget. That is not a\n"
                "failure and nothing needs repairing: you have a fresh context window. Resume the persisted phase.\n\n"
            )
        handle.write(
            "Preserve the full original issue scope, acceptance criteria, verification commands, and recovery artifact paths in every worker retry payload.\n\n"
        )
        handle.write("Original sub-coordinator context follows:\n")
        handle.write(original_text)
        if original_text and not original_text.endswith("\n"):
            handle.write("\n")
    return 0


def record_sub_coord_recovery(entry, *, context_file, attempt, reason, budget):
    if not entry.get("sub_coord_original_context_file"):
        original_context = entry.get("context_file") or entry.get("sub_coord_context_file")
        if original_context:
            entry["sub_coord_original_context_file"] = original_context
    entry[budget] = int(attempt)
    entry["sub_coord_recovery_last_reason"] = reason
    entry["sub_coord_recovery_context_file"] = context_file


@state_transaction
def mark_sub_coord_recovery_started(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    budget = ("sub_coord_compaction_handoffs" if args.reason == "sub-coordinator-context-exhausted"
              else "sub_coord_recovery_attempts")
    record_sub_coord_recovery(issue_entry(state, args.issue), context_file=args.context_file,
        attempt=args.attempt, reason=args.reason, budget=budget)
    save_json(args.state_file, state)
    return 0


@state_transaction
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


@state_transaction
def finalize_merge_recovery(args: argparse.Namespace) -> int:
    report = load_report(args.report_file)
    outcome = report.get("outcome", "blocked")
    status = "completed" if outcome == "completed" else outcome
    if status not in {"completed", "failed-merge", "blocked"}:
        status = "blocked"

    state = load_json(args.state_file)
    complete_pool_attempt(state, args)
    entry = issue_entry(state, args.issue)
    entry["status"] = status
    entry["report_file"] = args.report_file
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
    if not getattr(args, "quiet", False):
        print(status)
    return 0


@state_transaction
def mark_merge_recovery_dispatch_failed(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    entry = issue_entry(state, args.issue)
    entry["status"] = "blocked"
    entry["merge_recovery_report_file"] = args.report_file
    entry["blocking_reasons"] = unique(entry.get("blocking_reasons", []) + ["merge recovery dispatcher failed"])
    save_json(args.state_file, state)
    return 0


@state_transaction
def requeue_issue(args: argparse.Namespace) -> int:
    """Force a fresh retry: quarantine stale terminal artifacts and reset the
    issue to pending so the pool re-dispatches it from scratch. Programmatic
    replacement for the manual clean-requeue done for issue 618."""
    state = load_json(args.state_file)
    entry = issue_entry(state, args.issue)
    issue_dir = entry.get("issue_dir", "") or issue_dir_for_report(state, args.issue, entry.get("report_file", ""))
    timestamp = utc_stamp()
    archived = quarantine_artifacts(
        issue_dir, stale_terminal_artifact_paths(entry, issue_dir), f"requeue-{timestamp}"
    )
    entry["status"] = "pending"
    entry["blocking_reasons"] = []
    entry["requeued_at"] = timestamp
    entry["sub_coord_recovery_attempts"] = 0
    entry["sub_coord_compaction_handoffs"] = 0
    if getattr(args, "reason", ""):
        entry["manual_requeue_reason"] = args.reason
    if archived:
        entry["requeue_archive_dir"] = archived
    state["active_pool_issues"] = [
        value for value in state.get("active_pool_issues", []) if str(value) != str(args.issue)
    ]
    state.setdefault("auto_repairs", []).append(
        {
            "at": timestamp,
            "kind": f"requeue-issue-{args.issue}",
            "issue": int(args.issue),
            "reason": getattr(args, "reason", ""),
            "archive_dir": archived,
        }
    )
    save_json(args.state_file, state)
    print("pending")
    return 0


def quarantine_sub_coord_markers(args: argparse.Namespace) -> int:
    """Move any pre-existing sub-coordinator terminal markers aside before a
    (re)dispatch so the fresh runner can never reuse a poisoned report and no-op.
    Runs at the dispatch chokepoint, so it is correct no matter how the issue was
    requeued (programmatic `requeue`, manual reset, or auto recovery). Idempotent:
    prints the archive dir when something moved, or an empty line when already clean."""
    label = args.label or f"predispatch-{utc_stamp()}"
    archived = quarantine_artifacts(
        args.issue_dir, sub_coord_terminal_marker_paths(args.issue_dir), label
    )
    print(archived or "")
    return 0


