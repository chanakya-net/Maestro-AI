"""Decode final responses from the admitted provider wire format."""
from __future__ import annotations

import json

CODEX = 'codex-last-message'
CLAUDE = 'claude-json'
AGY = 'agy-stream-json'
OPENCODE_VERSION = '1.16.2'
OPENCODE = 'opencode-json-v' + OPENCODE_VERSION
STREAM_SOURCES = frozenset((CLAUDE, AGY, OPENCODE))


class ProviderError(ValueError):
    """The provider explicitly reported a failed request."""


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('duplicate provider JSON member')
    return result


def _invalid_constant(_value):
    raise ValueError('invalid provider JSON constant')


def _json_object(raw):
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(value, dict):
        raise ValueError('expected a provider JSON object')
    return value


def _claude_response(stream):
    event = _json_object(stream.read())
    if event.get('type') != 'result':
        raise ValueError('missing Claude terminal result')
    error = event.get('errors') or event.get('error')
    if event.get('is_error') is True or str(event.get('subtype', '')).startswith('error') or error:
        raise ProviderError(error or event.get('result') or event.get('subtype'))
    if event.get('subtype') != 'success' or event.get('is_error') is not False:
        raise ValueError('missing successful Claude terminal result')
    return event.get('result')


def _json_events(stream):
    for raw in stream:
        if raw.strip():
            if not raw.endswith(b'\n'):
                raise ValueError('incomplete provider stream line')
            yield _json_object(raw)


def agy_result_response(result, *, strict=False):
    """Shared terminal payload for narrative output and managed capture."""
    response = result.get('response') if isinstance(result, dict) else None
    if strict or isinstance(response, str):
        error = result.get('error')
        if result.get('status') != 'SUCCESS' or (strict and error):
            raise ProviderError('AGY result failed: ' + str(error or result.get('status')))
    if not isinstance(response, str):
        raise ValueError('AGY stream ended without a complete terminal result.response')
    return response


def _agy_step(event, conversation):
    step = event.get('step_update')
    if (not isinstance(step, dict) or step.get('conversation_id') != conversation or
            type(step.get('step_index')) is not int or step['step_index'] < 0 or
            step.get('state') not in ('ACTIVE', 'DONE') or not isinstance(step.get('step_type'), str)):
        raise ValueError('invalid Agy step')
    return step


def _agy_response(stream):
    events = _json_events(stream)
    init = next(events, {})
    conversation = init.get('conversation_id')
    if (init.get('event') != 'init' or not isinstance(init.get('init'), dict) or
            not isinstance(conversation, str) or not conversation):
        raise ValueError('missing Agy conversation initialization')
    user_step = None
    user_done = False
    for event in events:
        kind = event.get('event')
        if kind == 'result':
            result = event.get('result')
            if (not isinstance(result, dict) or result.get('conversation_id') != conversation or
                    next(events, None) is not None):
                raise ValueError('invalid or ambiguous Agy terminal result')
            response = agy_result_response(result, strict=True)
            if user_done:
                return response
            break
        if kind != 'step_update':
            raise ValueError('unexpected Agy event')
        step = _agy_step(event, conversation)
        if step['step_type'] == 'user_input':
            if user_done or user_step not in (None, step['step_index']):
                raise ValueError('unexpected extra Agy user turn')
            user_step, user_done = step['step_index'], step['state'] == 'DONE'
        elif not user_done or step['step_index'] <= user_step:
            raise ValueError('Agy step precedes the submitted turn')
    raise ValueError('missing complete Agy turn')


_OPENCODE_PART_TYPES = {'step_start': 'step-start', 'step_finish': 'step-finish',
                        'text': 'text', 'tool_use': 'tool', 'reasoning': 'reasoning'}


def _opencode_part(event, session):
    kind, part = event.get('type'), event.get('part')
    if not session or not isinstance(session, str) or event.get('sessionID') != session:
        raise ValueError('invalid OpenCode session')
    if kind == 'error':
        raise ProviderError(event.get('error'))
    if (not isinstance(kind, str) or kind not in _OPENCODE_PART_TYPES or
            not isinstance(part, dict) or part.get('type') != _OPENCODE_PART_TYPES[kind] or
            part.get('sessionID') != session or not _string_id(part, 'messageID') or not _string_id(part, 'id')):
        raise ValueError('invalid OpenCode event part')
    return kind, part


def _string_id(value, key):
    return isinstance(value.get(key), str) and bool(value[key])


def _opencode_response(stream):
    session = message = reason = None
    text = []
    has_tool = False
    for event in _json_events(stream):
        session = session or event.get('sessionID')
        kind, part = _opencode_part(event, session)
        if kind == 'step_start':
            if message is not None or (reason == 'stop' and not has_tool):
                raise ValueError('overlapping or ambiguous OpenCode steps')
            message, reason, text, has_tool = part['messageID'], None, [], False
            continue
        if message != part['messageID']:
            raise ValueError('OpenCode event outside its assistant step')
        if kind == 'step_finish':
            reason, message = part.get('reason'), None
            if reason == 'error':
                raise ProviderError('OpenCode step failed')
            if not isinstance(reason, str) or not reason:
                raise ValueError('missing OpenCode finish reason')
        elif kind == 'text':
            timing = part.get('time')
            if (not isinstance(timing, dict) or type(timing.get('end')) is not int or
                    not isinstance(part.get('text'), str)):
                raise ValueError('incomplete OpenCode text part')
            text.append(part['text'])
        elif kind == 'tool_use':
            state = part.get('state')
            if not isinstance(state, dict) or state.get('status') not in ('completed', 'error'):
                raise ValueError('incomplete OpenCode tool event')
            has_tool = True
    if message is not None or reason != 'stop' or has_tool or len(text) != 1:
        raise ValueError('missing unambiguous final OpenCode response')
    return text[0]


def extract_response(source, stream):
    if source == CODEX:
        return stream.read()
    extractor = {CLAUDE: _claude_response, AGY: _agy_response, OPENCODE: _opencode_response}.get(source)
    if extractor is None:
        raise ValueError('unknown provider capture source')
    response = extractor(stream)
    if not isinstance(response, str):
        raise ValueError('missing provider response text')
    return response.encode('utf-8')
