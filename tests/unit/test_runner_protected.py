"""Fixture-only coverage for the shared protected AGY launch seam.

The packaged runtime has no verified profile; every positive case here injects
an isolated fixture profile.
"""
import json
import sys

import pytest

from ai_skills import runner
from ai_skills.processes import ProcessSupervisor

MODEL = 'agy-model-one'


def _registry(tmp_path):
    script = tmp_path / 'p.py'
    script.write_text('')
    agy = {'detection': {'command': sys.executable},
           'invocation': {'command': sys.executable, 'args_template': [str(script), '{{model_flag}}', '{{permission_mode}}', '{{prompt}}']},
           'permission_modes': {'default': '', 'read_only': '--read-only'},
           'model': {'known_models': [MODEL], 'default': '', 'flag_template': '{{model}}'}}
    return {'aliases': {'alias': 'agy'}, 'agents': {'agy': agy}}


def _config(tmp_path, role, agent='agy', model=MODEL):
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('hello')
    return runner.AgentRunConfig(agent=agent, model=model, role=role, prompt_file=prompt, context_file=prompt,
                                 repo_root=tmp_path, unattended=True, gui_mode='0', permission_mode='safe',
                                 record_directory=tmp_path / 'rec', stream_directory=tmp_path / 'streams',
                                 process_state_file=tmp_path / 'state.json', run_id='r', attempt_id='a')


class Stop(RuntimeError):
    pass


@pytest.fixture
def supervisor(monkeypatch):
    seen = []
    def start(self, spec, **kwargs):
        seen.append(spec)
        raise Stop
    monkeypatch.setattr(ProcessSupervisor, 'start', start)
    return seen


@pytest.mark.parametrize('role', ['agent', 'plan'])
def test_supervisor_receives_exactly_the_verified_input(tmp_path, monkeypatch, supervisor, role):
    verified = []
    def resolve(registry):
        def check(config, bound):
            verified.append(bound)
            return True
        return check
    monkeypatch.setattr(runner, 'resolve_agy_plan_profile', resolve)
    registry = _registry(tmp_path)
    config = _config(tmp_path, role)
    protected = runner.bind_agy_launch(config, registry)
    # A later registry change must not alter what launches.
    registry['agents']['agy']['invocation']['args_template'] = ['--other']
    monkeypatch.setattr(runner, 'load_registry', lambda *_: pytest.fail('registry reloaded'))
    monkeypatch.setattr(runner, 'build_invocation', lambda *_: pytest.fail('invocation rebuilt'))
    monkeypatch.setattr(runner, '_check_cli_capabilities', lambda *_: pytest.fail('provider probed'))
    runner.run_agent(config, protected)
    assert len(verified) == 1 and len(supervisor) == 1
    launched = supervisor[0]
    assert (launched.argv, launched.env, launched.stdin, launched.cwd) == (
        verified[0].argv, verified[0].env, verified[0].stdin, verified[0].cwd)
    assert MODEL in launched.argv and '--read-only' in launched.argv


def test_declined_binding_never_reaches_supervisor(tmp_path, monkeypatch, supervisor):
    monkeypatch.setattr(runner, 'resolve_agy_plan_profile', lambda r: (lambda c, b: False))
    with pytest.raises(ValueError, match='outside the verified profile'):
        runner.bind_agy_launch(_config(tmp_path, 'agent'), _registry(tmp_path))
    assert supervisor == []


def test_changed_config_is_refused_before_supervisor(tmp_path, monkeypatch, supervisor):
    monkeypatch.setattr(runner, 'resolve_agy_plan_profile', lambda r: (lambda c, b: True))
    config = _config(tmp_path, 'agent')
    protected = runner.bind_agy_launch(config, _registry(tmp_path))
    with pytest.raises(ValueError, match='admitted invocation changed'):
        runner.run_agent(config.replace(extra_args=('--yolo',)), protected)
    with pytest.raises(ValueError, match='admitted invocation changed'):
        runner.run_agent(config.replace(prompt_file=tmp_path / 'other.md'), protected)
    assert supervisor == []


def test_model_must_be_explicitly_passed(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'resolve_agy_plan_profile', lambda r: (lambda c, b: True))
    registry = _registry(tmp_path)
    registry['agents']['agy']['model']['flag_template'] = ''
    with pytest.raises(ValueError, match='not explicitly selected'):
        runner.bind_agy_launch(_config(tmp_path, 'agent'), registry)


def test_stock_resolver_refuses_binding(tmp_path):
    with pytest.raises(ValueError, match='no verified enforcement profile'):
        runner.bind_agy_launch(_config(tmp_path, 'agent'), _registry(tmp_path))
