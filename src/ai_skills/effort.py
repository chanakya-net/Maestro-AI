"""Pure, shared reasoning-effort policy for direct Claude and Codex runs."""
from __future__ import annotations

from dataclasses import dataclass
import json
import tomllib

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


def _settings_effort(value):
    try:
        settings = json.loads(value)
    except (ValueError, TypeError):
        return False
    if not isinstance(settings, dict):
        return False
    env = settings.get('env')
    return 'effortLevel' in settings or (isinstance(env, dict) and 'CLAUDE_CODE_EFFORT_LEVEL' in env)


def _codex_effort(value):
    # Parse only the TOML key, not the value: invalid values must also be rejected.
    for index, char in enumerate(value):
        if char != '=':
            continue
        try:
            key = tomllib.loads(value[:index] + '=0')
        except tomllib.TOMLDecodeError:
            continue
        while isinstance(key, dict) and len(key) == 1:
            name, key = next(iter(key.items()))
            if name == 'model_reasoning_effort':
                return True
        return False
    return False


def raw_effort_overrides(harness, extra_args, registry=None):
    """Find known provider effort settings without changing argv or user files."""
    name = canonical_harness(harness, registry)
    found = []
    for index, arg in enumerate(extra_args):
        following = extra_args[index + 1] if index + 1 < len(extra_args) else ''
        if name == 'claude':
            if arg == '--effort' or arg.startswith('--effort='):
                found.append(arg)
            elif arg == '--settings' and _settings_effort(following):
                found.append(arg)
            elif arg.startswith('--settings=') and _settings_effort(arg[len('--settings='):]):
                found.append(arg)
        elif name == 'codex':
            if arg in ('-c', '--config'):
                value = following
            elif arg.startswith('--config='):
                value = arg[len('--config='):]
            elif arg.startswith('-c') and not arg.startswith('--'):
                value = arg[2:].removeprefix('=')
            else:
                continue
            if _codex_effort(value):
                found.append(arg)
    return tuple(found)
