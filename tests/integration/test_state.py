import json
from ai_skills import state

def test_invalid_authoritative_state_fails_closed(tmp_path):
    path=tmp_path / "state.json"
    path.write_text("{")
    try:
        state.main(["status-counts", "--state-file", str(path)])
    except (ValueError, SystemExit):
        pass
    else:
        raise AssertionError("invalid state accepted")

from pathlib import Path
import argparse
from concurrent.futures import ThreadPoolExecutor
import pytest
from ai_skills import github

def state_file(tmp_path,registry,**extra):
    path=tmp_path/"main-state.json"
    path.write_text(json.dumps({"schema_version":4,"issue_registry":registry,**extra}))
    return path

def command(path,name,*args):
    return state.main([name,"--state-file",str(path),*map(str,args)])

def test_requeue_quarantines_fresh_retry_artifacts(tmp_path,capsys):
    issue=tmp_path/"7";issue.mkdir();(issue/"workers").mkdir()
    terminal=["report.json","sub-coordinator.done","sub-state.json"]
    for name in terminal:(issue/name).write_text("stale")
    (issue/"sub-state.json").write_text('{"phase":"review"}')
    (issue/"workers"/"review.json").write_text("resume")
    path=state_file(tmp_path,{"7":{"status":"blocked","issue_dir":str(issue),"report_file":str(issue/"report.json"),"sub_coord_recovery_attempts":2,"blocking_reasons":["failure"]}},active_pool_issues=[7])
    command(path,"requeue","--issue",7,"--reason","fresh retry")
    entry=json.loads(path.read_text())["issue_registry"]["7"]
    assert entry["status"] == "pending"
    assert entry["sub_coord_recovery_attempts"] == entry["sub_coord_compaction_handoffs"] == 0
    assert entry["blocking_reasons"] == []
    assert not any((issue/name).exists() for name in terminal)
    assert not (issue/"sub-state.json").exists()
    assert (issue/"workers"/"review.json").read_text()=="resume"
    archive=Path(entry["requeue_archive_dir"])
    assert all((archive/name).exists() for name in terminal)

def test_predispatch_marker_quarantine_is_idempotent(tmp_path,capsys):
    (tmp_path/"report.json").write_text("stale")
    (tmp_path/"sub-state.json").write_text("resume")
    state.main(["quarantine-sub-coord-markers","--issue-dir",str(tmp_path),"--label","custom"])
    archive=capsys.readouterr().out.strip();assert Path(archive,"report.json").read_text()=="stale"
    state.main(["quarantine-sub-coord-markers","--issue-dir",str(tmp_path)])
    assert capsys.readouterr().out.strip()==""
    assert (tmp_path/"sub-state.json").read_text()=="resume"

def test_finalize_dependency_unblocks_its_dependents_and_preserves_metrics(tmp_path):
    path=state_file(tmp_path,{
        "7":{"status":"in_progress","deps":[]},
        "8":{"status":"blocked","deps":[7],"blocking_reasons":["blocked-by-issue-7"]},
        "9":{"status":"blocked","deps":[7],"blocking_reasons":["actual failure"]}},active_pool_issues=["7"])
    report=tmp_path/"report.json";report.write_text(json.dumps({"outcome":"completed","summary":"done","files_modified":[{"path":"x","lines_added":3,"lines_deleted":1}],"model_usage":[{"role":"impl","model":"fixture","agent":"codex","reason":"forced-model"}]}))
    command(path,"finalize-issue","--issue",7,"--report-file",report)
    result=json.loads(path.read_text())
    assert result["issue_registry"]["8"]["status"]=="pending"
    assert result["issue_registry"]["9"]["status"]=="blocked"
    assert result["active_pool_issues"]==[]
    summary=result["completed_summaries"][0]
    assert summary["lines_added"]==3 and summary["lines_deleted"]==1
    assert summary["model_usage"][0]["selection_reason"]=="forced-model"

