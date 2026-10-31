from ai_skills.recovery import RecoveryEvidence, RecoveryPolicy, decide_recovery

def test_missing_sub_state_blocks():
    decision = decide_recovery(RecoveryEvidence(issue="7"), RecoveryPolicy())
    assert decision.reason == "missing-sub-state"
    assert decision.action == "block"

from dataclasses import replace
import pytest
from ai_skills.state import worker_liveness, process_alive, issues_can_run_together

@pytest.mark.parametrize("report,sub,attempts,handoffs,policy,action,reason",[
    ({"outcome":"completed"},{},99,99,RecoveryPolicy(),"finalize","terminal-report-present"),
    ({"outcome":"blocked"},{},0,0,RecoveryPolicy(),"finalize","terminal-report-present"),
    ({},{},2,0,RecoveryPolicy(),"block","sub-coordinator-recovery-attempts-exhausted"),
    ({},{"compaction_requested":True},2,6,RecoveryPolicy(),"block","sub-coordinator-compaction-handoffs-exhausted"),
    ({},{"compaction_requested":True},2,1,RecoveryPolicy(),"spawn_recovery","sub-coordinator-context-exhausted"),
    ({},{"phase":"review"},0,0,RecoveryPolicy(),"spawn_recovery","sub-state-present-no-in-flight-worker"),
])
def test_recovery_budget_and_terminal_branches(report,sub,attempts,handoffs,policy,action,reason):
    evidence=RecoveryEvidence(issue="7",report=report,sub_state=sub,recovery_attempts=attempts,compaction_handoffs=handoffs)
    decision=decide_recovery(evidence,policy)
    assert (decision.action,decision.reason) == (action,reason)
    if sub.get("compaction_requested"):
        assert decision.details["recovery_attempt"] == handoffs+1
        assert decision.details["max_recovery_attempts"] == 6

@pytest.mark.parametrize("worker_state,done,result,alive,wait,action,reason",[
    ("running",False,False,True,0,"wait_worker","in-flight-worker-running"),
    ("quiet",False,False,False,0,"spawn_recovery","in-flight-worker-orphaned"),
    ("running",False,False,True,3600,"spawn_recovery","in-flight-worker-wait-timeout"),
    ("completed",False,False,True,0,"spawn_recovery","in-flight-worker-finished"),
    ("failed",False,False,False,0,"spawn_recovery","in-flight-worker-failed"),
    ("artifact-recovery-required",False,False,False,0,"spawn_recovery","artifact-recovery-required"),
    ("running",True,True,False,0,"spawn_recovery","in-flight-worker-finished"),
    ("unknown",True,False,False,0,"spawn_recovery","in-flight-worker-failed"),
])
def test_worker_recovery_branches(worker_state,done,result,alive,wait,action,reason):
    worker={"role":"modify","cycle":2,"state_file":"worker.json","result_file":"result.json"}
    evidence=RecoveryEvidence(issue="7",sub_state={"phase":"modify"},wait_elapsed_seconds=wait,
        workers=((worker,{"state":worker_state},done,result,{"alive":alive,"reason":"fresh" if alive else "state-file-stale","age_seconds":700}),))
    decision=decide_recovery(evidence,RecoveryPolicy())
    assert (decision.action,decision.reason)==(action,reason)
    assert decision.details["worker_role"] == "modify"
    assert decision.details["worker_cycle"] == 2

def test_wait_cap_zero_means_no_cap():
    evidence=RecoveryEvidence(issue="7",sub_state={"phase":"impl"},wait_elapsed_seconds=99999,
        workers=(({}, {"state":"running"},False,False,{"alive":True}),))
    assert decide_recovery(evidence,RecoveryPolicy(max_wait_seconds=0)).action == "wait_worker"

def test_worker_liveness_uses_freshest_artifact_and_unknown_is_conservative(tmp_path,monkeypatch):
    import os
    import ai_skills.state as state
    monkeypatch.setattr(state,"process_alive",lambda pid:None)
    path=tmp_path / "worker.json";path.write_text("{}")
    os.utime(path,(100,100))
    assert worker_liveness({"state_file":str(path)}, {},now=800,stale_seconds=600,dead_pid_grace_seconds=60)["reason"] == "state-file-stale"
    assert worker_liveness({"state_file":str(path)}, {"updated_at":"1970-01-01T00:12:30Z"},now=800,stale_seconds=600,dead_pid_grace_seconds=60)["alive"]
    assert worker_liveness({}, {},now=800,stale_seconds=600,dead_pid_grace_seconds=60)["alive"]
    monkeypatch.setattr(state,"process_alive",lambda pid:False if pid else None)
    assert worker_liveness({}, {"runner_pid":999999},now=800,stale_seconds=600,dead_pid_grace_seconds=60)["reason"] == "pids-dead"

@pytest.mark.parametrize("pid",[None,True,-1,0,1,"10"])
def test_invalid_pid_is_unknown(pid):
    assert process_alive(pid) is None

@pytest.mark.parametrize("left,right,policy,expected",[
    ({},{},"permissive",True), ({},{},"strict",False),
    ({"parallel_safe":False},{},"permissive",False),
    ({"parallel_safe":"garbage"},{},"permissive",False),
    ({"parallel_safe":True,"ownership_scope":["src/api"]},{"parallel_safe":"true","ownership_scope":["src/web"]},"strict",True),
    ({"parallel_safe":True,"ownership_scope":["SRC/API/*"]},{"parallel_safe":True,"ownership_scope":["src/api/routes"]},"strict",False),
    ({"parallel_safe":True,"ownership_scope":["."]},{"parallel_safe":True,"ownership_scope":["src/web"]},"permissive",False),
    ({"parallel_safe":True,"ownership_scope":"src/api"},{},"permissive",False),
    ({"parallel_safe":True,"ownership_scope":["../outside"]},{},"permissive",False),
])
def test_parallel_scope_policy(left,right,policy,expected):
    assert issues_can_run_together(left,right,policy) is expected

def test_malformed_optional_terminal_outcome_does_not_crash_analyzer():
    assert decide_recovery(RecoveryEvidence(issue='7',report={'outcome':[]}),RecoveryPolicy()).reason=='missing-sub-state'
