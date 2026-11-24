"""Dependency readiness, ownership scope normalization, and concurrency rules."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import posixpath
import re
import sys
import time
from pathlib import Path
from typing import Any


def completed_issue_numbers(state: dict[str, Any]) -> set[int]:
    completed: set[int] = set()
    for key, value in state.get("issue_registry", {}).items():
        if isinstance(value, dict) and value.get("status") == "completed":
            completed.add(int(key))
    return completed


def issue_dependencies_completed(info: dict[str, Any], completed: set[int]) -> bool:
    return all(int(dep) in completed for dep in info.get("deps", []))


def classify_ownership_scope(entry: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Classify an issue's declared ownership scope as one of:
    - ("absent", ())      — key missing or an explicitly empty list; no evidence
    - ("root", ())        — an entry names the repo root (".", "/"); overlaps everything
    - ("malformed", ())   — not a list, non-string entries, or a path alias that
                            could evade overlap detection: absolute paths, drive
                            or UNC paths, and parent-escaping ".." prefixes
    - ("valid", scopes)   — normalized, sorted, repo-relative path prefixes
    Root and malformed must never be conflated with absent: an explicit claim of
    "everything" or unparseable metadata has to fail closed, not open."""
    if "ownership_scope" not in entry or entry.get("ownership_scope") is None:
        return ("absent", ())
    raw = entry.get("ownership_scope")
    if not isinstance(raw, list):
        return ("malformed", ())
    scopes: set[str] = set()
    saw_root = False
    for value in raw:
        if not isinstance(value, str):
            return ("malformed", ())
        scope = value.strip().replace("\\", "/")
        while scope.startswith("./"):
            scope = scope[2:]
        if scope in {"", ".", "/"}:
            saw_root = True
            continue
        normalized = posixpath.normpath(scope)
        # Aliases of paths outside (or re-entering) the repo compare unequal to
        # their repo-relative form and would evade overlap detection.
        if (
            normalized.startswith("/")
            or normalized == ".."
            or normalized.startswith("../")
            or re.match(r"^[A-Za-z]:", normalized)
        ):
            return ("malformed", ())
        scope = normalized.strip("/")
        if not scope or scope == ".":
            saw_root = True
            continue
        scopes.add(scope)
    if saw_root:
        return ("root", ())
    if not scopes:
        return ("absent", ())
    return ("valid", tuple(sorted(scopes)))


def scope_comparison_prefix(scope: str) -> str:
    """Reduce a glob-bearing scope to its literal directory prefix so patterns
    like `src/api/*` compare as `src/api` instead of conflicting with everything."""
    glob_positions = [scope.find(char) for char in "*?[" if scope.find(char) != -1]
    if not glob_positions:
        return scope
    literal = scope[: min(glob_positions)]
    if "/" in literal:
        return literal.rsplit("/", 1)[0]
    return ""


def ownership_scopes_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    for left_scope in left:
        for right_scope in right:
            left_key = scope_comparison_prefix(left_scope.casefold())
            right_key = scope_comparison_prefix(right_scope.casefold())
            if not left_key or not right_key:
                # Glob with no literal directory prefix matches anywhere.
                return True
            if left_key == right_key:
                return True
            if left_key.startswith(f"{right_key}/") or right_key.startswith(f"{left_key}/"):
                return True
    return False


def classify_parallel_safe(entry: dict[str, Any]) -> str:
    """Classify `parallel_safe` as "absent", "true", "false", or "malformed".
    Only canonical boolean forms are accepted; anything else (e.g. "off",
    "garbage", numbers, lists) is malformed and must fail closed. Absent
    defaults to parallel: execution happens in isolated per-issue worktrees
    with a dedicated merge-recovery path, so missing metadata must not
    serialize the whole pool."""
    value = entry.get("parallel_safe")
    if value is None:
        return "absent"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        canonical = value.strip().casefold()
        if canonical in {"true", "1", "yes"}:
            return "true"
        if canonical in {"false", "0", "no"}:
            return "false"
    return "malformed"


def issues_can_run_together(
    left: dict[str, Any], right: dict[str, Any], absent_policy: str = "permissive"
) -> bool:
    """absent_policy governs issues with MISSING concurrency metadata:
    - "permissive" (legacy states without the capability flag): absent metadata
      admits in parallel, relying on worktree isolation plus merge recovery.
      This is explicit fail-open behavior for backward compatibility only.
    - "strict" (required for newly generated plans, which must derive metadata
      for every issue): absent metadata runs exclusively, because worktrees
      only isolate filesystem conflicts — not semantic conflicts, migrations,
      generated files, or shared external resources."""
    left_safe = classify_parallel_safe(left)
    right_safe = classify_parallel_safe(right)
    if "false" in (left_safe, right_safe) or "malformed" in (left_safe, right_safe):
        return False
    left_kind, left_scope = classify_ownership_scope(left)
    right_kind, right_scope = classify_ownership_scope(right)
    if "malformed" in (left_kind, right_kind):
        return False
    if "root" in (left_kind, right_kind):
        # An explicit repo-root scope overlaps everything.
        return False
    if absent_policy == "strict" and (
        "absent" in (left_safe, right_safe)
        or left_kind == "absent"
        or right_kind == "absent"
    ):
        return False
    if left_kind == "absent" or right_kind == "absent":
        return True
    return not ownership_scopes_overlap(left_scope, right_scope)


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def is_dependency_blocking_reason(reason: Any, deps: list[Any]) -> bool:
    """True if a blocking_reason is only about an upstream dependency, so it can
    be cleared once that dependency completes."""
    if not isinstance(reason, str):
        return False
    low = reason.lower()
    if "blocked by" in low or "blocked-by" in low or "blocked_by" in low:
        return True
    for dep in deps:
        if f"#{dep}" in reason or f"issue-{dep}" in reason or f"issue {dep}" in reason:
            return True
    return False


def is_stale_base_dependency_reason(reason: Any, deps: list[Any]) -> bool:
    """True only when a reason names one of this issue's dependencies exactly.

    Automatic stale-base requeue is intentionally stricter than the legacy
    auto-unblock matcher above: generic "blocked by" text and partial issue IDs
    are not evidence that a completed dependency was missing from the base.
    """
    if not isinstance(reason, str):
        return False
    for dep in deps:
        dep_id = re.escape(str(dep))
        if re.search(rf"#{dep_id}(?!\d)", reason):
            return True
        if re.search(rf"\bissue(?:-| ){dep_id}\b", reason, re.IGNORECASE):
            return True
    return False