def test_stale_dependency_block_gets_only_one_automatic_retry(tmp_path,capsys):
    path=state_file(tmp_path,{"6":{"status":"completed"},"7":{"status":"in_progress","deps":[6]}})
    report=tmp_path/"report.json";report.write_text(json.dumps({"outcome":"blocked","blocking_reasons":["blocked-by-issue-6"]}))
    command(path,"finalize-issue","--issue",7,"--report-file",report)
    assert capsys.readouterr().out.strip()=="pending"
    command(path,"finalize-issue","--issue",7,"--report-file",report)
    assert capsys.readouterr().out.strip()=="blocked"

@pytest.mark.parametrize("strict,expected",[(False,"7 8"),(True,"7")])
def test_admission_scope_metadata_and_missing_context(tmp_path,capsys,strict,expected):
    context=tmp_path/"context.md";context.write_text("context")
    registry={"7":{"status":"pending","deps":[],"context_file":str(context)},"8":{"status":"pending","deps":[],"context_file":str(context)},"9":{"status":"pending","deps":[],"context_file":str(tmp_path/"missing")}}
    path=state_file(tmp_path,registry,execution_plan={"topo_order":[7,8,9],"concurrency_policy":"strict" if strict else "permissive"})
    command(path,"ready-issues","--limit",4)
    assert capsys.readouterr().out.strip()==expected
    command(path,"ready-missing-context-count")
    assert capsys.readouterr().out.strip()=="1"
    # Admission does not rewrite missing metadata into inferred grants.
    assert json.loads(path.read_text())["issue_registry"]==registry

def test_read_only_status_and_unreachable_cone(tmp_path,capsys):
    path=state_file(tmp_path,{"6":{"status":"failed-review","deps":[]},"7":{"status":"pending","deps":[6]},"8":{"status":"pending","deps":[7]},"9":{"status":"completed","deps":[]}})
    before=path.read_bytes()
    command(path,"unreachable-issues")
    assert capsys.readouterr().out.strip()=="7:6;8:6"
    command(path,"status-board","--oneline")
    board=capsys.readouterr().out
    assert "#9 done" in board and "#7 blocked:6" in board
    command(path,"status-counts")
    assert "total=4" in capsys.readouterr().out
    assert path.read_bytes()==before

@pytest.mark.parametrize("sub_state,expected",[
    ({"phase":"plan","in_flight_agents":[{"role":"plan","cycle":1}]}, "plan(cyc1)"),
    ({"phase":"impl","in_flight_agents":[{"role":"impl","cycle":2}]}, "impl(cyc2)"),
    ({"phase":"impl","in_flight_agents":[]}, "impl"),
])
def test_status_boards_read_coordinator_stage_with_pool_worker_metadata(tmp_path,monkeypatch,capsys,sub_state,expected):
    from ai_skills.pool import PoolSupervisor
    from pool_support import pool_fixture,FakePoolDriver,install_fake

    config=pool_fixture(tmp_path,{1:{}})
    install_fake(monkeypatch,FakePoolDriver())
    supervisor=PoolSupervisor(config)
    assert supervisor.initialize() is None
    supervisor.launch("1")
    entry=supervisor.read()["issue_registry"]["1"]
    # Keep the actual pool registration: this path is dispatcher liveness,
    # while the coordinator publishes its semantic stage under issue_dir.
    assert Path(entry["sub_coord_state_file"]).name=="worker.state.json"
    (Path(entry["issue_dir"])/"sub-state.json").write_text(json.dumps(sub_state))
    before=config.state_file.read_bytes()

    assert state.issue_stage(entry,set())==expected
    command(config.state_file,"status-board","--oneline")
    assert capsys.readouterr().out.strip()==f"#1 {expected}"
    supervisor.board()
    events=supervisor.config.events_log.read_text().splitlines()
    assert [line for line in events if "type=run-board|" in line]==[
        f"STATUS|type=run-board|board=#1 {expected}"]
    assert config.state_file.read_bytes()==before

