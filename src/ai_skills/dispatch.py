"""Worker lifecycle, identity-checked inspection, bounded event watches and stop.

A done file is a hint. Completion additionally requires the owned provider tree
be dead, a zero exit status, and a valid role artifact. Detached startup waits
for a durable provider identity or a terminal failure, never just Popen success.
"""
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
from ai_skills.runner import (
    AgentRunConfig, AgentRunResult, build_invocation, classify_unavailable,
    load_registry, run_agent, supports_final_response_capture,
)
from ai_skills.storage import DirectoryLock, load_json, write_json_atomic, replace_file_atomic
from ai_skills.outcomes import canonical_path, outcome_path, pending_outcome, outcome_reason


@dataclass(frozen=True)
class WorkerConfig:
    agent_run: AgentRunConfig
    state_file: Path
    detach: bool = False
    poll_seconds: float = .1
    quiet_seconds: float = 120
    stall_seconds: float = 600
    timeout_seconds: float = 0
    hard_limit_seconds: float = 7200
    bootstrap_seconds: float = 10
    pre_spawn_head: str = ''
    execution_mode: str = 'implement'
    issue_base_sha: str = ''
    retry_contract: dict | None = None
    tail_state_file: Path | None = None
    tail_lines: int = 5
    result_capture: dict | None = None

    def __post_init__(self):
        verification.validate_configuration(self)
        if type(self.detach) is not bool:
            raise ValueError('detach must be a boolean')
        if type(self.tail_lines) is not int or self.tail_lines < 1:
            raise ValueError('tail_lines must be a positive integer')
        for key in ('poll_seconds', 'quiet_seconds', 'stall_seconds', 'timeout_seconds', 'hard_limit_seconds', 'bootstrap_seconds'):
            value = getattr(self, key)
            if not math.isfinite(value) or value < 0 or (key in ('poll_seconds', 'bootstrap_seconds') and value == 0):
                raise ValueError(f'{key} must be finite and positive' if key in ('poll_seconds','bootstrap_seconds') else f'{key} must be finite and nonnegative')

    @classmethod
    def from_sources(cls, cli, env):
        cli = dict(cli)
        run_fields = {f.name for f in fields(AgentRunConfig)}
        aliases = {'agent':'AGENT','model':'MODEL','effort':'MODEL_EFFORT','context_file':'CONTEXT_PAYLOAD_FILE',
                   'prompt_file':'PROMPT_FILE','repo_root':'REPO_ROOT','permission_mode':'AGENT_PERMISSION_MODE',
                   'registry_file':'AGENT_REGISTRY_FILE','gui_mode':'GUI_MODE'}
        nested = cli.pop('agent_run', {}) or {}
        agent_values = asdict(nested) if isinstance(nested, AgentRunConfig) else dict(nested)
        runtime_capture = {'final_response_file', 'result_capture_context'}
        for key in runtime_capture:
            agent_values.pop(key, None)
        for key in run_fields - {'environment','state_file'} - runtime_capture:
            value = cli.get(key)
            # Managed environment IDs belong to the current provider, not to
            # a child it dispatches. Let dispatch_worker mint missing child IDs;
            # explicit CLI/config identities remain authoritative for admission.
            parent_identity = key in ('run_id', 'attempt_id') and env.get('RUN_WITH_IT_DISPATCHER_MANAGED') == '1'
            if value is None and key not in agent_values and not parent_identity:
                value = env.get(aliases.get(key, 'RUN_WITH_IT_' + key.upper()))
            if value is not None:
                agent_values[key] = value
        # Worker selectors are independent of coordinator selectors. Prefer
        # explicit worker FORCED settings to deprecated AGENT/MODEL aliases.
        for key in ('agent','model'):
            if cli.get(key) is None and key not in (asdict(nested) if isinstance(nested, AgentRunConfig) else nested) and env.get('FORCED_' + key.upper()):
                agent_values[key] = env['FORCED_' + key.upper()]
        agent_values.setdefault('agent', '')
        agent_values.setdefault('unattended', True)
        for name in ('unattended', 'dispatcher_managed'):
            if isinstance(agent_values.get(name), str):
                agent_values[name] = agent_values[name] == '1'
        agent_values['environment'] = dict(env)
        if isinstance(agent_values.get('heartbeat_seconds'), str):
            agent_values['heartbeat_seconds'] = float(agent_values['heartbeat_seconds'])
        state = cli.get('state_file') or env.get('RUN_WITH_IT_STATE_FILE')
        if not state:
            log = agent_values.get('log_file')
            if not log:
                raise ValueError('--state-file or --log-file is required')
            state = str(Path(log).with_suffix('.state.json'))
        agent_values['state_file'] = state
        values = {'agent_run':AgentRunConfig(**agent_values), 'state_file':Path(state)}
        env_names = {'poll_seconds':'WORKER_POLL_SECONDS','quiet_seconds':'RUN_WITH_IT_WORKER_QUIET_SECONDS',
                     'stall_seconds':'RUN_WITH_IT_WORKER_STALL_SECONDS','timeout_seconds':'RUN_WITH_IT_DISPATCH_TIMEOUT_SECONDS',
                     'hard_limit_seconds':'RUN_WITH_IT_WORKER_HARD_LIMIT_SECONDS','bootstrap_seconds':'RUN_WITH_IT_DETACH_BOOTSTRAP_SECONDS',
                     'tail_lines':'WORKER_LOG_TAIL_LINES'}
        for key in ('detach','poll_seconds','quiet_seconds','stall_seconds','timeout_seconds','hard_limit_seconds','bootstrap_seconds','pre_spawn_head','tail_state_file','tail_lines'):
            value = cli.get(key)
            if value is None:
                value = env.get(env_names.get(key, key.upper()))
            if value is not None:
                if key == 'detach' and isinstance(value, str):
                    normalized = value.strip().lower()
                    if normalized not in ('0', 'false', 'no', 'off', '1', 'true', 'yes', 'on'):
                        raise ValueError('detach must be a boolean')
                    value = normalized in ('1', 'true', 'yes', 'on')
                values[key] = float(value) if key.endswith('_seconds') else int(value) if key=='tail_lines' else value
        # Retry and capture authority are explicit, never inherited from a parent.
        for key in ('execution_mode', 'issue_base_sha', 'retry_contract', 'result_capture'):
            if key in cli:
                values[key] = cli[key]
        return cls(**values)

    def to_record(self):
        # Environment is inherited by the detached process, never persisted.
        value = asdict(self)
        value['agent_run']['environment'] = None
        return json.loads(json.dumps(value, default=str))

    @classmethod
    def from_record(cls, value):
        return cls(**{**value, 'agent_run':AgentRunConfig(**value['agent_run']), 'state_file':Path(value['state_file'])})


