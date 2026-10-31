"""Normalize finite numeric readings to inclusive bounds."""

import math


def _validate_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be an int or float")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} must be finite")


def normalize_readings(values, *, minimum=0, maximum=100) -> list[int | float]:
    """Return a new, ordered list of readings clamped to inclusive bounds.

    ``values`` is consumed once and never mutated. Readings and bounds must be
    finite ``int`` or ``float`` values, with ``minimum <= maximum``; otherwise
    ``ValueError`` is raised.
    """
    _validate_number(minimum, "minimum")
    _validate_number(maximum, "maximum")
    if minimum > maximum:
        raise ValueError("minimum must not exceed maximum")

    normalized = []
    for value in values:
        _validate_number(value, "reading")
        normalized.append(min(maximum, max(minimum, value)))
    return normalized
