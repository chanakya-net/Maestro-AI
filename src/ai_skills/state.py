"""Transactional state operations shared by the native orchestration runtime.
"""

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
from contextvars import ContextVar
from functools import wraps
from ai_skills.storage import StateStore, load_json as _load_json, write_json_atomic

_active_state = ContextVar("active_main_state", default=None)

def state_transaction(func):
    @wraps(func)
    def wrapped(args):
        if _active_state.get() is not None:
            return func(args)
        def update(state):
            token = _active_state.set((Path(args.state_file).resolve(), state))
            try:
                return func(args)
            finally:
                _active_state.reset(token)
        return StateStore(Path(args.state_file)).transaction(update)
    return wrapped


TERMINAL_OUTCOMES = {"completed", "failed-review", "blocked", "merge_failed", "failed-merge"}
LIVE_WORKER_STATES = {"ready", "starting", "running", "quiet", "stalled"}
FINISHED_WORKER_STATES = {"completed", "failed", "artifact-recovery-required"}


def load_json(path: str) -> dict[str, Any]:
    active = _active_state.get()
    if active is not None and Path(path).resolve() == active[0]:
        return active[1]
    value = _load_json(Path(path))
    if not isinstance(value, dict):
        raise ValueError(f"state must be a JSON object: {path}")
    return value


def save_json(path: str, value: dict[str, Any]) -> None:
    active = _active_state.get()
    if active is not None and Path(path).resolve() == active[0]:
        if value is not active[1]:
            active[1].clear()
            active[1].update(value)
        return
    write_json_atomic(Path(path), value)


from ai_skills.reports import load_json as load_report


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