@dataclass(frozen=True)
class WorkerResult:
    state: str
    return_code: int
    state_file: Path
    reason: str = ''
    identity: dict | None = None


@dataclass(frozen=True)
class WorkerSnapshot:
    liveness: str
    heartbeat_fresh: bool
    artifact_valid: bool
    artifact_reason: str
    done_present: bool
    current_activity: str
    state: str
    identity: dict | None = None
    log_present: bool = False
    log_tail_changed: bool = False
    tail_hash: str = 'none'


def _optional(path):
    return load_json(Path(path), missing={}) if path else {}


def _process_record(config):
    path = config.agent_run.process_state_file or Path(str(config.state_file) + '.process.json')
    return _optional(path)


def _artifact_args(config, *, from_stall=False):
    run = config.agent_run
    return SimpleNamespace(role=run.role, issue=str(run.issue), result_file=str(run.result_file or ''),
                           done_file=str(run.done_file or ''), log_file=str(run.log_file or ''),
                           issue_dir=str(run.issue_dir or ''), repo_root=str(run.repo_root), pre_spawn_head=config.pre_spawn_head, from_stall=from_stall, worker_config=config)


def expected_artifact_paths(config: WorkerConfig) -> dict[str, str]:
    """Compatibility adapter over the neutral role-path contract."""
    run = config.agent_run
    return role_artifact_paths(role=run.role, result_file=run.result_file, issue_dir=run.issue_dir)


def _paths_collide(first: Path, second: Path) -> bool:
    if first == second or first in second.parents or second in first.parents:
        return True
    try:
        return first.samefile(second)
    except FileNotFoundError:
        return False


def _preflight_paths(config: WorkerConfig) -> dict[str, str]:
    """Reject aliases before the first claim; never modify a previous artifact."""
    run = config.agent_run
    if run.role != 'sub-coord' and run.issue_dir and run.result_file:
        report = (Path(run.issue_dir)/'report.json').resolve()
        if _paths_collide(Path(run.result_file).resolve(), report):
            raise ValueError('worker result path is reserved for sub-coordinator report')
    expected = expected_artifact_paths(config)
    outputs = {name: Path(path) for name, path in expected.items()}
    if run.done_file:
        outputs['done'] = Path(run.done_file).resolve()
    state = Path(config.state_file).resolve()
    runtime = {'state': state, 'config': Path(str(state)+'.config.json'), 'outcome': outcome_path(state),
               'process': Path(run.process_state_file).resolve(),
               'dispatcher_process': Path(str(state)+'.dispatcher.process.json'),
               'dispatcher_log': Path(str(state)+'.dispatcher.log')}
    if config.tail_state_file:
        runtime['tail_state'] = Path(config.tail_state_file).resolve()
    inputs = {name: Path(getattr(run, name)).resolve() for name in ('context_file', 'prompt_file', 'registry_file')
              if getattr(run, name)}
    destinations = {name: Path(getattr(run, name)).resolve() for name in ('events_log', 'status_file', 'log_file')
                    if getattr(run, name)}
    directories = {'streams': Path(run.stream_directory).resolve(),
                   'records': Path(run.record_directory or (Path(run.repo_root)/'.ai-skills'/'processes')).resolve(),
                   'state_lock': Path(str(state)+'.lock')}

    def reject_aliases(left, right, *, same_group=False):
        for name, path in left.items():
            for other, other_path in right.items():
                if same_group and name == other:
                    continue
                if _paths_collide(path, other_path):
                    raise ValueError(f'path collision: {name} and {other}')

    reject_aliases(outputs, outputs, same_group=True)
    reject_aliases(outputs, {**runtime, **inputs, **destinations, **directories})
    reject_aliases(runtime, runtime, same_group=True)
    reject_aliases(runtime, {**inputs, **destinations, **directories})
    reject_aliases(destinations, {**inputs, **directories})
    reject_aliases(directories, inputs)
    reject_aliases({'state_lock': directories['state_lock']},
                   {name: directories[name] for name in ('streams', 'records')})
    exclusive = {**runtime, **{name: path for name, path in outputs.items() if name not in ('plan', 'done')}}
    if run.final_response_file is not None:
        capture = Path(run.final_response_file)
        if capture != directories['streams'] / 'final-response.json':
            raise ValueError('invalid capture path')
        reject_aliases({'capture': capture}, {**outputs, **runtime, **inputs, **destinations,
            'records': directories['records'], 'state_lock': directories['state_lock']})
        exclusive['capture'] = capture
    for name, path in exclusive.items():
        if path.exists() or path.is_symlink():
            raise RuntimeError(f'occupied {name} path prohibits a new attempt: {path}')
    supervisor = ProcessSupervisor(directories['records'])
    for run_id in (run.run_id, run.run_id+'-dispatcher'):
        if supervisor.lookup_attempt(run_id, run.attempt_id) is not None:
            raise RuntimeError('existing supervisor claim prohibits a new attempt')
    return expected


