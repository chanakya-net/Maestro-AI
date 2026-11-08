import copy
import json
from pathlib import Path
import sys

import pytest

from ai_skills.runner import AgentRunConfig, build_invocation, load_registry, resolve_executable


@pytest.fixture
def config(tmp_path):
    context = tmp_path / 'context.md'
    prompt = tmp_path / 'prompt.md'
    context.write_text('Context λ\n', encoding='utf-8')
    prompt.write_text('Do "work" & %PATH%\n', encoding='utf-8')
    return AgentRunConfig(agent='codex', context_file=context, prompt_file=prompt,
                          repo_root=tmp_path, unattended=True, gui_mode='0', environment={})


def test_registry_command_preserves_exact_arguments(config):
    registry = load_registry()
    config = config.replace(model='model with spaces', effort='xhigh', extra_args=('--empty', '', 'C:\\tail\\'))
    spec = build_invocation(config, registry)
    assert spec.argv == ('codex', 'exec', '-C', str(config.repo_root),
                         '--dangerously-bypass-approvals-and-sandbox', '--model', 'model with spaces',
                         '--empty', '', 'C:\\tail\\', '-c', 'model_reasoning_effort=high',
                         '-')
    assert spec.stdin == 'Context λ\n\nInstructions:\n\nDo "work" & %PATH%'.encode('utf-8')
    assert spec.cwd == config.repo_root


@pytest.mark.parametrize('agent,flag', [('codex', '--sandbox=read-only'), ('claude', '--permission-mode=plan')])
def test_safe_mode_uses_registered_read_only_profile(config, agent, flag):
    spec = build_invocation(config.replace(agent=agent, permission_mode='safe', gui_mode='1'), load_registry())
    assert flag in spec.argv
    assert 'safe' not in spec.argv


def test_safe_mode_without_profile_fails(config):
    with pytest.raises(ValueError, match='no registered read-only'):
        build_invocation(config.replace(agent='agy', permission_mode='safe'), load_registry())


def test_manual_permission_requires_explicit_unattended(config):
    with pytest.raises(ValueError, match='requires --unattended'):
        build_invocation(config.replace(unattended=False), load_registry())


@pytest.mark.parametrize('agent,flag', [('codex', '--sandbox=workspace-write'), ('claude', '--permission-mode=acceptEdits'), ('agy', '--sandbox')])
def test_gui_mode_permissions(config, agent, flag):
    assert flag in build_invocation(config.replace(agent=agent, gui_mode='1', unattended=False), load_registry()).argv


def test_alias_and_default_effort(config):
    spec = build_invocation(config.replace(agent='codex-cli', model='gpt-6.1-sol'), load_registry())
    assert 'model_reasoning_effort=high' in spec.argv


def test_claude_default_invocation_uses_sonnet_5_5(config):
    spec = build_invocation(config.replace(agent='claude'), load_registry())
    model_flag = spec.argv.index('--model')
    assert spec.argv[model_flag + 1] == 'claude-sonnet-5-5'


def test_codex_default_invocation_uses_sol_6_1(config):
    spec = build_invocation(config, load_registry())
    model_flag = spec.argv.index('--model')
    assert spec.argv[model_flag + 1] == 'gpt-6.1-sol'


@pytest.mark.parametrize('model', [
    *(f'gemini-4-argon-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'gemini-3.8-flash-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'claude-opus-5-5-{effort}' for effort in ('low', 'medium', 'high')),
    *(f'claude-sonnet-5-5-{effort}' for effort in ('low', 'medium', 'high')),
    'gpt-oss-120b-medium',
])
def test_agy_invocation_forwards_selected_model_unchanged(config, model):
    spec = build_invocation(config.replace(agent='agy', model=model), load_registry())
    model_flag = spec.argv.index('--model')
    assert spec.argv[model_flag:model_flag + 2] == ('--model', model)
    assert spec.argv[-4:] == ('--input-format', 'stream-json', '--output-format', 'stream-json')


def test_agy_default_invocation_pins_flash_high_once(config):
    spec = build_invocation(config.replace(agent='agy', model=''), load_registry())
    assert spec.argv.count('--model') == 1
    model_flag = spec.argv.index('--model')
    assert spec.argv[model_flag:model_flag + 2] == ('--model', 'gemini-3.8-flash-high')


def test_agy_explicit_model_overrides_flash_high_default_once(config):
    spec = build_invocation(config.replace(agent='agy', model='gemini-3.8-flash-low'), load_registry())
    assert spec.argv.count('--model') == 1
    model_flag = spec.argv.index('--model')
    assert spec.argv[model_flag:model_flag + 2] == ('--model', 'gemini-3.8-flash-low')