def compaction_handoffs(entry: dict[str, Any]) -> int:
    value = entry.get("sub_coord_compaction_handoffs", 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def parse_epoch(value: Any) -> float | None:
    """Parse an ISO-8601 `...Z` stamp (the format every runtime artifact uses)
    into epoch seconds. Returns None for anything unparseable rather than
    raising: a malformed stamp must degrade to "no freshness evidence", never
    to a crash inside the recovery analyzer."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def path_mtime(path: Any) -> float | None:
    if not isinstance(path, str) or not path:
        return None
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def process_alive(pid: Any) -> bool | None:
    """Conservative cross-platform probe; never send a signal to inspect a PID."""
    import psutil
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        return None
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        return None


def worker_liveness(
    worker: dict[str, Any],
    worker_state: dict[str, Any],
    *,
    now: float,
    stale_seconds: int,
    dead_pid_grace_seconds: int,
) -> dict[str, Any]:
    """Decide whether a worker whose state file still reads "running" is really
    running. The state file is a snapshot maintained by that worker's dispatcher
    (rewritten every poll, ~20s); it is NOT self-invalidating. When the
    sub-coordinator dies it takes the dispatcher down with it, freezing the file
    mid-flight at state=running/alive=true forever. Trusting that frozen field
    is what wedged the pool: the supervisor waited on a worker that had been
    dead for hours because nothing ever re-derived liveness.

    Two independent signals, either of which proves death:
      * both recorded PIDs provably gone (POSIX only), after a short grace so a
        dispatcher observed mid-exit is not mistaken for an orphan;
      * no artifact has been touched in `stale_seconds` — a live dispatcher
        rewrites the state file every poll, so silence that long means the
        dispatcher itself is gone.
    Freshness takes the newest of state-file mtime, log mtime, and the recorded
    `updated_at`, so a worker still emitting output counts as alive even if its
    own state writer died."""
    candidates = [
        path_mtime(worker_state.get("state_file") or worker.get("state_file")),
        path_mtime(worker_state.get("log_file") or worker.get("log_file")),
        parse_epoch(worker_state.get("updated_at")),
        parse_epoch(worker_state.get("last_output_at")),
    ]
    stamps = [value for value in candidates if isinstance(value, (int, float))]
    freshest = max(stamps) if stamps else None
    age = int(max(now - freshest, 0)) if freshest is not None else None

    pids = [worker_state.get("runner_pid"), worker_state.get("dispatcher_pid"), worker.get("pid")]
    verdicts = [process_alive(pid) for pid in pids]
    known = [verdict for verdict in verdicts if verdict is not None]
    pids_alive = True if any(verdict is True for verdict in verdicts) else (False if known else None)

    if age is not None and age >= stale_seconds:
        return {"alive": False, "reason": "state-file-stale", "age_seconds": age, "pids_alive": pids_alive}
    if pids_alive is False and (age is None or age >= dead_pid_grace_seconds):
        return {"alive": False, "reason": "pids-dead", "age_seconds": age, "pids_alive": pids_alive}
    return {"alive": True, "reason": "fresh", "age_seconds": age, "pids_alive": pids_alive}


from ai_skills.recovery import compact_worker_decision


def completed_issue_numbers(state: dict[str, Any]) -> set[int]:
    completed: set[int] = set()
    for key, value in state.get("issue_registry", {}).items():
        if isinstance(value, dict) and value.get("status") == "completed":
            completed.add(int(key))
    return completed


def issue_dependencies_completed(info: dict[str, Any], completed: set[int]) -> bool:
    return all(int(dep) in completed for dep in info.get("deps", []))


def classify_ownership_scope(entry: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Classify an issue's declared ownership scope as one of:
    - ("absent", ())      — key missing or an explicitly empty list; no evidence
    - ("root", ())        — an entry names the repo root (".", "/"); overlaps everything
    - ("malformed", ())   — not a list, non-string entries, or a path alias that
                            could evade overlap detection: absolute paths, drive
                            or UNC paths, and parent-escaping ".." prefixes
    - ("valid", scopes)   — normalized, sorted, repo-relative path prefixes
    Root and malformed must never be conflated with absent: an explicit claim of
    "everything" or unparseable metadata has to fail closed, not open."""
    if "ownership_scope" not in entry or entry.get("ownership_scope") is None:
        return ("absent", ())
    raw = entry.get("ownership_scope")
    if not isinstance(raw, list):
        return ("malformed", ())
    scopes: set[str] = set()
    saw_root = False
    for value in raw:
        if not isinstance(value, str):
            return ("malformed", ())
        scope = value.strip().replace("\\", "/")
        while scope.startswith("./"):
            scope = scope[2:]
        if scope in {"", ".", "/"}:
            saw_root = True
            continue
        normalized = posixpath.normpath(scope)
        # Aliases of paths outside (or re-entering) the repo compare unequal to
        # their repo-relative form and would evade overlap detection.
        if (
            normalized.startswith("/")
            or normalized == ".."
            or normalized.startswith("../")
            or re.match(r"^[A-Za-z]:", normalized)
        ):
            return ("malformed", ())
        scope = normalized.strip("/")
        if not scope or scope == ".":
            saw_root = True
            continue
        scopes.add(scope)
    if saw_root:
        return ("root", ())
    if not scopes:
        return ("absent", ())
    return ("valid", tuple(sorted(scopes)))


def scope_comparison_prefix(scope: str) -> str:
    """Reduce a glob-bearing scope to its literal directory prefix so patterns
    like `src/api/*` compare as `src/api` instead of conflicting with everything."""
    glob_positions = [scope.find(char) for char in "*?[" if scope.find(char) != -1]
    if not glob_positions:
        return scope
    literal = scope[: min(glob_positions)]
    if "/" in literal:
        return literal.rsplit("/", 1)[0]
    return ""


def ownership_scopes_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    for left_scope in left:
        for right_scope in right:
            left_key = scope_comparison_prefix(left_scope.casefold())
            right_key = scope_comparison_prefix(right_scope.casefold())
            if not left_key or not right_key:
                # Glob with no literal directory prefix matches anywhere.
                return True
            if left_key == right_key:
                return True
            if left_key.startswith(f"{right_key}/") or right_key.startswith(f"{left_key}/"):
                return True
    return False


def classify_parallel_safe(entry: dict[str, Any]) -> str:
    """Classify `parallel_safe` as "absent", "true", "false", or "malformed".
    Only canonical boolean forms are accepted; anything else (e.g. "off",
    "garbage", numbers, lists) is malformed and must fail closed. Absent
    defaults to parallel: execution happens in isolated per-issue worktrees
    with a dedicated merge-recovery path, so missing metadata must not
    serialize the whole pool."""
    value = entry.get("parallel_safe")
    if value is None:
        return "absent"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        canonical = value.strip().casefold()
        if canonical in {"true", "1", "yes"}:
            return "true"
        if canonical in {"false", "0", "no"}:
            return "false"
    return "malformed"


def issues_can_run_together(
    left: dict[str, Any], right: dict[str, Any], absent_policy: str = "permissive"
) -> bool:
    """absent_policy governs issues with MISSING concurrency metadata:
    - "permissive" (legacy states without the capability flag): absent metadata
      admits in parallel, relying on worktree isolation plus merge recovery.
      This is explicit fail-open behavior for backward compatibility only.
    - "strict" (required for newly generated plans, which must derive metadata
      for every issue): absent metadata runs exclusively, because worktrees
      only isolate filesystem conflicts — not semantic conflicts, migrations,
      generated files, or shared external resources."""
    left_safe = classify_parallel_safe(left)
    right_safe = classify_parallel_safe(right)
    if "false" in (left_safe, right_safe) or "malformed" in (left_safe, right_safe):
        return False
    left_kind, left_scope = classify_ownership_scope(left)
    right_kind, right_scope = classify_ownership_scope(right)
    if "malformed" in (left_kind, right_kind):
        return False
    if "root" in (left_kind, right_kind):
        # An explicit repo-root scope overlaps everything.
        return False
    if absent_policy == "strict" and (
        "absent" in (left_safe, right_safe)
        or left_kind == "absent"
        or right_kind == "absent"
    ):
        return False
    if left_kind == "absent" or right_kind == "absent":
        return True
    return not ownership_scopes_overlap(left_scope, right_scope)


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def is_dependency_blocking_reason(reason: Any, deps: list[Any]) -> bool:
    """True if a blocking_reason is only about an upstream dependency, so it can
    be cleared once that dependency completes."""
    if not isinstance(reason, str):
        return False
    low = reason.lower()
    if "blocked by" in low or "blocked-by" in low or "blocked_by" in low:
        return True
    for dep in deps:
        if f"#{dep}" in reason or f"issue-{dep}" in reason or f"issue {dep}" in reason:
            return True
    return False


def is_stale_base_dependency_reason(reason: Any, deps: list[Any]) -> bool:
    """True only when a reason names one of this issue's dependencies exactly.

    Automatic stale-base requeue is intentionally stricter than the legacy
    auto-unblock matcher above: generic "blocked by" text and partial issue IDs
    are not evidence that a completed dependency was missing from the base.
    """
    if not isinstance(reason, str):
        return False
    for dep in deps:
        dep_id = re.escape(str(dep))
        if re.search(rf"#{dep_id}(?!\d)", reason):
            return True
        if re.search(rf"\bissue(?:-| ){dep_id}\b", reason, re.IGNORECASE):
            return True
    return False


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


def sub_coord_recovery_decision(args: argparse.Namespace) -> dict:
    from ai_skills.recovery import RecoveryEvidence, RecoveryPolicy, decide_recovery
    state = load_json(args.state_file)
    report = load_report(args.report_file)
    entry = state.get("issue_registry", {}).get(str(args.issue), {})
    issue_dir = issue_dir_for_report(state, args.issue, args.report_file)
    sub_state_file = str(Path(issue_dir) / "sub-state.json") if issue_dir else ""
    sub_state = load_optional_json(sub_state_file)
    now = time.time()
    workers = []
    for item in sub_state.get("in_flight_agents", []) if isinstance(sub_state.get("in_flight_agents"), list) else []:
        if not isinstance(item, dict):
            continue
        snapshot = load_optional_json(item.get("state_file") if isinstance(item.get("state_file"), str) else "")
        done = file_nonempty(item.get("done_file")) or snapshot.get("done") is True
        result = file_has_json(item.get("result_file")) or snapshot.get("result_present") is True
        liveness = worker_liveness(item, snapshot, now=now,
            stale_seconds=max(int(getattr(args, "worker_stale_seconds", 600) or 0), 60),
            dead_pid_grace_seconds=max(int(getattr(args, "worker_dead_pid_grace_seconds", 60) or 0), 0))
        workers.append((item, snapshot, done, result, liveness))
    evidence = RecoveryEvidence(issue=str(args.issue), issue_dir=issue_dir, sub_state_file=sub_state_file,
        report=report, sub_state=sub_state, recovery_attempts=recovery_attempt(entry),
        compaction_handoffs=compaction_handoffs(entry), workers=tuple(workers),
        wait_elapsed_seconds=max(int(getattr(args, "wait_elapsed_seconds", 0) or 0), 0))
    policy = RecoveryPolicy(max_attempts=recovery_max_attempts(args),
        max_compaction_handoffs=max(int(getattr(args, "max_compaction_handoffs", 6) or 0), 0),
        max_wait_seconds=max(int(getattr(args, "max_wait_seconds", 3600) or 0), 0))
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


@state_transaction
def mark_sub_coord_recovery_started(args: argparse.Namespace) -> int:
    state = load_json(args.state_file)
    entry = issue_entry(state, args.issue)
    if not entry.get("sub_coord_original_context_file"):
        original_context = entry.get("context_file") or entry.get("sub_coord_context_file") or ""
        if original_context:
            entry["sub_coord_original_context_file"] = original_context
    # A compaction handoff spends the handoff budget, not the failure budget —
    # the analyzer derives the attempt number from whichever counter applies, so
    # the write has to land on the same one.
    if args.reason == "sub-coordinator-context-exhausted":
        entry["sub_coord_compaction_handoffs"] = int(args.attempt)
    else:
        entry["sub_coord_recovery_attempts"] = int(args.attempt)
    entry["sub_coord_recovery_last_reason"] = args.reason
    entry["sub_coord_recovery_context_file"] = args.context_file
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
        sub_state_file = entry.get("sub_coord_state_file") or (
            os.path.join(entry.get("issue_dir", ""), "sub-state.json") if entry.get("issue_dir") else ""
        )
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
    sub_analysis.add_argument("--worker-stale-seconds", type=int, default=600)
    sub_analysis.add_argument("--worker-dead-pid-grace-seconds", type=int, default=60)
    sub_analysis.add_argument("--wait-elapsed-seconds", type=int, default=0)
    sub_analysis.add_argument("--max-wait-seconds", type=int, default=3600)
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
