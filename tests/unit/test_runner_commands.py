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
                         '--empty', '', 'C:\\tail\\', '-c', 'model_reasoning_effort=xhigh',
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


def test_process_unset_environment_rejects_invalid_variable_names(tmp_path):
    from ai_skills.processes import ProcessSpec
    for names in [('BAD=NAME',),('',),('NUL\0NAME',),(True,)]:
        with pytest.raises(ValueError,match='unset_env'):
            ProcessSpec((sys.executable,),tmp_path,{},unset_env=names)
