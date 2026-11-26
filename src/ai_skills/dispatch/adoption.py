"""Admitted worker adoption, prelaunch inspection, and recovery child binding."""
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
    _artifact_args, _optional, _preflight_paths, _process_record,
    expected_artifact_paths,
)
from ai_skills.dispatch.capture import (
    _artifact_reason, inspect_worker,
    _agy_adoption_diagnostic, _availability_reason, _bound_stop_intent,
    _launcher_state, _provider_record, _read_bound_outcome, _resolved_route,
    _unknown_attempt, write_text_atomic, _availability_until,
)
from ai_skills.dispatch.lifecycle import (
    _bind_agy_plan_capture, _bind_result_capture, _check_protected_plan_locations,
    _detached, _finalize_attempt, _stop_provider, _work,
)

def _admitted_worker(state_file):
    config = WorkerConfig.from_record(load_json(Path(str(state_file)+'.config.json')))
    if (canonical_path(config.state_file) != canonical_path(state_file) or
            (config.agent_run.state_file is not None and
             canonical_path(config.agent_run.state_file) != canonical_path(state_file))):
        raise ValueError('worker-config-state-binding-mismatch')
    return config


def _dispatcher_record(config, supervisor):
    run = config.agent_run
    wrapper = _optional(Path(str(config.state_file)+'.dispatcher.process.json'))
    if not isinstance(wrapper, dict):
        raise ValueError('invalid-dispatcher-process-record')
    if not wrapper.get('identity'):
        raw = supervisor.lookup_attempt(run.run_id+'-dispatcher',run.attempt_id)
        if raw is not None:
            return {**raw,'record_directory':str(supervisor.directory)}
    return wrapper


def _owner_result(config, state, reason, identity=None):
    return WorkerResult('running' if state == 'alive' else 'unknown',
        0 if state == 'alive' else 3, config.state_file, reason, identity)


def _inspect_dispatcher(config, wrapper):
    if not wrapper.get('identity'):
        return _owner_result(config, 'unknown', 'dispatcher-launch-identity-unknown')
    identity = ProcessIdentity(**wrapper['identity'])
    run = config.agent_run
    if (identity.run_id, identity.attempt_id) != (run.run_id+'-dispatcher', run.attempt_id):
        raise ValueError('dispatcher identity binding mismatch')
    owner = ProcessSupervisor(Path(wrapper['record_directory'])).inspect(identity)
    if owner.state != 'dead':
        return _owner_result(config, owner.state, 'dispatcher-finalizing')
    return None


def _inspect_adoption(config, state, *, strict=False) -> WorkerResult | int | None:
    """Return a hold, or the durable provider exit code (None for prelaunch)."""
    journal = _read_bound_outcome(config)
    if strict and journal is None:
        raise ValueError('worker-outcome-missing')
    _bound_stop_intent(config,state)
    process, supervisor = _provider_record(config)
    wrapper = _dispatcher_record(config, supervisor)
    if strict and not wrapper:
        launcher = _launcher_state(state)
        if launcher != 'dead':
            return _owner_result(config, launcher, 'launcher-ownership-'+launcher, process.get('identity'))
        # Admission could publish a dispatcher after the first lookup.
        wrapper = _dispatcher_record(config, supervisor)
    if wrapper:
        hold = _inspect_dispatcher(config, wrapper)
        if hold:
            return replace(hold, identity=process.get('identity'))
    if wrapper or strict:
        # The dispatcher may have admitted a provider just before it died.
        process, supervisor = _provider_record(config)
    if not process.get('identity'):
        return _inspect_prelaunch(config, state, process, wrapper, journal, strict=strict)
    identity = ProcessIdentity(**process['identity'])
    liveness = supervisor.inspect(identity)
    if liveness.state != 'dead':
        return _owner_result(config, liveness.state, liveness.reason, process['identity'])
    if strict and config.detach and not wrapper:
        return _owner_result(config, 'unknown', 'dispatcher-launch-identity-unknown')
    if not strict and not wrapper and (journal is None or journal['status']=='pending'):
        launcher = _launcher_state(state)
        if launcher != 'dead':
            return _owner_result(config, launcher, 'launcher-ownership-'+launcher, process['identity'])
    code = supervisor.return_code(identity)
    if not strict:
        deadline = time.monotonic()+.5
        while code is None and time.monotonic()<deadline:
            time.sleep(.02)
            code = supervisor.return_code(identity)
    if code is None:
        return _owner_result(config, 'unknown', 'provider-exit-status-unknown', process['identity'])
    return code


def _inspect_prelaunch(config, state, process, wrapper, journal, *, strict):
    if process or (strict and state.get('provider_identity') is not None):
        return _owner_result(config, 'unknown', 'provider-launch-identity-unknown')
    if not wrapper and not strict:
        launcher = _launcher_state(state)
        if launcher != 'dead':
            return _owner_result(config, launcher, 'launcher-ownership-'+launcher)
    claim_state = state.get('state')
    launching = claim_state in ('launching', 'starting')
    if strict:
        if journal['status'] == 'pending':
            released = launching
        else:
            # A bound terminal journal with no provider exit supports prelaunch finalization.
            # The claim may still be in bootstrap if publication stopped after the journal.
            finalized = claim_state in ('failed', 'interrupted', 'artifact-recovery-required', 'completed')
            released = journal['provider_return_code'] is None and (launching or finalized)
    else:
        released = wrapper or launching or state.get('reason') in (
            'dispatch-bootstrap-failed', 'missing-command', 'cli-unsupported')
    if released:
        return None
    return _owner_result(config, 'unknown', 'provider-launch-identity-unknown')