@pytest.mark.parametrize('extra_args', [('--model', 'other-model'), ('--model=other-model',), ('-m', 'other-model')])
def test_agy_rejects_extra_model_selection_options(config, extra_args):
    with pytest.raises(ValueError, match='AGY.*--model'):
        build_invocation(config.replace(agent='agy', model='gemini-3.8-flash-high', extra_args=extra_args), load_registry())


def test_agy_preserves_unrelated_extra_args(config):
    spec = build_invocation(config.replace(agent='agy', extra_args=('--verbose', '--label=test')), load_registry())
    assert '--verbose' in spec.argv
    assert '--label=test' in spec.argv
    assert spec.argv.count('--model') == 1


def test_disabled_provider_cannot_be_unblocked_by_registry_copy(config):
    registry = load_registry()
    registry['agents']['github-copilot'].pop('routing_disabled')
    with pytest.raises(ValueError, match='disabled'):
        build_invocation(config.replace(agent='copilot'), registry)


def test_npm_cmd_adapter_preserves_payload_without_cmd_shell(tmp_path):
    shim = tmp_path / 'provider.cmd'
    node = tmp_path / 'node.exe'
    node.touch()
    entry = tmp_path / 'node_modules' / 'provider' / 'cli.js'
    entry.parent.mkdir(parents=True)
    entry.write_text('console.log(process.argv)', encoding='utf-8')
    shim.write_text('@ECHO off\r\n"%~dp0\\node.exe" "%~dp0\\node_modules\\provider\\cli.js" %*\r\n', encoding='utf-8')
    argv = resolve_executable((str(shim), 'with spaces', '&|<>^%!"', '', 'C:\\tail\\'), {}, windows=True)
    assert argv == (str(node), str(entry), 'with spaces', '&|<>^%!"', '', 'C:\\tail\\')


def test_unknown_batch_script_fails_closed(tmp_path):
    shim = tmp_path / 'provider.cmd'
    shim.write_text('@echo %*', encoding='utf-8')
    with pytest.raises(ValueError, match='npm'):
        resolve_executable((str(shim), '&echo injected'), {}, windows=True)


@pytest.mark.parametrize('command', ['npm', 'npx'])
def test_npm_cli_variable_shim_preserves_payload_without_cmd_shell(tmp_path, command):
    # npm/cli v11.19.1 bin/npm.cmd and bin/npx.cmd use this variable-based form.
    root = tmp_path / 'node installation'
    root.mkdir()
    shim = root / f'{command}.CMD'
    node = root / 'node.exe'
    node.touch()
    entry = root / 'node_modules/npm/bin' / f'{command}-cli.js'
    entry.parent.mkdir(parents=True)
    entry.write_text('console.log(process.argv)', encoding='utf-8')
    variable = command.upper() + '_CLI_JS'
    shim.write_text(
        '@ECHO OFF\nSETLOCAL\n'
        'SET "NODE_EXE=%~dp0\\node.exe"\n'
        'IF NOT EXIST "%NODE_EXE%" (\n  SET "NODE_EXE=node"\n)\n'
        'SET "NPM_PREFIX_JS=%~dp0\\node_modules\\npm\\bin\\npm-prefix.js"\n'
        f'SET "{variable}=%~dp0\\node_modules\\npm\\bin\\{command}-cli.js"\n'
        'FOR /F "delims=" %%F IN (\'CALL "%NODE_EXE%" "%NPM_PREFIX_JS%"\') DO (\n'
        f'  SET "NPM_PREFIX_{variable}=%%F\\node_modules\\npm\\bin\\{command}-cli.js"\n)\n'
        f'IF EXIST "%NPM_PREFIX_{variable}%" (\n'
        f'  SET "{variable}=%NPM_PREFIX_{variable}%"\n)\n'
        f'"%NODE_EXE%" "%{variable}%" %*\n',
        encoding='utf-8-sig', newline='\r\n')
    payload = ('with spaces', '&|<>^%!"', '', 'C:\\tail\\')
    assert resolve_executable((str(shim), *payload), {}, windows=True) == (
        str(node), str(entry), *payload)


