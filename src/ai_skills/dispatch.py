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
import subprocess
import socket

import psutil
import sys
import threading
import time
from types import SimpleNamespace
import uuid

from ai_skills import artifacts, git
from ai_skills.events import EventBus
from ai_skills.processes import ProcessIdentity, ProcessSpec, ProcessSupervisor, StopResult
from ai_skills.runner import AgentRunConfig, AgentRunResult, build_invocation, load_registry, run_agent
from ai_skills.storage import DirectoryLock, load_json, write_json_atomic


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
    tail_state_file: Path | None = None
    tail_lines: int = 5

    def __post_init__(self):
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
        for key in run_fields - {'environment','state_file'}:
            value = cli.get(key)
            if value is None and key not in agent_values:
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
        if isinstance(agent_values.get('unattended'), str):
            agent_values['unattended'] = agent_values['unattended'] == '1'
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
                           issue_dir=str(run.issue_dir or ''), repo_root=str(run.repo_root), pre_spawn_head=config.pre_spawn_head, from_stall=from_stall)


def _artifact_reason(config):
    # Inspection must never synthesize, canonicalize, commit or cherry-pick.
    run = config.agent_run
    if not run.result_file:
        return 'missing-result-artifact'
    payload, error = artifacts.load_json(str(run.result_file))
    if error:
        return 'missing-result-artifact' if error == 'missing' else 'invalid-result-artifact'
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
        result_is_report=report,plan_file_present=not run.issue_dir or (Path(run.issue_dir)/'plan.md').is_file(),review_instructions=instructions)
    return artifacts.validate_artifact(run.role,payload,evidence).reason


def inspect_worker(config: WorkerConfig) -> WorkerSnapshot:
    state = _optional(config.state_file)
    process = _process_record(config)
    identity = process.get('identity')
    liveness = 'dead' if state.get('state') in ('completed','failed','artifact-recovery-required') else 'unknown'
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
        if config.tail_state_file and log_tail_changed:
            write_json_atomic(Path(config.tail_state_file),{'schema_version':1,'tail_hash':tail_hash})
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
                stop = _stop_provider(config)
                if stop.state != 'dead':
                    timeout_reason = 'shutdown-identity-unknown'
                break
            # Admission must not wait for the steady-state observation cadence.
            time.sleep(config.poll_seconds if provider_identity else min(.03, config.poll_seconds))
    except KeyboardInterrupt:
        timeout_reason = 'interrupted'
        _stop_provider(config)
    thread.join(timeout=5)
    if thread.is_alive():
        state, code, reason = 'failed', 3, 'shutdown-identity-unknown'
    else:
        result = results[0] if results else AgentRunResult(1,run.agent,run.model)
        reason = timeout_reason or (errors[0] if errors else '')
        code = 124 if timeout_reason in ('hard-limit-exceeded','dispatch-timeout') else result.return_code
        if timeout_reason == 'interrupted':
            code = 130
        if _artifact_reason(config):
            args = _artifact_args(config, from_stall=bool(timeout_reason))
            synthesized = any(function(args) for function in (artifacts.synthesize_implementation, artifacts.synthesize_review, artifacts.synthesize_complexity))
            if synthesized:
                bus.emit('result-artifact-synthesized', common)
        artifact_reason = _artifact_reason(config)
        reason = reason or artifact_reason
        if artifact_reason == 'artifact-recovery-required' and timeout_reason != 'shutdown-identity-unknown':
            reason = artifact_reason
            state, code = 'artifact-recovery-required', 75
        elif code or reason:
            state, code = 'failed', code or 1
        else:
            state = 'completed'
    process = _process_record(config)
    base.update(state=state,return_code=code,exit_code=code,reason=reason,completed_at=time.time(),
                provider_identity=process.get('identity'),alive=False,
                failure_class='infrastructure' if results and results[0].unavailable_reason else 'capability')
    write_json_atomic(Path(config.state_file),base)
    bus.emit('dispatch-complete' if state=='completed' else 'dispatch-recovery-required' if state=='artifact-recovery-required' else 'dispatch-failed',
             {**common,'status':state,'reason':reason,'result_file':str(run.result_file or '')})
    return WorkerResult(state,code,Path(config.state_file),reason,process.get('identity'))


