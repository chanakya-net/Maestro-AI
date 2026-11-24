"""Artifact schema constants, JSON loading, and shared failure helpers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from ai_skills.registry import LEGACY_BAND_ALIASES, canonical_band
from ai_skills.storage import replace_file_atomic, write_json_atomic

COMPLEXITY_BAND_ORDER = (
    "quite-easy",
    "easy",
    "medium",
    "medium-hard",
    "complex",
    "extreme",
)

COMPLEXITY_LEVELS = set(COMPLEXITY_BAND_ORDER)

# Saved artifacts from earlier runs may still use the former band name.
ACCEPTED_COMPLEXITY_LEVELS = COMPLEXITY_LEVELS | set(LEGACY_BAND_ALIASES)

COMPLEXITY_SCHEMA_VERSION = 2

COMPLEXITY_SCORE_BANDS = (
    (7, 9, "quite-easy"),
    (10, 14, "easy"),
    (15, 19, "medium"),
    (20, 23, "medium-hard"),
    (24, 28, "complex"),
    (29, 35, "extreme"),
)

COMPLEXITY_SCORE_KEYS = {
    "coupling_and_blast_radius",
    "pattern_novelty",
    "state_and_concurrency_risk",
    "data_and_rollback_risk",
    "verification_risk",
    "ambiguity_of_requirements",
    "scope_size",
}

COMPLEXITY_PEAK_FLOORS = {
    4: "medium",
    5: "complex",
}

LEGACY_COMPLEXITY_SCORE_BANDS = (
    (9, 12, "quite-easy"),
    (13, 17, "easy"),
    (18, 22, "medium"),
    (23, 27, "medium-hard"),
    (28, 32, "complex"),
    (33, 45, "extreme"),
)

LEGACY_COMPLEXITY_PEAK_FLOORS = {
    4: "medium-hard",
    5: "complex",
}

LEGACY_COMPLEXITY_SCORE_KEYS = {
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
    "complexity_rationale",
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



def role_failure_reason(payload: object, *, issue: str, role: str, run_id: str, attempt_id: str) -> str:
    """Validate a failure report, independently of substantive success proof."""
    if not isinstance(payload, dict) or payload.get('artifact_kind') != 'role_failure':
        return 'invalid-role-failure-artifact'
    if type(payload.get('schema_version')) is not int or payload['schema_version'] != 1:
        return 'invalid-role-failure-schema'
    for key, expected in (('run_id', run_id), ('attempt_id', attempt_id), ('role', role)):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip() or value != expected:
            return 'role-failure-identity-mismatch'
    if str(payload.get('issue')) != str(issue):
        return 'role-failure-identity-mismatch'
    if payload.get('status') not in ('failed', 'blocked', 'interrupted'):
        return 'invalid-role-failure-status'
    if any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in ('reason', 'summary')):
        return 'invalid-role-failure-reason'
    if 'verification' in payload:
        if not isinstance(payload['verification'], dict) or payload['verification'].get('passed') is True:
            return 'invalid-role-failure-verification'
    if 'evidence' in payload and (not isinstance(payload['evidence'], list) or any(not isinstance(item, str) for item in payload['evidence'])):
        return 'invalid-role-failure-evidence'
    return ''


CLOSURE_SUCCESS_STATUSES = ('addressed', 'declined')


def _string_list(value: object, *, nonempty: bool) -> bool:
    return (isinstance(value, list) and (bool(value) or not nonempty) and
            all(isinstance(item, str) and (item.strip() or not nonempty) for item in value))


def _closure_entry_reason(entry: object) -> str:
    if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
        return 'invalid-review-comment-closure'
    if entry.get('status') not in CLOSURE_SUCCESS_STATUSES:
        return 'review-comment-unresolved'
    if not isinstance(entry.get('action'), str) or not entry['action'].strip():
        return 'review-comment-closure-action-missing'
    if not _string_list(entry.get('files_changed'), nonempty=False):
        return 'invalid-review-comment-closure'
    if not _string_list(entry.get('verification'), nonempty=True):
        return 'review-comment-closure-evidence-missing'
    return ''


def review_comment_closure_reason(payload: object, expected_ids: list[str]) -> str:
    """Require exactly one evidenced success closure per expected comment ID."""
    closure = payload.get('review_comment_closure') if isinstance(payload, dict) else None
    if not isinstance(closure, list):
        return 'invalid-review-comment-closure'
    expected, seen = set(expected_ids), set()
    for entry in closure:
        reason = _closure_entry_reason(entry)
        if reason:
            return reason
        if entry['id'] not in expected:
            return 'review-comment-closure-unexpected-id'
        if entry['id'] in seen:
            return 'review-comment-closure-duplicate-id'
        seen.add(entry['id'])
    return '' if seen == expected else 'review-comment-closure-incomplete'


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



def non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_nitpick_comment(comment: dict[str, Any]) -> bool:
    return comment.get("severity") == "info" and isinstance(comment.get("fix"), str) and comment["fix"].startswith("[nitpick]")




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

