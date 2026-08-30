# Messages and documents: communicator history, drive files, incremental re-sync

## Problem

The foundation and announcements specs (#1–#4) ship a dumper that
backs up announcements and galleries per child. Two PRD F2 surfaces
remain undumped, and both are at risk when the account closes:

- **Messages (komunikator)** — conversation list + full message history
  + attachments. There is no official export.
- **Documents/files (Dysk tab)** — all downloadable files shared with
  the parent.

This spec adds both to `inso-dumper sync` (PRD M3), reusing the
established pipeline: JSON API clients with fail-loud payload modeling,
the content-hash dedup store, the SQLite manifest, and the sync
orchestrator.

A discovery spike (Aug 29, 2026; captures under
`docs/api-notes-data/`) verified the endpoint surfaces against a real
session; see [Findings](#findings-the-spike-contract). AGENTS.md
forbids inventing API details — every model in this spec traces to a
captured payload.

## Scope

- **In scope:**
  - `GET /system-api/conversations[?category=main&loadOlder=1]` client
    with page iteration.
  - `GET /system-api/conversations/{id}?messages=1[&startingIndex=N]`
    client; full-history walk at 30 messages per page.
  - `GET /drive/items[/{directoryId}]` client with recursive DFS walk.
  - Pydantic models mirroring the captured payloads
    (`extra="forbid"`, fail loud on drift).
  - Storage: `dump/messages/<dir>/` per conversation (account-level —
    see Trade-offs) and `dump/documents/` mirroring the drive tree;
    attachments/files deduplicated into the shared `_common/` store.
  - Manifest schema v3: `conversations` table (id, dir_name,
    last_update, message_count) enabling **incremental tail fetches**.
  - CLI: `--category` extended to
    `{announcements,galleries,messages,documents,all}` (`all` replaces
    `both`); summary line reports per-type counts.
  - Idempotent re-runs: unchanged conversations skipped (by
    `last_update`), changed conversations tail-fetched, unchanged
    drive bytes no-op in the dedup store.
  - Tests on recorded JSON fixtures (gitignored corpus).
- **Out of scope:**
  - **Settlements ("Rozliczenia")** — user-requested, absent from PRD
    F2; server-rendered HTML + invoice PDFs, i.e. a different transport
    style. Own follow-up spec (with a PRD F2 update).
  - `verify` / `materialize` / dump indexes (PRD F6, F7, M5).
  - Message templates, notifications, calendar, diary — not dumps.
  - Sending messages, uploading attachments, marking anything read —
    forbidden (see Constraints).
  - Comment threads, message search.

## User Stories

- **US-1 (must): dump all conversations.** As a parent, I want `sync`
  to fetch every conversation with its full message history, so that
  nothing in the communicator is lost if the account closes.
  - Given a valid session, when the messages category runs, then every
    conversation is written to `dump/messages/<dir>/` with
    `conversation.json` and `messages.json` (complete history).
  - Given a multi-page history, when walked, then no message is
    duplicated or skipped across page boundaries (dedupe by message id
    regardless of boundary semantics).
- **US-2 (must): dedup attachments.** As a parent, I want every message
  attachment's bytes stored once, so shared media survive and don't
  waste disk.
  - Given a message with attachments `{media: [{name, url{thumb, full},
    isVideo}], other: [...]}`, when dumped, then each `media` item's
    bytes are stored under `_common/<kind>/<hash[:2]>/<hash>.<ext>` and
    linked into `attachments/<message-id>/<n>.<ext>`.
  - Kind mapping: `is_video` → `videos/`; image extension → `photos/`;
    else `attachments/` (once the `other` shape is captured — Open
    Question 2).
- **US-3 (must): incremental re-runs.** As a parent, I want re-runs to
  skip unchanged conversations and fetch only new messages.
  - Given a conversation whose `last_update` is unchanged, when
    re-running, then it is skipped entirely.
  - Given a conversation with new messages, when re-running, then only
    the tail is fetched (`startingIndex = message_count`),
    `messages.json` is extended atomically, and the manifest row is
    updated.
- **US-4 (must): dump the drive tree.** As a parent, I want every file
  from the Dysk tab, with folders preserved.
  - Given a valid session, when the documents category runs, then
    `/drive/items` is walked depth-first, every file's bytes are stored
    in `_common/`, and the tree is mirrored under `dump/documents/`.
  - ⚠️ The file-download URL shape is unverified (empty drive at spike
    time) — gated spike task before documents release (Open Question 1).
- **US-5 (must): read-only guarantee.** As a parent, I want sync to
  never change my account state.
  - Given any run, then only `GET`s are issued. The dumper must never
    call `POST /system-api/conversations/read-all-unread`
    (mark-as-read), `POST /system-api/messages/attachment` (upload), or
    any `/drive/directory*` / `/drive/file*` write endpoint. A test
    asserts these paths appear in no request.
- **US-6 (should): one CLI surface.** `--category
  {announcements,galleries,messages,documents,all}`, default `all`;
  per-type counts in the summary line.

## Constraints

- All Inso traffic is **read-only GETs only**; the forbidden write
  endpoints above are listed in the spec so tests can pin them.
- uv-only tooling; Python floor 3.14; PEP 695, `@dataclass(frozen=True,
  slots=True)`, `StrEnum`, `Result[ValueT, ErrorT]`, `match`/`case` +
  `assert_never`, pydantic v2 `extra="forbid"`, basedpyright
  `recommended`, ruff.
- Functional core / imperative shell (AGENTS.md): parsers are pure
  (bytes in, models out); shells own httpx/SQLite/disk and convert
  errors to `CliError`; async iterators yield `CliResult` items so
  consumers short-circuit without raising.
- Signed attachment URLs (host `file.inso.pl`) are short-lived;
  downloaded bytes are the only durable artifact (same policy as
  announcements — never rely on persisted URLs; `conversation.json` /
  `messages.json` keep the URLs verbatim as inert provenance).
- `dump/` is local-only, gitignored, mode `0700`; files `0600` via the
  existing writer invariants.
- Spike captures are local-only (gitignored); tests skip when the
  corpus is absent; CI does not run them.
- No new exit codes: drift → `PLATFORM_CHANGED` (6), transport →
  `HTTP` (5), disk/manifest → `INTERNAL` (99), stale manifest schema →
  `CONFIG` (2).

## Context

### What exists today

- Foundation (#1): auth/session, config, children scrape, `CliError`
  machinery, `TokenRedactingFilter`.
- Announcements (#2–#4): timeline client, `models/timeline.py` with
  discriminated media union, `dump/` package (layout, writer, manifest
  v2, sync), `sync` CLI.
- Real-traffic fixes (#5, #6, #7): redirect-following shell machinery
  (`http/redirects.py`), switcher parser v2, verified video shape,
  status-bearing media errors, `ext_from` URL fallback.

### Findings (the spike contract)

Captures: `conversations-sample.json`,
`conversation-detail-sample.json`, `drive-items-sample.json`.

```
GET /system-api/conversations?category=main[&loadOlder=1]
  → {categories: [], category: "main", conversations: [...],
     templates: [], unreadCount: int}

GET /system-api/conversations/{id}?messages=1[&startingIndex=N]
  → [Message, ...]                    # bare list; 30 per page

GET /drive/items[/{directoryId}]
  → {breadcrumbs: [{name, id}], directories: [...],
     directory: ..., files: [...]}
```

Observed shapes (verbatim field names; camelCase → snake_case via
pydantic aliases as in `models/timeline.py`):

```json
// conversation (list item)
{"id": "5b8789d6-…", "lastUpdate": 1787814351,
 "recipient": {"name": "Żółta", "description": "Wychowawcy w grupie",
               "type": "teachers", "prefix": "Grupa:"},
 "read": true, "branch": "ee94092c-…", "participantsNames": [],
 "excerpt": "…"}

// message (list item of ?messages=1)
{"id": "4b6763e8-…", "message": "Dziękuję", "sendDate": "czwartek,  9:05",
 "sendTimestamp": 1787814351,
 "sender": {"type": "worker", "name": "…", "initials": "…", "avatar": null},
 "incoming": true, "attachments": {"media": [], "other": []},
 "main": false, "isRemoved": false, "canRemove": false}

// message media attachment
{"name": "IMG_8171.jpeg",
 "url": {"thumb": "https://file.inso.pl/…/thumb.jpg?Expires=…",
         "full":  "https://file.inso.pl/…/full.jpg?Expires=…"},
 "isVideo": false}
```

Verified behaviours:

- `?child=` on `/system-api/conversations` is **accepted but ignored** —
  conversations are account-level group-teacher threads
  (`recipient.type: "teachers"`), not child-scoped.
- `startingIndex=30` returns the next 30-message page (verified).
- The drive of the spike account is empty; only `breadcrumbs` is
  verified.

### Gotchas, assumptions, technical debt

- **Conversations mutate; posts don't.** Re-sync = compare
  `last_update`, tail-fetch via `startingIndex = message_count`.
  Limitation: edits/removals of *old* messages (`isRemoved`) are not
  caught by a tail fetch — accepted; `--force` re-fetches from scratch
  (stored `isRemoved` flags keep the on-dump record honest).
- **`dir_name` is assigned once** (`<lastUpdate-date>-<slugified
  recipient name>`) and stored in the manifest; the date prefix does
  not chase later activity. Same rule as posts (#4 review fix).
- **Drive models are provisional** beyond `breadcrumbs`; first
  non-empty drive either parses or fails loud → fix from capture
  (videos precedent, #6).
- **`attachments.other` shape unverified** (none in captures); parsed
  permissively and dumped verbatim into `conversation.json`-adjacent
  data until captured.
- **PRD F3 layout update needed**: messages live under
  `dump/messages/`, not `children/<slug>/messages/` (account-level API
  reality); the PRD note should be amended when settlements spec lands.
- Duplicated messages across pages are harmless: the walker dedupes by
  message id, so boundary semantics (page size 30 assumption) are
  self-correcting.

## Architecture

### Component structure

```
inso_dumper/
├── cli.py                     # modify: --category choices, summary builder
├── http/
│   ├── client.py              # no change
│   ├── redirects.py           # no change
│   ├── timeline.py            # no change
│   ├── messages.py            # NEW: list_conversations(), fetch_messages() shells + pure parsers
│   └── drive.py               # NEW: walk_drive() shell + pure parser
├── models/
│   ├── messages.py            # NEW: ConversationListPage, Conversation, Message, …
│   └── drive.py               # NEW: DriveListing, DriveBreadcrumb (provisional)
└── dump/
    ├── layout.py, writer.py   # no change (dedup targets, atomic writes, links)
    ├── manifest.py            # modify: schema v3 + conversations table
    ├── messages.py            # NEW: conversation dir writer (json, append, links)
    └── sync.py                # modify: category dispatch → sync_messages, sync_documents
```

Layer rules:

- `http/messages.py` / `http/drive.py` parsers are **pure** (bytes in,
  `Result[list[…], CliError]` out; unknown key →
  `Err(PLATFORM_CHANGED)`). Shells own the `HttpClient`, yield
  `CliResult` items, end iteration on `Err` (house pattern).
- `dump/messages.py` is the disk boundary for conversations: creates
  dirs via `writer.ensure_private_dir`, appends `messages.json`
  atomically (read → extend → temp write → `os.replace`), links
  attachments via `writer.link_media_to_post`.
- `dump/sync.py` orchestrates and owns the manifest; the CLI only
  dispatches and prints.
- **Account-level manifest and execution**: conversations are
  account-level data, so their manifest lives at
  `dump/messages/.manifest.sqlite` (own SQLite file, same schema-gate
  rules) — never inside a per-child manifest. `sync_messages` and
  `sync_documents` run **once per invocation** regardless of how many
  children the account has; the `<child-slug>` argument only selects
  the announcements/galleries categories. The summary line reports
  account-level segments without attributing them to a child:
  `…, 3 conversations (41 new messages, 6 attachments), 12 documents
  for the account in 95.3s.` (the `for <child-slug>` tail covers only
  the per-child announcements/galleries segments).

### Sync flow (messages category)

```
for conv_page in list_conversations():     # loadOlder walk, empty/no-progress stops
    for conversation in conv_page:
        recorded = manifest.recorded_conversation(id)
        if recorded and recorded.last_update == conversation.last_update:
            skip
        starting = recorded.message_count if recorded else 0
        for msg_page in fetch_messages(id, starting):
            new_messages.extend(msg_page)            # dedupe by message id
        dir = writer.resolve_conversation_dir(...)  # assigned once, stored
        write conversation.json + append messages.json
        for attachment in new messages: download → dedup → link
        manifest.upsert_conversation(id, dir.name, last_update, count)
```

Documents: DFS from `/drive/items`; per file: download → dedup → place
under `dump/documents/<path>` as a link into `_common/`. Every drive
directory name is sanitized before joining the relative path — the same
`_slugify` rule used for post/recipient names — so a platform-supplied
name containing `/`, `\`, or `..` can never escape
`dump/documents/`; empty names after slugification fall back to
`directory`.

## API Design

### CLI surface

```
inso-dumper sync <child-slug>
                  [--category {announcements,galleries,messages,documents,all}]  # default: all
                  [--dump-root PATH]
                  [--force <slug> ...]        # repeatable
                  [--verbose|-v]
```

`--force` semantics per category: post slug (announcements/galleries,
unchanged), conversation id or dir name (messages → drop the manifest
row and re-fetch history from 0), `all` (documents re-walk; existing
bytes no-op in the dedup store).

Summary line (segments only for categories that ran):

```
Synced 12 announcements (3 skipped, 34 photos, 2 videos, 1 attachments), 2 galleries, 3 conversations (41 new messages, 6 attachments), 12 documents for franek in 95.3s.
```

### Error handling

No new kinds. Unknown payload keys → `PLATFORM_CHANGED`; transport/non-
200 → `HTTP` (`messages_status_<n>` subjects where diagnostics
matter); disk/manifest → `INTERNAL`; stale manifest `user_version` →
`CONFIG`. The forbidden-endpoints test (US-5) asserts no request path
ever matches the write endpoints.

## Data Model

### Manifest schema v3 (`PRAGMA user_version = 3`)

```sql
CREATE TABLE IF NOT EXISTS posts (…);            -- unchanged (v2)

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,   -- platform UUID; sole dedup key
    dir_name        TEXT NOT NULL,      -- assigned once, stored
    last_update     INTEGER NOT NULL,   -- platform lastUpdate (unix)
    message_count   INTEGER NOT NULL,   -- ⇒ startingIndex for tail fetch
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);
```

Drive items: **no table** (no timestamps; byte-dedup is idempotent;
avoids untrustworthy state pre-verification).

### Storage layout

```
dump/
├── _common/                                  # shared, content-addressed
│   ├── photos/<hash[:2]>/<hash>.<ext>        # + videos/, attachments/ (unchanged)
├── messages/
│   └── 2026-08-27-zolta/
│       ├── conversation.json                 # list payload verbatim (+dumped_at)
│       ├── messages.json                     # full history, append-only
│       └── attachments/<message-id>/1.jpg → ../../../_common/photos/…
└── documents/
    └── <mirrored drive path>/… -> link into _common/
```

`messages.json` is the source of truth for the history; re-runs read
the expected count from the manifest. **Truncation fail-safe**: before
appending a tail, the writer compares the on-disk message count with
the manifest's `message_count`; on mismatch it returns
`Err(CliError(INTERNAL, subject='messages_history_truncated'))` and the
run stops — the user re-dumps the conversation with `--force`
(full refetch from 0), which rebuilds the file. The tail is never
appended onto a file that disagrees with the manifest.

## Trade-offs

- **Account-level messages storage** (per PRD-per-child sketch): the
  API is account-level; group-name→child mapping relies on mutable
  display strings and duplicates multi-child groups. PRD F3 to be
  amended.
- **Tail fetch over full re-fetch**: cheaper, but edits of old messages
  are invisible to tail fetches; `--force` covers verification.
- **One `messages.json` per conversation** over per-message files:
  append-only single source of truth; fewer inodes.
- **No drive manifest table**: no timestamps, idempotent byte dedup;
  avoids state that cannot be trusted before the download URL is
  verified.
- **Shared `_common/` store** over per-conversation copies: one
  content-addressed store dedups across conversations and categories.
- **`all` replaces `both`**: pre-release rename; `both` doesn't compose
  with four categories.
- **Settlements split out**: different transport (HTML + invoice PDFs)
  and absent from PRD F2; own spec keeps this one focused.

## Open Questions

1. **Drive `directories`/`files` shapes are provisional** (only
   `breadcrumbs` verified — empty drive). Gated spike task before
   documents release: capture a non-empty drive, correct the models.
   Until then a non-empty drive fails loud.
2. **`attachments.other` shape unverified** (none in captures); dumped
   verbatim; kind mapping deferred until captured.
3. **`loadOlder` termination semantics** — assume empty page = end;
   verify on an account with deeper conversation history.
4. **Messages page size 30** — boundary semantics self-correct via
   message-id dedupe; verify on a long conversation.
5. **Settlements follow-up spec** — HTML scrape + invoice PDFs +
   PRD F2 update; explicitly deferred.
6. **`read`/`unreadCount`** — recorded verbatim, never acted on.
