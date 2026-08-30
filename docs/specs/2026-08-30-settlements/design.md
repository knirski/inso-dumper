# Settlements (Rozliczenia) read-only dump

Spec for M6: per-child dump of the Inso "Rozliczenia" pages — parsed
month JSON + invoice PDFs — as a new `sync --category settlements`.

Approved in workshop 2026-08-30 (approach A + all four batches).

## Problem

The Inso platform's Rozliczenia section (monthly fees, itemized
charges, invoices) exists only as server-rendered HTML inside the
web app. A parent cannot archive it: there is no export, and the
data vanishes when the child leaves the institution or the account
loses access (an open risk tracked since the PRD).

Today the user reads it in the browser; nothing is stored locally.

## Scope

**In scope:**

- `sync --category settlements` (per child, also in default `all`)
- Scrape the full history page (`/panel/child/<child-uuid>/settlement`)
- Parse every month card into `settlement.json` (amounts, saldo, due
  date, status, itemized charges)
- Download invoice PDFs once (v4 manifest + on-disk presence rule)
- `--force <YYYY-MM>` refetch, category-scoped
- Manifest schema v4 (settlements table; new per-child manifest file)

**Out of scope:**

- The current-month summary page (`/panel/settlement/child/<uuid>`) —
  the history page contains the same card; total-due is derivable
- The payment flow (`Zapłać`, transfer instructions) — never touched
- Attendance, diaries, child data, and any other HTML surface
- Writing anything to the platform (PRD: reads only)

## User Stories

### US-1 — Dump settlement history (must)

As a parent, I run `uv run inso-dumper sync franek --category
settlements` and get one `settlement.json` per month plus invoice
PDFs.

- **Given** a valid session, **when** the category runs, **then** it
  fetches the history page once, parses every month card, and writes
  `dump/<child-slug>/settlements/<YYYY-MM>/settlement.json`
  (amount, saldo, due date, status, itemized charges), 0600 files in
  0700 dirs.
- **Given** a card with a `Pobierz fakturę` link, **when** parsed,
  **then** the invoice is fetched from
  `/panel/settlement/<bill-uuid>/invoice` and written to
  `<YYYY-MM>/invoice.pdf`.
- **Given** markup that does not match the captured shapes, **when**
  parsed, **then** the run fails loud (`PLATFORM_CHANGED`), never
  with silently partial data.

### US-2 — Default behavior (must)

As a parent, I run plain `sync <child>` and settlements are included.

- **Given** the default category `all`, **when** sync runs per child,
  **then** settlements run for each child (per-child data, unlike the
  account-level messages/documents).

### US-3 — Cheap re-runs (must)

- **Given** a second run with no changes, **when** settlements sync,
  **then** the history page is re-scraped (one GET), `settlement.json`
  files are rewritten idempotently, and invoices are **not**
  re-downloaded: skip requires both a v4-manifest row with
  `invoice_downloaded_at` and the file on disk (posts rule — a
  manifest row without the file is re-downloaded).
- **Then** the summary reports
  `N settlements (M skipped, K invoices)`.

### US-4 — Force refetch (should)

- **Given** `--force <YYYY-MM>` (repeatable), **when** settlements
  sync, **then** the manifest rows are cleared and matching months'
  invoices re-download into the same directories. Force is
  category-scoped: forcing announcements never touches settlements.

### US-5 — Fail loud on drift (must)

- **Given** an unknown status badge variant (e.g. an unpaid month —
  never captured), a missing Kwota/Saldo/Termin line, an unparseable
  money string or month name, two cards resolving to the same
  `YYYY-MM`, or a non-`application/pdf` invoice response, **when**
  parsed/fetched, **then** the run fails with
  `Err(PLATFORM_CHANGED)` / `Err(HTTP)` naming the drift subject.

### US-6 — Read-only (must)

- **Given** any settlements run, **then** only the two verified GETs
  are issued (history page, invoice); the payment flow is never
  requested — test-pinned like the other read-only guarantees.

## Constraints

- Read-only platform access (AGENTS.md, PRD): GETs only, test-pinned.
- Functional core / imperative shell; `Result`-typed errors;
  `match`/`assert_never` exhaustiveness; no new dependencies.
- Secrets never enter the repo; dumps stay out of git.
- Python ≥ 3.14, uv, ruff, basedpyright, pytest per AGENTS.md.

## Context

The Aug 2026 billing spike (api-notes.md § Settlements, captures in
`docs/api-notes-data/billing-*`) established:

- Settlements has **no JSON API** — fully server-rendered documents;
  the only XHR is the shared notifications poller.
- Full history lives on one page:
  `GET /panel/child/<child-uuid>/settlement` — every month card
  (47 captured), no pagination. "Starsze rachunki" links here from
  the current-month page.
