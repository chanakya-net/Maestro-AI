"""Actionable diagnostics for explicit AGY model selections.

The patterns apply only to AGY provider diagnostics (the terminal result error
or stderr), never to model-generated stream text. The unknown-model and
authentication wording follows https://antigravity.google/docs/cli/headless/.
"""
from __future__ import annotations

import re

_PATTERNS = (
    ('model-unsupported', re.compile(
        r'invalid model selection|not recognized as a known model|unknown model'
        r'|\b(?:access|permission)\b[^\n]{0,40}\bdenied\b[^\n]{0,60}\bmodel\b'
        r'|\bmodel\b[^\n]{0,60}\b(?:access|permission)\b[^\n]{0,20}\bdenied\b'
        r'|\b(?:not entitled to|(?:does not|doesn\'t) have access to)\b[^\n]{0,20}\bmodel\b', re.IGNORECASE)),
    ('auth', re.compile(r'authentication required|not authenticated|unauthenticated|not signed in', re.IGNORECASE)),
    ('quota', re.compile(r'resource[_ ]exhausted|quota exceeded', re.IGNORECASE)),
)

_REMEDIES = {
    'model-unsupported': ('check the exact slug with `agy models`; if it is listed, verify the signed-in account '
                          'has access to it or ask the account administrator'),
    'auth': 'authenticate interactively by running `agy` and signing in, then retry',
    'quota': 'check AGY usage and retry after the quota resets',
}


def classify(text: str) -> str:
    """Map an AGY provider diagnostic to an existing unavailability reason."""
    for reason, pattern in _PATTERNS:
        if pattern.search(text):
            return reason
    return ''


def failure_message(model: str, reason: str, detail: str = '') -> str:
    """Human guidance naming the exact requested model and its remedy."""
    remedy = _REMEDIES.get(reason, 'inspect the provider error and retry; no other model was selected')
    message = f'AGY could not run requested model {model}: {remedy}.'
    return message + (f' Provider error: {detail}' if detail else '')


def malformed_message(model: str, error: Exception) -> str:
    return f'AGY returned malformed output for requested model {model}; no result was accepted: {error}'
