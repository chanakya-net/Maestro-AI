"""Pure and Git-backed validators for role artifacts."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from ai_skills import verification
from ai_skills.git import (
    abort_cherry_pick,
    commit_exists,
    commit_salvaged_tree,
    current_head,
    git_output,
    git_success,
    is_ancestor,
    repo_available,
    resolve_commit,
    working_tree_clean,
)
from ai_skills.outcomes import review_instructions_file
from ai_skills.registry import LEGACY_BAND_ALIASES, canonical_band
from ai_skills.storage import replace_file_atomic, write_json_atomic
from ai_skills.artifacts.schemas import (
    ACCEPTED_COMPLEXITY_LEVELS,
    COMPLEXITY_BAND_ORDER,
    COMPLEXITY_LEVELS,
    COMPLEXITY_PEAK_FLOORS,
    COMPLEXITY_SCHEMA_VERSION,
    COMPLEXITY_SCORE_BANDS,
    COMPLEXITY_SCORE_KEYS,
    LEGACY_COMPLEXITY_PEAK_FLOORS,
    LEGACY_COMPLEXITY_SCORE_BANDS,
    LEGACY_COMPLEXITY_SCORE_KEYS,
    NON_NITPICK_CATEGORIES,
    PLAN_REQUIRED_KEYS,
    REVIEW_CATEGORIES,
    REVIEW_SEVERITIES,
    REVIEW_VERDICTS,
    is_issue_report_path,
    is_nitpick_comment,
    issue_report_path,
    load_json,
    non_empty_str,
    result_file_is_issue_report,
    review_comment_closure_reason,
    role_failure_reason,
    worker_payload_written_to_issue_report,
)

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
    try:
        config = verification.managed_config(args)
    except (OSError, ValueError, TypeError, KeyError) as error:
        return str(error)
    if config is not None and config.execution_mode == 'verify_existing':
        return verification.result_reason(config, payload)
    available = bool(args.repo_root) and repo_available(args.repo_root)
    head = None
    if available:
        try:
            head = current_head(args.repo_root)
        except Exception:
            pass
    commit = resolve_commit(args.repo_root, payload.get("commit_sha", "")) if available and isinstance(payload, dict) and isinstance(payload.get("commit_sha"), str) else None
    evidence = GitEvidence(issue=str(args.issue), repo_available=available, head=head,
        pre_spawn_head=args.pre_spawn_head, canonical_commit=commit,
        canonical_pre_spawn_head=resolve_commit(args.repo_root, args.pre_spawn_head) if available and args.pre_spawn_head else None)
    decision = validate_artifact(args.role, payload, evidence)
    # Canonicalization and deliberate cross-worktree recovery are effects, kept
    # outside the pure validator. Never salvage a result that failed verification.
    if commit and isinstance(payload, dict) and payload.get("no_op") is not True and decision.reason in {"", "commit-outside-issue-worktree", "missing-implementation-commit"}:
        if commit != payload.get("commit_sha"):
            payload = dict(payload, commit_sha=commit)
            write_json_atomic(args.result_file, payload)
    if decision.reason == "commit-outside-issue-worktree" and recover_wrong_worktree_commit(args, payload, head, commit):
        return ""
    return decision.reason


def expected_complexity_level(
    scores: dict[str, int],
    *,
    schema_version: int = COMPLEXITY_SCHEMA_VERSION,
) -> str:
    """Compute the canonical complexity band from integer dimension scores (1-5).

    Schema v2 (7 dimensions, sum 7-35): applies the 7-35 sum thresholds and peak
    floor (any dimension scored 4 floors at `medium`; any dimension scored 5
    floors at `complex`).
    Legacy schema v1 (9 dimensions, sum 9-45): applies the legacy 9-45 sum
    thresholds and legacy peak-dimension floor (any 4 -> `medium-hard`, any 5 -> `complex`).
    """
    total = sum(scores.values())
    bands = LEGACY_COMPLEXITY_SCORE_BANDS if schema_version == 1 else COMPLEXITY_SCORE_BANDS
    base = "extreme"
    for score_min, score_max, label in bands:
        if score_min <= total <= score_max:
            base = label
            break
    peak = max(scores.values())
    floors = LEGACY_COMPLEXITY_PEAK_FLOORS if schema_version == 1 else COMPLEXITY_PEAK_FLOORS
    floor = floors.get(peak)
    if floor and COMPLEXITY_BAND_ORDER.index(floor) > COMPLEXITY_BAND_ORDER.index(base):
        return floor
    return base


def valid_complexity_payload(payload: Any, *, allow_legacy: bool = True) -> bool:
    if not isinstance(payload, dict):
        return False
    if "schema_version" in payload:
        schema_version = payload.get("schema_version")
        if type(schema_version) is not int:
            return False
        if schema_version == COMPLEXITY_SCHEMA_VERSION:
            expected_keys = COMPLEXITY_SCORE_KEYS
        elif schema_version == 1 and allow_legacy:
            expected_keys = LEGACY_COMPLEXITY_SCORE_KEYS
        else:
            return False
    elif allow_legacy:
        schema_version = 1
        expected_keys = LEGACY_COMPLEXITY_SCORE_KEYS
    else:
        return False

    total = payload.get("total")
    if type(total) is not int:
        return False
    level = payload.get("level")
    if not isinstance(level, str) or level not in ACCEPTED_COMPLEXITY_LEVELS:
        return False
    scores = payload.get("scores")
    rationale = payload.get("rationale")
    if not isinstance(scores, dict) or set(scores) != expected_keys:
        return False
    if not isinstance(rationale, dict) or set(rationale) != expected_keys:
        return False
    if not all(type(scores[key]) is int and 1 <= scores[key] <= 5 for key in expected_keys):
        return False
    if total != sum(scores.values()):
        return False
    if canonical_band(level) != expected_complexity_level(scores, schema_version=schema_version):
        return False
    if not all(non_empty_str(rationale[key]) for key in expected_keys):
        return False
    files_inspected = payload.get("files_inspected")
    if not isinstance(files_inspected, list) or not all(non_empty_str(item) for item in files_inspected):
        return False
    return True


def valid_plan_payload(payload: Any) -> bool:
    """Structural validity for plan.json. complexity_level must be a router band,
    complexity_rationale must be a non-empty string explaining the grounded
    band, and slices must be a non-empty ordered list; the plan never commits,
    so no commit_sha is required. Issue-number matching is checked by the caller."""
    if not isinstance(payload, dict):
        return False
    if not PLAN_REQUIRED_KEYS.issubset(payload):
        return False
    if payload.get("role") != "plan":
        return False
    if type(payload.get("schema_version")) is not int:
        return False
    # A self-reported failure is not a usable plan. Match the impl/modify
    # convention (status must be exactly "success") so a failed planner cannot
    # refine routing or be treated as a completed plan.
    if payload.get("status") != "success":
        return False
    approach = payload.get("approach")
    if not isinstance(approach, str) or not approach.strip():
        return False
    if payload.get("complexity_level") not in ACCEPTED_COMPLEXITY_LEVELS:
        return False
    if not non_empty_str(payload.get("complexity_rationale")):
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
        if valid_complexity_payload(payload, allow_legacy=False):
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
    instructions_file = review_instructions_file(args.result_file)
    instructions, error = load_json(instructions_file)
    return review_payload_reason(status_payload, status_error, instructions, error)


def review_payload_reason(status_payload: Any, status_error: str | None, instructions_payload: Any, instructions_error: str | None) -> str:
    if status_error == "missing":
        return "missing-result-artifact"
    if status_error or not valid_review_status(status_payload):
        return "invalid-review-status-artifact"
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
        return validate_artifact(args.role, payload, GitEvidence(issue=str(args.issue))).reason
    if args.role == "plan":
        issue_dir = getattr(args, "issue_dir", "") or ""
        plan_path = Path(issue_dir) / "plan.md" if issue_dir else None
        present = plan_path is None or (plan_path.is_file() and plan_path.stat().st_size > 0)
        return validate_artifact(args.role, payload, GitEvidence(issue=str(args.issue), plan_file_present=present)).reason
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
# Only `agent-unavailable` is used. It is emitted by the native provider runner for the
# current runner, and every post-availability retry uses a fresh
# attempt-specific log, so it cannot be inherited. `dispatch-bootstrap-failed`
# is deliberately NOT a marker: a bootstrap loss happens before any runner_pid
# and exits via a path that never calls this classifier, so the only way the
# marker reaches a log scanned here is the foreground bootstrap retry reusing
# the same log (see sub-coordinator-prompt.md) — where it is always stale and


@dataclass(frozen=True)
class GitEvidence:
    issue: str = ""
    repo_available: bool = False
    head: str | None = None
    pre_spawn_head: str = ""
    canonical_commit: str | None = None
    canonical_pre_spawn_head: str | None = None
    result_is_report: bool = False
    plan_file_present: bool = True
    review_instructions: object = None

@dataclass(frozen=True)
class ArtifactDecision:
    valid: bool
    reason: str = ""
    canonical_commit: str | None = None


def terminal_report_reason(role: str, payload: object) -> str:
    """A negative issue outcome can still be a complete coordinator handoff."""
    outcomes = ({'completed', 'blocked', 'failed-merge'} if role == 'merge-recovery'
                else {'completed', 'blocked', 'failed-review', 'merge_failed', 'failed-merge'})
    if not isinstance(payload, dict):
        return 'invalid-terminal-report'
    outcome = payload.get('outcome')
    if not isinstance(outcome, str) or outcome not in outcomes:
        return 'invalid-terminal-report-outcome'
    if not isinstance(payload.get('summary'), str) or not payload['summary'].strip():
        return 'missing-terminal-report-summary'
    reasons = payload.get('blocking_reasons', [])
    if not isinstance(reasons, list) or any(not isinstance(item, str) or not item.strip() for item in reasons):
        return 'invalid-terminal-report-blocking-reasons'
    if outcome == 'blocked' and not reasons:
        return 'missing-terminal-report-blocking-reasons'
    return ''


def validate_artifact(role: str, payload: object, evidence: GitEvidence) -> ArtifactDecision:
    """Validate captured evidence; this function never edits Git or artifact files."""
    reason = ""
    if isinstance(payload, dict) and payload.get('artifact_kind') == 'role_failure':
        reason = 'role-failure-report'
    elif evidence.result_is_report and role in {"impl", "modify"}:
        reason = "worker-result-path-is-sub-coordinator-report"
    elif role in {"impl", "modify"}:
        if not isinstance(payload, dict) or str(payload.get("issue")) != str(evidence.issue) or payload.get("role") != role:
            reason = "invalid-result-artifact"
        elif payload.get("execution_mode") == "verify_existing":
            reason = "verification-contract-required"
        elif payload.get("status") == "artifact_recovery_required":
            reason = "artifact-recovery-required"
        elif payload.get("status") != "success":
            reason = "invalid-result-artifact"
        elif payload.get("no_op") is True:
            if not isinstance(payload.get("verification"), dict) or payload["verification"].get("passed") is not True:
                reason = "verified-no-op-requires-passing-verification"
            elif evidence.repo_available and not evidence.head:
                reason = "implementation-repo-unavailable"
            elif evidence.repo_available and evidence.pre_spawn_head and evidence.head != evidence.pre_spawn_head:
                reason = "verified-no-op-with-unexpected-commit"
        elif not isinstance(payload.get("commit_sha"), str) or payload["commit_sha"] in {"", "NONE"} or not isinstance(payload.get("files_committed"), list) or not payload["files_committed"] or not isinstance(payload.get("verification"), dict):
            reason = "invalid-result-artifact"
        elif payload["verification"].get("passed") is not True:
            reason = "implementation-verification-failed"
        elif not evidence.repo_available or not evidence.head:
            reason = "implementation-repo-unavailable"
        elif not evidence.canonical_commit:
            reason = "invalid-implementation-commit"
        elif evidence.head != evidence.canonical_commit:
            reason = "commit-outside-issue-worktree"
        elif evidence.pre_spawn_head and evidence.canonical_commit == evidence.canonical_pre_spawn_head:
            reason = "missing-implementation-commit"
    elif role == "complexity":
        if not valid_complexity_payload(payload): reason = "invalid-complexity-result-artifact"
    elif role == "plan":
        if not valid_plan_payload(payload) or str(payload.get("issue")) != str(evidence.issue): reason = "invalid-plan-result-artifact"
        elif not evidence.plan_file_present: reason = "missing-plan-file-artifact"
    elif role == "review":
        reason = review_payload_reason(payload, None, evidence.review_instructions,
            "missing" if evidence.review_instructions is None else None)
    elif role in {"sub-coord", "merge-recovery"}:
        reason = terminal_report_reason(role, payload)
    elif not isinstance(payload, dict):
        reason = "invalid-result-artifact"
    return ArtifactDecision(not reason, reason, evidence.canonical_commit)

