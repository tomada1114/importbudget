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

## Applying a plan

`importbudget apply` rewrites the statements a plan proved safe. It reads a
saved plan document, never a live measurement, so you can inspect exactly what
will change before anything is written:

```bash
importbudget plan mypackage --json > plan.json
importbudget apply plan.json                        # dry run: prints a diff
importbudget apply plan.json --write                # rewrite the files
importbudget apply plan.json --target-version 3.13  # pre-3.15 fallback
```

| Flag | Meaning |
|---|---|
| `--write` | write the converted files to disk; without it nothing on disk changes |
| `--target-version X.Y` | interpreter the converted source must run on (`3.11`–`3.15`, default `3.15`) |
| `--json` | emit the machine-readable apply document instead of the report |

Conversion is deliberately narrow. Only statements sitting directly in the
module body are rewritten, only into `lazy import x [as y]` and
`lazy from x import y [as z]`, and never onto a physical line that carries more
than one statement. Anything else keeps a machine-readable reason code:

| Code | Meaning |
|---|---|
| `UNSUPPORTED_FORM` | a shape outside the four emittable ones — `import a, b`, `import a.b.c`, `from . import x`, parenthesized or multi-name from-imports |
| `FALLBACK_UNSUPPORTED` | a `from x import y` under `--target-version` below 3.15 |
| `COMPOUND_LINE` | the line holds more than one statement |
| `ALREADY_LAZY` | the name is already bound lazily, so re-running is a no-op |
| `SOURCE_MISMATCH` | the file changed since the plan was written |
| `NOT_FOUND` | the plan entry does not point at an import statement |

!!! warning "Run your tests after `--write`"

    A lazy import defers the target module's import-time side effects and moves
    any `ImportError` to the first use of the name. No static rule can prove
    that harmless, which is why `apply` prints this reminder itself.

!!! note "Below Python 3.15 the fallback is limited"

    `--target-version 3.11`–`3.14` binds whole-module imports through
    `importlib.util.LazyLoader`, injecting one small helper per converted file.
    `from x import y` has no equivalent in that form — the PEP 562 module
    `__getattr__` trick does not fire for the module's own global lookups — so
    those statements are reported as `FALLBACK_UNSUPPORTED` and left eager.
    Type checkers also lose the module's type across a fallback binding.

```python
from importbudget import ApplyOptions, apply, render_apply_diff

result = apply("plan.json", ApplyOptions(target_version="3.15", write=True))
print(render_apply_diff(result))
```

## Proving the change helped

`importbudget verify` answers the question `plan` cannot: *did it actually get
faster?* It reads the same plan document, copies the source root twice — once
unconverted, once converted — and re-measures the entrypoint on both:

```bash
importbudget verify plan.json                        # measure both sides
importbudget verify plan.json --runs 15              # more pairs, tighter sd
importbudget verify plan.json --target-version 3.13  # pre-3.15 fallback
```

| Flag | Meaning |
|---|---|
| `--runs N` | interleaved before/after pairs to measure (default 5) |
| `--warmup N` | pairs discarded first, to pay the cold page cache on both trees (default 1) |
| `--target-version X.Y` | interpreter the converted tree is emitted for; it must be one the measuring interpreter can run (`3.11`–`3.15`, default `3.15`) |
| `--divergence-threshold X` | warn when predicted and measured savings differ by more than this fraction (default 0.3) |
| `--json` | emit the machine-readable verify document instead of the report |

Nothing you own is written to. Both trees are scratch copies, so `verify` works
whether or not you have already run `apply --write`.

!!! note "Why the runs alternate"

    Runs are paired and strictly interleaved — before, after, before, after —
    and the reported statistic is the mean of the *per-pair differences*. A
    machine that slows down halfway through the session then moves both sides
    of a pair rather than one arm of the comparison. The order actually
    executed is printed as `schedule:` and carried in the JSON, so you can
    check it.

!!! warning "An improvement is claimed only above 3 sigma"

    Warm-run coefficients of variation between 7% and 26% are normal. If
    `3 sigma` is not *strictly* below the absolute delta, importbudget reports
    "no significant change" rather than a small win — a delta sitting exactly
    on the noise floor is no result.

    A delta too small to see in the raw totals is retried against a subtree the
    conversion left structurally identical, measured in the same run. Dividing
    one by the other cancels most of the machine-load noise the two share. Both
    comparisons are always reported; the verdict says which one it rested on.

```python
from importbudget import RunOptions, VerifyOptions, render_verify_table, verify

result = verify("plan.json", VerifyOptions(run=RunOptions(runs=15)))
print(render_verify_table(result))
```

## Enforcing a budget in CI

`importbudget check` measures an entrypoint's import cost — excluding
interpreter startup — and compares it with a ceiling:

```bash
importbudget check mypackage --max 150ms   # same budget, either spelling
importbudget check mypackage --max 0.15s
importbudget check -m mypackage.cli --max 150ms --json
```

| Flag | Meaning |
|---|---|
| `--max DURATION` | required; a number and a unit (`us`, `ms`, `s`). A bare `150` is refused |
| `--runs N` / `--warmup N` | as for `profile` (defaults 3 and 1) |
| `--json` | emit the machine-readable check document instead of the summary |

| Exit code | Meaning |
|---|---|
| `0` | measured cost is at or below the budget — equality passes |
| `1` | measured cost exceeds the budget |
| `2` | the entrypoint could not be measured, so the budget was never tested |

!!! note "Why three exit codes"

    A crashing entrypoint imports less than a working one. Reporting that as
    "within budget" would hide the breakage, and reporting it as "over budget"
    would invent a regression, so it gets its own code. A `--max` value that
    cannot be parsed also exits non-zero naming the offending text, and never
    as `1`.

Add it to GitHub Actions with the recipe in the
[README](https://github.com/tomada1114/importbudget#continuous-integration).

```python
from importbudget import Budget, check, render_check_table

result = check("mypackage", Budget.parse("150ms"))
print(render_check_table(result))
raise SystemExit(result.exit_code)
```

## What's Next?

See the [API Reference](reference.md) for the complete API documentation.
