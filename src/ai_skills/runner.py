"""Registry-backed native provider runner with durable process ownership.

Provider output goes to independent files, drained incrementally in bounded
chunks. No pipe can deadlock a provider and no background heartbeat survives
this owner. Batch files are never evaluated: recognized npm shims are adapted
to their node entrypoint, preserving every argument through native argv.
"""
from __future__ import annotations

import argparse
import codecs
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from ai_skills.effort import resolve_effort
from ai_skills.events import EventBus, normalize
from ai_skills.processes import ProcessSpec, ProcessSupervisor
from ai_skills.provider_capture import (
    AGY, CLAUDE, CODEX, OPENCODE, OPENCODE_VERSION, STREAM_SOURCES, ProviderError, agy_result_response, extract_response,
)
from ai_skills.resources import resource_path
from ai_skills.registry import load_registry
from ai_skills.outcomes import canonical_path, role_artifact_paths
from ai_skills.storage import write_json_atomic


@dataclass(frozen=True)
class AgentRunConfig:
    agent: str
    model: str = ''
    effort: str = ''
    context_file: Path | None = None
    prompt_file: Path | None = None
    repo_root: Path = field(default_factory=Path.cwd)
    permission_mode: str = ''
    gui_mode: str = 'auto'
    unattended: bool = False
    status_file: Path | None = None
    events_log: Path | None = None
    log_file: Path | None = None
    done_file: Path | None = None
    state_file: Path | None = None
    process_state_file: Path | None = None
    result_file: Path | None = None
    registry_file: Path | None = None
    issue_dir: Path | None = None
    role: str = 'agent'
    issue: str = 'unknown'
    cycle: str = ''
    environment: dict[str, str] | None = None
    extra_args: tuple[str, ...] = ()
    heartbeat_seconds: float = 30
    run_id: str = ''
    attempt_id: str = ''
    record_directory: Path | None = None
    stream_directory: Path | None = None
    scrub_ambient_aliases: bool = False
    dispatcher_managed: bool = False
    verification_context: dict | None = None
    final_response_file: Path | None = None
    result_capture_context: dict | None = None
    requested_effort: str = ''

    def replace(self, **values):
        return replace(self, **values)


@dataclass(frozen=True)
class AgentRunResult:
    return_code: int
    agent: str
    model: str
    unavailable_reason: str = ''


def _agent(registry, name):
    name = registry.get('aliases', {}).get(name, name)
    if name not in registry['agents']:
        raise ValueError(f'unknown agent: {name}')
    agent = registry['agents'][name]
    if name == 'github-copilot' or agent.get('routing_disabled'):
        raise ValueError(f'agent is disabled: {name} ({agent.get("routing_disabled_reason", "blocked from automatic routing and direct use")})')
    return name, agent


def runner_environment(config):
    env = dict(os.environ if config.environment is None else config.environment)
    if env.get('RUN_AGENT_BOOTSTRAP_PATH', '1') != '0':
        home = Path(env.get('HOME') or env.get('USERPROFILE') or Path.home())
        directories = ['/opt/homebrew/bin', '/usr/local/bin', *(str(home / p) for p in ('.npm-global/bin', '.local/bin', '.cargo/bin', '.bun/bin', '.dotnet/tools'))]
        current = env.get('PATH', os.defpath).split(os.pathsep)
        for directory in directories:
            if Path(directory).is_dir() and directory not in current:
                current.insert(0, directory)
        env['PATH'] = os.pathsep.join(current)
    for key, value in {'REPO_ROOT':config.repo_root, 'RUN_WITH_IT_STATUS_FILE':config.status_file,
                       'RUN_WITH_IT_EVENTS_LOG':config.events_log, 'RUN_WITH_IT_LOG_FILE':config.log_file,
                       'RUN_WITH_IT_DONE_FILE':config.done_file, 'RUN_WITH_IT_RESULT_FILE':config.result_file,
                       'RUN_WITH_IT_STATE_FILE':config.state_file, 'RUN_WITH_IT_ISSUE_DIR':config.issue_dir,
                       'RUN_WITH_IT_ROLE':config.role, 'RUN_WITH_IT_ISSUE':config.issue, 'RUN_WITH_IT_CYCLE':config.cycle,
                       'RUN_WITH_IT_RUN_ID':config.run_id, 'RUN_WITH_IT_ATTEMPT_ID':config.attempt_id,
                       'RUN_WITH_IT_DISPATCHER_MANAGED':'1' if config.dispatcher_managed else '0'}.items():
        if value is not None:
            env[key] = str(value)
    if config.dispatcher_managed and config.role == 'review' and config.result_file:
        from ai_skills.artifacts import review_instructions_file
        env['REVIEWER_STATUS_FILE'] = str(config.result_file)
        env['REVIEWER_INSTRUCTIONS_FILE'] = review_instructions_file(str(config.result_file))
    return env