- Cards: child-initials badge, status badge (`bg-green-100`
  "opłacone" captured), `Kwota`/`Saldo`/`Termin` lines (Polish month
  names, e.g. `Termin 11 sierpnia 2026`), client-side `Zobacz
  szczegóły` expander with an itemized `Składowe rachunku` table
  (`Czesne`, meal rows with counts, `Suma`, `Do zapłaty`,
  `Zapłacono`, `Aktualne saldo`) and a `Pobierz fakturę` link.
- Invoice: `GET /panel/settlement/<bill-uuid>/invoice` → direct
  `application/pdf` (~46 KB), same-origin session-cookie auth, **no
  CloudFront signing** (unlike timeline media — a plain
  `client.request` GET works; the `_fetch_attachment` transport
  workaround is not needed).
- Unverified: unpaid/overdue badge variants, `Saldo` ≠ 0, bills
  without an invoice link. These must fail loud (US-5) until a real
  capture pins them.

## Architecture

Mirrors the messages slice; same functional core / imperative shell
boundaries.

```
models/settlements.py      pure: models + Polish date/money normalization
http/settlements.py        parser (core) + fetch shells (sockets)
dump/settlements.py        disk writer (shell)
dump/manifest.py           v4 gate + settlements table (shell)
dump/sync.py               run_settlements orchestrator (shell)
cli.py                     category plumbing (presentation)
```

### Domain model (`models/settlements.py`)

Pydantic, `extra="forbid"` — these models are our own contract
(parsed from HTML, not platform JSON), so field names are
snake_case-out.

- `SettlementStatus` — `StrEnum`: `PAID`, `UNKNOWN`. The parser maps
  any un-captured badge variant to loud drift on first sight; `PAID`
  is the only captured value, `UNKNOWN` exists for future captures.
- `SettlementItem` — one charges-table row: `label: str`,
  `amount_pln: Decimal | None`, `count: int | None` (from
  `"Śniadanie (16 sztuk):"`), `is_summary: bool` (the
  Suma/Do zapłaty/Zapłacono/Aktualne saldo rows; `Czesne` is an
  ordinary charge, not a summary row).
- `SettlementMonth` — one card: `month: str` (`YYYY-MM`, normalized
  via a Polish month-name map), `amount_pln: Decimal`,
  `saldo_pln: Decimal`, `due_date: datetime.date | None`, `status:
  SettlementStatus`, `items: list[SettlementItem]`,
  `invoice_bill_uuid: str | None`.
- `SettlementHistoryPage` — `months: list[SettlementMonth]`.

Pure helpers: `_parse_money("1 424,00 zł") -> Decimal` (accepts a
leading `-`), `_parse_due_date("11 sierpnia 2026") -> date` (Polish
month-name map), both `Result`-typed with drift errors.

### Parser (`parse_settlements_history`)

`html.parser.HTMLParser` subclass (stdlib, like the children
scraper). Cards are matched structurally; no ids/classes are assumed
beyond what the capture shows. Drift →
`Err(PLATFORM_CHANGED, 'settlements_<detail>')` with subjects
`settlements_month_card`, `settlements_money`, `settlements_month_name`,
`settlements_duplicate_month`. The core does no IO.

### Shells (`http/settlements.py`)

- `fetch_settlements_history(client, session, child_uuid)` — async
  generator yielding `CliResult[SettlementHistoryPage]`; one GET;
  non-200 → `Err(HTTP, 'settlements_status_<n>')`; redirect-to-login
  → `Err(SESSION_EXPIRED)` (reuse `session_expiry` helpers).
- `fetch_invoice(client, session, bill_uuid)` — `CliResult[bytes]`;
  non-200 → `Err(HTTP, 'invoice_status_<n>')`; content-type not
  `application/pdf` → `Err(PLATFORM_CHANGED, 'invoice_content_type')`.

### Writer (`dump/settlements.py`)

- `write_settlement_month(month, dir_path)` — `ensure_private_dir`,
  atomic `settlement.json` via `_write_bytes`, 0600.
- `write_invoice(data, dir_path)` — atomic `invoice.pdf`; the
  manifest+presence check lives in the orchestrator.

### Manifest v4 (`dump/manifest.py`)

Per-child manifest file at
`dump/<child-slug>/settlements/.manifest.sqlite`, created at
`user_version = 4`. Gate accepts `{0, 4}` for settlement manifests;
any other version → loud `Err(CONFIG, 'manifest_schema')` with the
documented recovery (delete the file — bytes on disk are untouched).
Existing v3 manifest files (announcements, messages) are separate
databases and stay at v3.

```sql
CREATE TABLE settlements (
    month_key             TEXT PRIMARY KEY,  -- 'YYYY-MM'
    bill_uuid             TEXT,              -- NULL when no invoice
    invoice_downloaded_at TEXT,              -- ISO; NULL until downloaded
    first_seen_at         TEXT NOT NULL
);
```

`recorded_settlement`, `upsert_settlement` (first_seen_at preserved),
`force_clear_settlements` mirror the conversations trio, including
the swallow-and-warn read semantics.

