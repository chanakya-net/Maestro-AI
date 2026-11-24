"""Provider records, stop intents, captured-result import, and protected plan publication."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields, replace
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

def _provider_record(config, *, allow_supervisor_fallback=True):
    """Read runner process state, with optional fallback for a lost handshake."""
    run = config.agent_run
    supervisor = ProcessSupervisor(run.record_directory or (Path(run.repo_root)/'.ai-skills'/'processes'))
    process = _process_record(config)
    if not isinstance(process, dict):
        raise ValueError('invalid-provider-process-record')
    if not process.get('identity') and allow_supervisor_fallback:
        raw = supervisor.lookup_attempt(run.run_id, run.attempt_id)
        if raw is not None:
            process = {**raw, 'record_directory': str(supervisor.directory)}
    elif process.get('record_directory'):
        supervisor = ProcessSupervisor(Path(process['record_directory']))
    if process.get('identity'):
        identity = ProcessIdentity(**process['identity'])
        if identity.run_id != run.run_id or identity.attempt_id != run.attempt_id:
            raise ValueError('provider identity binding mismatch')
    return process, supervisor


def _unknown_attempt(config, reason, identity=None):
    state = _optional(config.state_file)
    state.update(state='unknown', return_code=3, reason=reason)
    # A previous terminal hint cannot establish tree death after uncertainty.
    state.pop('alive', None)
    state.pop('completed_at', None)
    write_json_atomic(Path(config.state_file), state)
    return WorkerResult('unknown', 3, Path(config.state_file), reason, identity)


def write_text_atomic(path, text, *, exclusive=False):
    """Flush before publication, optionally reserving the destination atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=path.parent,
                prefix=f'.{path.name}.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            replace_file_atomic(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_bound_outcome(config):
    run = config.agent_run
    missing = object()
    payload = load_json(outcome_path(config.state_file), missing=missing)
    if payload is not missing:
        reason = outcome_reason(payload, run_id=run.run_id, attempt_id=run.attempt_id,
            issue=str(run.issue), role=run.role, state_file=config.state_file,
            expected_artifacts=expected_artifact_paths(config))
        if reason:
            raise ValueError(reason)
    return None if payload is missing else payload


def _safe_synthesis(config, reason):
    """Only absent outputs may be reconstructed; never erase provider bytes."""
    if config.execution_mode == 'verify_existing':
        return False
    paths = expected_artifact_paths(config)
    run = config.agent_run
    if run.final_response_file is not None and Path(run.result_file).resolve() != Path(run.result_file):
        return False
    # Review repair can overwrite both files. Preserve any malformed existing
    # companion, and only use the existing repair rules for an absent output.
    for path in paths.values():
        if Path(path).exists():
            payload, error = artifacts.load_json(path)
            if error or not isinstance(payload, dict):
                return False
    if run.role == 'review':
        if Path(paths['result']).exists() and Path(paths['review_instructions']).exists():
            return False
        if Path(paths['result']).exists():
            payload, _ = artifacts.load_json(paths['result'])
            if not artifacts.valid_review_status(payload):
                return False
        if Path(paths['review_instructions']).exists():
            payload, _ = artifacts.load_json(paths['review_instructions'])
            if not artifacts.valid_review_instructions(payload):
                return False
    elif Path(paths['result']).exists():
        return False
    args = _artifact_args(config, from_stall=reason in ('interrupted','hard-limit-exceeded','dispatch-timeout'))
    if run.role in ('impl','modify') and config.pre_spawn_head and git.repo_available(str(run.repo_root)):
        try:
            args.from_stall = args.from_stall or git.current_head(str(run.repo_root)) != config.pre_spawn_head
        except (OSError, subprocess.CalledProcessError):
            pass
    return any(function(args) for function in (artifacts.synthesize_implementation,
        artifacts.synthesize_review, artifacts.synthesize_complexity))


