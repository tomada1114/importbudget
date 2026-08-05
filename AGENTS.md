# Project Guide

## Overview

This is a Python library built with [uv](https://docs.astral.sh/uv/) and
[hatchling](https://hatch.pypa.io/). It uses a strict `src/` layout with
comprehensive type checking and linting.

## Quick Reference

```bash
just install   # Install dependencies and git hooks when .git/ is present
just fmt       # Format code (ruff check --fix + ruff format)
just lint      # Lint (ruff check) + type check (mypy)
just test      # Run tests with coverage
just smoke     # Build and verify the wheel in a temp virtual environment
just check     # Run all checks: fmt → lint → test
just docs      # Serve docs locally
just build     # Build distribution packages
```

Without Just: replace `just <cmd>` with the corresponding `uv run` commands
in the `justfile`. Run a single test with
`uv run pytest tests/test_<module>.py::test_<name>`.

## Architecture

```
src/importbudget/
├── __init__.py     # Public API — export everything users need here
├── __main__.py     # `python -m importbudget`
├── py.typed        # PEP 561 marker for typed package
├── errors.py       # Exception hierarchy (ImportBudgetError and friends)
├── entrypoints.py  # Value objects: Entrypoint, RunOptions, Measurement
├── importtime.py   # `-X importtime` stderr parser + import tree
├── stderr.py       # Capped capture of the entrypoint's own stderr
├── averaging.py    # Mean of several runs, totals kept exact
├── measure.py      # Child processes, baseline, run collection
├── sources.py      # AST scan: one file -> import statements (+ the tree)
├── _names.py       # Statement -> candidate module names
├── index.py        # Per-file scans -> the modules we own
├── _resolve.py     # Measured node -> the statement that imported it
├── attribute.py    # Self time -> the statement that caused it
├── profiler.py     # profile(): measure + attribute in one session
├── rules/          # Safety rules, one per file, each with a reason code
│   ├── _rule.py      # Rule protocol, RuleCode, Violation
│   ├── _context.py   # Per-module AST facts every rule shares
│   └── <rule>.py     # star_import, future_import, non_toplevel, ...
├── analyze.py      # Whitelist engine: every rule over every statement
├── plans.py        # Value objects: PlanOptions, PlanEntry, PlanResult, ...
├── _plan_input.py  # Saved profile JSON -> planner inputs
├── planner.py      # plan(): verdicts joined with attributed cost
├── applies.py      # Value objects: ApplyOptions, ApplyEntry, ApplyResult, ...
├── _apply_input.py # Saved plan JSON -> codemod targets
├── _emit.py        # One eager import -> a lazy one (native + fallback)
├── _lazy_gap.py    # Names a module reaches indirectly while importing
├── codemod.py      # apply(): rewrite the statements the plan proved safe
├── report.py       # Human table + versioned JSON document (profile)
├── plan_report.py  # Human table + versioned JSON document (plan)
├── apply_report.py # Diff + human table + versioned JSON document (apply)
└── cli.py          # argparse command line (profile, plan, apply)
```

Every rule cites the constraint IDs it rests on from `docs/pep810-rules.md`,
the verified PEP 810 constraint table. Rules never invent semantics beyond
that document, and a rule that cannot decide rejects rather than abstains.

- Keep the public API surface small — export via `__init__.py.__all__`
- Internal modules can use a leading underscore (`_internal.py`)
- Separate concerns: one module per logical unit
- Update `docs/reference.md` and README examples whenever you change the public API

## Review Checklist

Before submitting a PR:

1. `just check` passes (format, lint, type check, tests)
2. New public APIs have type annotations and docstrings
3. Tests cover the new functionality
4. No unnecessary dependencies added

## Important Reminders

- All code, docs, commits, and PRs must be written in English
- Do what has been asked; nothing more, nothing less
- NEVER create files unless absolutely necessary
- ALWAYS prefer editing an existing file to creating a new one
- NEVER proactively create documentation files unless explicitly requested
- Dependencies should always be added to the appropriate group in pyproject.toml
