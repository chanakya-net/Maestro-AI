"""Compose normalized labels and duration totals."""

from examples.live_e2e_pr169_four.durations import total_minutes
from examples.live_e2e_pr169_four.labels import normalize_label


def build_summary(label, minutes):
    """Return a normalized label and the total duration in minutes."""
    return {
        "label": normalize_label(label),
        "total_minutes": total_minutes(minutes),
    }
