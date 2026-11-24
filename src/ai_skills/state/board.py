"""Status board rendering, unreachable-issue analysis, and CLI entrypoint."""
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
    issue_dir_for_report,
    issue_entry,
    load_json,
    load_optional_json,
    unique,
)
from ai_skills.state.concurrency import completed_issue_numbers, issue_dependencies_completed
from ai_skills.state.pool_ops import (
    active_pool_entries,
    admission_deferrals,
    context_file_for,
    finalize_issue,
    mark_in_progress,
    parallel_jobs,
    ready_issues,
    ready_missing_context_count,
    status_counts,
)
from ai_skills.state.recovery_ops import (
    analyze_sub_coord_failure,
    finalize_merge_recovery,
    mark_merge_recovery_dispatch_failed,
    mark_sub_coord_recovery_dispatch_failed,
    mark_sub_coord_recovery_started,
    quarantine_sub_coord_markers,
    requeue_issue,
    write_merge_recovery_context,
    write_sub_coord_recovery_context,
)

def issue_stage(entry: dict[str, Any], completed: set[int]) -> str:
    """Compact, human-readable current stage for an issue (no detail)."""
    status = entry.get("status")
    if status == "completed":
        return "done"
    if status == "merge_recovery":
        return "merge-recovery"
    if status == "blocked":
        return "blocked"
    if status == "pending":
        if not issue_dependencies_completed(entry, completed):
            pending = [str(dep) for dep in entry.get("deps", []) if int(dep) not in completed]
            return "blocked:" + ",".join(pending) if pending else "queued"
        return "ready"
    if status == "in_progress":
        wait = entry.get("worker_wait")
        if isinstance(wait, dict) and wait.get("holding") and wait.get("expired"):
            role = wait.get("role") or "worker"
            cycle = f" cycle {wait['cycle']}" if wait.get("cycle") is not None else ""
            return (f"recovery-wait-expired({role}{cycle}, {wait['ownership']}, "
                    f"{wait['max_wait_seconds']}s limit)")
        # Pool registration may point at dispatcher liveness rather than the
        # coordinator's semantic state. Prefer explicit stage evidence, then
        # consult the canonical coordinator state without changing ownership.
        sub_state_files = (
            entry.get("sub_coord_state_file"),
            os.path.join(entry["issue_dir"], "sub-state.json") if entry.get("issue_dir") else "",
        )
        for sub_state_file in sub_state_files:
            sub = load_optional_json(sub_state_file)
            phase = sub.get("phase") if isinstance(sub.get("phase"), str) else None
            in_flight = sub.get("in_flight_agents") if isinstance(sub.get("in_flight_agents"), list) else []
            if in_flight and isinstance(in_flight[0], dict):
                role = in_flight[0].get("role") or phase or "running"
                cycle = in_flight[0].get("cycle")
                return f"{role}(cyc{cycle})" if isinstance(cycle, int) else str(role)
            if phase:
                return phase
        return "running"
    return str(status or "unknown")


def unreachable_issue_roots(state: dict) -> dict[str, set[str]]:
    """Print `issue:root[,root]` pairs for every issue that can never become
    ready, because a dependency reached a terminal non-completed state.

    Only `completed` satisfies a dependency, so a `failed-review` / `blocked` /
    `merge_failed` issue silently strands its whole downstream cone: those
    dependents just sit at `pending` and the heartbeat keeps counting them as
    work-in-hand while the pool drains to empty. That produced a run which
    finished with six issues never dispatched and no signal explaining why.
    Attribution is transitive, and roots are the terminal issues actually to
    blame — not the intermediate dependents that merely inherited the block."""
    registry = state.get("issue_registry", {})
    terminal_dead = {
        str(key)
        for key, entry in registry.items()
        if isinstance(entry, dict)
        and str(entry.get("status") or "") in {"failed-review", "blocked", "merge_failed", "failed-merge"}
    }
    if not terminal_dead:
        return {}

    roots: dict[str, set[str]] = {}
    # Iterate to a fixed point so a dependent two or more hops downstream of the
    # terminal issue is attributed to the same root rather than being missed.
    changed = True
    while changed:
        changed = False
        for key, entry in registry.items():
            if not isinstance(entry, dict) or str(entry.get("status") or "") not in {"pending", "waiting"}:
                continue
            blame: set[str] = set()
            for dep in entry.get("deps", []) or []:
                dep_key = str(dep)
                if dep_key in terminal_dead:
                    blame.add(dep_key)
                elif dep_key in roots:
                    blame |= roots[dep_key]
            if blame and blame != roots.get(str(key)):
                roots[str(key)] = blame
                changed = True

    return roots


