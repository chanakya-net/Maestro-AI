import json
from pathlib import Path
import pytest
from ai_skills.pair import main


def invoke(capsys, *args):
    code = main(list(args)); out = capsys.readouterr()
    return code, dict(line.split('=', 1) for line in out.out.splitlines() if '=' in line), out.err


def test_start_requires_explicit_config_and_no_side_effects(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    task = tmp_path / 'task.md'; task.write_text('Design durable storage.')
    code, _, error = invoke(capsys, 'start', str(task))
    assert code == 2 and 'configuration' in error
    assert not (tmp_path / '.pair-colleague').exists()


@pytest.mark.parametrize('bounds', [('1', '8'), ('3', '9'), ('4', '3'), ('bad', '8')])
def test_round_bounds(tmp_path, monkeypatch, capsys, bounds):
    monkeypatch.chdir(tmp_path)
    task = tmp_path / 'task.md'; task.write_text('Design storage.')
    code, _, _ = invoke(capsys, 'start', str(task), '--min-rounds', bounds[0], '--max-rounds', bounds[1])
    assert code == 2


@pytest.fixture
def pair_fixture(tmp_path, monkeypatch, capsys):
    import sys
    from ai_skills import pair_documents as d
    monkeypatch.chdir(tmp_path)
    script = tmp_path / 'provider.py'
    script.write_text('''import json, os, pathlib, re, sys, time
payload=sys.argv[-1]
round_no=int(re.search(r'round (\\d+)', payload, re.I).group(1))
model=sys.argv[1]
root=pathlib.Path(os.environ['REPO_ROOT'])
(root / (model + '-argv.json')).write_text(json.dumps(sys.argv[1:]))
(root / (model + '-role.txt')).write_text(os.environ.get('RUN_WITH_IT_ROLE', ''))
count=root / (model + '-r' + str(round_no) + '.count')
n=int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(n))
(root / (model + '-context.txt')).write_text(payload)
(root / (model + '-cwd.txt')).write_text(str(pathlib.Path.cwd()))
mode=os.environ.get('FAKE_MODE', '')
if mode == 'slow': time.sleep(float(os.environ.get('FAKE_SLEEP', '1')))
if mode == 'gated':
    deadline = time.monotonic() + 30
    while not (root / 'providers.release').exists():
        if time.monotonic() >= deadline:
            print('provider release gate timed out', file=sys.stderr)
            sys.exit(99)
        time.sleep(.03)
if mode in ('fail-once', 'fail') and model == 'two' and (n == 1 or mode == 'fail'):
    print('simulated harness failure',file=sys.stderr); print('partial'); sys.exit(7)
if mode == 'hang' and model == 'two':
    import subprocess
    child=subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    (root/'grandchild.pid').write_text(str(child.pid))
    time.sleep(60)
sections=''' + repr(d.AGENT_SECTIONS) + '''
for section in sections:
    print('# ' + section + '\\n\\nEvidence MARKER-R' + str(round_no) + ' from ' + model + '.\\n')
print('DISCUSSION_STATUS: READY')
''', encoding='utf-8')
    registry = {'aliases': {'alias': 'fake'}, 'agents': {'fake': {
        'detection': {'command': sys.executable},
        'invocation': {'command': sys.executable, 'args_template': [str(script), '{{model_flag}}', '{{permission_mode}}', '{{prompt}}']},
        'permission_modes': {'default': '', 'read_only': '--read-only'},
        'model': {'known_models': ['one', 'two'], 'default': '', 'flag_template': '{{model}}'}}}}
    registry_file = tmp_path / 'registry.json'; registry_file.write_text(json.dumps(registry))
    monkeypatch.setenv('AGENT_REGISTRY_FILE', str(registry_file))
    for slot, model in [('1', 'one'), ('2', 'two')]:
        for key, value in {'HARNESS': 'alias', 'MODEL': model, 'EFFORT': 'maximum'}.items(): monkeypatch.setenv(f'AGENT_{slot}_{key}', value)
    task = tmp_path / 'task.md'; task.write_text('Choose durable storage.\n# Constraints and success criteria\nSurvive restarts.\n')
    def start(*args):
        code, values, err = invoke(capsys, 'start', str(task), *args)
        assert code == 0, err
        return Path(values['RUN_DIR'])
    return start, tmp_path, registry_file


def decision_file(root, action):
    from ai_skills import pair_documents as d
    p = root / ('decision-' + action + '.md')
    p.write_text('\n\n'.join('# ' + s + '\n\nEvidence supports candidate A.' for s in d.DECISION_SECTIONS) + '\n\nCOORDINATOR_ACTION: ' + action + '\n')
    return p


def test_full_rounds_decisions_snapshots_and_finalization(pair_fixture, capsys):
    from ai_skills import pair_documents as d
    start, root, _ = pair_fixture
    run = start('--min-rounds', '2', '--max-rounds', '3')
    code, values, err = invoke(capsys, 'run-round', '--run-dir', str(run)); assert code == 0, err
    assert values['STATUS'] == 'awaiting-coordinator'
    assert (root / 'one.count').exists() is False
    assert (root / 'one-r1.count').read_text() == '1'
    assert '--read-only' not in (root / 'one-context.txt').read_text() # permission is separate argv
    c = json.loads((run / 'config.json').read_text()); assert c['agents']['1']['effort'] == 'max'
    snapshot1 = (run / 'rounds/round-01/snapshot.md').read_text()
    assert snapshot1.count('Survive restarts.') == 1 and 'MARKER' not in snapshot1
    for model in ('one', 'two'):
        text = (root / (model + '-context.txt')).read_text()
        assert text.startswith(snapshot1.rstrip('\n')) and '{{' not in text
        assert (root / (model + '-cwd.txt')).read_text() == str(root)
    early = decision_file(root, 'ACCEPT')
    assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(early))[0] == 2
    cont = decision_file(root, 'CONTINUE')
    assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(cont))[0] == 0
    assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(cont))[0] == 0
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(early))[0] == 0
    state = json.loads((run / 'state.json').read_text())
    expected = {key: str(state[key]) for _, key in d.DOSSIER_RUN_LABELS}
    final = root / 'final.md'
    record = '\n'.join('- ' + label + ': ' + expected[key] for label, key in d.DOSSIER_RUN_LABELS)
    record += '\n- Coordinator: current invoking agent\n'
    for slot, a in c['agents'].items(): record += '- Agent ' + slot + ': ' + ', '.join(a[k] for k in ('name', 'harness', 'model', 'effort')) + '\n'
    final.write_text('# Final Solution Decision\n\n' + '\n\n'.join('## ' + s + '\n\n' + (record if s == 'Discussion record' else 'Durable storage solves the problem.') for s in d.DOSSIER_SECTIONS))
    assert invoke(capsys, 'finalize', '--run-dir', str(run), '--final-file', str(final))[0] == 0
    assert invoke(capsys, 'finalize', '--run-dir', str(run), '--final-file', str(final))[0] == 0
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 2
    final.write_text(final.read_text() + '\nConflict\n')
    assert invoke(capsys, 'finalize', '--run-dir', str(run), '--final-file', str(final))[0] == 2


