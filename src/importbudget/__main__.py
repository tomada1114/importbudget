"""Allow ``python -m importbudget`` to run the command line interface."""

from __future__ import annotations

from .cli import main

raise SystemExit(main())
