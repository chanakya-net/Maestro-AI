from ai_skills.artifacts import validate_artifact, GitEvidence

def test_noop_requires_verification():
    decision = validate_artifact("impl", {"issue":"7", "role":"impl", "status":"success", "no_op":True}, GitEvidence(issue="7"))
    assert decision.reason == "verified-no-op-requires-passing-verification"

from dataclasses import replace
from pathlib import Path
import argparse
import json
import subprocess
import pytest
from ai_skills import artifacts


def role_failure(**patch):
    return dict({'schema_version':1, 'artifact_kind':'role_failure', 'run_id':'run-7',
                 'attempt_id':'attempt-1', 'issue':'7', 'role':'impl', 'status':'failed',
                 'reason':'tests-failed', 'summary':'The tests did not pass.'}, **patch)


@pytest.mark.parametrize('status', ['failed','blocked','interrupted'])
def test_failure_writer_accepts_failure_without_commit(tmp_path, monkeypatch, status):
    monkeypatch.setenv('RUN_WITH_IT_DISPATCHER_MANAGED','1')
    monkeypatch.setenv('RUN_WITH_IT_RUN_ID','run-7')
    monkeypatch.setenv('RUN_WITH_IT_ATTEMPT_ID','attempt-1')
    monkeypatch.setenv('RUN_WITH_IT_ROLE','impl')
    monkeypatch.setenv('RUN_WITH_IT_ISSUE','7')
    payload=tmp_path/'payload.json'; payload.write_text(json.dumps(role_failure(status=status)))
    result=tmp_path/'result.json'
    assert artifacts.main(['write-json','--role','impl','--issue','7','--payload-file',str(payload),'--result-file',str(result)]) == 0
    assert json.loads(result.read_text()) == role_failure(status=status)
    # Failure publication is never accepted as successful substantive work.
    assert not validate_artifact('impl',json.loads(result.read_text()),GitEvidence(issue='7')).valid


@pytest.mark.parametrize('patch', [
    {'schema_version':2}, {'schema_version':True}, {'run_id':'other'}, {'attempt_id':'other'},
    {'issue':'8'}, {'role':'review'}, {'status':'success'}, {'reason':' '}, {'summary':''},
    {'verification':{'passed':True}}, {'verification':[]}, {'evidence':['ok',1]}, {'evidence':'log'},
])
def test_failure_validator_rejects_invalid_or_unbound_payload(patch):
    assert artifacts.role_failure_reason(role_failure(**patch), issue='7', role='impl',run_id='run-7',attempt_id='attempt-1')


def test_review_failure_needs_no_companion_and_writer_binds_managed_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('RUN_WITH_IT_DISPATCHER_MANAGED','1')
    monkeypatch.setenv('RUN_WITH_IT_RUN_ID','run-7')
    monkeypatch.setenv('RUN_WITH_IT_ATTEMPT_ID','attempt-1')
    monkeypatch.setenv('RUN_WITH_IT_ROLE','review')
    monkeypatch.setenv('RUN_WITH_IT_ISSUE','7')
    result=tmp_path/'review-status.json'; payload=tmp_path/'payload.json'
    failure=role_failure(role='review',verification={'passed':False},evidence=['test.log'])
    payload.write_text(json.dumps(failure))
    arguments=['write-json','--role','review','--issue','7','--payload-file',str(payload),'--result-file',str(result)]
    assert artifacts.main(arguments) == 0
    assert not (tmp_path/'review-instructions.json').exists()
    before=result.read_bytes()
    payload.write_text(json.dumps(dict(failure,attempt_id='stale')))
    assert artifacts.main(arguments) == 1
    assert result.read_bytes() == before


