"""Read-only evidence for an explicitly admitted preserved-commit retry.

The dispatcher owns contracts and source journals. Reports supply check evidence,
not permission to reuse a commit. This is a workflow contract, not an OS sandbox.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

from ai_skills import git
from ai_skills.outcomes import canonical_path, outcome_path, outcome_reason, role_artifact_paths
from ai_skills.processes import ProcessIdentity, ProcessSupervisor
from ai_skills.storage import load_json


def validate_configuration(config):
    if config.execution_mode not in ('implement', 'verify_existing'):
        raise ValueError('invalid execution_mode')
    if config.execution_mode == 'implement':
        if config.retry_contract is not None:
            raise ValueError('retry_contract requires verify_existing')
        return
    contract = config.retry_contract
    if config.agent_run.role not in ('impl', 'modify') or not config.agent_run.cycle:
        raise ValueError('verify_existing requires impl/modify and an explicit cycle')
    if not isinstance(contract, dict) or set(contract) != {
            'source_state_file', 'source_attempt_id', 'expected_commit_sha', 'required_checks'}:
        raise ValueError('invalid retry_contract')
    for name in ('source_state_file', 'source_attempt_id', 'expected_commit_sha'):
        if not isinstance(contract[name], str) or not contract[name].strip():
            raise ValueError('invalid retry_contract ' + name)
    if not Path(contract['source_state_file']).is_absolute():
        raise ValueError('source_state_file must be absolute')
    for sha in (config.issue_base_sha, contract['expected_commit_sha']):
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise ValueError('verify_existing requires canonical commit SHAs')
    checks = contract['required_checks']
    if (not isinstance(checks, list) or not checks or
            any(not isinstance(check, str) or not check.strip() for check in checks) or
            len(set(checks)) != len(checks)):
        raise ValueError('required_checks must be unique nonempty strings')


def _source(config):
    # Lazy import avoids a dispatch -> artifacts -> verification -> dispatch cycle.
    from ai_skills.dispatch import WorkerConfig, _provider_record
    contract, run = config.retry_contract, config.agent_run
    source_path = Path(contract['source_state_file'])
    source = WorkerConfig.from_record(load_json(Path(str(source_path) + '.config.json')))
    old = source.agent_run
    if (canonical_path(source.state_file) != canonical_path(source_path) or
            canonical_path(source_path) == canonical_path(config.state_file) or
            old.attempt_id != contract['source_attempt_id'] or
            source.execution_mode != 'implement' or
            str(old.issue) != str(run.issue) or old.role != run.role or old.cycle != run.cycle or
            canonical_path(old.repo_root) != canonical_path(run.repo_root) or
            not old.issue_dir or not run.issue_dir or
            canonical_path(old.issue_dir) != canonical_path(run.issue_dir) or
            source.issue_base_sha != config.issue_base_sha):
        raise ValueError('verification-source-mismatch')
    outcome = load_json(outcome_path(source_path))
    reason = outcome_reason(outcome, run_id=old.run_id, attempt_id=old.attempt_id,
        issue=str(old.issue), role=old.role, state_file=source_path,
        expected_artifacts=role_artifact_paths(role=old.role, result_file=old.result_file, issue_dir=old.issue_dir))
    if reason or outcome['status'] not in ('blocked', 'failed', 'interrupted'):
        raise ValueError('verification-source-not-terminal')
    process, supervisor = _provider_record(source)
    if not process.get('identity') or supervisor.inspect(ProcessIdentity(**process['identity'])).state != 'dead':
        raise ValueError('verification-source-ownership-unknown')
    wrapper = load_json(Path(str(source_path) + '.dispatcher.process.json'), missing={})
    if not isinstance(wrapper, dict):
        raise ValueError('verification-source-dispatcher-invalid')
    if not wrapper.get('identity'):
        raw = supervisor.lookup_attempt(old.run_id + '-dispatcher', old.attempt_id)
        if raw is not None:
            wrapper = {**raw, 'record_directory': str(supervisor.directory)}
    if source.detach or wrapper:
        if not wrapper.get('identity'):
            raise ValueError('verification-source-dispatcher-unknown')
        identity = ProcessIdentity(**wrapper['identity'])
        if identity.run_id != old.run_id + '-dispatcher' or identity.attempt_id != old.attempt_id:
            raise ValueError('verification-source-dispatcher-mismatch')
        owner = ProcessSupervisor(Path(wrapper['record_directory'])).inspect(identity)
        if owner.state != 'dead':
            raise ValueError('verification-source-dispatcher-not-dead')
    head = contract['expected_commit_sha']
    progress = outcome.get('git_progress', {})
    if (not isinstance(progress, dict) or progress.get('head') != head or progress.get('pre_spawn_head') != source.pre_spawn_head or
            progress.get('issue_base_sha') != config.issue_base_sha or
            not source.pre_spawn_head or source.pre_spawn_head == head or
            not git.is_ancestor(str(run.repo_root), config.issue_base_sha, source.pre_spawn_head) or
            not git.is_ancestor(str(run.repo_root), source.pre_spawn_head, head)):
        raise ValueError('verification-source-commit-mismatch')
    _protect_source_paths(config, source, supervisor)
    return source


def _attempt_paths(config):
    run, state = config.agent_run, str(config.state_file)
    paths = [state + suffix for suffix in ('', '.config.json', '.outcome.json',
        '.dispatcher.process.json', '.dispatcher.log', '.lock')]
    paths.extend((run.result_file, run.done_file, run.log_file, config.tail_state_file,
        run.process_state_file or state + '.process.json',
        run.stream_directory or state + '.streams'))
    return [Path(path).resolve() for path in paths if path is not None]


def _protect_source_paths(config, source, supervisor):
    from ai_skills.dispatch import _paths_collide
    protected = _attempt_paths(source)
    for run_id in (source.agent_run.run_id, source.agent_run.run_id + '-dispatcher'):
        record = supervisor._path(run_id, source.agent_run.attempt_id)
        protected.extend((record, record.with_suffix('.stdin'), record.with_suffix('.stop')))
    # Global status/events buses and the shared supervisor directory intentionally
    # remain reusable. Their new destinations still cannot alias source records.
    destinations = _attempt_paths(config)
    destinations.extend(Path(path).resolve() for path in
        (config.agent_run.status_file, config.agent_run.events_log) if path is not None)
    for destination in destinations:
        if any(_paths_collide(destination, path) for path in protected):
            raise ValueError('verification-source-path-collision')


def admission_reason(config):
    """Check provenance and starting tree without mutating source attempts."""
    try:
        validate_configuration(config)
        _source(config)
        root = str(config.agent_run.repo_root)
        expected = config.retry_contract['expected_commit_sha']
        if git.current_head(root) != expected or (config.pre_spawn_head and config.pre_spawn_head != expected):
            return 'verification-head-changed'
        if not git.working_tree_clean(root):
            return 'verification-dirty-worktree'
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as error:
        return str(error) or 'verification-source-unavailable'
    return ''


def result_reason(config, payload):
    reason = admission_reason(config)
    if reason:
        return reason
    run, contract = config.agent_run, config.retry_contract
    expected = contract['expected_commit_sha']
    if (not isinstance(payload, dict) or str(payload.get('issue')) != str(run.issue) or
            payload.get('role') != run.role or payload.get('status') != 'success' or
            payload.get('no_op') is True or payload.get('execution_mode', 'verify_existing') != 'verify_existing'):
        return 'invalid-verification-result'
    if payload.get('commit_sha') != expected or config.pre_spawn_head != expected:
        return 'verification-commit-mismatch'
    if not isinstance(payload.get('files_committed'), list) or not payload['files_committed']:
        return 'invalid-verification-result'
    verification = payload.get('verification')
    if not isinstance(verification, dict) or verification.get('passed') is not True:
        return 'implementation-verification-failed'
    if verification.get('commit_sha') != expected:
        return 'verification-evidence-commit-mismatch'
    checks = verification.get('checks')
    if not isinstance(checks, list) or len(checks) != len(contract['required_checks']):
        return 'verification-checks-incomplete'
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            return 'verification-checks-incomplete'
        command = check.get('command')
        if not isinstance(command, str) or command not in contract['required_checks'] or command in seen:
            return 'verification-checks-incomplete'
        if check.get('passed') is not True or not isinstance(check.get('evidence'), str) or not check['evidence'].strip():
            return 'verification-check-not-passed'
        seen.add(command)
    return ''


def managed_config(args):
    """Resolve publication authority from the admitted attempt, never payload flags."""
    supplied = getattr(args, 'worker_config', None)
    if supplied is not None:
        return supplied
    if getattr(args, 'publication_unmanaged', False) or os.environ.get('RUN_WITH_IT_DISPATCHER_MANAGED') != '1':
        return None
    from ai_skills.dispatch import WorkerConfig
    state = os.environ.get('RUN_WITH_IT_STATE_FILE', '')
    if not state:
        raise ValueError('missing-managed-worker-state')
    config = WorkerConfig.from_record(load_json(Path(state + '.config.json')))
    run = config.agent_run
    # Recovery workers publishing a separate recovered role artifact retain their
    # existing contract. An impl/modify worker cannot change its own binding.
    if run.role not in ('impl', 'modify'):
        return None
    if (run.run_id != os.environ.get('RUN_WITH_IT_RUN_ID') or
            run.attempt_id != os.environ.get('RUN_WITH_IT_ATTEMPT_ID') or
            run.role != args.role or str(run.issue) != str(args.issue) or
            canonical_path(config.state_file) != canonical_path(state) or
            canonical_path(run.result_file) != canonical_path(args.result_file) or
            canonical_path(run.repo_root) != canonical_path(args.repo_root)):
        raise ValueError('managed-artifact-binding-mismatch')
    return config


def git_progress(config):
    """Capture the commit produced by a terminated attempt once, in its journal."""
    if config.agent_run.role not in ('impl', 'modify') or not config.issue_base_sha:
        return None
    try:
        return {'head': git.current_head(str(config.agent_run.repo_root)),
            'pre_spawn_head': config.pre_spawn_head, 'issue_base_sha': config.issue_base_sha}
    except (OSError, subprocess.SubprocessError):
        return None


def handoff_fingerprint(config):
    """Bind a terminal proof to its immutable contract and exact report bytes.

    Later review/modification may advance the worktree. Replaying a journaled
    decision must not reinterpret those later changes as this attempt's work.
    """
    import hashlib
    run = config.agent_run
    try:
        report_hash = hashlib.sha256(Path(run.result_file).read_bytes()).hexdigest()
    except FileNotFoundError:
        report_hash = None
    except IsADirectoryError:
        report_hash = 'directory'
    return {'retry_contract': config.retry_contract, 'issue_base_sha': config.issue_base_sha,
        'pre_spawn_head': config.pre_spawn_head, 'repo_root': canonical_path(run.repo_root),
        'issue_dir': canonical_path(run.issue_dir), 'cycle': run.cycle,
        'report_sha256': report_hash}