def unreachable_issues(args: argparse.Namespace) -> int:
    roots = unreachable_issue_roots(load_json(args.state_file))
    pairs = [
        f"{issue}:{','.join(sorted(blame, key=lambda value: int(value) if value.isdigit() else 0))}"
        for issue, blame in sorted(roots.items(), key=lambda item: int(item[0]) if item[0].isdigit() else 0)
    ]
    print(";".join(pairs))
    return 0


def status_board(args: argparse.Namespace) -> int:
    """One compact line per issue showing its current stage. With --oneline,
    emit a single pipe-joined board line for the live status feed."""
    state = load_json(args.state_file)
    registry = state.get("issue_registry", {})
    completed = completed_issue_numbers(state)
    order = [str(issue) for issue in state.get("execution_plan", {}).get("topo_order", [])]
    if not order:
        order = sorted(registry.keys(), key=lambda value: int(value) if str(value).isdigit() else 0)
    cells: list[tuple[str, str]] = []
    for key in order:
        entry = registry.get(str(key), {})
        if not isinstance(entry, dict):
            continue
        cells.append((str(key), issue_stage(entry, completed)))
    if getattr(args, "oneline", False):
        print(" | ".join(f"#{key} {stage}" for key, stage in cells))
    else:
        for key, stage in cells:
            print(f"#{key:<5} {stage}")
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

    counts_cmd = subparsers.add_parser("status-counts")
    add_state_file(counts_cmd)
    counts_cmd.set_defaults(func=status_counts)

    missing = subparsers.add_parser("ready-missing-context-count")
    add_state_file(missing)
    missing.set_defaults(func=ready_missing_context_count)

    deferrals = subparsers.add_parser("admission-deferrals")
    add_state_file(deferrals)
    deferrals.set_defaults(func=admission_deferrals)

    active_entries = subparsers.add_parser("active-pool-entries")
    add_state_file(active_entries)
    active_entries.set_defaults(func=active_pool_entries)

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
    progress.add_argument("--sub-coord-state-file", default="")
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
    sub_analysis.add_argument("--max-compaction-handoffs", type=int, default=6)
    # A live dispatcher rewrites its worker state file every poll (~20s), so
    # silence beyond this means the dispatcher itself is gone and the file's
    # "running" is stale. Floor of 60s inside the analyzer.
    sub_analysis.add_argument("--worker-stale-seconds", type=int, default=DEFAULT_WORKER_STALE_SECONDS)
    sub_analysis.add_argument("--worker-dead-pid-grace-seconds", type=int, default=60)
    sub_analysis.add_argument("--wait-elapsed-seconds", type=int, default=0)
    sub_analysis.add_argument("--max-wait-seconds", type=int, default=0)
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

    requeue = subparsers.add_parser("requeue")
    add_state_file(requeue)
    requeue.add_argument("--issue", required=True)
    requeue.add_argument("--reason", default="")
    requeue.set_defaults(func=requeue_issue)

    quarantine_markers = subparsers.add_parser("quarantine-sub-coord-markers")
    quarantine_markers.add_argument("--issue-dir", required=True)
    quarantine_markers.add_argument("--label", default="")
    quarantine_markers.set_defaults(func=quarantine_sub_coord_markers)

    board = subparsers.add_parser("status-board")
    add_state_file(board)
    board.add_argument("--oneline", action="store_true", default=False)
    board.set_defaults(func=status_board)

    unreachable = subparsers.add_parser("unreachable-issues")
    add_state_file(unreachable)
    unreachable.set_defaults(func=unreachable_issues)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
