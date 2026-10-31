"""Polling pool with one owner lease and durable per-issue launch intents.

The main-state transaction records intent before dispatch. Restart reconciles the
same attempt and never treats a missing handshake as permission to duplicate it.
Network calls and process waits occur outside main-state transactions.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields, replace
import json
import math
import os
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace
import uuid

import psutil

from ai_skills import state as state_policy
from ai_skills.dispatch import WorkerConfig, WorkerResult, adopt_worker, dispatch_worker
from ai_skills.events import EventBus
from ai_skills.github import GitHubUpdateRequest, update_github
from ai_skills.processes import ProcessIdentity, ProcessSpec, ProcessSupervisor
from ai_skills.resources import resource_path
from ai_skills.runner import AgentRunConfig
from ai_skills.storage import DirectoryLock, LockTimeoutError, StateStore, load_json, write_json_atomic

ENGINE_VERSION = 1


@dataclass(frozen=True)
class AdmissionDecision:
    issues: tuple[str, ...]
    deferrals: dict[str, str]
    missing_context: tuple[str, ...] = ()


def select_ready(state: dict, *, limit: int) -> AdmissionDecision:
    """Apply the shared dependency and ownership policy in topological order."""
    registry = state.get('issue_registry', {})
    completed = state_policy.completed_issue_numbers(state)
    policy = str(state.get('execution_plan', {}).get('concurrency_policy') or 'permissive').casefold()
    active = [(str(issue), registry.get(str(issue), {})) for issue in state.get('active_pool_issues', [])
              if isinstance(registry.get(str(issue)), dict) and registry[str(issue)].get('status') == 'in_progress']
    selected, missing, deferrals = [], [], {}
    for issue in state.get('execution_plan', {}).get('topo_order', []):
        info = registry.get(str(issue), {})
        if not isinstance(info, dict) or info.get('status') != 'pending':
            continue
        if not state_policy.issue_dependencies_completed(info, completed):
            continue
        context = info.get('context_file') or info.get('sub_coord_context_file')
        if not context or not Path(context).is_file():
            missing.append(str(issue))
            continue
        if len(selected) >= max(limit, 0):
            continue
        conflict = next((other for other, entry in active + selected
                         if not state_policy.issues_can_run_together(info, entry, policy)), None)
        if conflict is not None:
            deferrals[str(issue)] = f'concurrency-conflict-with-{conflict}'
            continue
        selected.append((str(issue), info))
    return AdmissionDecision(tuple(issue for issue, _ in selected), deferrals, tuple(missing))


@dataclass(frozen=True)
class PoolConfig:
    state_file: Path
    run_root: Path | None = None
    parallel_jobs: int | None = None
    forced_agent: str | None = None
    forced_model: str | None = None
    sub_coord_agent: str = 'codex'
    sub_coord_model: str = 'gpt-6.1-sol'
    effort: str = ''
    registry_file: Path | None = None
    prompt_file: Path | None = None
    merge_prompt_file: Path | None = None
    permission_mode: str = ''
    gui_mode: str = '0'
    poll_seconds: float = 10
    heartbeat_seconds: float = 60
    timeout_seconds: float = 3600
    hard_limit_seconds: float = 7200
    quiet_seconds: float = 120
    stall_seconds: float = 600
    bootstrap_seconds: float = 10
    max_recovery_attempts: int = 2
    max_compaction_handoffs: int = 6
    max_worker_wait_seconds: int = 3600
    worker_stale_seconds: int = 600
    max_bootstrap_attempts: int = 3
    status_file: Path | None = None
    events_log: Path | None = None
    main_log: Path | None = None
    pool_state_file: Path | None = None
    record_directory: Path | None = None
    detach: bool = False
    dry_run: bool = False
    validate_only: bool = False
    environment: dict[str, str] | None = None

    def __post_init__(self):
        for name in ('poll_seconds', 'heartbeat_seconds', 'timeout_seconds', 'hard_limit_seconds', 'quiet_seconds', 'stall_seconds', 'bootstrap_seconds'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (name in ('poll_seconds', 'heartbeat_seconds', 'bootstrap_seconds') and value == 0):
                raise ValueError(f'{name} must be finite and nonnegative (poll/heartbeat/bootstrap positive)')
        for name in ('max_recovery_attempts', 'max_compaction_handoffs', 'max_worker_wait_seconds', 'worker_stale_seconds', 'max_bootstrap_attempts'):
            if type(getattr(self, name)) is not int or getattr(self, name) < (1 if name == 'max_bootstrap_attempts' else 0):
                raise ValueError(f'{name} must be a nonnegative integer')
        if self.parallel_jobs is not None and (type(self.parallel_jobs) is not int or self.parallel_jobs < 1):
            raise ValueError('parallel_jobs must be a positive integer')
        for name in ('detach','dry_run','validate_only'):
            if type(getattr(self,name)) is not bool:raise ValueError(f'{name} must be boolean')
        for name in ('forced_agent','forced_model'):
            value=getattr(self,name)
            if value is not None and (not isinstance(value,str) or not value.strip()):raise ValueError(f'{name} must be nonempty')
        if not self.sub_coord_agent or not self.sub_coord_model:
            raise ValueError('sub-coordinator agent and model are required')

    def resolved(self):
        state_file = Path(self.state_file).absolute()
        root = Path(self.run_root).absolute() if self.run_root else (state_file.parent.parent if state_file.parent.name == '.run-with-it' else state_file.parent)
        directory = root / '.run-with-it'
        env=self.environment if self.environment is not None else os.environ
        return replace(self, state_file=state_file, run_root=root,
            forced_agent=self.forced_agent if self.forced_agent is not None else env.get('FORCED_AGENT') or None,
            forced_model=self.forced_model if self.forced_model is not None else env.get('FORCED_MODEL') or None,
            pool_state_file=Path(self.pool_state_file).absolute() if self.pool_state_file else directory/'main'/'pool.state.json',
            status_file=Path(self.status_file).absolute() if self.status_file else directory/'status'/'current.txt',
            events_log=Path(self.events_log).absolute() if self.events_log else directory/'status'/'events.log',
            main_log=Path(self.main_log).absolute() if self.main_log else directory/'main'/'main.log',
            record_directory=Path(self.record_directory).absolute() if self.record_directory else directory/'processes')

    def to_record(self):
        value = asdict(self)
        value.pop('environment')
        return {'schema_version':1, **json.loads(json.dumps(value, default=str))}

    @classmethod
    def from_record(cls, value):
        value = dict(value)
        schema=value.pop('schema_version', None)
        if type(schema) is not int or schema != 1:
            raise ValueError('unsupported pool configuration schema')
        if 'environment' in value:
            raise ValueError('pool records cannot contain an environment dump')
        return cls(**value)

    @classmethod
    def from_sources(cls, cli, env):
        cli = dict(cli)
        aliases = {'forced_agent':('forced_agent','FORCED_AGENT'),'forced_model':('forced_model','FORCED_MODEL'), 'sub_coord_agent':('agent','SUB_COORD_AGENT'), 'sub_coord_model':('model','SUB_COORD_MODEL'),
                   'poll_seconds':('poll_seconds','STATUS_POLL_SECONDS'), 'heartbeat_seconds':('heartbeat_seconds','POOL_HEARTBEAT_SECONDS'),
                   'timeout_seconds':('timeout_seconds','SUB_COORD_TIMEOUT_SECONDS'), 'max_recovery_attempts':('max_recovery_attempts','MAX_SUB_COORD_RECOVERY_ATTEMPTS'),
                   'max_compaction_handoffs':('max_compaction_handoffs','MAX_SUB_COORD_COMPACTION_HANDOFFS'),
                   'max_worker_wait_seconds':('max_worker_wait_seconds','MAX_WORKER_WAIT_SECONDS'), 'worker_stale_seconds':('worker_stale_seconds','RUN_WITH_IT_WORKER_STALE_SECONDS'),
                   'max_bootstrap_attempts':('max_bootstrap_attempts','MAX_SPAWN_BOOTSTRAP_ATTEMPTS')}
        values = {}
        integer = {'parallel_jobs','max_recovery_attempts','max_compaction_handoffs','max_worker_wait_seconds','worker_stale_seconds','max_bootstrap_attempts'}
        for field in fields(cls):
            key = field.name
            if key == 'environment': continue
            alias, env_key = aliases.get(key, (key, 'RUN_WITH_IT_'+key.upper()))
            value = cli.get(key)
            if value is None: value = cli.get(alias)
            if value is None: value = env.get(env_key)
            if value is not None:
                if key in ('detach','dry_run','validate_only') and isinstance(value,str):
                    if value.casefold() not in ('0','1','false','true'):raise ValueError(f'{key} must be boolean')
                    value=value.casefold() in ('1','true')
                values[key] = int(value) if key in integer else float(value) if key.endswith('_seconds') else value
        values.setdefault('state_file', Path.cwd()/'.run-with-it'/'main-state.json')
        values['environment'] = dict(env)
        return cls(**values)


@dataclass(frozen=True)
class PoolResult:
    state: str
    return_code: int = 0
    detail: str = ''
    active: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reconciliation:
    active: dict[str, WorkerResult]
    finished: dict[str, WorkerResult]
    launchable: tuple[str, ...]
    unknown: dict[str, str]


def reconcile_attempts(state: dict, processes: ProcessSupervisor) -> Reconciliation:
    active, finished, pending, unknown = {}, {}, [], {}
    attempts = state.get('pool_attempts', {})
    if not isinstance(attempts, dict):
        raise ValueError('pool_attempts must be an object')
    for issue, intent in attempts.items():
        try:
            if not isinstance(intent, dict): raise ValueError('attempt must be an object')
            if intent.get('status') == 'finalized': continue
            if intent.get('engine_version') != ENGINE_VERSION or intent.get('engine') != 'python':
                raise ValueError('unsupported-attempt-engine')
            worker = WorkerConfig.from_record(intent['worker_config'])
            run = worker.agent_run
            path = Path(worker.state_file)
            provider = processes.lookup_attempt(run.run_id,run.attempt_id)
            wrapper = processes.lookup_attempt(run.run_id+'-dispatcher',run.attempt_id)
            if not path.exists():
                if provider is not None or wrapper is not None:
                    raise ValueError('worker claim missing but durable launch evidence exists')
                pending.append(issue)
                continue
            snapshot = load_json(path)
            if not isinstance(snapshot, dict): raise ValueError('worker state must be an object')
            # Recover publication interrupted after start()'s durable handshake.
            # The supervisor's tagged record, rather than a PID, is authority.
            for raw, metadata in ((provider,Path(run.process_state_file or str(path)+'.process.json')),
                                  (wrapper,Path(str(path)+'.dispatcher.process.json'))):
                if raw is not None and not metadata.exists():
                    if not raw.get('identity'):
                        raise ValueError('durable launch record has no confirmed identity')
                    identity = ProcessIdentity(**raw['identity'])
                    if processes.inspect(identity).state == 'unknown':
                        raise ValueError('discovered process identity is unknown')
                    write_json_atomic(metadata,{'schema_version':1,'identity':raw['identity'],
                        'record_directory':str(processes.directory),'state':raw.get('state')})
            config_path = Path(str(path)+'.config.json')
            if not config_path.exists() and (provider is not None or wrapper is not None):
                write_json_atomic(config_path,worker.to_record())
            if provider is None and wrapper is not None:
                status = processes.inspect(ProcessIdentity(**wrapper['identity']))
                if status.state == 'alive':
                    active[issue] = WorkerResult('running',0,path,'dispatcher-bootstrap-in-progress',wrapper['identity'])
                    continue
                if status.state != 'dead': raise ValueError('dispatcher ownership unknown')
            if snapshot.get('state') in ('completed','failed','artifact-recovery-required') and not snapshot.get('provider_identity'):
                result = WorkerResult(snapshot['state'],int(snapshot.get('return_code',1)),path,snapshot.get('reason',''))
            elif snapshot.get('state') == 'launching' and provider is None and wrapper is None:
                # A dead previous pool persisted admission but never called
                # start(): absence of both exclusive process records proves it.
                write_json_atomic(path,{**snapshot,'state':'failed','return_code':1,'reason':'dispatch-bootstrap-failed'})
                result = WorkerResult('failed',1,path,'dispatch-bootstrap-failed')
            else:
                result = adopt_worker(path)
            if result.state == 'unknown': unknown[issue] = result.reason
            elif result.state in ('running','starting'): active[issue] = result
            else: finished[issue] = result
        except (OSError, ValueError, KeyError, TypeError) as error:
            unknown[issue] = str(error)
    return Reconciliation(active, finished, tuple(pending), unknown)


class PoolSupervisor:
    def __init__(self, config: PoolConfig):
        self.config = config.resolved()
        self.store = StateStore(self.config.state_file)
        self.processes = ProcessSupervisor(self.config.record_directory)
        self.bus = EventBus(status_file=self.config.status_file, events_log=self.config.events_log, log_file=self.config.main_log)
        self.active: dict[str, WorkerConfig] = {}
        self._board = None
        self._waiting = None
        self._last_heartbeat = 0.0
        self._unreachable = None
        self._deferrals = None

    def stop_requested(self):
        request = load_json(Path(str(self.config.pool_state_file)+'.stop'), missing={})
        if not isinstance(request, dict):
            raise ValueError('pool stop request must be an object')
        return (request.get('host') == socket.gethostname() and request.get('pool_pid') == os.getpid()
            and request.get('pool_created_at') == psutil.Process().create_time())

    def emit(self, kind, **fields):
        self.bus.emit(kind, fields)

    def read(self):
        value = load_json(self.config.state_file)
        if not isinstance(value, dict): raise ValueError('main state must be an object')
        return value

    def initialize(self):
        state = self.read()
        engine_path = self.config.run_root/'.run-with-it'/'engine.json'
        engine = load_json(engine_path, missing={})
        if not isinstance(engine,dict):raise ValueError('engine record must be an object')
        if engine and (type(engine.get('schema_version')) is not int or engine.get('schema_version')!=1 or type(engine.get('engine_version')) is not int):
            raise ValueError('unsupported engine record schema')
        if not isinstance(state.get('issue_registry',{}),dict):raise ValueError('issue_registry must be an object')
        active = bool(state.get('active_pool_issues')) or any(isinstance(entry, dict) and entry.get('status') == 'in_progress' for entry in state.get('issue_registry', {}).values())
        if active and (engine.get('engine') != 'python' or not state.get('pool_attempts')):
            return PoolResult('legacy-engine-active', 3, 'active legacy or unidentified attempts require explicit shutdown')
        for issue,entry in state.get('issue_registry',{}).items():
            if isinstance(entry,dict) and entry.get('status')=='in_progress':
                intent=state.get('pool_attempts',{}).get(str(issue))
                if not isinstance(intent,dict) or intent.get('status')=='finalized':
                    return PoolResult('ownership-unknown',3,f'active issue {issue} has no current attempt')
        if engine and (engine.get('engine') != 'python' or engine.get('engine_version') != ENGINE_VERSION):
            if active: return PoolResult('legacy-engine-active',3,'active engine cannot be migrated')
            raise ValueError('unsupported pinned run engine')
        if engine.get('python') and engine['python'] != sys.executable:
            raise ValueError(f"run is pinned to {engine['python']}; use that interpreter")
        if not engine:
            write_json_atomic(engine_path, {'schema_version':1,'engine':'python','engine_version':ENGINE_VERSION,'python':sys.executable})
        def init(value):
            value.setdefault('pool_run_id', 'pool-'+uuid.uuid4().hex)
            value.setdefault('pool_attempts', {})
            value.setdefault('active_pool_issues', [])
        self.store.transaction(init)
        return None

    def ready(self, limit):
        def update(value):
            decision = select_ready(value, limit=limit)
            value['last_admission_deferrals'] = decision.deferrals
            return decision
        decision = self.store.transaction(update)
        if decision.deferrals and decision.deferrals != self._deferrals:
            self.emit('pool-admission-deferred',count=len(decision.deferrals),
                deferrals=','.join(f'{issue}:{reason}' for issue,reason in sorted(decision.deferrals.items())))
        self._deferrals = dict(decision.deferrals)
        return decision

    def worker_environment(self):
        environment = dict(self.config.environment if self.config.environment is not None else os.environ)
        environment['AI_SKILLS_RUN_ROOT'] = str(self.config.run_root)
        for name in ('agent', 'model'):
            selector = getattr(self.config, 'forced_' + name)
            if selector:
                environment['FORCED_' + name.upper()] = selector
        return environment

    def worker(self, issue, *, kind='sub-coord', context=None):
        state = self.read(); entry = state['issue_registry'][str(issue)]
        directory = Path(entry.get('issue_dir') or self.config.run_root/'.run-with-it'/'issues'/str(issue)).absolute()
        attempt = uuid.uuid4().hex
        attempt_dir = directory/'attempts'/f'{kind}-{attempt}'
        result = directory/('merge-recovery-report.json' if kind == 'merge-recovery' else 'report.json')
        prompt = self.config.merge_prompt_file if kind == 'merge-recovery' else self.config.prompt_file
        prompt = Path(prompt) if prompt else Path(str(resource_path('merge-recovery-prompt.md' if kind == 'merge-recovery' else 'sub-coordinator-prompt.md')))
        agent = AgentRunConfig(agent=self.config.sub_coord_agent, model=self.config.sub_coord_model,
            effort=self.config.effort, context_file=Path(context or entry.get('context_file') or entry.get('sub_coord_context_file')),
            prompt_file=prompt, repo_root=self.config.run_root, registry_file=self.config.registry_file,
            permission_mode=self.config.permission_mode,gui_mode=self.config.gui_mode,unattended=True,
            issue=str(issue),role=kind,issue_dir=directory,result_file=result,
            log_file=attempt_dir/'worker.log',done_file=attempt_dir/'worker.done',
            state_file=attempt_dir/'worker.state.json',status_file=self.config.status_file,events_log=self.config.events_log,
            run_id=state['pool_run_id'],attempt_id=attempt,record_directory=self.config.record_directory,
            heartbeat_seconds=self.config.heartbeat_seconds,environment=self.worker_environment())
        return WorkerConfig(agent_run=agent,state_file=attempt_dir/'worker.state.json',detach=True,
            poll_seconds=min(self.config.poll_seconds,.1),quiet_seconds=self.config.quiet_seconds,
            stall_seconds=self.config.stall_seconds,timeout_seconds=self.config.timeout_seconds,
            hard_limit_seconds=self.config.hard_limit_seconds,bootstrap_seconds=self.config.bootstrap_seconds)

    def _intent(self, issue, worker):
        def update(value):
            old = value.setdefault('pool_attempts', {}).get(str(issue))
            if old and old.get('attempt_id') != worker.agent_run.attempt_id:
                value.setdefault('pool_attempt_history', []).append(old)
            value['pool_attempts'][str(issue)] = {
                'engine':'python','engine_version':ENGINE_VERSION,'status':'launch-intent',
                'run_id':worker.agent_run.run_id,'attempt_id':worker.agent_run.attempt_id,
                'worker_config':worker.to_record(),'created_at':time.time()}
            entry = value['issue_registry'][str(issue)]
            entry.update(status='in_progress',issue_dir=str(worker.agent_run.issue_dir),
                report_file=str(worker.agent_run.result_file),context_file=str(worker.agent_run.context_file),
                sub_coord_state_file=str(worker.state_file))
            if str(issue) not in [str(item) for item in value.setdefault('active_pool_issues', [])]:
                value['active_pool_issues'].append(str(issue))
        self.store.transaction(update)

    def _registered(self, issue, worker, result):
        run = worker.agent_run
        def update(value):
            value['pool_attempts'][str(issue)].update(status='registered',identity=result.identity)
            value['issue_registry'][str(issue)].update(status='in_progress',context_file=str(run.context_file),issue_dir=str(run.issue_dir),
                log_file=str(run.log_file),done_file=str(run.done_file),report_file=str(run.result_file),
                sub_coord_state_file=str(worker.state_file),pid=(result.identity or {}).get('pid',0),started_at=int(time.time()))
            if result.reason!='dispatch-bootstrap-failed':
                value['issue_registry'][str(issue)]['spawn_bootstrap_attempts']=0
        self.store.transaction(update)
        self.active[str(issue)] = worker

    def bootstrap_failed(self, issue, worker, detail):
        run=worker.agent_run
        for run_id in (run.run_id,run.run_id+'-dispatcher'):
            record=self.processes.lookup_attempt(run_id,run.attempt_id)
            if record is None:continue
            if record.get('identity'):
                if self.processes.inspect(ProcessIdentity(**record['identity'])).state!='dead':
                    return PoolResult('ownership-unknown',3,'bootstrap ownership not proven dead',tuple(self.active))
            elif record.get('state')!='failed':
                return PoolResult('ownership-unknown',3,'bootstrap handshake incomplete',tuple(self.active))
        def failed(value):
            value['pool_attempts'][str(issue)]['status']='finalized'
            entry=value['issue_registry'][str(issue)]
            count=int(entry.get('spawn_bootstrap_attempts',0))+1
            entry['spawn_bootstrap_attempts']=count
            entry['status']='blocked' if count>=self.config.max_bootstrap_attempts else 'pending'
            if entry['status']=='blocked':
                entry['blocking_reasons']=['sub-coordinator dispatch bootstrap attempts exhausted']
            value['active_pool_issues']=[item for item in value.get('active_pool_issues',[]) if str(item)!=str(issue)]
            return entry['status']=='blocked'
        terminal=self.store.transaction(failed)
        self.active.pop(str(issue),None)
        self.emit('sub-coord-dispatch-bootstrap-failed',issue=issue,reason=detail)
        if terminal:
            write_json_atomic(Path(worker.agent_run.result_file),{'outcome':'blocked',
                'summary':'Sub-coordinator bootstrap attempts exhausted',
                'blocking_reasons':['sub-coordinator dispatch bootstrap attempts exhausted'],
                'verification':{'passed':False,'evidence':detail}})
            self.finalize(issue,worker)
        return None

    def launch(self, issue, worker=None):
        if self.stop_requested():
            return None
        worker = worker or self.worker(issue)
        # Archive shared terminal outputs, preserving sub-state/workers for resume.
        directory = str(worker.agent_run.issue_dir)
        if worker.agent_run.role == 'merge-recovery':
            state_policy.quarantine_artifacts(directory,[str(worker.agent_run.result_file)],'merge-predispatch-'+uuid.uuid4().hex)
        else:
            state_policy.quarantine_artifacts(directory,state_policy.sub_coord_terminal_marker_paths(directory),'predispatch-'+uuid.uuid4().hex)
        if worker is not None:
            self._intent(issue, worker)
        try:
            result = dispatch_worker(worker)
        except (OSError, ValueError, RuntimeError) as error:
            # If any launch evidence exists, only reconciliation may decide the
            # attempt's fate. Failure to handshake never authorizes a duplicate.
            if Path(worker.state_file).exists():
                self.active[str(issue)] = worker
                self.emit('sub-coord-dispatch-bootstrap-failed',issue=issue,reason=str(error))
                return None
            self.active[str(issue)]=worker
            self.bootstrap_failed(issue,worker,str(error))
            return None
        if result.reason=='dispatch-bootstrap-failed' and result.identity is None:
            self.active[str(issue)]=worker
            # Reconciliation on the next tick proves no unknown owner remains.
            return result
        self._registered(issue,worker,result)
        self.emit('merge-recovery' if worker.agent_run.role=='merge-recovery' else 'sub-coord-spawn',issue=issue,
            state='started',pid=(result.identity or {}).get('pid',0),attempt=worker.agent_run.attempt_id,
            agent=worker.agent_run.agent,model=worker.agent_run.model,parallel_jobs=self.parallel_jobs)
        return result

    @property
    def parallel_jobs(self):
        value = self.config.parallel_jobs or self.read().get('execution_plan', {}).get('parallel_jobs', 4)
        if type(value) is not int or value < 1: raise ValueError('parallel_jobs must be a positive integer')
        return value

    def _github(self, issue, outcome, report):
        update_github(GitHubUpdateRequest(str(self.config.state_file),str(self.config.run_root),str(issue),outcome,str(report)))

    def finalize(self, issue, worker):
        args = SimpleNamespace(state_file=str(self.config.state_file),issue=str(issue),report_file=str(worker.agent_run.result_file),quiet=True,pool_attempt_id=worker.agent_run.attempt_id)
        if worker.agent_run.role == 'merge-recovery':
            state_policy.finalize_merge_recovery(args)
        else:
            state_policy.finalize_issue(args)
        self.active.pop(str(issue),None)
        outcome=self.read()['issue_registry'][str(issue)]['status']
        self.emit('sub-coord-complete',issue=issue,outcome=outcome,report_file=worker.agent_run.result_file)
        if outcome == 'merge_recovery':
            self.launch_merge_recovery(issue)
        else:
            self._github(issue,outcome,worker.agent_run.result_file)

    def launch_merge_recovery(self, issue):
        entry=self.read()['issue_registry'][str(issue)]
        directory=Path(entry.get('issue_dir') or self.config.run_root/'.run-with-it'/'issues'/str(issue))
        context=directory/'merge-recovery-context.md'
        state_policy.write_merge_recovery_context(SimpleNamespace(state_file=str(self.config.state_file),issue=str(issue),context_file=str(context),recovery_report_file=str(directory/'merge-recovery-report.json')))
        self.launch(issue,self.worker(issue,kind='merge-recovery',context=context))

    def recover(self, issue, worker):
        entry=self.read()['issue_registry'][str(issue)]
        wait_since=entry.get('worker_wait_since') or time.time()
        self.store.transaction(lambda value:value['issue_registry'][str(issue)].setdefault('worker_wait_since',wait_since))
        args=SimpleNamespace(state_file=str(self.config.state_file),issue=str(issue),report_file=str(worker.agent_run.result_file),
            max_attempts=self.config.max_recovery_attempts,max_compaction_handoffs=self.config.max_compaction_handoffs,
            worker_stale_seconds=self.config.worker_stale_seconds,worker_dead_pid_grace_seconds=60,
            wait_elapsed_seconds=int(time.time()-wait_since),max_wait_seconds=self.config.max_worker_wait_seconds)
        decision=state_policy.sub_coord_recovery_decision(args)
        action,reason=decision['action'],decision['reason']
        if action=='wait_worker':
            signature=(reason,decision.get('worker_state'))
            if entry.get('worker_wait_signature')!=list(signature):
                self.emit('sub-coord-recovery-wait',issue=issue,reason=reason,role=decision.get('worker_role'))
                self.store.transaction(lambda value:value['issue_registry'][str(issue)].update(worker_wait_signature=list(signature)))
            return
        if action=='finalize': self.finalize(issue,worker);return
        if action=='spawn_recovery':
            attempt=decision['recovery_attempt'];directory=Path(worker.agent_run.issue_dir)
            context=directory/f'sub-coordinator-recovery-{attempt}-context.md'
            state_policy.write_sub_coord_recovery_context(SimpleNamespace(state_file=str(self.config.state_file),issue=str(issue),context_file=str(context),attempt=attempt,reason=reason))
            state_policy.mark_sub_coord_recovery_started(SimpleNamespace(state_file=str(self.config.state_file),issue=str(issue),context_file=str(context),attempt=attempt,reason=reason))
            self.store.transaction(lambda value:value['issue_registry'][str(issue)].pop('worker_wait_since',None))
            self.launch(issue,self.worker(issue,context=context))
            return
        write_json_atomic(Path(worker.agent_run.result_file),{'outcome':'blocked','summary':'Sub-coordinator recovery could not continue','blocking_reasons':[reason]})
        state_policy.mark_sub_coord_recovery_dispatch_failed(args)
        self.finalize(issue,worker)

    def board(self):
        state=self.read();completed=state_policy.completed_issue_numbers(state)
        board='; '.join(f'#{issue} {state_policy.issue_stage(entry,completed)}' for issue,entry in state.get('issue_registry',{}).items() if isinstance(entry,dict))
        if board!=self._board:
            self.emit('run-board',board=board);self._board=board
        missing=select_ready(state,limit=0).missing_context
        if missing and missing!=self._waiting:
            self.emit('pool-waiting-context',count=len(missing),issues=','.join(missing))
        self._waiting=missing
        roots=state_policy.unreachable_issue_roots(state)
        unreachable=';'.join(f"{issue}:{','.join(sorted(blame))}" for issue,blame in sorted(roots.items()))
        if unreachable and unreachable!=self._unreachable:
            self.emit('pool-unreachable',count=len(roots),unreachable=unreachable)
        self._unreachable=unreachable
        if time.monotonic()-self._last_heartbeat >= self.config.heartbeat_seconds:
            counts = dict.fromkeys(('total','completed','in_progress','pending','blocked',
                'failed_review','failed_merge','merge_recovery'),0)
            for entry in state.get('issue_registry',{}).values():
                if not isinstance(entry,dict):continue
                counts['total'] += 1
                status = str(entry.get('status') or 'unknown').replace('-','_')
                if status in counts and status != 'total':counts[status] += 1
            self.emit('pool-heartbeat',pool_pid=os.getpid(),active=len(self.active),
                parallel_jobs=self.parallel_jobs,**counts,waiting_context=len(missing))
            self._last_heartbeat=time.monotonic()

    def reconcile(self):
        state=self.read();result=reconcile_attempts(state,self.processes)
        if result.unknown:
            return PoolResult('ownership-unknown',3,json.dumps(result.unknown),tuple(result.active))
        for issue in [*result.active,*result.finished]:
            worker=WorkerConfig.from_record(state['pool_attempts'][issue]['worker_config'])
            self._registered(issue,worker,(result.active|result.finished)[issue])
        for issue in result.launchable:
            worker=WorkerConfig.from_record(state['pool_attempts'][issue]['worker_config'])
            worker=replace(worker,agent_run=replace(worker.agent_run,environment=self.worker_environment()))
            self.launch(issue,worker)
        return None

    def tick(self):
        if self.stop_requested():
            self.emit('pool-stopped', state_file=self.config.state_file)
            return PoolResult('stopped', active=tuple(self.active))
        for issue,worker in list(self.active.items()):
            try: result=adopt_worker(worker.state_file)
            except (OSError,ValueError,KeyError,TypeError) as error:
                return PoolResult('ownership-unknown',3,str(error),tuple(self.active))
            if result.state=='unknown': return PoolResult('ownership-unknown',3,result.reason,tuple(self.active))
            if result.state in ('running','starting'): continue
            if result.reason=='dispatch-bootstrap-failed' and result.identity is None:
                failure=self.bootstrap_failed(issue,worker,result.reason)
                if failure:return failure
                continue
            if worker.agent_run.role=='merge-recovery':
                if result.return_code and state_policy.load_report(str(worker.agent_run.result_file)).get('outcome')=='completed':
                    write_json_atomic(Path(worker.agent_run.result_file),{'outcome':'blocked','blocking_reasons':['merge recovery dispatcher failed']})
                self.finalize(issue,worker)
            else: self.recover(issue,worker)
        if self.stop_requested():
            self.emit('pool-stopped', state_file=self.config.state_file)
            return PoolResult('stopped', active=tuple(self.active))
        # A crash can occur after merge_failed is atomically finalized but
        # before its recovery launch intent exists. Resume that durable stage.
        for issue,entry in self.read().get('issue_registry',{}).items():
            if len(self.active)>=self.parallel_jobs:break
            if isinstance(entry,dict) and entry.get('status')=='merge_recovery' and str(issue) not in self.active:
                self.launch_merge_recovery(str(issue))
        free=max(self.parallel_jobs-len(self.active),0)
        decision=self.ready(free)
        for issue in decision.issues:
            self.launch(issue)
        self.board()
        state=self.read()
        if not self.active and not select_ready(state,limit=self.parallel_jobs).issues and not select_ready(state,limit=0).missing_context:
            self.emit('pool-empty',state_file=self.config.state_file)
            return PoolResult('pool-empty')
        return None

    def run(self):
        process_record=load_json(Path(str(self.config.pool_state_file)+'.process.json'),missing={})
        created=psutil.Process().create_time()
        identity=process_record.get('identity') or {}
        if identity.get('pid')!=os.getpid() or identity.get('created_at')!=created:
            process_record={}
        lease={'schema_version':1,'engine':'python','engine_version':ENGINE_VERSION,'state':'initializing',
            'pool_pid':os.getpid(),'pool_created_at':created,'host':socket.gethostname(),
            'started_at':time.time(),'state_file':str(self.config.state_file),'config':self.config.to_record(),**process_record}
        write_json_atomic(self.config.pool_state_file,lease)
        def finish(result):
            write_json_atomic(self.config.pool_state_file,{**lease,'state':result.state,'return_code':result.return_code,
                'detail':result.detail,'completed_at':time.time()})
            return result
        error=self.initialize()
        if error:return finish(error)
        self.emit('pool-ready',parallel_jobs=self.parallel_jobs,state_file=self.config.state_file)
        if self.config.validate_only:return finish(PoolResult('validated'))
        if self.config.dry_run:
            selected=self.ready(self.parallel_jobs)
            for issue in selected.issues:
                worker=self.worker(issue)
                print(json.dumps(worker.to_record(),sort_keys=True))
            return finish(PoolResult('dry-run'))
        result=self.reconcile()
        if result:return finish(result)
        lease['state']='running'
        write_json_atomic(self.config.pool_state_file,lease)
        while True:
            result=self.tick()
            if result:return finish(result)
            time.sleep(self.config.poll_seconds)


def run_pool(config: PoolConfig) -> PoolResult:
    config=config.resolved()
    if config.detach:return detach_pool(config)
    try:
        with DirectoryLock(config.pool_state_file,timeout_seconds=.1):
            return PoolSupervisor(config).run()
    except LockTimeoutError as error:
        return PoolResult('pool-already-running',2,str(error))


def detach_pool(config):
    config=config.resolved()
    config_path=Path(str(config.pool_state_file)+'.config.json')
    # Serialize concurrent detach admission through a different short-lived lock;
    # the child holds the lifetime lease on pool_state_file itself.
    try:
        with DirectoryLock(Path(str(config.pool_state_file)+'.startup'),timeout_seconds=.1):
            try:
                with DirectoryLock(config.pool_state_file,timeout_seconds=.1):
                    pass
            except LockTimeoutError:
                return PoolResult('pool-already-running',2,'pool owner lease exists')
            write_json_atomic(config_path,replace(config,detach=False).to_record())
            supervisor=ProcessSupervisor(config.record_directory)
            root=str(Path(__file__).resolve().parents[1])
            bootstrap='import sys;sys.path.insert(0,sys.argv[1]);from ai_skills.pool import _detached;raise SystemExit(_detached(sys.argv[2]))'
            run_id='pool-'+uuid.uuid4().hex
            # The admission key must survive a crash before sidecar publication,
            # including when multiple runs share a custom process directory.
            write_json_atomic(Path(str(config.pool_state_file)+'.launch.json'),{
                'schema_version':1,'run_id':run_id,'attempt_id':'supervisor',
                'record_directory':str(supervisor.directory)})
            owned=supervisor.start(ProcessSpec((sys.executable,'-c',bootstrap,root,str(config_path)),config.run_root,
                config.environment or {},config.main_log,config.main_log),run_id=run_id,attempt_id='supervisor')
            metadata={'schema_version':1,'identity':asdict(owned.identity),'record_directory':str(supervisor.directory)}
            write_json_atomic(Path(str(config.pool_state_file)+'.process.json'),metadata)
            deadline=time.monotonic()+config.bootstrap_seconds
            while time.monotonic()<deadline:
                record=load_json(config.pool_state_file,missing={})
                if record.get('pool_pid')==owned.identity.pid and record.get('pool_created_at')==owned.identity.created_at:
                    if record.get('state') in ('running','pool-empty'):
                        return PoolResult('detached',0,active=tuple(record.get('active_pool_issues',())))
                    if record.get('return_code') is not None:
                        return PoolResult(record['state'],int(record['return_code']),record.get('detail',''))
                if supervisor.inspect(owned.identity).state=='dead':break
                time.sleep(.03)
            return PoolResult('bootstrap-unknown',3,'pool startup not confirmed; inspect ownership before retry')
    except LockTimeoutError as error:
        return PoolResult('pool-already-running',2,str(error))


def _detached(path):
    config=PoolConfig.from_record(load_json(Path(path))).resolved()
    deadline=time.monotonic()+config.bootstrap_seconds
    while time.monotonic()<deadline:
        metadata=load_json(Path(str(config.pool_state_file)+'.process.json'),missing={})
        identity=metadata.get('identity') or {}
        if identity.get('pid')==os.getpid() and identity.get('created_at')==psutil.Process().create_time():
            break
        time.sleep(.01)
    return run_pool(config).return_code


def main(argv=None):
    parser=argparse.ArgumentParser(prog='ai-skills runpool')
    parser.add_argument('--config')
    for key in ('state-file','run-root','agent','model','forced-agent','forced-model','effort','registry-file','prompt-file','merge-prompt-file','status-file','events-log','main-log','pool-state-file','record-directory','permission-mode','gui-mode'):
        parser.add_argument('--'+key)
    for key in ('parallel-jobs','max-recovery-attempts','max-compaction-handoffs','max-worker-wait-seconds','worker-stale-seconds','max-bootstrap-attempts'):
        parser.add_argument('--'+key,type=int)
    for key in ('poll-seconds','heartbeat-seconds','timeout-seconds','hard-limit-seconds','quiet-seconds','stall-seconds','bootstrap-seconds'):
        parser.add_argument('--'+key,type=float)
    for key in ('detach','dry-run','validate-only'):parser.add_argument('--'+key,action='store_true',default=None)
    parser.add_argument('--asset-root')
    args=vars(parser.parse_args(argv));record=args.pop('config');args.pop('asset_root',None)
    for old,new in (('agent','sub_coord_agent'),('model','sub_coord_model')):
        value=args.pop(old)
        if value is not None:args[new]=value
    if record:
        saved=PoolConfig.from_record(load_json(Path(record))).to_record();saved.pop('schema_version')
        saved.update({key:value for key,value in args.items() if value is not None});args=saved
    try:
        result=run_pool(PoolConfig.from_sources(args,os.environ))
    except (OSError,ValueError,KeyError,TypeError) as error:
        print(f'runpool: {error}',file=sys.stderr);return 2
    if result.return_code:print(f'runpool: {result.state}: {result.detail}',file=sys.stderr)
    return result.return_code


if __name__=='__main__':
    raise SystemExit(main())
