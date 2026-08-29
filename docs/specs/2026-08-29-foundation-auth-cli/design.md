# Foundation: project skeleton, auth, config, CLI, children listing

## Problem

A parent of children in an institution using the Inso platform
(https://app.inso.pl) accumulates years of announcements with photo sets,
messages, and documents that the platform provides no official way to export.
The full project (`docs/prd.md`, `docs/plan.md`) covers content sync, dedup,
indexing, and polish across milestones M0–M5. Before any of that, the user
needs a runnable tool that authenticates against app.inso.pl and lists the
children on the account.

This spec covers only the foundation needed for the **first user-visible
checkpoint** in `plan.md` (Phase 1 check):

> `uv run inso-dumper login` prints "OK" and `uv run inso-dumper children`
> lists real children.

Everything beyond that (announcements, messages, documents, dedup, indexes,
`verify`, `materialize`) belongs in follow-up specs.

## Scope

- **In scope:**
  - uv-managed Python package `inso_dumper` with a `typer` CLI entry point
    `inso-dumper`.
  - Config loader (TOML + env overrides).
  - Session store under `~/.local/state/inso-dumper/`.
  - Auth against app.inso.pl driven by the documented discovery spike
    (`docs/api-notes.md`).
  - `login` command — validate credentials, persist a refreshable session.
  - `children` command — list children of the authenticated account with a
    stable slug per child.
  - Minimal HTTP client abstraction so a later switch from a JSON-API
    implementation to a browser-driven scrape is a one-module change.
  - Token-redacting logging and crash-safe re-runs.
- **Out of scope:**
  - Announcement, message, or document sync.
  - Storage layout under `dump/` (PRD F3) — touched only as a forward note.
  - SQLite state DB for incremental sync (PRD F5) — added in a later spec.
  - Deduplication, indexes, galleries (PRD F4, F7).
  - `verify`, `materialize` (PRD F6).
  - Multi-account orchestration (PRD Non-goals).
  - Packaging for PyPI / `uv tool install` (PRD M5).

## Constraints

- All Python invocations go through `uv run …`; no bare `python` / `pip`
  (AGENTS.md).
- Python floor: 3.14 (AGENTS.md; PRD NFRs updated in step with this
  spec). 3.14 unlocks PEP 695 generic syntax without
  `from __future__ import annotations` shims. Pin via
  `requires-python = ">=3.14"` and a `.python-version` file.
- Deps (PRD NFRs + AGENTS.md): `httpx`, `typer`, `rich`, `pydantic`. Dev:
  `pytest`, `pytest-asyncio`, `ruff`, `basedpyright`. The `Result` type
  (`Ok`, `Err`) is defined in `inso_dumper._result`; see AGENTS.md
  "Result type".
- All Inso traffic is read-only GETs where possible; non-GETs must be
  justified in code and called out in `api-notes.md` (AGENTS.md).
- Secrets never enter the repo: `INSO_EMAIL` / `INSO_PASSWORD` env vars or
  the user-level config file (AGENTS.md).
- `uv.lock` is committed; CI uses `uv sync --locked`.
- The dump output directory contains children's personal data; nothing
  derived from it is ever committed (AGENTS.md).
- The `agent-browser` skill is the authoritative tool for the discovery
  spike (PRD/plan Phase 0).
- Style: modern, idiomatic, typed, FP-style Python (AGENTS.md "Python
  style"). Use `Result[ValueT, ErrorT]` at all recoverable failure
  boundaries, `match`/`case` + `assert_never` for closed unions,
  `@dataclass(frozen=True, slots=True)` for value objects, and `StrEnum`
  for closed string sets. basedpyright in `recommended` mode.

## Context

### What exists today

- `docs/prd.md` — product requirements, milestones, non-goals, open
  questions.
- `docs/plan.md` — phased plan. Phase 0 = API discovery; Phase 1 = this
  spec.
- `AGENTS.md` — project rules: uv-only, secrets policy, read-only scraping.
- `.agents/AGENTS.md` — skill maintenance rules (read-only for this work).
- No source code yet; `docs/specs/` did not exist before this document.
- A `skills-lock.json` and skill install under `.agents/skills/` and
  `.claude/skills/` (symlink). Skills relevant to this spec: `agent-browser`,
  `python-build-tools`, `python-modern-python`, `python-architecture`,
  `python-testing`.

### How it works end-to-end (target)

```
inso-dumper login
   → config.load()                     # TOML + env, secrets from env
   → client.authenticate(creds)        # httpx against discovered endpoint
   → session.save(refreshable_blob)    # to ~/.local/state/inso-dumper/session.json
inso-dumper children
   → session.load() → refresh if expired
   → client.list_children()            # against discovered endpoint
   → children_table(rich)
   → exit 0
```

### Open external dependencies (resolved during this spec, but not by code)

The auth flow and the "list children" endpoint are **not yet known**. They
are discovered in a 1–2 hour spike using `agent-browser` against
https://app.inso.pl/login. The spike's output is `docs/api-notes.md` and is
a **required input** before this spec's code is written. See
[Open Questions](#open-questions) for the spike's contract.

### Existing patterns and conventions

- Tooling: `uv` only (AGENTS.md). `pyproject.toml` declares deps; `uv.lock`
  pins them.
- TOML config at `~/.config/inso-dumper/config.toml` (PRD NFRs).
- State at `~/.local/state/inso-dumper/` (PRD NFRs, xdg-style).
- Slash-case CLI commands: `inso-dumper login`, `inso-dumper children`.
- `rich` for table output, `typer` for command plumbing, `pydantic` for
  config + payload models.
- The `python-architecture` skill recommends a functional core / imperative
  shell split. For a foundation spec with only auth + listing, the
  discipline is to keep `inso_dumper.api.*` (pure parse/transform) free of
  IO and to push all httpx/file I/O into `inso_dumper.http` and
  `inso_dumper.session`.

### Gotchas, assumptions, technical debt

- **Assumption:** the platform exposes a JSON API the `agent-browser` spike
  will find. If the spike finds a server-rendered or heavily-SPA-only app,
  the auth path forks to a browser-driven implementation (see
  [Architecture](#architecture) — the `HttpClient` port is designed for
  this).
- **Assumption:** the session can be refreshed without interactive 2FA
  most of the time. 2FA handling is explicitly out of scope for v1 and
  re-listed in [Open Questions](#open-questions) as a known follow-up.
- The PRD does not specify the children-listing endpoint, so the slug
  derivation rule and what fields appear are designed in this spec from
  the minimum the parent needs to identify a child.
- `~/.local/state/inso-dumper/session.json` must be `0600`; the spec
  enforces it on write.
- **PRD divergence (intentional):** PRD F3 shows `children/2021-anna-k/`
  with the year as a slug prefix. This spec uses `children/anna-kowalska/`
  with no year — the follow-up `announcements-and-dedup` spec should
  update PRD F3 to drop the year-prefix example.

## Architecture

### Component structure (functional core / imperative shell)

```
inso_dumper/
├── __init__.py
├── __main__.py                # `python -m inso_dumper` entry
├── cli.py                     # typer app, command wiring
├── config.py                  # pydantic Settings, TOML+env loader (functional)
├── paths.py                   # xdg path resolution (functional)
├── errors.py                  # closed CliError union (StrEnum kind + dataclass payload)
├── logging_setup.py           # rich logging, token redaction
├── http/
│   ├── __init__.py
│   ├── client.py              # HttpClient Protocol + HttpxClient impl (shell)
│   ├── auth.py                # login + refresh flows (shell)
│   └── children.py            # list_children (shell)
├── models/
│   ├── __init__.py
│   ├── config.py              # Config pydantic model
│   ├── session.py             # Session pydantic model
│   └── children.py            # Child pydantic model + slug derivation (core)
└── session/
    ├── __init__.py
    └── store.py               # load/save Session (shell, 0600 enforcement)
tests/
├── unit/
│   ├── test_config.py
│   ├── test_paths.py
│   ├── test_models_children.py
│   ├── test_session_store.py
│   └── test_http_auth.py
└── integration/
    └── test_cli.py            # CLI command tests with monkeypatched client
```

Layer rules:

- `models/` and the parsing helpers in `http/auth.py`, `http/children.py`
  contain no IO. They take already-fetched bytes/JSON and return pydantic
  models. This keeps the auth payload shape unit-testable from recorded
  HAR fixtures without making real HTTP calls.
- `http/client.py` defines a `HttpClient` `Protocol` (`async def request(method, url, **kw) -> Response`)
  and an `HttpxClient` implementation. The `Protocol` exists so the
  foundation can ship against JSON-API auth **and** be re-pointed at a
  browser-driven `BrowserClient` later without touching call sites.
- `cli.py` wires commands: it loads config, builds an `HttpClient`, calls
  `auth.login` or `children.list_children`, formats with `rich`, exits.
  No business logic in the CLI.
- `config.py` is the only place that touches the env / TOML; the rest of
  the code takes a `Config` object. This makes tests trivial — they
  construct `Config(...)` directly.

### Domain model

```python
# models/children.py
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Child:
    child_id: str          # opaque platform id; kept for API calls
    first_name: str
    last_name: str
    group: str | None      # e.g. "Biedronki"; None if API doesn't return it

    @property
    def slug(self) -> str:
        # Example: "anna-kowalska".
        first = _ascii_fold(self.first_name).lower()
        last = _ascii_fold(self.last_name).lower() if self.last_name else ""
        base = f"{first}-{last}" if last else first
        return _SLUG_RE.sub("-", base).strip("-") or "child"

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()
```

Slugs are stable across runs (deterministic) and human-readable.

```python
# models/session.py
class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    cookies: dict[str, str]            # platform session cookies
    bearer_token: str | None = None    # if platform uses JWT
    refresh_token: str | None = None
    expires_at: datetime               # UTC; refresh before this
    csrf_token: str | None = None
    raw_metadata: dict[str, Any]       # opaque passthrough for future fields
```

```python
# models/config.py
class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: HttpUrl = HttpUrl("https://app.inso.pl")
    rate_limit_per_second: float = 3.0
    request_timeout_seconds: float = 30.0
    # credentials are NOT in this model — read directly from env at the
    # `auth` boundary to keep them out of logs and repr.
```

### Where business logic lives, where IO lives

| Concern | Location | Why |
| --- | --- | --- |
| Slug derivation | `models/children.py` (`Child.slug`) | Pure function on a value object |
| Cookie expiry math | `models/session.py` (`Session.is_expired`) | Pure predicate |
| Login payload construction | `http/auth.py` (function `build_login_payload(email, password) -> dict`) | Pure transform of credentials to platform's expected shape |
| Login response parsing | `http/auth.py` (function `parse_login_response(body: bytes) -> Session`) | Pure parse; no IO |
| `httpx` call against `/api/login` | `http/client.py` (`HttpxClient`) | The only place that opens sockets |
| File read/write of `session.json` | `session/store.py` | The only place that touches disk |
| CLI arg parsing, exit codes | `cli.py` | Shell |
| `rich` table formatting of children | `cli.py` (or a `presenters/` helper if it grows) | Shell |

### Why a `Protocol` for `HttpClient` (v1 cost paid for v2)

The PRD's Phase 0 decision point says:

> JSON API exists → Python httpx client (default).
> API too opaque → fall back to browser-driven scraping
> (agent-browser in a long-lived session, `--session-name inso`).

The first spec ships the JSON path. To avoid rewriting every caller when
the spike forces a browser fallback, `HttpClient` is a `Protocol` with the
single method:

```python
class HttpClient(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response: ...

@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    headers: Mapping[str, str]
    def json(self) -> Any: ...
```

`HttpxClient` implements it. A future `BrowserClient` (subclass of nothing,
same Protocol) replaces it in `cli.py` only. The decision to pay this cost
now is justified by the PRD explicitly enumerating both paths.

## API Design

### CLI surface (v1)

```
inso-dumper login [--config PATH]
inso-dumper children [--config PATH] [--json]
inso-dumper --version
inso-dumper --help
```

`login`:

- Reads `INSO_EMAIL` and `INSO_PASSWORD` from the env. If either is missing,
  prints a one-line error pointing at the env vars and exits 2.
- Performs the discovered login exchange (one POST, possibly a follow-up
  cookie/csrf exchange — the spike decides).
- Saves the resulting `Session` to `~/.local/state/inso-dumper/session.json`
  with `0600` perms. Refuses to save if the platform response is missing
  `expires_at` or any field the `Session` model requires.
- Prints `OK` to stdout on success. Prints the platform `user_id` and
  expiry timestamp at `INFO` level.
- On auth failure (HTTP 401/403), exits 3 with a redacted message.

`children`:

- Loads the saved `Session`. If expired, attempts one refresh. If refresh
  fails, exits 4 with a clear "run `inso-dumper login`" message.
- Calls the discovered "list children" endpoint.
- Prints a `rich` table: `Slug | Name | Group`. Exits 0.
- `--json` prints the same data as JSON for scripting; the JSON shape
  matches the `Child` pydantic model (`child_id`, `first_name`, `last_name`,
  `group`, `slug`, `display_name`).

### Config surface

TOML (`~/.config/inso-dumper/config.toml`):

```toml
base_url = "https://app.inso.pl"
rate_limit_per_second = 3.0
request_timeout_seconds = 30.0
```

Env (PRD NFRs):

- `INSO_EMAIL`, `INSO_PASSWORD` — credentials, never written to TOML.
- `INSO_DUMPER_CONFIG` — alternate config file path.

Env does not override TOML for non-secret keys in v1. This is a
deliberate non-feature: keep the surface small until a future spec needs
more.

### Logging

`rich`-backed stderr logging at `INFO` by default, `-v` for `DEBUG`.
Loggers:

- `inso_dumper.http` — request method/path/status/duration.
- `inso_dumper.session` — load/save events; never logs tokens.
- `inso_dumper.cli` — command entry/exit.

A `TokenRedactingFilter` is installed at root and scrubs anything matching
`(?i)(token|cookie|set-cookie|password|secret)=?` values from log records.
This is the "log redacts tokens" PRD NFR — done at the logger boundary so
individual call sites cannot forget.

### Error handling approach

The CLI's recoverable error family is a closed union, not an exception
hierarchy. AGENTS.md mandates `Result[ValueT, ErrorT]` at failure
boundaries; the CLI is the single dispatcher that maps each variant to
an exit code.

```python
# _result.py — see AGENTS.md "Result type"
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Ok[ValueT]:
    value: ValueT

@dataclass(frozen=True, slots=True)
class Err[ErrorT]:
    error: ErrorT

type Result[ValueT, ErrorT] = Ok[ValueT] | Err[ErrorT]
```

```python
# errors.py
from enum import StrEnum
from dataclasses import dataclass

class CliErrorKind(StrEnum):
    CONFIG = "config"               # exit 2 — bad/missing config or env
    AUTH = "auth"                   # exit 3 — login rejected
    SESSION_EXPIRED = "session_expired"  # exit 4 — refresh failed; run `login`
    HTTP = "http"                   # exit 5 — transport-level failure
    PLATFORM_CHANGED = "platform_changed"  # exit 6 — payload shape drift
    INTERNAL = "internal"           # exit 99 — unclassified (should not happen)

@dataclass(frozen=True, slots=True)
class CliError:
    kind: CliErrorKind
    subject: str = ""        # short redacted identifier for logs

type CliResult[T] = Result[T, CliError]
```

Each call site returns `Result[..., CliError]`. The CLI is the only place
that dispatches on `kind`:

```python
# cli.py (sketch)
def exit_code_for(err: CliError) -> int:
    match err.kind:
        case CliErrorKind.CONFIG:           return 2
        case CliErrorKind.AUTH:             return 3
        case CliErrorKind.SESSION_EXPIRED:  return 4
        case CliErrorKind.HTTP:             return 5
        case CliErrorKind.PLATFORM_CHANGED: return 6
        case CliErrorKind.INTERNAL:         return 99
        case _ as unreachable:               return assert_never(unreachable)
```

Exit codes are stable and documented in `--help`. A future `systemd` timer
(PRD M5) can react to each one. The `INTERNAL` variant exists for
unclassified exceptions caught at the top of each command; the spec
asserts that no path returns it during normal operation, and basedpyright
flags any missing variant via `assert_never`.

`parse_login_response` and `list_children` are the two pure parsers
that return `Result[Session, CliError]` and `Result[list[Child], CliError]`
respectively. Their IO shells (`HttpxClient`, `SessionStore`) catch
`httpx.HTTPError` / `OSError` and convert them into the relevant
sanitized `CliError` — those exceptions never escape the boundary.

## Data Model

The PRD's SQLite state DB is **not** part of this spec. v1 only persists
a single `Session` JSON file. The state DB appears in the announcements
spec.

`session.json` shape:

```json
{
  "schema_version": 1,
  "user_id": "...",
  "cookies": {"sessionid": "..."},
  "bearer_token": null,
  "refresh_token": null,
  "expires_at": "2026-08-30T12:00:00Z",
  "csrf_token": null,
  "raw_metadata": {}
}
```

`schema_version` is checked on load; mismatches return
`Err(CliError(kind=CONFIG, subject="session_schema"))`. The file is
written atomically (write to `session.json.tmp`, `os.replace`,
`chmod 0o600`). On load, file mode is verified; if it has been relaxed
the loader warns and re-tightens.

## Trade-offs

### Considered: ship a single `inso_dumper.py` instead of a package

- **Pro:** less ceremony for a two-command tool.
- **Con:** the second we add a second module (e.g. the eventual
  announcements client) the file becomes unmanageable, and we have to
  pay the cost of the refactor under deadline pressure.
- **Decision:** package layout. The PRD's plan implies multiple
  content-type clients (announcements, messages, documents) — starting
  with the layout they will live in is cheaper than migrating later.

### Considered: typer vs. click vs. argparse

- **Pro typer:** the PRD lists `typer` in NFRs and `python-modern-python`
  patterns pair naturally with it.
- **Con typer:** the dependency surface is larger than `argparse`.
- **Decision:** `typer`. PRD is explicit.

### Considered: synchronous `httpx.Client` vs. async

- **Pro async:** future content sync will benefit from concurrent fetches.
- **Pro sync:** `login` and `children` are one round-trip each; async
  adds `asyncio` ceremony for no v1 win.
- **Decision:** `HttpClient` is `async` because the eventual content
  client will need it, but the v1 CLI uses `asyncio.run` from sync
  commands. The discipline is "all `HttpClient` methods are async, callers
  may be sync or async." Tests use `pytest-asyncio` for the async path.

### Considered: store session as JSON vs. encrypted blob

- **Pro encryption:** defense in depth if the laptop is stolen while the
  session is still valid.
- **Con:** key management (a derived key from the user password? a system
  keyring?) is significant new surface and out of PRD scope.
- **Decision:** plaintext JSON with `0600` mode. Listed in
  [Open Questions](#open-questions) as a v1.1 candidate.

### Considered: per-child API model vs. flat list

- The PRD Open Question 3 asks whether announcements are scoped per-child
  or per-group. For `children`, the equivalent question is whether
  `list_children` returns the user's children flat or returns groups
  with children inside. The design treats the response as a flat list of
  `Child` and lets the platform's payload decide; a future spec can
  model groups explicitly if needed.

## Open Questions

These block implementation and are resolved by the **discovery spike** in
`docs/api-notes.md` before the first line of code is written. The spike
itself is tracked as a discrete step in `plan.md` Phase 0; this spec
records what it must produce.

1. **Auth mechanism** — does app.inso.pl accept a `POST /api/login` (form
   or JSON) returning a cookie + bearer, or does it require a
   server-rendered form post, or is the session purely a `localStorage`
   JWT (in which case `HttpxClient` cannot satisfy the Protocol and we
   need `BrowserClient` from day one)?
2. **Children endpoint** — exact path, request shape, and response JSON
   schema. The `Child` pydantic model in this spec is a candidate and
   gets adjusted to match.
3. **CSRF** — is there a CSRF token? When? Header name?
4. **Refresh** — is there a refresh endpoint, or does the bearer/JWT
   have its own long expiry with re-login only?
5. **2FA** — is 2FA mandatory for any account, or only some? Out of
   scope for v1; if the user can't get past login without 2FA, this spec
   is unblocked by the user disabling 2FA for testing (documented in the
   `login` command's README section) or escalated to a follow-up spec.
6. **Rate limit on login** — brute-force protection may force a delay
   between retries; the spec's `rate_limit_per_second` should be applied
   to login too.

### Decomposition reminder

The PRD is M0–M5. This spec is M0 (the discovery spike, owned by
`api-notes.md`) + M1 foundation only. Follow-up specs, each their own
`docs/specs/<date>-<slug>/design.md`:

- `announcements-and-dedup` — Phase 2+3 (M1 finish, M2).
- `messages-and-documents` — Phase 4 (M3).
- `indexes-and-gallery` — Phase 5 (M4).
- `polish-and-materialize` — Phase 6 (M5).