def _use_direct_harness_registry(registry_file):
    registry = json.loads(registry_file.read_text())
    fake = registry['agents'].pop('fake')
    fake['invocation']['args_template'] = [
        fake['invocation']['args_template'][0], '{{model_flag}}', '{{model_settings}}',
        '{{permission_mode}}', '{{prompt}}']
    for name in ('codex', 'claude'):
        registry['agents'][name] = {**fake, 'model': {**fake['model'], 'known_models': ['one', 'two']}}
    registry['aliases'] = {'codex-alias': 'codex', 'claude-alias': 'claude'}
    registry_file.write_text(json.dumps(registry))


def _direct_args(harness1, effort1, effort2='maximum'):
    return ['--agent-1-harness', harness1, '--agent-1-model', 'one', '--agent-1-effort', effort1,
            '--agent-2-harness', 'claude-alias', '--agent-2-model', 'two', '--agent-2-effort', effort2]


@pytest.mark.parametrize('purpose', [None, 'general', 'plan'])
def test_pair_start_purpose_validation_and_persistence(pair_fixture, capsys, purpose):
    start, _, _ = pair_fixture
    run = start(*(['--purpose', purpose] if purpose else []))
    config_file = run / 'config.json'
    config = json.loads(config_file.read_text())
    assert config['purpose'] == (purpose or 'general')
    # Old configurations remain readable without adding fields to saved bytes.
    config.pop('purpose')
    config_file.write_text(json.dumps(config))
    frozen = config_file.read_bytes()
    assert invoke(capsys, 'status', '--run-dir', str(run))[0] == 0
    assert config_file.read_bytes() == frozen
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert config_file.read_bytes() == frozen
    assert (run / 'rounds/round-01/agent-1-attempt-01-spec.json').read_text().count('"purpose": "general"') == 1