@pytest.mark.parametrize('assignment', [
    'SET "OTHER_CLI_JS=%~dp0\\cli.js"',
    'SET "NPX_CLI_JS=%UNTRUSTED%\\cli.js"',
])
def test_npm_cli_variable_shim_rejects_unresolved_entrypoint(tmp_path, assignment):
    shim = tmp_path / 'npx.cmd'
    (tmp_path / 'cli.js').touch()
    shim.write_text(assignment + '\n"%NODE_EXE%" "%NPX_CLI_JS%" %*\n', encoding='utf-8')
    with pytest.raises(ValueError, match='unsupported batch'):
        resolve_executable((str(shim), '&echo injected'), {}, windows=True)


def test_process_unset_environment_rejects_invalid_variable_names(tmp_path):
    from ai_skills.processes import ProcessSpec
    for names in [('BAD=NAME',),('',),('NUL\0NAME',),(True,)]:
        with pytest.raises(ValueError,match='unset_env'):
            ProcessSpec((sys.executable,),tmp_path,{},unset_env=names)


def test_codex_capture_flag_precedes_stdin_and_rejects_conflicts(config):
    registry = load_registry()
    context = {'envelope': {'cycle': '4'}, 'required_checks': ['check']}
    bound = config.replace(dispatcher_managed=True, role='impl', state_file=config.repo_root / 'state.json',
                           result_file=config.repo_root / 'result.json', final_response_file=config.repo_root / 'final-response.json',
                           result_capture_context=context)
    spec = build_invocation(bound, registry)
    assert spec.argv[-3:] == ('--output-last-message', str(bound.final_response_file), '-')
    metadata = json.loads(spec.stdin.decode().rsplit('Authoritative attempt metadata:\n', 1)[1])
    assert metadata['result_capture'] == context
    for flag in ('-o', '-ofoo', '-qofoo', '--output-last-message', '--output-last-message=foo'):
        with pytest.raises(ValueError, match='capture'):
            build_invocation(bound.replace(extra_args=(flag,)), registry)
    modifier = build_invocation(bound.replace(role='modify'), registry)
    assert modifier.argv[-3:] == ('--output-last-message', str(bound.final_response_file), '-')
    assert json.loads(modifier.stdin.decode().rsplit('Authoritative attempt metadata:\n', 1)[1])['result_capture'] == context
    with pytest.raises(ValueError, match='capture'):
        build_invocation(bound.replace(role='modify', extra_args=('-o',)), registry)
    verify = bound.replace(role='modify', verification_context={'execution_mode': 'verify_existing'})
    for ineligible in (bound.replace(dispatcher_managed=False), bound.replace(agent='claude'),
                       bound.replace(role='review', result_file=config.repo_root / 'review-status.json'),
                       bound.replace(role='plan', issue_dir=config.repo_root), verify):
        spec = build_invocation(ineligible, registry)
        assert '--output-last-message' not in spec.argv
        assert b'"result_capture"' not in spec.stdin


@pytest.mark.parametrize(('agent', 'source', 'flags', 'conflicts'), [
    ('claude-code', 'claude-json', ('--output-format', 'json'),
     ('--output-format=stream-json', '--input-format', '--include-partial-messages')),
    ('agy-cli', 'agy-stream-json', (),
     ('--output-format=text', '--input-format=text', '--prompt')),
    ('open_code', 'opencode-json-v1.16.2', ('--format', 'json'),
     ('--format=default', '--interactive', '--command')),
])
def test_stream_capture_preserves_baseline_arguments_and_rejects_conflicts(config, agent, source, flags, conflicts):
    registry = load_registry()
    plain = config.replace(agent=agent, model='configured/model', dispatcher_managed=True, role='impl',
                           state_file=config.repo_root / 'state.json', result_file=config.repo_root / 'result.json')
    context = {'source': source}
    bound = plain.replace(result_capture_context=context)
    baseline = build_invocation(plain, registry)
    captured = build_invocation(bound, registry)
    assert captured.argv == baseline.argv + flags
    stdin = captured.stdin
    if source == 'agy-stream-json':
        assert stdin.count(b'\n') == 1
        stdin = json.loads(stdin)['message']['content'].encode()
    assert json.loads(stdin.decode().rsplit('Authoritative attempt metadata:\n', 1)[1])['result_capture'] == context
    for option in (*conflicts, '--'):
        with pytest.raises(ValueError, match='capture conflicts'):
            build_invocation(bound.replace(extra_args=(option,)), registry)
    for ineligible in (bound.replace(dispatcher_managed=False),
                       bound.replace(verification_context={'execution_mode': 'verify_existing'})):
        spec = build_invocation(ineligible, registry)
        assert spec.argv == baseline.argv
        assert b'"result_capture"' not in spec.stdin


