# PRD: inso-dumper

Backup/dump tool for the **Inso** platform (https://app.inso.pl) — the kindergarten/preschool
communication app used by parents and teachers.

## Problem

A parent's Inso account accumulates years of content: teacher announcements with photo sets,
messages, and documents. There is no official export. If the child leaves the institution (or the
institution switches providers), everything is lost. We want a **local, structured, durable archive**
of everything the parent account can see.

## Goals

1. Dump all content available to a parent account: **announcements (tablica/ogłoszenia) with
   attachments**, **messages (komunikator)**, **documents/files tab**.
2. Preserve structure and make content **findable by date and event name**, especially photo sets.
3. Store content **per child where the platform scopes it per child**
   (announcements/galleries); account-level content (messages, documents) is stored
   once. Automatic **media deduplication into a shared `_common/` area**
   (the same photo is usually broadcast to multiple children/groups).
4. Support **multiple children** in the same (or different) institution(s) from one account.
5. Be **incremental**: first run = full dump; later runs download only what's new.
6. Be resilient: resumable downloads, retries, rate-limited, idempotent re-runs.

## Non-goals

- No write access to Inso (never send messages, never mark-as-read, never upload anything,
  never write to the drive or billing). Read-only `GET`s only — verified by test.
- Billing **reads** are in scope as of Aug 2026 (settlements/"Rozliczenia" dump, see Phase 7 of
  `plan.md`) — strictly read-only viewing of fees and invoices, never interaction.
- No multi-account orchestration UI (one parent account per run is enough; CLI flag for a second).
- No cloud upload / sync destination (local filesystem only; user can rsync/restic the output).
- Announcement **comments**: the posts API exposes counts only, not bodies — not dumped.

## Legal & privacy notes

- Scope: personal/household backup of the user's own children's data (GDPR "household exemption").
- Credentials are stored outside the repository; the dump contains children's photos — treat the
  output directory as sensitive, never commit it.
- Polite scraping: throttled requests, only content the logged-in parent can already see.

## Users

- Primary: the repo owner (parent with 2+ children in one Inso institution).
- Secondary: any parent of the same institution (config-driven, no institution hardcoding).

---

## Functional requirements

### F1. Authentication
- Login with e-mail + password (config file or `INSO_EMAIL`/`INSO_PASSWORD` env).
- Session persisted locally; re-login on expiry. Use the mechanism discovered during Phase 0
  (agent-browser HAR capture of app.inso.pl login → cookies / CSRF tokens / bearer tokens).

### F2. Content discovery

Scope discovered during the Aug 2026 spikes (see `api-notes.md`):

- **Announcements + galleries (per child)**: `/system-api/timeline/posts` paginated JSON,
  filtered by `child` and category (Ogłoszenia id 2, Galerie zdjęć id 3). Post payload: id,
  title, HTML body, author, `createdAt`, target groups, photos + videos (videos ride inside
  `media.photos[]` with `type:"video"`), document attachments.
- **Messages (account-level)**: `/system-api/conversations` + per-conversation history.
  The `?child=` param is accepted but ignored — conversations are group-teacher threads, so
  messages are dumped once per account, not per child (PRD amendment, Aug 2026).
- **Documents (account-level)**: the Dysk drive (`/drive/items` tree). Same account-level
  reasoning; node shapes provisional until a non-empty drive is captured.

### F3. Storage layout

Shipped layout (Aug 2026), followed by the planned messages/documents extension:

```
dump/
├── _common/                                  # shared, deduplicated media
│   ├── photos/<sha256[:2]>/<sha256>.<ext>    # content-addressed blob store
│   ├── videos/<sha256[:2]>/<sha256>.<ext>
│   └── attachments/<sha256[:2]>/<sha256>.<ext>
├── <child-slug>/                             # e.g. 2021-anna-k
│   └── announcements/                        # both announcements and galleries posts
│       ├── .manifest.sqlite                  # per-child sync state (posts table)
│       ├── 2025-09-15_dzien-kolorowy/        # event directory (flat, no year level)
│       │   ├── post.json                     # full post metadata + source URLs
│       │   ├── post.html
│       │   ├── post.md                       # HTML→Markdown conversion
│       │   ├── photos/001.jpg ...            # ordered hardlinks into _common/
│       │   └── videos/ and files/            # same linking scheme
│       └── 2025-09-08_obejscie/
├── messages/                                 # account-level (planned, spec approved)
│   ├── .manifest.sqlite                      # schema v3: conversations table
│   └── <conversation-dir>/                   # YYYY-MM-DD_<slugified-recipient>
│       ├── conversation.json
│       └── messages/0001.json ...            # appended tail-wise
├── documents/                                # account-level (planned, gated on capture)
```

Naming rules:
- Event directory: `YYYY-MM-DD_<slug-of-title>`; slug transliterated to ASCII, lowercased,
  length-capped; disambiguated with `-2` suffix if collision on the same day.
- If a post has no meaningful title → `YYYY-MM-DD_ogloszenie` (galleries get a category-aware
  fallback).
- Media ordered by in-post order → `001.jpg`, `002.jpg` (extensions preserved); per-kind
  subdirectories `photos/`, `videos/`, `files/`.
- Conversation directory name is assigned once from the first-seen `lastUpdate` date and does
  not chase later activity.

### F4. Deduplication rules
1. **File-level (shipped)**: blob stored once by SHA-256 under `_common/{photos,videos,attachments}/`;
   per-child and per-conversation files are **hardlinks** into the blob store, with symlink
   fallback and plain copy on filesystems that reject links; metadata records the checksum.
2. **Announcement-level (revised)**: the same post delivered to 2+ children gets a post directory
   per child (per-child manifests are independent), but the media bytes exist exactly once via
   rule 1. The originally sketched `_common/announcements/<id>/` area is not implemented.
3. `inso-dumper materialize` (resolve links to real copies for portability) remains a planned
   optional command (Phase 6).

### F5. Incremental sync
- SQLite manifests per slice (`<slice>/.manifest.sqlite`), each carrying a `user_version`
  schema gate — an unknown version fails loud instead of migrating silently.
- **Posts**: every run walks the list (all pages) and skips posts whose id is recorded and whose
  directory exists; `--force <slug>` re-dumps a specific post (also the recovery path for
  edited/removed content and truncated dumps).
- **Conversations (planned)**: manifest v3 records `last_update` + `message_count`; re-runs
  tail-fetch new messages via `startingIndex` instead of re-walking history. Old-message
  edits/removals are caught only by a `--force` re-fetch (accepted trade-off).
- Documents: byte-dedup makes re-walks idempotent; no state table planned.

### F6. CLI
All commands are run through uv: `uv run inso-dumper …`.
```
inso-dumper login            # interactive: validate credentials, save session   [shipped]
inso-dumper children         # list children + slugs                                [shipped]
inso-dumper sync <child-slug> [--category announcements|galleries|both]            [shipped]
                             #   messages/documents/all land with the M3 spec
inso-dumper index            # rebuild _index.json + index.html pages (offline)     [shipped]
inso-dumper verify           # recompute checksums, report missing/corrupt blobs    [shipped]
inso-dumper materialize      # replace symlinks with real copies (_common kept)     [shipped]
```
Options: `--config`, `--dump-root`, `--force <slug>` (repeatable), `-v/--verbose`.
`--limit` (testing aid) is not implemented; `--full` was replaced by the per-slug `--force`.
`verify` exits 0 clean and 1 with findings (scriptable audit; the closed
`CliError` exit-code family is untouched).

### F7. Human-friendly output
- Per-post `post.html` + `post.md` (shipped); per-event `index.html` photo gallery and the
  top-level `index.html` + `_index.json` (by date/child/event name) ship with the offline
  `inso-dumper index` command (Phase 5, shipped), so "find photos from Dzień Kolorowy 2025"
  is a filename/search away.

---

## Non-functional requirements

- Python ≥3.14, project managed with **uv** (`uv` owns venv, deps, and every invocation — all
  scripts/commands run via `uv run`); deps: `httpx`, `typer`, `rich`, `pydantic`
  (models for API payloads), `sqlite3` (stdlib).
- Rate limit: default ~2–4 req/s, jittered; per-file retry ×3 with backoff.
- Crash-safe: download to temp file, checksum, atomic rename; safe to Ctrl-C and re-run.
- Config: `~/.config/inso-dumper/config.toml` (never in repo); secrets via env override.
- No shell/eval of API data; log redacts tokens.

---

## Open questions (resolved during the Aug 2026 spikes unless noted)

1. ~~Auth flow~~ → form POST + `PHPSESSID` HttpOnly cookie, no CSRF, no 2FA (api-notes § Auth).
2. ~~API shape~~ → hybrid: HTML shell + JSON `system-api` endpoints for posts/conversations/drive.
3. ~~Per-child vs per-group announcements~~ → per-child filtering works via `?child=`; messages
   are account-level despite accepting the param.
4. ~~Attachment URLs~~ → CloudFront-signed (`Expires`/`Signature`/`Key-Pair-Id`), ~hours; no
   cookies needed; download immediately, never persist.
5. ~~Message pagination~~ → 30/page via `startingIndex`; conversation list pages via `loadOlder=1`.
   Comment bodies are not exposed by the API (counts only) — out of scope.
6. ~~2FA/captcha~~ → none encountered (still deferred — see api-notes § Open Questions).
7. **Still open**: real `PHPSESSID` lifetime; drive file/download URL shapes (empty drive);
   message `attachments.other` shape.

---

## Milestones (status as of Aug 2026)

- **M0 — API discovery** ✅ (plus two follow-up spikes: timeline, conversations/drive).
- **M1 — Auth + announcements with photo sets** ✅ (PRs #3/#4; video shape #6, markup drift #5).
- **M2 — Multi-child layout + file-level dedup (`_common`)** ✅.
- **M3 — Messages + documents tabs** 🔄 (messages spec approved; documents gated on a real
  drive capture).
- **M4 — Incremental sync, verify, galleries/indexes** 🔄 (post incremental shipped; indexes
  pending).
- **M5 — Polish**: docs, packaging (`uv tool install` from repo / PyPI), edge cases.
- **M6 — Settlements ("Rozliczenia") read-only dump** ⬜ (own spike + spec; HTML + invoice PDFs).
