"""Actionable diagnostics for explicit AGY model selections.

The patterns apply only to AGY provider diagnostics (the terminal result error
or stderr), never to model-generated stream text. The unknown-model and
authentication wording follows https://antigravity.google/docs/cli/headless/.
"""
from __future__ import annotations

import re

_FORMAT_VALUE = rb'["\']?[\w-]+["\']?'
_FORMAT_CHOICES = re.compile(
    rb'[(\[<{]\s*(?:(?:choices|possible values):\s*)?('
    + _FORMAT_VALUE + rb'(?:\s*[,|/]\s*' + _FORMAT_VALUE + rb')*)'
    rb'(?:\s+only)?\s*[)\]>}]', re.IGNORECASE,
)
_KNOWN_FORMATS = {b'text', b'json', b'stream-json'}

_PATTERNS = (
    ('auth', re.compile(r'authentication required|not authenticated|unauthenticated|not signed in', re.IGNORECASE)),
    ('quota', re.compile(r'resource[_ ]exhausted|quota exceeded', re.IGNORECASE)),
)

_REMEDIES = {
    'model-unsupported': ('check the exact slug with `agy models`; if the model is absent, verify access for the '
                          'signed-in account or ask its administrator'),
    'auth': 'authenticate interactively by running `agy` and signing in, then retry',
    'quota': 'check AGY usage and retry after the quota resets',
}


def supports_stream_json(option: bytes) -> bool:
    """Accept an unspecified format list, but reject an incompatible one."""
    for match in _FORMAT_CHOICES.finditer(option.lower()):
        choices = set(re.findall(rb'[\w-]+', match[1]))
        if choices & _KNOWN_FORMATS:
            return b'stream-json' in choices
    return True


def classify(text: str, model: str) -> str:
    """Map an AGY provider diagnostic to an existing unavailability reason."""
    model_term = r'\bmodel\b(?![./\\])'
    slug = re.escape(model)
    target = rf'(?:{model_term}|["\']?{slug}(?![\w./\\-]))' if model else model_term
    model_failure = (
        r'\binvalid model selection\b|\bnot recognized as a known model\b'
        rf'|\b(?:unknown|unsupported) {model_term}'
        rf'|{model_term} (?:["\']?{slug}["\']? )?(?:is )?not supported\b'
        r'|\bmodel access (?:is )?denied\b'
        r'|\b(?:(?:access|permission) denied (?:for|to)|not entitled to'
        rf'|does not have access to|doesn\'t have access to) (?:the )?(?:requested )?{target}'
    )
    if re.search(model_failure, text, re.IGNORECASE):
        return 'model-unsupported'
    for reason, pattern in _PATTERNS:
        if pattern.search(text):
            return reason
    return ''


def failure_message(model: str, reason: str) -> str:
    """Human guidance naming the exact requested model and its remedy."""
    remedy = _REMEDIES.get(reason, 'retry the request')
    return f'AGY could not run requested model {model}: {remedy}.'


def malformed_message(model: str, error: Exception) -> str:
    return f'AGY returned malformed output for requested model {model}; no result was accepted: {error}'
