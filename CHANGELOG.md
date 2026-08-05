# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `importbudget plan` — joins a profile with the PEP 810 safety rules and
  proposes the import statements that can safely become `lazy` imports.
  Accepts the same entrypoint forms as `profile`, or `--from-profile
  path.json` to plan from a saved profile without re-measuring. Supports
  `--min-ms` / `--runs` / `--warmup` / `--top` / `--json`
- `importbudget.rules` — the whitelist rule set, one rule per file, each with a
  machine-readable reason code (`STAR_IMPORT`, `FUTURE_IMPORT`,
  `NON_TOPLEVEL`, `TRY_EXCEPT_IMPORT`, `MODULE_LEVEL_USE`, `REEXPORT_IN_INIT`,
  `UNUSED_IMPORT`) citing the constraint IDs it rests on from
  `docs/pep810-rules.md`. A statement is proposed only when *every* rule proves
  it safe; a rule that cannot decide rejects
- Public API: `plan()`, `plan_from_profile()`, `analyze()`, `render_plan_table()`,
  `render_plan_json()` / `to_plan_json_dict()`, and the plan/verdict dataclasses
- Versioned JSON plan document (`schema_version` 1, `document` `"plan"`) with
  per-statement `verdict` / `status` / `reasons` and a `totals` block
- `document` discriminator on the profile JSON, so the two documents that share
  a schema version can be told apart. Purely additive: a document carrying
  `schema_version` 1 and no `document` key is a profile
- `importbudget profile <entrypoint>` — runs an entrypoint under
  `python -X importtime` and attributes each module's self time to the first
  import statement that imported it. Supports module, `-m` and script
  entrypoints, `--runs` / `--warmup` / `--top` / `--json`
- Public API: `profile()`, `render_table()`, `render_json()` /
  `to_json_dict()` and the result dataclasses
- Versioned JSON document (`schema_version` 1) that the later `plan` stage
  consumes. `warnings` holds importbudget's own diagnostics only; whatever the
  profiled program wrote to its own stderr lands in the separate `stderr`
  object, deduplicated and capped
- Initial project structure, bootstrapped from
  [uv-template](https://github.com/tomada1114/uv-template)

### Removed

- The `core.add` placeholder from the template

### Fixed

- Entrypoint classification no longer prefers a same-named extensionless file
  in the working directory over a valid dotted module name. A `.py` suffix
  always means a script, a valid dotted name always means a module, and a
  path-shaped target such as `./tool` is how an extensionless script is asked
  for
- Namespace packages split across several `sys.path` entries now have every
  portion scanned, so imports made from the portions after the first are
  attributed to their own source lines instead of collapsing into the caller
- `import importbudget.__main__` no longer exits the interpreter as a side
  effect of the import; `python -m importbudget` is unchanged

[Unreleased]: https://github.com/tomada1114/importbudget/commits/main