def dispatch_worker(config: WorkerConfig) -> WorkerResult:
    # Complete input validation before writing launch state or starting a child.
    registry = load_registry(config.agent_run.registry_file)
    build_invocation(config.agent_run, registry)
    if not config.agent_run.result_file:
        raise ValueError('result_file is required')
    state_file = Path(config.state_file).resolve()
    directory = state_file.parent
    directory.mkdir(parents=True, exist_ok=True)
    run = replace(config.agent_run, state_file=state_file, scrub_ambient_aliases=True,
                  process_state_file=config.agent_run.process_state_file or Path(str(state_file)+'.process.json'),
                  stream_directory=config.agent_run.stream_directory or Path(str(state_file)+'.streams'),
                  run_id=config.agent_run.run_id or f'worker-{config.agent_run.issue}-{config.agent_run.role}-{uuid.uuid4().hex}',
                  attempt_id=config.agent_run.attempt_id or uuid.uuid4().hex)
    pre_head = config.pre_spawn_head
    if not pre_head and git.repo_available(str(run.repo_root)):
        try:
            pre_head = git.current_head(str(run.repo_root))
        except (OSError, subprocess.CalledProcessError):
            pre_head = ''
    config = replace(config,agent_run=run,state_file=state_file,pre_spawn_head=pre_head)
    with DirectoryLock(state_file):
        old = _optional(state_file)
        if old:
            raise RuntimeError('existing worker state prohibits relaunch; inspect or create a new attempt')
        write_json_atomic(state_file,{'schema_version':1,'state':'launching','run_id':run.run_id,'attempt_id':run.attempt_id})
    config_path = Path(str(state_file)+'.config.json')
    write_json_atomic(config_path,config.to_record())
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
        if state.get('state') in ('running','quiet','stalled','completed','failed','artifact-recovery-required'):
            return WorkerResult(state['state'],int(state.get('return_code',0)),state_file,state.get('reason',''),state.get('provider_identity'))
        if supervisor.inspect(owned.identity).state=='dead':
            break
        time.sleep(min(.03,config.poll_seconds))
    # Admission may be incomplete. Stop both nested provider and wrapper; never
    # authorize another attempt merely because the handshake timed out.
    _stop_provider(config)
    wrapper_stop = owned.close()
    # Admission may publish while wrapper shutdown is in flight. Recheck the
    # current provider identity before classifying this attempt as retryable.
    provider_stop = _stop_provider(config)
    state = _optional(state_file)
    if wrapper_stop.state != 'dead' or provider_stop.state != 'dead':
        state.update(state='unknown',return_code=3,reason='shutdown-identity-unknown')
        write_json_atomic(state_file,state)
        return WorkerResult('unknown',3,state_file,'shutdown-identity-unknown',state.get('provider_identity'))
    state.update(state='failed',return_code=1,reason='dispatch-bootstrap-failed')
    write_json_atomic(state_file,state)
    return WorkerResult('failed',1,state_file,'dispatch-bootstrap-failed')


def adopt_worker(state_file) -> WorkerResult:
    """Reconcile an admitted attempt after dispatcher loss; never launch work."""
    state_file=Path(state_file)
    config=WorkerConfig.from_record(load_json(Path(str(state_file)+'.config.json')))
    state=load_json(state_file)
    process=_process_record(config)
    if not process.get('identity'):
        if state.get('state') in ('failed','artifact-recovery-required'):
            return WorkerResult(state['state'],int(state.get('return_code',1)),state_file,state.get('reason',''))
        return WorkerResult('unknown',3,state_file,'provider-launch-identity-unknown')
    identity=ProcessIdentity(**process['identity'])
    supervisor=ProcessSupervisor(Path(process['record_directory']))
    liveness=supervisor.inspect(identity)
    if liveness.state!='dead':
        return WorkerResult('running' if liveness.state=='alive' else 'unknown',0 if liveness.state=='alive' else 3,
                            state_file,liveness.reason,process['identity'])
    if state.get('state') in ('completed','failed','artifact-recovery-required'):
        return WorkerResult(state['state'],int(state.get('return_code',1)),state_file,state.get('reason',''),process['identity'])
    wrapper=_optional(Path(str(state_file)+'.dispatcher.process.json'))
    if wrapper.get('identity'):
        owner_status=ProcessSupervisor(Path(wrapper['record_directory'])).inspect(ProcessIdentity(**wrapper['identity']))
        if owner_status.state!='dead':
            return WorkerResult('running' if owner_status.state=='alive' else 'unknown',0 if owner_status.state=='alive' else 3,state_file,'dispatcher-finalizing',process['identity'])
    code=supervisor.return_code(identity)
    exit_deadline=time.monotonic()+.5
    while code is None and time.monotonic()<exit_deadline:
        time.sleep(.02)
        code=supervisor.return_code(identity)
    if code is None:
        return WorkerResult('unknown',3,state_file,'provider-exit-status-unknown',process['identity'])
    run=config.agent_run
    reason=_artifact_reason(config)
    if reason:
        args=_artifact_args(config)
        # A confirmed committed HEAD can be reconstructed after wrapper loss,
        # but adoption never commits an unverified dirty tree.
        if run.role in ('impl','modify') and config.pre_spawn_head and git.repo_available(str(run.repo_root)):
            try:
                args.from_stall=git.current_head(str(run.repo_root))!=config.pre_spawn_head
            except (OSError, subprocess.CalledProcessError):
                args.from_stall=False
        any(function(args) for function in (artifacts.synthesize_implementation,artifacts.synthesize_review,artifacts.synthesize_complexity))
        reason=_artifact_reason(config)
    final='artifact-recovery-required' if reason=='artifact-recovery-required' else 'failed' if code or reason else 'completed'
    code=75 if final=='artifact-recovery-required' else code or (1 if reason else 0)
    with DirectoryLock(state_file):
        latest=load_json(state_file)
        latest.update(state=final,return_code=code,exit_code=code,reason=reason,alive=False,completed_at=time.time(),provider_identity=process['identity'])
        write_json_atomic(state_file,latest)
    EventBus(status_file=run.status_file,events_log=run.events_log,log_file=run.log_file).emit(
        'dispatch-complete' if final=='completed' else 'dispatch-recovery-required' if final=='artifact-recovery-required' else 'dispatch-failed',
        {'issue':run.issue,'role':run.role,'status':final,'reason':reason,'source':'dispatcher-adoption'})
    return WorkerResult(final,code,state_file,reason,process['identity'])


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
            spec=build_invocation(config.agent_run,load_registry(config.agent_run.registry_file))
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
