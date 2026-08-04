"""Small helper module pulling in a real (non-bootstrap) stdlib module."""

import decimal


def build_label(value: str) -> str:
    """Return a label for a value."""
    return f"{value}:{decimal.Decimal(1)}"