def _artifact_reason(config):
    # Inspection must never synthesize, canonicalize, commit or cherry-pick.
    run = config.agent_run
    if not run.result_file:
        return 'missing-result-artifact'
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
    def invoke():
        try:
            results.append(run_agent(run))
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
        prelaunch_failure=prelaunch, runner_failed=result.return_code != 0)


def _bind_result_capture(config, registry, original_run):
    run = config.agent_run
    agent = registry.get('aliases', {}).get(run.agent, run.agent)
    if (config.result_capture is None or config.execution_mode != 'implement' or run.role != 'impl' or
            agent != 'codex' or not verification.capture_cycle(run.cycle) or not config.pre_spawn_head or
            not git.repo_available(str(run.repo_root))):
        return config
    if registry['agents'][agent].get('invocation', {}).get('prompt_transport') != 'stdin':
        return config
    try:
        if git.current_head(str(run.repo_root)) != config.pre_spawn_head:
            return config
    except (OSError, subprocess.SubprocessError):
        return config
    if not supports_final_response_capture(build_invocation(run, registry)):
        return config
    for path in (original_run.result_file, original_run.stream_directory or run.stream_directory):
        if Path(path).is_symlink():
            raise ValueError('capture paths must not be symlinks')
    contract = {'required_checks': list(config.result_capture['required_checks']), 'review_comment_ids': []}
    identity = verification.capture_identity(run)
    context = {'schema_version': 1, 'source': 'codex-last-message', 'cycle': identity['cycle'],
        **contract, 'envelope': identity}
    run = replace(run, final_response_file=Path(run.stream_directory) / 'final-response.json',
        result_capture_context=context)
    build_invocation(run, registry)
    return replace(config, agent_run=run, result_capture=contract)


def dispatch_worker(config: WorkerConfig) -> WorkerResult:
    # Complete input validation before writing launch state or starting a child.
    registry = load_registry(config.agent_run.registry_file)
    original_run = config.agent_run
    run = replace(original_run, final_response_file=None, result_capture_context=None)
    build_invocation(run, registry)
    if not config.agent_run.result_file:
        raise ValueError('result_file is required')
    state_file = Path(config.state_file).resolve()
    directory = state_file.parent
    directory.mkdir(parents=True, exist_ok=True)
    run = replace(run, state_file=state_file, scrub_ambient_aliases=True, dispatcher_managed=True,
                  process_state_file=config.agent_run.process_state_file or Path(str(state_file)+'.process.json'),
                  stream_directory=config.agent_run.stream_directory or Path(str(state_file)+'.streams'),
                  run_id=config.agent_run.run_id or f'worker-{config.agent_run.issue}-{config.agent_run.role}-{uuid.uuid4().hex}',
                  attempt_id=config.agent_run.attempt_id or uuid.uuid4().hex)
    # Bind the paths used by the provider to the exact paths preflight checks.
    run = replace(run, **{name: Path(getattr(run, name)).resolve() for name in (
        'repo_root', 'context_file', 'prompt_file', 'registry_file', 'issue_dir', 'result_file',
        'done_file', 'log_file', 'events_log', 'status_file', 'process_state_file',
        'stream_directory', 'record_directory') if getattr(run, name) is not None})
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
        expected = _preflight_paths(config)
        write_json_atomic(state_file,{'schema_version':1,'state':'launching','run_id':run.run_id,'attempt_id':run.attempt_id,
            'launcher_identity':{'pid':os.getpid(),'created_at':psutil.Process().create_time(),'host':socket.gethostname()}})
        write_json_atomic(config_path,config.to_record())
        write_json_atomic(outcome_path(state_file),pending_outcome(run_id=run.run_id,attempt_id=run.attempt_id,
            issue=str(run.issue),role=run.role,state_file=state_file,expected_artifacts=expected))
    if not config.detach:
        return _work(config)
    supervisor = ProcessSupervisor(run.record_directory or (Path(run.repo_root)/'.ai-skills'/'processes'))
    root = str(Path(__file__).resolve().parents[1])
    bootstrap = 'import sys;sys.path.insert(0,sys.argv[1]);from ai_skills.dispatch import _detached;raise SystemExit(_detached(sys.argv[2]))'
    output = Path(str(state_file)+'.dispatcher.log')
    owned = supervisor.start(ProcessSpec((sys.executable,'-c',bootstrap,root,str(config_path)),Path(run.repo_root),run.environment or {},output,output),
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


def _provider_record(config, *, allow_supervisor_fallback=True):
    """Read runner process state, with optional fallback for a lost handshake."""
    run = config.agent_run
    supervisor = ProcessSupervisor(run.record_directory or (Path(run.repo_root)/'.ai-skills'/'processes'))
    process = _process_record(config)
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
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
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


def _availability_reason(config):
    process, supervisor = _provider_record(config)
    raw = supervisor.lookup_attempt(config.agent_run.run_id, config.agent_run.attempt_id) or {}
    text = ''
    for key, stream_key in (('stdout_file','stdout'),('stderr_file','stderr')):
        path = process.get(key) or raw.get('spec',{}).get(stream_key)
        if path:
            with Path(path).open('rb') as stream:
                stream.seek(max(0, Path(path).stat().st_size - 65536))
                text += stream.read().decode('utf-8', errors='replace')
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
        {'run_id':run.run_id, 'attempt_id':run.attempt_id, 'issue':run.issue, 'role':run.role, 'reason':reason})


