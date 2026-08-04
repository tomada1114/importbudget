"""Module executed by ``python -m demopkg.cli``."""

from . import slow_a  # DUPLICATE: demopkg/__init__.py imported it first
from .util import build_label


def main() -> int:
    """Print a label and exit."""
    print(build_label(slow_a.VALUE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
