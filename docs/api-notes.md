# Inso API notes

Spike output from `plan.md` Phase 0 plus the follow-up spikes for
`announcements-and-dedup` and `messages-and-documents` (all Aug 2026,
against https://app.inso.pl). This document is the living record of how
the platform actually behaves; the specs' assumptions are replaced by
what is recorded here.

## TL;DR

The Inso platform is a **server-rendered PHP application behind
Cloudflare with a JSON `system-api` for content** — a hybrid. Auth is a
one-step form POST returning a single `HttpOnly PHPSESSID` cookie. The
children list is scraped from HTML (sidebar), but the content surfaces
are JSON endpoints discovered in the follow-up spikes:

| Content | Endpoint | Verified |
| --- | --- | --- |
| Announcements/galleries | `GET /system-api/timeline/posts` | fully (3 pages + real syncs) |
| Messages | `GET /system-api/conversations[/{id}]` | fully (list + 2 history pages) |
| Documents (drive) | `GET /drive/items[/{directoryId}]` | `breadcrumbs` only (empty drive) |
| Settlements (Rozliczenia) | server-rendered `/panel/settlement/child/<uuid>` + `/panel/child/<uuid>/settlement` | fully (2 pages + invoice PDF) |

The original "no JSON API" conclusion from the login spike applied only
to the pages the first spike visited; see the endpoint sections below.
Cloudflare blocks bare HTTP clients, but **passes `httpx` with a
realistic browser header set** — confirmed in the spike and a
follow-up probe. See *Cloudflare bot management* below. The
`HttpxClient` production transport sends a fixed
`BROWSER_LIKE_HEADERS` constant on every request.

## Auth model

| Concern | Value | Source |
| --- | --- | --- |
| Login URL | `https://app.inso.pl/login` (GET and POST) | HAR entry 41 |
| Method | `POST` form-encoded | HAR entry 41 |
| Content-Type | `application/x-www-form-urlencoded` | HAR entry 41 |
| Username field | `_username` | HAR entry 41 |
| Password field | `_password` | HAR entry 41 |
| CSRF token | **none** | confirmed by inspecting `login.<hash>.js` and the POST request |
| Required headers | `Origin`, `Referer`, `Upgrade-Insecure-Requests`, normal UA | HAR entry 41 |
| Response | `302 Found` with `Location: /panel/` | HAR entry 41 |
| Set-Cookie | **`PHPSESSID=<opaque>; path=/; secure; httponly; samesite=lax`** | HAR capture via `curl` |
| Remember-me | `REMEMBERME=deleted` cleared on every login (server-side "you don't have one") | HAR capture via `curl` |
| Post-login redirect chain | `/login` → `/panel/` → `/panel/home/<user-uuid>/` | HAR entries 41–43 |
| Session lifetime | not measured; `PHPSESSID` is the standard PHP session cookie (server-side expiry) | inferred |
| Refresh mechanism | none — re-login on expiry | n/a (v1) |
| 2FA | none encountered | spike did not trigger any second factor |

### What the client must do

1. GET `/login` once to satisfy any pre-session cookies and CSRF (none
   required, but cheap and defensive).
2. POST `/login` with `_username` and `_password` as
   `application/x-www-form-urlencoded`. Follow redirects.
3. Capture `PHPSESSID` from `Set-Cookie`. Persist it. Send it on every
   subsequent request.

### What the client must NOT do

- Send any custom auth header. There is no `Authorization: Bearer …`
  and no `X-…-Token` to forge.
- Assume the body of a successful login response contains the user.
  The body is empty; the user UUID is the path segment of the final
  `Location`.

## Cloudflare bot management

A bare `httpx` request (no headers set beyond defaults) is **rejected
with HTTP 403** by Cloudflare's bot management. A bare `urllib`
request is rejected for the same reason. `curl` succeeds.

**Re-tested on 2026-08-29 with browser-like headers** (User-Agent,
sec-ch-ua, Accept-Language: pl-PL, sec-ch-ua-platform, etc. — the
exact set is documented in
`docs/specs/2026-08-29-foundation-auth-cli/design.md` § Cloudflare
verification). With those headers, `httpx` gets HTTP 200 and a clean
14 KB HTML response. End-to-end login + authenticated dashboard
fetch also works through `httpx` (302 on POST `/login` with
`Set-Cookie: PHPSESSID=…`, then GET dashboard returns 200 with
both children's names visible).

**Conclusion for v1:** `HttpxClient` is the production transport. No
`curl_cffi`, no subprocess `curl`, no `agent-browser` fallback. The
`HttpxClient` pins a `BROWSER_LIKE_HEADERS` constant and includes it
on every request; a test asserts the constant stays present.

If Cloudflare ever tightens to the point of rejecting this header
set, the existing `HttpClient` Protocol accommodates a transport
swap (subprocess `curl`, `curl_cffi`, or `agent-browser`) without
call-site changes. The exit code 5 (`HTTP`) covers the failure
mode regardless.

## Children endpoint

**There is no `GET /api/children`.** The children list is rendered
into the HTML of any logged-in page. The spike saw it in:

- `https://app.inso.pl/panel/home/<child-uuid>/` (current child's
  dashboard)
- Every other `/panel/...` and `/timeline/...` page, in the sidebar
  child-switcher menu (`<el-menu id="menu-0">`).

### URL pattern for a child's dashboard

```
https://app.inso.pl/panel/home/<child-uuid>/
```

`<child-uuid>` is a v4-style UUID, e.g.
`eea48660-3740-11ed-a611-06dd2728d782` (Franek) and
`a703dc5a-042d-4122-96e8-4c5111e5f7cf` (Zofia). The UUID is stable
across sessions and is the canonical child identifier.

### Sidebar markup that exposes children

```html
<el-menu id="menu-0" role="menu">
  <a href="https://app.inso.pl/panel/home/<uuid-1>" id="item-2" role="menuitem">
    <div style="background-color: #FCCC34;"><span>IG</span></div>
    <span>Franek</span>
  </a>
  <a href="https://app.inso.pl/panel/home/<uuid-2>" id="item-3" role="menuitem">
    <div style="background-color: #A9CC63;"><span>LI</span></div>
    <span>Zofia</span>
  </a>
  <a href="/panel/add-child" id="item-4" role="menuitem">
    <div><i class="fal fa-plus"></i></div>
    <span>Dodaj dziecko</span>
  </a>
</el-menu>
```

### `Child` model (revised by the spike)

The spec's draft `Child` model was missing a few fields that the
spike found in the markup. Revised fields:

| Field | Source | Type |
| --- | --- | --- |
| `child_id` | UUID segment of `/panel/home/<uuid>/` | `str` |
| `first_name` | `<span>` inside the `<a>` menu item | `str` |
| `last_name` | not present in the spike's two children; appears in the URL slugs of `/messages?child=<uuid>` and `/timeline/child/<uuid>` page titles. The spike's home page title was "Podsumowanie - Kowalski Franek - Inso" — full name is available on the dashboard. | `str` |
| `group` | the home page title does not include a group; sidebar items don't either. The group name is likely on a per-child profile page that the spike did not visit. | `str \| None` (kept from spec) |
| `avatar_color` | the `<div style="background-color: #XXXXXX;">` in each menu item | `str` (CSS hex) |
| `initials` | `<span>` inside the avatar div, e.g. `IG`, `LI` | `str` |

The `last_name` is reachable through `/messages?child=<uuid>` (page
title contains the full name), but **fetching every child just to
read the last name is wasteful**. The spec's `Child.slug` rule
(`firstname-lastinitial` or similar) is fine without the last name;
the foundation spec should add a documented `last_name` discovery
field that future specs may fill in lazily, not on the children
list call.

### `list_children` derivation

`list_children()` is **not a network call** in v1. It is:

1. Load the saved `Session` (`PHPSESSID`).
2. GET `https://app.inso.pl/panel/home/<any-uuid>/` (or the
   post-login landing page; either contains the sidebar). Any
   logged-in page works.
3. Parse the `<el-menu id="menu-0">` block; extract each `<a>`
   whose `href` matches `^https://app.inso.pl/panel/home/<uuid>/?$`.
4. Return `list[Child]`.

The "any logged-in page" choice is the cheapest. The post-login
landing `/panel/home/<user-uuid>/` is the natural target — it is
always 200, always has the sidebar, and its `<child-uuid>` happens
to be the last-active child.

### `list_children` derivation — addendum (live traffic, Aug 2026)

First real-session run surfaced two markup/transport changes; the
parser was updated to match (both re-verified against a captured
dashboard):

1. **Trailing-slash 301.** `GET /panel/home/<uuid>/` now answers
   `301 Moved Permanently` → `Location: /panel/home/<uuid>` (no
   slash), which the post-login landing also uses. `HttpxClient`
   does not auto-follow redirects (login observes its 302 chain), so
   `list_children` follows the redirect itself (bounded, same-origin
   checks in `http/redirects.py`).
2. **No more `<el-menu id="menu-0">`.** The dashboard renders three
   `el-menu` popover elements without that id. Child entries are now
   plain anchors:

   ```html
   <a href="https://app.inso.pl/panel/home/<child-uuid>" class="…hover:bg-blue-100…">
     <div class="inline-flex … rounded-full" style="background-color: #FCCC34;">
       <span class="font-bold … uppercase" style="color: #639302;">IG</span>
     </div>
     <div class="ml-1 inline-flex flex-col …">
       <span>Ignacy</span>
     </div>
   </a>
   ```

   The same entry renders twice (desktop + mobile popovers) and the
   main nav carries same-URL links without the avatar/name structure
   (e.g. a lone `Podsumowanie` span). The parser therefore accepts
   only anchors that have both an avatar `background-color` div and
   two spans (initials, then first name), deduplicating by
   `child_id`. Zero matching anchors → `PLATFORM_CHANGED` (markup
   drift), matching anchors without names → `Ok([])`.


## Timeline posts API (JSON, discovered in the announcements spike)

Captured page: `docs/api-notes-data/timeline-posts-page3-category2.json`
(gitignored — real names inside).

```
GET /system-api/timeline/posts?page={1,2,...}&categoryId={0,1,2,3,4,6,99}&child={child-uuid}
→ 200 application/json: {"items": [Post, ...], "waitingToProcess": 0}
```

- Page size **10**; a page with fewer items is the last one (we walk until
  an empty `items` page).
- `waitingToProcess > 0` means a server-side write is queued and the page
  may be incomplete — the client retries with exponential backoff
  (cap 30 s, 3 attempts) before treating the page as settled.
- Categories: 0 Wszystkie, 1 Wydarzenia, **2 Ogłoszenia**, **3 Galerie
  zdjęć**, 4 Jadłospisy, 6 Ankiety, 99 Archiwum (from `data-categories`
  on `#root`).
- Auth: `Cookie: PHPSESSID=…` only; no CSRF, no bearer.
- `?child=` filters per child; `?categoryId=` filters per tab.

`Post` payload (verbatim field names; validated against captures):

```json
{
  "id": "3d5b38b4-…", "title": "…", "content": "<p>…sanitised HTML…</p>",
  "media": {
    "photos": [{"type": "photo", "name": "IMG_2679.jpeg",
                "src": {"thumb": "https://file.inso.pl/…", "full": "https://file.inso.pl/…"}}],
    "videos": [],
    "attachments": [{"name": "pozegnanie-z-dzieckiem.pdf", "url": "https://file.inso.pl/…"}]
  },
  "createdAt": 1787811431, "createdAtText": "czwartek,  8:17",
  "visibleFor": ["Żółta", "Zielona"], "visibleForChildren": ["eea48660-…"],
  "author": "Lipińska Agnieszka", "likes": 1,
  "commentsAvailable": false, "comments": 0,
  "sticky": false, "archived": false
}
```

Ignored fields: `actions`, `userVoted`, `likesText`, `commentsText`,
`isWorker*`, `displayed`, `poll`, `translations`. **Comment bodies are
not in the payload** — counts only.

- **Videos ride inside `media.photos[]`** with `"type": "video"` and
  `src: {mp4, thumb}` (no `full`); the dedicated `media.videos` array
  has been empty on every captured post. Details and the captured shape
  are in *Timeline media: video item shape* at the bottom of this file.
- **`PUT /view` is intentionally never called** (the browser fires it on
  every page load; the dumper does not — read-only guarantee).
- Signed media URLs:
  `https://file.inso.pl/timeline/{institution}/{y}/{m}/{uuid}/{file}?Expires=…&Signature=…&Key-Pair-Id=…`
  — CloudFront, short-lived (~hours). Download immediately; never persist.

## Conversations API (JSON, discovered in the messages spike)

Captures: `conversations-sample.json`, `conversation-detail-sample.json`.

```
GET /system-api/conversations?category=main[&loadOlder=1]
→ {"categories": [], "category": "main", "conversations": [...],
   "templates": [], "unreadCount": int}

GET /system-api/conversations/{id}?messages=1[&startingIndex=N]
→ [Message, ...]                    # bare list; 30 per page
```

- **Account-level**: `?child=` is accepted but ignored; conversations are
  group-teacher threads (`recipient.type: "teachers"`). Storage and
  manifests are therefore account-level, not per-child.
- History pages: `startingIndex=30` returns the next 30 messages
  (verified); the list is a bare JSON array, no envelope.
- Conversation item: `{id, lastUpdate (unix), recipient: {name,
  description, type, prefix}, read, branch, participantsNames, excerpt}`.
- Message item: `{id, message, sendDate, sendTimestamp, sender: {type,
  name, initials, avatar}, incoming, attachments: {media: [], other: []},
  main, isRemoved, canRemove}`.
- Media attachment: `{name, url: {thumb, full}, isVideo}` — a dict, unlike
  the timeline's flat attachment URL. `attachments.other` shape is
  **unverified** (none captured); parsed permissively until a capture.
- Conversations **mutate** (unlike posts): re-sync compares `lastUpdate`
  and tail-fetches via `startingIndex = message_count`. Old-message
  edits/removals are only caught by a full re-fetch.
- Forbidden (never called): `POST /system-api/conversations/read-all-unread`,
  `POST /system-api/messages/attachment`.

## Drive API (JSON, provisional — messages spike)

Capture: `drive-items-sample.json`. The spike account's drive is empty,
so only `breadcrumbs` is verified:

```
GET /drive/items[/{directoryId}]
→ {"breadcrumbs": [{"name", "id"}], "directories": [...], "directory": ..., "files": [...]}
```

The `directories`/`files` node shapes are **provisional**; the first
non-empty drive either parses or fails loud and the models are fixed from
a fresh capture (same path as the video-shape fix). Until then
`sync_documents` refuses to guess
(`Err(CONFIG, 'documents_unverified')`). File download URLs are expected
to be CloudFront-signed like the rest of `file.inso.pl` — **unverified**.
Forbidden (never called): all `/drive/directory*` and `/drive/file*`
write endpoints.

## Settlements (Rozliczenia) — server-rendered HTML (billing spike, Aug 2026)

Unlike every other content surface, settlements has **no JSON API**: the
pages are plain server-rendered documents and the only XHR observed is
the shared `system-api/notifications` badge poller. All data lives in
the markup.

### URL patterns (the PRD sketch guessed wrong)

| Page | URL | Verified |
| --- | --- | --- |
| Current month (summary card) | `GET /panel/settlement/child/<child-uuid>` | fully |
| Full history ("Starsze rachunki" link) | `GET /panel/child/<child-uuid>/settlement` | fully |
| Invoice download | `GET /panel/settlement/<bill-uuid>/invoice` | fully |

The history page lists **every month on one page** — October 2022 →
August 2026 (47 cards) on the spike account, no pagination, no
load-more. The "Starsze rachunki" control is a link to the second
pattern, not an in-place expander.

### Month card markup

Tailwind-based cards (divs on the history page, wrapped in a `<form>`
on the current-month page). Per card:

- child initials badge, `Rozliczenia` title, status badge
  `<span class="…bg-green-100…">opłacone</span>`
- `Kwota`, `Saldo`, `Termin` lines — the due date is human text with
  Polish month names (`Termin 11 sierpnia 2026`; no space after
  `Termin` in the current-month variant) and needs a month-name map to
  normalize
- a `Zobacz szczegóły` expander — **client-side toggle, fires no
  request**

### Details (expander content)

A `Składowe rachunku` `<table>` with itemized rows (`Czesne`, per-meal
lines with counts, `Suma`), then `Do zapłaty`, `Zapłacono`,
`Aktualne saldo`, and a `Pobierz fakturę` link when an invoice exists.

### Invoice download

`GET /panel/settlement/<bill-uuid>/invoice` returns the PDF **directly**
(`200`, `application/pdf`, ~46 KB captured), same-origin, authenticated
by the session cookie — **no CloudFront signing**, unlike timeline
media. `bill-uuid` is per-bill and unrelated to the child uuid.

### Read-only notes

Every useful request is a GET. The page also offers a payment flow
(`Zapłać` button, traditional-transfer instructions) — never touched;
the dumper only ever issues the two listing GETs and invoice downloads.

### Captures

`docs/api-notes-data/billing-{page,main,main-details,history-page}.html`
and `billing-session.har` (41 requests; includes the interactive login —
credentials redact-before-commit if ever moved).

### Unverified (none present on the spike account)

- unpaid / overdue status badge variants (every captured card is
  `opłacone`; `saldo 0,00`)
- a bill **without** an invoice link (whether `Pobierz fakturę` can be
  absent)
- partial payments (`Saldo` ≠ `0,00 zł`)

## Other endpoints discovered (out of scope for v1)

These URLs appeared in the sidebar's navigation links. The JSON-backed
ones are covered by the sections above; the rest are server-rendered
HTML pages. The settlements pages are now spiked too (§ Settlements);
the remaining sections stay out of scope for v1.

| Section | URL pattern | What it shows |
| --- | --- | --- |
| Dashboard (Podsumowanie) | `/panel/home/<child-uuid>/` | Today's summary; sidebar; children scrape |
| Messages (Wiadomości) | `/messages?child=<child-uuid>` | HTML shell for the conversations JSON API |
| Announcements (Ogłoszenia) | `/timeline/child/<child-uuid>` | HTML shell for the timeline JSON API |
| Billing (Rozliczenia) | `/panel/settlement/child/<child-uuid>` (current) + `/panel/child/<child-uuid>/settlement` (history) | Fees + invoices — read-only dump planned (M6); see § Settlements |
| Attendance calendar | `/panel/attendance/<child-uuid>/` | Calendar |
| Events | `/events/child/<child-uuid>` | Events list |
| Files (Pliki) | `/panel/files/<child-uuid>/` | HTML shell for the drive JSON API |
| Diaries (Dzienniki) | `/diaries/child/<child-uuid>` | Daily reports |
| Child data | `/panel/child-data/<child-uuid>/` | Profile, parents, pickup |
| Authorizations | `/panel/authorizations/<child-uuid>/` | Pickup authorizations |
| Add child | `/panel/add-child` | New child wizard (skip) |

## Session model (spec revision)

The foundation spec's draft `Session` model was over-specified. The
spike shows the **minimum persisted state** is:

```python
class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    phpsessid: str                  # the value of PHPSESSID
    user_uuid: str                  # the post-login landing UUID
    expires_at: datetime | None     # unknown; PHP sessions are server-side.
                                     # Conservative: None, treat as
                                     # always-needs-validation, with
                                     # a future spike measuring real expiry.
```

The `bearer_token`, `refresh_token`, `csrf_token`, and `raw_metadata`
fields in the spec's draft are **not needed** for v1. Drop them. The
`cookies: dict[str, str]` field can be reduced to `phpsessid: str`
plus `remember_me: str | None` for forward compat, but v1 only needs
`phpsessid`.

## Open Questions (still open after all spikes)

1. **Session lifetime** — not measured. The `expires_at` field in
   the persisted session is currently always `None` and treated as
   "validate on every load" (i.e. re-login required every run).
2. **2FA on this account** — none encountered. If a future account has
   2FA, the `login` command's error taxonomy has no
   `NEEDS_2FA` variant. **Defer to that account.**
3. **Drive file/download shapes** — the account drive is empty;
   `directories`/`files` shapes and the download URL scheme are
   provisional (see § Drive API).
4. **Message `attachments.other`** — never captured; parsed permissively.
5. **`loadOlder` semantics on `/system-api/conversations`** — the exact
   paging contract is assumption-level; the planned walker stops on an
   empty/no-progress page rather than assuming a page size.

## How to reproduce this spike

```bash
# 1. Save credentials once (interactive, password on stdin):
agent-browser auth save inso \
  --url https://app.inso.pl/login \
  --username <email> --password-stdin

# 2. Drive a headful session, capturing everything:
AGENT_BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  agent-browser open https://app.inso.pl/login
AGENT_BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  agent-browser auth login inso

# 3. With HAR running, navigate to the children switcher:
AGENT_BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  agent-browser network har start
AGENT_BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  agent-browser network har stop docs/api-notes/har/spike-<date>.har

# 4. For the cookies themselves, use curl (HAR cannot see
#    HttpOnly Set-Cookie on a 3xx chain):
curl -c jar -b jar -L --data-urlencode '_username=…' \
  --data-urlencode '_password=…' -X POST \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -H 'Referer: https://app.inso.pl/login' \
  https://app.inso.pl/login
# inspect jar for PHPSESSID
```

`docs/api-notes/har/spike-2026-08-29-login.har` (55 requests) and
`docs/api-notes/har/spike-2026-08-29.har` (12 requests, mid-session)
are the artifacts.

## Timeline media: video item shape (live traffic, Aug 2026)

The discovery spike captured `media.videos: []` on every post, so the
video shape was unknown until the first real sync failed loud
(`PLATFORM_CHANGED`) on a video-bearing announcement post. Recorded
page: `docs/api-notes-data/timeline-posts-page3-category2.json`.

Findings:

- **Videos arrive inside `media.photos[]`**, discriminated by
  `"type": "video"` (photos carry `"type": "photo"`). The dedicated
  `media.videos` array exists but was empty on every captured post.
- Video item shape:

  ```json
  {
    "type": "video",
    "name": "VID-20260531-WA0008.mp4",
    "src": {
      "mp4":   "https://file.inso.pl/timeline/<institution>/<y>/<m>/<uuid>/video.mp4?Expires=…&Signature=…&Key-Pair-Id=…",
      "thumb": "https://file.inso.pl/timeline/<institution>/<y>/<m>/<uuid>/thumb.jpg?Expires=…&Signature=…&Key-Pair-Id=…"
    }
  }
  ```

  Unlike photos (`src.thumb` + `src.full`), a video's playable URL is
  `src.mp4` — there is no `full`.
- Same short-lived CloudFront signing as photos; download immediately.
