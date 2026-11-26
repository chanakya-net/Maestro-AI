"""Worker dispatch, execution loop, capture binding, and attempt finalization."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import socket

import psutil
import sys
import threading
import tempfile
import time
from types import SimpleNamespace
import uuid

from ai_skills import artifacts, git, verification
from ai_skills.outcomes import role_artifact_paths
from ai_skills.events import EventBus, normalize
from ai_skills.processes import ProcessIdentity, ProcessSpec, ProcessSupervisor, StopResult
from ai_skills.provider_capture import AGY, CODEX, ProviderError, agy_narrative_response, extract_response
from ai_skills.runner import (
    AgentRunConfig, AgentRunResult, _agent, _classify_agy, agy_plan_invocation, build_invocation,
    classify_unavailable, effective_config, capture_candidate, capture_source, load_registry,
    managed_agy_plan, require_agy_plan_profile, run_agent, supports_final_response_capture,
)
from ai_skills.storage import DirectoryLock, load_json, write_json_atomic, replace_file_atomic
from ai_skills.outcomes import canonical_path, outcome_path, pending_outcome, outcome_reason

DEFAULT_QUIET_SECONDS = 900
DEFAULT_STALL_SECONDS = 1200


from ai_skills.dispatch.models import (
    WorkerConfig, WorkerResult, WorkerSnapshot,
    _artifact_args, _optional, _paths_collide, _preflight_paths,
    _process_record, expected_artifact_paths,
)
from ai_skills.dispatch.capture import (
    _artifact_reason, inspect_worker,
    _agy_adoption_diagnostic, _availability_reason, _bound_stop_intent, _capture_bytes,
    _capture_rejected, _check_capture_file, _launcher_state, _plan_handoff_change,
    _plan_handoff_fingerprint, _plan_paths, _protected_artifact_bytes,
    _protected_plan_capture, _provider_record, _publish_captured_result,
    _publish_protected_plan, _read_bound_outcome, _read_tail_text, _record_stop_intent,
    _resolved_route, _safe_synthesis, _unknown_attempt, write_text_atomic, _availability_until,
)

_STOP_REASONS = frozenset({'interrupted', 'hard-limit-exceeded', 'dispatch-timeout', 'dispatch-bootstrap-failed',
                           'shutdown-identity-unknown'})


def _with_attempt_deadline(run, hard_limit_seconds):
    """Give a bounded attempt its wall-clock deadline so it can publish before the kill.

    The hard-limit stop has no useful grace period for an LLM, so the only way a
    worker keeps its result is to publish before the deadline (artifact protocol).
    Only the launched copy carries the variables; persisted configs are unchanged.
    Protected read-only AGY plan launches are skipped: their admitted invocation,
    environment included, must match exactly.
    """
    if not hard_limit_seconds or hard_limit_seconds <= 0 or _protected_plan_capture(run):
        return run
    deadline = time.time() + hard_limit_seconds
    environment = dict(os.environ if run.environment is None else run.environment)
    environment['RUN_WITH_IT_ATTEMPT_DEADLINE_EPOCH'] = str(int(deadline))
    environment['RUN_WITH_IT_ATTEMPT_DEADLINE'] = datetime.fromtimestamp(deadline, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    environment['RUN_WITH_IT_ATTEMPT_HARD_LIMIT_SECONDS'] = str(int(hard_limit_seconds))
    return run.replace(environment=environment)


def _stop_provider(config, grace=.1):
    record = _process_record(config)
    if not record.get('identity'):
        return StopResult('unknown', reason='provider identity has not been recorded')
    supervisor = ProcessSupervisor(Path(record['record_directory']))
    return supervisor.stop(ProcessIdentity(**record['identity']), grace_seconds=grace)


def _work(config):
    run = config.agent_run
    bus = EventBus(status_file=run.status_file, events_log=run.events_log, log_file=run.log_file)
    common = {'issue':run.issue,'role':run.role,'agent':run.agent,'model':run.model,**({'cycle':run.cycle} if run.cycle else {})}
    base = _optional(config.state_file)
    base.update(schema_version=1, state='starting', started_at=time.time(), dispatcher_pid=os.getpid())
    write_json_atomic(Path(config.state_file), base)
    bus.emit('dispatch-ready', common)
    results, errors = [], []
    launch = _with_attempt_deadline(run, config.hard_limit_seconds)
    def invoke():
        try:
            results.append(run_agent(launch))
        except BaseException as error:
            errors.append(str(error))
    thread = threading.Thread(target=invoke, name='owned-agent-runner', daemon=True)
    thread.start()
    started = time.monotonic()
    timeout_reason = ''
    provider_identity = None
    last_output = started
    stream_sizes = {}
    try:
        while thread.is_alive():
            record = _process_record(config)
            if record.get('identity') and provider_identity is None:
                provider_identity = record['identity']
                base.update(state='running',provider_identity=provider_identity)
                last_output = time.monotonic()
                write_json_atomic(Path(config.state_file),base)
            if provider_identity:
                # Wrapper heartbeats share the user log, so measure silence
                # against the provider's raw streams instead of log mtime.
                for key in ('stdout_file', 'stderr_file'):
                    if record.get(key):
                        path = Path(record[key])
                        try:
                            size = path.stat().st_size
                        except OSError:
                            continue
                        if size != stream_sizes.get(key, 0):
                            last_output = time.monotonic()
                        stream_sizes[key] = size
                silence = time.monotonic() - last_output
                observed = 'stalled' if silence >= config.stall_seconds else 'quiet' if silence >= config.quiet_seconds else 'running'
                if observed != base['state']:
                    stall_reason = 'alive-but-silent' if observed == 'stalled' else 'alive-but-quiet' if observed == 'quiet' else ''
                    base.update(state=observed, stall_reason=stall_reason, seconds_since_last_output=silence)
                    write_json_atomic(Path(config.state_file), base)
                    if observed in ('quiet','stalled'):
                        bus.emit('worker-' + observed, {**common, 'reason':stall_reason, 'silence_seconds':silence,
                                                       'state_file':str(config.state_file)})
            elapsed = time.monotonic()-started
            limits = [(config.hard_limit_seconds,'hard-limit-exceeded'), (config.timeout_seconds,'dispatch-timeout')]
            timeout_reason = next((reason for limit,reason in limits if limit and elapsed>=limit), '')
            if timeout_reason and provider_identity:
                _record_stop_intent(config, timeout_reason)
                stop = _stop_provider(config)
                if stop.state != 'dead':
                    timeout_reason = 'shutdown-identity-unknown'
                break
            # Admission must not wait for the steady-state observation cadence.
            time.sleep(config.poll_seconds if provider_identity else min(.03, config.poll_seconds))
    except KeyboardInterrupt:
        timeout_reason = 'interrupted'
        _record_stop_intent(config, timeout_reason)
        _stop_provider(config)
    thread.join(timeout=5)
    if thread.is_alive() or timeout_reason == 'shutdown-identity-unknown':
        return _unknown_attempt(config, 'shutdown-identity-unknown')
    process, supervisor = _provider_record(config)
    if process.get('identity'):
        identity = ProcessIdentity(**process['identity'])
        if supervisor.inspect(identity).state != 'dead':
            return _unknown_attempt(config, 'shutdown-identity-unknown', process['identity'])
        code = supervisor.return_code(identity)
        if code is None:
            deadline = time.monotonic() + 1.5
            while code is None and time.monotonic() < deadline:
                time.sleep(.02)
                code = supervisor.return_code(identity)
        if code is None:
            return _unknown_attempt(config, 'provider-exit-status-unknown', process['identity'])
        prelaunch = False
    else:
        # A finished runner and absence of its exclusive start record prove no
        # provider was launched. Incomplete handshake claims are never absent.
        if process:
            return _unknown_attempt(config, 'provider-launch-identity-unknown')
        code, prelaunch = None, True
    result = results[0] if results else AgentRunResult(1, run.agent, run.model)
    reason = (result.unavailable_reason or 'dispatch-bootstrap-failed') if prelaunch else timeout_reason or result.unavailable_reason or (errors[0] if errors else '')
    return _finalize_attempt(config, provider_return_code=code, reason=reason,
        failure_class='infrastructure' if result.unavailable_reason else 'capability',
        prelaunch_failure=prelaunch, runner_failed=result.return_code != 0,
        route=(result.agent, result.model), unavailable_until=result.unavailable_until)


def _bind_result_capture(config, registry, original_run):
    run = config.agent_run
    if managed_agy_plan(run, registry):
        return _bind_agy_plan_capture(config, registry, original_run)
    ids = (config.result_capture or {}).get('review_comment_ids')
    # Review comment IDs are modifier closure authority; an impl never binds them.
    if (config.result_capture is None or config.execution_mode != 'implement' or
            run.role not in ('impl', 'modify') or (run.role == 'impl' and ids) or
            not verification.capture_cycle(run.cycle) or not config.pre_spawn_head or
            not git.repo_available(str(run.repo_root))):
        return config
    try:
        if git.current_head(str(run.repo_root)) != config.pre_spawn_head:
            return config
    except (OSError, subprocess.SubprocessError):
        return config
    spec = build_invocation(run, registry)
    source = capture_candidate(run, registry, spec)
    if not source or not supports_final_response_capture(spec, source):
        return config
    for path in (original_run.result_file, original_run.stream_directory or run.stream_directory):
        if Path(path).is_symlink():
            raise ValueError('capture paths must not be symlinks')
    contract = {'required_checks': list(config.result_capture['required_checks']), 'review_comment_ids': list(ids)}
    identity = verification.capture_identity(run)
    context = {'schema_version': 1, 'source': source, 'cycle': identity['cycle'],
        **contract, 'envelope': identity}
    run = replace(run, final_response_file=Path(run.stream_directory) / 'final-response.json' if source == CODEX else None,
        result_capture_context=context)
    build_invocation(run, registry)
    return replace(config, agent_run=run, result_capture=contract)


def _bind_agy_plan_capture(config, registry, original_run):
    run = config.agent_run
    if (not verification.capture_cycle(run.cycle) or run.extra_args or
            run.permission_mode not in ('', 'safe') or run.gui_mode not in ('auto', '0')):
        raise ValueError('agy-plan-read-only-unavailable: unsafe invocation settings or missing cycle')
    if original_run.stream_directory and Path(original_run.stream_directory).is_symlink():
        raise ValueError('capture paths must not be symlinks')
    identity = verification.capture_identity(run)
    context = {'schema_version': 1, 'source': AGY, 'cycle': identity['cycle'], 'envelope': identity}
    run = replace(run, permission_mode='safe', gui_mode='0', result_capture_context=context)
    _, digest = agy_plan_invocation(run, registry)
    return replace(config, agent_run=replace(run, result_capture_context={**context, 'invocation_digest': digest}),
                   result_capture=None)


def _check_protected_plan_locations(config, original_run):
    run = config.agent_run
    plan, result = _plan_paths(original_run)
    if any(path.absolute() != path.resolve() for path in (plan, result)):
        raise ValueError('unsafe protected plan artifact path')
    if Path(str(config.state_file) + '.lock').is_symlink():
        raise ValueError('unsafe protected plan state lock path')
    source = str(run.repo_root)
    roots = [Path(source)]
    if git.repo_available(source):
        roots.extend(Path(git.git_output(source, 'rev-parse', '--path-format=absolute', flag)).resolve()
                     for flag in ('--show-toplevel', '--git-dir', '--git-common-dir'))
    locations = (plan, result, config.state_file, config.tail_state_file, run.log_file, run.events_log,
                 run.status_file, run.done_file, run.process_state_file, run.stream_directory, run.record_directory)
    for location in locations:
        if location is None:
            continue
        path = Path(location).resolve()
        if any(_paths_collide(path, root) for root in roots):
            raise ValueError(f'protected plan writes must be outside source and Git metadata: {path}')
        if path.is_file() and path.stat().st_nlink != 1:
            raise ValueError(f'unsafe hard-linked protected plan path: {path}')
    if plan.exists():
        raise RuntimeError(f'occupied plan path prohibits a new attempt: {plan}')




def _finalize_attempt(config: WorkerConfig, *, provider_return_code: int | None,
                      reason: str = '', failure_class: str = 'capability',
                      prelaunch_failure: bool = False, runner_failed: bool = False,
                      route: tuple[str, str] | None = None,
                      unavailable_until: float | None = None) -> WorkerResult:
    """Journal one decision after callers prove owned death or no launch.

    Outcome -> state -> DONE -> event. A storage failure propagates, retaining
    the claim and any committed journal so adoption can repair publication.
    """
    run = config.agent_run
    with DirectoryLock(config.state_file):
        state = load_json(Path(config.state_file))
        if any(key in state and state[key] != value for key, value in
                (('run_id',run.run_id),('attempt_id',run.attempt_id))):
            return WorkerResult('unknown',3,Path(config.state_file),'worker-state-binding-mismatch')
        try:
            previous = _read_bound_outcome(config)
            stop_intent = _bound_stop_intent(config,state)
        except (ValueError, TypeError):
            return WorkerResult('unknown',3,Path(config.state_file),'invalid-or-conflicting-outcome')
        if provider_return_code is None and not prelaunch_failure:
            return WorkerResult('unknown',3,Path(config.state_file),'provider-exit-status-unknown')
        terminal = previous is not None and previous['status'] != 'pending'
        protected_plan = _protected_plan_capture(run)
        if terminal and protected_plan:
            changed = _plan_handoff_change(run, previous)
            if changed:
                return WorkerResult('unknown', 3, Path(config.state_file), changed)
        if terminal and config.execution_mode == 'verify_existing':
            try:
                if previous.get('verification_handoff') != verification.handoff_fingerprint(config):
                    return WorkerResult('unknown',3,Path(config.state_file),'verification-handoff-changed')
            except OSError:
                return WorkerResult('unknown',3,Path(config.state_file),'verification-handoff-unavailable')
        if terminal:
            # Only runtime stop reasons change the provider's actual exit code.
            if previous['reason'] in ('interrupted','hard-limit-exceeded','dispatch-timeout','dispatch-bootstrap-failed'):
                reason = previous['reason']
            elif prelaunch_failure:
                reason = reason or previous['reason']
            if previous['failure_class'] == 'infrastructure':
                reason = reason or _availability_reason(config)
                failure_class = 'infrastructure'
        if stop_intent:
            reason = stop_intent['reason']
        # Kept apart from ``reason``, which artifact recovery may overwrite, so
        # routing still excludes an unavailable harness after salvaged work.
        unavailable_reason = reason if failure_class == 'infrastructure' and reason not in _STOP_REASONS else ''
        if unavailable_reason == 'quota' and unavailable_until is None:
            unavailable_until = _availability_until(config, unavailable_reason)
        capture_reason = 'capture-attempt-not-bound' if protected_plan and previous is None else ''
        plan_handoff = None
        if previous is not None and not terminal and capture_source(run):
            capture_reason, plan_handoff = _publish_captured_result(config, eligible=(provider_return_code == 0 and
                not reason and not prelaunch_failure and not runner_failed))
        if protected_plan and terminal:
            plan_handoff = previous.get('plan_handoff')
            capture_reason = previous['reason']
        # A failed protected capture must never read a stray direct report.
        if protected_plan and plan_handoff is None:
            payload = None
        else:
            payload, _ = artifacts.load_json(str(run.result_file))
        typed = isinstance(payload, dict) and payload.get('artifact_kind') == 'role_failure'
        status, issue_outcome = 'failed', None
        if typed:
            invalid = artifacts.role_failure_reason(payload, issue=str(run.issue), role=run.role,
                run_id=run.run_id, attempt_id=run.attempt_id)
            if invalid:
                reason, code = invalid, provider_return_code or 1
            else:
                status, reason = payload['status'], payload['reason']
                code = 130 if status == 'interrupted' else provider_return_code or 1
        else:
            artifact_reason = capture_reason if protected_plan else _artifact_reason(config)
            if artifact_reason and not protected_plan and not terminal and not prelaunch_failure:
                _safe_synthesis(config, reason, provider_return_code=provider_return_code)
                artifact_reason = _artifact_reason(config)
            if artifact_reason == 'artifact-recovery-required':
                reason, code = artifact_reason, 75
            elif prelaunch_failure:
                reason = reason or 'dispatch-bootstrap-failed'
                code = 127 if reason == 'missing-command' else 1
            elif reason == 'interrupted':
                status, code = 'interrupted', 130
            elif reason in ('hard-limit-exceeded','dispatch-timeout'):
                code = 124
            else:
                if protected_plan and provider_return_code and capture_reason == 'capture-attempt-not-successful':
                    reason = reason or 'provider-exit-nonzero'
                reason = reason or artifact_reason
                code = provider_return_code or (1 if reason else 0)
                if code and not reason:
                    reason = 'provider-exit-nonzero'
                if not code:
                    status = 'success'
                    if run.role in ('sub-coord','merge-recovery') and isinstance(payload,dict):
                        issue_outcome = payload.get('outcome')
        if stop_intent and not (not typed and code == 75 and reason == 'artifact-recovery-required'):
            # Provider reports remain untouched, but cannot erase a forced stop.
            status, code, reason = (stop_intent['status'],stop_intent['worker_return_code'],stop_intent['reason'])
            issue_outcome = None
        decision = {'status':status,'provider_return_code':provider_return_code,
            'worker_return_code':code,'reason':reason,'failure_class':failure_class}
        if issue_outcome:
            decision['issue_outcome'] = issue_outcome
        if terminal:
            if any(previous.get(key) != value for key,value in decision.items()) or previous.get('issue_outcome') != issue_outcome:
                return WorkerResult('unknown',3,Path(config.state_file),'conflicting-terminal-outcome')
            outcome = previous
        else:
            outcome = previous or pending_outcome(run_id=run.run_id,attempt_id=run.attempt_id,
                issue=str(run.issue),role=run.role,state_file=config.state_file,
                expected_artifacts=expected_artifact_paths(config))
            outcome = {**outcome, **decision, 'finished_at':time.time()}
            progress = verification.git_progress(config)
            if progress is not None:
                outcome['git_progress'] = progress
            if config.execution_mode == 'verify_existing':
                outcome['verification_handoff'] = verification.handoff_fingerprint(config)
            if plan_handoff is not None:
                outcome['plan_handoff'] = plan_handoff
            write_json_atomic(outcome_path(config.state_file),outcome)
        worker_state = ('completed' if status == 'success' else 'interrupted' if status == 'interrupted'
            else 'artifact-recovery-required' if not typed and code == 75 and reason == 'artifact-recovery-required' else 'failed')
        process, _ = _provider_record(config)
        state_was_terminal = terminal and state.get('state') == worker_state
        state.update(state=worker_state,return_code=code,exit_code=code,reason=reason,
            completed_at=outcome['finished_at'],alive=False,provider_identity=process.get('identity'),
            failure_class=failure_class)
        if unavailable_reason:
            state['unavailable_reason'] = unavailable_reason
            if unavailable_reason == 'quota' and isinstance(unavailable_until, (int, float)) and unavailable_until > 0:
                state['unavailable_until'] = float(unavailable_until)
        if route and failure_class == 'infrastructure' and not state_was_terminal:
            state.update(agent=route[0], model=route[1])  # resolved route, dispatcher-owned
        write_json_atomic(Path(config.state_file),state)
        if run.done_file:
            fields = {'issue':run.issue,'role':run.role,'agent':run.agent,'model':run.model,
                'status':status,'source':'dispatcher','scope':'role-handoff',
                'run_id':run.run_id,'attempt_id':run.attempt_id,'completed_at':outcome['finished_at']}
            if issue_outcome:
                fields['issue_outcome'] = issue_outcome
            write_text_atomic(run.done_file, 'DONE|' + '|'.join(f'{key}={normalize(value)}' for key,value in fields.items()) + '\n')
        bus = EventBus(status_file=run.status_file,events_log=run.events_log,log_file=run.log_file)
        bus.emit('dispatch-complete' if worker_state=='completed' else 'dispatch-recovery-required' if worker_state=='artifact-recovery-required' else 'dispatch-failed',
            {'issue':run.issue,'role':run.role,'status':worker_state,'reason':reason,
             'result_file':str(run.result_file),'run_id':run.run_id,'attempt_id':run.attempt_id})
        return WorkerResult(worker_state,code,Path(config.state_file),reason,process.get('identity'))


def _detached(path):
    config = WorkerConfig.from_record(load_json(Path(path)))
    def interrupt(_number,_frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupt)
    return _work(config).return_code