def test_invalid_purpose_fails_before_task_read_or_run_creation(pair_fixture, capsys):
    from ai_skills.pair import PairConfig, PairError, PairRun
    _, root, _ = pair_fixture
    run_root = root / 'custom-runs'
    options = PairConfig(task_source=str(root / 'missing.md'), run_root=run_root, purpose='planning')
    with pytest.raises(PairError, match='purpose'):
        PairRun(options).start()
    code, _, error = invoke(capsys, 'start', str(root / 'missing.md'),
                            '--purpose', 'planning', '--run-root', str(run_root))
    assert code == 2 and 'purpose' in error
    assert not run_root.exists()


@pytest.mark.parametrize('purpose', ['general', 'plan'])
@pytest.mark.parametrize(('slot', 'harness', 'requested', 'effective', 'flag'), [
    ('1', 'codex-alias', value, 'high', '-c model_reasoning_effort=high')
    for value in ('low', 'medium', 'high', 'xhigh', 'max', 'maximum')
] + [
    ('2', 'claude-alias', value, expected, '--effort ' + expected)
    for value, expected in (('low', 'low'), ('medium', 'medium'), ('high', 'high'),
                            ('xhigh', 'high'), ('max', 'high'), ('maximum', 'high'))
])
def test_pair_resolves_direct_harness_effort_before_freezing_inputs(
        pair_fixture, capsys, slot, harness, requested, effective, flag, purpose):
    if purpose == 'plan':
        effective = 'xhigh' if slot == '1' or requested in ('max', 'maximum') else requested
        flag = ('-c model_reasoning_effort=' if slot == '1' else '--effort ') + effective
    start, root, registry_file = pair_fixture
    _use_direct_harness_registry(registry_file)
    args =['--agent-1-harness', 'codex-alias', '--agent-1-model', 'one', '--agent-1-effort', 'xhigh',
            '--agent-2-harness', 'claude-alias', '--agent-2-model', 'two', '--agent-2-effort', 'maximum']
    args[args.index('--agent-' + slot + '-harness') + 1] = harness
    args[args.index('--agent-' + slot + '-effort') + 1] = requested
    code, values, error = invoke(capsys, 'start', str(root / 'task.md'), '--purpose', purpose, *args)
    assert code == 0, error
    run = Path(values['RUN_DIR'])
    config = json.loads((run / 'config.json').read_text())
    agent = config['agents'][slot]
    assert agent['effort'] == effective
    assert agent.get('requested_effort', '') == (requested if requested != effective else '')
    if requested != effective:
        assert values[f'AGENT_{slot}_EFFORT'] == f'requested {requested}, effective {effective}'
    assert agent['harness'] == ('codex' if slot == '1' else 'claude')

    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    prompt = (run / f'rounds/round-01/agent-{slot}-prompt.md').read_text()
    assert f'configured effort level is **{effective}**' in prompt
    other_model = 'one' if slot == '1' else 'two'
    argv = json.loads((root / f'{other_model}-argv.json').read_text())
    assert flag.split() == argv[1:3]
    assert (root / f'{other_model}-role.txt').read_text() == ('plan' if purpose == 'plan' else 'agent')
    for colleague in ('1', '2'):
        prefix = run / f'rounds/round-01/agent-{colleague}-attempt-01'
        spec = json.loads(Path(str(prefix) + '-spec.json').read_text())
        record = json.loads(Path(str(prefix) + '-run.json').read_text())
        assert spec['purpose'] == record['purpose'] == purpose
        assert spec['agent'] == config['agents'][colleague]
        assert spec['agent']['effort'] not in ('max', 'maximum')


