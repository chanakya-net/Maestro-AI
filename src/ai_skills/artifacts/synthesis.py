"""Artifact synthesis, fallback repair, and validated atomic JSON publication."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import sys
from typing import Any

from ai_skills import verification
from ai_skills.git import (
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
from ai_skills.storage import replace_file_atomic, write_json_atomic
from ai_skills.artifacts.schemas import (
    COMPLEXITY_BAND_ORDER,
    COMPLEXITY_LEVELS,
    COMPLEXITY_SCHEMA_VERSION,
    load_json,
    result_file_is_issue_report,
    role_failure_reason,
    worker_payload_written_to_issue_report,
)
from ai_skills.artifacts.validators import (
    canonical_complexity_retry_result_file,
    canonical_review_retry_status_file,
    complexity_payload_from_log,
    implementation_result_reason,
    nitpick_only,
    repair_protected_nitpicks,
    result_failure_reason,
    terminal_report_reason,
    valid_complexity_payload,
    valid_plan_payload,
    valid_review_instructions,
    valid_review_status,
)

def synthesize_implementation(args: argparse.Namespace) -> bool:
    try:
        config = verification.managed_config(args)
    except (OSError, ValueError, TypeError, KeyError):
        return False
    if config is not None and config.execution_mode == 'verify_existing':
        return False
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
        if not canonical_error and valid_complexity_payload(canonical_payload, allow_legacy=False):
            payload = dict(canonical_payload)
            payload.setdefault("source", "dispatcher-copied-from-canonical-retry")
            write_json_atomic(args.result_file, payload)
            return result_failure_reason(args) == ""

    have_done = bool(args.done_file) and os.path.exists(args.done_file) and os.path.getsize(args.done_file) > 0
    if not have_done and not getattr(args, "provider_exited_cleanly", False):
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
    if args.role in {"impl", "modify"} and payload.get("artifact_kind") != "role_failure":
        try:
            args.worker_config = verification.managed_config(args)
        except (OSError, ValueError, TypeError, KeyError) as error:
            print(str(error), file=sys.stderr)
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
    validation_args.publication_unmanaged = getattr(args, "worker_config", None) is None
    try:
        write_json_atomic(str(validated_path), payload)
        reason = ""
        if payload.get('artifact_kind') == 'role_failure':
            managed = os.environ.get('RUN_WITH_IT_DISPATCHER_MANAGED') == '1'
            reason = role_failure_reason(payload, issue=str(args.issue), role=args.role,
                run_id=os.environ.get('RUN_WITH_IT_RUN_ID','') if managed else payload.get('run_id',''),
                attempt_id=os.environ.get('RUN_WITH_IT_ATTEMPT_ID','') if managed else payload.get('attempt_id',''))
            if managed and (os.environ.get('RUN_WITH_IT_ROLE') != args.role or os.environ.get('RUN_WITH_IT_ISSUE') != str(args.issue)):
                reason = 'role-failure-identity-mismatch'
        elif args.role in {"impl", "modify"}:
            reason = implementation_result_reason(validation_args, payload)
        elif args.role == "complexity" and not valid_complexity_payload(payload, allow_legacy=False):
            reason = "invalid-complexity-result-artifact"
        elif args.role == "plan" and not valid_plan_payload(payload):
            reason = "invalid-plan-result-artifact"
        elif args.role == "review" and not valid_review_status(payload):
            reason = "invalid-review-status-artifact"
        elif args.role == "review-instructions" and not valid_review_instructions(payload):
            reason = "invalid-review-instructions-artifact"
        elif args.role in {"sub-coord", "merge-recovery"}:
            reason = terminal_report_reason(args.role, payload)
        if reason:
            print(reason, file=sys.stderr)
            return 1
        replace_file_atomic(validated_path, target)
        return 0
    finally:
        try:
            validated_path.unlink()
        except FileNotFoundError:
            pass