def _gui(mode, env):
    mode = str(mode).lower()
    if mode == 'auto':
        return any(env.get(key) for key in ('VSCODE_PID', 'ELECTRON_RUN_AS_NODE', 'ANTIGRAVITY_APP', 'CURSOR_TRACE_ID', 'CLAUDE_CODE_ENTRYPOINT')) or env.get('TERM_PROGRAM') == 'vscode'
    if mode in ('1', 'true', 'yes', 'on'):
        return True
    if mode in ('0', 'false', 'no', 'off'):
        return False
    raise ValueError('GUI_MODE must be auto, 1, or 0')


def _payload(config):
    if not config.context_file:
        raise ValueError('context payload file is required. Pass --context-file or set CONTEXT_PAYLOAD_FILE.')
    if not config.prompt_file:
        raise ValueError('prompt file is required')
    context = Path(config.context_file).read_text(encoding='utf-8')
    prompt = Path(config.prompt_file).read_text(encoding='utf-8')
    payload = (context + '\nInstructions:\n\n' + prompt + '\n').rstrip('\n')
    if config.dispatcher_managed:
        metadata = {'run_id':config.run_id, 'attempt_id':config.attempt_id,
            'issue':str(config.issue), 'role':config.role,
            'state_file':canonical_path(config.state_file),
            'expected_artifacts':role_artifact_paths(role=config.role,
                result_file=config.result_file, issue_dir=config.issue_dir)}
        if config.verification_context is not None:
            metadata['verification_contract'] = config.verification_context
        if config.result_capture_context is not None:
            metadata['result_capture'] = config.result_capture_context
        protocol = resource_path('artifact-protocol.md').read_text(encoding='utf-8')
        payload += '\n\n' + protocol.rstrip('\n') + '\n\nAuthoritative attempt metadata:\n'
        payload += json.dumps(metadata, ensure_ascii=False, sort_keys=True) + '\n\n'
    return payload.rstrip('\n')


def _template_tokens(template, substitutions):
    # Tokenize templates before substitution, so payload/model values never split.
    tokens = shlex.split(template) if isinstance(template, str) else list(template)
    return tuple(_render(token, substitutions) for token in tokens)


def _render(text, substitutions):
    for key, value in substitutions.items():
        text = text.replace('{{' + key + '}}', str(value))
    return text


_CAPTURE_ROUTES = {'codex': (CODEX, 'stdin'), 'claude': (CLAUDE, 'stdin'),
                   'agy': (AGY, 'agy-stream-json'), 'opencode': (OPENCODE, 'stdin')}


def capture_source(config, name=None):
    if (not config.dispatcher_managed or config.role not in ('impl', 'modify') or
            config.verification_context is not None or config.result_capture_context is None):
        return None
    source = config.result_capture_context.get('source', CODEX)
    if not isinstance(source, str) or (name is not None and source != _CAPTURE_ROUTES.get(name, (None, None))[0]):
        return None
    if source == CODEX and config.final_response_file is not None:
        return source
    if source in STREAM_SOURCES and config.final_response_file is None:
        return source
    return None


def capture_candidate(config, registry, spec):
    name, agent = _agent(registry, config.agent)
    source, transport = _CAPTURE_ROUTES.get(name, (None, None))
    if source and agent.get('invocation', {}).get('prompt_transport') == transport:
        if name != 'opencode' or _configured(agent, spec.env, spec.cwd):
            return source
    return None


