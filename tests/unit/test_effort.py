import pytest

from ai_skills.effort import EffortResolution, resolve_effort
from ai_skills.registry import load_registry

TOKENS = ('low', 'medium', 'high', 'xhigh', 'max', 'maximum')


@pytest.mark.parametrize('token', TOKENS)
def test_codex_non_planning_is_always_high(token):
    assert resolve_effort('codex', token, 'impl').effective == 'high'


@pytest.mark.parametrize('token', TOKENS)
def test_codex_planning_is_always_xhigh(token):
    assert resolve_effort('codex', token, 'plan').effective == 'xhigh'


@pytest.mark.parametrize('token,expected', [('low', 'low'), ('medium', 'medium'), ('high', 'high'),
                                            ('xhigh', 'high'), ('max', 'high'), ('maximum', 'high')])
def test_claude_non_planning_caps_at_high(token, expected):
    assert resolve_effort('claude', token, 'impl').effective == expected


@pytest.mark.parametrize('token,expected', [('low', 'low'), ('medium', 'medium'), ('high', 'high'),
                                            ('xhigh', 'xhigh'), ('max', 'xhigh'), ('maximum', 'xhigh')])
def test_claude_planning_keeps_up_to_xhigh(token, expected):
    assert resolve_effort('claude', token, 'plan').effective == expected


@pytest.mark.parametrize('harness', ('codex', 'claude'))
def test_absent_defaults(harness):
    assert resolve_effort(harness, '', 'impl').effective == 'high'
    assert resolve_effort(harness, '', 'plan').effective == 'xhigh'


def test_requested_effort_reported_only_when_changed():
    changed = resolve_effort('claude', 'max', 'impl')
    assert changed == EffortResolution('high', 'max')
    assert resolve_effort('claude', 'low', 'impl').requested == ''
    assert resolve_effort('claude', '', 'impl').requested == ''


@pytest.mark.parametrize('harness', ('codex', 'claude', 'agy', 'opencode'))
def test_unknown_token_fails(harness):
    with pytest.raises(ValueError, match='unknown effort'):
        resolve_effort(harness, 'ultra', 'impl')


@pytest.mark.parametrize('harness', ('agy', 'opencode', 'other'))
def test_other_harnesses_unchanged(harness):
    assert resolve_effort(harness, 'max', 'plan') == EffortResolution('max', '')
    assert resolve_effort(harness, '', 'impl') == EffortResolution('', '')


def test_aliases_resolve_through_registry():
    registry = load_registry()
    assert resolve_effort('codex-cli', 'low', 'impl', registry).effective == 'high'
    assert resolve_effort('claude-code', 'max', 'impl', registry).effective == 'high'
