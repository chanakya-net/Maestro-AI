"""Deterministic issue-level Sub-Coordinator state, routing, and report operations.

Moves Sub-Coordinator bookkeeping (sub-state.json persistence, attempt-numbered
worker configs, failure classification, review-loop transitions, review gating,
resume inspection, and compact report assembly) into validated Python commands.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from ai_skills import artifacts, dispatch, outcomes, resources, routing
from ai_skills.registry import BANDS, canonical_band
from ai_skills.storage import StateStore, load_json, write_json_atomic

SCHEMA_VERSION = 1
MAX_REVIEW_CYCLES = 8
DEFAULT_MAX_FALLBACKS = 2
DEFAULT_MAX_CAPABILITY_RETRIES = 2
DEFAULT_MAX_REVIEW_RETRIES = 2

ROLE_PROMPT_FILES = {
    "complexity": "complexity-prompt.md",
    "plan": "plan-prompt.md",
    "impl": "prompt.md",
    "review": "review-prompt.md",
    "modify": "modifier-prompt.md",
    "artifact-recovery": "artifact-recovery-prompt.md",
    "merge-recovery": "merge-recovery-prompt.md",
}


class IssueError(ValueError):
    """Invalid issue-level state, configuration, or transition."""


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sub_state_path(issue_dir: Path | str) -> Path:
    return Path(issue_dir).absolute() / "sub-state.json"


def _default_sub_state(issue: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "issue_number": str(issue) if issue else "",
        "phase": "bootstrap",
        "compaction_requested": False,
        "in_flight_agents": [],
        "review_history": [],
        "routes": [],
        "cycle": 1,
        "cycles_used": 0,
        "non_approval_count": 0,
        "issue_reviewer": None,
        "effective_complexity": None,
        "modify_band": None,
        "escalated": False,
        "redesign_required": False,
        "review_skipped": False,
        "updated_at": _utc_now(),
    }


def load_sub_state(issue_dir: Path | str, *, missing_ok: bool = False) -> dict[str, Any]:
    path = sub_state_path(issue_dir)
    data = load_json(path, missing=None if missing_ok else None)
    if data is None:
        if missing_ok:
            return _default_sub_state()
        raise IssueError(f"missing sub-state file: {path}")
    if not isinstance(data, dict):
        raise IssueError(f"sub-state must be a JSON object: {path}")
    return data


def _mutate_sub_state(issue_dir: Path | str, fn) -> dict[str, Any]:
    path = sub_state_path(issue_dir)
    store = StateStore(path)

    def _apply(state: dict[str, Any]) -> dict[str, Any]:
        if not state:
            state.update(_default_sub_state())
        fn(state)
        state["schema_version"] = SCHEMA_VERSION
        state["updated_at"] = _utc_now()
        return state

    return store.transaction(_apply)


# --------------------------------------------------------------------------- state verbs


def init_sub_state(
    issue_dir: Path | str,
    *,
    issue: str,
    worktree: str = "",
    issue_branch: str = "",
    issue_base_sha: str = "",
    issue_base_source: str = "",
    phase: str = "bootstrap",
) -> dict[str, Any]:
    def _update(state: dict[str, Any]) -> None:
        state["issue_number"] = str(issue)
        if phase:
            state["phase"] = phase
        if worktree:
            state["worktree"] = str(worktree)
        if issue_branch:
            state["issue_branch"] = str(issue_branch)
        if issue_base_sha:
            state["issue_base_sha"] = str(issue_base_sha)
        if issue_base_source:
            state["issue_base_source"] = str(issue_base_source)
        state.setdefault("compaction_requested", False)
        state.setdefault("in_flight_agents", [])
        state.setdefault("review_history", [])
        state.setdefault("routes", [])

    return _mutate_sub_state(issue_dir, _update)


def record_attempt(
    issue_dir: Path | str,
    *,
    state_file: Path | str,
    route_file: Path | str | None = None,
    phase: str | None = None,
) -> dict[str, Any]:
    sf = Path(state_file).absolute()
    cfg_path = Path(str(sf) + ".config.json")
    cfg = load_json(cfg_path, missing={})
    if not isinstance(cfg, dict):
        raise IssueError(f"invalid worker config record: {cfg_path}")
    agent_run = cfg.get("agent_run") if isinstance(cfg.get("agent_run"), dict) else cfg
    runtime = load_json(sf, missing={})
    if not isinstance(runtime, dict):
        runtime = {}
    route = load_json(Path(route_file), missing={}) if route_file else {}
    if not isinstance(route, dict):
        route = {}

    entry = {
        "role": str(agent_run.get("role") or route.get("role") or ""),
        "cycle": str(agent_run.get("cycle") or "1"),
        "pid": runtime.get("pid") or runtime.get("dispatcher_pid"),
        "attempt_id": str(agent_run.get("attempt_id") or runtime.get("attempt_id") or ""),
        "agent": str(route.get("agent") or agent_run.get("agent") or ""),
        "model": str(route.get("model") or agent_run.get("model") or ""),
        "effort": str(route.get("effort") if "effort" in route else agent_run.get("effort") or ""),
        "selection_reason": str(route.get("selection_reason") or "explicit"),
        "log_file": str(agent_run.get("log_file") or ""),
        "done_file": str(agent_run.get("done_file") or ""),
        "result_file": str(agent_run.get("result_file") or ""),
        "state_file": str(sf),
        "started_at": str(runtime.get("started_at") or _utc_now()),
    }

    def _update(state: dict[str, Any]) -> None:
        if phase:
            state["phase"] = phase
        if not state.get("issue_number") and agent_run.get("issue"):
            state["issue_number"] = str(agent_run["issue"])
        existing = [
            item for item in state.get("in_flight_agents", [])
            if isinstance(item, dict) and str(item.get("state_file")) != str(sf)
        ]
        existing.append(entry)
        state["in_flight_agents"] = existing
        if entry["agent"] and entry["model"]:
            routes = list(state.get("routes", []))
            if not any(r.get("state_file") == str(sf) for r in routes if isinstance(r, dict)):
                routes.append({
                    "role": entry["role"],
                    "cycle": entry["cycle"],
                    "agent": entry["agent"],
                    "model": entry["model"],
                    "effort": entry["effort"],
                    "selection_reason": entry["selection_reason"],
                    "state_file": str(sf),
                })
            state["routes"] = routes

    return _mutate_sub_state(issue_dir, _update)


def clear_attempt(
    issue_dir: Path | str,
    *,
    state_file: Path | str,
    phase: str | None = None,
) -> dict[str, Any]:
    sf = str(Path(state_file).absolute())

    def _update(state: dict[str, Any]) -> None:
        if phase:
            state["phase"] = phase
        state["in_flight_agents"] = [
            item for item in state.get("in_flight_agents", [])
            if isinstance(item, dict) and str(item.get("state_file")) != sf
        ]

    return _mutate_sub_state(issue_dir, _update)


def _coerce_value(raw: str) -> Any:
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw == "null":
        return None
    if re.fullmatch(r"-?[0-9]+", raw):
        return int(raw)
    return raw


def set_sub_state_fields(
    issue_dir: Path | str,
    *,
    phase: str | None = None,
    compaction_requested: bool | None = None,
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def _update(state: dict[str, Any]) -> None:
        if phase is not None:
            state["phase"] = phase
        if compaction_requested is not None:
            state["compaction_requested"] = compaction_requested
        for key, value in (fields or {}).items():
            if key in ("effective_complexity", "modify_band", "complexity_level") and isinstance(value, str):
                value = canonical_band(value)
            state[key] = value

    return _mutate_sub_state(issue_dir, _update)


# --------------------------------------------------------------------------- worker-config


def _next_attempt_number(role_dir: Path, cycle: str) -> int:
    prefix = f"cycle-{cycle}-attempt-"
    highest = 0
    if role_dir.is_dir():
        for entry in role_dir.iterdir():
            name = entry.name
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix):]
            match = re.match(r"([0-9]+)", rest)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def build_worker_config(
    *,
    issue_dir: Path | str,
    issue: str,
    role: str,
    cycle: str,
    context_file: Path | str,
    repo_root: Path | str,
    agent: str = "",
    model: str = "",
    effort: str = "",
    route_file: Path | str | None = None,
    attempt: int | None = None,
    asset_root: Path | str | None = None,
    prompt_file: Path | str | None = None,
    status_file: Path | str | None = None,
    events_log: Path | str | None = None,
    issue_base_sha: str = "",
    pre_spawn_head: str = "",
    execution_mode: str = "implement",
    retry_contract_file: Path | str | None = None,
    result_capture_file: Path | str | None = None,
    output_file: Path | str | None = None,
    detach: bool = True,
) -> dict[str, Any]:
    if role not in ROLE_PROMPT_FILES:
        raise IssueError(f"unsupported worker role: {role}")
    if route_file:
        route = load_json(Path(route_file))
        if not isinstance(route, dict):
            raise IssueError(f"route file must contain a JSON object: {route_file}")
        agent = agent or str(route.get("agent") or "")
        model = model or str(route.get("model") or "")
        effort = effort if effort != "" else str(route.get("effort") or "")
    if not agent or not model:
        raise IssueError("agent and model are required (pass --route-file or --agent and --model)")

    issue_path = Path(issue_dir).absolute()
    role_dir = issue_path / "workers" / role
    role_dir.mkdir(parents=True, exist_ok=True)
    attempt_num = attempt if attempt is not None else _next_attempt_number(role_dir, str(cycle))
    if attempt_num < 1:
        raise IssueError("attempt must be a positive integer")
    stem = f"cycle-{cycle}-attempt-{attempt_num}"

    if prompt_file:
        resolved_prompt = Path(prompt_file).absolute()
    elif asset_root:
        resolved_prompt = (Path(asset_root).absolute() / ROLE_PROMPT_FILES[role])
    else:
        resolved_prompt = Path(str(resources.resource_path(ROLE_PROMPT_FILES[role]))).absolute()

    log_file = role_dir / f"{stem}.log"
    done_file = role_dir / f"{stem}.done"
    state_file = role_dir / f"{stem}.state.json"
    if role == "review":
        result_file = role_dir / f"{stem}-status.json"
        instructions_file = role_dir / f"{stem}-instructions.json"
    else:
        result_file = role_dir / f"{stem}-result.json"
        instructions_file = None

    run_dir = issue_path.parent.parent if issue_path.parent.name == "issues" else issue_path
    resolved_status = Path(status_file).absolute() if status_file else run_dir / "status" / "current.txt"
    resolved_events = Path(events_log).absolute() if events_log else run_dir / "status" / "events.log"

    config_record: dict[str, Any] = {
        "agent": agent,
        "model": model,
        "effort": effort,
        "role": role,
        "issue": str(issue),
        "cycle": str(cycle),
        "context_file": str(Path(context_file).absolute()),
        "prompt_file": str(resolved_prompt),
        "repo_root": str(Path(repo_root).absolute()),
        "issue_dir": str(issue_path),
        "log_file": str(log_file),
        "done_file": str(done_file),
        "result_file": str(result_file),
        "state_file": str(state_file),
        "status_file": str(resolved_status),
        "events_log": str(resolved_events),
        "gui_mode": "0",
        "unattended": True,
        "poll_seconds": 20,
        "quiet_seconds": dispatch.DEFAULT_QUIET_SECONDS,
        "stall_seconds": dispatch.DEFAULT_STALL_SECONDS,
        "hard_limit_seconds": 7200,
        "detach": detach,
        "execution_mode": execution_mode,
    }
    if issue_base_sha:
        config_record["issue_base_sha"] = issue_base_sha
    if pre_spawn_head:
        config_record["pre_spawn_head"] = pre_spawn_head
    if retry_contract_file:
        config_record["retry_contract"] = load_json(Path(retry_contract_file))
    if result_capture_file:
        config_record["result_capture"] = load_json(Path(result_capture_file))

    # Validate through WorkerConfig so invalid contracts fail immediately.
    dispatch.WorkerConfig.from_sources(config_record, {})

    config_path = Path(output_file).absolute() if output_file else role_dir / f"{stem}.worker.json"
    write_json_atomic(config_path, config_record)
    paths: dict[str, str] = {
        "log_file": str(log_file),
        "done_file": str(done_file),
        "result_file": str(result_file),
        "state_file": str(state_file),
    }
    if instructions_file is not None:
        paths["review_instructions_file"] = str(instructions_file)
    if role == "plan":
        paths["plan_file"] = str(issue_path / "plan.md")
    return {
        "schema_version": SCHEMA_VERSION,
        "config_file": str(config_path),
        "attempt": attempt_num,
        "prompt_file": str(resolved_prompt),
        "paths": paths,
    }


# --------------------------------------------------------------------------- failure-action


def decide_failure_action(
    *,
    issue_dir: Path | str,
    role: str,
    cycle: str,
    max_fallbacks: int = DEFAULT_MAX_FALLBACKS,
    max_capability_retries: int = DEFAULT_MAX_CAPABILITY_RETRIES,
    max_review_retries: int = DEFAULT_MAX_REVIEW_RETRIES,
    state_files: list[str] | None = None,
) -> dict[str, Any]:
    issue_path = Path(issue_dir).absolute()
    role_dir = issue_path / "workers" / role
    if state_files:
        candidates = [Path(p).absolute() for p in state_files]
    elif role_dir.is_dir():
        prefix = f"cycle-{cycle}-attempt-"
        candidates = sorted(
            p for p in role_dir.glob(f"{prefix}*.state.json")
            if p.is_file()
        )
    else:
        candidates = []

    availability_states: list[str] = []
    capability_states: list[str] = []
    exclude_models: list[str] = []
    last_stall = ""
    last_class = "capability"
    last_state_file = str(candidates[-1]) if candidates else ""

    for sf in candidates:
        record = load_json(sf, missing={})
        if not isinstance(record, dict):
            continue
        stall = str(record.get("stall_reason") or record.get("reason") or "")
        if stall:
            last_stall = stall
        cfg = load_json(Path(str(sf) + ".config.json"), missing={})
        agent_run = cfg.get("agent_run") if isinstance(cfg, dict) and isinstance(cfg.get("agent_run"), dict) else {}
        log_file = str(agent_run.get("log_file") or record.get("log_file") or "")
        is_infra = (
            record.get("failure_class") == "infrastructure"
            or bool(record.get("unavailable_reason"))
            or bool(routing.failed_attempt_routes([str(sf)]))
            or artifacts.log_signals_unavailable_route(log_file)
        )
        fclass = "infrastructure" if is_infra else "capability"
        last_class = fclass
        model = str(agent_run.get("model") or record.get("model") or "")
        if fclass == "infrastructure":
            availability_states.append(str(sf))
        else:
            capability_states.append(str(sf))
            if model and model not in exclude_models:
                exclude_models.append(model)

    avail_count = len(availability_states)
    cap_count = len(capability_states)
    route_args: list[str] = []
    for sf in availability_states:
        route_args.extend(["--failed-attempt-state", sf])
    if availability_states:
        route_args.extend(["--max-availability-retries", str(max_fallbacks)])
    for model in exclude_models:
        route_args.extend(["--exclude-model", model])

    if last_class == "infrastructure":
        if avail_count <= max_fallbacks:
            action = "reroute-availability"
            reason = last_stall or "provider-unavailable"
        else:
            action = "plan-skipped" if role == "plan" else "blocked"
            reason = "all-routes-unavailable"
    elif role == "plan":
        action = "plan-skipped"
        reason = last_stall or "plan-attempt-failed"
    elif role == "review":
        if cap_count <= max_review_retries:
            action = "retry-same-role"
            reason = last_stall or "review-artifact-retry"
        else:
            action = "blocked"
            reason = f"review-artifact-retries-exhausted ({last_stall or 'invalid-review'})"
    elif role in ("impl", "modify"):
        if cap_count <= max_capability_retries:
            action = "retry-same-role"
            reason = last_stall or "capability-retry"
        else:
            action = "artifact-recovery"
            reason = last_stall or "capability-retries-exhausted"
    else:
        action = "blocked"
        reason = last_stall or "worker-failed"

    return {
        "schema_version": SCHEMA_VERSION,
        "role": role,
        "cycle": str(cycle),
        "failure_class": last_class,
        "stall_reason": last_stall,
        "availability_attempts": avail_count,
        "capability_attempts": cap_count,
        "max_availability_retries": max_fallbacks,
        "failed_attempt_states": availability_states,
        "capability_attempt_states": capability_states,
        "exclude_models": exclude_models,
        "last_state_file": last_state_file,
        "action": action,
        "reason": reason,
        "route_args": route_args,
        "recovery_dir": str(issue_path / "recovery" / role / f"cycle-{cycle}") if action == "artifact-recovery" else None,
    }


# --------------------------------------------------------------------------- review-gate


def _numstat(repo_root: Path | str, base_sha: str, head_sha: str) -> dict[str, Any]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--numstat", f"{base_sha}..{head_sha}"],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode:
        raise IssueError(proc.stderr.strip() or "git diff --numstat failed")
    files = []
    total_lines = 0
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added = int(parts[0]) if parts[0].isdigit() else 0
        deleted = int(parts[1]) if parts[1].isdigit() else 0
        total_lines += added + deleted
        files.append({"path": parts[2], "added": added, "deleted": deleted})
    return {"files_changed": len(files), "total_lines_changed": total_lines, "files": files}


def evaluate_review_gate(
    *,
    repo_root: Path | str,
    base_sha: str,
    head_sha: str = "HEAD",
    tests_all_passed: bool = False,
    delegated_review: bool = True,
) -> dict[str, Any]:
    stats = _numstat(repo_root, base_sha, head_sha)
    if not delegated_review:
        decision, reason = "skip", "delegated-review-disabled"
    elif stats["files_changed"] > 3 or stats["total_lines_changed"] > 55:
        decision, reason = "mandatory", "diff-exceeds-skip-threshold"
    elif tests_all_passed:
        decision, reason = "skip", "small-diff-with-passing-tests"
    else:
        decision, reason = "mandatory", "tests-not-explicitly-all-passed"
    return {
        "schema_version": SCHEMA_VERSION,
        **stats,
        "tests_all_passed": tests_all_passed,
        "delegated_review": delegated_review,
        "decision": decision,
        "reason": reason,
    }


# --------------------------------------------------------------------------- next-step


def _bump_band(band: str) -> str:
    band = canonical_band(band)
    if band not in BANDS:
        return "extreme"
    idx = BANDS.index(band)
    return BANDS[min(idx + 1, len(BANDS) - 1)]


def compute_next_step(
    *,
    issue_dir: Path | str,
    verdict_file: Path | str,
    reviewer_model: str = "",
    review_head_sha: str = "",
    effective_complexity: str = "",
) -> dict[str, Any]:
    status = load_json(Path(verdict_file))
    if not isinstance(status, dict) or not artifacts.valid_review_status(status):
        raise IssueError(f"invalid review status file: {verdict_file}")
    verdict = str(status["verdict"])
    nitpick_only = bool(status.get("nitpick_only", False))
    comment_count = int(status.get("comment_count", 0))

    result_box: dict[str, Any] = {}

    def _update(state: dict[str, Any]) -> None:
        cycle = int(state.get("cycle") or 1)
        cycles_used = int(state.get("cycles_used") or 0) + 1
        non_approval = int(state.get("non_approval_count") or 0)
        base_band = canonical_band(effective_complexity or state.get("effective_complexity") or "medium")
        issue_reviewer = state.get("issue_reviewer") or (reviewer_model or None)
        if issue_reviewer:
            state["issue_reviewer"] = issue_reviewer
        if effective_complexity:
            state["effective_complexity"] = base_band
        if review_head_sha:
            state["review_head_sha"] = review_head_sha

        history = list(state.get("review_history", []))
        history.append({
            "cycle": cycle,
            "verdict": verdict,
            "comment_count": comment_count,
            "nitpick_only": nitpick_only,
            "reviewer_model": reviewer_model or issue_reviewer,
            "review_head_sha": review_head_sha or state.get("review_head_sha"),
        })
        state["review_history"] = history
        state["cycles_used"] = cycles_used

        if verdict == "approve" or (verdict == "revise" and nitpick_only):
            action = "merge"
            state["phase"] = "merge"
            modify_band = state.get("modify_band") or base_band
            escalated = bool(state.get("escalated", False))
            redesign_required = bool(state.get("redesign_required", False))
        elif verdict == "reject":
            action = "failed-review"
            state["phase"] = "failed-review"
            non_approval += 1
            modify_band = state.get("modify_band") or base_band
            escalated = bool(state.get("escalated", False))
            redesign_required = bool(state.get("redesign_required", False))
        else:
            non_approval += 1
            escalated = bool(state.get("escalated", False)) or non_approval >= 2
            modify_band = _bump_band(base_band) if escalated else base_band
            redesign_required = non_approval >= 3
            if cycles_used >= MAX_REVIEW_CYCLES:
                action = "failed-review"
                state["phase"] = "failed-review"
            else:
                action = "spawn-modify"
                state["phase"] = "modify"
                state["cycle"] = cycle + 1

        state["non_approval_count"] = non_approval
        state["escalated"] = escalated
        state["modify_band"] = modify_band
        state["redesign_required"] = redesign_required

        result_box.update({
            "schema_version": SCHEMA_VERSION,
            "action": action,
            "verdict": verdict,
            "nitpick_only": nitpick_only,
            "cycle": state["cycle"],
            "cycles_used": cycles_used,
            "max_cycles": MAX_REVIEW_CYCLES,
            "non_approval_count": non_approval,
            "effective_complexity": base_band,
            "modify_band": modify_band,
            "escalated": escalated,
            "redesign_required": redesign_required,
            "issue_reviewer": issue_reviewer,
        })

    _mutate_sub_state(issue_dir, _update)
    return result_box


# --------------------------------------------------------------------------- resume-plan


def build_resume_plan(issue_dir: Path | str) -> dict[str, Any]:
    issue_path = Path(issue_dir).absolute()
    state = load_sub_state(issue_path, missing_ok=True)
    base_meta = load_json(issue_path / "base.json", missing={})
    if not isinstance(base_meta, dict):
        base_meta = {}

    inspected = []
    any_wait = False
    any_process = False
    any_recover = False

    for entry in state.get("in_flight_agents", []):
        if not isinstance(entry, dict):
            continue
        sf = entry.get("state_file")
        role = str(entry.get("role") or "")
        cycle = str(entry.get("cycle") or "1")
        if not sf or not Path(sf).is_file():
            inspected.append({
                "role": role,
                "cycle": cycle,
                "state_file": sf,
                "liveness": "missing-state",
                "artifact_valid": False,
                "action": "recover",
            })
            any_recover = True
            continue
        snap = dispatch.inspect_worker(Path(sf))
        if snap.liveness in ("alive", "unknown") or snap.state in ("running", "starting", "quiet", "stalled"):
            action = "wait"
            any_wait = True
        elif snap.state == "completed" and snap.artifact_valid:
            action = "process"
            any_process = True
        else:
            action = "recover"
            any_recover = True
        inspected.append({
            "role": role,
            "cycle": cycle,
            "state_file": str(sf),
            "liveness": snap.liveness,
            "worker_state": snap.state,
            "artifact_valid": snap.artifact_valid,
            "artifact_reason": snap.artifact_reason,
            "action": action,
        })

    if any_wait:
        next_action = "wait-in-flight"
    elif any_process:
        next_action = "process-completed-attempt"
    elif any_recover:
        next_action = "run-failure-action"
    else:
        next_action = f"continue-phase:{state.get('phase') or 'bootstrap'}"

    return {
        "schema_version": SCHEMA_VERSION,
        "issue_number": state.get("issue_number") or base_meta.get("issue") or "",
        "phase": state.get("phase") or "bootstrap",
        "cycle": state.get("cycle", 1),
        "cycles_used": state.get("cycles_used", 0),
        "non_approval_count": state.get("non_approval_count", 0),
        "effective_complexity": state.get("effective_complexity"),
        "modify_band": state.get("modify_band"),
        "escalated": bool(state.get("escalated", False)),
        "redesign_required": bool(state.get("redesign_required", False)),
        "worktree": base_meta or None,
        "in_flight": inspected,
        "next_action": next_action,
    }


# --------------------------------------------------------------------------- report


def write_issue_report(
    *,
    issue_dir: Path | str,
    outcome: str,
    summary: str,
    blocking_reasons: list[str] | None = None,
    merge_result_file: Path | str | None = None,
    repo_root: Path | str | None = None,
    head_sha: str = "HEAD",
    report_file: Path | str | None = None,
    review_skipped: bool | None = None,
) -> dict[str, Any]:
    issue_path = Path(issue_dir).absolute()
    state = load_sub_state(issue_path, missing_ok=True)
    base_meta = load_json(issue_path / "base.json", missing={})
    if not isinstance(base_meta, dict):
        base_meta = {}

    issue_number = str(state.get("issue_number") or base_meta.get("issue") or issue_path.name)
    merge_info: dict[str, Any] = {}
    if merge_result_file:
        raw_merge = load_json(Path(merge_result_file))
        if isinstance(raw_merge, dict):
            merge_info = {
                "outcome": raw_merge.get("outcome", outcome),
                "merge_sha": raw_merge.get("merge_sha") or raw_merge.get("commit_sha") or "",
                "merge_worktree": raw_merge.get("merge_worktree") or "",
                "failure_reason": raw_merge.get("failure_reason") or raw_merge.get("reason") or "",
                "evidence": raw_merge.get("evidence") or "",
                "conflict_files": raw_merge.get("conflict_files") or [],
            }

    files_modified: list[dict[str, Any]] = []
    base_sha = state.get("issue_base_sha") or base_meta.get("issue_base_sha") or ""
    worktree = str(repo_root or state.get("worktree") or base_meta.get("worktree") or "")
    if worktree and base_sha and Path(worktree).is_dir():
        try:
            files_modified = _numstat(worktree, base_sha, head_sha)["files"]
        except IssueError:
            files_modified = []

    skipped = bool(state.get("review_skipped", False)) if review_skipped is None else review_skipped
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "issue": issue_number,
        "outcome": outcome,
        "summary": summary,
        "blocking_reasons": list(blocking_reasons or []),
        "review_skipped": skipped,
        "branches": {
            "issue_branch": state.get("issue_branch") or base_meta.get("issue_branch") or "",
            "issue_base_sha": base_sha,
        },
        "merge": merge_info,
        "files_modified": files_modified,
        "review_summary": {
            "cycles_used": int(state.get("cycles_used") or 0),
            "non_approval_count": int(state.get("non_approval_count") or 0),
            "history": list(state.get("review_history") or []),
        },
        "model_usage": list(state.get("routes") or []),
    }

    reason = artifacts.terminal_report_reason("sub-coord", report)
    if reason:
        raise IssueError(f"invalid sub-coord report ({reason})")

    target = Path(report_file).absolute() if report_file else issue_path / "report.json"
    write_json_atomic(target, report)
    return {"schema_version": SCHEMA_VERSION, "report_file": str(target), "report": report}


# --------------------------------------------------------------------------- CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-skills issue", description="Sub-Coordinator issue state and helpers.")
    sub = parser.add_subparsers(dest="command", required=True)

    st = sub.add_parser("state", help="Manage .run-with-it/issues/<n>/sub-state.json.")
    st_sub = st.add_subparsers(dest="state_action", required=True)

    st_init = st_sub.add_parser("init")
    st_init.add_argument("--issue-dir", required=True)
    st_init.add_argument("--issue", required=True)
    st_init.add_argument("--worktree", default="")
    st_init.add_argument("--issue-branch", default="")
    st_init.add_argument("--issue-base-sha", default="")
    st_init.add_argument("--issue-base-source", default="")
    st_init.add_argument("--phase", default="bootstrap")

    st_rec = st_sub.add_parser("record-attempt")
    st_rec.add_argument("--issue-dir", required=True)
    st_rec.add_argument("--state-file", required=True)
    st_rec.add_argument("--route-file", default="")
    st_rec.add_argument("--phase", default=None)

    st_clr = st_sub.add_parser("clear-attempt")
    st_clr.add_argument("--issue-dir", required=True)
    st_clr.add_argument("--state-file", required=True)
    st_clr.add_argument("--phase", default=None)

    st_set = st_sub.add_parser("set")
    st_set.add_argument("--issue-dir", required=True)
    st_set.add_argument("--phase", default=None)
    st_set.add_argument("--compaction-requested", choices=["true", "false"], default=None)
    st_set.add_argument("--field", action="append", default=[])
    st_set.add_argument("--json-file", default="")

    st_show = st_sub.add_parser("show")
    st_show.add_argument("--issue-dir", required=True)

    wc = sub.add_parser("worker-config", help="Generate and validate an attempt-numbered worker.json.")
    wc.add_argument("--issue-dir", required=True)
    wc.add_argument("--issue", required=True)
    wc.add_argument("--role", required=True, choices=sorted(ROLE_PROMPT_FILES))
    wc.add_argument("--cycle", required=True)
    wc.add_argument("--context-file", required=True)
    wc.add_argument("--repo-root", required=True)
    wc.add_argument("--route-file", default="")
    wc.add_argument("--agent", default="")
    wc.add_argument("--model", default="")
    wc.add_argument("--effort", default="")
    wc.add_argument("--attempt", type=int, default=None)
    wc.add_argument("--asset-root", default="")
    wc.add_argument("--prompt-file", default="")
    wc.add_argument("--status-file", default="")
    wc.add_argument("--events-log", default="")
    wc.add_argument("--issue-base-sha", default="")
    wc.add_argument("--pre-spawn-head", default="")
    wc.add_argument("--execution-mode", choices=["implement", "verify_existing"], default="implement")
    wc.add_argument("--retry-contract-file", default="")
    wc.add_argument("--result-capture-file", default="")
    wc.add_argument("--output", default="")
    wc.add_argument("--foreground", action="store_true")

    fa = sub.add_parser("failure-action", help="Classify failed worker attempts and return the next action.")
    fa.add_argument("--issue-dir", required=True)
    fa.add_argument("--role", required=True)
    fa.add_argument("--cycle", required=True)
    fa.add_argument("--max-fallbacks", type=int, default=DEFAULT_MAX_FALLBACKS)
    fa.add_argument("--max-capability-retries", type=int, default=DEFAULT_MAX_CAPABILITY_RETRIES)
    fa.add_argument("--max-review-retries", type=int, default=DEFAULT_MAX_REVIEW_RETRIES)
    fa.add_argument("--state-file", action="append", default=[])

    rg = sub.add_parser("review-gate", help="Decide whether delegated review is mandatory or skippable.")
    rg.add_argument("--repo-root", required=True)
    rg.add_argument("--base-sha", required=True)
    rg.add_argument("--head-sha", default="HEAD")
    rg.add_argument("--tests-passed", action="store_true")
    rg.add_argument("--no-delegated-review", action="store_true")

    ns = sub.add_parser("next-step", help="Advance the review/modify loop from a validated review status file.")
    ns.add_argument("--issue-dir", required=True)
    ns.add_argument("--verdict-file", required=True)
    ns.add_argument("--reviewer-model", default="")
    ns.add_argument("--review-head-sha", default="")
    ns.add_argument("--effective-complexity", default="")

    rp = sub.add_parser("resume-plan", help="Inspect sub-state and in-flight workers to produce a resume plan.")
    rp.add_argument("--issue-dir", required=True)

    rep = sub.add_parser("report", help="Assemble, validate, and write the Sub-Coordinator compact report JSON.")
    rep.add_argument("--issue-dir", required=True)
    rep.add_argument("--outcome", required=True, choices=["completed", "failed-review", "merge_failed", "blocked"])
    rep.add_argument("--summary", default="")
    rep.add_argument("--summary-file", default="")
    rep.add_argument("--blocking-reason", action="append", default=[])
    rep.add_argument("--merge-result-file", default="")
    rep.add_argument("--repo-root", default="")
    rep.add_argument("--head-sha", default="HEAD")
    rep.add_argument("--report-file", default="")
    rep.add_argument("--review-skipped", choices=["true", "false"], default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "state":
            if args.state_action == "init":
                out = init_sub_state(
                    args.issue_dir, issue=args.issue, worktree=args.worktree,
                    issue_branch=args.issue_branch, issue_base_sha=args.issue_base_sha,
                    issue_base_source=args.issue_base_source, phase=args.phase,
                )
            elif args.state_action == "record-attempt":
                out = record_attempt(
                    args.issue_dir, state_file=args.state_file,
                    route_file=args.route_file or None, phase=args.phase,
                )
            elif args.state_action == "clear-attempt":
                out = clear_attempt(args.issue_dir, state_file=args.state_file, phase=args.phase)
            elif args.state_action == "set":
                extra: dict[str, Any] = {}
                if args.json_file:
                    loaded = load_json(Path(args.json_file))
                    if not isinstance(loaded, dict):
                        raise IssueError("--json-file must contain a JSON object")
                    extra.update(loaded)
                for pair in args.field:
                    if "=" not in pair:
                        raise IssueError(f"--field must be key=value: {pair!r}")
                    key, val = pair.split("=", 1)
                    extra[key.strip()] = _coerce_value(val.strip())
                comp = None if args.compaction_requested is None else (args.compaction_requested == "true")
                out = set_sub_state_fields(args.issue_dir, phase=args.phase, compaction_requested=comp, fields=extra)
            else:
                out = load_sub_state(args.issue_dir)
        elif args.command == "worker-config":
            out = build_worker_config(
                issue_dir=args.issue_dir, issue=args.issue, role=args.role, cycle=args.cycle,
                context_file=args.context_file, repo_root=args.repo_root,
                agent=args.agent, model=args.model, effort=args.effort,
                route_file=args.route_file or None, attempt=args.attempt,
                asset_root=args.asset_root or None, prompt_file=args.prompt_file or None,
                status_file=args.status_file or None, events_log=args.events_log or None,
                issue_base_sha=args.issue_base_sha, pre_spawn_head=args.pre_spawn_head,
                execution_mode=args.execution_mode,
                retry_contract_file=args.retry_contract_file or None,
                result_capture_file=args.result_capture_file or None,
                output_file=args.output or None, detach=not args.foreground,
            )
        elif args.command == "failure-action":
            out = decide_failure_action(
                issue_dir=args.issue_dir, role=args.role, cycle=args.cycle,
                max_fallbacks=args.max_fallbacks,
                max_capability_retries=args.max_capability_retries,
                max_review_retries=args.max_review_retries,
                state_files=args.state_file or None,
            )
        elif args.command == "review-gate":
            out = evaluate_review_gate(
                repo_root=args.repo_root, base_sha=args.base_sha, head_sha=args.head_sha,
                tests_all_passed=args.tests_passed, delegated_review=not args.no_delegated_review,
            )
        elif args.command == "next-step":
            out = compute_next_step(
                issue_dir=args.issue_dir, verdict_file=args.verdict_file,
                reviewer_model=args.reviewer_model, review_head_sha=args.review_head_sha,
                effective_complexity=args.effective_complexity,
            )
        elif args.command == "resume-plan":
            out = build_resume_plan(args.issue_dir)
        else:
            summary_text = args.summary
            if args.summary_file:
                summary_text = Path(args.summary_file).read_text(encoding="utf-8").strip()
            rev_skip = None if args.review_skipped is None else (args.review_skipped == "true")
            out = write_issue_report(
                issue_dir=args.issue_dir, outcome=args.outcome, summary=summary_text,
                blocking_reasons=args.blocking_reason,
                merge_result_file=args.merge_result_file or None,
                repo_root=args.repo_root or None, head_sha=args.head_sha,
                report_file=args.report_file or None, review_skipped=rev_skip,
            )
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except (IssueError, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