def effective_config(config: AgentRunConfig, registry: dict) -> AgentRunConfig:
    """Apply the shared effort policy once, keeping an adjusted explicit request."""
    name, agent = _agent(registry, config.agent)
    model = config.model or agent.get('model', {}).get('default', '')
    configured = registry.get('model_catalog', {}).get(model, {}).get('reasoning_effort', '')
    if config.effort:
        resolution = resolve_effort(name, config.effort, config.role)
        requested = config.requested_effort or resolution.requested
    else:
        resolution = resolve_effort(name, configured, config.role)
        if name == 'claude' and config.role == 'plan':
            resolution = resolve_effort(name, '', config.role)
        requested = config.requested_effort
    return config.replace(effort=resolution.effective, requested_effort=requested)


def build_invocation(config: AgentRunConfig, registry: dict) -> ProcessSpec:
    name, agent = _agent(registry, config.agent)
    config = effective_config(config, registry)
    if name == 'agy':
        _reject_agy_model_overrides(config.extra_args)
    source = capture_source(config, name)
    if not source:
        config = config.replace(final_response_file=None, result_capture_context=None)
    root = Path(config.repo_root).resolve()
    if not root.is_dir():
        raise ValueError(f'repo root not found: {root}')
    payload = _payload(config)
    env = runner_environment(config)
    gui = _gui(config.gui_mode, env)
    mode = config.permission_mode or agent.get('permission_modes', {}).get('default', '')
    if mode == 'safe':
        mode = agent.get('permission_modes', {}).get('read_only', '')
        if not mode:
            raise ValueError(f'agent {name} has no registered read-only permission mode; --permission-mode safe cannot be honoured')
    if gui:
        defaults = {'codex':('--dangerously-bypass-approvals-and-sandbox', '--sandbox=workspace-write'),
                    'claude':('--dangerously-skip-permissions', '--permission-mode=acceptEdits'),
                    'agy':('--dangerously-skip-permissions', '--sandbox')}
        if name in defaults and mode in ('', defaults[name][0]):
            mode = defaults[name][1]
    if mode and not (config.unattended or gui):
        raise ValueError('unattended permission mode requires --unattended or UNATTENDED=1')
    model = config.model or agent.get('model', {}).get('default', '')
    effort = config.effort
    model_flag = _template_tokens(agent.get('model', {}).get('flag_template', ''), {'model':model}) if model else ()
    settings = ()
    if effort:
        if name == 'codex':
            settings = ('-c', 'model_reasoning_effort=' + effort)
        elif name == 'claude':
            settings = ('--effort', effort)
    invocation = agent.get('invocation', {})
    transport = invocation.get('prompt_transport', 'argv')
    if transport not in ('argv', 'stdin', 'agy-stream-json'):
        raise ValueError(f'unsupported prompt transport: {transport}')
    input_bytes = None
    if transport == 'stdin':
        input_bytes = payload.encode('utf-8')
    elif transport == 'agy-stream-json':
        input_bytes = (json.dumps({'event':'user', 'message':{'content':payload}}, ensure_ascii=False) + '\n').encode('utf-8')
    command = invocation.get('command')
    if not command:
        raise ValueError(f'agent has no invocation command: {name}')
    expansions = {'permission_mode':_template_tokens(mode, {}), 'model_flag':model_flag,
                  'extra_args':config.extra_args, 'model_settings':settings}
    substitutions = {'repo_root':root, 'prompt':payload if transport == 'argv' else '', 'permission_mode':mode,
                     'model_flag':' '.join(model_flag), 'extra_args':' '.join(config.extra_args), 'model_settings':' '.join(settings)}
    argv = [command]
    for token in invocation.get('args_template', []):
        key = token[2:-2] if token.startswith('{{') and token.endswith('}}') else ''
        if key in expansions:
            argv.extend(expansions[key])
        else:
            argv.append(_render(token, substitutions))
    if source == CODEX:
        _add_capture_flag(argv, transport, config.final_response_file)
    elif source:
        _stream_capture_args(argv, transport, source)
    return ProcessSpec(tuple(argv), root, env, unset_env=('AGENT','MODEL') if config.scrub_ambient_aliases else (), stdin=input_bytes)


def _reject_agy_model_overrides(extra_args):
    if any(arg.partition('=')[0] in ('--model', '-m') for arg in extra_args):
        raise ValueError('AGY extra arguments cannot override the maintained --model selection')