def _resolved_route(config):
    run = config.agent_run
    try:
        registry = load_registry(run.registry_file)
        name, agent = _agent(registry, run.agent)
        model = effective_config(run, registry).model or agent.get('model', {}).get('default', '')
        agy_stream = agent.get('invocation', {}).get('prompt_transport') == AGY or capture_source(run, name) == AGY
        return name, model, agy_stream
    except (OSError, ValueError):
        return run.agent, run.model, capture_source(run) == AGY


def _read_tail_text(path):
    with Path(path).open('rb') as stream:
        stream.seek(max(0, Path(path).stat().st_size - 65536))
        return stream.read().decode('utf-8', errors='replace')


def _agy_adoption_diagnostic(config, model, process, raw, *, provider_code):
    """Classify AGY failure on adoption using only structured terminal errors and stderr."""
    stdout_path = process.get('stdout_file') or raw.get('spec', {}).get('stdout')
    stderr_path = process.get('stderr_file') or raw.get('spec', {}).get('stderr')
    stderr_text = _read_tail_text(stderr_path) if stderr_path and Path(stderr_path).is_file() else ''
    if stdout_path and Path(stdout_path).is_file():
        managed = capture_source(config.agent_run) == AGY
        try:
            with Path(stdout_path).open('rb') as stream:
                if managed:
                    extract_response(AGY, stream)
                else:
                    agy_narrative_response(stream)
        except ProviderError as error:
            return _classify_agy(str(error), model) or _classify_agy(stderr_text, model)
        except (ValueError, RecursionError):
            pass
    return _classify_agy(stderr_text, model) if provider_code else ''


def _availability_reason(config, *, provider_code=1):
    process, supervisor = _provider_record(config)
    raw = supervisor.lookup_attempt(config.agent_run.run_id, config.agent_run.attempt_id) or {}
    _, model, agy_stream = _resolved_route(config)
    if agy_stream:
        return _agy_adoption_diagnostic(config, model, process, raw, provider_code=provider_code)
    text = ''
    for key, stream_key in (('stdout_file','stdout'),('stderr_file','stderr')):
        path = process.get(key) or raw.get('spec',{}).get(stream_key)
        if path and Path(path).is_file():
            text += _read_tail_text(path)
    return classify_unavailable(text)


def _launcher_state(state):
    owner = state.get('launcher_identity')
    if not isinstance(owner,dict) or owner.get('host') != socket.gethostname():
        return 'unknown'
    if type(owner.get('pid')) is not int or owner['pid'] <= 0 or type(owner.get('created_at')) not in (int,float) or not math.isfinite(owner['created_at']) or owner['created_at'] <= 0:
        return 'unknown'
    try:
        process = psutil.Process(owner['pid'])
        return 'dead' if process.create_time() != owner['created_at'] or process.status() == psutil.STATUS_ZOMBIE else 'alive'
    except psutil.NoSuchProcess:
        return 'dead'
    except (OSError, psutil.AccessDenied):
        return 'unknown'


def _bound_stop_intent(config, state):
    """A stop decision is durable provenance, not tree-death evidence."""
    intent = state.get('stop_intent')
    if intent is None:
        return None
    run = config.agent_run
    binding = {'schema_version':1,'run_id':run.run_id,'attempt_id':run.attempt_id,
        'issue':str(run.issue),'role':run.role,'state_file':canonical_path(config.state_file)}
    if not isinstance(intent,dict) or any(intent.get(key) != value for key,value in binding.items()):
        raise ValueError('stop-intent-binding-mismatch')
    expected = {'hard-limit-exceeded':('failed',124),'dispatch-timeout':('failed',124),
        'interrupted':('interrupted',130)}.get(intent.get('reason'))
    if expected is None or (intent.get('status'),intent.get('worker_return_code')) != expected:
        raise ValueError('invalid-stop-intent-decision')
    if type(intent.get('schema_version')) is not int or type(intent.get('worker_return_code')) is not int or type(intent.get('recorded_at')) not in (float,int) or not math.isfinite(intent['recorded_at']) or intent['recorded_at'] < 0:
        raise ValueError('invalid-stop-intent-schema')
    process, _ = _provider_record(config)
    if 'provider_identity' not in intent or (intent['provider_identity'] is not None and intent['provider_identity'] != process.get('identity')):
        raise ValueError('stop-intent-provider-binding-mismatch')
    return intent