def _adopt_released(config, state, code):
    name, model, agy_stream = _resolved_route(config)
    route = (name, model)
    if code is None:
        return _finalize_attempt(config,provider_return_code=None,
            reason=state.get('reason') or '', failure_class=state.get('failure_class','capability'),
            prelaunch_failure=True, route=route)
    process, supervisor = _provider_record(config)
    reason = process.get('unavailable_reason', '')
    if not reason:
        if agy_stream:
            raw = supervisor.lookup_attempt(config.agent_run.run_id, config.agent_run.attempt_id) or {}
            reason = _agy_adoption_diagnostic(config, model, process, raw, provider_code=code)
        elif code:
            reason = _availability_reason(config, provider_code=code)
    return _finalize_attempt(config,provider_return_code=code,reason=reason,
        failure_class='infrastructure' if reason else 'capability', route=route,
        unavailable_until=_availability_until(config, reason))


def adopt_worker(state_file) -> WorkerResult:
    """Reconcile only after provider and dispatcher death; never launch work."""
    state_file = Path(state_file)
    try:
        config = _admitted_worker(state_file)
        state = load_json(state_file)
        inspected = _inspect_adoption(config, state)
        if isinstance(inspected, WorkerResult):
            return inspected
        return _adopt_released(config, state, inspected)
    except (ValueError, TypeError, KeyError) as error:
        return WorkerResult('unknown',3,state_file,str(error))


def _bind_recovery_child(config, state, snapshot, issue, issue_dir):
    run = config.agent_run
    if str(run.issue) != str(issue) or run.role != snapshot.get('role'):
        raise ValueError('child-issue-or-role-binding-mismatch')
    if not run.issue_dir or canonical_path(run.issue_dir) != canonical_path(issue_dir):
        raise ValueError('child-issue-directory-binding-mismatch')
    for key in ('done_file', 'result_file'):
        path, recorded = getattr(run, key), snapshot.get(key)
        if not path or not isinstance(recorded, str) or not recorded or canonical_path(path) != canonical_path(recorded):
            raise ValueError('child-artifact-path-binding-mismatch')
    for key in ('run_id', 'attempt_id'):
        expected = getattr(run, key)
        if not expected or state.get(key) != expected or (key in snapshot and snapshot[key] != expected):
            raise ValueError('child-attempt-binding-mismatch')
    if run.cycle and snapshot.get('cycle') is not None:
        if verification.capture_cycle(run.cycle) != verification.capture_cycle(snapshot['cycle']):
            raise ValueError('child-cycle-binding-mismatch')


def inspect_recovery_child(issue, issue_dir, child, *, reconcile=False):
    """Inspect the exact ledger child, optionally adopting its released handoff."""
    recorded = child.get('state_file')
    if not isinstance(recorded, str) or not recorded:
        return WorkerResult('unknown', 3, Path(), 'child-state-path-missing')
    state_file = Path(recorded)
    try:
        config = _admitted_worker(state_file)
        state = load_json(state_file)
        if not isinstance(state, dict):
            raise ValueError('invalid-child-claim')
        _bind_recovery_child(config, state, child, issue, issue_dir)
        inspected = _inspect_adoption(config, state, strict=True)
        if isinstance(inspected, WorkerResult):
            return inspected
        if reconcile:
            return _adopt_released(config, state, inspected)
        return WorkerResult('released', 0, state_file, 'child-ownership-released')
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        return WorkerResult('unknown', 3, state_file, str(error))


