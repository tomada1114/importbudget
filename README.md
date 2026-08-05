# importbudget

[![CI](https://github.com/tomada1114/importbudget/actions/workflows/ci.yml/badge.svg)](https://github.com/tomada1114/importbudget/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/tomada1114/importbudget/branch/main/graph/badge.svg)](https://codecov.io/gh/tomada1114/importbudget)
[![PyPI](https://img.shields.io/pypi/v/importbudget)](https://pypi.org/project/importbudget/)
[![Python](https://img.shields.io/pypi/pyversions/importbudget)](https://pypi.org/project/importbudget/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Profile, safely convert, and CI-budget Python import time with PEP 810 lazy imports

## Quickstart

```bash
pip install importbudget
# or
uv add importbudget
```

```bash
importbudget profile mypackage          # import the package
importbudget profile -m mypackage.cli   # run it with -m
importbudget profile scripts/run.py     # run a script
```

```python
from importbudget import profile, render_table

result = profile("mypackage")
print(render_table(result, top=10))
```

Then ask which of those imports can safely become PEP 810 `lazy` imports:

```bash
importbudget plan mypackage                        # measure, then plan
importbudget plan --from-profile profile.json      # plan without re-measuring
importbudget plan mypackage --min-ms 5             # ignore sub-5 ms wins
```

```python
from importbudget import plan, render_plan_table

print(render_plan_table(plan("mypackage")))
```

`plan` is a whitelist: a statement is proposed only when every safety rule
proves it convertible. Everything else is listed with the reason code that
rejected it, so "excluded" means *not proven safe*, not *unsafe*.

Then convert exactly what the plan proved safe:

```bash
importbudget plan mypackage --json > plan.json
importbudget apply plan.json                        # dry run: prints a diff
importbudget apply plan.json --write                # rewrite the files
importbudget apply plan.json --target-version 3.13  # pre-3.15 fallback
```

```python
from importbudget import ApplyOptions, apply, render_apply_diff

print(render_apply_diff(apply("plan.json")))
```

`apply` defaults to a dry run and writes nothing without `--write`. It rewrites
only module-top-level statements, emits only the `lazy import x [as y]` and
`lazy from x import y [as z]` forms, and re-running it is a no-op. Statements it
declines to rewrite keep a machine-readable reason code
(`UNSUPPORTED_FORM`, `FALLBACK_UNSUPPORTED`, `COMPOUND_LINE`, ...) instead of
disappearing.

Below Python 3.15 the `lazy` keyword does not exist, so `--target-version
3.11`..`3.14` binds whole-module imports through `importlib.util.LazyLoader`
instead. `from x import y` has no equivalent in that form and is reported as
`FALLBACK_UNSUPPORTED` rather than half-converted.

> Lazy imports move a module's import-time side effects, and any `ImportError`,
> to the first use of the name. Run your test suite after `--write`.

## Design Philosophy

Every choice in this template has a reason. If you disagree with a decision,
you know exactly what to change and why it was there in the first place.

### Why `src/` layout?

The `src/` layout prevents accidental imports of the local package during
development and testing. It ensures that tests always run against the
*installed* version, catching packaging errors before they reach users.

### Why strict mypy + comprehensive Ruff rules?

Type errors and lint issues are cheapest to fix at write time. Strict settings
from day one mean every line of code is held to the same standard — there is
never a "legacy" codebase to clean up. LLMs generating code also benefit from
strict rules: they produce higher-quality output when constraints are clear.

### Why exactly one runtime dependency?

`profile` and `plan` need nothing but the standard library. `apply` needs
[LibCST](https://github.com/Instagram/LibCST) (>= 1.9.0), which is the only
parser that both round-trips source byte-for-byte — comments, blank lines and
quote styles survive a rewrite — and ships real `LazyImport` / `LazyImportFrom`
nodes. Building those nodes rather than splicing the word `lazy` into text is
what makes it structurally impossible to emit `from . lazy import x`, a
whitespace slip that CPython accepts as an import of a module named `lazy` and
merely warns about. That guarantee is worth one dependency; nothing else is.

### Why Just over Make?

Just has cleaner syntax (no mandatory tabs), better cross-platform support, and
more readable recipe definitions. It is a task runner, not a build system —
which is exactly what a Python project needs.

### Why AGENTS.md and .claude/?

AI-assisted development is the norm, not the exception. `AGENTS.md` gives any
coding agent (Claude Code, Codex, Cursor, Gemini CLI, ...) the context it
needs to match your project's standards; `CLAUDE.md` imports it and adds
Claude Code specifics. The committed `.claude/` directory goes further than
prose: path-scoped rules load conventions only when relevant files are
touched, hooks deterministically auto-format edited files, block edits to
`uv.lock`/`.env*` and `--no-verify`/force-push commands, and run ruff + mypy
before the agent ends a turn, while a reviewed permission allowlist covers
local build/lint/test commands only — commit, push, and PR creation always
stay behind human approval.

### Why 80% coverage minimum?

80% is high enough to catch most regressions but low enough to avoid
test-for-the-sake-of-testing. Branch coverage is enabled, so conditional logic
is meaningfully tested.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for full setup instructions.

```bash
uv sync --all-groups
# Optional but recommended when working in a Git checkout
uv run pre-commit install --install-hooks
just check
```

`just install` installs pre-commit hooks automatically when the project lives in
a Git repository and skips that step for "Use this template" bootstrap copies
before Git is initialized.

For packaging verification, run `just smoke` (or `uv build && uv run python scripts/smoke_test.py`)
to install the freshly built wheel into a temporary virtual environment and
confirm the distribution imports from the wheel, not from `src/`.

## Documentation

- [Getting Started](https://tomada1114.github.io/importbudget/getting-started/)
- [API Reference](https://tomada1114.github.io/importbudget/reference/)

## License

[MIT](LICENSE)