@pytest.mark.parametrize('purpose', ['general', 'plan'])
@pytest.mark.parametrize(('harness', 'requested', 'effective'), [
    ('fake', 'maximum', 'max'), ('fake', 'xhigh', 'xhigh'),
    ('agy', 'maximum', 'max'), ('agy', 'xhigh', 'xhigh'),
])
def test_non_target_harness_effort_keeps_semantics_without_provenance(
        pair_fixture, capsys, harness, requested, effective, purpose):
    start, root, registry_file = pair_fixture
    registry = json.loads(registry_file.read_text())
    registry['agents']['agy'] = {**registry['agents']['fake']}
    model = 'claude-opus-5-5-low' if harness == 'agy' else 'one'
    registry['agents'][harness]['model'] = {**registry['agents'][harness]['model'], 'known_models': [model, 'two']}
    registry_file.write_text(json.dumps(registry))
    run = start('--purpose', purpose, '--agent-1-harness', harness, '--agent-1-model', model, '--agent-1-effort', requested)
    agent = json.loads((run / 'config.json').read_text())['agents']['1']
    assert agent['harness'] == harness
    assert agent['effort'] == effective
    assert agent.get('requested_effort', '') == ''
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert json.loads((root / f'{model}-argv.json').read_text())[0] == model


@pytest.mark.parametrize('purpose', ['general', 'plan'])
@pytest.mark.parametrize('harness', ['codex-alias', 'claude-alias'])
def test_unknown_effort_token_fails_before_launch(pair_fixture, capsys, harness, purpose):
    _, root, registry_file = pair_fixture
    _use_direct_harness_registry(registry_file)
    code, _, error = invoke(capsys, 'start', str(root / 'task.md'), '--purpose', purpose, *_direct_args(harness, 'ultra'))
    assert code == 2 and 'unknown effort' in error
    assert not (root / '.pair-colleague').exists()


@pytest.mark.parametrize('purpose', ['general', 'plan'])
def test_adjusted_effort_still_requires_read_only_profile(pair_fixture, capsys, purpose):
    _, root, registry_file = pair_fixture
    _use_direct_harness_registry(registry_file)
    data = json.loads(registry_file.read_text())
    del data['agents']['codex']['permission_modes']['read_only']
    registry_file.write_text(json.dumps(data))
    code, _, error = invoke(capsys, 'start', str(root / 'task.md'), '--purpose', purpose, *_direct_args('codex-alias', 'xhigh'))
    assert code == 2 and 'read-only' in error
    assert not (root / '.pair-colleague').exists()


@pytest.mark.parametrize('purpose', ['general', 'plan'])
def test_missing_effort_still_fails_before_launch(pair_fixture, monkeypatch, capsys, purpose):
    _, root, registry_file = pair_fixture
    _use_direct_harness_registry(registry_file)
    monkeypatch.delenv('AGENT_1_EFFORT')
    args = [a for a in _direct_args('codex-alias', 'xhigh') if a not in ('--agent-1-effort', 'xhigh')]
    code, _, error = invoke(capsys, 'start', str(root / 'task.md'), '--purpose', purpose, *args)
    assert code == 2 and 'effort' in error
    assert not (root / '.pair-colleague').exists()