def _add_capture_flag(argv, transport, path):
    if transport != 'stdin' or argv[-1] != '-':
        raise ValueError('Codex capture requires a stdin invocation')
    for arg in argv[1:-1]:
        option = arg.partition('=')[0]
        if (option in ('--', '--output-last-message') or
                (option.startswith('-') and not option.startswith('--') and 'o' in option[1:])):
            raise ValueError('Codex capture conflicts with a provider argument')
    argv[-1:-1] = ['--output-last-message', str(path)]


def _stream_capture_args(argv, transport, source):
    if source == CLAUDE:
        if transport != 'stdin' or argv[-1] != '--print':
            raise ValueError('Claude capture requires print mode with stdin')
        _reject_capture_options(argv[1:], '--output-format', '--input-format',
                                '--include-partial-messages', '--replay-user-messages')
        argv.extend(('--output-format', 'json'))
    elif source == AGY:
        if transport != 'agy-stream-json' or argv[-4:] != [
                '--input-format', 'stream-json', '--output-format', 'stream-json']:
            raise ValueError('Agy capture requires stream-json input and output')
        _reject_capture_options(argv[1:-4], '--input-format', '--output-format',
                                '-p', '--print', '--prompt', '-i', '--prompt-interactive')
    elif source == OPENCODE:
        if transport != 'stdin' or argv[1:2] != ['run']:
            raise ValueError('OpenCode capture requires a run invocation with stdin')
        _reject_capture_options(argv[2:], '--format', '--interactive', '-i', '--command', '--demo')
        argv.extend(('--format', 'json'))


def _reject_capture_options(args, *flags):
    if any(arg.partition('=')[0] in (*flags, '--') for arg in args):
        raise ValueError('capture conflicts with a provider argument')


def resolve_executable(argv, environment, *, windows=None):
    windows = os.name == 'nt' if windows is None else windows
    executable = shutil.which(argv[0], path=environment.get('PATH'))
    if executable is None and Path(argv[0]).is_file():
        executable = str(Path(argv[0]).resolve())
    if executable is None:
        raise FileNotFoundError(f'missing command: {argv[0]}')
    if not windows or Path(executable).suffix.lower() not in ('.cmd', '.bat'):
        return (executable, *argv[1:])
    # npm-generated shims invoke a fixed JS entrypoint with %*. Adapt only this
    # known pattern rather than passing untrusted payload through cmd.exe.
    shim = Path(executable)
    text = shim.read_text(encoding='utf-8-sig')
    matches = re.findall(r'"(?:%~dp0|%dp0%)[\\/]([^"\r\n]+\.(?:js|cjs|mjs))"\s+%\*', text, re.IGNORECASE)
    if not matches:
        # npm's own npm.cmd/npx.cmd store their bundled CLI in a variable.
        # Resolve only that literal relative path, never evaluate batch code.
        invocation = re.search(r'^"%NODE_EXE%" "%((?:NPM|NPX)_CLI_JS)%" %\*\s*$',
                               text, re.IGNORECASE | re.MULTILINE)
        if invocation:
            matches = re.findall(r'^SET "' + re.escape(invocation[1])
                                 + r'=%~dp0[\\/]([^"\r\n]+\.(?:js|cjs|mjs))"\s*$',
                                 text, re.IGNORECASE | re.MULTILINE)
    if len(set(matches)) != 1 or not re.search(r'(?:node(?:\.exe)?|%_prog%)"?', text, re.IGNORECASE):
        raise ValueError(f'unsupported batch executable {executable}; install a native executable or standard npm node shim')
    entry = (shim.parent / Path(matches[0].replace('\\', '/'))).resolve()
    if not entry.is_file():
        raise ValueError(f'npm shim entrypoint missing: {entry}')
    local_node = shim.parent / 'node.exe'
    node = str(local_node) if local_node.is_file() else shutil.which('node', path=environment.get('PATH'))
    if not node:
        raise FileNotFoundError('missing command: node (required by npm shim)')
    return (node, str(entry), *argv[1:])


def _capture_probe(prefix, spec, env, *args):
    with tempfile.TemporaryFile() as output:
        result = subprocess.run((*prefix, *args), cwd=spec.cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, timeout=10)
        output.seek(0)
        return output.read(65536) if result.returncode == 0 else b''


