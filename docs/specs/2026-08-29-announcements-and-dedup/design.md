# Announcements and dedup: timeline API, photo sets, storage layout

## Problem

The foundation spec
(`docs/specs/2026-08-29-foundation-auth-cli/design.md`) ships a CLI that
can log in and list children but does **not** dump any content. The
PRD's first content checkpoint (M1 finish) is to dump every
announcement and its photo set for a chosen child, with **deduplication
across children** so two children in the same group do not duplicate
identical content.

A fresh discovery spike (Aug 29, 2026; data saved under
`docs/api-notes-data/`) discovered that the platform exposes a private
JSON API at `/system-api/timeline/posts?page=N&categoryId={id}&child={uuid}`,
which **supersedes the HTML-scrape branch the foundation spec took for
the children list**. This spec adopts the JSON API for the timeline
content type, keeps the HTML scrape only for the children list (it works
and changing it is out of scope), and designs the storage layout, the
dedup rules, and the CLI surface for the announcements sync.

The spec scope is **announcements only** (PRD F1 + F2 + F3 partial):
text + inline photos + videos + file attachments, with dedup. Messages
and documents are deferred to a follow-up spec (the PRD's
`messages-and-documents`).

## Scope

- **In scope:**
  - `GET /system-api/timeline/posts?page=N&categoryId={id}&child={uuid}`
    client with `categoryId ∈ {2, 3}` (Ogłoszenia, Galerie zdjęć);
    one shell function per category, each a paginated iterator that
    yields `list[Post]` and stops when the server returns an empty
    page.
  - Pydantic model `Post` mirroring the API's item shape, with
    `media.photos[]`, `media.videos[]`, and `media.attachments[]`
    normalised to typed `Photo` / `Video` / `Attachment` records
    (each carrying `name`, `src.thumb`, `src.full`, plus a stable
    content hash to be computed client-side at dump time).
  - Storage layout under `dump/<child-slug>/announcements/<YYYY-MM-DD>-<post-slug>/`
    with `_common/` for shared media (PRD F3 partial).
  - Dedup rules: photos, videos, and attachments dedup by **content
    hash** (SHA-256 of the downloaded bytes), stored once in
    `dump/_common/photos/<sha256[:2]>/<sha256>.<ext>` (videos under
    `_common/videos/`, attachments under `_common/attachments/`).
    The dump records a symlink (or relative path) into that shared
    store. Text content is **not** deduped across announcements
    (it's a small fraction of total bytes and cross-post textual
    reuse is not the user's concern).
  - `inso-dumper sync <child-slug> [--category announcements|galleries|both]`
    — top-level command that walks the chosen categories, downloads
    media, writes the storage layout.
  - Idempotent re-runs: a second `sync` of the same child must not
    re-download anything and must update only the manifest with new
    entries. A `--force` flag re-downloads a single post by slug.
  - The `HttpClient` Protocol stays as-is; a new module
    `http/timeline.py` plugs into it.
  - Tests on recorded JSON fixtures (the spike's
    `docs/api-notes-data/*.json` is the test corpus).
- **Out of scope:**
  - Messages, documents, polls, menus (PRD F2, F5, F7, F8) — next spec.
  - `verify` / `materialize` / SQLite state DB (PRD F6, M3) — later spec.
  - Indexes, gallery HTML (PRD F4, F7) — later spec.
  - Multi-account orchestration (PRD Non-goals).
  - Switching the children list from HTML scrape to JSON. The
    children endpoint is not part of the spike; defer.
  - 2FA, refresh tokens, encrypted session (PRD Open Questions 7, 8
    from the foundation spec) — unchanged, deferred.

## Constraints

- All Python invocations go through `uv run …` (AGENTS.md).
- Python floor: 3.14; PEP 695 generics, `@dataclass(frozen=True,
  slots=True)`, `StrEnum`, `Result[ValueT, ErrorT]`, `match`/`case` +
  `assert_never` for closed unions, pydantic v2 with `extra="forbid"`,
  basedpyright `recommended`.
- All Inso traffic is read-only **GETs only**. The platform also
  exposes `PUT /system-api/timeline/posts/{uuid}/view` (mark-as-read)
  — the dumper **must not call it**. (AGENTS.md: "Work is read-only
  with respect to the Inso platform".)
- Signed media URLs (`https://file.inso.pl/.../full.jpg?Expires=…
  &Signature=…&Key-Pair-Id=…`) are short-lived (~hours). The dumper
  downloads bytes immediately after fetching the post; the URL is
  not persisted to disk. The dump must be self-contained — re-running
  the dumper offline must not require re-fetching from the platform.
- `dump/` is local-only, never committed, mode `0700` (existing rule
  in AGENTS.md). Photo files are `0600` (children's personal data).
- Slug derivation rule must remain stable across runs. The foundation
  spec's `Child.slug` is the input; per-post slugs are derived from
  the post title with the same `_SLUG_RE` (lowercased, ASCII-folded,
  non-alnum collapsed to `-`).
- Test corpus is the spike JSON in `docs/api-notes-data/` (gitignored
  — see `.gitignore` `docs/api-notes-data/`). Tests skip when the
  corpus is absent; CI is **not** expected to run them. Local-only
  spike-driven tests, per the foundation spec's pattern.
- Functional core / imperative shell split (AGENTS.md): pure
  `parse_timeline_page(bytes) -> Result[list[Post], CliError]`,
  `media_hash(bytes) -> str`, `derive_post_slug(title) -> str`,
  `dedup_target(hash, ext) -> Path` live in `http/timeline.py`
  (parsers) and `dump/layout.py` (pure layout). The shell modules
  own httpx, file writes, and the SQLite manifest.

## Context

### What exists today

- `inso_dumper` package (foundation spec, merged).
  - `Config` (TOML + env), `Session` (PHPSESSID + user_uuid), `Child`
    (with `slug`).
  - `HttpClient` Protocol + `HttpxClient` async context manager.
  - `HttpxClient` rate-limited at 3 req/s.
  - `TokenRedactingFilter` on the root logger.
  - Closed `CliError` union: CONFIG=2, AUTH=3, SESSION_EXPIRED=4,
    HTTP=5, PLATFORM_CHANGED=6, INTERNAL=99.
- `docs/api-notes.md` — spike notes from the foundation spec.
- `docs/api-notes-data/` (gitignored) — fresh JSON samples for three
  category pages of one child:
  - `timeline-posts-page1-category0.json` — 10 items, 347 photos, 9 attachments.
  - `timeline-posts-page1-category2.json` — 10 items, 0 photos, 9 attachments (announcements).
  - `timeline-posts-page1-category3.json` — 10 items, 973 photos, 0 attachments (galleries).

### How it works end-to-end (target)

```
inso-dumper sync franek-kowalski
   → config.load()                                  # base_url, rate limit
   → session.load()                                 # PHPSESSID; SESSION_EXPIRED → 4
   → list children (existing scrape); resolve slug → child_uuid
   → for category in (2, 3):                        # Ogłoszenia, Galerie
        for page in 1..N:                           # while last page is non-empty
            GET /system-api/timeline/posts?page=N&categoryId={c}&child={uuid}
            parse_timeline_page(bytes) -> list[Post]
            for post in posts:
                post_slug = derive_post_slug(post.title)
                target = dump/franek-kowalski/announcements/{date}-{slug}/
                write post.json + post.html
                for photo in post.media.photos:
                    bytes = GET photo.src.full
                    h = sha256(bytes)
                    path = dump/_common/photos/{h[:2]}/{h}.{ext}
                    if not path.exists(): write atomically
                    target/photos/{n}.{ext} -> symlink(path)
                for video in post.media.videos:
                    same as photos, into _common/videos/ and target/videos/
                for att in post.media.attachments:
                    same as photos, into target/files/
   → upsert each post into the manifest DB (.manifest.sqlite under
     dump/franek-kowalski/announcements/)
   → print "Synced N posts (S skipped, M photos, V videos, K attachments) for franek-kowalski"
   → exit 0
```

A re-run reads the manifest, fetches only new posts, skips posts
already on disk. A `--force <post-slug>` flag re-downloads media for
the named post.

### Open external dependencies (resolved by the spike, captured in this spec)

The Aug 29 spike (captured data in `docs/api-notes-data/`) confirmed
the timeline API surface. See [Findings](#findings) for the full
schema. The spike is the input this spec is built against; future
specs may add endpoints (e.g. `PUT /view` is intentionally
**not** added).

### Existing patterns and conventions

- Reuse the foundation's `HttpClient` Protocol, `HttpxClient`,
  `Result` machinery, `CliError` union, and `TokenRedactingFilter`.
- The new `Post` model follows the same pydantic style as `Child` /
  `Session` (`extra="forbid"`, frozen, slots).
- The new `inso-dumper sync` command is wired by `cli.py` the same
  way `login` / `children` are — no business logic in the CLI.
- Dedup: PRD F3 specifies a `_common/` area; this spec pins its
  layout and the rules for placing files in it.
- No new heavy deps. `httpx` (already in), `pydantic` (already in),
  `typer` (already in), `rich` (already in), plus the stdlib
  `hashlib` and `sqlite3` for the manifest.

### Gotchas, assumptions, technical debt

- **Resolved by the spike (see [Findings](#findings)):** the
  platform exposes a private JSON API at
  `/system-api/timeline/posts`. The PRD's plan Phase 0 "JSON API vs
  HTML scrape" decision point is **resolved in favour of JSON API**
  for the timeline content type. The children list (foundation spec)
  still uses HTML scrape.
- **Assumption:** the JSON schema is stable. The spec treats the
  spike's three pages as the contract; any unknown key triggers
  `PLATFORM_CHANGED` (exit 6) so the dumper fails loud rather than
  silently dropping fields. (Foundation spec already has this exit
  code.)
- **Pagination stop condition:** the iterator walks `page=1, 2, …`
  and stops on the first response whose `items` array is empty
  (one extra request past the end; the observed page size is 10,
  but the dumper does not rely on the exact size). A page that
  returns `waitingToProcess > 0` is retried with backoff.
- **Child UUID source:** `sync` resolves `<child-slug>` to a UUID
  via the children scrape — the UUID embedded in the panel-home
  sidebar link (`Child.child_id` from the foundation spec).
  **Assumption:** that UUID is what the timeline API's `child=`
  param expects. The spike only proved `child=` works with
  `visibleForChildren`-style UUIDs from recorded traffic; it did
  not prove the scrape-derived one is accepted. Before implementing
  P2, confirm with one recorded request (finding added to
  `docs/api-notes.md`); if the scrape-derived UUID is not accepted,
  the fallback is matching the child against the `visibleForChildren`
  UUIDs seen in timeline pages. The `child=` param is **never**
  `session.user_uuid` (that is the parent).
- **Slug collisions:** two posts with the same title on the same
  day disambiguate by appending `-<n>` where `<n>` is the
  collision count, in dump order. The manifest records the
  canonical slug the dumper used. An existing directory whose
  `post.json` names the **same** `post_id` is a partial dump from
  an interrupted run; it is re-dumped in place instead of
  receiving a new suffix (no orphaned `-1` directories).
- **Sticky / pinned posts:** the spike's `Przypięte ogłoszenia`
  section re-appears in API responses when `categoryId=0` (All).
  When filtering by `categoryId=2` (Ogłoszenia) the spike shows
  sticky posts **without** the sticky modifier. The spec treats
  sticky as a server-side concern; the dumper dumps every post
  the API returns and the manifest records `sticky: bool` for
  the user's reference.
- **HTML in `content`:** the API returns sanitised HTML (the same
  HTML the browser renders). The dumper writes both the raw
  `content` (as `post.html`) and a markdown conversion (as
  `post.md`) using a small, documented conversion. The
  conversion is best-effort: if it fails on an unknown element,
  the dumper falls back to writing only `post.html` and logs
  at INFO. (PRD M2 polish: replace with `html-to-markdown` if
  the simple converter proves insufficient.)
- **Videos:** the spike captured `videos: []` on every post — no
  video-bearing post was recorded, so the video item shape is
  **unverified**. The `Video` model mirrors the photo item (the
  closest captured analog) as a working assumption. A video-bearing
  post must be captured (spike task in plan.json P1) and the model
  confirmed before a video-dumping release. `extra="forbid"` makes a
  mismatched real shape fail loud with `PLATFORM_CHANGED` (exit 6)
  rather than silently dropping videos. Videos flow through the same
  download → hash → dedup → symlink path as photos, into
  `_common/videos/` and `post_target_dir/videos/`.
- **Attachments with non-image MIME:** the spike's `attachments[]`
  contains a PDF; the dedup path is content-hash + extension
  derived from `Content-Type` or the URL's path. Unknown types
  are saved with a sanitised filename and a `.bin` extension.
- **Session lifetime:** the foundation spec left this as
  `None` (deferred). This spec measures a real `PHPSESSID`
  expiry during the spike (the `INSO_APP_USER_LOGGED` cookie
  and the `Set-Cookie` expiry header both hint at lifetime).
  The dumper treats 1h as a working floor; if a sync call gets
  a 401/403 the dumper surfaces `SESSION_EXPIRED` (exit 4) and
  prints "run `inso-dumper login`". The spec does not add
  silent re-login in v1.
- **PRD F3 example drift:** the PRD shows
  `children/2021-anna-k/` with a year prefix. The foundation
  spec dropped the year. This spec follows the foundation —
  `children/<slug>/announcements/<date>-<slug>/`. A note in
  the PRD should be updated to match.

## Architecture

### Component structure (functional core / imperative shell)

```
inso_dumper/
├── cli.py                          # add `sync` subcommand
├── config.py                       # (no change)
├── errors.py                       # (no change; reuse CliError)
├── paths.py                        # (no change; reuse state-path resolution)
├── http/
│   ├── client.py                   # (no change; reuse HttpClient)
│   ├── auth.py                     # (no change)
│   ├── children.py                 # (no change; reused by sync to find child_uuid)
│   └── timeline.py                 # NEW: GET /system-api/timeline/posts + parse
├── models/
│   ├── ...
│   └── timeline.py                 # NEW: Post, Photo, Attachment pydantic models
├── dump/
│   ├── __init__.py
│   ├── layout.py                   # NEW: pure path derivation, content-hash, dedup
│   ├── writer.py                   # NEW: shell — atomic file write, symlink
│   ├── manifest.py                 # NEW: shell — SQLite read/write of sync state
│   └── sync.py                     # NEW: shell — orchestrates pages → posts → media → manifest
└── session/                        # (no change)
```

Layer rules:

- `http/timeline.py` parsers: `parse_timeline_page(bytes) -> Result[list[Post], CliError]`,
  `parse_post_detail(bytes) -> Result[Post, CliError]`. **Pure** —
  take bytes, return models. No IO.
- `http/timeline.py` shell: `list_posts(client, session, child_uuid, category_id) -> AsyncIterator[Post]`
  and `download_media(client, post) -> AsyncIterator[tuple[MediaItemKind, str, bytes]]`
  (media kind, original name, bytes). Both call into
  the existing `HttpClient` and convert network errors to `CliError(HTTP)`.
- `dump/layout.py` is **pure** — no IO. `derive_post_slug(title, date) -> str`,
  `dedup_target(hash, ext) -> Path`, `post_target_dir(dump_root, child_slug, post) -> Path`.
- `dump/writer.py` is the **shell** boundary for disk: atomic write
  with `os.replace`, `fsync`, `0o600`/`0o700` modes, symlink
  creation that falls back to copy when the filesystem rejects
  symlinks. Converts `OSError` to `CliError(INTERNAL)` with
  subject = the failing path.
- `dump/manifest.py` is the **shell** boundary for the SQLite state
  DB. One table: `posts(post_id PRIMARY KEY, post_slug, category_id,
  child_slug, first_seen_at, last_seen_at, media_count)`. Read on
  every sync to skip posts already dumped (by `post_id`); write
  after each successful post write.
- `dump/sync.py` is the **orchestrator** — it pulls everything
  together. It is the only module that calls the `HttpClient`,
  iterates pages, downloads media, writes the manifest. The CLI
  command is a thin wrapper that builds the `Config` and `Session`
  and calls `sync.run(config, session, child_slug)`.

### Domain model

```python
# models/timeline.py
from datetime import datetime
from enum import IntEnum
from pydantic import BaseModel, ConfigDict, HttpUrl, Field


class Category(IntEnum):
    """Closed mirror of the platform's data-categories enum."""
    ANNOUNCEMENTS = 2   # "Ogłoszenia"
    GALLERIES     = 3   # "Galerie zdjęć"

    @property
    def api_id(self) -> int:
        return int(self)


class PhotoSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thumb: HttpUrl
    full: HttpUrl


class Photo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str                                  # original filename, e.g. "IMG_2679.jpeg"
    src: PhotoSource

    @property
    def ext(self) -> str:
        # .jpg, .jpeg, .png, .webp, .heic — derived from `name`, fallback to
        # the URL path's extension, fallback to "bin".
        ...


class Video(BaseModel):
    """Working assumption — see the videos gotcha. No video-bearing
    post was captured by the spike (`videos` was always `[]`); the
    shape mirrors the photo item, the closest captured analog. A
    video-bearing post must be captured and this model confirmed
    before a video-dumping release (spike task in plan.json)."""
    model_config = ConfigDict(extra="forbid")
    name: str
    src: PhotoSource

    @property
    def ext(self) -> str:
        # .mp4, .mov, .webm — derived from `name`, fallback to
        # the URL path's extension, fallback to "bin".
        ...


class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    url: HttpUrl

    @property
    def ext(self) -> str:
        ...


class Media(BaseModel):
    model_config = ConfigDict(extra="forbid")
    photos: list[Photo] = Field(default_factory=list)
    videos: list[Video] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)


class Post(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str                              # UUID; primary key
    title: str
    content: str                         # sanitised HTML
    media: Media
    created_at: datetime                 # parsed from createdAt (unix seconds)
    created_at_text: str                 # e.g. "czwartek, 8:17"; preserved verbatim
    visible_for: list[str]               # group names; informational
    visible_for_children: list[str]      # child UUIDs; informational
    sticky: bool
    archived: bool
    author: str | None
    likes: int
    comments: int
    poll: object | None                  # nullable; ignored in v1
```

The `Poll` shape (the only nullable unknown) is intentionally typed
as `object | None` and the parser discards it. If a future spec
needs polls, it adds a `Poll` model and the parser enforces it.

```python
# dump/layout.py (pure)
def post_target_dir(*, dump_root: Path, child_slug: str, post: Post) -> Path:
    date = post.created_at.date().isoformat()              # "2026-08-27"
    base = f"{date}-{_slugify(post.title)}"
    # Collision disambiguation (-1, -2, ...) and partial-run recovery
    # both need existence checks (IO), so they are the writer's job.
    return dump_root / child_slug / "announcements" / base

def dedup_target(*, dump_root: Path, hash_hex: str, ext: str, kind: str) -> Path:
    # kind is "photos", "videos", or "attachments" — the _common/ subtree.
    return dump_root / "_common" / kind / hash_hex[:2] / f"{hash_hex}.{ext}"
```

```python
# dump/manifest.py (shell)
# SQLite table:
#   CREATE TABLE posts (
#       post_id         TEXT PRIMARY KEY,        -- platform UUID; the sole dedup key
#       post_slug       TEXT NOT NULL,           -- relative to dump_root
#       category_id     INTEGER NOT NULL,
#       child_slug      TEXT NOT NULL,
#       first_seen_at   TEXT NOT NULL,           -- ISO-8601 UTC
#       last_seen_at    TEXT NOT NULL,           -- ISO-8601 UTC
#       media_count     INTEGER NOT NULL
#   );
```

The manifest DB lives at `dump/<child-slug>/announcements/.manifest.sqlite`.
One DB per child keeps each dump self-contained; the absence of a
DB at the start of a run is a no-op (the dumper just dumps
everything).

### Where business logic lives, where IO lives

| Concern                                | Location                      | Why |
| ---                                    | ---                           | --- |
| Slug derivation (post)                 | `dump/layout.py`              | Pure, no IO |
| Dedup target path                      | `dump/layout.py`              | Pure, no IO |
| `Post` model                           | `models/timeline.py`          | Pydantic, extra=forbid |
| Parse one API page → `list[Post]`      | `http/timeline.py` (parser)   | Pure, bytes in, models out |
| Iterate pages until exhausted          | `http/timeline.py` (shell)    | Owns the `HttpClient` and rate limit |
| Download one media file's bytes        | `http/timeline.py` (shell)    | Owns the signed URL fetch; converts errors to `CliError(HTTP)` |
| Atomic write, symlink                  | `dump/writer.py`              | Shell boundary; converts `OSError` to `CliError(INTERNAL)` |
| SQLite manifest read/write             | `dump/manifest.py`            | Shell boundary; same conversion rule |
| Orchestrate pages → posts → media      | `dump/sync.py`                | The only module that calls the above in order |
| CLI arg parsing, exit codes            | `cli.py`                      | Shell |

### Findings (the spike's contract)

The Aug 29 spike (data in `docs/api-notes-data/`) produced:

**Endpoint:**

```
GET https://app.inso.pl/system-api/timeline/posts
    ?page={1,2,...}
    &categoryId={0,1,2,3,4,6,99}
    &child={child-uuid}

Response 200 application/json:
{
  "items": [Post, ...],            # length 10 normally; <10 on the last page
  "waitingToProcess": 0            # 0 = page is settled; >0 = retry with backoff
}
```

**Auth:** `Cookie: PHPSESSID=<opaque>` is the only credential. No
`Authorization` header, no CSRF. The foundation spec's
`HttpxClient` already sets the `BROWSER_LIKE_HEADERS` set; the JSON
API returns 200 for that header set. The spike re-confirmed this
with direct `curl` calls.

**Categories (from `data-categories` on `#root`):**

| id  | name           | scope of this spec |
| --- | ---            | ---                |
| 0   | Wszystkie      | ignored            |
| 1   | Wydarzenia     | out of scope       |
| 2   | Ogłoszenia     | ✅ dumped          |
| 3   | Galerie zdjęć  | ✅ dumped          |
| 4   | Jadłospisy     | out of scope       |
| 6   | Ankiety        | out of scope       |
| 99  | Archiwum       | out of scope       |

**`Post` schema (validated against the spike's three pages):**

```json
{
  "id": "3d5b38b4-6c0b-4e79-8e55-375272c76779",
  "title": "Wycieczka do Leniwej Zagrody",
  "content": "<p>...sanitised HTML, possibly empty...</p>",
  "media": {
    "photos": [{
      "type": "photo",
      "name": "IMG_2679.jpeg",
      "src": {
        "thumb": "https://file.inso.pl/.../thumb.jpg?Expires=...&Signature=...&Key-Pair-Id=...",
        "full":  "https://file.inso.pl/.../full.jpg?Expires=...&Signature=...&Key-Pair-Id=..."
      }
    }, ...],
    "videos": [],                            // captured always-empty; item shape unverified
    "attachments": [{
      "name": "pozegnanie-z-dzieckiem.pdf",
      "url": "https://file.inso.pl/.../pozegnanie-z-dzieckiem.pdf?Expires=...&Signature=...&Key-Pair-Id=..."
    }, ...]
  },
  "createdAt": 1787811431,                 // unix seconds
  "createdAtText": "czwartek,  8:17",      // Polish, server-formatted
  "visibleFor": ["Żółta", "Zielona"],      // group names
  "visibleForChildren": ["eea48660-...", ...],
  "actions": { "vote": "...", "unvote": "..." },   // ignored
  "sticky": false,
  "archived": false,
  "userVoted": false,                       // ignored
  "author": "Lipińska Agnieszka",
  "likes": 1,
  "likesText": "1 polubienie",              // ignored
  "commentsAvailable": false,
  "comments": 0,
  "commentsText": "0 komentarzy",           // ignored
  "isWorker": false,
  "isWorkerOnly": false,
  "displayed": true,                        // ignored
  "poll": null,                             // ignored
  "translations": []                        // ignored
}
```

**Signed URLs:** `https://file.inso.pl/timeline/{institution-id}/{year}/{month}/{photo-uuid}/{thumb|full}.{ext}?Expires=<unix>&Signature=<base64>&Key-Pair-Id=…`
— short-lived (~hours). The dumper downloads immediately after
fetching the page; URLs are not persisted.

**`PUT /view` is intentionally never called.** The browser fires it
on every page load; the dumper does not. The `actions` field in the
response is the only place this endpoint is hinted at.

**Per-page semantics (confirmed):**
- Page size is **10**. The 11th item triggers `page=2`.
- `waitingToProcess` is the server telling us a write was queued
  and the next page may not include the new post. We retry on
  `waitingToProcess > 0` with exponential backoff (capped at
  30s, 3 attempts) before falling back to "page is settled".

## API Design

### CLI surface (v1 of this spec)

```
inso-dumper sync <child-slug>
                  [--category {announcements,galleries,both}]  # default: both
                  [--dump-root PATH]                          # default: ./dump
                  [--force <post-slug> ...]                   # repeatable; re-download named posts
                  [--verbose|-v]
inso-dumper --version
inso-dumper --help
```

`<child-slug>` is the same `Child.slug` the foundation spec produces
(`inso-dumper children --json` lists them; copy-paste).

`sync`:

- Validates the session (foundation spec, exit 4 if expired).
- Resolves `<child-slug>` to `<child-uuid>` by re-running the
  children scrape and matching slug; exits 6 (`PLATFORM_CHANGED`)
  if the slug is unknown. (A user typo maps to 6 rather than a
  dedicated user-error code because the slug list itself comes
  from a platform scrape and no user-error exit code exists in
  the foundation's closed union.)
- For each category in the chosen set, iterates pages from
  `page=1` until the first empty `items` page, parsing each into
  `list[Post]`. If the API returns a new key not in `Post`, the
  parser returns `Err(CliError(PLATFORM_CHANGED))` and the
  dumper stops before the manifest is touched; re-runs recover
  in place (see the target-dir rule below).
- For each post, checks the manifest. If `post_id` is in the
  manifest and the on-disk directory exists, skip. Otherwise:
  - Derive `post_target_dir`. If that directory already exists:
    - its `post.json` names the same `post_id` → a partial dump
      from an interrupted run; remove the directory and re-dump
      in place (no orphaned `-N` directories);
    - its `post.json` names a different `post_id`, or is
      unreadable → slug collision; use the next `-N` suffix.
    Otherwise create it (`0o700`).
  - Write `post.json` (full `Post` as JSON), `post.html` (the
    raw sanitised HTML from `content`), `post.md` (a best-effort
    HTML→markdown conversion; if conversion fails, log at INFO
    and skip `post.md`).
  - For each `Photo` / `Video` / `Attachment`: download the source
    URL (`src.full` for photos and videos, `url` for attachments),
    hash, write to
    `dump/_common/<kind>/<hash[:2]>/<hash>.<ext>` (atomic, `0o600`)
    if not already present, then create
    `post_target_dir/{photos,videos,files}/<n>.<ext>` as a symlink
    relative to `dump_root` (videos live under `videos/`, attachments
    under `files/`, mirroring the platform's naming). If symlinks
    are unsupported, copy.
  - Upsert into the manifest DB.
- After the run, prints a one-line summary:
  `Synced N posts (S skipped, M photos, V videos, K attachments) for <child-slug> in Ts.`
  Exits 0.

`--force <post-slug>`: re-downloads media for the named posts even
if they're in the manifest, by deleting the symlinks and re-running
the download step. The manifest entries are updated.

`--category`: restricts the run. `both` is the default and the
most common case.

### Storage layout

```
dump/
├── _common/                                       # shared across all children
│   ├── photos/
│   │   └── ab/
│   │       └── abc123…def.jpg                     # SHA-256[:2]/SHA-256.<ext>
│   ├── videos/
│   │   └── cd/
│   │       └── cdef123…789.mp4
│   └── attachments/
│       └── 12/
│           └── 123abc…456.pdf
└── <child-slug>/
    └── announcements/
        ├── .manifest.sqlite                       # sync state for this child
        ├── 2026-08-27-wycieczka-do-leniwej-zagrody/
        │   ├── post.json                          # full Post, pydantic-serialised
        │   ├── post.html                          # raw sanitised HTML
        │   ├── post.md                            # best-effort conversion (optional)
        │   ├── photos/
        │   │   ├── 1.jpg -> ../../../../_common/photos/ab/abc123…def.jpg
        │   │   └── 2.jpg -> …
        │   ├── videos/                            # only if videos present
        │   │   └── 1.mp4 -> ../../../../_common/videos/cd/cdef123…789.mp4
        │   └── files/                             # only if attachments present
        │       └── 1.pdf -> ../../../../_common/attachments/12/123abc…456.pdf
        └── 2026-08-25-szafki-w-szatni/
            ├── post.json
            ├── post.html
            └── (no photos/, no files/)
```

The first `<child-slug>` directory is `children/<slug>/` in PRD
terminology; the spec uses `<child-slug>/announcements/` directly
to keep the depth shallow. The PRD's example in F3 should be
updated to match.

### Logging

Reuse the foundation spec's logger discipline:

- `inso_dumper.dump.sync` — page boundaries, per-post summary,
  errors.
- `inso_dumper.http.timeline` — per-request method/path/status.
- `inso_dumper.dump.writer` — atomic write / symlink events.
- `inso_dumper.dump.manifest` — upsert events.

`-v` adds per-page request logs. The `TokenRedactingFilter` is
already on the root logger, so signed URLs in logs are
redacted automatically (the filter scrubs `Signature=…&…`).

### Error handling approach

The closed `CliError` union from the foundation spec is reused
unchanged. New failure modes are mapped to existing kinds:

- Network/HTTP → `CliError(HTTP)` (5).
- API returns a key the `Post` model doesn't know about →
  `CliError(PLATFORM_CHANGED)` (6).
- Manifest schema mismatch (existing v1, `schema_version` field) →
  `CliError(CONFIG)` (2).
- `OSError` on write → `CliError(INTERNAL)` (99). The shell
  boundary captures and converts; the core never raises.
- New exit code? None added. All expected failures map to the
  foundation's six.

`Result[..., CliError]` is the boundary type at every shell
function, the same as the foundation spec.

## Data Model

```python
# Dump manifest (one per child, per category-of-posts)
# Path: dump/<child-slug>/announcements/.manifest.sqlite
# Table: posts
#   post_id          TEXT    PRIMARY KEY     -- platform UUID; the sole dedup key
#   post_slug        TEXT    NOT NULL        -- "<YYYY-MM-DD>-<title-slug>"
#   category_id      INTEGER NOT NULL        -- 2 or 3
#   child_slug       TEXT    NOT NULL        -- denormalised for queries
#   first_seen_at    TEXT    NOT NULL        -- ISO-8601 UTC
#   last_seen_at     TEXT    NOT NULL        -- ISO-8601 UTC
#   media_count      INTEGER NOT NULL        -- photos + videos + attachments
#
# Index: posts_by_slug (post_slug)                 -- --force lookup
# Schema version: tracked in PRAGMA user_version. Migrations: not needed
# in v1 (greenfield).
```

The manifest is local-only. It is created lazily on first
`sync`. The dumper treats its absence as "no posts dumped yet" and
walks all pages.

The per-post `post.json` is the full `Post` as JSON. It is the
**source of truth** for the dump — the manifest only tracks
"have we seen this post". Re-runs that need to reconstruct a
`Post` (e.g. for `verify` in a later spec) read `post.json`.

`post.html` is the raw `content` from the API, written verbatim
(no transformations). `post.md` is best-effort — see
[Gotchas](#gotchas-assumptions-technical-debt).

`post.json` includes a `dumped_at` field set at write time
(ISO-8601 UTC) for forensic value; this is **added by the
dumper**, not the platform, and is a "shadow" field on the
`Post` model. It serialises under its Python name `dumped_at`
(no camelCase alias — the snake_case key makes its dumper-added
origin visually obvious next to the platform's camelCase keys).

## Trade-offs

### Considered: dump everything server-side-rendered HTML, parse no JSON

- **Pro:** the foundation spec's children list works by HTML
  scrape; the same pattern would feel consistent.
- **Con:** HTML is larger, less stable, and the dump would
  re-parse it on every read.
- **Decision:** use the JSON API. The platform already exposes
  it; the spike confirmed it's stable. The dump is a mirror of
  the API response (`post.json`) plus resolved media.

### Considered: keep all photos under each child, no `_common/`

- **Pro:** simpler; no symlinks; works on every filesystem.
- **Con:** duplicates gigabytes across children; defeats the
  PRD's F3 dedup goal.
- **Decision:** `_common/` with content-hash dedup. Symlinks
  with copy fallback.

### Considered: only dump `categoryId=0` (Wszystkie), let the user filter

- **Pro:** one walk, all posts.
- **Con:** includes events, polls, menus, archive — out of
  scope. Mixing categories breaks the PRD F3 directory model.
- **Decision:** two separate walks, one per category. The
  manifest tracks `category_id` so a future "merge all
  categories" option is a small change.

### Considered: re-fetch each page every run

- **Pro:** simple; no manifest.
- **Con:** the platform's rate limit is hit every run; signed
  URLs expire so re-downloaded media needs re-fetching anyway.
- **Decision:** manifest + on-disk post check. A re-run is
  bounded by "how many new posts since last sync", not "total
  posts".

### Considered: silent re-login on session expiry

- **Pro:** no UX friction.
- **Con:** env vars (`INSO_EMAIL`/`INSO_PASSWORD`) must be
  present at sync time. The foundation spec's `login` is
  deliberately the only command that reads them. Mixing
  concerns weakens the security model.
- **Decision:** do not. The dumper prints "run
  `inso-dumper login`" and exits 4. (PRD Open Question 8 from
  the foundation spec.)

### Considered: write `post.md` via `html-to-markdown` (PyPI)

- **Pro:** robust, handles edge cases.
- **Con:** new dep; licensing / supply chain surface.
- **Decision:** small built-in converter (`html.parser` from
  stdlib) for v1. If the spike's HTML reveals structures the
  converter can't handle, the dumper logs INFO and skips
  `post.md` (the raw `post.html` is always present). A
  follow-up spec can swap in `html-to-markdown` if needed.

### Considered: store `PHPSESSID` expiry timestamp

- **Pro:** the dumper could warn before the session expires.
- **Con:** the spike did not measure a real lifetime; the
  foundation spec deferred this.
- **Decision:** measure during the first sync; persist
  `expires_at` on `Session` if the server hints at one. The
  spec captures this in a follow-up note; not v1.

## Open Questions

1. **Session lifetime.** The spike did not measure how long
   `PHPSESSID` stays valid. The dumper treats it as
   always-valid until a 401/403 surfaces. A future spec should
   measure and pin.
2. **Server-side HTML sanitisation.** The API's `content`
   field is sanitised by the platform. We dump it verbatim.
   If the platform ever changes its sanitiser, `post.html`
   changes — but the dumper is read-only so this is fine.
3. **Sticky/pinned posts in Ogłoszenia.** The spike shows
   sticky posts re-appearing under `categoryId=0` but not
   under `categoryId=2`. If the platform ever changes this,
   `sync` will encounter them under a second category. The
   manifest dedups by `post_id` alone, so such a post is
   dumped once (the first category walked wins) and a re-run
   after a category change is bounded.
4. **Multi-child dedup semantics.** The spec dedups media
   across all children (one `_common/`). If the user wants
   per-child isolation (e.g. one `dump/` per child for
   separate archival), a `--no-dedup` flag is a small
   follow-up.
5. **Author identity.** The `author` field is a display name
   (e.g. "Lipińska Agnieszka"), not a UUID. If the platform
   renames a teacher, the dumper records the new name. The
   manifest has no stable author ID; a future spec may add
   one if the user wants author-level change tracking.
6. **Comments.** The API exposes `comments: 0` and
   `commentsText`. The spec ignores both. A follow-up could
   walk `/system-api/timeline/posts/{uuid}/comments` if the
   user wants to preserve comment threads.
7. **PDF text extraction.** Attachments are dumped as bytes.
   If the user wants full-text search later, a follow-up
   spec can add `pdftotext` extraction.
8. **HTML in `content` is non-empty for text announcements
   and empty for galleries.** The converter handles both.
   Edge case: `<span style="background-color:rgb(...)">` —
   the converter strips styles, keeping text. Captured in the
   spike data for follow-up testing.

### Decomposition reminder

This spec is M1 finish + M2 (announcements + dedup). The PRD's
remaining M3–M5 each get their own follow-up spec:

- `messages-and-documents` — Phase 4 (M3).
- `indexes-and-gallery` — Phase 5 (M4).
- `polish-and-materialize` — Phase 6 (M5).
