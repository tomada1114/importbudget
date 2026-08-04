# PEP 810 Constraint Reference

Single source of truth for `importbudget`'s codemod. Every row is traceable to a
primary source (PEP text, `docs.python.org/3.15`, or CPython source). Rows the
codemod relies on must never be inferred — anything not backed by a source is
marked **UNVERIFIED**.

---

## 1. Status Summary

| Field | Value | Source |
| --- | --- | --- |
| PEP | 810 — "Explicit lazy imports" | [PEP 810](https://peps.python.org/pep-0810/) |
| Status | **Final** (not merely "Accepted") | PEP header, [pep-0810.rst](https://raw.githubusercontent.com/python/peps/main/peps/pep-0810.rst) |
| Type | Standards Track | PEP header |
| Created | 02-Oct-2025 | PEP header |
| Resolution | [03-Nov-2025](https://discuss.python.org/t/pep-810-explicit-lazy-imports/104131/466) | PEP header |
| Python-Version | 3.15 | PEP header |
| Canonical docs | `:external+py3.15:ref:lazy-imports` — the PEP now defers to the CPython docs | PEP header `.. canonical-doc::` directive |
| Implementation | Merged into CPython `main`; present in `v3.15.0rc1` | [CPython tags](https://github.com/python/cpython/tags) |

Because the PEP is **Final** and carries a `canonical-doc` directive, the CPython
documentation and implementation — **not the PEP prose** — are authoritative
where they disagree. See §6 for the one known divergence.

---

## 2. Grammar Table

Reference grammar ([PEP 810 §Grammar](https://peps.python.org/pep-0810/#grammar),
[`Grammar/python.gram`](https://github.com/python/cpython/blob/main/Grammar/python.gram) L228–236):

```text
import_name:
    | 'lazy'? 'import' dotted_as_names

import_from:
    | 'lazy'? 'from' ('.' | '...')* dotted_name 'import' import_from_targets
    | 'lazy'? 'from' ('.' | '...')+ 'import' import_from_targets
```

| # | Form | Allowed | Notes / Source |
| --- | --- | --- | --- |
| G1 | `lazy import x` | ✅ | PEP §Grammar; PEP §Semantics example |
| G2 | `lazy import x as y` | ✅ | `dotted_as_names` covers `as`; LibCST test `"lazy import os as operating_system"` (PR [#1456](https://github.com/Instagram/LibCST/pull/1456)) |
| G3 | `lazy import a, b` (multiple targets) | ✅ | `dotted_as_names` is comma-separated; LibCST test `"lazy import os, sys"` |
| G4 | `lazy import a.b.c` (dotted, no alias) | ✅ | `dotted_as_names`. **Binds only `a`** — same codegen path as an eager dotted import ([`Python/codegen.c`](https://github.com/python/cpython/blob/main/Python/codegen.c) `codegen_import`, dot-splitting branch). See S7. |
| G5 | `lazy import a.b.c as d` | ✅ | `codegen_import_as` emits an `IMPORT_FROM` chain against the lazy proxy |
| G6 | `lazy from x import y` | ✅ | PEP §Grammar |
| G7 | `lazy from x import y as z` | ✅ | `import_from_targets` covers `as` |
| G8 | `lazy from x import (y, z)` (parenthesized) | ✅ | `import_from_targets` covers the parenthesized form; language reference §7.11 grammar |
| G9 | `lazy from . import x` / `lazy from .. import x` | ✅ | Second `import_from` alternative; LibCST tests `"lazy from . import sibling"`, `"lazy from .. import parent"` |
| G10 | `lazy from .pkg.mod import x` | ✅ | First `import_from` alternative with leading dots |
| G11 | `lazy from x import *` | ❌ **SyntaxError** | `"lazy from ... import * is not allowed"` ([`Python/symtable.c`](https://github.com/python/cpython/blob/main/Python/symtable.c) L2193); secondary check `"cannot lazy import *"` in `codegen.c` L3005 |
| G12 | `lazy from __future__ import ...` | ❌ **SyntaxError** | `"lazy from __future__ import is not allowed"` ([`Parser/action_helpers.c`](https://github.com/python/cpython/blob/main/Parser/action_helpers.c) L2071) |
| G13 | `from x lazy import y` (misplaced keyword) | ❌ **SyntaxError** | `"use 'lazy from ... ' instead of 'from ... lazy import'"` (`python.gram` L1449, `invalid_import_from`) |
| G14 | `from . lazy import x` | ⚠️ Parses as import of relative module `lazy`, **emits a warning** | `_warn_relative_import_of_lazy` (`action_helpers.c` L2012–2043): `"'from . lazy import' is the same as 'from .lazy import'; did you mean 'lazy from . import'?"`. Codemod must never emit this spelling. |
| G15 | `lazy` as an ordinary identifier (`lazy = 1`, `def lazy():`) | ✅ still valid | Soft keyword — "only has special meaning when it appears before import statements" (PEP §Grammar) |

**Codemod rule:** only G1, G2, G6, G7 are safe to *emit*. G3–G5 and G8–G10 are
legal but carry extra semantics (see S7, S8); treat as opt-in. G11–G14 are hard
rejects — the source import must be left untouched.

---

## 3. Placement Table

Two independent enforcement layers exist. Both must pass:

- **Symbol table** — [`Python/symtable.c`](https://github.com/python/cpython/blob/main/Python/symtable.c) `check_lazy_import_context` (L1847–1876)
- **Code generator** — [`Python/codegen.c`](https://github.com/python/cpython/blob/main/Python/codegen.c) `codegen_validate_lazy_import` (L2910–2919)

| # | Placement | Allowed | Error message / Source |
| --- | --- | --- | --- |
| P1 | Module top level (statement directly in module body) | ✅ | PEP §Syntax restrictions |
| P2 | Inside a function / `async def` / lambda-adjacent scope | ❌ | `"lazy import not allowed inside functions"` / `"lazy from ... import not allowed inside functions"` (`symtable.c` L1862) |
| P3 | Inside a class body | ❌ | `"lazy ... not allowed inside classes"` (`symtable.c` L1870) |
| P4 | Inside `try:` / `except:` / `else:` / `finally:` (incl. `try*`) | ❌ | `"lazy ... not allowed inside try/except blocks"` (`symtable.c` L1853). `ste_in_try_block` is set by `Try_kind` **and** `TryStar_kind` only (`symtable.c` L2145–2162), and covers the whole statement including `orelse`/`finalbody`. |
| P5 | Any non-module compile scope (comprehension, generator expr, `exec` of a nested scope) | ❌ | `"lazy imports only allowed in module scope"` (`codegen.c` L2914) |
| P6 | Module-level `if` / `elif` / `else` block | ✅ **(implementation-verified, undocumented)** | Not checked anywhere: `If_kind` only calls `ENTER_CONDITIONAL_BLOCK`, never `ENTER_TRY_BLOCK` (`symtable.c` L2103–2131), and scope stays `COMPILE_SCOPE_MODULE`. Neither the PEP nor the docs mention `if`. |
| P7 | Module-level `with` block | ✅ **(implementation-verified, undocumented)** | Same reasoning as P6 |
| P8 | Module-level `for` / `while` loop | ✅ **(implementation-verified, undocumented)** | Same reasoning as P6 |
| P9 | Module-level `match` statement | ✅ **(implementation-verified, undocumented)** | Same reasoning as P6 |
| P10 | Top level of a string passed to `exec()` | ❌ inside a function/class; module-scope case **UNVERIFIED** | CPython tests `test_exec_import_func_with_lazy_modules` / `test_exec_import_class_with_lazy_modules` confirm the function- and class-scoped `exec` cases raise `SyntaxError`. No source found for the module-scope `exec` case — do not rely on it. |

> **Codemod rule (conservative):** convert **only P1**. P6–P9 are legal but rest
> on reading CPython's C source, not on any documented guarantee — the docs'
> phrasing "only permitted at module scope" could be tightened later. Converting
> inside `if TYPE_CHECKING:` in particular changes runtime behavior and must stay
> out of scope. Never convert P2–P5; those are the exact shapes a naive
> "hoist the import" codemod would break on.

**Explicit documented restriction wording**, for citation
([Doc/whatsnew/3.15.rst](https://docs.python.org/3.15/whatsnew/3.15.html),
[Doc reference §7.11](https://docs.python.org/3.15/reference/simple_stmts.html)):

> "Lazy imports are only permitted at module scope; using `lazy` inside a
> function, class body, or `try`/`except`/`finally` block raises a
> `SyntaxError`. Neither star imports nor future imports can be lazy."

---

## 4. Semantics Relevant to Safe Conversion

| # | Rule | Source |
| --- | --- | --- |
| S1 | A `lazy` import binds a **`types.LazyImportType` proxy**, not the module. The real import happens on first *use* of the name. | PEP §Semantics; §Lazy objects |
| S2 | Proxy type: `types.LazyImportType`, whose `__name__` is `'lazy_import'`. It is **not directly constructible** (`TypeError`). It exposes a `.resolve()` method to force reification. | PEP §Lazy objects; CPython `Lib/test/test_lazy_import/__init__.py` class `LazyImportTypeTests` |
| S3 | `lazy from mod import a, b` binds **each name** to its own proxy. First access to *any* one loads the **entire module** and reifies **only that name**; the others stay lazy. | PEP §Semantics |
| S4 | Laziness defers **when** a module loads, never **what** loads. `lazy from heavy import MyClass` still executes all of `heavy.py` at first use. | PEP FAQ "Does `lazy from module import Class` load the entire module or just the class?" |
| S5 | A lazily imported module is **absent from `sys.modules`** until reified, and listed in the new **`sys.lazy_modules`** set (removed from that set on reification). | PEP §Lazy import mechanism; §Reification; test `test_lazy_modules_attribute_is_dict` asserts `isinstance(sys.lazy_modules, set)` |
| S6 | Reification calls `__import__` using the import system state (`sys.path`, `sys.meta_path`, `sys.path_hooks`) **at reification time**, not at statement-execution time. | PEP §Reification |
| S7 | `lazy import a.b.c` binds the name **`a`**. Reaching `a.b.c` walks attributes on the reified package. A missing submodule surfaces as **`AttributeError`** at the attribute access — not `ImportError`. | `codegen.c` `codegen_import` dot-splitting; test `test_missing_lazy_submodule_raises_attribute_error` |
| S8 | Reifying a package does **not** reify its lazily imported submodules, but they *are* added to the reified package's globals (unless the package bound something else to that name). | PEP §Reification; test `test_lazy_submodule_stored_in_parent_dict` |
| S9 | `lazy from mod import name` does **not** publish `name` on `mod.__dict__`. | test `test_lazy_from_import_does_not_pollute_parent` |
| S10 | **Reads that reify:** ordinary name access, module attribute access, `getattr()`. **Reads that do NOT reify:** `globals()`, `mod.__dict__`, `dir()` (module `__dir__` is special-cased), `frame.f_globals`. Those hand back live proxies. | PEP §Reification; FAQ "How do lazy imports interact with `dir()`, `getattr()`…" |
| S11 | **Error timing shift:** `ImportError` / `AttributeError` that an eager import would raise at the import statement now raises at first use. The exception is chained to show both the `lazy import` line and the access site. | PEP §Observable behavioral shifts; §Reification |
| S12 | **Failed reification is not cached.** The proxy is not replaced; every subsequent use retries the import. | PEP §Reification |
| S13 | **Side-effect timing shift:** import-time registration / monkey-patching / global-state mutation in the target module is deferred to first use. This is the primary correctness hazard for the codemod. | PEP §Observable behavioral shifts; FAQ "How do I know if a library is compatible…" |
| S14 | **Import-order shift:** the order modules load in may differ from source order. | PEP §Observable behavioral shifts |
| S15 | Mixing lazy + eager imports of the same module is fine; the eager import wins and both names resolve to the same module object. | PEP FAQ "Can I mix lazy and eager imports of the same module?" |
| S16 | Thread-safe: the import lock guarantees exactly one thread performs the import, and the global is **atomically rebound**. No free-threading special cases. Subinterpreters each keep their own `sys.lazy_modules`. | PEP §Thread-safety and reification |
| S17 | Circular imports are **not** fixed by laziness. It helps only when the circular reference is not touched during module initialization. | PEP FAQ "Do lazy imports work with circular imports?" |
| S18 | Annotations (PEP 649/749): `lazy from m import T` used only in an annotation stays unloaded until `__annotations__` / `annotationlib.get_annotations()` / `getattr` touches it. Lazy imports are a documented replacement for `if TYPE_CHECKING:` guards. | PEP FAQ "…PEP 649 and PEP 749"; FAQ "What about type annotations and `TYPE_CHECKING` imports?" |
| S19 | `.pth` files processed by `site` are **never** lazy. | PEP §Note on .pth files |
| S20 | Import hooks and custom loaders run normally, under the standard protocol, at reification time. | PEP §Backwards Compatibility; FAQ "…import hooks and custom loaders" |

### UNVERIFIED — not addressed by any primary source

These must **not** drive codemod logic. Determine empirically on 3.15.0rc1 if
needed, and record results here with a test reference.

| Topic | Status |
| --- | --- |
| `__all__` interaction (does a lazy name in `__all__` reify on `from mod import *`?) | **UNVERIFIED** — silent in PEP, docs, and `Doc/reference` |
| `importlib.reload()` on a module holding lazy bindings | **UNVERIFIED** — silent in PEP and docs |
| Pickling / `copy` / `deepcopy` of a `LazyImportType` proxy | **UNVERIFIED** — silent in PEP and docs |
| `del <lazy_name>` at module level | **UNVERIFIED** — silent in PEP and docs |
| Whether `isinstance()` / `type()` on a proxy reifies (`hasattr`/`getattr` clearly do, per S10) | **UNVERIFIED** — PEP states only the general attribute-access rule |
| Whether module-level `__getattr__` shadows an unreified lazy global | **UNVERIFIED** — CPython has tests named `test_from_import_with_module_getattr*`, but no normative prose |
| Whether `-X importtime` output omits lazily deferred modules until reification | **UNVERIFIED** — no source found; likely (S5) but must be measured, since it directly affects `importbudget`'s profiler |

---

## 5. Global Controls

| Mechanism | Exact name | Accepted values | Source |
| --- | --- | --- | --- |
| CLI flag | `-X lazy_imports=<mode>` | **`all`, `normal` only** | [`Doc/using/cmdline.rst`](https://docs.python.org/3.15/using/cmdline.html#cmdoption-X) L708–711; [`Python/initconfig.c`](https://github.com/python/cpython/blob/main/Python/initconfig.c) L482, L2448–2460 |
| Environment variable | `PYTHON_LAZY_IMPORTS=<mode>` | **`all`, `normal` only** | `Doc/using/cmdline.rst` L1419; `initconfig.c` L2433–2445 |
| Runtime setter | `sys.set_lazy_imports(mode, /)` | **`"all"`, `"normal"` only** — anything else raises `ValueError("mode must be 'normal' or 'all'")`; non-str raises `TypeError` | [`Python/sysmodule.c`](https://github.com/python/cpython/blob/main/Python/sysmodule.c) L2811–2853 |
| Runtime getter | `sys.get_lazy_imports()` → `"normal"` \| `"all"` | — | `sysmodule.c` L2856–2878 |
| Filter setter | `sys.set_lazy_imports_filter(func)`; `func=None` removes it | `func(importer: str, name: str, fromlist: tuple[str, ...] \| None) -> bool`; `True` keeps the import lazy | PEP §Lazy imports filter; `sysmodule.c` L2764–2789 |
| Filter getter | `sys.get_lazy_imports_filter()` → callable \| `None` | — | PEP §Lazy imports filter |
| Introspection set | `sys.lazy_modules` — a `set` of fully qualified names currently lazy | — | PEP §Lazy import mechanism |
| Proxy type | `types.LazyImportType` (`__name__ == 'lazy_import'`) | — | PEP §Lazy objects; `Doc/whatsnew/3.15.rst` |
| Import hook | `__lazy_import__` — called in place of `__import__` for lazy imports, same signature | — | PEP §Lazy import mechanism |
| Per-module opt-in | **`__lazy_modules__`** — a module-global container of fully qualified module-name strings | Membership tested via `__contains__` on each `import` statement; typically a `set` or `list`. Ignored (harmless) on Python < 3.15. | PEP §Semantics; FAQ "What about forwards compatibility…"; `Doc/reference/datamodel` `module.__lazy_modules__` |

**Mode precedence** (PEP §Global lazy imports control):
`sys.set_lazy_imports()` > `-X lazy_imports` > `PYTHON_LAZY_IMPORTS` > default `normal`.

**What `all` mode actually covers** — verified in `codegen.c` (L2941–2951,
L3002–3020): a plain module-level import compiles with lazy-flag `0`
("potentially lazy"); imports inside `try`/`except` or in a non-module scope, and
star imports, compile with flag `2` ("forced eager"). So `-X lazy_imports=all`
affects exactly the same statement positions that the `lazy` keyword is allowed
in. There is **no per-module opt-out** other than `__lazy_modules__` (opt-*in*)
and the filter callback.

**No per-module opt-out exists.** To force eagerness the only documented routes
are `sys.set_lazy_imports_filter(...)` returning `False`, or simply not marking
the import.

---

## 6. Divergences and Traps

| # | Issue | Detail |
| --- | --- | --- |
| D1 | **The `"none"` mode described in the PEP does not exist in shipped CPython.** | PEP §Global lazy imports control and §Specification describe a third mode `"none"` ("no potentially lazy import is ever lazy"), and PEP FAQ recommends `-X lazy_imports=none` for debugging. The implementation rejects it: `sys_set_lazy_imports_impl` accepts only `"normal"`/`"all"` (`sysmodule.c` L2838–2848); `config_init_lazy_imports` accepts only `all`/`normal` (`initconfig.c` L2429–2460, comment: *"lazy_imports can be -1 (default) or 1 (on). 0 is rejected later"*); `Doc/using/cmdline.rst` documents `{all,normal}`. **The PEP prose is stale — the docs and implementation win (PEP is Final with a `canonical-doc` redirect).** `importbudget` must not offer a `--force-eager` escape hatch built on `-X lazy_imports=none`; use the filter API or run without the flag. |
| D2 | Docs do not enumerate `if`/`with`/`for`/`while` placements | See P6–P9. Legal today per the C sources, but undocumented. Treat as unsupported for automated conversion. |
| D3 | `from . lazy import x` warning trap | G14. A whitespace-only difference between "relative module named `lazy`" and a transposed lazy import. Codegen must always emit `lazy` **before** `from`. |
| D4 | `lazy import a.b` binds `a`, not `a.b` | S7. A codemod that assumes the bound name equals the dotted path will mis-track usage. |
| D5 | Failed lazy imports retry forever | S12. A profiling run that survives a broken lazy import may still fail later; budget checks must not treat "no error at import time" as success. |

---

## 7. Python 3.15 Release Schedule

Source: [PEP 790 — Python 3.15 Release Schedule](https://peps.python.org/pep-0790/) (Status: **Active**).

| Milestone | Date | Happened by 2026-08-04? |
| --- | --- | --- |
| 3.15.0 alpha 1 | 2025-10-14 | ✅ |
| 3.15.0 alpha 2 | 2025-11-19 | ✅ |
| 3.15.0 alpha 3 | 2025-12-16 | ✅ |
| 3.15.0 alpha 4 | 2026-01-13 | ✅ |
| 3.15.0 alpha 5 | 2026-01-14 | ✅ |
| 3.15.0 alpha 6 | 2026-02-11 | ✅ |
| 3.15.0 alpha 7 | 2026-03-10 | ✅ |
| 3.15.0 alpha 8 | 2026-04-07 | ✅ |
| **3.15.0 beta 1 — feature freeze** | **2026-05-07** | ✅ |
| 3.15.0 beta 2 | 2026-06-02 | ✅ |
| 3.15.0 beta 3 | 2026-06-23 | ✅ |
| 3.15.0 beta 4 | 2026-07-18 | ✅ |
| **3.15.0 rc1** | **2026-08-04** | ✅ **today** — tag `v3.15.0rc1` exists |
| 3.15.0 rc2 | 2026-09-01 | ⏳ |
| **3.15.0 final** | **2026-10-01** | ⏳ |

Confirmed via [CPython tags](https://api.github.com/repos/python/cpython/tags):
`v3.15.0a1`…`v3.15.0a8`, `v3.15.0b1`…`v3.15.0b4`, **`v3.15.0rc1`**.
`docs.python.org/3.15/` currently builds from 3.15.0rc1 and documents lazy
imports. **PEP 810 is testable today** on a real release artifact — the codemod
does not need to be designed against speculation.

---

## 8. Tooling Support (context for the codemod)

| Tool | Status | Source |
| --- | --- | --- |
| **LibCST** | ✅ **Supported since v1.9.0 (2026-07-29)** — ships `cst.LazyImport` and `cst.LazyImportFrom` nodes, each with a `whitespace_after_lazy: SimpleWhitespace` field (validated non-empty) and full round-trip codegen. | [LibCST v1.9.0 release](https://github.com/Instagram/LibCST/releases/tag/v1.9.0); PRs [#1454](https://github.com/Instagram/LibCST/pull/1454) (3.15 build), [#1456](https://github.com/Instagram/LibCST/pull/1456) (3.15 syntax) |
| mypy | Tracking issue open | [python/mypy#20978](https://github.com/python/mypy/issues/20978) |
| ty (astral) | Tracking issue open | [astral-sh/ty#2968](https://github.com/astral-sh/ty/issues/2968) |
| isort | Tracking issue open | [PyCQA/isort#2462](https://github.com/pycqa/isort/issues/2462) |

`importbudget` should pin `libcst >= 1.9.0` and emit only `LazyImport` /
`LazyImportFrom` nodes (never raw string splicing), which structurally prevents
the G14 whitespace trap.

---

## Verification Footer

**Verified against primary sources on 2026-08-04.**

- PEP 810 (rendered): https://peps.python.org/pep-0810/
- PEP 810 (source, header + Specification + Semantics + Reification + FAQ read in full): https://raw.githubusercontent.com/python/peps/main/peps/pep-0810.rst
- PEP 790 — Python 3.15 Release Schedule: https://peps.python.org/pep-0790/
- What's New in Python 3.15: https://docs.python.org/3.15/whatsnew/3.15.html
- Language reference, simple statements §7.11 (`lazy`): https://docs.python.org/3.15/reference/simple_stmts.html
- Command line and environment (`-X lazy_imports`, `PYTHON_LAZY_IMPORTS`): https://docs.python.org/3.15/using/cmdline.html
- CPython `Grammar/python.gram`: https://github.com/python/cpython/blob/main/Grammar/python.gram
- CPython `Python/symtable.c` (`check_lazy_import_context`): https://github.com/python/cpython/blob/main/Python/symtable.c
- CPython `Python/codegen.c` (`codegen_validate_lazy_import`, `codegen_import`, `codegen_from_import`): https://github.com/python/cpython/blob/main/Python/codegen.c
- CPython `Parser/action_helpers.c` (`_PyPegen_checked_from_import`, `_warn_relative_import_of_lazy`): https://github.com/python/cpython/blob/main/Parser/action_helpers.c
- CPython `Python/sysmodule.c` (`sys.set_lazy_imports`, filter API): https://github.com/python/cpython/blob/main/Python/sysmodule.c
- CPython `Python/initconfig.c` (`config_init_lazy_imports`): https://github.com/python/cpython/blob/main/Python/initconfig.c
- CPython `Lib/test/test_lazy_import/__init__.py`: https://github.com/python/cpython/blob/main/Lib/test/test_lazy_import/__init__.py
- CPython release tags: https://github.com/python/cpython/tags
- LibCST v1.9.0 release notes: https://github.com/Instagram/LibCST/releases/tag/v1.9.0

C-source line numbers reflect `python/cpython@main` as fetched on 2026-08-04 and
should be re-checked against the 3.15 branch before v0.1.0 ships.
