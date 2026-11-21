"""Pure recovery decisions over snapshots collected by the state adapter."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

TERMINAL_OUTCOMES = {"completed", "failed-review", "blocked", "merge_failed", "failed-merge"}
LIVE_WORKER_STATES = {"ready", "starting", "running", "quiet", "stalled"}
FINISHED_WORKER_STATES = {"completed", "failed", "artifact-recovery-required"}

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
    liveness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    worker = worker if isinstance(worker, dict) else {}
    worker_state = worker_state if isinstance(worker_state, dict) else {}
    liveness = liveness if isinstance(liveness, dict) else {}
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
        "worker_age_seconds": liveness.get("age_seconds"),
        "worker_liveness": liveness.get("reason"),
        "recovery_attempt": attempt,
        "max_recovery_attempts": max_attempts,
    }

@dataclass(frozen=True)
class RecoveryPolicy:
    max_attempts: int = 2
    max_compaction_handoffs: int = 6
    max_wait_seconds: int = 0

@dataclass(frozen=True)
class RecoveryEvidence:
    issue: str
    issue_dir: str = ""
    sub_state_file: str = ""
    report: dict = field(default_factory=dict)
    sub_state: dict = field(default_factory=dict)
    recovery_attempts: int = 0
    compaction_handoffs: int = 0
    wait_elapsed_seconds: int = 0
    # Ordered (worker, worker state, done, result, liveness) snapshots.
    workers: tuple = ()

@dataclass(frozen=True)
class RecoveryDecision:
    details: dict

    @property
    def action(self): return self.details["action"]
    @property
    def reason(self): return self.details["reason"]


def _legacy_worker_action(workers):
    for evidence in workers:
        _, snapshot, done, result, liveness = evidence
        if snapshot.get("state") in LIVE_WORKER_STATES and not (done and result):
            if not liveness.get("alive", True):
                return "spawn_recovery", "in-flight-worker-orphaned", evidence
            return "wait_worker", "in-flight-worker-running", evidence
    for evidence in workers:
        _, snapshot, done, result, _ = evidence
        state = snapshot.get("state")
        if state == "artifact-recovery-required":
            return "spawn_recovery", state, evidence
        if state in FINISHED_WORKER_STATES or done or result:
            reason = "in-flight-worker-finished" if state == "completed" or result else "in-flight-worker-failed"
            return "spawn_recovery", reason, evidence
    return None


def _released_worker_action(workers):
    for state, reason in (
        ("completed", "in-flight-worker-finished"),
        ("artifact-recovery-required", "artifact-recovery-required"),
    ):
        for evidence in workers:
            if evidence[4].get("handoff_state") == state:
                return "spawn_recovery", reason, evidence
    selected = workers[0]
    reason = ("in-flight-worker-released" if selected[4].get("handoff_state") == "released"
              else "in-flight-worker-failed")
    return "spawn_recovery", reason, selected


def decide_recovery(evidence: RecoveryEvidence, policy: RecoveryPolicy) -> RecoveryDecision:
    compaction = evidence.sub_state.get("compaction_requested") is True
    attempt = (evidence.compaction_handoffs if compaction else evidence.recovery_attempts) + 1
    maximum = max(policy.max_compaction_handoffs if compaction else policy.max_attempts, 0)
    phase = evidence.sub_state.get("phase")
    check_ownership = policy.max_wait_seconds > 0 and bool(evidence.workers)
    expired = check_ownership and evidence.wait_elapsed_seconds >= policy.max_wait_seconds

    def decision(action, reason, selected=None):
        if selected is None and check_ownership:
            selected = evidence.workers[0]
        worker, snapshot, _, _, liveness = selected or ({}, {}, False, False, {})
        details = compact_worker_decision(action=action, reason=reason, issue=evidence.issue,
            issue_dir=evidence.issue_dir, sub_state_file=evidence.sub_state_file,
            phase=phase if isinstance(phase, str) else None, worker=worker,
            worker_state=snapshot, liveness=liveness, attempt=attempt, max_attempts=maximum)
        details.update(wait_expired=expired, wait_elapsed_seconds=evidence.wait_elapsed_seconds,
            max_wait_seconds=policy.max_wait_seconds, check_worker_ownership=check_ownership,
            worker_ownership=liveness.get("ownership"), worker_attempt_id=liveness.get("attempt_id"),
            recovery_budget=("sub_coord_compaction_handoffs" if compaction else "sub_coord_recovery_attempts"))
        return RecoveryDecision(details)

    if check_ownership:
        for selected in evidence.workers:
            if selected[4].get("ownership") != "released":
                reason = "in-flight-worker-wait-timeout" if expired else "in-flight-worker-running"
                return decision("wait_worker", reason, selected)
    outcome = evidence.report.get("outcome")
    if isinstance(outcome, str) and outcome in TERMINAL_OUTCOMES:
        return decision("finalize", "terminal-report-present")
    if attempt > maximum:
        reason = ("sub-coordinator-compaction-handoffs-exhausted" if compaction
                  else "sub-coordinator-recovery-attempts-exhausted")
        return decision("block", reason)
    if not evidence.sub_state:
        return decision("block", "missing-sub-state")
    if check_ownership:
        return decision(*_released_worker_action(evidence.workers))
    action = _legacy_worker_action(evidence.workers)
    if action:
        return decision(*action)
    reason = "sub-coordinator-context-exhausted" if compaction else "sub-state-present-no-in-flight-worker"
    return decision("spawn_recovery", reason)
