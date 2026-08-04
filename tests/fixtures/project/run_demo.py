"""Script entrypoint fixture: its own imports have no parent row."""

import demopkg

raise SystemExit(0 if demopkg.slow_a.VALUE else 1)
