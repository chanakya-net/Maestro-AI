"""Bounded event watching, owned process tree shutdown, and worker CLI entrypoint."""
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
    _artifact_args, _optional, _process_record,
    expected_artifact_paths,
)
from ai_skills.dispatch.capture import (
    _artifact_reason, inspect_worker,
    _bound_stop_intent, _provider_record, _read_bound_outcome, _record_stop_intent,
    write_text_atomic,
)
from ai_skills.dispatch.lifecycle import (
    _bind_agy_plan_capture, _detached, _finalize_attempt, _stop_provider, _work,
)
from ai_skills.dispatch.adoption import (
    _admitted_worker, _dispatcher_record, _inspect_dispatcher, _owner_result,
    adopt_worker, dispatch_worker, inspect_recovery_child,
)

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
                if 'effort' in run_updates:
                    run_updates['requested_effort'] = ''
                worker_updates={field.name:values[field.name] for field in fields(WorkerConfig) if field.name!='agent_run' and values.get(field.name) is not None}
                config=replace(config,agent_run=replace(config.agent_run,**run_updates),**worker_updates)
            else:
                if values.get('effort') is not None:
                    record.pop('requested_effort', None)
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
            registry = load_registry(run.registry_file)
            managed = replace(run, dispatcher_managed=True)
            if managed_agy_plan(managed, registry):
                require_agy_plan_profile(registry)
                run = _bind_agy_plan_capture(replace(config, agent_run=managed), registry, run).agent_run
            spec = build_invocation(run, registry)
            print(json.dumps(list(spec.argv)) if args.dry_run else 'STATUS|type=dispatch-ready')
            return 0
        result=dispatch_worker(config)
        print('DISPATCH|state='+result.state+'|state_file='+str(result.state_file)+'|reason='+result.reason)
        return result.return_code
    except (OSError,ValueError,RuntimeError) as error:
        print('error: '+str(error),file=sys.stderr)
        return 2