def _capture_bytes(run):
    path = Path(run.final_response_file)
    if path != Path(run.stream_directory) / 'final-response.json' or path.resolve() != path:
        raise ValueError('capture path changed')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('capture is not an independent regular file')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(info, opened) or opened.st_nlink != 1 or path.resolve() != path:
            raise ValueError('capture changed before reading')
        return stream.read()


def _publish_captured_result(config, *, eligible):
    """Recover committed success while the attempt lock excludes other finalizers."""
    run = config.agent_run
    if (config.result_capture is None or config.execution_mode != 'implement' or run.role != 'impl' or
            not run.dispatcher_managed or run.result_capture_context is None):
        return
    path = Path(run.result_file)
    if path.exists() or path.is_symlink():
        return
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
        response = _capture_bytes(run)
    except FileNotFoundError:
        return _capture_rejected(run, 'capture-response-missing')
    except (OSError, ValueError, RuntimeError):
        return _capture_rejected(run, 'capture-path-unsafe')
    reason, payload = verification.captured_result_reason(config, response)
    if reason:
        return _capture_rejected(run, reason)
    if path.resolve() != path:
        return _capture_rejected(run, 'capture-result-path-unsafe')
    if path.exists() or path.is_symlink():
        return
    try:
        write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + '\n', exclusive=True)
    except FileExistsError:
        # A direct file published concurrently has precedence.
        return


def _finalize_attempt(config: WorkerConfig, *, provider_return_code: int | None,
                      reason: str = '', failure_class: str = 'capability',
                      prelaunch_failure: bool = False, runner_failed: bool = False) -> WorkerResult:
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
        if previous is not None and not terminal and run.final_response_file is not None:
            _publish_captured_result(config, eligible=(provider_return_code == 0 and not reason and
                not prelaunch_failure and not runner_failed))
        payload, error = artifacts.load_json(str(run.result_file))
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
            artifact_reason = _artifact_reason(config)
            if artifact_reason and not terminal and not prelaunch_failure:
                _safe_synthesis(config, reason)
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
            write_json_atomic(outcome_path(config.state_file),outcome)
        worker_state = ('completed' if status == 'success' else 'interrupted' if status == 'interrupted'
            else 'artifact-recovery-required' if not typed and code == 75 and reason == 'artifact-recovery-required' else 'failed')
        process, _ = _provider_record(config)
        state.update(state=worker_state,return_code=code,exit_code=code,reason=reason,
            completed_at=outcome['finished_at'],alive=False,provider_identity=process.get('identity'),
            failure_class=failure_class)
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


