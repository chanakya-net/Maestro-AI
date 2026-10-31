"""Durable two-colleague coordinator, using the shared Python agent runner.

No provider invocation is built here. An owned worker calls run_agent; both the
worker and provider publish creation-time identities before work can be retried.
Unknown ownership always blocks spending or termination.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import datetime
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid

from ai_skills import pair_documents as documents
from ai_skills.processes import ProcessIdentity, ProcessSpec, ProcessSupervisor
from ai_skills.resources import resource_path
from ai_skills.storage import DirectoryLock, LockTimeoutError, load_json, write_json_atomic

ENGINE_VERSION = 'ai-skills-pair/1'


class PairError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


@dataclass
class PairConfig:
    task_source: str = ''
    run_dir: Path | None = None
    run_root: Path | None = None
    agents: dict = field(default_factory=dict)
    min_rounds: int = 3
    max_rounds: int = 8
    timeout_seconds: int = 3600
    max_agent_attempts: int = 3
    registry_file: Path | None = None
    prompt_file: Path | None = None
    workspace_root: Path | None = None
    decision_file: Path | None = None
    final_file: Path | None = None
    bind_workspace: Path | None = None
    break_lock: bool = False
    force: bool = False
    allow_unverified_safe: bool = False
    environment: dict | None = None


@dataclass(frozen=True)
class PairResult:
    return_code: int
    values: dict


def _object(path):
    value = load_json(path)
    if not isinstance(value, dict):
        raise PairError(f'expected JSON object: {path}')
    return value


def _positive(value, label):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise PairError(f'{label} must be a positive integer')
    return int(value)


def _grace(value):
    if not str(value).isdigit():
        raise PairError('termination grace must be a nonnegative integer')
    return int(value)


def _verify(path, digest):
    if not digest or not Path(path).is_file() or documents.sha256_file(path) != digest:
        raise PairError(f'artifact changed after this run recorded it: {path}')


def _immutable_copy(source, target):
    payload = Path(source).read_bytes()
    target = Path(target)
    if target.exists() and target.read_bytes() != payload:
        raise PairError(f'a different document is already recorded at {target}')
    if not target.exists():
        documents.write_text_atomic(target, payload.decode('utf-8'))


def _new_round(digest):
    return {'snapshot_sha256': digest, 'agents': {s: {'status': 'pending', 'attempts': 0} for s in ('1', '2')}}


class PairRun:
    def __init__(self, config):
        self.options = config
        self.path = Path(config.run_dir).resolve() if config.run_dir else None
        self.state = None
        self.config = None

    def save(self):
        self.state['updated_at'] = documents.utc_now()
        write_json_atomic(self.path / 'state.json', self.state)

    @property
    def round_dir(self):
        return self.path / 'rounds' / f'round-{self.state["round"]:02d}'

    @property
    def current(self):
        return self.state['rounds'][str(self.state['round'])]

    def values(self, status=None, live=()):
        state = self.state
        d = self.round_dir
        return {'STATUS': status or ('error' if state['phase'] == 'blocked' else state['phase']),
                'RUN_DIR': str(self.path), 'ROUND': state['round'], 'MIN_ROUNDS': state['min_rounds'],
                'MAX_ROUNDS': state['max_rounds'], 'COMPLETED_ROUNDS': state['completed_rounds'],
                'SNAPSHOT': str(d / 'snapshot.md'), 'AGENT_1_OUTPUT': str(d / 'agent-1-output.md'),
                'AGENT_2_OUTPUT': str(d / 'agent-2-output.md'),
                'DECISION_TEMPLATE': str(d / 'coordinator-decision-template.md'),
                'FINAL': str(self.path / 'final-solution.md') if (self.path / 'final-solution.md').exists() else '',
                'WORKSPACE_ROOT': state.get('workspace_root', ''), 'LIVE_AGENTS': ' '.join(live),
                'COMPLETION_REASON': state.get('completion_reason', '')}

    def start(self):
        options = self.options
        task = sys.stdin.read() if options.task_source == '-' else Path(options.task_source).read_text(encoding='utf-8')
        if not task.strip():
            raise PairError('the task is empty; provide the problem to work on')
        minimum = _positive(options.min_rounds, '--min-rounds')
        maximum = _positive(options.max_rounds, '--max-rounds')
        if not 2 <= minimum <= maximum <= 8:
            raise PairError('round bounds must satisfy 2 <= min <= max <= 8')
        timeout = _positive(options.timeout_seconds, '--timeout-seconds')
        cap = _positive(options.max_agent_attempts, 'MAX_AGENT_ATTEMPTS')
        # Explicit values are checked before registry access or directory creation.
        agents = {}
        for slot in ('1', '2'):
            a = dict(options.agents.get(slot, {}))
            missing = [k for k in ('harness', 'model', 'effort') if not a.get(k)]
            if missing:
                raise PairError(f'agent {slot} is missing required configuration: {", ".join(missing)}; values are never guessed')
            a.setdefault('name', f'Agent {slot}')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}', a['name']):
                raise PairError(f'agent {slot} name must be 1-64 letters, digits, spaces, dot, underscore, or hyphen')
            a['effort'] = 'max' if a['effort'] == 'maximum' else a['effort']
            if a['effort'] not in ('low', 'medium', 'high', 'xhigh', 'max'):
                raise PairError(f'agent {slot} effort must be low, medium, high, xhigh, max, or maximum')
            agents[slot] = a
        from ai_skills.runner import load_registry, _agent, _configured, runner_environment, AgentRunConfig
        registry = load_registry(options.registry_file)
        for slot, a in agents.items():
            requested = a['harness']
            a['harness_requested'] = requested
            a['harness'] = registry.get('aliases', {}).get(requested, requested)
            _, entry = _agent(registry, a['harness'])
            if not entry or entry.get('enabled') is False:
                raise PairError(f'agent {slot} harness "{requested}" is unavailable or not registered')
            if a['model'] not in entry.get('model', {}).get('known_models', []):
                raise PairError(f'model "{a["model"]}" is not registered for agent {slot}')
            detection = entry.get('detection', {})
            executable = detection.get('command') or entry.get('invocation', {}).get('command')
            environment = runner_environment(AgentRunConfig(agent=a['harness'], environment=options.environment))
            if not executable or not shutil.which(executable, path=environment.get('PATH')):
                raise PairError(f'agent {slot} harness "{requested}" is not installed or detectable')
            user_config = entry.get('user_model_configuration', {})
            if user_config.get('requires_user_model_config') and user_config.get('skip_when_unconfigured'):
                if not _configured(entry, environment, Path(options.workspace_root or Path.cwd())):
                    raise PairError(f'agent {slot} harness "{requested}" is not configured')
            profile = entry.get('permission_modes', {}).get('read_only')
            if not profile and not options.allow_unverified_safe:
                raise PairError(f'agent {slot} harness "{requested}" has no registered read-only profile')
            a['safety_profile'] = profile or 'unverified'
        prompt = options.prompt_file or Path(str(resource_path('pair-colleague-agent-prompt.md')))
        if not Path(prompt).is_file():
            raise PairError(f'missing colleague prompt: {prompt}')
        workspace = Path(options.workspace_root or Path.cwd()).resolve()
        if not workspace.is_dir():
            raise PairError(f'workspace directory not found: {workspace}')
        root = Path(options.run_root or '.pair-colleague/runs').resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12])
        self.path.mkdir()
        (self.path / 'workers').mkdir()
        (self.path / 'processes').mkdir()
        documents.write_text_atomic(self.path / 'task.md', task.rstrip('\n') + '\n')
        created = documents.utc_now()
        assets = {'prompt_template_sha256': documents.sha256_file(prompt), 'task_sha256': documents.sha256_file(self.path / 'task.md')}
        self.config = {'schema_version': 3, 'engine_version': ENGINE_VERSION, 'created_at': created,
                       'coordinator': 'current invoking agent', 'workspace_root': str(workspace), 'agents': agents,
                       'engine_runtime': {'schema_version': 1, 'engine': 'python', 'engine_version': 1, 'python': os.path.abspath(sys.executable)},
                       'min_rounds': minimum, 'max_rounds': maximum, 'timeout_seconds': timeout,
                       'max_agent_attempts': cap, 'permission_mode': 'safe', 'prompt_template': str(Path(prompt).resolve()),
                       'registry_file': str(Path(options.registry_file).resolve()) if options.registry_file else '', 'assets': assets}
        write_json_atomic(self.path / 'config.json', self.config)
        # Keep the prompt frozen: package upgrades must not change a resumed run.
        documents.write_text_atomic(self.path / 'agent-prompt-template.md', Path(prompt).read_text(encoding='utf-8'))
        self.state = {'schema_version': 3, 'engine_version': ENGINE_VERSION, 'run_id': uuid.uuid4().hex,
                      'host': socket.gethostname(), 'run_dir': str(self.path), 'workspace_root': str(workspace),
                      'phase': 'awaiting-agents', 'round': 1, 'completed_rounds': 0, 'min_rounds': minimum,
                      'max_rounds': maximum, 'timeout_seconds': timeout, 'max_agent_attempts': cap,
                      'completion_reason': '', 'created_at': created, 'assets': assets, 'rounds': {}}
        self.state['rounds']['1'] = _new_round(documents.freeze_snapshot(self.path, 1))
        self.save()
        return PairResult(0, self.values('initialized'))

    def load(self):
        if not self.path or not (self.path / 'state.json').is_file():
            raise PairError('not a pair-colleague run directory (state.json missing); --run-dir is required')
        self.state = _object(self.path / 'state.json')
        self.config = _object(self.path / 'config.json')
        if self.state.get('schema_version') not in (1, 2, 3):
            raise PairError('unsupported pair schema_version')
        engine = self.state.get('engine_version')
        if engine and engine != ENGINE_VERSION:
            raise PairError(f'incompatible pair engine/version: {engine}')
        if self.config.get('engine_version') not in (None, ENGINE_VERSION):
            raise PairError('incompatible pair config engine/version')
        if self.state.get('host') and self.state['host'] != socket.gethostname():
            raise PairError(f'run belongs to another host: {self.state["host"]}; recover it there', 4)
        # Reject Windows paths on POSIX rather than resolving C:\\... inside cwd.
        workspace = self.state.get('workspace_root')
        if workspace and os.name != 'nt' and PureWindowsPath(workspace).drive:
            raise PairError(f'Windows workspace binding cannot be used on this host: {workspace}')

    def import_legacy(self):
        if self.state.get('engine_version'):
            return
        if self.state.get('schema_version') not in (1, 2):
            raise PairError('unsupported legacy pair schema_version')
        # Legacy PID/group records lack creation times. A nonterminal record
        # cannot prove quiescence, even if that numeric PID happens to be gone.
        for r in self.state.get('rounds', {}).values():
            for slot, a in r.get('agents', {}).items():
                if a.get('status') == 'running':
                    raise PairError('active legacy attempt cannot be imported; recover with the original engine', 4)
        for p in self.path.glob('rounds/*/*-attempt-*-run.json'):
            record = _object(p)
            if record.get('state') not in ('finished', 'dead') or record.get('host', socket.gethostname()) != socket.gethostname():
                raise PairError('legacy attempt is active, unknown, or from another host; import refused', 4)
        backup = self.path / 'legacy-backup'
        backup.mkdir(exist_ok=True)
        for name in ('state.json', 'config.json'):
            target = backup / name
            if not target.exists(): shutil.copy2(self.path / name, target)
        self.state.update(schema_version=3, engine_version=ENGINE_VERSION, host=socket.gethostname(), run_id=uuid.uuid4().hex)
        self.config.update(schema_version=3, engine_version=ENGINE_VERSION,
                           engine_runtime={'schema_version': 1, 'engine': 'python',
                                           'engine_version': 1, 'python': os.path.abspath(sys.executable)})
        prompt = self.options.prompt_file or Path(self.config.get('prompt_template', ''))
        if not prompt.is_file(): prompt = Path(str(resource_path('pair-colleague-agent-prompt.md')))
        documents.write_text_atomic(self.path / 'agent-prompt-template.md', prompt.read_text(encoding='utf-8'))
        for number, round_state in self.state.get('rounds', {}).items():
            for slot, agent_state in round_state.get('agents', {}).items():
                prompt_path = Path(documents.round_dir(str(self.path), int(number))) / f'agent-{slot}-prompt.md'
                if prompt_path.exists(): agent_state['prompt_sha256'] = documents.sha256_file(prompt_path)
        self.state.setdefault('assets', {})['task_sha256'] = documents.sha256_file(self.path / 'task.md')
        self.state.setdefault('assets', {})['prompt_template_sha256'] = documents.sha256_file(self.path / 'agent-prompt-template.md')
        write_json_atomic(self.path / 'config.json', self.config)
        self.save()

    def bind_workspace(self):
        workspace = self.state.get('workspace_root')
        requested = self.options.bind_workspace
        if workspace:
            if requested and str(Path(requested).resolve()) != workspace:
                raise PairError(f'this run is already bound to {workspace}; workspace cannot change')
        elif requested:
            workspace = str(Path(requested).resolve())
            if not Path(workspace).is_dir(): raise PairError('--bind-workspace directory not found')
            self.state['workspace_root'] = workspace
            self.save()
        else:
            raise PairError('this run records no workspace root; use --bind-workspace explicitly')
        if not Path(workspace).is_dir(): raise PairError(f'the workspace this run reviews no longer exists: {workspace}')
        return Path(workspace)

    def supervisor(self, kind='processes'):
        return ProcessSupervisor(self.path / kind)

    def prefix(self, slot, attempt=None):
        attempt = attempt or self.current['agents'][slot]['attempts']
        return self.round_dir / f'agent-{slot}-attempt-{attempt:02d}'

    def attempt_record(self, slot):
        return Path(str(self.prefix(slot)) + '-run.json')

    def identities(self, record):
        """Read admission records directly, including pre-handshake crashes."""
        proofs = []
        key = hashlib.sha256(json.dumps([self.state['run_id'], record['attempt_id']]).encode()).hexdigest()
        for kind in ('workers', 'processes'):
            supervisor = self.supervisor(kind)
            path = supervisor.directory / (key + '.json')
            if path.exists():
                data = _object(path).get('identity')
                proofs.append((supervisor, ProcessIdentity(**data) if data else None))
        return proofs

    def probe(self, record):
        if record.get('host') != socket.gethostname(): return 'unknown'
        proofs = self.identities(record)
        states = [supervisor.inspect(identity).state if identity else 'unknown' for supervisor, identity in proofs]
        if 'alive' in states: return 'alive'
        if 'unknown' in states: return 'unknown'
        if states: return 'dead'
        # A manifest allocated before a crash without any admission record
        # proves no worker was admitted. Never infer this from missing output.
        return 'dead' if record.get('state') == 'allocated' else 'unknown'

    def stop(self, record):
        # Stop the detached provider before its worker; the outer worker is not
        # a process-tree parent of the provider's separate ownership session.
        for supervisor, identity in reversed(self.identities(record)):
            if identity is None: raise PairError('attempt ownership unknown; refusing termination', 4)
            stopped = supervisor.stop(identity)
            if stopped.state != 'dead': raise PairError('attempt ownership unknown or still alive; refusing another launch', 4)

    def collect(self, slot, record):
        prefix = self.prefix(slot)
        output = Path(str(prefix) + '-output.md')
        errors = []
        # A worker can die after the provider finished but before committing
        # its result. Recover the actual paid provider's durable exit and raw
        # streams; never turn a lost tailer into another paid invocation.
        if record.get('state') != 'finished':
            key = hashlib.sha256(json.dumps([self.state['run_id'], record['attempt_id']]).encode()).hexdigest()
            admitted = self.path / 'processes' / (key + '.json')
            if admitted.exists():
                provider = _object(admitted)
                if provider.get('identity'):
                    supervisor = self.supervisor()
                    code = supervisor.return_code(ProcessIdentity(**provider['identity']))
                    if code is None:
                        raise PairError('provider exited without durable return code; refusing another launch', 4)
                    record.update(exit_code=code, state='finished', recovered=True)
                    write_json_atomic(self.attempt_record(slot), record)
        raw_output = Path(str(prefix) + '-streams') / 'stdout'
        if raw_output.exists():
            documents.write_text_atomic(output, raw_output.read_text(encoding='utf-8', errors='replace'))
        if record.get('exit_code') == 0 and not record.get('timed_out') and not record.get('interrupted') and output.is_file():
            errors = documents.validate_agent_output(output)
            if not errors:
                for suffix in ('output.md', 'stderr.log', 'run.json'):
                    source = Path(str(prefix) + '-' + suffix)
                    if source.exists(): _immutable_copy(source, self.round_dir / f'agent-{slot}-{suffix}')
                self.current['agents'][slot].update(status='complete', output_sha256=documents.sha256_file(output))
                self.save()
                return
        documents.write_text_atomic(str(prefix) + '-validation.log', '\n'.join(errors) + ('\n' if errors else ''))
        self.current['agents'][slot]['status'] = 'failed'
        self.save()

    def reconcile(self, reap=False):
        live = []
        for slot, a in self.current['agents'].items():
            if a.get('status') == 'complete':
                _verify(self.round_dir / f'agent-{slot}-output.md', a.get('output_sha256'))
                continue
            if a.get('status') != 'running': continue
            path = self.attempt_record(slot)
            if not path.is_file(): raise PairError('attempt manifest missing; ownership unknown; refusing another launch', 4)
            record = _object(path)
            expected_id = f'round-{self.state["round"]}-agent-{slot}-attempt-{a["attempts"]}'
            if record.get('run_id') != self.state['run_id'] or record.get('attempt_id') != expected_id:
                raise PairError('attempt manifest identity mismatch; refusing recovery', 4)
            if record.get('snapshot_sha256') != self.current['snapshot_sha256'] or record.get('workspace_root') != self.state.get('workspace_root'):
                raise PairError('attempt manifest does not match frozen snapshot or workspace')
            status = self.probe(record)
            if status == 'unknown': raise PairError('attempt ownership unknown or from another host; refusing another launch', 4)
            overdue = time.time() > record.get('deadline_epoch', float('inf')) + _grace((self.options.environment or os.environ).get('PAIR_COLLEAGUE_TERMINATION_GRACE_SECONDS', '60'))
            if status == 'alive' and (reap and self.options.force or overdue):
                self.stop(record)
                record.update(state='finished', exit_code=-1, timed_out=overdue, interrupted=not overdue)
                write_json_atomic(path, record)
            elif status == 'alive':
                live.append(slot)
                continue
            self.collect(slot, record)
        return live

    def render_prompt(self, slot):
        output = self.round_dir / f'agent-{slot}-prompt.md'
        if output.exists():
            _verify(output, self.current['agents'][slot].get('prompt_sha256'))
            return output
        a = self.config['agents'][slot]
        other = self.config['agents']['2' if slot == '1' else '1']
        template = self.path / 'agent-prompt-template.md'
        _verify(template, self.state['assets']['prompt_template_sha256'])
        documents.render_template(str(template), str(output), [f'AGENT_NAME={a["name"]}', f'OTHER_AGENT_NAME={other["name"]}', f'ROUND_NUMBER={self.state["round"]}', f'MIN_ROUNDS={self.state["min_rounds"]}', f'MAX_ROUNDS={self.state["max_rounds"]}', f'EFFORT_LEVEL={a["effort"]}'])
        self.current['agents'][slot]['prompt_sha256'] = documents.sha256_file(output)
        return output

    def run_round(self):
        phase = self.state['phase']
        if phase == 'awaiting-coordinator': return PairResult(0, self.values())
        if phase == 'blocked': raise PairError('this run is blocked by unrecoverable agent failures', 3)
        if phase != 'awaiting-agents': raise PairError(f'cannot run a round in phase {phase}')
        workspace = self.bind_workspace()
        _verify(self.path / 'task.md', self.state['assets'].get('task_sha256'))
        _verify(self.round_dir / 'snapshot.md', self.current['snapshot_sha256'])
        live = self.reconcile()
        if live: return PairResult(4, {**self.values(live=live), 'LAUNCH': 'busy'})
        env = self.options.environment or os.environ
        cap = _positive(env.get('MAX_AGENT_ATTEMPTS', self.state['max_agent_attempts']), 'MAX_AGENT_ATTEMPTS')
        pending = [s for s, a in self.current['agents'].items() if a['status'] != 'complete']
        if any(self.current['agents'][s]['attempts'] >= cap for s in pending):
            self.state.update(phase='blocked', completion_reason='blocked'); self.save()
            return PairResult(3, self.values())
        supervisor = self.supervisor('workers')
        for slot in pending:
            a = self.current['agents'][slot]
            a.update(attempts=a['attempts'] + 1, status='running')
            prefix = self.prefix(slot)
            attempt_id = f'round-{self.state["round"]}-agent-{slot}-attempt-{a["attempts"]}'
            prompt = self.render_prompt(slot)
            record = {'schema_version': 1, 'engine_version': ENGINE_VERSION, 'state': 'allocated',
                      'host': socket.gethostname(), 'run_id': self.state['run_id'], 'attempt_id': attempt_id,
                      'round': self.state['round'], 'slot': slot, 'attempt': a['attempts'],
                      'snapshot_sha256': self.current['snapshot_sha256'], 'workspace_root': str(workspace),
                      'deadline_epoch': time.time() + self.state['timeout_seconds'], 'exit_code': None,
                      'timed_out': False, 'interrupted': False, 'provider_state': str(prefix) + '-provider.json'}
            manifest = self.attempt_record(slot)
            write_json_atomic(manifest, record)
            spec = {'record': str(manifest), 'agent': self.config['agents'][slot], 'context': str(self.round_dir / 'snapshot.md'),
                    'prompt': str(prompt), 'registry_file': self.config.get('registry_file', ''),
                    'record_directory': str(self.path / 'processes')}
            spec_file = Path(str(prefix) + '-spec.json')
            write_json_atomic(spec_file, spec)
            self.save()
            root = str(Path(__file__).resolve().parents[1])
            bootstrap = 'import sys;sys.path.insert(0,sys.argv[1]);from ai_skills.pair import _attempt;raise SystemExit(_attempt(sys.argv[2]))'
            # ProcessSupervisor atomically admits this exact attempt ID; a crash
            # during handshake is recovered from its durable admission record.
            supervisor.start(ProcessSpec((sys.executable, '-c', bootstrap, root, str(spec_file)), workspace,
                                         {**env, 'REPO_ROOT': str(workspace)}, Path(str(prefix) + '-output.md'), Path(str(prefix) + '-stderr.log')),
                             run_id=self.state['run_id'], attempt_id=attempt_id)
        # Wait without owning provider process handles; a killed coordinator is
        # resumed by reading the exact same durable manifests.
        while pending:
            live = self.reconcile()
            if not live: break
            time.sleep(.05)
        _verify(self.path / 'task.md', self.state['assets'].get('task_sha256'))
        _verify(self.round_dir / 'snapshot.md', self.current['snapshot_sha256'])
        if all(a['status'] == 'complete' for a in self.current['agents'].values()):
            template = '\n\n'.join('# ' + title + '\n\n<coordinator evidence and assessment>' for title in documents.DECISION_SECTIONS)
            documents.write_text_atomic(self.round_dir / 'coordinator-decision-template.md', template + '\n\nCOORDINATOR_ACTION: CONTINUE\n')
            self.state.update(phase='awaiting-coordinator', completed_rounds=self.state['round']); self.save()
            return PairResult(0, self.values())
        if any(a['status'] != 'complete' and a['attempts'] >= cap for a in self.current['agents'].values()):
            self.state.update(phase='blocked', completion_reason='blocked'); self.save()
            return PairResult(3, self.values())
        return PairResult(1, self.values())

    def decision(self):
        source = self.options.decision_file
        if not source or not Path(source).is_file(): raise PairError('--decision-file is required and must exist')
        phase = self.state['phase']
        target = self.round_dir / 'coordinator-decision.md'
        if phase == 'blocked': raise PairError('this run is blocked; no decision can be recorded', 3)
        if phase == 'awaiting-final' and target.is_file() and target.read_bytes() == Path(source).read_bytes(): return PairResult(0, self.values())
        # A repeated CONTINUE reaches the next round: recognize only identical
        # bytes from its immediately preceding committed decision.
        if phase == 'awaiting-agents' and self.state['round'] > 1:
            previous = Path(documents.round_dir(str(self.path), self.state['round'] - 1)) / 'coordinator-decision.md'
            if previous.exists() and previous.read_bytes() == Path(source).read_bytes(): return PairResult(0, self.values('continue'))
        if phase != 'awaiting-coordinator': raise PairError(f'cannot record a decision in phase {phase}')
        errors = documents.validate_decision(source)
        if errors: raise PairError('coordinator decision contract: ' + '; '.join(errors))
        action, _ = documents.trailing_status(documents.normalize_lines(documents.read_text(source)), 'COORDINATOR_ACTION')
        number = self.state['round']
        if action == 'ACCEPT' and number < self.state['min_rounds']: raise PairError('ACCEPT is not allowed before the minimum completed rounds')
        if action == 'CONTINUE' and number >= self.state['max_rounds']: raise PairError('CONTINUE is invalid at the maximum round')
        _verify(self.path / 'task.md', self.state['assets'].get('task_sha256'))
        _verify(self.round_dir / 'snapshot.md', self.current['snapshot_sha256'])
        for slot, a in self.current['agents'].items(): _verify(self.round_dir / f'agent-{slot}-output.md', a.get('output_sha256'))
        _immutable_copy(source, target)
        self.current.update(decision=action, decision_sha256=documents.sha256_file(target))
        if action == 'ACCEPT':
            self.state.update(phase='awaiting-final', completion_reason='maximum-rounds' if number >= self.state['max_rounds'] else 'coordinator-accepted')
        else:
            digest = documents.freeze_snapshot(self.path, number + 1)
            self.state['rounds'][str(number + 1)] = _new_round(digest)
            self.state.update(phase='awaiting-agents', round=number + 1)
        self.save()
        return PairResult(0, self.values('continue' if action == 'CONTINUE' else None))

    def finalize(self):
        source = self.options.final_file
        if not source or not Path(source).is_file(): raise PairError('--final-file is required and must exist')
        phase = self.state['phase']; target = self.path / 'final-solution.md'
        if phase == 'blocked': raise PairError('blocked run must not produce a conclusive dossier', 3)
        if phase == 'complete':
            _verify(target, self.state.get('final_sha256'))
            if target.read_bytes() == Path(source).read_bytes(): return PairResult(0, self.values())
            raise PairError('this run is already finalized with different content')
        if phase != 'awaiting-final': raise PairError(f'coordinator has not accepted a solution (phase: {phase})')
        _verify(self.path / 'task.md', self.state['assets'].get('task_sha256'))
        expected = {key: str(self.state[key]) for _, key in documents.DOSSIER_RUN_LABELS}
        expected['run_dir'] = str(self.path)
        errors = documents.validate_dossier(source, expected, self.config)
        if errors: raise PairError('final dossier contract: ' + '; '.join(errors))
        for number, r in self.state['rounds'].items():
            d = Path(documents.round_dir(str(self.path), int(number)))
            _verify(d / 'snapshot.md', r['snapshot_sha256'])
            _verify(d / 'coordinator-decision.md', r.get('decision_sha256'))
            for slot, a in r['agents'].items(): _verify(d / f'agent-{slot}-output.md', a.get('output_sha256'))
        _immutable_copy(source, target)
        self.state.update(phase='complete', final_sha256=documents.sha256_file(target)); self.save()
        return PairResult(0, self.values())

    def command(self, command):
        if command == 'start': return self.start()
        self.load()
        if command == 'status':
            live = []
            if self.state.get('engine_version') == ENGINE_VERSION:
                for slot, a in self.current['agents'].items():
                    if a['status'] == 'running':
                        p = self.attempt_record(slot)
                        if not p.exists() or self.probe(_object(p)) != 'dead': live.append(slot)
            return PairResult(0, self.values(live=live))
        # Explicit break is confined to unprovable owners, never live local
        # owners. Quarantine the observed lock so replacements are not removed.
        lock = DirectoryLock(self.path / '.command', timeout_seconds=.05)
        if self.options.break_lock and lock.path.exists():
            observed_stat = lock.path.stat()
            owner = load_json(lock.path / 'owner.json', missing={})
            if isinstance(owner, dict) and owner.get('host') == socket.gethostname() and not DirectoryLock._proven_dead(owner):
                raise PairError('local lock owner is live or unknown; refusing --break-lock', 4)
            for slot, a in self.current['agents'].items():
                if a.get('status') == 'running':
                    p = self.attempt_record(slot)
                    if not p.exists() or self.probe(_object(p)) != 'dead': raise PairError('attempt live or unknown; refusing --break-lock', 4)
            claim = lock.path / '.breaking'
            try:
                claim.mkdir()
            except FileExistsError as error:
                raise PairError('another command is recovering this lock', 4) from error
            try:
                current_stat = lock.path.stat()
                if (current_stat.st_dev, current_stat.st_ino) != (observed_stat.st_dev, observed_stat.st_ino) or load_json(lock.path / 'owner.json', missing={}) != owner:
                    raise PairError('lock owner changed during recovery; retry', 4)
                quarantine = lock.path.with_name(lock.path.name + '.broken.' + uuid.uuid4().hex)
                lock.path.rename(quarantine)
            finally:
                if claim.exists(): claim.rmdir()
        legacy_lock = self.path / '.lock'
        if legacy_lock.exists():
            raise PairError('legacy lock present; recover with original engine before import', 4)
        try:
            with lock:
                # Load under admission lock, never apply a pre-lock snapshot.
                self.load()
                self.import_legacy()
                if command == 'run-round': return self.run_round()
                if command == 'record-decision': return self.decision()
                if command == 'finalize': return self.finalize()
                if command == 'reap':
                    live = self.reconcile(reap=True)
                    return PairResult(4 if live else 0, self.values(live=live))
                raise PairError(f'unknown pair command: {command}')
        except LockTimeoutError as error: raise PairError(f'run is busy; {error}', 4) from error


def pair_command(command: str, config: PairConfig) -> PairResult:
    return PairRun(config).command(command)


def _attempt(spec_path):
    from ai_skills.runner import AgentRunConfig, run_agent
    spec = _object(Path(spec_path)); path = Path(spec['record']); record = _object(path)
    a = spec['agent']
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        config = AgentRunConfig(agent=a['harness'], model=a['model'], effort=a['effort'],
                                context_file=Path(spec['context']), prompt_file=Path(spec['prompt']),
                                repo_root=Path(record['workspace_root']), unattended=True, gui_mode='0', permission_mode='safe' if a.get('safety_profile') != 'unverified' else '',
                                registry_file=Path(spec['registry_file']) if spec['registry_file'] else None,
                                process_state_file=Path(record['provider_state']), run_id=record['run_id'],
                                attempt_id=record['attempt_id'], record_directory=Path(spec['record_directory']),
                                stream_directory=Path(str(path).removesuffix('-run.json') + '-streams'))
        result = run_agent(config)
        record.update(exit_code=result.return_code, state='finished')
    except BaseException as error:
        print(f'pair attempt failed: {type(error).__name__}: {error}', file=sys.stderr)
        record.update(exit_code=2, state='finished', error=str(error))
    write_json_atomic(path, record)
    return record['exit_code']


def parser():
    p = argparse.ArgumentParser(prog='ai-skills pair', description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    for command in ('start', 'run-round', 'record-decision', 'finalize', 'status', 'reap'):
        s = sub.add_parser(command)
        if command == 'start':
            s.add_argument('task_source')
            for slot in ('1', '2'):
                for key in ('name', 'harness', 'model', 'effort'):
                    s.add_argument(f'--agent-{slot}-{key}', default=None)
            for name in ('min-rounds', 'max-rounds', 'timeout-seconds', 'run-root'): s.add_argument('--' + name)
        else: s.add_argument('--run-dir', required=True)
        s.add_argument('--bind-workspace'); s.add_argument('--break-lock', action='store_true')
        s.add_argument('--force', action='store_true'); s.add_argument('--decision-file'); s.add_argument('--final-file')
    return p


def main(argv=None):
    try:
        args = vars(parser().parse_args(argv))
        env = os.environ.copy()
        command = args.pop('command')
        agents = {}
        for slot in ('1', '2'):
            agents[slot] = {key: args.pop(f'agent_{slot}_{key}', None) or env.get(f'AGENT_{slot}_{key.upper()}', f'Agent {slot}' if key == 'name' else '') for key in ('name', 'harness', 'model', 'effort')}
        for name, default in (('min_rounds', 3), ('max_rounds', 8), ('timeout_seconds', 3600), ('run_root', '.pair-colleague/runs')):
            args[name] = args.get(name) or env.get('PAIR_COLLEAGUE_RUN_ROOT' if name == 'run_root' else name.upper(), default)
        assets_dir = env.get('PAIR_COLLEAGUE_ASSETS_DIR')
        # ASSETS_DEST selects an installation containing runtime.json and
        # immutable environments. Its resources belong to the selected package.
        # A complete pre-migration flat asset directory remains importable.
        legacy_assets = Path(env['ASSETS_DEST']) if env.get('ASSETS_DEST') else None
        if not assets_dir and legacy_assets and not (legacy_assets / 'runtime.json').exists():
            if all((legacy_assets / name).is_file() for name in ('agent-registry.json', 'pair-colleague-agent-prompt.md')):
                assets_dir = str(legacy_assets)
        if assets_dir:
            for required in ('agent-registry.json', 'pair-colleague-agent-prompt.md'):
                if not (Path(assets_dir) / required).is_file():
                    raise PairError(f'incomplete asset directory; missing {required}: {assets_dir}')
        args.update(agents=agents, max_agent_attempts=env.get('MAX_AGENT_ATTEMPTS', 3),
                    registry_file=Path(env['AGENT_REGISTRY_FILE']) if env.get('AGENT_REGISTRY_FILE') else Path(assets_dir) / 'agent-registry.json' if assets_dir else None,
                    prompt_file=Path(env['PAIR_COLLEAGUE_PROMPT_FILE']) if env.get('PAIR_COLLEAGUE_PROMPT_FILE') else Path(assets_dir) / 'pair-colleague-agent-prompt.md' if assets_dir else None,
                    allow_unverified_safe=env.get('PAIR_COLLEAGUE_ALLOW_UNVERIFIED_SAFE') == '1', environment=env)
        result = pair_command(command, PairConfig(**args))
        for name, value in result.values.items(): print(f'{name}={value}')
        return result.return_code
    except SystemExit as error: return int(error.code)
    except PairError as error:
        print(f'pair-colleague: error: {error}', file=sys.stderr); return error.code
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        print(f'pair-colleague: error: {error}', file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
