"""Runtime-owned attempt outcomes and canonical path binding.

This module deliberately knows nothing about dispatch or runner configuration.
Callers supply the authoritative identity and artifact paths.
"""
from __future__ import annotations

import math
from pathlib import Path
import time


OUTCOME_STATUSES = frozenset({'pending', 'success', 'failed', 'blocked', 'interrupted'})


def canonical_path(path: str | Path) -> str:
    return str(Path(path).resolve())


def outcome_path(state_file: str | Path) -> Path:
    return Path(str(Path(state_file)) + '.outcome.json')


def pending_outcome(*, run_id: str, attempt_id: str, issue: str, role: str,
                    state_file: str | Path, expected_artifacts: dict[str, str],
                    started_at: float | None = None) -> dict:
    return {'schema_version': 1, 'run_id': run_id, 'attempt_id': attempt_id,
            'issue': str(issue), 'role': role, 'state_file': canonical_path(state_file),
            'expected_artifacts': {key: canonical_path(path) for key, path in expected_artifacts.items()},
            'status': 'pending', 'started_at': time.time() if started_at is None else started_at,
            'finished_at': None, 'provider_return_code': None, 'worker_return_code': None,
            'reason': '', 'failure_class': ''}


def _timestamp(value: object) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def outcome_reason(payload: object, *, run_id: str, attempt_id: str, issue: str, role: str,
                   state_file: str | Path, expected_artifacts: dict[str, str]) -> str:
    """Empty means structurally valid and bound, never proof of work or death."""
    if not isinstance(payload, dict) or type(payload.get('schema_version')) is not int or payload['schema_version'] != 1:
        return 'invalid-outcome-schema'
    if not {'finished_at', 'provider_return_code', 'worker_return_code'}.issubset(payload):
        return 'invalid-outcome-schema'
    binding = {'run_id': run_id, 'attempt_id': attempt_id, 'issue': str(issue), 'role': role,
               'state_file': canonical_path(state_file),
               'expected_artifacts': {key: canonical_path(path) for key, path in expected_artifacts.items()}}
    if any(not isinstance(value, str) or not value.strip() for value in (run_id, attempt_id, str(issue), role)):
        return 'invalid-outcome-identity'
    if any(payload.get(key) != value for key, value in binding.items()):
        return 'outcome-binding-mismatch'
    status = payload.get('status')
    if not isinstance(status, str) or status not in OUTCOME_STATUSES:
        return 'invalid-outcome-status'
    if not _timestamp(payload.get('started_at')):
        return 'invalid-outcome-started-at'
    if not isinstance(payload.get('reason'), str) or not isinstance(payload.get('failure_class'), str):
        return 'invalid-outcome-reason'
    if 'issue_outcome' in payload and (not isinstance(payload['issue_outcome'], str) or not payload['issue_outcome'].strip()):
        return 'invalid-outcome-issue-outcome'
    if status == 'pending':
        if any(payload.get(key) is not None for key in ('finished_at', 'provider_return_code', 'worker_return_code')):
            return 'invalid-pending-outcome'
    else:
        if not _timestamp(payload.get('finished_at')) or payload['finished_at'] < payload['started_at']:
            return 'invalid-outcome-finished-at'
        if type(payload.get('worker_return_code')) is not int:
            return 'invalid-outcome-worker-return-code'
        if (status == 'success') != (payload['worker_return_code'] == 0):
            return 'outcome-status-return-code-mismatch'
        if payload.get('provider_return_code') is not None and type(payload['provider_return_code']) is not int:
            return 'invalid-outcome-provider-return-code'
        if status != 'success' and not payload['reason'].strip():
            return 'missing-outcome-failure-reason'
    return ''


def review_instructions_file(result_file: str) -> str:
    if result_file.endswith('-status.json'):
        return f"{result_file[:-len('-status.json')]}-instructions.json"
    return ''


def role_artifact_paths(*, role: str, result_file: str | Path | None,
                        issue_dir: str | Path | None = None) -> dict[str, str]:
    """Derive the same authoritative role paths for dispatch and prompt injection."""
    if not result_file:
        raise ValueError('result_file is required')
    paths = {'result': canonical_path(result_file)}
    if role == 'review':
        instructions = review_instructions_file(str(result_file))
        if not instructions:
            raise ValueError('review result_file must end with -status.json')
        paths['review_instructions'] = canonical_path(instructions)
    if role == 'plan':
        if not issue_dir:
            raise ValueError('issue_dir is required for plan workers')
        paths['plan'] = canonical_path(Path(issue_dir) / 'plan.md')
    return paths