@pytest.mark.parametrize('key,value',[('RUN_WITH_IT_ROLE','review'),('RUN_WITH_IT_ISSUE','8')])
def test_failure_writer_rejects_managed_role_and_issue_mismatch(tmp_path,monkeypatch,key,value):
    for name, content in {'RUN_WITH_IT_DISPATCHER_MANAGED':'1','RUN_WITH_IT_RUN_ID':'run-7',
        'RUN_WITH_IT_ATTEMPT_ID':'attempt-1','RUN_WITH_IT_ROLE':'impl','RUN_WITH_IT_ISSUE':'7',key:value}.items():
        monkeypatch.setenv(name,content)
    payload=tmp_path/'payload.json';payload.write_text(json.dumps(role_failure()))
    result=tmp_path/'result.json'
    assert artifacts.main(['write-json','--role','impl','--issue','7','--payload-file',str(payload),'--result-file',str(result)]) == 1
    assert not result.exists()


@pytest.mark.parametrize('role',['sub-coord','artifact-recovery','merge-recovery'])
def test_failure_payload_is_never_substantive_success_for_generic_roles(role):
    assert not validate_artifact(role,role_failure(role=role),GitEvidence(issue='7')).valid

BASE={"issue":"7","role":"impl","status":"success","commit_sha":"a"*40,"files_committed":["feature.txt"],"verification":{"passed":True}}
EVIDENCE=GitEvidence(issue="7",repo_available=True,head="a"*40,pre_spawn_head="b"*40,canonical_commit="a"*40,canonical_pre_spawn_head="b"*40)

@pytest.mark.parametrize("patch,evidence,reason",[
    ({},EVIDENCE,""),
    ({"issue":"8"},EVIDENCE,"invalid-result-artifact"),
    ({"role":"review"},EVIDENCE,"invalid-result-artifact"),
    ({"status":"artifact_recovery_required"},EVIDENCE,"artifact-recovery-required"),
    ({"status":"failed"},EVIDENCE,"invalid-result-artifact"),
    ({"no_op":True},EVIDENCE,"verified-no-op-with-unexpected-commit"),
    ({"no_op":True},replace(EVIDENCE,head="b"*40),""),
    ({"no_op":True},replace(EVIDENCE,head=None),"implementation-repo-unavailable"),
    ({"commit_sha":"NONE"},EVIDENCE,"invalid-result-artifact"),
    ({"files_committed":[]},EVIDENCE,"invalid-result-artifact"),
    ({"verification":{"passed":False}},EVIDENCE,"implementation-verification-failed"),
    ({},replace(EVIDENCE,repo_available=False),"implementation-repo-unavailable"),
    ({},replace(EVIDENCE,canonical_commit=None),"invalid-implementation-commit"),
    ({},replace(EVIDENCE,head="c"*40),"commit-outside-issue-worktree"),
    ({},replace(EVIDENCE,canonical_pre_spawn_head="a"*40),"missing-implementation-commit"),
    ({},replace(EVIDENCE,result_is_report=True),"worker-result-path-is-sub-coordinator-report"),
])
def test_implementation_reason_table(patch,evidence,reason):
    decision=validate_artifact("impl",dict(BASE,**patch),evidence)
    assert decision.reason == reason
    assert decision.valid is (not reason)

@pytest.fixture
def repo(tmp_path):
    path=tmp_path / "repo";path.mkdir()
    def git(*args):
        return subprocess.check_output(["git","-C",str(path),*args],text=True,stderr=subprocess.DEVNULL).strip()
    git("init","-q");git("config","user.name","Fixture");git("config","user.email","fixture@example.invalid")
    (path/"base.txt").write_text("base\n");git("add","-A");git("commit","-qm","base")
    return path,git,git("rev-parse","HEAD")

def worker_args(tmp_path,repo,**kwargs):
    path,git,base=repo
    return argparse.Namespace(role="impl",issue="7",result_file=str(tmp_path/"result.json"),repo_root=str(path),pre_spawn_head=base,issue_dir="",done_file="",log_file="",from_stall=False,**kwargs)

