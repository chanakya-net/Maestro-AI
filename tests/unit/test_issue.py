"""Unit tests for ai-skills issue subcommands and git preserve-worktree."""
import json
import subprocess
from pathlib import Path

import pytest

from ai_skills import dispatch, git_operations, issue


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "a.txt").write_text("line1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    return repo


def _head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


# ------------------------------------------------------------------ git preserve-worktree


def test_preserve_worktree_captures_staged_unstaged_binary_and_untracked(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("line1\nstaged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    (repo / "a.txt").write_text("line1\nstaged\nunstaged\n", encoding="utf-8")
    (repo / "sub").mkdir()
    (repo / "sub" / "bin.dat").write_bytes(b"\x00\x01\x02hello\xff")

    dest = tmp_path / "recovery"
    result = git_operations.preserve_worktree(repo, dest)
    assert result["dirty"] is True
    assert Path(result["patch"]).is_file()
    assert Path(result["cached_patch"]).is_file()
    assert result["untracked_files"] == ["sub/bin.dat"]
    assert (Path(result["untracked_dir"]) / "sub" / "bin.dat").read_bytes() == b"\x00\x01\x02hello\xff"


def test_preserve_worktree_clean_repo_reports_not_dirty(tmp_path):
    repo = _init_repo(tmp_path)
    result = git_operations.preserve_worktree(repo, tmp_path / "rec")
    assert result["dirty"] is False
    assert result["patch"] is None
    assert result["cached_patch"] is None
    assert result["untracked_files"] == []


# ------------------------------------------------------------------ issue state


def test_issue_state_init_record_clear_and_set(tmp_path):
    issue_dir = tmp_path / ".run-with-it" / "issues" / "42"
    state = issue.init_sub_state(
        issue_dir, issue="42", worktree="/tmp/wt", issue_branch="Maestro/fox-issue-42",
        issue_base_sha="a" * 40, issue_base_source="remote",
    )
    assert state["issue_number"] == "42"
    assert state["phase"] == "bootstrap"
    assert state["compaction_requested"] is False

    sf = issue_dir / "workers" / "impl" / "cycle-1-attempt-1.state.json"
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps({"pid": 9999, "attempt_id": "att-1", "started_at": "2026-01-01T00:00:00Z"}))
    Path(str(sf) + ".config.json").write_text(json.dumps({
        "agent_run": {
            "issue": "42", "role": "impl", "cycle": "1", "attempt_id": "att-1",
            "agent": "codex", "model": "gpt-6.2-sol", "effort": "high",
            "log_file": "/tmp/a.log", "done_file": "/tmp/a.done", "result_file": "/tmp/a-result.json",
        }
    }))
    route_file = tmp_path / "route.json"
    route_file.write_text(json.dumps({
        "agent": "codex", "model": "gpt-6.2-sol", "effort": "high", "selection_reason": "usage-share-debt",
    }))

    after_rec = issue.record_attempt(issue_dir, state_file=sf, route_file=route_file, phase="implement")
    assert after_rec["phase"] == "implement"
    assert len(after_rec["in_flight_agents"]) == 1
    assert after_rec["in_flight_agents"][0]["selection_reason"] == "usage-share-debt"
    assert len(after_rec["routes"]) == 1

    after_set = issue.set_sub_state_fields(issue_dir, compaction_requested=True, fields={"effective_complexity": "holy-fuck"})
    assert after_set["compaction_requested"] is True
    assert after_set["effective_complexity"] == "extreme"

    after_clr = issue.clear_attempt(issue_dir, state_file=sf, phase="review")
    assert after_clr["in_flight_agents"] == []
    assert after_clr["phase"] == "review"


# ------------------------------------------------------------------ worker-config


def test_worker_config_auto_increments_attempt_and_validates(tmp_path):
    issue_dir = tmp_path / ".run-with-it" / "issues" / "7"
    ctx = tmp_path / "ctx.md"
    ctx.write_text("task")
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"agent": "codex", "model": "gpt-6.2-sol", "effort": "high"}))

    first = issue.build_worker_config(
        issue_dir=issue_dir, issue="7", role="impl", cycle="1",
        context_file=ctx, repo_root=tmp_path, route_file=route,
    )
    assert first["attempt"] == 1
    assert first["paths"]["result_file"].endswith("cycle-1-attempt-1-result.json")
    assert first["prompt_file"].endswith("prompt.md")

    second = issue.build_worker_config(
        issue_dir=issue_dir, issue="7", role="impl", cycle="1",
        context_file=ctx, repo_root=tmp_path, route_file=route,
    )
    assert second["attempt"] == 2
    assert second["paths"]["state_file"].endswith("cycle-1-attempt-2.state.json")

    rev = issue.build_worker_config(
        issue_dir=issue_dir, issue="7", role="review", cycle="1",
        context_file=ctx, repo_root=tmp_path, agent="claude", model="claude-opus-5-5", effort="high",
    )
    assert rev["paths"]["result_file"].endswith("cycle-1-attempt-1-status.json")
    assert rev["paths"]["review_instructions_file"].endswith("cycle-1-attempt-1-instructions.json")


# ------------------------------------------------------------------ failure-action


