from pathlib import Path
import pytest
from ai_skills import pair_documents as d


def document(sections, level=1):
    return '\n\n'.join('#' * level + ' ' + s + '\n\nEvidence supports this choice.' for s in sections)


def test_valid_contracts_and_exact_trailing_status(tmp_path):
    p = tmp_path / 'agent.md'
    p.write_text(document(d.AGENT_SECTIONS) + '\n\nDISCUSSION_STATUS: READY\n')
    assert d.validate_agent_output(p) == []
    p.write_text(p.read_text() + 'extra\n')
    assert any('after' in e for e in d.validate_agent_output(p))


@pytest.mark.parametrize('mutation', ['empty', 'duplicate', 'reorder', 'placeholder', 'variable', 'status', 'multiple'])
def test_reject_malformed_output(tmp_path, mutation):
    text = document(d.AGENT_SECTIONS) + '\n\nDISCUSSION_STATUS: CONTINUE\n'
    text = {'empty': '', 'duplicate': '# Candidate analysis\n\nDuplicate\n' + text,
            'reorder': text.replace('# Candidate analysis', '# Swapped').replace('# Preferred solution', '# Candidate analysis') + '\n# Preferred solution\nText',
            'placeholder': text.replace('Evidence supports', 'TODO supports'),
            'variable': text.replace('Evidence supports', '{{NAME}} supports'),
            'status': text.replace('CONTINUE', 'CONTINUE extra'),
            'multiple': text + '\nDISCUSSION_STATUS: READY\n'}[mutation]
    p = tmp_path / 'agent.md'; p.write_text(text)
    assert d.validate_agent_output(p)


def test_fenced_examples_do_not_satisfy_structure_or_violate_placeholders(tmp_path):
    p = tmp_path / 'decision.md'
    text = document(d.DECISION_SECTIONS)
    p.write_text(text + '\n```markdown\n# Round assessment\nTODO {{NAME}}\nCOORDINATOR_ACTION: BAD\n```\n\nCOORDINATOR_ACTION: ACCEPT\n')
    assert d.validate_decision(p) == []
    p.write_text('```markdown\n' + text + '\n```\nCOORDINATOR_ACTION: CONTINUE\n')
    assert d.validate_decision(p)


def test_snapshot_constraints_and_bounded_history(tmp_path):
    (tmp_path / 'task.md').write_text('Choose storage.\n# Constraints and success criteria\nMust survive restarts.\n')
    one = d.build_round_one_snapshot(str(tmp_path))
    assert one.count('Must survive restarts.') == 1
    assert 'Candidate-solution ledger' not in one
    r = tmp_path / 'rounds' / 'round-01'; r.mkdir(parents=True)
    (r / 'coordinator-decision.md').write_text(document(d.DECISION_SECTIONS))
    for slot in ('1', '2'): (r / f'agent-{slot}-output.md').write_text('Previous ' + slot)
    two = d.build_next_snapshot(str(tmp_path), 2)
    assert 'Previous 1' in two and 'Previous 2' in two
    assert 'Candidate-solution ledger' in two


def test_dossier_requires_exact_provenance_identity_and_every_section(tmp_path):
    expected = {'completed_rounds': '3', 'min_rounds': '3', 'max_rounds': '8', 'completion_reason': 'coordinator-accepted', 'run_dir': str(tmp_path)}
    agents = {s: {'name': 'Agent ' + s, 'harness': 'fake', 'model': 'model-' + s, 'effort': 'high'} for s in ('1', '2')}
    agents['1']['requested_effort'] = 'maximum'
    record = '\n'.join('- ' + label + ': ' + expected[key] for label, key in d.DOSSIER_RUN_LABELS)
    record += '\n- Coordinator: current agent\n'
    for slot, agent in agents.items():
        values = [agent[key] for key in ('name', 'harness', 'model', 'effort')]
        if agent.get('requested_effort'):
            values.append('requested effort ' + agent['requested_effort'])
        record += '- Agent ' + slot + ': ' + ', '.join(values) + '\n'
    text = '# Final Solution Decision\n\n' + '\n\n'.join('## ' + s + '\n\n' + (record if s == 'Discussion record' else 'Concrete evidence and conclusions.') for s in d.DOSSIER_SECTIONS)
    p = tmp_path / 'final.md'; p.write_text(text)
    assert d.validate_dossier(p, expected, {'agents': agents}) == []
    p.write_text(text.replace(', requested effort maximum', ''))
    assert any("Agent 1" in error for error in d.validate_dossier(p, expected, {'agents': agents}))
    for changed in (text.replace('- Completed rounds: 3', '- Completed rounds: 2'), text.replace('model-1', 'invented'), text.replace('## Risks and mitigations', '## Wrong heading'), text + '\n## Discussion record\nDuplicate', text.replace('Concrete evidence', '<unfilled placeholder>\n\nConcrete evidence')):
        p.write_text(changed)
        assert d.validate_dossier(p, expected, {'agents': agents})
    p.write_text(text + '\n```markdown\n## Discussion record\n- Completed rounds: 999\nTODO {{NAME}}\n```\n')
    assert d.validate_dossier(p, expected, {'agents': agents}) == []


def test_freeze_snapshot_idempotent_and_rejects_changed_inputs(tmp_path):
    (tmp_path / 'task.md').write_text('Original problem.')
    first = d.freeze_snapshot(tmp_path, 1)
    assert d.freeze_snapshot(tmp_path, 1) == first
    (tmp_path / 'task.md').write_text('Changed problem.')
    with pytest.raises(d.HelperError, match='different content'): d.freeze_snapshot(tmp_path, 1)


def test_historical_dossier_identity_keeps_legacy_effort_without_provenance():
    agent = {'name': 'Agent 1', 'harness': 'codex', 'model': 'model-1', 'effort': 'xhigh'}
    assert d.agent_identity_errors('- Agent 1: Agent 1, codex, model-1, xhigh', '1', agent) == []
    assert d.agent_identity_errors('- Agent 1: Agent 1, codex, model-1, high', '1', agent)


def test_dossier_reports_changed_attempt_without_relabeling_historical_identity():
    record = '- Agent 1: Agent 1, codex, model-1, xhigh\n'
    attempt = {'round': 2, 'slot': '1', 'attempt': 1,
               'agent': {'effort': 'high', 'requested_effort': 'xhigh'}}
    assert d.attempt_provenance_errors(record, [attempt])
    record += '- Round 2 Agent 1 attempt 1: high, requested effort xhigh\n'
    assert d.attempt_provenance_errors(record, [attempt]) == []
    assert d.attempt_provenance_errors(record.replace('attempt 1: high', 'attempt 1: xhigh'), [attempt])