def test_abbreviated_commit_is_canonicalized_and_foreign_commit_rejected(tmp_path,repo):
    path,git,base=repo
    (path/"feature.txt").write_text("feature\n");git("add","-A");git("commit","-qm","feature")
    head=git("rev-parse","HEAD");args=worker_args(tmp_path,repo)
    payload=dict(BASE,commit_sha=head[:12]);Path(args.result_file).write_text(json.dumps(payload))
    assert artifacts.implementation_result_reason(args,payload) == ""
    assert json.loads(Path(args.result_file).read_text())["commit_sha"] == head
    assert artifacts.implementation_result_reason(args,dict(BASE,commit_sha="deadbeef")) == "invalid-implementation-commit"
    # A known commit outside this worker's current history is rejected when HEAD
    # has advanced beyond the spawn base; recovery cannot replace existing work.
    git("checkout","-q","--orphan","foreign");git("rm","-rf",".")
    (path/"other.txt").write_text("other\n");git("add","-A");git("commit","-qm","foreign")
    assert artifacts.implementation_result_reason(args,dict(BASE,commit_sha=head)) == "commit-outside-issue-worktree"

def test_wrong_worktree_commit_cherry_pick_preserves_verified_work(tmp_path,repo):
    path,git,base=repo
    git("checkout","-qb","other")
    (path/"feature.txt").write_text("feature\n");git("add","-A");git("commit","-qm","feature")
    head=git("rev-parse","HEAD");git("checkout","-q","--detach",base)
    args=worker_args(tmp_path,repo);payload=dict(BASE,commit_sha=head)
    Path(args.result_file).write_text(json.dumps(payload))
    assert artifacts.implementation_result_reason(args,payload) == ""
    assert (path/"feature.txt").read_text() == "feature\n"
    recovered=json.loads(Path(args.result_file).read_text())
    assert recovered["recovered_from_commit"] == head

def test_stall_salvage_is_recovery_required_and_never_verified(tmp_path,repo):
    path,git,base=repo;args=worker_args(tmp_path,repo)
    (path/"feature.txt").write_text("unfinished\n")
    assert artifacts.synthesize_implementation(args) is False
    assert git("rev-parse","HEAD") == base
    args.from_stall=True
    assert artifacts.synthesize_implementation(args)
    payload=json.loads(Path(args.result_file).read_text())
    assert payload["status"] == "artifact_recovery_required"
    assert payload["verification"]["passed"] is False
    assert artifacts.result_failure_reason(args) == "artifact-recovery-required"

def test_protected_issue_report_never_overwritten(tmp_path,repo):
    args=worker_args(tmp_path,repo)
    args.issue_dir=str(tmp_path);args.result_file=str(tmp_path/"report.json")
    Path(args.result_file).write_text('{"outcome":"blocked"}')
    before=Path(args.result_file).read_bytes()
    assert artifacts.result_failure_reason(args) == "worker-result-path-is-sub-coordinator-report"
    args.from_stall=True
    assert artifacts.synthesize_implementation(args) is False
    assert Path(args.result_file).read_bytes() == before

COMMENT={"id":"R1","file":"feature.py","line":1,"severity":"warning","category":"security","blocking":True,"fix":"Validate input","evidence":"Unchecked input","expected_change":"Reject bad input","verification":"pytest"}

@pytest.mark.parametrize("status,instructions,reason",[
    ({"verdict":"revise","comment_count":1,"nitpick_only":False},{"verdict":"revise","summary":"Fix","comments":[COMMENT],"blocking_reasons":[]},""),
    ({"verdict":"revise","comment_count":2,"nitpick_only":False},{"verdict":"revise","summary":"Fix","comments":[COMMENT],"blocking_reasons":[]},"review-comment-count-mismatch"),
    ({"verdict":"approve","comment_count":0,"nitpick_only":False},None,"missing-review-instructions-artifact"),
    ({"verdict":"approve","comment_count":1,"nitpick_only":False},{"verdict":"approve","summary":"Fix","comments":[COMMENT],"blocking_reasons":[]},"invalid-review-instructions-artifact"),
])
def test_pure_review_policy(status,instructions,reason):
    assert validate_artifact("review",status,GitEvidence(review_instructions=instructions)).reason == reason

