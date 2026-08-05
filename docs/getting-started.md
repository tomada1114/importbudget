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

!!! note "Modules and scripts"

    A `.py` suffix always means a script, and a valid dotted name always means a
    module — whatever files happen to sit in the working directory. To profile an
    extensionless script whose name is also a valid module name, give it as a
    path: `importbudget profile ./mytool`.

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

## Planning lazy imports

`importbudget plan` joins a profile with the PEP 810 safety rules and proposes
the import statements that can safely become `lazy` imports:

```bash
importbudget plan mypackage                    # measure, then plan
importbudget plan --from-profile profile.json  # plan from a saved profile
```

It accepts the same entrypoint forms and the same `--runs` / `--warmup` /
`--top` / `--json` flags as `profile`, plus:

| Flag | Meaning |
|---|---|
| `--from-profile PATH` | plan from a saved `profile --json` document instead of measuring; `--runs` and `--warmup` are then unused |
| `--min-ms X` | do not propose statements attributed less than X ms; they are still listed, as skipped by the threshold (default 0) |

!!! note "Excluded means *not proven safe*"

    The rule set is a whitelist: a statement is proposed only when every rule
    proves it convertible, and a rule that cannot decide rejects rather than
    abstains. Each excluded row carries machine-readable reason codes
    (`STAR_IMPORT`, `TRY_EXCEPT_IMPORT`, `MODULE_LEVEL_USE`, …) so you can
    branch on them in scripts.

    The predicted saving is an **upper bound**, not a promise: a module is paid
    for once, by whichever statement imports it first, so converting several
    statements saves less than the sum of their rows. Measure the real
    difference after converting.

```python
from importbudget import PlanOptions, plan, render_plan_table

result = plan("mypackage", PlanOptions(min_us=5000))
print(render_plan_table(result))
```

## What's Next?

See the [API Reference](reference.md) for the complete API documentation.
