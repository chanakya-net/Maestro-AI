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


def current_head(repo_root: str) -> str:
    return git_output(repo_root, "rev-parse", "HEAD")


def repo_available(repo_root: str) -> bool:
    try:
        return git_output(repo_root, "rev-parse", "--is-inside-work-tree") == "true"
    except Exception:
        return False


def implementation_result_reason(args: argparse.Namespace, payload: Any) -> str:
    if not isinstance(payload, dict):
        return "invalid-result-artifact"
    if str(payload.get("issue")) != str(args.issue):
        return "invalid-result-artifact"
    if payload.get("role") != args.role:
        return "invalid-result-artifact"
    if payload.get("status") != "success":
        return "invalid-result-artifact"

    commit_sha = payload.get("commit_sha")
    if not isinstance(commit_sha, str) or not commit_sha or commit_sha == "NONE":
        return "invalid-result-artifact"
    files_committed = payload.get("files_committed")
    if not isinstance(files_committed, list) or not files_committed:
        return "invalid-result-artifact"
    if not isinstance(payload.get("verification"), dict):
        return "invalid-result-artifact"

    if not args.repo_root or not repo_available(args.repo_root):
        return "implementation-repo-unavailable"
    try:
        head = current_head(args.repo_root)
    except Exception:
        return "implementation-repo-unavailable"
    if head != commit_sha:
        return "commit-outside-issue-worktree"
    if args.pre_spawn_head and commit_sha == args.pre_spawn_head:
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


def valid_review_instructions(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("verdict") in REVIEW_VERDICTS
        and isinstance(payload.get("summary"), str)
        and isinstance(payload.get("comments"), list)
        and isinstance(payload.get("blocking_reasons"), list)
    )


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
        if comment.get("severity") != "info":
            return False
        fix = comment.get("fix")
        if not isinstance(fix, str) or not fix.startswith("[nitpick]"):
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
    if args.role == "review":
        return review_result_reason(args, payload, error)
    if error or not isinstance(payload, dict):
        return "invalid-result-artifact"
    return ""


def synthesize_implementation(args: argparse.Namespace) -> bool:
    if args.role not in {"impl", "modify"}:
        return False
    if result_file_is_issue_report(args):
        return False
    if os.path.exists(args.result_file) and os.path.getsize(args.result_file) > 0:
        return False
    if worker_payload_written_to_issue_report(args):
        return False
    if not args.done_file or not os.path.exists(args.done_file) or os.path.getsize(args.done_file) == 0:
        return False
    if not args.repo_root or not repo_available(args.repo_root):
        return False
    try:
        head = current_head(args.repo_root)
    except Exception:
        return False
    if not head or not args.pre_spawn_head or head == args.pre_spawn_head:
        return False
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
            "status": "success",
            "commit_sha": head,
            "files_committed": files,
            "verification": {
                "passed": False,
                "commands": [],
                "source": "dispatcher-synthesized",
                "note": "Worker exited successfully and advanced HEAD but did not write RUN_WITH_IT_RESULT_FILE; verification evidence was not machine-readable.",
            },
            "source": "dispatcher-synthesized",
        },
    )
    return result_failure_reason(args) == ""


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

    if (
        valid_review_status(status_payload)
        and status_payload.get("verdict") == "approve"
        and instructions_file
        and (instructions_error or not valid_review_instructions(instructions_payload))
    ):
        write_json_atomic(
            instructions_file,
            {
                "verdict": "approve",
                "summary": "Dispatcher synthesized approve instructions because the reviewer wrote a valid approve status artifact but omitted REVIEWER_INSTRUCTIONS_FILE.",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("failure-reason", "synthesize"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--role", required=True)
        sub.add_argument("--issue", required=True)
        sub.add_argument("--result-file", required=True)
        sub.add_argument("--done-file", default="")
        sub.add_argument("--log-file", default="")
        sub.add_argument("--issue-dir", default="")
        sub.add_argument("--repo-root", default="")
        sub.add_argument("--pre-spawn-head", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "failure-reason":
        return failure_reason(args)
    if args.command == "synthesize":
        return synthesize(args)
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
