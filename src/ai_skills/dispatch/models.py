"""Worker configuration, snapshots, artifact preflight, and worker inspection."""
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


@dataclass(frozen=True)
class WorkerConfig:
    agent_run: AgentRunConfig
    state_file: Path
    detach: bool = False
    poll_seconds: float = .1
    quiet_seconds: float = DEFAULT_QUIET_SECONDS
    stall_seconds: float = DEFAULT_STALL_SECONDS
    timeout_seconds: float = 0
    hard_limit_seconds: float | None = None
    bootstrap_seconds: float = 10
    pre_spawn_head: str = ''
    execution_mode: str = 'implement'
    issue_base_sha: str = ''
    retry_contract: dict | None = None
    tail_state_file: Path | None = None
    tail_lines: int = 5
    result_capture: dict | None = None

    def __post_init__(self):
        if self.hard_limit_seconds is None:
            default_limit = 0 if self.agent_run.role in ('sub-coord', 'merge-recovery') else 7200
            object.__setattr__(self, 'hard_limit_seconds', default_limit)
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
        if nested and cli.get('effort') is not None:
            agent_values['requested_effort'] = ''
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
        if not value['agent_run'].get('requested_effort'):
            value['agent_run'].pop('requested_effort', None)  # additive: older readers stay compatible
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