def _help_option(help_text, flag):
    pattern = (rb'^[ \t]*(?:-\w,[ \t]+)?' + flag + rb'(?=[\s,]|=<|$)'
               rb'[^\r\n]*(?:\r?\n[ \t]+[^\s-][^\r\n]*)*')
    match = re.search(pattern, help_text, re.MULTILINE)
    return match[0] if match else b''


def supports_final_response_capture(spec: ProcessSpec, source=CODEX) -> bool:
    """Probe only the resolved executable prefix, without task arguments or input."""
    subcommand, flag = {CODEX: (('exec',), b'--output-last-message'),
                        CLAUDE: ((), b'--output-format'),
                        AGY: ((), b'--output-format'),
                        OPENCODE: (('run',), b'--format')}[source]
    try:
        argv = resolve_executable(spec.argv, spec.env)
        prefix = argv[:len(argv) - len(spec.argv) + 1]
        env = {key: value for key, value in spec.env.items() if key not in spec.unset_env}
        help_text = _capture_probe(prefix, spec, env, *subcommand, '--help')
        option = _help_option(help_text, flag)
        if not option:
            return False
        if source == OPENCODE and _capture_probe(prefix, spec, env, '--version').strip() != OPENCODE_VERSION.encode():
            return False
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False
    if source == AGY:
        return bool(_help_option(help_text, b'--input-format'))
    return source == CODEX or bool(re.search(rb'(?<![\w-])json(?![\w-])', option))


class _Tail:
    def __init__(self, path, channel, bus):
        self.stream = path.open('rb')
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.pending = ''
        self.channel = channel
        self.bus = bus
        self.recent = ''
        self.continuation = False
        self.hidden = False

    def drain(self, *, final=False):
        data = self.stream.read(65536)
        text = self.decoder.decode(data, final=final and not data)
        self.recent = (self.recent + text)[-65536:]
        self.pending += text
        while '\n' in self.pending:
            line, self.pending = self.pending.split('\n', 1)
            self.bus.forward(line.rstrip('\r'), self.channel,
                             record_status=not self.continuation and len(line)<=65536,
                             suppress_console=self.hidden)
            self.continuation = False
            self.hidden = False
        if len(self.pending) >= 65536 or (final and not data and self.pending):
            self.hidden = self.hidden or self.pending.startswith('STATUS|type=heartbeat|')
            self.bus.forward(self.pending, self.channel, newline=False,
                             record_status=not self.continuation and len(self.pending)<65536,
                             suppress_console=self.hidden)
            self.continuation = True
            self.pending = ''
        return bool(data)

    def close(self):
        self.stream.close()


def read_provider_output(path, *, transport='argv'):
    """Recover narrative from raw streams using the registered wire protocol.

    AGY step deltas can repeat final text and include tool output. Only the
    terminal result is authoritative, including when the wrapper died early.
    """
    if transport != 'agy-stream-json':
        return Path(path).read_text(encoding='utf-8', errors='replace')
    result = None
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():
                continue
            event = json.loads(line)
            if isinstance(event, dict) and event.get('event') == 'result':
                if result is not None:
                    raise ValueError('AGY returned multiple results for a single prompt')
                result = event.get('result')
    return agy_result_response(result)


class _RawTail(_Tail):
    def drain(self, *, final=False):
        # Machine output stays in the durable stream file.
        data = self.stream.read(65536)
        self.recent = (self.recent + self.decoder.decode(data, final=final and not data))[-65536:]
        return bool(data)


class _AgyTail(_RawTail):
    def __init__(self, path, channel, bus):
        super().__init__(path, channel, bus)
        self.path = path

    def finish(self):
        response = read_provider_output(self.path, transport='agy-stream-json')
        for line in response.splitlines(keepends=True):
            self.bus.forward(line.rstrip('\r\n'), 'stdout', newline=line.endswith('\n'),
                             record_status=len(line) <= 65536)


class _CLIUnsupported(ValueError):
    pass


