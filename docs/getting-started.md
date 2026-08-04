# Getting Started

## Installation

```bash
pip install importbudget
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add importbudget
```

## Basic Usage

`importbudget profile` runs an entrypoint under `python -X importtime`,
attributes each module's self time to the first import statement that imported
it, and prints the most expensive statements:

```bash
importbudget profile mypackage            # import the package
importbudget profile -m mypackage.cli     # run it with -m
importbudget profile scripts/run.py       # run a script
```

Useful flags:

| Flag | Meaning |
|---|---|
| `--runs N` | measured runs to average (default 3) |
| `--warmup N` | runs discarded first, to pay the cold page cache (default 1) |
| `--top N` | statements shown in the table, `0` for all (default 10) |
| `--json` | emit the versioned JSON document instead of the table |

!!! note "Reading the table"

    The `self ms` column sums to the measured total, so those numbers are
    additive. The `potential` column is the cost of everything a statement
    pulls in; neighbouring rows overlap, so it must never be summed.

    Anything the profiled program prints to stderr is reported under
    `entrypoint stderr:` rather than mixed into importbudget's own warnings,
    and only the first few distinct lines are kept.

The same thing from Python:

```python
from importbudget import RunOptions, profile, render_table

result = profile("mypackage", RunOptions(runs=3, warmup=1))
print(render_table(result, top=10))
```

## What's Next?

See the [API Reference](reference.md) for the complete API documentation.