### Orchestrator (`run_settlements`)

`dump/sync.py`: fetch page → per month: resolve dir `<YYYY-MM>`,
write `settlement.json`, upsert manifest, download invoice when the
manifest row lacks `invoice_downloaded_at` or the file is absent (or
forced). Catches `httpx.HTTPError`/`OSError` at the boundary →
`Err(HTTP, 'media_status_<n>')`-style / `Err(IO, 'settlements_write')`
variants; never re-raises. Returns a `SyncSummary` with the new
`settlements` field.

## API Design

No external API changes (the Inso side is pinned by the spike).

### CLI

```
uv run inso-dumper sync <child>
    [--category {announcements,galleries,messages,documents,settlements,all}]
    [--force <key>]… [-v]
```

- `SyncCategory.SETTLEMENTS` joins the enum; default `all` includes
  it; `both` stays rejected.
- `_plan_for` treats settlements as per-child: it runs inside the
  child loop (unlike messages/documents, which run once per
  invocation).
- `--force` accepts `YYYY-MM` for settlements; category-scoped as
  today.
- Summary line gains a segment, only when the category ran:
  `Synced 12 announcements (…), 2 galleries, 3 conversations (…),
  4 settlements (3 skipped, 2 invoices) for franek in 95.3s.`
- The documents gate is unchanged; settlements has no gate.

### Internal contracts

```python
# http/settlements.py
def parse_settlements_history(html: str) -> CliResult[SettlementHistoryPage]
async def fetch_settlements_history(client, session, child_uuid)
    -> AsyncIterator[CliResult[SettlementHistoryPage]]
async def fetch_invoice(client, session, bill_uuid) -> CliResult[bytes]

# dump/settlements.py
def write_settlement_month(month: SettlementMonth, dir_path: Path) -> CliResult[Path]
def write_invoice(data: bytes, dir_path: Path) -> CliResult[Path]

# dump/manifest.py (v4)
def upsert_settlement(conn, month_key: str, bill_uuid: str | None,
                      invoice_downloaded: bool) -> CliResult[None]
def recorded_settlement(conn, month_key: str) -> CliResult[SettlementRecord | None]
def force_clear_settlements(conn) -> CliResult[None]
```

Error subjects: `settlements_status_<n>`, `settlements_month_card`,
`settlements_money`, `settlements_month_name`,
`settlements_duplicate_month`, `invoice_status_<n>`,
`invoice_content_type`, `settlements_write`; manifest kinds unchanged.

## Data Model

### On-disk layout

```
dump/<child-slug>/settlements/
├── .manifest.sqlite            # user_version = 4, settlements table
├── 2026-08/
│   ├── settlement.json         # SettlementMonth, 0600
│   └── invoice.pdf             # 0600, when a faktura exists
└── 2022-10/
    └── …
```

Month directories are named by the normalized `YYYY-MM` key — no
slugification needed (the key is derived from a month-name map, not
platform text).

### JSON shape (example)

```json
{
  "month": "2026-08",
  "status": "paid",
  "amount_pln": "1424.00",
  "saldo_pln": "0.00",
  "due_date": "2026-08-11",
  "items": [
    {"label": "Czesne", "amount_pln": "1080.00", "count": null, "is_summary": false},
    {"label": "Śniadanie", "amount_pln": "64.00", "count": 16, "is_summary": false},
    {"label": "Do zapłaty", "amount_pln": "1424.00", "count": null, "is_summary": true}
  ],
  "invoice_bill_uuid": "a907a688-322b-4044-bb52-09ecd986a769"
}
```

## Trade-offs

- **Parsed JSON + PDFs** over raw-HTML dumps: the HTML is redundant
  for small, stable data; parsed JSON is immediately queryable.
- **History page only** over also scraping the current-month page:
  identical card data, one parser instead of two; the total-due
  header is derivable by summing `saldo_pln`.
- **stdlib `HTMLParser`** over BeautifulSoup/lxml: zero deps, two
  parser precedents in-repo, markup is simple Tailwind divs.
- **Manifest v4** over no manifest: a schema bump and ~60 lines of
  manifest code for one-page data, in exchange for skip-state
  persistence and reporting parity with posts/messages. The strict
  gate (no migrations) matches the v3 precedent; the v2→v3
  reject-loud incident confirmed the recovery path.
- **`Decimal` money** over float: amounts are text
  (`1 424,00 zł`); Decimal avoids float drift in the JSON dump
  (serialized as strings in JSON for exactness).

## Open Questions

1. **Unpaid/partial-saldo badge variants** — none captured (the
   account is fully paid); they surface as drift (US-5) until a real
   capture pins them. Mitigation: one-capture fix, same as the
   video-shape precedent. No design impact.
2. **Negative saldo** — parser accepts a leading `-`; harmless
   either way. No decision needed.
