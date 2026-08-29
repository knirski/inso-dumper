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
3. Store content **per child**, with automatic **deduplication into a shared `_common/` area**
   (the same announcement/photo is usually broadcast to multiple children/groups).
4. Support **multiple children** in the same (or different) institution(s) from one account.
5. Be **incremental**: first run = full dump; later runs download only what's new.
6. Be resilient: resumable downloads, retries, rate-limited, idempotent re-runs.

## Non-goals

- No write access to Inso (never send messages, never mark-as-read unless unavoidable, never
  interact with billing).
- No multi-account orchestration UI (one parent account per run is enough; CLI flag for a second).
- No cloud upload / sync destination (local filesystem only; user can rsync/restic the output).

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

### F2. Content discovery (per child)
For each child linked to the account:
- Announcements list: id, title, body, author, publish datetime, target group(s), attachments
  (photos, videos, documents), comment visibility.
- Messages: conversation list + full message history + attachments.
- Documents/files tab: all downloadable files.

### F3. Storage layout
```
dump/
├── _common/                          # shared, deduplicated content
│   ├── blobs/<sha256[:2]>/<sha256>.<ext>   # content-addressed file store
│   └── announcements/<announcement-id>/    # announcements visible to >1 child
│       ├── meta.json
│       └── media/... (hardlinks into blobs/)
├── children/
│   ├── <child-slug>/                 # e.g. 2021-anna-k
│   │   ├── announcements/
│   │   │   └── 2025/
│   │   │       ├── 2025-09-15_dzien-kolorowy/
│   │   │       │   ├── meta.json           # full post metadata + source URLs
│   │   │       │   ├── index.html          # simple gallery
│   │   │       │   ├── photos/001.jpg ...  # ordered
│   │   │       │   ├── videos/ and files/
│   │   │       └── 2025-09-08_obejscie/
│   │   ├── messages/
│   │   │   ├── conversations.json
│   │   │   └── attachments/<conversation-id>/...
│   │   └── documents/
│   ├── _index.json                   # machine-readable dump index
│   └── index.html                    # human browseable index (by date, by child)
└── state/
    └── dump.db (sqlite)              # sync state: seen ids, checksums, mappings
```

(The dump index files `_index.json` / `index.html` live at `children/` level; F7 calls this the
"top-level" browseable index.)

Naming rules:
- Event directory: `YYYY-MM-DD_<slug-of-title>`; slug transliterated to ASCII, lowercased,
  length-capped; disambiguated with `-2` suffix if collision on the same day.
- If an announcement has no meaningful title → `YYYY-MM-DD_ogloszenie`.
- Photos ordered by their in-post order → `001.jpg`, `002.jpg` (extensions preserved).

### F4. Deduplication rules
1. **File-level**: blob stored once by SHA-256 in `_common/blobs/`; per-child files are
   **hardlinks** (same volume) with symlink fallback; metadata records the checksum.
2. **Announcement-level**: same announcement id delivered to 2+ children → stored once in
   `_common/announcements/<id>/`; children's `meta.json` reference it (plus hardlinked media).
3. If both children are gone from an institution / for future portability, hardlinks may be
   resolved to copies via `inso-dumper materialize` (optional command).

### F5. Incremental sync
- SQLite state DB: item ids (announcement, message, file), checksums, mtimes, attachment URLs.
- `sync` fetches lists (paginated) and downloads only unseen/changed items; list pages are walked
  until a known id threshold or end.
- `--full` forces re-check of everything (re-verify checksums, pick up edited posts).

### F6. CLI
All commands are run through uv: `uv run inso-dumper …`.
```
inso-dumper login            # interactive: validate credentials, save session
inso-dumper sync             # incremental dump (default)
inso-dumper sync --full      # re-verify everything
inso-dumper children         # list children + slugs
inso-dumper verify           # recompute checksums, report missing/corrupt blobs
inso-dumper materialize      # replace hardlinks/symlinks with real copies (portability)
```
Options: `--config`, `--dry-run`, `--limit <n>` (testing), `--child <slug>`, `--only announcements|messages|documents`.

### F7. Human-friendly output
- Per-event `index.html` photo gallery (no JS deps, works offline from `file://`).
- Top-level `index.html` + `_index.json`: everything by date/child/event name, so
  "find photos from Dzień Kolorowy 2025" is a filename/search away.

---

## Non-functional requirements

- Python ≥3.11, project managed with **uv** (`uv` owns venv, deps, and every invocation — all
  scripts/commands run via `uv run`); deps: `httpx`, `typer`, `rich`, `pydantic`
  (models for API payloads), `sqlite3` (stdlib).
- Rate limit: default ~2–4 req/s, jittered; per-file retry ×3 with backoff.
- Crash-safe: download to temp file, checksum, atomic rename; safe to Ctrl-C and re-run.
- Config: `~/.config/inso-dumper/config.toml` (never in repo); secrets via env override.
- No shell/eval of API data; log redacts tokens.

---

## Open questions (to resolve in Phase 0 with agent-browser)

1. Exact auth flow of app.inso.pl (form post? session cookie? CSRF? JWT in localStorage?) and its
   expiry behavior.
2. API shape: are announcements/messages/files JSON endpoints (SPA) or server-rendered HTML?
3. Does the parent account see announcements **per child** or per group? (affects F4.2 mapping)
4. Attachment URLs (file.inso.pl): signed/expiring? do they require cookies?
5. Message history pagination limits; comments on announcements — are they readable by parents?
6. Any 2FA / captcha on login?

---

## Milestones

- **M0 — API discovery** (agent-browser, HAR): login flow + endpoint map. Output: `docs/api-notes.md`.
- **M1 — Auth + announcements with photo sets** end-to-end for one child.
- **M2 — Multi-child layout + file-level dedup (`_common`)**.
- **M3 — Messages + documents tabs**.
- **M4 — Incremental sync, verify, galleries/indexes**.
- **M5 — Polish**: docs, packaging (`uv tool install` from repo / PyPI), edge cases (renamed events, deleted posts).
