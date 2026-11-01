"""Label normalization for the PR169 live fixture."""

__all__ = ["normalize_label"]


def normalize_label(value):
    """Return a canonical label: trimmed, whitespace-collapsed, lowercased.

    Raises TypeError for non-str input and ValueError when the result is empty.
    Punctuation and Unicode text are preserved.
    """
    if not isinstance(value, str):
        raise TypeError(f"label must be str, got {type(value).__name__}")
    normalized = " ".join(value.split()).lower()
    if not normalized:
        raise ValueError("label must not be empty or whitespace-only")
    return normalized