def test_failure_action_separates_availability_and_capability(tmp_path):
    issue_dir = tmp_path / "issue-5"
    role_dir = issue_dir / "workers" / "impl"
    role_dir.mkdir(parents=True)

    s1 = role_dir / "cycle-1-attempt-1.state.json"
    s1.write_text(json.dumps({"stall_reason": "rate-limited", "unavailable_reason": "rate-limited", "agent": "codex", "model": "gpt-6.2-sol"}))
    Path(str(s1) + ".config.json").write_text(json.dumps({"agent_run": {"agent": "codex", "model": "gpt-6.2-sol"}}))
    out1 = issue.decide_failure_action(issue_dir=issue_dir, role="impl", cycle="1", max_fallbacks=2)
    assert out1["action"] == "reroute-availability"
    assert out1["availability_attempts"] == 1
    assert "--failed-attempt-state" in out1["route_args"]

    # Capability retries exhaust after 3 attempts (1 initial + 2 retries -> >2) -> artifact-recovery
    for n in (2, 3, 4):
        sf = role_dir / f"cycle-1-attempt-{n}.state.json"
        sf.write_text(json.dumps({"stall_reason": "missing-result-artifact"}))
        Path(str(sf) + ".config.json").write_text(json.dumps({"agent_run": {"agent": "claude", "model": f"m-{n}"}}))
    out2 = issue.decide_failure_action(issue_dir=issue_dir, role="impl", cycle="1", max_capability_retries=2)
    assert out2["action"] == "artifact-recovery"
    assert out2["capability_attempts"] == 3
    assert out2["recovery_dir"] is not None


# ------------------------------------------------------------------ review-gate & next-step


def test_review_gate_rules(tmp_path):
    repo = _init_repo(tmp_path)
    base = _head(repo)
    (repo / "a.txt").write_text("line1\nline2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-aq", "-m", "small"], check=True)
    head = _head(repo)

    small_pass = issue.evaluate_review_gate(repo_root=repo, base_sha=base, head_sha=head, tests_all_passed=True)
    assert small_pass["decision"] == "skip"
    small_no_tests = issue.evaluate_review_gate(repo_root=repo, base_sha=base, head_sha=head, tests_all_passed=False)
    assert small_no_tests["decision"] == "mandatory"

    for i in range(4):
        (repo / f"f{i}.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "four files"], check=True)
    big = issue.evaluate_review_gate(repo_root=repo, base_sha=base, head_sha=_head(repo), tests_all_passed=True)
    assert big["decision"] == "mandatory"


def test_next_step_escalation_redesign_and_cycle_cap(tmp_path):
    issue_dir = tmp_path / "issue-9"
    issue.init_sub_state(issue_dir, issue="9")
    verdict = tmp_path / "status.json"
    verdict.write_text(json.dumps({"verdict": "revise", "comment_count": 2, "nitpick_only": False}))

    r1 = issue.compute_next_step(issue_dir=issue_dir, verdict_file=verdict, reviewer_model="claude-opus-5-5", effective_complexity="medium")
    assert r1["action"] == "spawn-modify" and r1["escalated"] is False and r1["modify_band"] == "medium"
    assert r1["issue_reviewer"] == "claude-opus-5-5"

    r2 = issue.compute_next_step(issue_dir=issue_dir, verdict_file=verdict, reviewer_model="other-model")
    assert r2["action"] == "spawn-modify" and r2["escalated"] is True and r2["modify_band"] == "medium-hard"
    assert r2["issue_reviewer"] == "claude-opus-5-5"  # sticky cycle-1 reviewer preserved

    r3 = issue.compute_next_step(issue_dir=issue_dir, verdict_file=verdict)
    assert r3["redesign_required"] is True

    # Approve terminates cleanly
    verdict.write_text(json.dumps({"verdict": "approve", "comment_count": 0, "nitpick_only": False}))
    r4 = issue.compute_next_step(issue_dir=issue_dir, verdict_file=verdict)
    assert r4["action"] == "merge"


# ------------------------------------------------------------------ resume-plan & report


def test_resume_plan_and_report_mapping(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    base = _head(repo)
    (repo / "a.txt").write_text("line1\nupdated\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-aq", "-m", "work"], check=True)

    issue_dir = tmp_path / ".run-with-it" / "issues" / "11"
    issue.init_sub_state(issue_dir, issue="11", worktree=str(repo), issue_branch="Maestro/fox-issue-11", issue_base_sha=base)
    sf = issue_dir / "workers" / "impl" / "cycle-1-attempt-1.state.json"
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps({"pid": 1}))
    Path(str(sf) + ".config.json").write_text(json.dumps({"agent_run": {"issue": "11", "role": "impl", "cycle": "1"}}))
    issue.record_attempt(issue_dir, state_file=sf, phase="implement")

    monkeypatch.setattr(dispatch, "inspect_worker", lambda p: dispatch.WorkerSnapshot(
        liveness="alive", heartbeat_fresh=True, artifact_valid=False, artifact_reason="",
        done_present=False, current_activity="running", state="running"))
    resume = issue.build_resume_plan(issue_dir)
    assert resume["next_action"] == "wait-in-flight"
    assert resume["in_flight"][0]["action"] == "wait"

    merge_file = tmp_path / "merge.json"
    merge_file.write_text(json.dumps({"outcome": "completed", "commit_sha": "b" * 40, "evidence": ""}))
    rep = issue.write_issue_report(
        issue_dir=issue_dir, outcome="completed", summary="Implemented cleanly.",
        merge_result_file=merge_file, repo_root=repo,
    )
    assert rep["report"]["outcome"] == "completed"
    assert rep["report"]["merge"]["merge_sha"] == "b" * 40
    assert len(rep["report"]["files_modified"]) == 1
    assert Path(rep["report_file"]).is_file()
