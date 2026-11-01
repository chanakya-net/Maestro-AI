from collections.abc import Iterable


def total_minutes(values: Iterable[object]) -> int:
    """Return the sum of nonnegative integer durations."""
    total = 0
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("durations must be integers")
        if value < 0:
            raise ValueError("durations must be nonnegative")
        total += value
    return total