@pytest.mark.parametrize('purpose', [None, 'general', 'plan'])
def test_pair_purpose_is_independent_of_task_and_environment(pair_fixture, monkeypatch, capsys, purpose):
    start, root, registry_file = pair_fixture
    _use_direct_harness_registry(registry_file)
    (root / 'task.md').write_text('Produce a detailed plan. Explicit planning task, highest difficulty.')
    for key, value in {'RUN_WITH_IT_ROLE': 'agent' if purpose == 'plan' else 'plan',
                       'MODEL_EFFORT': 'maximum', 'RUN_WITH_IT_PLAN_ENABLED': '1',
                       'TASK_COMPLEXITY': 'holy-fuck', 'PAIR_COLLEAGUE_PURPOSE': 'plan'}.items():
        monkeypatch.setenv(key, value)
    # Reverse colleagues and use canonical IDs to ensure neither slot nor provider drives policy.
    args = _direct_args('claude', 'maximum', 'low')
    args[args.index('--agent-2-harness') + 1] = 'codex'
    run = start(*args, *(['--purpose', purpose] if purpose else []))
    expected = 'xhigh' if purpose == 'plan' else 'high'
    config = json.loads((run / 'config.json').read_text())
    assert config['purpose'] == (purpose or 'general')
    assert all(a['effort'] == expected for a in config['agents'].values())
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    for model, flag in [('one', '--effort ' + expected), ('two', '-c model_reasoning_effort=' + expected)]:
        assert json.loads((root / f'{model}-argv.json').read_text())[1:3] == flag.split()
        assert (root / f'{model}-role.txt').read_text() == ('plan' if purpose == 'plan' else 'agent')


@pytest.mark.parametrize(('purpose', 'codex_request', 'codex_effective', 'claude_request', 'claude_effective'), [
    ('general', 'xhigh', 'high', 'maximum', 'high'),
    ('plan', 'high', 'xhigh', 'maximum', 'xhigh'),
    ('plan', 'high', 'xhigh', 'low', 'low'),
])
def test_adjusted_effort_run_writes_dossier_and_finalizes(
        pair_fixture, capsys, purpose, codex_request, codex_effective, claude_request, claude_effective):
    from ai_skills import pair_documents as d
    _, root, registry_file = pair_fixture
    _use_direct_harness_registry(registry_file)
    code, values, error = invoke(capsys, 'start', str(root / 'task.md'),
                                 '--purpose', purpose, *_direct_args('codex-alias', codex_request, claude_request),
                                 '--min-rounds', '2', '--max-rounds', '3')
    assert code == 0, error
    run = Path(values['RUN_DIR'])
    assert values['AGENT_1_EFFORT'] == f'requested {codex_request}, effective {codex_effective}'
    config = json.loads((run / 'config.json').read_text())
    assert config['agents']['1']['effort'] == codex_effective
    assert config['agents']['1']['requested_effort'] == codex_request
    assert config['agents']['2']['effort'] == claude_effective
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert invoke(capsys, 'record-decision', '--run-dir', str(run),
                  '--decision-file', str(decision_file(root, 'CONTINUE')))[0] == 0
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert invoke(capsys, 'record-decision', '--run-dir', str(run),
                  '--decision-file', str(decision_file(root, 'ACCEPT')))[0] == 0
    state = json.loads((run / 'state.json').read_text())
    record = '\n'.join('- ' + label + ': ' + str(state[key]) for label, key in d.DOSSIER_RUN_LABELS)
    record += '\n- Coordinator: current invoking agent\n'
    for slot, a in config['agents'].items():
        fields = [a['name'], a['harness'], a['model'], a['effort']]
        if a.get('requested_effort'):
            fields.append('requested effort ' + a['requested_effort'])
        record += '- Agent ' + slot + ': ' + ', '.join(fields) + '\n'
    final = root / 'final.md'
    final.write_text('# Final Solution Decision\n\n' + '\n\n'.join(
        '## ' + s + '\n\n' + (record if s == 'Discussion record' else 'Durable storage solves the problem.')
        for s in d.DOSSIER_SECTIONS))
    code, _, error = invoke(capsys, 'finalize', '--run-dir', str(run), '--final-file', str(final))
    assert code == 0, error
    assert f'- Agent 1: Agent 1, codex, one, {codex_effective}, requested effort {codex_request}' in final.read_text()
    assert f'- Agent 2: Agent 2, claude, two, {claude_effective}' in final.read_text()
    assert (run / 'final-solution.md').read_bytes() == final.read_bytes()


