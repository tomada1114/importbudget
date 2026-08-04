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

## Next Steps

- [Getting Started](getting-started.md) — setup and first steps
- [API Reference](reference.md) — full API documentation
- [Contributing](contributing.md) — how to contribute