def adopt_worker(state_file) -> WorkerResult:
    """Reconcile only after provider and dispatcher death; never launch work."""
    state_file = Path(state_file)
    config = WorkerConfig.from_record(load_json(Path(str(state_file)+'.config.json')))
    if canonical_path(config.state_file) != canonical_path(state_file) or (config.agent_run.state_file is not None and canonical_path(config.agent_run.state_file) != canonical_path(state_file)):
        return WorkerResult('unknown',3,state_file,'worker-config-state-binding-mismatch')
    state = load_json(state_file)
    try:
        journal = _read_bound_outcome(config)
        _bound_stop_intent(config,state)
        process, supervisor = _provider_record(config)
        run = config.agent_run
        wrapper = _optional(Path(str(state_file)+'.dispatcher.process.json'))
        if not wrapper.get('identity'):
            raw = supervisor.lookup_attempt(run.run_id+'-dispatcher',run.attempt_id)
            if raw is not None:
                wrapper = {**raw,'record_directory':str(supervisor.directory)}
        if wrapper:
            if not wrapper.get('identity'):
                return WorkerResult('unknown',3,state_file,'dispatcher-launch-identity-unknown')
            identity = ProcessIdentity(**wrapper['identity'])
            if identity.run_id != run.run_id+'-dispatcher' or identity.attempt_id != run.attempt_id:
                raise ValueError('dispatcher identity binding mismatch')
            owner = ProcessSupervisor(Path(wrapper['record_directory'])).inspect(identity)
            if owner.state != 'dead':
                return WorkerResult('running' if owner.state=='alive' else 'unknown',
                    0 if owner.state=='alive' else 3,state_file,'dispatcher-finalizing',process.get('identity'))
        if not process.get('identity'):
            if process:
                return WorkerResult('unknown',3,state_file,'provider-launch-identity-unknown')
            if not wrapper:
                launcher = _launcher_state(state)
                if launcher != 'dead':
                    return WorkerResult('running' if launcher=='alive' else 'unknown',
                        0 if launcher=='alive' else 3,state_file,'launcher-ownership-'+launcher)
                if state.get('state') not in ('launching','starting') and state.get('reason') not in ('dispatch-bootstrap-failed','missing-command','cli-unsupported'):
                    return WorkerResult('unknown',3,state_file,'provider-launch-identity-unknown')
            return _finalize_attempt(config,provider_return_code=None,
                reason=state.get('reason') or 'dispatch-bootstrap-failed',
                failure_class=state.get('failure_class','capability'),prelaunch_failure=True)
        identity = ProcessIdentity(**process['identity'])
        liveness = supervisor.inspect(identity)
        if liveness.state != 'dead':
            return WorkerResult('running' if liveness.state=='alive' else 'unknown',
                0 if liveness.state=='alive' else 3,state_file,liveness.reason,process['identity'])
        if not wrapper and (journal is None or journal['status']=='pending'):
            launcher = _launcher_state(state)
            if launcher != 'dead':
                return WorkerResult('running' if launcher=='alive' else 'unknown',
                    0 if launcher=='alive' else 3,state_file,'launcher-ownership-'+launcher,process['identity'])
        code = supervisor.return_code(identity)
        exit_deadline = time.monotonic()+.5
        while code is None and time.monotonic()<exit_deadline:
            time.sleep(.02)
            code = supervisor.return_code(identity)
        if code is None:
            return WorkerResult('unknown',3,state_file,'provider-exit-status-unknown',process['identity'])
        reason = _availability_reason(config) if code else ''
        return _finalize_attempt(config,provider_return_code=code,reason=reason,
            failure_class='infrastructure' if reason else 'capability')
    except (ValueError,TypeError,KeyError) as error:
        return WorkerResult('unknown',3,state_file,str(error))


def _detached(path):
    config = WorkerConfig.from_record(load_json(Path(path)))
    def interrupt(_number,_frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupt)
    return _work(config).return_code


@dataclass(frozen=True)
class WatchConfig:
    events_log: Path
    pool_state_file: Path
    cursor_file: Path | None = None
    wait_seconds: float = 240
    poll_seconds: float = 10

    def __post_init__(self):
        if not math.isfinite(self.wait_seconds) or self.wait_seconds<0 or not math.isfinite(self.poll_seconds) or self.poll_seconds<=0:
            raise ValueError('watch intervals must be finite; wait nonnegative and poll positive')


@dataclass(frozen=True)
class WatchResult:
    """`running` also covers bounded catchup of a finished pool's event log."""
    result: str
    lines: tuple[str,...]
    return_code: int = 0


def _drain_events(config):
    events = Path(config.events_log)
    cursor_path = Path(config.cursor_file) if config.cursor_file else events.parent/'watch-cursor.json'
    with DirectoryLock(cursor_path):
        cursor = _optional(cursor_path)
        pool = _optional(config.pool_state_file)
        pool_identity = None
        if (pool.get('engine') == 'python' and isinstance(pool.get('host'), str) and pool['host']
                and type(pool.get('pool_pid')) is int and pool['pool_pid'] > 0
                and type(pool.get('pool_created_at')) in (int, float)
                and math.isfinite(pool['pool_created_at']) and pool['pool_created_at'] > 0):
            pool_identity = [pool['host'], pool['pool_pid'], pool['pool_created_at']]
        elif (pool.get('identity') or pool.get('pool_identity')) and pool.get('record_directory'):
            pool_identity = [pool['record_directory'], pool.get('identity') or pool['pool_identity']]
        if not events.exists():
            return (), False, False
        stat = events.stat()
        identity = [stat.st_dev,stat.st_ino]
        offset = cursor.get('offset',0)
        with events.open('rb') as stream:
            prefix_length = cursor.get('prefix_length',min(stat.st_size,256))
            prefix = stream.read(prefix_length)
            digest = hashlib.sha256(prefix).hexdigest()
            if (cursor.get('schema_version')!=1 or cursor.get('identity')!=identity or stat.st_size<offset or
                    cursor.get('prefix_digest') != digest):
                offset=0
                cursor={}
                prefix_length=min(stat.st_size,256)
                stream.seek(0)
                digest=hashlib.sha256(stream.read(prefix_length)).hexdigest()
            stream.seek(offset)
            data=stream.read(1024*1024)
        end=data.rfind(b'\n')+1
        complete=data[:end]
        lines=tuple(line.decode('utf-8',errors='replace').rstrip('\r') for line in complete.splitlines())
        same_pool = pool_identity is not None and cursor.get('pool_identity') == pool_identity
        terminal = bool(same_pool and cursor.get('pool_empty',False))
        if pool.get('engine') == 'python':
            # The event history is shared by successive leases. A running new
            # lease can have unread pool-empty events from its predecessor;
            # only this lease's terminal publication proves it is drained.
            terminal = pool_identity is not None and pool.get('state') == 'pool-empty'
        else:
            terminal = terminal or bool(pool_identity is not None and any(
                line.startswith('STATUS|') and 'type=pool-empty' in line.split('|') for line in lines))
        # A terminal lease can predate this reader by many bounded chunks.
        # Deliver its pending records before exposing completion to callers.
        terminal_backlog = terminal and offset + len(data) < stat.st_size
        terminal = terminal and not terminal_backlog
        write_json_atomic(cursor_path,{'schema_version':1,'identity':identity,'offset':offset+end,
                                      'prefix_length':prefix_length,'prefix_digest':digest,'pool_empty':terminal,
                                      'pool_identity':pool_identity})
        return lines,terminal,terminal_backlog