@pytest.mark.parametrize(('purpose', 'claude_request', 'claude_effective'), [
    ('general', 'maximum', 'high'), ('plan', 'maximum', 'xhigh'), ('plan', 'medium', 'medium'),
])
def test_pair_saved_purpose_and_effort_survive_retry_and_next_round(
        pair_fixture, monkeypatch, capsys, purpose, claude_request, claude_effective):
    start, root, registry_file = pair_fixture
    _use_direct_harness_registry(registry_file)
    monkeypatch.setenv('FAKE_MODE', 'fail-once')
    run = start('--purpose', purpose, *_direct_args('codex-alias', 'maximum', claude_request))
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 1
    paths = [run / 'config.json', run / 'agent-prompt-template.md']
    paths += list((run / 'rounds/round-01').glob('*-spec.json'))
    paths += list((run / 'rounds/round-01').glob('*-run.json'))
    paths += list((run / 'rounds/round-01').glob('*-prompt.md'))
    paths += list((run / 'rounds/round-01').glob('*-output.md'))
    frozen = {p: p.read_bytes() for p in paths}
    for key, value in {'RUN_WITH_IT_ROLE': 'agent' if purpose == 'plan' else 'plan',
                       'MODEL_EFFORT': 'low', 'AGENT_1_HARNESS': 'missing',
                       'AGENT_2_EFFORT': 'low', 'PAIR_COLLEAGUE_PURPOSE': 'general'}.items():
        monkeypatch.setenv(key, value)
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert all(p.read_bytes() == contents for p, contents in frozen.items())
    assert (root / 'one-r1.count').read_text() == '1'
    assert (root / 'two-r1.count').read_text() == '2'
    assert invoke(capsys, 'record-decision', '--run-dir', str(run),
                  '--decision-file', str(decision_file(root, 'CONTINUE')))[0] == 0
    monkeypatch.delenv('FAKE_MODE')
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert all(p.read_bytes() == contents for p, contents in frozen.items())
    config = json.loads((run / 'config.json').read_text())
    for spec_file in (run / 'rounds').glob('*/*-spec.json'):
        spec = json.loads(spec_file.read_text())
        manifest = json.loads(spec_file.with_name(spec_file.name.replace('-spec.json', '-run.json')).read_text())
        assert spec['purpose'] == manifest['purpose'] == purpose
        assert spec['agent'] in config['agents'].values()
    codex_effective = 'xhigh' if purpose == 'plan' else 'high'
    for model, effective, flag in [('one', codex_effective, '-c model_reasoning_effort='),
                                   ('two', claude_effective, '--effort ')]:
        assert (root / f'{model}-r2.count').read_text() == '1'
        assert json.loads((root / f'{model}-argv.json').read_text())[1:3] == (flag + effective).split()
        assert (root / f'{model}-role.txt').read_text() == ('plan' if purpose == 'plan' else 'agent')
        assert f'configured effort level is **{effective}**' in (root / f'{model}-context.txt').read_text()


def test_retry_only_failed_colleague_and_block_after_cap(pair_fixture, monkeypatch, capsys):
    start, root, _ = pair_fixture
    monkeypatch.setenv('FAKE_MODE', 'fail-once')
    run = start()
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 1
    first = run / 'rounds/round-01/agent-1-output.md'; digest = first.read_bytes()
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert first.read_bytes() == digest
    assert (root / 'one-r1.count').read_text() == '1'
    assert (root / 'two-r1.count').read_text() == '2'
    assert 'simulated harness failure' in (run / 'rounds/round-01/agent-2-attempt-01-stderr.log').read_text()
    run2 = start(); monkeypatch.setenv('MAX_AGENT_ATTEMPTS', '1'); monkeypatch.setenv('FAKE_MODE', 'fail')
    assert invoke(capsys, 'run-round', '--run-dir', str(run2))[0] == 3
    assert invoke(capsys, 'finalize', '--run-dir', str(run2), '--final-file', str(decision_file(root, 'ACCEPT')))[0] == 3