def _record_stop_intent(config, reason):
    """Persist the forced decision before any signal can change process exit."""
    run = config.agent_run
    status, code = ('interrupted',130) if reason == 'interrupted' else ('failed',124)
    with DirectoryLock(config.state_file):
        state = load_json(Path(config.state_file))
        if state.get('run_id') != run.run_id or state.get('attempt_id') != run.attempt_id:
            raise ValueError('worker-state-binding-mismatch')
        _read_bound_outcome(config)
        _bound_stop_intent(config,state)
        process, _ = _provider_record(config)
        state['stop_intent'] = {'schema_version':1,'run_id':run.run_id,'attempt_id':run.attempt_id,
            'issue':str(run.issue),'role':run.role,'state_file':canonical_path(config.state_file),
            'recorded_at':time.time(),'reason':reason,'status':status,'worker_return_code':code,
            'provider_identity':process.get('identity')}
        write_json_atomic(Path(config.state_file),state)


def _capture_rejected(run, reason):
    EventBus(events_log=run.events_log, log_file=run.log_file).emit('dispatch-capture-rejected',
        {'run_id':run.run_id, 'attempt_id':run.attempt_id, 'issue':run.issue, 'role':run.role, 'cycle':verification.capture_cycle(run.cycle), 'reason':reason})
    return reason, None


def _capture_bytes(run, process, supervisor):
    source = capture_source(run)
    directory = Path(run.stream_directory)
    if source == CODEX:
        path = Path(run.final_response_file)
        if path != directory / 'final-response.json':
            raise OSError('capture path changed')
    else:
        path = directory / 'stdout'
        raw = supervisor.lookup_attempt(run.run_id, run.attempt_id) or {}
        if (raw.get('identity') != process['identity'] or process.get('stdout_file') != str(path) or
                (raw.get('spec') or {}).get('stdout') != str(path)):
            raise OSError('capture stdout binding changed')
    if path.resolve() != path:
        raise OSError('capture path changed')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError('capture is not an independent regular file')
    if source != CODEX and process.get('stdout_identity') != [info.st_dev, info.st_ino]:
        raise OSError('capture stdout was replaced')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        _check_capture_file(path, opened, info)
        response = extract_response(source, stream)
        after_read = os.fstat(stream.fileno())
        _check_capture_file(path, after_read, info)
        if after_read.st_ctime_ns != opened.st_ctime_ns:
            raise OSError('capture changed while reading')
        return response


def _check_capture_file(path, opened, original):
    current = path.lstat()
    for info in (opened, current):
        if (not os.path.samestat(original, info) or info.st_nlink != 1 or
                (info.st_size, info.st_mtime_ns) != (original.st_size, original.st_mtime_ns)):
            raise OSError('capture changed while reading')
    # Windows reports creation time for lstat and metadata change time for
    # fstat. Compare each ctime only against an earlier value from the same API.
    if current.st_ctime_ns != original.st_ctime_ns:
        raise OSError('capture changed while reading')
    if path.resolve() != path:
        raise OSError('capture path changed')


