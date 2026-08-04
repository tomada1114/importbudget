# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

[Unreleased]: https://github.com/tomada1114/importbudget/commits/main