@pytest.mark.parametrize('agent,effort,role,flag', [
    ('codex', 'low', 'impl', ('-c', 'model_reasoning_effort=high')),
    ('codex', 'max', 'plan', ('-c', 'model_reasoning_effort=xhigh')),
    ('claude', 'maximum', 'impl', ('--effort', 'high')),
    ('claude', 'xhigh', 'impl', ('--effort', 'high')),
    ('claude', 'low', 'impl', ('--effort', 'low')),
    ('claude', 'max', 'plan', ('--effort', 'xhigh')),
    ('claude', '', 'plan', ('--effort', 'xhigh')),
    ('claude', '', 'impl', ('--effort', 'high')),
])
def test_effort_flags_follow_role_policy(config, agent, effort, role, flag):
    argv = build_invocation(config.replace(agent=agent, effort=effort, role=role), load_registry()).argv
    assert any(argv[i:i + 2] == flag for i in range(len(argv) - 1))
    assert 'max' not in argv and 'maximum' not in argv


def test_unknown_effort_fails_before_launch(config):
    with pytest.raises(ValueError, match='unknown effort'):
        build_invocation(config.replace(effort='ultra'), load_registry())


def test_agy_effort_untouched(config):
    spec = build_invocation(config.replace(agent='agy', model='claude-opus-5-5-high', effort='max'), load_registry())
    assert 'max' not in spec.argv and '--effort' not in spec.argv


@pytest.mark.parametrize('default', ['low', 'medium', 'high', 'xhigh', 'max', 'maximum'])
@pytest.mark.parametrize('role', ['plan', 'impl'])
@pytest.mark.parametrize('agent', ['claude', 'claude-code'])
@pytest.mark.parametrize('explicit', ['', 'low'])
def test_custom_default_is_not_an_explicit_request(config, default, role, agent, explicit):
    from ai_skills.runner import effective_config
    registry = copy.deepcopy(load_registry())
    registry['model_catalog']['custom'] = {'reasoning_effort': default}
    run = config.replace(agent=agent, model='custom', role=role, effort=explicit)
    effective = effective_config(run, registry)
    expected = explicit or ('xhigh' if role == 'plan' else
                            default if default in ('low', 'medium', 'high') else 'high')
    assert effective.effort == expected
    assert effective.requested_effort == ''
    assert effective_config(effective, registry) == effective
    argv = build_invocation(run, registry).argv
    assert ('--effort', expected) == argv[argv.index('--effort'):argv.index('--effort') + 2]


def test_codex_default_planning_does_not_invent_request(config):
    from ai_skills.runner import effective_config
    effective = effective_config(config.replace(agent='codex-cli', role='plan', effort=''), load_registry())
    assert (effective.effort, effective.requested_effort) == ('xhigh', '')
    assert effective_config(effective, load_registry()) == effective


def test_original_positional_config_order_and_optional_provenance(tmp_path):
    context, prompt = tmp_path / 'context', tmp_path / 'prompt'
    run = AgentRunConfig('claude', 'm', 'high', context, prompt, tmp_path, 'safe', '1', True,
                         requested_effort='maximum')
    assert (run.context_file, run.prompt_file, run.repo_root, run.permission_mode, run.gui_mode, run.unattended) == (
        context, prompt, tmp_path, 'safe', '1', True)
    assert run.requested_effort == 'maximum'
    assert AgentRunConfig(**{'agent': 'claude'}).requested_effort == ''


def test_explicit_adjustment_survives_repeated_preparation(config):
    from ai_skills.runner import effective_config
    registry = load_registry()
    effective = effective_config(config.replace(agent='claude', role='plan', effort='maximum'), registry)
    assert (effective.effort, effective.requested_effort) == ('xhigh', 'maximum')
    assert effective_config(effective, registry) == effective


@pytest.mark.parametrize('agent', ['codex', 'codex-cli', 'claude', 'claude-code'])
@pytest.mark.parametrize('role', ['review', 'complexity'])
def test_structured_ordinary_roles_remain_capped_for_difficult_prompts(config, agent, role, tmp_path):
    prompt = tmp_path / 'difficult-plan.md'
    prompt.write_text('Plan a holy-fuck complexity task')
    argv = build_invocation(config.replace(agent=agent, role=role, effort='maximum', prompt_file=prompt), load_registry()).argv
    assert ('model_reasoning_effort=high' in argv if agent.startswith('codex') else
            argv[argv.index('--effort') + 1] == 'high')