@pytest.mark.parametrize("count,expected",[(0,True),(2,False)])
def test_review_synthesis_never_drops_reported_comments(tmp_path,count,expected):
    result=tmp_path/"cycle-1-status.json";done=tmp_path/"done"
    result.write_text(json.dumps({"verdict":"approve","comment_count":count,"nitpick_only":bool(count)}));done.write_text("DONE")
    args=argparse.Namespace(role="review",issue="7",result_file=str(result),done_file=str(done),log_file="")
    assert artifacts.synthesize_review(args) is expected
    assert (tmp_path/"cycle-1-instructions.json").exists() is expected

def test_protected_nitpicks_repair_only_revise(tmp_path):
    comment=dict(COMMENT,severity="info",blocking=False,fix="[nitpick] Improve test",category="test")
    payload={"verdict":"revise","summary":"Fix","comments":[comment],"blocking_reasons":[]}
    repaired=artifacts.repair_protected_nitpicks(payload)
    assert repaired["comments"][0]["severity"] == "warning"
    assert not repaired["comments"][0]["fix"].startswith("[nitpick]")
    assert artifacts.repair_protected_nitpicks(dict(payload,verdict="approve")) is None

@pytest.mark.parametrize("log,expected",[("",False),("STATUS|type=dispatch-bootstrap-failed\nSTATUS|type=worker-done",False),("STATUS|type=agent-unavailable|reason=quota",True)])
def test_infrastructure_failure_only_uses_current_availability_marker(tmp_path,log,expected):
    path=tmp_path/"worker.log";path.write_text(log)
    assert artifacts.log_signals_unavailable_route(str(path)) is expected

def test_atomic_artifact_cli_preserves_destination_on_invalid_payload(tmp_path):
    target=tmp_path/"result.json";target.write_text('{"previous":true}')
    payload=tmp_path/"payload.json";payload.write_text("{")
    code=artifacts.main(["write-json","--role","impl","--issue","7","--result-file",str(target),"--payload-file",str(payload)])
    assert code != 0
    assert target.read_text() == '{"previous":true}'

@pytest.mark.parametrize('patch,present,reason',[
    ({},True,''),({},False,'missing-plan-file-artifact'),
    ({'status':'failed'},True,'invalid-plan-result-artifact'),
    ({'issue':'8'},True,'invalid-plan-result-artifact'),
    ({'complexity_level':'unknown'},True,'invalid-plan-result-artifact'),
])
def test_planning_stage_requires_successful_matching_plan_and_markdown(patch,present,reason):
    payload={'schema_version':1,'issue':'7','role':'plan','status':'success','approach':'Implement the slice','complexity_level':'easy','slices':[{'id':1}]}
    payload.update(patch)
    assert validate_artifact('plan',payload,GitEvidence(issue='7',plan_file_present=present)).reason==reason


@pytest.mark.parametrize('role', ['sub-coord', 'merge-recovery'])
@pytest.mark.parametrize('payload', [None, {}, {'outcome':'surprise','summary':'x'},
    {'outcome':'blocked','summary':'x'}, {'outcome':'blocked','summary':'x','blocking_reasons':[]},
    {'outcome':'blocked','summary':'x','blocking_reasons':['']},
    {'outcome':'blocked','summary':'x','blocking_reasons':[1]},
    {'outcome':'completed','summary':' '}, {'outcome':['completed'],'summary':'x'}])
def test_terminal_coordinator_reports_fail_closed(role, payload):
    assert not validate_artifact(role, payload, GitEvidence(issue='7')).valid