def _check_cli_capabilities(argv, spec, invocation):
    required = invocation.get('required_cli_flags', [])
    if required:
        try:
            help_result = subprocess.run((*argv, '--help'), cwd=spec.cwd, env=spec.env,
                                         stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            help_output = help_result.stdout + help_result.stderr
        except (subprocess.TimeoutExpired, OSError) as error:
            raise _CLIUnsupported(f'Cannot verify provider stdin capabilities; upgrade the CLI: {error}') from error
        missing = [flag for flag in required if not _help_option(help_output, flag.encode())]
        if help_result.returncode or missing:
            details = {
                '--model': 'exact model selection',
                '--input-format': 'stream-json input',
                '--output-format': 'stream-json output',
            }
            capabilities = ', '.join(f'{flag} ({details.get(flag, "required capability")})' for flag in missing)
            reason = f'required capabilities are missing: {capabilities}' if missing else 'capabilities could not be verified'
            raise _CLIUnsupported('Upgrade AGY CLI: ' + reason + '; see ' + invocation.get('protocol_documentation', 'https://antigravity.google/docs/cli/headless/'))


def classify_unavailable(text):
    text = text.lower()
    for reason, phrases in [('auth', ('failed to authenticate', 'invalid authentication credentials', 'api error: 401', 'authentication failed')),
                            ('quota', ('usage limit', 'quota', 'rate limit')),
                            ('model-unsupported', ('unsupported model', 'model is not supported', 'not supported'))]:
        if any(phrase in text for phrase in phrases):
            return reason
    return ''


def _stream_unavailability(source, tail):
    tail.stream.seek(0)
    try:
        extract_response(source, tail.stream)
    except ProviderError as error:
        return classify_unavailable(str(error))
    except (ValueError, RecursionError):
        # The dispatcher rejects invalid framing only when capture is needed.
        pass
    return ''


def run_agent(config: AgentRunConfig) -> AgentRunResult:
    registry = load_registry(config.registry_file)
    name, agent = _agent(registry, config.agent)
    config = effective_config(config, registry)
    source = capture_source(config, name)
    stream_capture = source in STREAM_SOURCES
    invocation = agent.get('invocation', {})
    model = config.model or agent.get('model', {}).get('default', '')
    spec = build_invocation(config, registry)
    if not math.isfinite(config.heartbeat_seconds) or config.heartbeat_seconds < 0:
        raise ValueError('heartbeat_seconds must be nonnegative and finite')
    bus = EventBus(status_file=config.status_file, events_log=config.events_log, log_file=config.log_file)
    fields = {'issue':config.issue, 'role':config.role, 'agent':name, 'model':model}
    if name in ('codex', 'claude') and config.effort:
        fields['effort'] = config.effort
        if config.requested_effort:
            fields['requested_effort'] = config.requested_effort
    if config.done_file and not config.dispatcher_managed:
        Path(config.done_file).parent.mkdir(parents=True, exist_ok=True)
        Path(config.done_file).unlink(missing_ok=True)
    bus.emit('agent-start', fields)
    reason = ''
    code = 1
    stream_context = nullcontext(config.stream_directory) if config.stream_directory else tempfile.TemporaryDirectory(prefix='ai-skills-streams-')
    with stream_context as temporary:
        directory = Path(temporary)
        if source == CODEX:
            capture = Path(config.final_response_file)
            if (capture != directory / 'final-response.json' or capture.resolve() != capture or
                    capture.exists() or capture.is_symlink()):
                raise ValueError('unsafe or occupied Codex capture path')
        directory.mkdir(parents=True, exist_ok=True)
        output, errors = directory/'stdout', directory/'stderr'
        with output.open('xb') as stdout, errors.open('xb'):
            stdout_info = os.fstat(stdout.fileno())
        output_tail = _AgyTail if invocation.get('prompt_transport') == 'agy-stream-json' else _Tail
        if stream_capture:
            output_tail = _RawTail
        tails = [output_tail(output, 'stdout', bus), _Tail(errors, 'stderr', bus)]
        owned = None
        try:
            argv = resolve_executable(spec.argv, spec.env)
            _check_cli_capabilities(argv, spec, invocation)
            supervisor = ProcessSupervisor(config.record_directory or (Path(config.repo_root)/'.ai-skills'/'processes'))
            owned = supervisor.start(replace(spec, argv=argv, stdout=output, stderr=errors),
                                     run_id=config.run_id or 'agent-' + uuid.uuid4().hex,
                                     attempt_id=config.attempt_id or uuid.uuid4().hex)
            state = {'schema_version':1, 'identity':asdict(owned.identity), 'record_directory':str(supervisor.directory), 'state':'alive', 'stdout_file':str(output), 'stderr_file':str(errors)}
            if stream_capture:
                state['stdout_identity'] = [stdout_info.st_dev, stdout_info.st_ino]
            if config.process_state_file:
                write_json_atomic(Path(config.process_state_file), state)
            next_heartbeat = time.monotonic() + config.heartbeat_seconds
            unknown_since = None
            while True:
                for tail in tails:
                    tail.drain()
                liveness = supervisor.inspect(owned.identity)
                if liveness.state == 'dead':
                    # inspect may observe a finished session just before owner
                    # publishes return_code. Wait for its durable final record.
                    deadline = time.monotonic() + 3
                    while True:
                        return_code = supervisor.return_code(owned.identity)
                        if return_code is not None:
                            code = int(return_code)
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError('provider exited without durable return code')
                        time.sleep(.02)
                    break
                if liveness.state == 'unknown':
                    unknown_since = unknown_since or time.monotonic()
                    if time.monotonic() - unknown_since > 5:
                        raise RuntimeError('provider ownership unknown: ' + liveness.reason)
                else:
                    unknown_since = None
                if config.heartbeat_seconds and time.monotonic() >= next_heartbeat:
                    bus.emit('wrapper-heartbeat', {**fields, 'status':'alive'})
                    next_heartbeat = time.monotonic() + config.heartbeat_seconds
                time.sleep(.02)
            for tail in tails:
                while tail.drain(final=True):
                    pass
            if code == 0 and isinstance(tails[0], _AgyTail):
                tails[0].finish()
            reason = classify_unavailable(''.join(t.recent for t in tails)) if code else ''
            if stream_capture:
                reason = reason or _stream_unavailability(source, tails[0])
                if reason:
                    state['unavailable_reason'] = reason
                    code = code or 1
            if config.process_state_file and (not source or code != 0):
                write_json_atomic(Path(config.process_state_file), {**state, 'state':'dead', 'return_code':code})
        except FileNotFoundError as error:
            code, reason = 127, 'missing-command'
            bus.forward(str(error))
        except _CLIUnsupported as error:
            code, reason = 1, 'cli-unsupported'
            bus.forward(str(error))
        except (RuntimeError, ValueError, OSError) as error:
            code = 1
            reason = classify_unavailable(str(error))
            bus.forward(str(error))
        except KeyboardInterrupt:
            code = 130
        finally:
            if owned is not None:
                owned.close()
            for tail in tails:
                tail.close()
    status = 'success' if code == 0 else 'failed'
    if reason:
        bus.emit('agent-unavailable', {**fields, 'reason':reason, 'action':'exclude-route'})
    if config.dispatcher_managed:
        bus.emit('provider-exit', {**fields, 'return_code':code})
    else:
        if config.done_file:
            done = 'DONE|' + '|'.join(f'{key}={normalize(value)}' for key, value in {**fields, 'status':status, 'source':'runner-exit', 'completed_at':datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}.items())
            EventBus.append(Path(config.done_file), done + '\n')
            EventBus.append(bus.log_file, done + '\n')
        bus.emit('worker-done', {**fields, 'status':status})
        bus.emit('agent-complete', {**fields, 'status':status})
    telemetry = {'agent':name, 'model':model, 'input_tokens':'unknown', 'output_tokens':'unknown', 'cache_hit_tokens':'unknown', 'status':status, 'source':'runner-default'}
    # Telemetry is a log/console record, not a replacement for terminal status.
    from ai_skills.events import emit_status
    line = emit_status('telemetry', telemetry)
    EventBus.append(bus.log_file, line + '\n')
    print(line, file=sys.stderr)
    if source and config.process_state_file and code == 0:
        # Adoption needs durable proof that the runner finished after the owned provider.
        write_json_atomic(Path(config.process_state_file), {**state, 'state':'dead', 'return_code':code})
    return AgentRunResult(code, name, model, reason)


def _configured(agent, env, root):
    configuration = agent.get('user_model_configuration', {})
    if not configuration.get('requires_user_model_config') or not configuration.get('skip_when_unconfigured'):
        return True
    for candidate in configuration.get('config_paths', []):
        candidate = candidate.replace('$HOME', env.get('HOME') or env.get('USERPROFILE') or str(Path.home()))
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            path = root / path
        if path.is_file():
            return True
    return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    action = argv.pop(0) if argv and argv[0] in ('run', 'list', 'models') else 'run'
    parser = argparse.ArgumentParser(prog='ai-skills agent')
    for name in ('agent', 'model', 'effort', 'context-file', 'prompt-file', 'permission-mode', 'repo-root', 'registry-file', 'gui-mode', 'process-state-file', 'role'):
        parser.add_argument('--' + name)
    parser.add_argument('--context-payload-file', dest='context_file')
    parser.add_argument('--unattended', action='store_true', default=None)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--print-prompt', action='store_true')
    parser.add_argument('--list-agents', action='store_true')
    parser.add_argument('--detected-only', action='store_true')
    parser.add_argument('--list-models')
    parser.add_argument('--extra-arg', action='append', default=[])
    if action == 'models' and argv and not argv[0].startswith('-'):
        argv = ['--list-models', argv[0], *argv[1:]]
    args = parser.parse_args(argv)
    env = dict(os.environ)
    aliases = {'agent':'AGENT', 'model':'MODEL', 'effort':'MODEL_EFFORT', 'context_file':'CONTEXT_PAYLOAD_FILE',
               'prompt_file':'PROMPT_FILE', 'permission_mode':'AGENT_PERMISSION_MODE', 'repo_root':'REPO_ROOT',
               'registry_file':'AGENT_REGISTRY_FILE', 'gui_mode':'GUI_MODE'}
    values = {key:getattr(args,key) if getattr(args,key) is not None else env.get(alias) for key,alias in aliases.items()}
    values = {key:value for key,value in values.items() if value is not None}
    values.setdefault('repo_root', Path.cwd())
    values.setdefault('prompt_file', resource_path('prompt.md'))
    values.setdefault('agent', '')
    for key in ('status_file', 'events_log', 'log_file', 'done_file', 'state_file', 'result_file', 'issue_dir', 'role', 'issue', 'cycle'):
        value = env.get('RUN_WITH_IT_' + key.upper())
        if value:
            values[key] = value
    if args.role is not None:
        values['role'] = args.role
    elif values.get('role') == 'plan':
        values['role'] = 'agent'
    if args.process_state_file:
        values['process_state_file'] = args.process_state_file
    values['environment'] = env
    values['unattended'] = args.unattended if args.unattended is not None else env.get('UNATTENDED') == '1'
    values['extra_args'] = tuple(args.extra_arg) or tuple(shlex.split(env.get('AGENT_EXTRA_ARGS', '')))
    try:
        values['heartbeat_seconds'] = float(env.get('RUN_WITH_IT_HEARTBEAT_SECONDS', '30'))
        config = AgentRunConfig(**values)
        registry = load_registry(config.registry_file)
        if action == 'list' or args.list_agents:
            environment = runner_environment(config)
            for name, agent in registry['agents'].items():
                status, reason = 'detected', 'detected'
                try:
                    _agent(registry, name)
                except ValueError as error:
                    status, reason = 'disabled', str(error)
                else:
                    command = agent.get('detection', {}).get('command', '')
                    if not shutil.which(command, path=environment.get('PATH')):
                        status, reason = 'missing', 'missing command: ' + command
                    elif not _configured(agent, environment, Path(config.repo_root)):
                        status, reason = 'missing', agent.get('user_model_configuration', {}).get('skip_message') or 'missing user model configuration'
                if not args.detected_only or status == 'detected':
                    print('\t'.join((name, agent.get('display_name', name), status, reason)))
            return 0
        if args.list_models or action == 'models':
            name, agent = _agent(registry, args.list_models or config.agent)
            print('\n'.join(agent.get('model', {}).get('known_models', [])) or f'No configured models for {name}.')
            return 0
        if args.print_prompt or env.get('PRINT_PROMPT') == '1':
            _agent(registry, config.agent)
            print(_payload(config))
            return 0
        spec = build_invocation(config, registry)
        if args.dry_run:
            applied = effective_config(config, registry)
            if applied.requested_effort:
                print(f'effort: requested {applied.requested_effort}, effective {applied.effort}', file=sys.stderr)
            print(shlex.join(spec.argv))
            return 0
        return run_agent(config).return_code
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print('error: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
