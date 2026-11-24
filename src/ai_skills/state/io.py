"""Main-state JSON transactions, liveness inspection, and report metric helpers."""
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
DEFAULT_WORKER_STALE_SECONDS = 1200


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