@pytest.mark.parametrize("custom_state,expected",[
    ({"phase":"review","in_flight_agents":[]}, "review"),
    ({"in_flight_agents":[{"role":"modify","cycle":3}]}, "modify(cyc3)"),
])
def test_status_board_prefers_explicit_coordinator_stage(tmp_path,capsys,custom_state,expected):
    custom=tmp_path/"custom-coordinator.json"
    custom.write_text(json.dumps(custom_state))
    (tmp_path/"sub-state.json").write_text(json.dumps({"phase":"plan"}))
    path=state_file(tmp_path,{"7":{"status":"in_progress","issue_dir":str(tmp_path),
        "sub_coord_state_file":str(custom)}})

    command(path,"status-board","--oneline")
    assert capsys.readouterr().out.strip()==f"#7 {expected}"

@pytest.mark.parametrize("canonical_state",[None,"{","[]",'{"state":"running"}',
    '{"phase":42,"in_flight_agents":[null]}'])
def test_status_board_keeps_running_without_coordinator_stage(tmp_path,capsys,canonical_state):
    worker=tmp_path/"worker.state.json"
    worker.write_text(json.dumps({"state":"running","alive":True}))
    if canonical_state is not None:
        (tmp_path/"sub-state.json").write_text(canonical_state)
    path=state_file(tmp_path,{"7":{"status":"in_progress","issue_dir":str(tmp_path),
        "sub_coord_state_file":str(worker)}})
    before=path.read_bytes()

    command(path,"status-board","--oneline")
    assert capsys.readouterr().out.strip()=="#7 running"
    assert path.read_bytes()==before

def test_compaction_handoff_does_not_spend_failure_budget(tmp_path):
    path=state_file(tmp_path,{"7":{"status":"in_progress","context_file":"original.md","sub_coord_recovery_attempts":2}})
    command(path,"mark-sub-coord-recovery-started","--issue",7,"--attempt",3,"--reason","sub-coordinator-context-exhausted","--context-file","recovery.md")
    entry=json.loads(path.read_text())["issue_registry"]["7"]
    assert entry["sub_coord_compaction_handoffs"]==3
    assert entry["sub_coord_recovery_attempts"]==2
    assert entry["sub_coord_original_context_file"]=="original.md"

def test_recovery_analyzer_consumes_snapshots(tmp_path,capsys):
    issue=tmp_path/"7";issue.mkdir()
    path=state_file(tmp_path,{"7":{"status":"in_progress","issue_dir":str(issue)}})
    (issue/"sub-state.json").write_text(json.dumps({"phase":"modify","compaction_requested":True}))
    command(path,"analyze-sub-coord-failure","--issue",7,"--report-file",issue/"report.json")
    decision=json.loads(capsys.readouterr().out)
    assert decision["action"]=="spawn_recovery" and decision["reason"]=="sub-coordinator-context-exhausted"

def test_main_state_and_github_mutations_share_lock_without_lost_updates(tmp_path):
    path=state_file(tmp_path,{str(issue):{"status":"pending"} for issue in range(1,9)})
    def mark(issue):
        state.mark_in_progress(argparse.Namespace(state_file=str(path),issue=str(issue),pid="42",context_file="ctx",log_file="log",done_file="done",report_file="report",issue_dir=str(tmp_path/str(issue)),sub_coord_state_file=""))
        github.set_github_update_state(str(path),str(issue),"updated","fixture")
    with ThreadPoolExecutor(max_workers=8) as executor:list(executor.map(mark,range(1,9)))
    result=json.loads(path.read_text())
    assert len(result["active_pool_issues"])==8
    assert all(entry["status"]=="in_progress" and entry["github_update_status"]=="updated" for entry in result["issue_registry"].values())
    assert not Path(str(path)+".lock").exists()

@pytest.mark.parametrize("payload",["[]","null","42"])
def test_nonobject_authoritative_state_rejected(tmp_path,payload):
    path=tmp_path/"state.json";path.write_text(payload)
    with pytest.raises(ValueError):command(path,"requeue","--issue",7)
    assert path.read_text()==payload
