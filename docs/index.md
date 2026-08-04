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

## Next Steps

- [Getting Started](getting-started.md) — setup and first steps
- [API Reference](reference.md) — full API documentation
- [Contributing](contributing.md) — how to contribute