def dispatch_worker(config: WorkerConfig) -> WorkerResult:
    # Complete input validation before writing launch state or starting a child.
    registry = load_registry(config.agent_run.registry_file)
    original_run = config.agent_run
    run = replace(original_run, final_response_file=None, result_capture_context=None)
    protected_plan = managed_agy_plan(replace(run, dispatcher_managed=True), registry)
    if protected_plan:
        require_agy_plan_profile(registry)
    else:
        build_invocation(run, registry)
    if not config.agent_run.result_file:
        raise ValueError('result_file is required')
    state_file = Path(config.state_file).resolve()
    directory = state_file.parent
    run = replace(run, state_file=state_file, scrub_ambient_aliases=True, dispatcher_managed=True,
                  process_state_file=config.agent_run.process_state_file or Path(str(state_file)+'.process.json'),
                  stream_directory=config.agent_run.stream_directory or Path(str(state_file)+'.streams'),
                  run_id=config.agent_run.run_id or f'worker-{config.agent_run.issue}-{config.agent_run.role}-{uuid.uuid4().hex}',
                  attempt_id=config.agent_run.attempt_id or uuid.uuid4().hex)
    if protected_plan and run.record_directory is None:
        run = replace(run, record_directory=directory / 'processes')
    # Bind the paths used by the provider to the exact paths preflight checks.
    run = replace(run, **{name: Path(getattr(run, name)).resolve() for name in (
        'repo_root', 'context_file', 'prompt_file', 'registry_file', 'issue_dir', 'result_file',
        'done_file', 'log_file', 'events_log', 'status_file', 'process_state_file',
        'stream_directory', 'record_directory') if getattr(run, name) is not None})
    # Admission records the effective effort; the explicit request stays as provenance.
    run = effective_config(run, registry)
    pre_head = config.pre_spawn_head
    if not pre_head and git.repo_available(str(run.repo_root)):
        try:
            pre_head = git.current_head(str(run.repo_root))
        except (OSError, subprocess.CalledProcessError):
            pre_head = ''
    if config.execution_mode == 'verify_existing':
        run = replace(run, verification_context={'execution_mode': config.execution_mode,
            'issue_base_sha': config.issue_base_sha, **config.retry_contract})
    else:
        run = replace(run, verification_context=None)
    config = replace(config,agent_run=run,state_file=state_file,pre_spawn_head=pre_head)
    if protected_plan:
        _check_protected_plan_locations(config, original_run)
    if config.execution_mode == 'verify_existing':
        reason = verification.admission_reason(config)
        if reason:
            raise ValueError(reason)
    config_path = Path(str(state_file)+'.config.json')
    with DirectoryLock(state_file):
        old = _optional(state_file)
        if old:
            raise RuntimeError('existing worker state prohibits relaunch; inspect or create a new attempt')
        config = _bind_result_capture(config, registry, original_run)
        run = config.agent_run
        if protected_plan:
            _check_protected_plan_locations(config, original_run)
        expected = _preflight_paths(config)
        write_json_atomic(state_file,{'schema_version':1,'state':'launching','run_id':run.run_id,'attempt_id':run.attempt_id,
            'launcher_identity':{'pid':os.getpid(),'created_at':psutil.Process().create_time(),'host':socket.gethostname()}})
        write_json_atomic(config_path,config.to_record())
        write_json_atomic(outcome_path(state_file),pending_outcome(run_id=run.run_id,attempt_id=run.attempt_id,
            issue=str(run.issue),role=run.role,state_file=state_file,expected_artifacts=expected))
    if not config.detach:
        return _work(config)
    supervisor = ProcessSupervisor(run.record_directory or (Path(run.repo_root)/'.ai-skills'/'processes'))
    root = str(Path(__file__).resolve().parents[2])
    bootstrap = 'import sys;sys.path.insert(0,sys.argv[1]);from ai_skills.dispatch import _detached;raise SystemExit(_detached(sys.argv[2]))'
    output = Path(str(state_file)+'.dispatcher.log')
    wrapper_environment = {} if protected_plan else run.environment or {}
    owned = supervisor.start(ProcessSpec((sys.executable,'-c',bootstrap,root,str(config_path)),Path(run.repo_root),wrapper_environment,output,output),
                             run_id=run.run_id+'-dispatcher',attempt_id=run.attempt_id)
    # Store the wrapper identity separately. Provider process_state_file is the
    # authority for nested session shutdown, including after dispatcher loss.
    write_json_atomic(Path(str(state_file)+'.dispatcher.process.json'),{'schema_version':1,'identity':asdict(owned.identity),'record_directory':str(supervisor.directory)})
    agent = registry.get('aliases', {}).get(run.agent, run.agent)
    probe_seconds = 10 if registry['agents'][agent].get('invocation', {}).get('required_cli_flags') else 0
    # The bootstrap interval covers dispatcher setup. The runner then has a
    # separately bounded capability probe (when required) and owner handshake.
    deadline = time.monotonic()+config.bootstrap_seconds+probe_seconds+10
    while time.monotonic()<deadline:
        state = _optional(state_file)
        if state.get('state') in ('running','quiet','stalled'):
            return WorkerResult(state['state'],int(state.get('return_code',0)),state_file,state.get('reason',''),state.get('provider_identity'))
        if supervisor.inspect(owned.identity).state=='dead':
            # A terminal state can precede DONE or fail its publication. Only
            # dead-wrapper adoption replays the complete publication contract.
            if owned.close().state != 'dead':
                return _unknown_attempt(config,'shutdown-identity-unknown',state.get('provider_identity'))
            return adopt_worker(state_file)
        time.sleep(min(.03,config.poll_seconds))
    # Admission may be incomplete. Stop both nested provider and wrapper; never
    # authorize another attempt merely because the handshake timed out.
    _stop_provider(config)
    wrapper_stop = owned.close()
    # Admission may publish while wrapper shutdown is in flight. Recheck the
    # current provider identity before classifying this attempt as retryable.
    provider_stop = _stop_provider(config)
    state = _optional(state_file)
    process, _ = _provider_record(config)
    if wrapper_stop.state != 'dead' or (process and provider_stop.state != 'dead'):
        return _unknown_attempt(config,'shutdown-identity-unknown',state.get('provider_identity'))
    return adopt_worker(state_file)