def _publish_captured_result(config, *, eligible):
    """Publish a validated captured report while the attempt lock excludes other finalizers."""
    run = config.agent_run
    protected_plan = _protected_plan_capture(run)
    if not protected_plan and (config.result_capture is None or config.execution_mode != 'implement' or
                               not capture_source(run)):
        return '', None
    path = Path(run.result_file)
    if not protected_plan and (path.exists() or path.is_symlink()):
        return '', None
    if not eligible:
        return _capture_rejected(run, 'capture-attempt-not-successful')
    # The supervisor can prove provider exit even if the runner failed before
    # writing its process state. Only the runner's own record proves completion.
    process, supervisor = _provider_record(config, allow_supervisor_fallback=False)
    if (not process.get('identity') or process.get('state') != 'dead' or
            type(process.get('return_code')) is not int or process['return_code'] != 0):
        return _capture_rejected(run, 'capture-runner-exit-unconfirmed')
    identity = ProcessIdentity(**process['identity'])
    if supervisor.inspect(identity).state != 'dead' or supervisor.return_code(identity) != 0:
        return _capture_rejected(run, 'capture-provider-exit-unconfirmed')
    try:
        response = _capture_bytes(run, process, supervisor)
    except FileNotFoundError:
        return _capture_rejected(run, 'capture-response-missing')
    except ProviderError:
        return _capture_rejected(run, 'capture-provider-error')
    except (ValueError, RecursionError):
        return _capture_rejected(run, 'capture-response-invalid')
    except (OSError, RuntimeError):
        return _capture_rejected(run, 'capture-path-unsafe')
    reason, payload, markdown = verification.captured_result_reason(config, response)
    if reason:
        return _capture_rejected(run, reason)
    if protected_plan:
        reason, proof = _publish_protected_plan(config, payload, markdown)
        return _capture_rejected(run, reason) if reason else ('', proof)
    if path.resolve() != path:
        return _capture_rejected(run, 'capture-result-path-unsafe')
    if path.exists() or path.is_symlink():
        return '', None
    try:
        write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + '\n', exclusive=True)
    except FileExistsError:
        # A direct file published concurrently has precedence.
        pass
    return '', None


def _protected_plan_capture(run):
    return run.role == 'plan' and capture_source(run) == AGY


def _plan_paths(run):
    if not run.issue_dir:
        raise ValueError('issue_dir is required for plan workers')
    return Path(run.issue_dir) / 'plan.md', Path(run.result_file)


def _protected_artifact_bytes(path):
    if path.resolve() != path:
        raise ValueError('unsafe protected plan artifact path')
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('unsafe protected plan artifact file')
    return path.read_bytes()


def _plan_handoff_fingerprint(contents):
    return [hashlib.sha256(content).hexdigest() if content is not None else None for content in contents]


def _plan_handoff_change(run, journal):
    if 'plan_handoff' not in journal:
        return 'capture-plan-handoff-missing' if journal['status'] == 'success' else ''
    try:
        contents = map(_protected_artifact_bytes, _plan_paths(run))
        if journal['plan_handoff'] != _plan_handoff_fingerprint(contents):
            return 'capture-plan-handoff-changed'
    except (OSError, ValueError, RuntimeError):
        return 'capture-plan-handoff-unavailable'
    return ''


def _publish_protected_plan(config, payload, markdown):
    run = config.agent_run
    plan, result = _plan_paths(run)
    report = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + '\n'
    paths = {plan: markdown, result: report}
    if _paths_collide(plan, result):
        return 'capture-result-path-unsafe', None
    missing = []
    for path, content in paths.items():
        try:
            existing = _protected_artifact_bytes(path)
        except (ValueError, RuntimeError):
            return 'capture-result-path-unsafe', None
        if existing is None:
            if content is not None:
                missing.append(path)
            continue
        if content is None or existing != content.encode('utf-8'):
            return 'capture-plan-artifact-conflict', None
    if plan in missing and result not in missing:
        return 'capture-plan-artifact-conflict', None
    created = []
    for path in missing:
        try:
            write_text_atomic(path, paths[path], exclusive=True)
            created.append(path)
        except FileExistsError:
            for written in reversed(created):
                written.unlink(missing_ok=True)
            return 'capture-plan-artifact-conflict', None
    proof = _plan_handoff_fingerprint(content.encode('utf-8') if content is not None else None
                                      for content in paths.values())
    reason = _plan_handoff_change(run, {'plan_handoff': proof})
    return reason, proof if not reason else None