def _pool_alive(config):
    record = _optional(config.pool_state_file)
    identity = record.get('identity') or record.get('pool_identity')
    if identity and record.get('record_directory'):
        return ProcessSupervisor(Path(record['record_directory'])).inspect(ProcessIdentity(**identity)).state
    if record.get('engine') == 'python' and record.get('state') == 'running' and record.get('host') == socket.gethostname():
        try:
            process = psutil.Process(int(record['pool_pid']))
            if process.create_time() != record['pool_created_at']:
                return 'dead'
            return 'dead' if process.status() == psutil.STATUS_ZOMBIE else 'alive'
        except psutil.NoSuchProcess:
            return 'dead'
        except (psutil.AccessDenied, OSError, ValueError, KeyError, TypeError):
            return 'unknown'
    return 'dead'


def watch_run(config: WatchConfig) -> WatchResult:
    deadline=time.monotonic()+config.wait_seconds
    seen=[]
    while True:
        lines,empty,terminal_backlog=_drain_events(config)
        seen.extend(lines)
        if empty:
            return WatchResult('pool-empty',tuple(seen))
        if terminal_backlog:
            return WatchResult('running',tuple(seen))
        alive=_pool_alive(config)
        if alive!='alive':
            lines,empty,terminal_backlog=_drain_events(config)
            seen.extend(lines)
            if terminal_backlog:
                return WatchResult('running',tuple(seen))
            return WatchResult('pool-empty' if empty else 'pool-dead',tuple(seen),0 if empty else 3)
        remaining=deadline-time.monotonic()
        if remaining<=0:
            return WatchResult('running',tuple(seen))
        time.sleep(min(remaining,config.poll_seconds))


