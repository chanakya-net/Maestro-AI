from ai_skills.reports import render_pr_body

def test_pr_body_avoids_implicit_close():
    state={"issue_registry":{"7":{"status":"completed"}}, "completed_summaries":[{"issue":7,"summary":"Fixes #9", "lines_added":3}]}
    result=render_pr_body(state, {})
    assert "- #7" in result
    assert "- Lines added: 3" in result
    assert "Fixes #9" not in result

import json
from pathlib import Path
import pytest
from ai_skills import reports

def test_only_completed_issues_in_model_and_verification_tables():
    state={"issue_registry":{"10":{"status":"completed"},"2":{"status":"completed"},"3":{"status":"blocked"}},"completed_summaries":[{"issue":2,"lines_added":7,"lines_deleted":3,"model_usage":[{"role":"impl","model":"summary-model","agent":"codex"}]}]}
    evidence={"2":{"verification":{"passed":True,"evidence":"Fixes #23 | new\nline"},"model_usage":[{"role":"impl","model":"fixture","agent":"codex","selection_reason":"forced-model"}]},"10":{"verification":{"passed":False,"evidence":"failed"}}}
    rendered=render_pr_body(state,evidence)
    assert rendered.index("- #2") < rendered.index("- #10")
    assert "| #3 |" not in rendered
    assert "| #2 | passed | Fixes \\#23 \\| new line |" in rendered
    assert "| #10 | failed | failed |" in rendered
    assert "fixture" in rendered and "summary-model" not in rendered
    assert "- Lines added: 7" in rendered and "- Lines deleted: 3" in rendered

def test_render_cli_uses_recovery_report_without_mutating_state(tmp_path,capsys):
    report=tmp_path/"recovery.json";report.write_text(json.dumps({"verification":{"passed":True,"evidence":"recovered"},"model_usage":[{"role":"merge-recovery","model":"fixture"}]}))
    state=tmp_path/"state.json";state.write_text(json.dumps({"issue_registry":{"7":{"status":"completed","merge_recovery_report_file":"recovery.json"}}}))
    before=state.read_bytes()
    assert reports.main(["render","--state-file",str(state)]) == 0
    rendered=capsys.readouterr().out
    assert "merge-recovery" in rendered and "recovered" in rendered
    assert state.read_bytes() == before

@pytest.mark.parametrize("payload",["{","[]","null"])
def test_report_authoritative_state_errors_not_empty_report(tmp_path,payload):
    state=tmp_path/"state.json";state.write_text(payload)
    with pytest.raises(SystemExit): reports.main(["render","--state-file",str(state)])

def test_report_missing_optional_evidence_is_explicit_unknown():
    rendered=render_pr_body({"issue_registry":{"7":{"status":"completed"}}},{})
    assert "missing-model-usage" in rendered
    assert "| #7 | unknown | unknown |" in rendered
    assert "Closes #7" not in rendered
