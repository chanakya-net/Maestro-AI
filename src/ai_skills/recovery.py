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
    max_wait_seconds: int = 3600

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


def decide_recovery(evidence: RecoveryEvidence, policy: RecoveryPolicy) -> RecoveryDecision:
    def decision(action, reason, *, phase=None, worker=None, worker_state=None, liveness=None, attempt=None, maximum=None):
        return RecoveryDecision(compact_worker_decision(action=action, reason=reason,
            issue=evidence.issue, issue_dir=evidence.issue_dir, sub_state_file=evidence.sub_state_file,
            phase=phase, worker=worker, worker_state=worker_state, liveness=liveness,
            attempt=attempt if attempt is not None else evidence.recovery_attempts+1,
            max_attempts=maximum if maximum is not None else max(policy.max_attempts, 0)))
    if isinstance(evidence.report.get("outcome"), str) and evidence.report["outcome"] in TERMINAL_OUTCOMES:
        return decision("finalize", "terminal-report-present")
    compaction=evidence.sub_state.get("compaction_requested") is True
    attempt=(evidence.compaction_handoffs if compaction else evidence.recovery_attempts)+1
    maximum=max(policy.max_compaction_handoffs if compaction else policy.max_attempts, 0)
    common={"attempt":attempt,"maximum":maximum}
    if attempt > maximum:
        return decision("block", "sub-coordinator-compaction-handoffs-exhausted" if compaction else "sub-coordinator-recovery-attempts-exhausted", **common)
    if not evidence.sub_state:
        return decision("block", "missing-sub-state", **common)
    phase=evidence.sub_state.get("phase") if isinstance(evidence.sub_state.get("phase"),str) else None
    common["phase"]=phase
    for worker, snapshot, done, result, liveness in evidence.workers:
        if snapshot.get("state") not in LIVE_WORKER_STATES or (done and result): continue
        if not liveness.get("alive", True):
            reason="in-flight-worker-orphaned"; action="spawn_recovery"
        elif policy.max_wait_seconds and evidence.wait_elapsed_seconds >= policy.max_wait_seconds:
            reason="in-flight-worker-wait-timeout"; action="spawn_recovery"
        else:
            reason="in-flight-worker-running"; action="wait_worker"
        return decision(action, reason, worker=worker,worker_state=snapshot,liveness=liveness,**common)
    for worker, snapshot, done, result, liveness in evidence.workers:
        state=snapshot.get("state")
        if state in FINISHED_WORKER_STATES or done or result:
            reason="artifact-recovery-required" if state=="artifact-recovery-required" else ("in-flight-worker-finished" if state=="completed" or result else "in-flight-worker-failed")
            return decision("spawn_recovery",reason,worker=worker,worker_state=snapshot,**common)
    return decision("spawn_recovery", "sub-coordinator-context-exhausted" if compaction else "sub-state-present-no-in-flight-worker", **common)