@pytest.mark.parametrize('outcome', ['completed','blocked','failed-review','merge_failed','failed-merge'])
def test_supported_subcoordinator_terminal_reports(outcome):
    payload={'outcome':outcome,'summary':'Concrete terminal explanation', 'blocking_reasons':['test failure']}
    assert validate_artifact('sub-coord', payload, GitEvidence(issue='7')).valid


@pytest.mark.parametrize('outcome', ['completed','blocked','failed-merge'])
def test_supported_merge_recovery_terminal_reports(outcome):
    assert validate_artifact('merge-recovery', {'outcome':outcome,'summary':'Recovery stopped',
        'blocking_reasons':['verification failed']}, GitEvidence(issue='7')).valid


def test_coordinator_writer_rejects_malformed_report_atomically(tmp_path):
    source=tmp_path/'payload.json';source.write_text(json.dumps({'outcome':'blocked','summary':'No reason'}))
    target=tmp_path/'report.json'
    assert artifacts.main(['write-json','--role','sub-coord','--issue','7','--payload-file',str(source),'--result-file',str(target)]) == 1
    assert not target.exists()


def test_check_evidence_reason_table():
    from ai_skills.verification import check_evidence_reason

    sha = 'a' * 40
    check = {'command': 'check', 'passed': True, 'evidence': 'exit 0'}
    passing = {'passed': True, 'commit_sha': sha, 'checks': [check]}
    for report, reason in (
        (passing, ''),
        (None, 'implementation-verification-failed'),
        ({**passing, 'commit_sha': 'b' * 40}, 'verification-evidence-commit-mismatch'),
        ({**passing, 'checks': []}, 'verification-checks-incomplete'),
        ({**passing, 'checks': [{**check, 'command': 'other'}]}, 'verification-checks-incomplete'),
        ({**passing, 'checks': [{**check, 'evidence': ' '}]}, 'verification-check-not-passed'),
    ):
        assert check_evidence_reason(report, expected_commit=sha, required_checks=['check']) == reason


def _closure(*ids, **changes):
    return [{'id': comment, 'status': 'addressed', 'action': 'Fixed ' + comment, 'files_changed': ['a.py'],
             'verification': ['pytest passed'], **changes} for comment in ids]


@pytest.mark.parametrize(('closure', 'expected', 'reason'), [
    (_closure('R1', 'R2'), ['R1', 'R2'], ''),
    (_closure('R2', 'R1'), ['R1', 'R2'], ''),
    (_closure('R1', status='declined', files_changed=[], extra='kept'), ['R1'], ''),
    ([], [], ''),
    (_closure('R1'), [], 'review-comment-closure-unexpected-id'),
    (_closure('R1'), ['R1', 'R2'], 'review-comment-closure-incomplete'),
    (_closure('R1', 'R1'), ['R1'], 'review-comment-closure-duplicate-id'),
    (_closure('r1'), ['R1'], 'review-comment-closure-unexpected-id'),
    (_closure('R1', status='blocked'), ['R1'], 'review-comment-unresolved'),
    (_closure('R1', action=''), ['R1'], 'review-comment-closure-action-missing'),
    (_closure('R1', action=None), ['R1'], 'review-comment-closure-action-missing'),
    (_closure('R1', files_changed=[None]), ['R1'], 'invalid-review-comment-closure'),
    (_closure('R1', verification=['ok', '']), ['R1'], 'review-comment-closure-evidence-missing'),
    (_closure('R1', status='declined', verification=None), ['R1'], 'review-comment-closure-evidence-missing'),
    ([[]], ['R1'], 'invalid-review-comment-closure'),
    ([{'id': ['R1']}], ['R1'], 'invalid-review-comment-closure'),
    (None, [], 'invalid-review-comment-closure'),
])
def test_review_comment_closure_reason_table(closure, expected, reason):
    payload = {'review_comment_closure': closure} if closure is not None else {}
    assert artifacts.review_comment_closure_reason(payload, expected) == reason
    assert artifacts.review_comment_closure_reason(None, expected) == 'invalid-review-comment-closure'