def test_read_only_model_detection_and_empty_task_refused(pair_fixture, capsys):
    start, root, registry = pair_fixture
    data = json.loads(registry.read_text()); del data['agents']['fake']['permission_modes']['read_only']; registry.write_text(json.dumps(data))
    code, _, error = invoke(capsys, 'start', str(root / 'task.md'))
    assert code == 2 and 'read-only' in error
    assert not (root / '.pair-colleague').exists()
    data['agents']['fake']['permission_modes']['read_only'] = '--read-only'; registry.write_text(json.dumps(data))
    code, _, error = invoke(capsys, 'start', str(root / 'task.md'), '--agent-1-model', 'invented')
    assert code == 2 and 'not registered' in error


def test_frozen_snapshot_and_output_hash_protect_context(pair_fixture, capsys):
    import time
    start, root, _ = pair_fixture
    run = start()
    snapshot = run / 'rounds/round-01/snapshot.md'; snapshot.write_text(snapshot.read_text() + 'changed')
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 2
    assert not (root / 'one-r1.count').exists()
    run = start()
    deadline = time.monotonic() + 15
    observations = []
    while True:
        code, values, error = invoke(capsys, 'run-round', '--run-dir', str(run))
        observations.append((code, values, error))
        # Exit 4 asks the consumer to observe the same owned attempt again;
        # hard errors must fail immediately rather than start another attempt.
        if code != 4 or time.monotonic() >= deadline:
            break
        time.sleep(.05)
    assert code == 0, {
        'observations': observations, 'state': (run / 'state.json').read_text(),
        'stderr': {p.name: p.read_text(errors='replace')
                   for p in (run / 'rounds/round-01').glob('*-stderr.log')}}
    for model in ('one', 'two'):
        assert (root / f'{model}-r1.count').read_text() == '1'
    output = run / 'rounds/round-01/agent-1-output.md'; output.write_text(output.read_text() + 'changed')
    assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(decision_file(root, 'CONTINUE')))[0] == 2
    assert not (run / 'rounds/round-02/snapshot.md').exists()


def test_stdin_start_defaults_config_secrecy_and_side_effect_free_status(pair_fixture, monkeypatch, capsys):
    import io
    import sys
    _, root, _ = pair_fixture
    monkeypatch.setattr(sys, 'stdin', io.StringIO('A problem supplied on stdin.\n'))
    monkeypatch.setenv('PAIR_SECRET_FIXTURE', 'should-never-be-persisted')
    code, values, err = invoke(capsys, 'start', '-')
    assert code == 0, err
    run = Path(values['RUN_DIR']); state = json.loads((run / 'state.json').read_text())
    assert state['timeout_seconds'] == 3600 and state['min_rounds'] == 3 and state['max_rounds'] == 8
    assert 'should-never-be-persisted' not in (run / 'config.json').read_text()
    before = {str(p.relative_to(run)): (p.read_bytes(), p.stat().st_mtime_ns) for p in run.rglob('*') if p.is_file()}
    assert invoke(capsys, 'status', '--run-dir', str(run))[0] == 0
    assert before == {str(p.relative_to(run)): (p.read_bytes(), p.stat().st_mtime_ns) for p in run.rglob('*') if p.is_file()}
    assert invoke(capsys, '--help')[0] == 0


@pytest.mark.parametrize('flag,value', [('--agent-1-effort', 'ultra'), ('--timeout-seconds', '0'), ('--timeout-seconds', 'bad'), ('--agent-1-name', 'bad,name')])
def test_invalid_config_fails_before_run_creation(pair_fixture, capsys, flag, value):
    _, root, _ = pair_fixture
    assert invoke(capsys, 'start', str(root / 'task.md'), flag, value)[0] == 2
    assert not (root / '.pair-colleague').exists()


