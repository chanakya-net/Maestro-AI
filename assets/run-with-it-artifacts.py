#!/usr/bin/env python3
"""Validate and repair run-with-it worker artifacts.

The platform dispatchers call this helper so Bash and PowerShell enforce the
same role-specific completion contract.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


COMPLEXITY_LEVELS = {
    "quite-easy",
    "easy",
    "medium",
    "medium-hard",
    "complex",
    "holy-fuck",
}

COMPLEXITY_SCORE_KEYS = {
    "dependency_complexity",
    "ownership_overlap_risk",
    "architecture_risk",
    "orchestration_burden",
    "verification_risk",
    "ambiguity_of_requirements",
    "integration_surface_breadth",
    "rollback_recovery_risk",
    "blast_radius",
}

REVIEW_VERDICTS = {"approve", "revise", "reject"}

# Required keys for a complete plan.json (see plan-prompt.md / sub-coordinator
# Worker Done Files). The plan worker never commits, so there is no commit_sha.
PLAN_REQUIRED_KEYS = {
    "schema_version",
    "issue",
    "role",
    "status",
    "approach",
    "complexity_level",
    "slices",
}
REVIEW_SEVERITIES = {"info", "warning", "critical"}
REVIEW_CATEGORIES = {
    "requirement",
    "security",
    "correctness",
    "test",
    "regression",
    "performance",
    "maintainability",
    "scope",
}
NON_NITPICK_CATEGORIES = {"requirement", "security", "correctness", "test", "regression"}


def load_json(path: str) -> tuple[Any | None, str | None]:
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return None, "missing"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle), None
    except Exception:
        return None, "invalid-json"


def write_json_atomic(path: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f"{target.name}.tmp.{os.getpid()}")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, target)


def issue_report_path(args: argparse.Namespace) -> Path | None:
    issue_dir = getattr(args, "issue_dir", "") or ""
    if not issue_dir:
        return None
    return Path(issue_dir) / "report.json"


def is_issue_report_path(path: Path, issue_dir: Path | None) -> bool:
    if issue_dir is None:
        return False
    try:
        return path.resolve() == (issue_dir / "report.json").resolve()
    except OSError:
        return False


def result_file_is_issue_report(args: argparse.Namespace) -> bool:
    if args.role not in {"impl", "modify"}:
        return False
    report_path = issue_report_path(args)
    if report_path is None:
        return False
    return is_issue_report_path(Path(args.result_file), report_path.parent)


def worker_payload_written_to_issue_report(args: argparse.Namespace) -> bool:
    if args.role not in {"impl", "modify"}:
        return False
    report_path = issue_report_path(args)
    if report_path is None:
        return False
    payload, error = load_json(str(report_path))
    if error or not isinstance(payload, dict):
        return False
    return str(payload.get("issue")) == str(args.issue) and payload.get("role") == args.role


def git_output(repo_root: str, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", repo_root, *args],
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()


def git_success(repo_root: str, *args: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", repo_root, *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def current_head(repo_root: str) -> str:
    return git_output(repo_root, "rev-parse", "HEAD")


def repo_available(repo_root: str) -> bool:
    try:
        return git_output(repo_root, "rev-parse", "--is-inside-work-tree") == "true"
    except Exception:
        return False


def working_tree_clean(repo_root: str) -> bool:
    try:
        return git_output(repo_root, "status", "--porcelain") == ""
    except Exception:
        return False


def commit_exists(repo_root: str, commit_sha: str) -> bool:
    return git_success(repo_root, "cat-file", "-e", f"{commit_sha}^{{commit}}")


def resolve_commit(repo_root: str, revision: str) -> str | None:
    """Resolve one revision to its canonical commit object ID."""
    if not revision or revision == "NONE":
        return None
    try:
        resolved = git_output(repo_root, "rev-parse", "--verify", f"{revision}^{{commit}}")
    except Exception:
        return None
    return resolved if re.fullmatch(r"[0-9a-fA-F]{40}", resolved) else None


def is_ancestor(repo_root: str, ancestor: str, descendant: str) -> bool:
    return git_success(repo_root, "merge-base", "--is-ancestor", ancestor, descendant)


def abort_cherry_pick(repo_root: str) -> None:
    git_success(repo_root, "cherry-pick", "--abort")


def commit_salvaged_tree(repo_root: str, issue: str, role: str) -> bool:
    """Commit a worker's uncommitted work so a stall/kill does not lose it."""
    if not git_success(repo_root, "add", "-A"):
        return False
    message = f"salvage(#{issue}): recover {role} work from interrupted worker"
    return git_success(repo_root, "commit", "--no-verify", "-m", message)


