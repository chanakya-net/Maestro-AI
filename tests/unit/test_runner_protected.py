"""Fixture-only coverage for the shared protected AGY launch seam.

The packaged runtime has no verified profile; every positive case here injects
an isolated fixture profile.
"""
from copy import deepcopy
from dataclasses import replace
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
           'invocation': {'command': sys.executable, 'prompt_transport': 'agy-stream-json',
                          'args_template': [str(script), '{{model_flag}}', '{{permission_mode}}',
                                            '--input-format', 'stream-json', '--output-format', 'stream-json']},
           'permission_modes': {'default': '', 'read_only': '--read-only'},
           'model': {'known_models': [MODEL], 'default': '', 'flag_template': '--model {{model}}'}}
    return {'aliases': {'alias': 'agy'}, 'agents': {'agy': agy}}


def _config(tmp_path, role, agent='agy', model=MODEL):
    prompt = tmp_path / 'prompt.md'
    prompt.write_text('hello')
    return runner.AgentRunConfig(agent=agent, model=model, role=role, prompt_file=prompt, context_file=prompt,
                                 repo_root=tmp_path, unattended=True, gui_mode='0', permission_mode='safe',
                                 record_directory=tmp_path / 'rec', stream_directory=tmp_path / 'streams',
                                 process_state_file=tmp_path / 'state.json', run_id='r', attempt_id='a',
                                 scrub_ambient_aliases=True)


class Stop(RuntimeError):
    pass


def _set_profile(monkeypatch, verifier):
    verifier.allows = lambda model, role: True
    monkeypatch.setattr(runner, 'resolve_agy_plan_profile', lambda registry: verifier)


@pytest.fixture
def supervisor(monkeypatch):
    seen = []
    def start(self, spec, **kwargs):
        seen.append(deepcopy(spec))
        raise Stop
    monkeypatch.setattr(ProcessSupervisor, 'start', start)
    return seen


@pytest.mark.parametrize('role', ['agent', 'plan'])
def test_supervisor_receives_exactly_the_verified_input(tmp_path, monkeypatch, supervisor, role):
    verified = []

    def verify(config, bound):
        verified.append(deepcopy(bound))
        return True

    _set_profile(monkeypatch, verify)
    registry = _registry(tmp_path)
    streams = tmp_path / 'actual-streams'
    streams.mkdir()
    config = _config(tmp_path, role).replace(stream_directory=streams / '..' / streams.name)
    monkeypatch.setenv('MODEL', 'ambient-model')
    protected = runner.bind_agy_launch(config, registry)
    # A later registry change must not alter what launches.
    registry['agents']['agy']['invocation']['args_template'] = ['--other']
    monkeypatch.setattr(runner, 'load_registry', lambda *_: pytest.fail('registry reloaded'))
    monkeypatch.setattr(runner, 'build_invocation', lambda *_: pytest.fail('invocation rebuilt'))
    monkeypatch.setattr(runner, '_check_cli_capabilities', lambda *_: pytest.fail('provider probed'))
    runner.run_agent(config, protected)
    assert len(verified) == 1 and len(supervisor) == 1
    launched = supervisor[0]
    assert launched == verified[0]
    assert (launched.stdout, launched.stderr) == (streams / 'stdout', streams / 'stderr')
    assert launched.env['MODEL'] == 'ambient-model'
    assert launched.unset_env == ('AGENT', 'MODEL')
    assert json.loads(launched.stdin)['message']['content'].endswith('hello')
    assert MODEL in launched.argv and '--read-only' in launched.argv


@pytest.mark.parametrize('role', ['agent', 'plan'])
def test_declined_binding_never_reaches_supervisor(tmp_path, monkeypatch, supervisor, role):
    config = _config(tmp_path, role)

    def verify(config, spec):
        assert spec.stderr == config.stream_directory / 'stderr'
        assert spec.stdout == config.stream_directory / 'stdout'
        return False

    _set_profile(monkeypatch, verify)
    with pytest.raises(ValueError, match='outside the verified profile'):
        runner.bind_agy_launch(config, _registry(tmp_path))
    assert supervisor == []
    assert not config.stream_directory.exists()


def test_changed_config_is_refused_before_supervisor(tmp_path, monkeypatch, supervisor):
    _set_profile(monkeypatch, lambda config, spec: True)
    config = _config(tmp_path, 'agent')
    protected = runner.bind_agy_launch(config, _registry(tmp_path))
    with pytest.raises(ValueError, match='admitted invocation changed'):
        runner.run_agent(config.replace(extra_args=('--yolo',)), protected)
    with pytest.raises(ValueError, match='admitted invocation changed'):
        runner.run_agent(config.replace(prompt_file=tmp_path / 'other.md'), protected)
    assert supervisor == []


def test_changed_capture_is_refused_before_paths_open(tmp_path, monkeypatch, supervisor):
    _set_profile(monkeypatch, lambda config, spec: True)
    config = _config(tmp_path, 'agent')
    protected = runner.bind_agy_launch(config, _registry(tmp_path))
    changed = replace(protected, spec=replace(protected.spec, stdout=tmp_path / 'other-capture'))
    with pytest.raises(ValueError, match='admitted invocation changed'):
        runner.run_agent(config, changed)
    assert supervisor == []
    assert not config.stream_directory.exists()


def test_worker_handoff_cannot_enable_packaged_profile(tmp_path, monkeypatch, supervisor):
    config, registry = _config(tmp_path, 'agent'), _registry(tmp_path)
    with monkeypatch.context() as fixture:
        _set_profile(fixture, lambda config, spec: True)
        protected = runner.bind_agy_launch(config, registry)
    with pytest.raises(ValueError, match='no verified enforcement profile'):
        runner.ProtectedLaunch.from_worker_input(config, registry, protected.worker_input(), protected.digest)
    assert supervisor == []


@pytest.mark.parametrize(('template', 'competing'), [
    ('', []),
    ('--model wrong-model', []),
    ('--model agy-model-one', []),
    ('--model {{model}}', ['--model=wrong-model']),
    ('--model {{model}}', ['-m', 'wrong-model']),
    ('--model {{model}}', ['-mwrong-model']),
    ('--model {{model}}', ['--']),
])
def test_model_must_be_explicitly_passed(tmp_path, monkeypatch, template, competing):
    _set_profile(monkeypatch, lambda config, spec: True)
    registry = _registry(tmp_path)
    registry['agents']['agy']['model']['flag_template'] = template
    registry['agents']['agy']['invocation']['args_template'][1:1] = competing
    with pytest.raises(ValueError, match='not explicitly selected'):
        runner.bind_agy_launch(_config(tmp_path, 'agent'), registry)


def test_stock_resolver_refuses_binding(tmp_path):
    with pytest.raises(ValueError, match='no verified enforcement profile'):
        runner.bind_agy_launch(_config(tmp_path, 'agent'), _registry(tmp_path))
