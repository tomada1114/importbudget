# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `importbudget verify <plan.json>` — measures the plan's conversion instead of
  trusting its prediction. Copies the source root twice, once unconverted and
  once converted, and re-measures the entrypoint on both in strictly
  interleaved before/after pairs; the reported statistic is the mean of the
  per-pair differences, so machine drift moves both sides of a pair rather than
  one arm of the comparison. Your own files are never written to. Supports
  `--runs` / `--warmup` / `--target-version` / `--divergence-threshold` /
  `--json`
- A 3 sigma significance rule on `verify`: an improvement is claimed only when
  `3 sigma` is *strictly* below the absolute delta, so a delta sitting exactly
  on the noise floor is reported as no result. A delta too small to see in the
  raw totals is retried against a subtree the conversion left structurally
  identical, measured in the same run, which cancels most of the machine-load
  noise the two share. Both comparisons are always reported
- A divergence warning naming the plan's predicted saving and the measured one
  whenever they differ by more than `--divergence-threshold` (default 30%),
  plus a sanity warning when the measured saving exceeds the cost statement
  conversion could remove at all
- `importbudget check <entrypoint> --max <budget>` — the CI gate. Measures
  import cost excluding interpreter startup and exits 0 at or below the budget
  (equality passes), 1 over it, and 2 when the entrypoint could not be measured
  at all, so a crashing entrypoint is never reported as a pass or as a
  regression. `--max` accepts `us` / `ms` / `s`; a bare number is refused with
  an error naming it
- Versioned JSON verify and check documents (`schema_version` 1, `document`
  `"verify"` / `"check"`), the verify document carrying the executed run
  schedule, every measured sample, and both the raw and normalized comparisons
- A copy-pasteable GitHub Actions recipe in the README, run by this
  repository's own CI against importbudget itself
- Public API: `verify()`, `check()`, `render_verify_table()` /
  `render_verify_json()` / `to_verify_json_dict()`, `render_check_table()` /
  `render_check_json()` / `to_check_json_dict()`, and the `Budget`,
  `Comparison`, `VerifyOptions`, `VerifyResult`, `CheckOptions`, `CheckResult`
  dataclasses
- `importbudget apply <plan.json>` — rewrites the statements a plan proved safe
  into PEP 810 `lazy` imports with LibCST. Defaults to a dry run printing a
  unified diff; `--write` applies it. Converts only module-top-level statements
  (placement P1), emits only grammar forms G1/G2/G6/G7, never rewrites a
  physical line holding more than one statement, and preserves every other line
  byte-for-byte. Re-running is a no-op: already-converted statements are
  detected by CST node type, not by scope analysis
- `--target-version 3.11`..`3.14` on `apply` — a fallback emitter binding
  whole-module imports through `importlib.util.LazyLoader` for interpreters
  without the `lazy` keyword. `from x import y` is excluded from it with the
  reason code `FALLBACK_UNSUPPORTED`
- Machine-readable reason codes for every statement `apply` declined to rewrite
  (`UNSUPPORTED_FORM`, `FALLBACK_UNSUPPORTED`, `COMPOUND_LINE`, `ALREADY_LAZY`,
  `SOURCE_MISMATCH`, `NOT_FOUND`), plus the `MODULE_LEVEL_CALL` advisory for a
  converted statement whose name an import-time call reaches anyway
- Versioned JSON apply document (`schema_version` 1, `document` `"apply"`) with
  the per-statement outcome, the diff and a `totals` block
- Public API: `apply()`, `render_apply_diff()`, `render_apply_table()`,
  `render_apply_json()` / `to_apply_json_dict()`, and the apply dataclasses
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
- `OPAQUE_EXPORTS` reason code, reported when a module's `__all__` cannot be
  read statically. Previously these statements were rejected as
  `MODULE_LEVEL_USE`, which sent scripts looking for an import-time read that
  does not exist
- `ModuleContext`, `Placement` and `build_context` are exported from the package
  root. Implementing the public `Rule` protocol needs `ModuleContext` for the
  `check` signature, and they were already in `importbudget.rules.__all__`

### Changed

- `plan --from-profile` no longer validates `--runs` and `--warmup`. Its help
  text documents them as unused on that path, but an out-of-range value still
  failed the command. `--min-ms` is read there and keeps its validation
- A profile document without the `document` discriminator is refused rather
  than assumed to be a profile. No released schema version ever omitted the
  key, so its absence means a foreign file

- Runtime dependency on `libcst>=1.9.0`, the first release shipping
  `cst.LazyImport` / `cst.LazyImportFrom`. `profile` and `plan` still need only
  the standard library; `apply` needs a parser that round-trips source
  byte-for-byte and can build lazy-import nodes rather than splice text
- `[tool.uv] exclude-newer` moved to `2026-07-30`, the earliest cutoff that
  admits `libcst` 1.9.0 (published 2026-07-29)

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
- A `match` capture pattern rebinding an imported name (`case json:`) is no
  longer judged safe. `MatchAs.name` is a plain string a generic AST walk never
  reaches; `MatchStar` and `MatchMapping` captures had the same hole
- A saved profile document carrying negative counts or microsecond totals is
  refused instead of loading and rendering nonsense in the plan header

[Unreleased]: https://github.com/tomada1114/importbudget/commits/main
