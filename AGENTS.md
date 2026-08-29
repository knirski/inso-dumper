# AGENTS.md

Guidance for AI agents working in this repository.

## Project

**inso-dumper** — a local backup tool for the Inso platform (https://app.inso.pl), a
kindergarten/preschool parent-teacher communication app. It dumps announcements (with photo
sets), messages, and documents available to a parent account, per child, with deduplication
into a shared `_common/` area.

Read before doing any work:

- `docs/prd.md` — product requirements (storage layout, dedup rules, CLI contract).
- `docs/plan.md` — phased implementation plan with per-phase checks.
- `docs/api-notes.md` — Inso API discoveries (created in Phase 0; may not exist yet).

Status: planning/API-discovery phase. Do not invent API details; they must come from real
captured traffic (see plan.md Phase 0).

## Tooling — uv only

- The project is uv-managed. **All** Python invocations go through uv:
  `uv run inso-dumper …`, `uv run pytest`, `uv run ruff check .`.
- Never use bare `python`, `pip`, or `pipx`. Add deps with `uv add`; commit `uv.lock`.
- Python floor: **3.14** (see `## Python style` below). Pin via
  `requires-python = ">=3.14"` and `.python-version` when `pyproject.toml`
  exists.
- There is no Bash scripting convention in this repo; prefer Python run via uv.

## Python style

The codebase is **modern, idiomatic, typed, FP-style Python** with
basedpyright. These rules apply to every module unless a deviation is
explicitly justified in the spec that introduces it.

- **Tooling:** manage dependencies with `uv`; format and lint with
  `ruff` (`target-version = "py314"`); type-check with `basedpyright`
  in `recommended` mode; run tests through `uv run pytest`.
- **Type annotations:** always explicit. Prefer `int | None` over
  `Optional[int]`, `list[X]` over `List[X]`, and modern generic syntax
  (`class Ok[ValueT]:`, `type Result[V, E] = Ok[V] | Err[E]`) over
  `TypeVar` boilerplate.
- **Data classes:** prefer `@dataclass(frozen=True, slots=True)` for
  immutable value objects. Use `StrEnum` for closed string sets
  (error kinds, content types, etc.).
- **Functional core / imperative shell:** the **functional core** is
  pure: parsing, validation, normalization, slug derivation, response
  decoding, error mapping. The core has no IO, no `try/except` for
  control flow, no `httpx` / `os` / `pathlib` calls, no `print`, no
  logging beyond typed return values. It lives in `models/`, in the
  pure helpers of `http/auth.py` and `http/children.py`
  (`build_login_payload`, `parse_login_response`, `parse_children_list`),
  and in `errors.py` / `_result.py`. The **imperative shell** is
  everything else: `http/client.py` (sockets), `session/store.py`
  (disk), `cli.py` (terminal), and the *shell* functions in
  `http/auth.py` and `http/children.py` that orchestrate shell calls
  into `Result`-typed outcomes. The shell catches `httpx.HTTPError`,
  `OSError`, and friends and converts them to the relevant `CliError`
  variant at the boundary — it never re-raises, and the core never
  sees those exceptions. CLI entrypoints are responsible only for
  argument parsing, effect orchestration, presentation, and exit
  codes; no business logic in the CLI. A single outer `try/except
  Exception` in `cli.py` is allowed only as the last-resort safety
  net that returns exit 99 (`INTERNAL`); see the "Result type" rule
  below for the result-typed return that every other function uses.
- **Typed results over exceptions for control flow:** functions that
  can fail in expected, recoverable ways return
  `Result[ValueT, ErrorT]` (see "Result type" below) instead of
  raising. Exceptions are reserved for programmer error and
  unrecoverable conditions. The CLI maps the closed error union to
  exit codes at the edge.
- **Exhaustive pattern matching:** for closed unions (error
  families, command kinds, payload variants), use `match`/`case`
  with **explicitly enumerated** cases and end with
  `assert_never(value)` (from `typing`) so basedpyright reports
  missing variants at type-check time. Do not use a bare
  `case _:` as the last arm of a closed-union match.
- **Tests:** idiomatic pytest. No `unittest.TestCase` subclasses and
  no test classes — tests are plain functions named `test_*` that
  take fixtures and use `pytest.mark.parametrize` for matrix cases.
  Use `tmp_path` for filesystem, `monkeypatch` for env vars,
  `pytest.raises` only for genuinely programmer-error conditions —
  expected failures are `Result` values, not raised exceptions.
- **YAGNI:** apply the rules above pragmatically, not as
  ceremony. Do not wrap standard-library calls in helper types,
  introduce "reusable" abstractions, or freeze values whose lifetime
  is one function call. The smallest design that is deterministic
  and testable wins.

### Result type

`Result[ValueT, ErrorT] = Ok[ValueT] | Err[ErrorT]` is the project's
standard typed outcome. It lives at `src/inso_dumper/_result.py`
(path finalized by the implementation spec) and is independent of
any external project. It uses PEP 695 generic syntax:

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Ok[ValueT]:
    value: ValueT

@dataclass(frozen=True, slots=True)
class Err[ErrorT]:
    error: ErrorT

type Result[ValueT, ErrorT] = Ok[ValueT] | Err[ErrorT]
```

Usage rules:

- Parse / validate / sanitize functions return `Result[..., ErrorT]`,
  not raise. Their error union is the closed family of *expected*
  failures for that function.
- IO boundary functions (`http.client`, `session.store`, `paths`,
  filesystem writes) catch exceptions and convert them to a
  sanitized error variant of the relevant family — they never
  propagate raw `OSError` / `httpx.HTTPError` past the boundary.
- The CLI dispatches on the closed error union with `match` and
  `assert_never` and maps each variant to the documented exit code
  (`docs/specs/2026-08-29-foundation-auth-cli/design.md` § API
  Design).
- A `Result` whose `Err` variant contains a token, password, cookie,
  or session blob is treated as sensitive: log it at `INFO` only
  with the same token-redacting filter used elsewhere.

## Repo layout

- `docs/` — lowercase file names (prd.md, plan.md, api-notes.md).
- `.agents/skills/<skill>/` — project skills (source of truth).
- `.agents/AGENTS.md` — rules for maintaining/updating those skills; read it before
  touching anything under `.agents/`.
- The future dump output directory (`dump/` by default) contains children's personal data —
  never commit it or anything derived from it.

## Conventions

- File names: lower case everywhere.
- Secrets (Inso credentials, session tokens) never enter the repo — use env vars
  (`INSO_EMAIL`/`INSO_PASSWORD`) or user-level config paths.
- Commits: conventional style, e.g. `docs: …`, `feat: …`, `fix: …`.
- Work is read-only with respect to the Inso platform: GET-only endpoints unless verified
  otherwise; never send messages or mark content as read as a side effect.