def _artifact_reason(config):
    # Inspection must never synthesize, canonicalize, commit or cherry-pick.
    run = config.agent_run
    if not run.result_file:
        return 'missing-result-artifact'
    if _protected_plan_capture(run):
        try:
            journal = _read_bound_outcome(config)
            if not journal or journal['status'] == 'pending':
                return 'capture-attempt-not-finalized'
            if journal['status'] != 'success':
                return journal['reason']
            return _plan_handoff_change(run, journal)
        except (OSError, ValueError, TypeError):
            return 'capture-plan-handoff-unavailable'
    if config.execution_mode == 'verify_existing':
        try:
            journal = _read_bound_outcome(config)
            if journal and journal['status'] != 'pending':
                if journal.get('verification_handoff') != verification.handoff_fingerprint(config):
                    return 'verification-handoff-changed'
                return '' if journal['status'] == 'success' else journal['reason']
        except (OSError, ValueError, TypeError, KeyError):
            return 'invalid-verification-handoff'
    payload, error = artifacts.load_json(str(run.result_file))
    if error:
        return 'missing-result-artifact' if error == 'missing' else 'invalid-result-artifact'
    if config.execution_mode == 'verify_existing':
        return verification.result_reason(config, payload)
    available = git.repo_available(str(run.repo_root)) if run.role in ('impl','modify') else False
    try:
        head = git.current_head(str(run.repo_root)) if available else None
    except (OSError, subprocess.CalledProcessError):
        head = None
    commit = git.resolve_commit(str(run.repo_root),payload.get('commit_sha','')) if available and isinstance(payload,dict) else None
    report = bool(run.issue_dir and Path(run.result_file).resolve() == (Path(run.issue_dir)/'report.json').resolve())
    instructions = None
    if run.role == 'review':
        instructions, error = artifacts.load_json(artifacts.review_instructions_file(str(run.result_file)))
        return artifacts.review_payload_reason(payload, None, instructions, error)
    evidence = artifacts.GitEvidence(issue=str(run.issue),repo_available=available,head=head,
        pre_spawn_head=config.pre_spawn_head,canonical_commit=commit,
        canonical_pre_spawn_head=git.resolve_commit(str(run.repo_root),config.pre_spawn_head) if available and config.pre_spawn_head else None,
        result_is_report=report,plan_file_present=bool(run.issue_dir and (Path(run.issue_dir)/'plan.md').is_file() and (Path(run.issue_dir)/'plan.md').stat().st_size > 0),review_instructions=instructions)
    return artifacts.validate_artifact(run.role,payload,evidence).reason


def inspect_worker(config: WorkerConfig) -> WorkerSnapshot:
    state = _optional(config.state_file)
    process = _process_record(config)
    identity = process.get('identity')
    liveness = 'unknown'
    if identity:
        liveness = ProcessSupervisor(Path(process['record_directory'])).inspect(ProcessIdentity(**identity)).state
    log = Path(config.agent_run.log_file) if config.agent_run.log_file else None
    fresh = bool(log and log.exists() and time.time()-log.stat().st_mtime <= config.quiet_seconds)
    activity = ''
    tail_hash = 'none'
    log_present = False
    log_tail_changed = False
    if log and log.exists():
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size-8192))
            text = stream.read(8192).decode('utf-8', errors='replace')
        activity = text.splitlines()[-1] if text else ''
        log_present = bool(text)
        tail = '\n'.join(text.splitlines()[-config.tail_lines:])
        tail_hash = hashlib.sha256(tail.encode('utf-8')).hexdigest() if text else 'none'
        previous = _optional(config.tail_state_file).get('tail_hash') if config.tail_state_file else None
        log_tail_changed = bool(text and tail_hash != previous)
    reason = _artifact_reason(config)
    done = bool(config.agent_run.done_file and Path(config.agent_run.done_file).is_file() and Path(config.agent_run.done_file).stat().st_size)
    return WorkerSnapshot(liveness, fresh, not reason, reason, done, activity, state.get('state','unknown'), identity, log_present, log_tail_changed, tail_hash)