def test_maximum_round_policy_and_advisory_ready_cannot_end_discussion(pair_fixture, capsys):
    start, root, _ = pair_fixture
    run = start('--min-rounds', '2', '--max-rounds', '2')
    for n in (1, 2):
        assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
        if n == 1:
            assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(decision_file(root, 'ACCEPT')))[0] == 2
            action = 'CONTINUE'
        else:
            assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(decision_file(root, 'CONTINUE')))[0] == 2
            action = 'ACCEPT'
        assert invoke(capsys, 'record-decision', '--run-dir', str(run), '--decision-file', str(decision_file(root, action)))[0] == 0
    assert json.loads((run / 'state.json').read_text())['completion_reason'] == 'maximum-rounds'


def test_prompt_snapshot_and_successful_attempt_are_frozen_on_retry(pair_fixture, monkeypatch, capsys):
    start, root, _ = pair_fixture
    monkeypatch.setenv('FAKE_MODE', 'fail-once')
    run = start()
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 1
    directory = run / 'rounds/round-01'
    frozen = {name: (directory / name).read_bytes() for name in ('snapshot.md', 'agent-1-output.md', 'agent-2-prompt.md')}
    # Upgrading the maintained prompt does not change a run's frozen copy.
    replacement = root / 'new-template.md'; replacement.write_text('Invalid updated prompt {{BROKEN}}')
    monkeypatch.setenv('PAIR_COLLEAGUE_PROMPT_FILE', str(replacement))
    monkeypatch.setenv('TIMEOUT_SECONDS', '1')
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    assert frozen == {name: (directory / name).read_bytes() for name in frozen}
    assert json.loads((directory / 'agent-2-attempt-02-run.json').read_text())['deadline_epoch'] > time_now() + 3500


def time_now():
    import time
    return time.time()


def test_arbitrary_command_override_rejected(pair_fixture, capsys):
    _, root, _ = pair_fixture
    assert invoke(capsys, 'start', str(root / 'task.md'), '--command', 'do-not-run')[0] == 2
    assert not (root / '.pair-colleague').exists()


@pytest.mark.parametrize('active_manifest', [False, True])
def test_installation_assets_root_uses_packaged_pair_resources(pair_fixture, monkeypatch, capsys, active_manifest):
    start, root, _ = pair_fixture
    assets = root / 'installed-assets'; assets.mkdir()
    if active_manifest: (assets / 'runtime.json').write_text('{"schema_version":1,"python":"unused in direct API test"}')
    monkeypatch.setenv('ASSETS_DEST', str(assets))
    run = start()
    config = json.loads((run / 'config.json').read_text())
    assert 'installed-assets' not in config['prompt_template']
    assert (run / 'agent-prompt-template.md').is_file()


def test_explicit_pair_assets_override_still_validates_complete_resources(pair_fixture, monkeypatch, capsys):
    _, root, _ = pair_fixture
    assets = root / 'explicit-pair-assets'; assets.mkdir()
    monkeypatch.setenv('PAIR_COLLEAGUE_ASSETS_DIR', str(assets))
    code, _, error = invoke(capsys, 'start', str(root / 'task.md'))
    assert code == 2 and 'missing agent-registry.json' in error
    assert not (root / '.pair-colleague').exists()


def test_pair_attempt_invalid_effort_does_not_restart_provider(pair_fixture, capsys, monkeypatch):
    from ai_skills.pair import _attempt
    start, root, registry_file = pair_fixture
    _use_direct_harness_registry(registry_file)
    run = start(*_direct_args('codex-alias', 'high'))
    assert invoke(capsys, 'run-round', '--run-dir', str(run))[0] == 0
    prefix = run / 'rounds/round-01/agent-2-attempt-01'
    spec_path = Path(str(prefix) + '-spec.json')
    spec = json.loads(spec_path.read_text())
    spec['agent']['effort'] = 'ultra'
    spec_path.write_text(json.dumps(spec))
    marker = root / 'two-argv.json'
    marker.unlink()
    monkeypatch.setattr('ai_skills.pair.signal.signal', lambda *args: None)
    assert _attempt(spec_path) == 2
    assert 'unknown effort' in json.loads(Path(spec['record']).read_text())['error']
    assert not marker.exists()
