"""Compose normalized reading values with their summary statistics."""

from examples.runtime_smoke.ranges.normalize import normalize_readings
from examples.runtime_smoke.stats.describe import describe_readings


def summarize_readings(values, *, minimum=0, maximum=100) -> dict:
    """Return statistics and the normalized readings used to calculate them."""
    normalized = normalize_readings(values, minimum=minimum, maximum=maximum)
    summary = dict(describe_readings(normalized))
    summary["values"] = normalized
    return summary
