"""Pure, shared reasoning-effort policy for direct Claude and Codex runs."""
from __future__ import annotations

from dataclasses import dataclass

RECOGNIZED = ('low', 'medium', 'high', 'xhigh', 'max', 'maximum')
PLANNING_ROLES = ('plan',)
_CLAUDE_NORMAL = {'low': 'low', 'medium': 'medium', 'high': 'high',
                  'xhigh': 'high', 'max': 'high', 'maximum': 'high'}
_CLAUDE_PLAN = {'low': 'low', 'medium': 'medium', 'high': 'high',
                'xhigh': 'xhigh', 'max': 'xhigh', 'maximum': 'xhigh'}


@dataclass(frozen=True)
class EffortResolution:
    effective: str
    requested: str = ''


def canonical_harness(harness, registry=None):
    return (registry or {}).get('aliases', {}).get(harness, harness)


def resolve_effort(harness, effort, role, registry=None) -> EffortResolution:
    """Return effective effort; `requested` is set only when an explicit value changed."""
    effort = (effort or '').strip().lower()
    if effort and effort not in RECOGNIZED:
        raise ValueError(f'unknown effort: {effort} (expected one of {", ".join(RECOGNIZED)})')
    name = canonical_harness(harness, registry)
    if name not in ('codex', 'claude'):
        return EffortResolution(effort)
    planning = role in PLANNING_ROLES
    if name == 'codex':
        effective = 'xhigh' if planning else 'high'
    elif not effort:
        effective = 'xhigh' if planning else 'high'
    else:
        effective = (_CLAUDE_PLAN if planning else _CLAUDE_NORMAL)[effort]
    return EffortResolution(effective, effort if effort and effort != effective else '')
