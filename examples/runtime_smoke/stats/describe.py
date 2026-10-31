import math
from typing import Iterable


def describe_readings(values: Iterable[int | float]) -> dict:
    """
    Consume a finite iterable once. Return exactly count, total, minimum,
    maximum, mean in a dictionary.

    Empty input: count=0, total=0, minimum/maximum/mean=None.
    Nonempty: conventional numeric statistics, preserving source input type.

    Accept int/float; reject bool, nonnumeric, NaN and infinity with ValueError.
    """
    count = 0
    total = 0
    minimum = None
    maximum = None

    for value in values:
        # Reject bool explicitly (bool is int subclass)
        if isinstance(value, bool):
            raise ValueError(f"bool value not allowed: {value}")

        # Reject non-numeric
        if not isinstance(value, (int, float)):
            raise ValueError(f"non-numeric value not allowed: {value}")

        # Reject NaN and infinity
        if isinstance(value, float):
            if math.isnan(value):
                raise ValueError(f"NaN value not allowed")
            if math.isinf(value):
                raise ValueError(f"infinite value not allowed: {value}")

        count += 1
        total += value

        if minimum is None:
            minimum = value
        else:
            minimum = min(minimum, value)

        if maximum is None:
            maximum = value
        else:
            maximum = max(maximum, value)

    mean = total / count if count > 0 else None

    return {
        "count": count,
        "total": total,
        "minimum": minimum,
        "maximum": maximum,
        "mean": mean,
    }