def stop_run(run_root, *, grace_seconds=1, pool_state_file=None):
    root=Path(run_root)
    if not root.is_dir():
        raise ValueError(f'run root not found: {root}')
    # Preflight every published identity before signaling any process. A corrupt
    # or mismatched record never authorizes killing or a replacement launch.
    if not math.isfinite(grace_seconds) or not 0 <= grace_seconds <= 300:
        raise ValueError('grace_seconds must be between 0 and 300')
    def metadata_paths(pattern):
        direct_pattern = '*.json' + pattern[1:] if pattern in ('*.config.json', '*.launch.json') else pattern
        paths = set(root.glob(direct_pattern))
        for name in ('.run-with-it', '.ai-skills', '.pair-colleague'):
            directory = root / name
            if directory.is_dir():
                paths.update(directory.rglob(pattern))
        return paths

    def collect_records():
        records = {}
        claims = set()

        def remember(directory, run_id, attempt_id):
            supervisor = ProcessSupervisor(Path(directory))
            claims.add((supervisor.directory, run_id, attempt_id))

        def worker_claim(value):
            if not isinstance(value, dict):
                raise ValueError('invalid worker launch configuration')
            run = value.get('agent_run', {})
            if not isinstance(run, dict):
                raise ValueError('invalid worker agent configuration')
            if run.get('run_id') and run.get('attempt_id'):
                directory = run.get('record_directory') or Path(run['repo_root']) / '.ai-skills/processes'
                remember(directory, run['run_id'], run['attempt_id'])
                remember(directory, run['run_id'] + '-dispatcher', run['attempt_id'])

        try:
            # Sidecars are useful publication records, but the hashed admission
            # is durable earlier. Preflight both, even when they disagree.
            for path in root.rglob('*.process.json'):
                record = load_json(path)
                identity = ProcessIdentity(**record['identity'])
                supervisor = ProcessSupervisor(Path(record['record_directory']))
                status = supervisor.inspect(identity)
                if status.state == 'unknown':
                    return StopResult('unknown', reason=str(path) + ': ' + status.reason)
                records[(supervisor.directory, identity.run_id, identity.attempt_id)] = (supervisor, identity)
                remember(supervisor.directory, identity.run_id, identity.attempt_id)

            # Recover exact attempt keys from launch intents/configurations. A
            # custom process directory can contain other runs; never sweep it.
            paths = set()
            for pattern in ('*.config.json', '*.launch.json', 'main-state.json', 'pool.state.json'):
                paths.update(metadata_paths(pattern))
            if pool_state_file:
                pool_path = Path(pool_state_file)
                paths.update((pool_path, Path(str(pool_path) + '.config.json'), Path(str(pool_path) + '.launch.json')))
            visited = set()
            while paths:
                path = paths.pop()
                if path in visited or not path.exists():
                    continue
                visited.add(path)
                value = load_json(path)
                if not isinstance(value, dict):
                    raise ValueError('runtime metadata must be an object: ' + str(path))
                worker_claim(value)
                attempts = value.get('pool_attempts', {})
                history = value.get('pool_attempt_history', [])
                if not isinstance(attempts, dict) or not isinstance(history, list):
                    raise ValueError('invalid pool attempt metadata: ' + str(path))
                for attempt in [*attempts.values(), *history]:
                    if not isinstance(attempt, dict):
                        raise ValueError('invalid pool attempt')
                    worker_claim(attempt.get('worker_config', {}))
                if path.name.endswith('.launch.json'):
                    remember(value['record_directory'], value['run_id'], value['attempt_id'])
                pool_config = value.get('config', value)
                if isinstance(pool_config, dict) and pool_config.get('sub_coord_agent') and pool_config.get('state_file'):
                    # A configured authoritative state file may live outside the
                    # run tree, so follow its recorded path rather than scanning
                    # arbitrary project JSON files.
                    paths.add(Path(pool_config['state_file']))

            # The two default directories belong to this run. These cover a
            # provider crash before any sidecar/config publication.
            for directory in (root / '.run-with-it/processes', root / '.ai-skills/processes'):
                if not directory.is_dir():
                    continue
                for raw in ProcessSupervisor(directory).attempt_records():
                    remember(directory, raw['run_id'], raw['attempt_id'])

            for directory, run_id, attempt_id in claims:
                supervisor = ProcessSupervisor(directory)
                raw = supervisor.lookup_attempt(run_id, attempt_id)
                if raw is None:
                    continue  # An intent alone does not prove that start ran.
                identity = ProcessIdentity(**raw['identity'])
                if (identity.run_id, identity.attempt_id, identity.host) != (run_id, attempt_id, raw.get('host')):
                    raise ValueError('admission identity does not match its claim')
                status = supervisor.inspect(identity)
                if status.state == 'unknown':
                    return StopResult('unknown', reason=str(directory) + ': ' + status.reason)
                records[(directory, run_id, attempt_id)] = (supervisor, identity)
        except (OSError, ValueError, KeyError, TypeError) as error:
            return StopResult('unknown', reason='process admission: ' + str(error))
        return list(records.values())
    records=collect_records()
    if isinstance(records,StopResult):
        return records
    # A pool may place its lease anywhere below the run root. Discover only
    # this exact runtime filename, without parsing arbitrary project JSON.
    pool_paths=set()
    excluded={'.git','node_modules','.superpowers','.pytest_cache','.codegraph','__pycache__'}
    def inaccessible(error):
        raise error
    try:
        for directory, children, files in os.walk(root,onerror=inaccessible):
            children[:]=[name for name in children if name not in excluded and not name.startswith('.venv')]
            if 'pool.state.json' in files:
                pool_paths.add(Path(directory)/'pool.state.json')
    except OSError as error:
        return StopResult('unknown',reason='pool lease discovery inaccessible: '+str(error))
    if pool_state_file:
        pool_paths.add(Path(pool_state_file))
    live_pools=[]
    for pool_path in pool_paths:
        lease=_optional(pool_path)
        if lease.get('engine')!='python' or lease.get('state')!='running':
            continue
        if lease.get('host')!=socket.gethostname():
            return StopResult('unknown',reason='pool lease belongs to another host')
        try:
            process=psutil.Process(int(lease['pool_pid']))
            if process.create_time()!=lease['pool_created_at']:
                return StopResult('unknown',reason='pool creation-time identity mismatch')
            if process.status()==psutil.STATUS_ZOMBIE:
                continue
        except psutil.NoSuchProcess:
            continue
        except (OSError,psutil.AccessDenied,ValueError,KeyError,TypeError) as error:
            return StopResult('unknown',reason='pool identity inaccessible: '+str(error))
        live_pools.append((pool_path,lease,process))
    # Halt admission before reaping workers, so a live pool cannot refill slots
    # while shutdown is in progress. Requests target one exact owner lifetime.
    for pool_path,lease,process in live_pools:
        write_json_atomic(Path(str(pool_path)+'.stop'),{key:lease[key] for key in ('pool_pid','pool_created_at','host')})
    for pool_path,lease,process in live_pools:
        deadline=time.monotonic()+grace_seconds+5
        while True:
            latest=_optional(pool_path)
            if latest.get('state') in ('stopped','completed','failed'):
                break
            try:
                if not process.is_running() or process.status()==psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            if time.monotonic()>=deadline:
                return StopResult('unknown',reason='pool has not confirmed admission shutdown')
            time.sleep(.02)
    # A launch already in flight may publish identity while the stop request
    # is acknowledged. Rescan after admission is quiescent before any signals.
    records=collect_records()
    if isinstance(records,StopResult):
        return records
    remaining=[]
    for supervisor,identity in records:
        result=supervisor.stop(identity,grace_seconds=grace_seconds)
        if result.state!='dead':
            return result
        remaining.extend(result.remaining)
    return StopResult('dead',tuple(remaining))


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    action=argv.pop(0) if argv and argv[0] in ('dispatch','inspect','watch','stop','emit') else 'dispatch'
    parser=argparse.ArgumentParser(prog='ai-skills worker '+action)
    parser.add_argument('--config')
    if action=='emit':
        parser.add_argument('--type',required=True)
        parser.add_argument('--fields-file',required=True)
        parser.add_argument('--log-file')
        parser.add_argument('--status-file')
        parser.add_argument('--events-log')
    elif action=='watch':
        parser.add_argument('--events-log',required=True)
        parser.add_argument('--pool-state-file',required=True)
        parser.add_argument('--cursor-file')
        parser.add_argument('--wait-seconds',type=float,default=240)
        parser.add_argument('--poll-seconds',type=float,default=10)
    elif action=='stop':
        parser.add_argument('--run-root',required=True)
        parser.add_argument('--term-wait-seconds',type=float,default=1)
        parser.add_argument('--pool-state-file')
    else:
        for key in ('role','issue','cycle','agent','model','effort','context-file','prompt-file','log-file','done-file','result-file','state-file','repo-root','issue-dir','status-file','events-log','registry-file','permission-mode','gui-mode'):
            parser.add_argument('--'+key)
        for key in ('poll-seconds','quiet-seconds','stall-seconds','timeout-seconds','hard-limit-seconds','bootstrap-seconds'):
            parser.add_argument('--'+key,type=float)
        parser.add_argument('--detach',action='store_true',default=None)
        parser.add_argument('--dry-run',action='store_true')
        parser.add_argument('--validate-only',action='store_true')
        parser.add_argument('--asset-root')
        parser.add_argument('--tail-state-file')
        parser.add_argument('--tail-lines',type=int)
        parser.add_argument('--dispatch-out-file')
    args=parser.parse_args(argv)
    try:
        if action=='emit':
            values=load_json(Path(args.fields_file))
            if not isinstance(values,dict):
                raise ValueError('status fields must be a JSON object')
            EventBus(log_file=args.log_file,status_file=args.status_file,events_log=args.events_log).emit(args.type,values)
            return 0
        if action=='watch':
            result=watch_run(WatchConfig(Path(args.events_log),Path(args.pool_state_file),Path(args.cursor_file) if args.cursor_file else None,args.wait_seconds,args.poll_seconds))
            for line in result.lines:
                print(line)
            print('WATCH|result='+result.result)
            return result.return_code
        if action=='stop':
            result=stop_run(args.run_root,grace_seconds=args.term_wait_seconds,pool_state_file=args.pool_state_file)
            print('STOP|state='+result.state+'|reason='+result.reason)
            return 0 if result.state=='dead' else 3
        values=vars(args).copy()
        if args.config:
            record=load_json(Path(args.config))
            if 'agent_run' in record:
                config=WorkerConfig.from_record(record)
                run_updates={field.name:values[field.name] for field in fields(AgentRunConfig) if values.get(field.name) is not None}
                worker_updates={field.name:values[field.name] for field in fields(WorkerConfig) if field.name!='agent_run' and values.get(field.name) is not None}
                config=replace(config,agent_run=replace(config.agent_run,**run_updates),**worker_updates)
            else:
                record.update({key:value for key,value in values.items() if value is not None and key!='config'})
                config=WorkerConfig.from_sources(record,os.environ)
        else:
            saved=Path(str(args.state_file)+'.config.json') if args.state_file else None
            config=WorkerConfig.from_record(load_json(saved)) if action=='inspect' and saved and saved.is_file() else WorkerConfig.from_sources(values,os.environ)
        if action=='inspect':
            snapshot=inspect_worker(config)
            diagnostic={'pid':(snapshot.identity or {}).get('pid',0),'alive':snapshot.liveness=='alive',
                        'done':snapshot.done_present,'log_present':snapshot.log_present,
                        'log_tail_changed':snapshot.log_tail_changed,'tail_hash':snapshot.tail_hash,
                        'liveness':snapshot.liveness,'artifact_valid':snapshot.artifact_valid,'state':snapshot.state}
            print('WORKER|'+ '|'.join(f'{key}={str(value).lower() if isinstance(value,bool) else value}' for key,value in diagnostic.items()))
            return 0
        if args.dry_run or args.validate_only:
            run = replace(config.agent_run, final_response_file=None, result_capture_context=None)
            spec=build_invocation(run,load_registry(run.registry_file))
            print(json.dumps(list(spec.argv)) if args.dry_run else 'STATUS|type=dispatch-ready')
            return 0
        result=dispatch_worker(config)
        print('DISPATCH|state='+result.state+'|state_file='+str(result.state_file)+'|reason='+result.reason)
        return result.return_code
    except (OSError,ValueError,RuntimeError) as error:
        print('error: '+str(error),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
