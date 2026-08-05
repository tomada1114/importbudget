# importbudget

Profile, safely convert, and CI-budget Python import time with PEP 810 lazy imports

## Installation

=== "pip"

    ```bash
    pip install importbudget
    ```

=== "uv"

    ```bash
    uv add importbudget
    ```

## Quick Example

Attribute your startup import time to the statements that caused it:

```bash
importbudget profile mypackage --top 10
```

```python
from importbudget import profile, render_json, render_table

result = profile("mypackage")
print(render_table(result, top=10))
print(render_json(result))  # stable, versioned JSON contract
```

Then find out which of those imports can safely become PEP 810 `lazy` imports:

```bash
importbudget plan mypackage --min-ms 5
```

```python
from importbudget import plan, render_plan_table

print(render_plan_table(plan("mypackage")))
```

A statement is proposed only when every safety rule proves it convertible;
everything else is listed with the reason code that rejected it.

Then convert exactly what the plan proved safe:

```bash
importbudget plan mypackage --json > plan.json
importbudget apply plan.json           # dry run: prints a diff
importbudget apply plan.json --write   # rewrite the files
```

```python
from importbudget import ApplyOptions, apply, render_apply_diff

print(render_apply_diff(apply("plan.json", ApplyOptions(write=True))))
```

`apply` writes nothing without `--write`, rewrites only module-top-level
statements into the four grammar forms that are safe to emit, and is a no-op
when re-run.

Then prove it helped, and keep it that way:

```bash
importbudget verify plan.json              # measure both sides, report delta ± sd
importbudget check mypackage --max 150ms   # CI gate: exit 1 when over budget
```

```python
from importbudget import Budget, check, render_verify_table, verify

print(render_verify_table(verify("plan.json")))
raise SystemExit(check("mypackage", Budget.parse("150ms")).exit_code)
```

`verify` measures both source trees itself, in strictly interleaved pairs, and
refuses to claim an improvement below 3 sigma — a prediction is not a result.
`check` exits 0 at or below the budget, 1 over it, and 2 when the entrypoint
could not be measured at all.

## Next Steps

- [Getting Started](getting-started.md) — setup and first steps
- [API Reference](reference.md) — full API documentation
- [Contributing](contributing.md) — how to contribute
