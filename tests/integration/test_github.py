from ai_skills.github import GitHubUpdateRequest, update_github

def test_disabled_github_preserves_completed_state(tmp_path, monkeypatch):
    import json
    state=tmp_path / "state.json"
    state.write_text(json.dumps({"issue_registry":{"7":{"status":"completed"}}}))
    monkeypatch.setenv("RUN_WITH_IT_GITHUB_UPDATES", "0")
    result=update_github(GitHubUpdateRequest(state_file=str(state), run_root=str(tmp_path), issue="7", outcome="completed", report_file=str(tmp_path / "report.json")))
    assert result.action == "skipped"
    assert json.loads(state.read_text())["issue_registry"]["7"]["status"] == "completed"

import json
from pathlib import Path
import subprocess
import pytest
from ai_skills import github

@pytest.mark.parametrize("outcome,fail_action,expected_action,closed,detail",[
    ("completed",None,"commented",True,"commented;closed=true"),
    ("blocked",None,"commented",False,"commented;closed=false"),
    ("failed-review",None,"commented",False,"commented;closed=false"),
    ("failed-merge",None,"commented",False,"commented;closed=false"),
    ("completed","comment","failed",False,"comment-failed"),
    ("completed","close","commented",False,"close-failed"),
    ("completed","missing-executable","failed",False,"comment-failed"),
])
def test_terminal_close_owner_and_failures(tmp_path,monkeypatch,outcome,fail_action,expected_action,closed,detail):
    state=tmp_path/"state.json";state.write_text(json.dumps({"issue_registry":{"7":{"status":outcome}}}))
    report=tmp_path/"report.json";report.write_text(json.dumps({"outcome":outcome,"summary":"Fixture","verification":{"passed":True}}))
    monkeypatch.delenv("RUN_WITH_IT_GITHUB_UPDATES",raising=False)
    monkeypatch.setattr(github.shutil,"which",lambda command:"fake-gh")
    monkeypatch.setattr(github,"has_github_remote",lambda root:True)
    calls=[];comment_paths=[]
    def run(argv,**kwargs):
        assert not Path(str(state)+".lock").exists(), "network must not hold state lock"
        calls.append(argv)
        if "--body-file" in argv:
            body=Path(argv[-1]);comment_paths.append(body)
            assert "## Verification" in body.read_text()
        if fail_action == "missing-executable": raise FileNotFoundError("gh disappeared")
        return subprocess.CompletedProcess(argv,1 if argv[2]==fail_action else 0)
    monkeypatch.setattr(github.subprocess,"run",run)
    result=update_github(GitHubUpdateRequest(str(state),str(tmp_path),"7",outcome,str(report)))
    assert result.action == expected_action
    assert result.closed is closed
    entry=json.loads(state.read_text())["issue_registry"]["7"]
    assert entry["status"] == outcome
    assert entry["github_update_detail"] == detail
    assert len(calls)==(2 if outcome=="completed" and fail_action not in {"comment","missing-executable"} else 1)
    assert all(not path.exists() for path in comment_paths)

def test_nonterminal_outcome_has_no_effect(tmp_path,monkeypatch):
    state=tmp_path/"state.json";state.write_text('{"issue_registry":{}}')
    before=state.read_bytes()
    monkeypatch.setattr(github.shutil,"which",lambda command:pytest.fail("nonterminal must not invoke gh"))
    result=update_github(GitHubUpdateRequest(str(state),str(tmp_path),"7","in_progress","missing"))
    assert result.action == "ignored"
    assert state.read_bytes()==before

@pytest.mark.parametrize("mode,reason",[("missing","gh-not-found"),("local","no-github-remote")])
def test_missing_cli_or_remote_skips(tmp_path,monkeypatch,mode,reason):
    state=tmp_path/"state.json";state.write_text('{"issue_registry":{"7":{"status":"completed"}}}')
    monkeypatch.delenv("RUN_WITH_IT_GITHUB_UPDATES",raising=False)
    monkeypatch.setattr(github.shutil,"which",lambda command:None if mode=="missing" else "gh")
    monkeypatch.setattr(github,"has_github_remote",lambda root:False)
    result=update_github(GitHubUpdateRequest(str(state),str(tmp_path),"7","completed","missing"))
    assert result.reason == reason

def test_comment_render_falls_back_only_for_review_and_tokens(tmp_path):
    (tmp_path/"report.json").write_text(json.dumps({"outcome":"failed-merge","summary":"OLD","verification":{"passed":False},"token_usage":{"input_tokens":25},"review_summary":{"cycles_used":1,"final_verdict":"approve","reviewer_model":"fixture"}}))
    recovery=tmp_path/"recovery.json";recovery.write_text(json.dumps({"outcome":"completed","summary":"RECOVERY","verification":{"passed":True,"commands_run":["pytest"],"evidence":"new"}}))
    rendered=github.render_terminal_comment(str(recovery),"blocked")
    assert "RECOVERY" in rendered and "OLD" not in rendered
    assert "State: passed" in rendered
    assert "Input tokens: 25" in rendered
    assert "Review: approve (1 cycle)" in rendered