def recover_wrong_worktree_commit(args: argparse.Namespace, payload: dict[str, Any], head: str, commit_sha: str) -> bool:
    if not args.pre_spawn_head:
        return False
    if head != args.pre_spawn_head:
        return False
    if not working_tree_clean(args.repo_root):
        return False
    if not commit_exists(args.repo_root, commit_sha):
        return False
    if not is_ancestor(args.repo_root, args.pre_spawn_head, commit_sha):
        return False

    result = subprocess.run(
        ["git", "-C", args.repo_root, "cherry-pick", "--no-edit", commit_sha],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        abort_cherry_pick(args.repo_root)
        return False

    try:
        recovered_head = current_head(args.repo_root)
    except Exception:
        return False
    if recovered_head == head:
        return False

    recovered_payload = dict(payload)
    recovered_payload["commit_sha"] = recovered_head
    recovered_payload["recovered_from_commit"] = commit_sha
    recovered_payload["recovery"] = "cherry-picked commit from outside issue worktree into issue worktree"
    write_json_atomic(args.result_file, recovered_payload)
    return True


def implementation_result_reason(args: argparse.Namespace, payload: Any) -> str:
    if not isinstance(payload, dict):
        return "invalid-result-artifact"
    if str(payload.get("issue")) != str(args.issue):
        return "invalid-result-artifact"
    if payload.get("role") != args.role:
        return "invalid-result-artifact"
    if payload.get("status") == "artifact_recovery_required":
        return "artifact-recovery-required"
    if payload.get("status") != "success":
        return "invalid-result-artifact"

    # Verified no-op: the slice is already satisfied upstream, so the worker made
    # no new commit but ran the verification suite and it passed. Accept this as
    # success instead of forcing a terminal missing-implementation-commit /
    # invalid-result-artifact (see issue 620: a correct no-op burned 7 review
    # cycles + a manual requeue). Requires an explicit no_op flag AND passing
    # verification so a worker cannot claim success without doing the work.
    if payload.get("no_op") is True:
        verification = payload.get("verification")
        if not isinstance(verification, dict) or verification.get("passed") is not True:
            return "verified-no-op-requires-passing-verification"
        if args.repo_root and repo_available(args.repo_root):
            try:
                head = current_head(args.repo_root)
            except Exception:
                return "implementation-repo-unavailable"
            if args.pre_spawn_head and head != args.pre_spawn_head:
                # HEAD advanced, so this is not actually a no-op.
                return "verified-no-op-with-unexpected-commit"
        return ""

    commit_sha = payload.get("commit_sha")
    if not isinstance(commit_sha, str) or not commit_sha or commit_sha == "NONE":
        return "invalid-result-artifact"
    files_committed = payload.get("files_committed")
    if not isinstance(files_committed, list) or not files_committed:
        return "invalid-result-artifact"
    verification = payload.get("verification")
    if not isinstance(verification, dict):
        return "invalid-result-artifact"
    if verification.get("passed") is not True:
        return "implementation-verification-failed"

    if not args.repo_root or not repo_available(args.repo_root):
        return "implementation-repo-unavailable"
    try:
        head = current_head(args.repo_root)
    except Exception:
        return "implementation-repo-unavailable"
    canonical_commit = resolve_commit(args.repo_root, commit_sha)
    if canonical_commit is None:
        return "invalid-implementation-commit"
    if canonical_commit != commit_sha:
        canonical_payload = dict(payload)
        canonical_payload["commit_sha"] = canonical_commit
        write_json_atomic(args.result_file, canonical_payload)
        payload = canonical_payload
    if head != canonical_commit:
        if recover_wrong_worktree_commit(args, payload, head, canonical_commit):
            return ""
        return "commit-outside-issue-worktree"
    if args.pre_spawn_head and canonical_commit == resolve_commit(args.repo_root, args.pre_spawn_head):
        return "missing-implementation-commit"
    return ""


def valid_complexity_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if not isinstance(payload.get("total"), int):
        return False
    if payload.get("level") not in COMPLEXITY_LEVELS:
        return False
    scores = payload.get("scores")
    rationale = payload.get("rationale")
    if not isinstance(scores, dict) or set(scores) != COMPLEXITY_SCORE_KEYS:
        return False
    if not isinstance(rationale, dict) or set(rationale) != COMPLEXITY_SCORE_KEYS:
        return False
    return all(isinstance(scores[key], int) and 1 <= scores[key] <= 5 for key in COMPLEXITY_SCORE_KEYS)


def valid_plan_payload(payload: Any) -> bool:
    """Structural validity for plan.json. complexity_level must be a router band
    and slices must be a non-empty ordered list; the plan never commits, so no
    commit_sha is required. Issue-number matching is checked by the caller."""
    if not isinstance(payload, dict):
        return False
    if not PLAN_REQUIRED_KEYS.issubset(payload):
        return False
    if payload.get("role") != "plan":
        return False
    if not isinstance(payload.get("schema_version"), int):
        return False
    # A self-reported failure is not a usable plan. Match the impl/modify
    # convention (status must be exactly "success") so a failed planner cannot
    # refine routing or be treated as a completed plan.
    if payload.get("status") != "success":
        return False
    approach = payload.get("approach")
    if not isinstance(approach, str) or not approach.strip():
        return False
    if payload.get("complexity_level") not in COMPLEXITY_LEVELS:
        return False
    slices = payload.get("slices")
    if not isinstance(slices, list) or not slices:
        return False
    return True


def complexity_payload_from_log(log_file: str) -> dict[str, Any] | None:
    if not log_file or not os.path.exists(log_file) or os.path.getsize(log_file) == 0:
        return None
    try:
        text = Path(log_file).read_text(encoding="utf-8")
    except Exception:
        return None

    decoder = json.JSONDecoder()
    index = 0
    while True:
        start = text.find("{", index)
        if start == -1:
            return None
        try:
            payload, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if valid_complexity_payload(payload):
            return payload
        index = start + max(end, 1)


def valid_review_status(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("verdict") in REVIEW_VERDICTS
        and isinstance(payload.get("comment_count"), int)
        and payload.get("comment_count", -1) >= 0
        and isinstance(payload.get("nitpick_only"), bool)
    )


def non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_nitpick_comment(comment: dict[str, Any]) -> bool:
    return comment.get("severity") == "info" and isinstance(comment.get("fix"), str) and comment["fix"].startswith("[nitpick]")


def valid_review_comment(comment: Any) -> bool:
    if not isinstance(comment, dict):
        return False
    for key in ("id", "file", "severity", "category", "fix", "evidence", "expected_change", "verification"):
        if not non_empty_str(comment.get(key)):
            return False
    if comment.get("severity") not in REVIEW_SEVERITIES:
        return False
    category = comment.get("category")
    if category not in REVIEW_CATEGORIES:
        return False
    if not isinstance(comment.get("blocking"), bool):
        return False
    line = comment.get("line")
    if line is not None and (not isinstance(line, int) or line < 1):
        return False
    if comment["blocking"] and comment["severity"] == "info":
        return False
    if category in NON_NITPICK_CATEGORIES and is_nitpick_comment(comment):
        return False
    return True


def valid_review_instructions(payload: Any) -> bool:
    if not (
        isinstance(payload, dict)
        and payload.get("verdict") in REVIEW_VERDICTS
        and isinstance(payload.get("summary"), str)
        and isinstance(payload.get("comments"), list)
        and isinstance(payload.get("blocking_reasons"), list)
    ):
        return False
    comments = payload["comments"]
    if any(not valid_review_comment(comment) for comment in comments):
        return False
    verdict = payload["verdict"]
    if verdict == "approve" and comments and not nitpick_only(comments):
        return False
    if verdict == "revise" and not comments:
        return False
    if verdict == "reject" and not payload["blocking_reasons"]:
        return False
    return True


def repair_protected_nitpicks(payload: Any) -> Any | None:
    """Salvage a revise/reject instructions artifact whose ONLY defect is one or
    more protected-category nitpicks.

    Reviewers (notably claude-sonnet-4-6) sometimes mark a genuinely low-priority
    security/correctness/test/requirement/regression observation as an ``info``
    ``[nitpick]``. The schema forbids that (those categories are never nitpicks),
    so ``valid_review_comment`` rejects the comment and the whole artifact is
    thrown out -- discarding every other valid finding and hard-blocking an
    otherwise mergeable issue (issue 653, where two consecutive claude reviews
    were lost this way). Rather than drop the feedback, escalate each offending
    comment to a ``warning`` and strip the ``[nitpick]`` marker. Escalation is
    fail-safe: it treats the finding as *more* important, the direction the
    protected-category rule already wants.

    Restricted to ``revise``/``reject`` verdicts, where every comment already
    flows to the modifier. An ``approve`` carrying a protected-category nitpick is
    the exact "real issue hidden as cosmetic" case the rule guards against, so it
    is left to fail and retry rather than silently rewritten.

    Returns a repaired copy, or ``None`` when the artifact is not repairable this
    way (wrong shape, approve verdict, nothing to escalate, or some other
    validation defect remains). Callers must re-check ``valid_review_instructions``
    on the result before trusting it.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("verdict") not in ("revise", "reject"):
        return None
    comments = payload.get("comments")
    if not isinstance(comments, list):
        return None
    repaired_comments: list[Any] = []
    changed = False
    for comment in comments:
        if (
            isinstance(comment, dict)
            and comment.get("category") in NON_NITPICK_CATEGORIES
            and is_nitpick_comment(comment)
        ):
            comment = dict(comment)
            comment["severity"] = "warning"
            comment["fix"] = re.sub(r"^\s*\[nitpick\]\s*", "", comment.get("fix", ""))
            changed = True
        repaired_comments.append(comment)
    if not changed:
        return None
    repaired = dict(payload)
    repaired["comments"] = repaired_comments
    return repaired


def review_instructions_file(result_file: str) -> str:
    if result_file.endswith("-status.json"):
        return f"{result_file[:-len('-status.json')]}-instructions.json"
    return ""


def canonical_review_retry_status_file(result_file: str) -> str:
    match = re.match(r"^(.*cycle-[0-9]+)-attempt-[0-9]+-status\.json$", result_file)
    if not match:
        return ""
    return f"{match.group(1)}-status.json"


def canonical_complexity_retry_result_file(result_file: str) -> str:
    match = re.match(r"^(.*cycle-[0-9]+)-attempt-[0-9]+-result\.json$", result_file)
    if not match:
        return ""
    return f"{match.group(1)}-result.json"


def nitpick_only(comments: list[Any]) -> bool:
    if not comments:
        return False
    for comment in comments:
        if not isinstance(comment, dict):
            return False
        if not is_nitpick_comment(comment):
            return False
    return True


def review_result_reason(args: argparse.Namespace, status_payload: Any, status_error: str | None) -> str:
    if status_error == "missing":
        return "missing-result-artifact"
    if status_error or not valid_review_status(status_payload):
        return "invalid-review-status-artifact"

    instructions_file = review_instructions_file(args.result_file)
    if not instructions_file:
        return "missing-review-instructions-artifact"
    instructions_payload, instructions_error = load_json(instructions_file)
    if instructions_error == "missing":
        return "missing-review-instructions-artifact"
    if instructions_error or not valid_review_instructions(instructions_payload):
        return "invalid-review-instructions-artifact"
    if instructions_payload.get("verdict") != status_payload.get("verdict"):
        return "review-artifact-verdict-mismatch"
    comments = instructions_payload.get("comments", [])
    if status_payload.get("comment_count") != len(comments):
        return "review-comment-count-mismatch"
    if status_payload.get("nitpick_only") != nitpick_only(comments):
        return "review-nitpick-only-mismatch"
    return ""


def result_failure_reason(args: argparse.Namespace) -> str:
    if result_file_is_issue_report(args):
        return "worker-result-path-is-sub-coordinator-report"
    payload, error = load_json(args.result_file)
    if error == "missing":
        return "missing-result-artifact"
    if args.role in {"impl", "modify"}:
        if error:
            return "invalid-result-artifact"
        return implementation_result_reason(args, payload)
    if args.role == "complexity":
        if error or not valid_complexity_payload(payload):
            return "invalid-complexity-result-artifact"
        return ""
    if args.role == "plan":
        if error or not valid_plan_payload(payload):
            return "invalid-plan-result-artifact"
        if str(payload.get("issue")) != str(args.issue):
            return "invalid-plan-result-artifact"
        # The human-readable plan.md is part of the required deliverable — the
        # implementer/reviewer/modifier consume it via RUN_WITH_IT_PLAN_FILE. It
        # lives at the conventional <issue-dir>/plan.md. Reject a plan.json that
        # arrived without it so routing cannot refine while RUN_WITH_IT_PLAN_FILE
        # is silently absent. (No issue-dir → can't locate plan.md, skip the check.)
        issue_dir = getattr(args, "issue_dir", "") or ""
        if issue_dir:
            plan_file = os.path.join(issue_dir, "plan.md")
            if not os.path.exists(plan_file) or os.path.getsize(plan_file) == 0:
                return "missing-plan-file-artifact"
        return ""
    if args.role == "review":
        return review_result_reason(args, payload, error)
    if error or not isinstance(payload, dict):
        return "invalid-result-artifact"
    return ""


# A log marker that means the route was never usable (auth/quota/model
# unavailable) for THIS worker run, rather than the agent trying and producing
# a bad artifact. An infrastructure failure must not consume the capability
# fallback budget (MAX_AGENT_FALLBACKS) — the coordinator excludes the route and
# re-routes instead.
#
# Only `agent-unavailable` is used. It is emitted by run-agent.sh for the
# current runner, and every post-availability retry uses a fresh
# attempt-specific log, so it cannot be inherited. `dispatch-bootstrap-failed`
# is deliberately NOT a marker: a bootstrap loss happens before any runner_pid
# and exits via a path that never calls this classifier, so the only way the
# marker reaches a log scanned here is the foreground bootstrap retry reusing
# the same log (see sub-coordinator-prompt.md) — where it is always stale and
# would misclassify a later real capability failure as infrastructure. Bootstrap
# exemption is handled separately by the coordinator, not by this scan.
INFRASTRUCTURE_LOG_MARKERS = (
    "type=agent-unavailable",
)


def log_signals_unavailable_route(log_file: str) -> bool:
    if not log_file or not os.path.exists(log_file) or os.path.getsize(log_file) == 0:
        return False
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:  # bounded: one line at a time, short-circuits on hit
                for marker in INFRASTRUCTURE_LOG_MARKERS:
                    if marker in line:
                        return True
    except OSError:
        return False
    return False


def failure_class(args: argparse.Namespace) -> int:
    """Classify a worker failure as infrastructure (availability/bootstrap) or
    capability (the agent ran and could not produce a valid artifact)."""
    klass = "infrastructure" if log_signals_unavailable_route(getattr(args, "log_file", "")) else "capability"
    print(klass)
    return 0


def synthesize_implementation(args: argparse.Namespace) -> bool:
    if args.role not in {"impl", "modify"}:
        return False
    if result_file_is_issue_report(args):
        return False
    if os.path.exists(args.result_file) and os.path.getsize(args.result_file) > 0:
        return False
    if worker_payload_written_to_issue_report(args):
        return False
    # A clean worker exit writes a DONE sentinel; a worker the dispatcher is
    # about to kill on a stall has not. Allow salvage without the sentinel only
    # when explicitly invoked from the stall path (--from-stall).
    have_done = bool(args.done_file) and os.path.exists(args.done_file) and os.path.getsize(args.done_file) > 0
    if not have_done and not getattr(args, "from_stall", False):
        return False
    if not args.repo_root or not repo_available(args.repo_root):
        return False
    try:
        head = current_head(args.repo_root)
    except Exception:
        return False
    if not head or not args.pre_spawn_head:
        return False

    note = "Worker advanced HEAD but did not write RUN_WITH_IT_RESULT_FILE; verification evidence was not machine-readable."
    if head == args.pre_spawn_head:
        # HEAD did not advance. On the STALL path only, salvage any uncommitted
        # work the worker left before it was killed by committing the dirty tree
        # (alive-but-silent stalls: issues 601/602/616/617/618). On a clean exit
        # a no-commit worker genuinely failed, so do not salvage — that path must
        # still report missing-implementation-commit / missing-result-artifact.
        if not getattr(args, "from_stall", False):
            return False
        if working_tree_clean(args.repo_root):
            return False
        if not commit_salvaged_tree(args.repo_root, str(args.issue), args.role):
            return False
        try:
            head = current_head(args.repo_root)
        except Exception:
            return False
        if not head or head == args.pre_spawn_head:
            return False
        note = "Worker left uncommitted work and did not write RUN_WITH_IT_RESULT_FILE; dispatcher committed the dirty tree to salvage it. Verification was not run on the salvaged commit."
    try:
        files = [
            line
            for line in git_output(args.repo_root, "show", "--name-only", "--pretty=format:", head).splitlines()
            if line.strip()
        ]
    except Exception:
        return False
    if not files:
        return False
    write_json_atomic(
        args.result_file,
        {
            "schema_version": 1,
            "issue": str(args.issue),
            "role": args.role,
            "status": "artifact_recovery_required",
            "commit_sha": head,
            "files_committed": files,
            "verification": {
                "passed": False,
                "commands": [],
                "source": "dispatcher-synthesized",
                "note": note,
            },
            "source": "dispatcher-synthesized",
        },
    )
    return result_failure_reason(args) == "artifact-recovery-required"


def synthesize_review(args: argparse.Namespace) -> bool:
    if args.role != "review":
        return False

    canonical_status_file = canonical_review_retry_status_file(args.result_file)
    if canonical_status_file:
        canonical_status_payload, canonical_status_error = load_json(canonical_status_file)
        canonical_instructions_file = review_instructions_file(canonical_status_file)
        canonical_instructions_payload: Any | None = None
        canonical_instructions_error: str | None = "missing"
        if canonical_instructions_file:
            canonical_instructions_payload, canonical_instructions_error = load_json(canonical_instructions_file)

        if (
            not canonical_status_error
            and valid_review_status(canonical_status_payload)
            and not canonical_instructions_error
            and valid_review_instructions(canonical_instructions_payload)
            and canonical_status_payload.get("verdict") == canonical_instructions_payload.get("verdict")
        ):
            status_copy = dict(canonical_status_payload)
            instructions_copy = dict(canonical_instructions_payload)
            status_copy.setdefault("source", "dispatcher-copied-from-canonical-retry")
            instructions_copy.setdefault("source", "dispatcher-copied-from-canonical-retry")
            write_json_atomic(args.result_file, status_copy)
            attempt_instructions_file = review_instructions_file(args.result_file)
            if attempt_instructions_file:
                write_json_atomic(attempt_instructions_file, instructions_copy)
            return result_failure_reason(args) == ""

    status_payload, status_error = load_json(args.result_file)
    instructions_file = review_instructions_file(args.result_file)
    instructions_payload: Any | None = None
    instructions_error: str | None = "missing"
    if instructions_file:
        instructions_payload, instructions_error = load_json(instructions_file)

    if (status_error or not valid_review_status(status_payload)) and valid_review_instructions(instructions_payload):
        comments = instructions_payload.get("comments", [])
        write_json_atomic(
            args.result_file,
            {
                "verdict": instructions_payload["verdict"],
                "comment_count": len(comments),
                "nitpick_only": nitpick_only(comments),
                "source": "dispatcher-synthesized",
            },
        )
        return result_failure_reason(args) == ""

    # Repair protected-category nitpicks before giving up. A revise/reject
    # instructions artifact that validates except for info+[nitpick] comments in a
    # protected category (security, correctness, test, requirement, regression)
    # must not hard-block the issue: escalate those comments to warnings so the
    # reviewer's feedback reaches the modifier instead of being discarded. The
    # status file is rewritten consistently (verdict/comment_count/nitpick_only)
    # so the dispatcher's verdict and comment-count cross-checks still pass. An
    # approve verdict is intentionally left to fail/retry (see issue 653).
    if (
        instructions_file
        and instructions_payload is not None
        and not instructions_error
        and not valid_review_instructions(instructions_payload)
    ):
        repaired_instructions = repair_protected_nitpicks(instructions_payload)
        if repaired_instructions is not None and valid_review_instructions(repaired_instructions):
            repaired_instructions.setdefault("source", "dispatcher-repaired-protected-nitpick")
            write_json_atomic(instructions_file, repaired_instructions)
            repaired_comments = repaired_instructions["comments"]
            write_json_atomic(
                args.result_file,
                {
                    "verdict": repaired_instructions["verdict"],
                    "comment_count": len(repaired_comments),
                    "nitpick_only": nitpick_only(repaired_comments),
                    "source": "dispatcher-repaired-protected-nitpick",
                },
            )
            return result_failure_reason(args) == ""

    # Only synthesize an empty approve when the reviewer self-reported ZERO
    # comments. A status with comment_count > 0 but a missing/invalid
    # instructions file means real review feedback was lost — synthesizing an
    # approve here silently drops it. Fall through to a failure reason instead so
    # the coordinator retries the reviewer (see issues 622/625/626/628).
    if (
        valid_review_status(status_payload)
        and status_payload.get("verdict") == "approve"
        and status_payload.get("comment_count") == 0
        and instructions_file
        and (instructions_error or not valid_review_instructions(instructions_payload))
    ):
        write_json_atomic(
            instructions_file,
            {
                "verdict": "approve",
                "summary": "Dispatcher synthesized approve instructions because the reviewer wrote a valid approve status artifact with zero comments but omitted REVIEWER_INSTRUCTIONS_FILE.",
                "comments": [],
                "blocking_reasons": [],
                "source": "dispatcher-synthesized",
            },
        )
        return result_failure_reason(args) == ""

    return False


def synthesize_complexity(args: argparse.Namespace) -> bool:
    if args.role != "complexity":
        return False
    if os.path.exists(args.result_file) and os.path.getsize(args.result_file) > 0:
        return False

    canonical_result_file = canonical_complexity_retry_result_file(args.result_file)
    if canonical_result_file:
        canonical_payload, canonical_error = load_json(canonical_result_file)
        if not canonical_error and valid_complexity_payload(canonical_payload):
            payload = dict(canonical_payload)
            payload.setdefault("source", "dispatcher-copied-from-canonical-retry")
            write_json_atomic(args.result_file, payload)
            return result_failure_reason(args) == ""

    if not args.done_file or not os.path.exists(args.done_file) or os.path.getsize(args.done_file) == 0:
        return False

    payload = complexity_payload_from_log(getattr(args, "log_file", ""))
    if payload is None:
        return False
    payload = dict(payload)
    payload["source"] = "dispatcher-synthesized-from-log"
    write_json_atomic(args.result_file, payload)
    return result_failure_reason(args) == ""


def synthesize(args: argparse.Namespace) -> int:
    ok = synthesize_implementation(args) or synthesize_review(args) or synthesize_complexity(args)
    return 0 if ok else 1


def failure_reason(args: argparse.Namespace) -> int:
    print(result_failure_reason(args))
    return 0


def write_json(args: argparse.Namespace) -> int:
    payload, error = load_json(args.payload_file)
    if error or not isinstance(payload, dict):
        print(f"invalid payload JSON: {error or 'expected-object'}", file=sys.stderr)
        return 1
    target = Path(args.result_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    validated_path = target.with_name(f"{target.name}.validated.{os.getpid()}")
    validation_args = argparse.Namespace(**vars(args))
    validation_args.result_file = str(validated_path)
    validation_args.done_file = ""
    validation_args.log_file = ""
    validation_args.issue_dir = getattr(args, "issue_dir", "")
    validation_args.from_stall = False
    try:
        write_json_atomic(str(validated_path), payload)
        reason = ""
        if args.role in {"impl", "modify"}:
            reason = implementation_result_reason(validation_args, payload)
        elif args.role == "complexity" and not valid_complexity_payload(payload):
            reason = "invalid-complexity-result-artifact"
        elif args.role == "plan" and not valid_plan_payload(payload):
            reason = "invalid-plan-result-artifact"
        elif args.role == "review" and not valid_review_status(payload):
            reason = "invalid-review-status-artifact"
        elif args.role == "review-instructions" and not valid_review_instructions(payload):
            reason = "invalid-review-instructions-artifact"
        if reason:
            print(reason, file=sys.stderr)
            return 1
        os.replace(validated_path, target)
        return 0
    finally:
        try:
            validated_path.unlink()
        except FileNotFoundError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("failure-reason", "failure-class", "synthesize"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--role", required=True)
        sub.add_argument("--issue", required=True)
        sub.add_argument("--result-file", required=True)
        sub.add_argument("--done-file", default="")
        sub.add_argument("--log-file", default="")
        sub.add_argument("--issue-dir", default="")
        sub.add_argument("--repo-root", default="")
        sub.add_argument("--pre-spawn-head", default="")
        sub.add_argument("--from-stall", action="store_true", default=False)
    writer = subparsers.add_parser("write-json")
    writer.add_argument(
        "--role",
        required=True,
        choices=[
            "impl",
            "modify",
            "complexity",
            "plan",
            "review",
            "review-instructions",
            "artifact-recovery",
            "merge-recovery",
        ],
    )
    writer.add_argument("--issue", required=True)
    writer.add_argument("--payload-file", required=True)
    writer.add_argument("--result-file", required=True)
    writer.add_argument("--issue-dir", default="")
    writer.add_argument("--repo-root", default="")
    writer.add_argument("--pre-spawn-head", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "failure-reason":
        return failure_reason(args)
    if args.command == "failure-class":
        return failure_class(args)
    if args.command == "synthesize":
        return synthesize(args)
    if args.command == "write-json":
        return write_json(args)
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
